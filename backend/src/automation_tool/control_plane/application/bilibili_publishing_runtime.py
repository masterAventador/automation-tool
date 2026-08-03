"""Short-lived production composition for Bilibili publishing.

Durable publish facts remain in PostgreSQL.  Platform credentials deliberately
do not: the desktop sends them when it opens a publishing session, this runtime
keeps them only in memory, and any single-use token rotation is returned to the
desktop for protected local storage.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchiveCreationReceipt,
    BilibiliArchiveFields,
    BilibiliArchiveOutcomeUncertain,
    BilibiliArchivePublishRejected,
    BilibiliArchivePublishService,
    BilibiliArchivePublishUnavailable,
    BilibiliPublishMaterial,
    BilibiliPublishPhase,
    BilibiliPublishPreparation,
    BilibiliPublishStepFailed,
)
from automation_tool.control_plane.domain.bilibili_open_api import BilibiliOpenApiContract
from automation_tool.control_plane.domain.resource_ids import InstallationId
from automation_tool.control_plane.domain.video_publishing import PublishJobId
from automation_tool.control_plane.infrastructure.bilibili import (
    BilibiliApiCredentials,
    FilesystemBilibiliPublishMaterial,
    HttpxBilibiliAccessTokenProvider,
    HttpxBilibiliOpenApiGateway,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.bilibili_publish_repository import (
    SqlAlchemyBilibiliArchivePublishStore,
    SqlAlchemyBilibiliReconciliationStore,
)

_MAX_SESSIONS: Final = 32
_SESSION_LIFETIME_SECONDS: Final = 2 * 60 * 60
_MAX_SECRET_LENGTH: Final = 4096


def _compact_secret(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= _MAX_SECRET_LENGTH
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise BilibiliArchivePublishRejected
    return value


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliPublishingCredential:
    client_id: str
    app_secret: str
    access_token: str
    refresh_token: str
    expires_at_epoch_seconds: int

    def __post_init__(self) -> None:
        _compact_secret(self.client_id)
        _compact_secret(self.app_secret)
        _compact_secret(self.access_token)
        _compact_secret(self.refresh_token)
        if type(self.expires_at_epoch_seconds) is not int or self.expires_at_epoch_seconds < 1:
            raise BilibiliArchivePublishRejected

    def __repr__(self) -> str:
        return (
            "BilibiliPublishingCredential("
            f"expires_at_epoch_seconds={self.expires_at_epoch_seconds}, <redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliCredentialRotation:
    access_token: str
    refresh_token: str
    expires_at_epoch_seconds: int

    def __post_init__(self) -> None:
        _compact_secret(self.access_token)
        _compact_secret(self.refresh_token)
        if type(self.expires_at_epoch_seconds) is not int or self.expires_at_epoch_seconds < 1:
            raise BilibiliArchivePublishRejected

    def __repr__(self) -> str:
        return (
            "BilibiliCredentialRotation("
            f"expires_at_epoch_seconds={self.expires_at_epoch_seconds}, <redacted>)"
        )


@dataclass(frozen=True, slots=True)
class BilibiliPublishRuntimeResult:
    phase: BilibiliPublishPhase
    request_digest: str
    resource_id: str | None
    replayed: bool
    credential_rotation: BilibiliCredentialRotation | None


@dataclass(slots=True, repr=False)
class _Session:
    token: str
    installation_id: InstallationId
    publish_job_id: PublishJobId
    service: BilibiliArchivePublishService
    gateway: HttpxBilibiliOpenApiGateway
    tokens: HttpxBilibiliAccessTokenProvider
    expires_at_monotonic: float
    reported_token_revision: int = 0

    def __repr__(self) -> str:
        return (
            "_Session("
            f"installation_id={self.installation_id!s}, "
            f"publish_job_id={self.publish_job_id!s}, <redacted>)"
        )

    async def aclose(self) -> None:
        await self.gateway.aclose()
        await self.tokens.aclose()

    def take_rotation(self) -> BilibiliCredentialRotation | None:
        snapshot = self.tokens.snapshot()
        if snapshot.revision <= self.reported_token_revision:
            return None
        self.reported_token_revision = snapshot.revision
        return BilibiliCredentialRotation(
            access_token=snapshot.access_token,
            refresh_token=snapshot.refresh_token,
            expires_at_epoch_seconds=snapshot.expires_at_epoch_seconds,
        )


class BilibiliPublishingRuntime:
    """Installation-bound, bounded sessions around the durable PB-03 service."""

    def __init__(self, *, database: Database, contract: BilibiliOpenApiContract) -> None:
        if not isinstance(database, Database) or not isinstance(contract, BilibiliOpenApiContract):
            raise BilibiliArchivePublishRejected
        self._contract = contract
        self._attempts = SqlAlchemyBilibiliArchivePublishStore(database)
        self._reconciliations = SqlAlchemyBilibiliReconciliationStore(database)
        self._sessions: dict[str, _Session] = {}
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return "BilibiliPublishingRuntime(<redacted>)"

    @property
    def maximum_video_bytes(self) -> int:
        return self._contract.video_max_bytes

    async def _close_sessions(self, sessions: list[_Session]) -> None:
        for session in sessions:
            with suppress(Exception):
                await session.aclose()

    async def _remove_expired_locked(self) -> list[_Session]:
        now = time.monotonic()
        expired_tokens = [
            token
            for token, session in self._sessions.items()
            if session.expires_at_monotonic <= now
        ]
        return [self._sessions.pop(token) for token in expired_tokens]

    async def aclose(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        await self._close_sessions(sessions)

    async def prepare(
        self,
        *,
        installation_id: InstallationId,
        publish_job_id: PublishJobId,
        credential: BilibiliPublishingCredential,
        material: BilibiliPublishMaterial,
        fields: BilibiliArchiveFields,
    ) -> tuple[str, BilibiliPublishRuntimeResult]:
        if (
            not isinstance(installation_id, InstallationId)
            or not isinstance(publish_job_id, PublishJobId)
            or not isinstance(credential, BilibiliPublishingCredential)
            or not isinstance(material, BilibiliPublishMaterial)
            or not isinstance(fields, BilibiliArchiveFields)
        ):
            raise BilibiliArchivePublishRejected
        api_credentials = BilibiliApiCredentials(
            client_id=credential.client_id,
            app_secret=credential.app_secret,
        )
        tokens = HttpxBilibiliAccessTokenProvider(
            contract=self._contract,
            credentials=api_credentials,
            access_token=credential.access_token,
            refresh_token=credential.refresh_token,
            expires_at_epoch_seconds=credential.expires_at_epoch_seconds,
        )
        gateway = HttpxBilibiliOpenApiGateway(
            contract=self._contract,
            credentials=api_credentials,
        )
        service = BilibiliArchivePublishService(
            contract=self._contract,
            gateway=gateway,
            token_provider=tokens,
            store=self._attempts,
        )
        try:
            preparation = await service.prepare(
                publish_job_id,
                material=material,
                fields=fields,
                with_cover=False,
            )
        except Exception:
            await gateway.aclose()
            await tokens.aclose()
            raise
        if not isinstance(preparation, BilibiliPublishPreparation):
            await gateway.aclose()
            await tokens.aclose()
            raise BilibiliArchivePublishUnavailable
        session = _Session(
            token=secrets.token_urlsafe(32),
            installation_id=installation_id,
            publish_job_id=publish_job_id,
            service=service,
            gateway=gateway,
            tokens=tokens,
            expires_at_monotonic=time.monotonic() + _SESSION_LIFETIME_SECONDS,
        )
        replaced: list[_Session] = []
        async with self._lock:
            replaced.extend(await self._remove_expired_locked())
            for token, existing in tuple(self._sessions.items()):
                if existing.installation_id == installation_id:
                    replaced.append(self._sessions.pop(token))
            if len(self._sessions) >= _MAX_SESSIONS:
                replaced.append(session)
                admitted = False
            else:
                self._sessions[session.token] = session
                admitted = True
        await self._close_sessions(replaced)
        if not admitted:
            raise BilibiliArchivePublishUnavailable
        return session.token, self._record_result(
            preparation.record.phase,
            preparation.record.request_digest,
            preparation.record.resource_id,
            preparation.replayed,
            session,
        )

    async def _session(
        self,
        *,
        installation_id: InstallationId,
        publish_job_id: PublishJobId,
        session_token: str,
    ) -> _Session:
        token = _compact_secret(session_token)
        expired: list[_Session] = []
        async with self._lock:
            expired.extend(await self._remove_expired_locked())
            session = self._sessions.get(token)
            if (
                session is None
                or session.installation_id != installation_id
                or session.publish_job_id != publish_job_id
            ):
                selected = None
            else:
                session.expires_at_monotonic = time.monotonic() + _SESSION_LIFETIME_SECONDS
                selected = session
        await self._close_sessions(expired)
        if selected is None:
            raise BilibiliArchivePublishRejected
        return selected

    async def _retire(self, token: str, session: _Session) -> None:
        retired: _Session | None = None
        async with self._lock:
            if self._sessions.get(token) is session:
                retired = self._sessions.pop(token)
        if retired is not None:
            await self._close_sessions([retired])

    def _record_result(
        self,
        phase: BilibiliPublishPhase,
        request_digest: str,
        resource_id: str | None,
        replayed: bool,
        session: _Session,
    ) -> BilibiliPublishRuntimeResult:
        return BilibiliPublishRuntimeResult(
            phase=phase,
            request_digest=request_digest,
            resource_id=resource_id,
            replayed=replayed,
            credential_rotation=session.take_rotation(),
        )

    async def upload_video(
        self,
        *,
        installation_id: InstallationId,
        publish_job_id: PublishJobId,
        session_token: str,
        material_root: Path,
    ) -> BilibiliPublishRuntimeResult:
        session = await self._session(
            installation_id=installation_id,
            publish_job_id=publish_job_id,
            session_token=session_token,
        )
        expected = await self._attempts.load(publish_job_id)
        if expected is None:
            raise BilibiliArchivePublishRejected
        reader = FilesystemBilibiliPublishMaterial(
            root=material_root,
            file_name=expected.material.file_name,
            duration_seconds=expected.material.duration_seconds,
        )
        record = await session.service.upload_video(publish_job_id, reader)
        return self._record_result(
            record.phase,
            record.request_digest,
            record.resource_id,
            False,
            session,
        )

    async def submit(
        self,
        *,
        installation_id: InstallationId,
        publish_job_id: PublishJobId,
        session_token: str,
    ) -> BilibiliPublishRuntimeResult:
        session = await self._session(
            installation_id=installation_id,
            publish_job_id=publish_job_id,
            session_token=session_token,
        )
        try:
            replayed = False
            try:
                receipt = await session.service.create_archive(publish_job_id)
                if not isinstance(receipt, BilibiliArchiveCreationReceipt):
                    raise BilibiliArchivePublishUnavailable
                replayed = receipt.replayed
            except (BilibiliArchiveOutcomeUncertain, BilibiliPublishStepFailed):
                pass
            record = await self._attempts.load(publish_job_id)
            if record is None:
                raise BilibiliArchivePublishUnavailable
            if record.phase in {
                BilibiliPublishPhase.SUBMITTED,
                BilibiliPublishPhase.OUTCOME_UNCERTAIN,
                BilibiliPublishPhase.DISPATCHED,
            }:
                await self._reconciliations.ensure_pending(
                    publish_job_id,
                    record.resource_id,
                    datetime.now(UTC),
                )
            return self._record_result(
                record.phase,
                record.request_digest,
                record.resource_id,
                replayed,
                session,
            )
        finally:
            await self._retire(session_token, session)

    async def cancel(
        self,
        *,
        installation_id: InstallationId,
        publish_job_id: PublishJobId,
        session_token: str,
    ) -> None:
        session = await self._session(
            installation_id=installation_id,
            publish_job_id=publish_job_id,
            session_token=session_token,
        )
        await self._retire(session_token, session)


__all__ = [
    "BilibiliCredentialRotation",
    "BilibiliPublishRuntimeResult",
    "BilibiliPublishingCredential",
    "BilibiliPublishingRuntime",
]

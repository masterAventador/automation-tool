"""Offline issuance of the one short-lived bootstrap a co-located App may use.

The Control Plane never holds a bootstrap signing key. A loopback deployment
therefore mints one in memory at start, configures the matching public key into
the very same registration service the Demo deployment uses, hands the signed
grant to the App through a private file and forgets the private key. Nothing is
persisted that could mint a second grant, and no new registration path exists:
the App still completes the ordinary challenge/device-proof exchange.
"""

import base64
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation_tool.control_plane.domain import DemoBootstrapGrant, DemoEnvironmentId

APP_IDENTIFIER: Final = "com.aventador.automationtool"
HANDOFF_FILE_NAME: Final = "local-registration-bootstrap-v1"
LOCAL_ENVIRONMENT_ID: Final = "local"
HANDOFF_DOCUMENT_VERSION: Final = 1
HANDOFF_DOCUMENT_FIELDS: Final = ("environmentId", "expiresAt", "token", "version")
MAX_HANDOFF_BYTES: Final = 4096
LOCAL_BOOTSTRAP_LIFETIME: Final = timedelta(minutes=10)
_TOKEN_PREFIX: Final = "atb1"
_DIRECTORY_MODE: Final = 0o700
_FILE_MODE: Final = 0o600


class LocalProvisioningUnavailable(RuntimeError):
    """A local bootstrap could not be issued, without reflecting any path."""

    def __init__(self) -> None:
        super().__init__("Local registration bootstrap is unavailable")


@dataclass(frozen=True, slots=True)
class LocalRegistrationBootstrap:
    """The non-secret half of an issued grant, for registration wiring only."""

    environment_id: str
    public_key: str


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _canonical(document: dict[str, object]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def local_app_data_directory() -> Path:
    """Resolve the App private directory this machine's desktop client owns."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_IDENTIFIER
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA")
        if not roaming:
            raise LocalProvisioningUnavailable
        return Path(roaming) / APP_IDENTIFIER
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / APP_IDENTIFIER


def _signed_token(signer: Ed25519PrivateKey, grant: DemoBootstrapGrant) -> str:
    claims = {
        "environmentId": str(grant.environment_id),
        "expiresAt": int(grant.expires_at.timestamp()),
        "notBefore": int(grant.not_before.timestamp()),
        "purpose": grant.purpose.value,
        "version": HANDOFF_DOCUMENT_VERSION,
    }
    payload_segment = _base64url(_canonical(claims))
    signing_input = f"{_TOKEN_PREFIX}.{payload_segment}".encode("ascii")
    return f"{_TOKEN_PREFIX}.{payload_segment}.{_base64url(signer.sign(signing_input))}"


def _write_private_document(directory: Path, document: bytes) -> None:
    temporary = directory / f".{HANDOFF_FILE_NAME}.{os.getpid()}.tmp"
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY_MODE)
        if sys.platform != "win32":
            os.chmod(directory, _DIRECTORY_MODE)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            _FILE_MODE,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(document)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            os.unlink(temporary)
            raise
        os.replace(temporary, directory / HANDOFF_FILE_NAME)
    except OSError:
        raise LocalProvisioningUnavailable from None


def provision_local_registration_bootstrap(
    directory: Path,
    *,
    now: datetime | None = None,
) -> LocalRegistrationBootstrap:
    """Issue one grant into the App private directory and discard the key."""
    issued_at = now if now is not None else datetime.now(UTC)
    if issued_at.utcoffset() is None:
        raise LocalProvisioningUnavailable
    not_before = int(issued_at.timestamp())
    expires_at = not_before + int(LOCAL_BOOTSTRAP_LIFETIME.total_seconds())
    grant = DemoBootstrapGrant(
        environment_id=DemoEnvironmentId.parse(LOCAL_ENVIRONMENT_ID),
        not_before=datetime.fromtimestamp(not_before, UTC),
        expires_at=datetime.fromtimestamp(expires_at, UTC),
    )
    signer = Ed25519PrivateKey.generate()
    public_key = signer.public_key().public_bytes_raw()
    token = _signed_token(signer, grant)
    del signer
    document = _canonical(
        {
            "environmentId": str(grant.environment_id),
            "expiresAt": expires_at,
            "token": token,
            "version": HANDOFF_DOCUMENT_VERSION,
        }
    )
    if len(document) > MAX_HANDOFF_BYTES:
        raise LocalProvisioningUnavailable
    _write_private_document(directory, document)
    return LocalRegistrationBootstrap(
        environment_id=LOCAL_ENVIRONMENT_ID,
        public_key=_base64url(public_key),
    )


__all__ = [
    "APP_IDENTIFIER",
    "HANDOFF_DOCUMENT_FIELDS",
    "HANDOFF_DOCUMENT_VERSION",
    "HANDOFF_FILE_NAME",
    "LOCAL_BOOTSTRAP_LIFETIME",
    "LOCAL_ENVIRONMENT_ID",
    "MAX_HANDOFF_BYTES",
    "LocalProvisioningUnavailable",
    "LocalRegistrationBootstrap",
    "local_app_data_directory",
    "provision_local_registration_bootstrap",
]

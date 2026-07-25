import json
import os
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from automation_tool.control_plane.bootstrap.local_provisioning import (
    HANDOFF_DOCUMENT_FIELDS,
    HANDOFF_DOCUMENT_VERSION,
    HANDOFF_FILE_NAME,
    LOCAL_BOOTSTRAP_LIFETIME,
    LOCAL_ENVIRONMENT_ID,
    MAX_HANDOFF_BYTES,
    LocalProvisioningUnavailable,
    LocalRegistrationBootstrap,
    local_app_data_directory,
    provision_local_registration_bootstrap,
)
from automation_tool.control_plane.domain import BootstrapPurpose, DemoEnvironmentId
from automation_tool.control_plane.infrastructure.security.bootstrap_tokens import (
    Ed25519BootstrapTokenVerifier,
)

CONTRACT_PATH = (
    Path(__file__).resolve().parents[4] / "contracts/protocol/local-registration-handoff-v1.json"
)
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def read_document(directory: Path) -> dict[str, object]:
    body = (directory / HANDOFF_FILE_NAME).read_bytes().decode("utf-8")
    return cast(dict[str, object], json.loads(body))


def test_frozen_contract_governs_every_local_provisioning_constant() -> None:
    assert CONTRACT["fileName"] == HANDOFF_FILE_NAME
    assert CONTRACT["environmentId"] == LOCAL_ENVIRONMENT_ID
    assert CONTRACT["documentVersion"] == HANDOFF_DOCUMENT_VERSION
    assert CONTRACT["documentFields"] == list(HANDOFF_DOCUMENT_FIELDS)
    assert CONTRACT["maxFileBytes"] == MAX_HANDOFF_BYTES
    assert timedelta(seconds=CONTRACT["maxLifetimeSeconds"]) == LOCAL_BOOTSTRAP_LIFETIME


def test_handoff_document_is_exact_canonical_json_within_the_frozen_bounds(
    tmp_path: Path,
) -> None:
    issued_at = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)

    bootstrap = provision_local_registration_bootstrap(tmp_path, now=issued_at)

    raw = (tmp_path / HANDOFF_FILE_NAME).read_bytes()
    assert len(raw) <= MAX_HANDOFF_BYTES
    document = json.loads(raw.decode("ascii"))
    assert sorted(document) == list(HANDOFF_DOCUMENT_FIELDS)
    assert raw == json.dumps(
        document, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    assert document["version"] == HANDOFF_DOCUMENT_VERSION
    assert document["environmentId"] == LOCAL_ENVIRONMENT_ID == bootstrap.environment_id
    assert document["expiresAt"] == int((issued_at + LOCAL_BOOTSTRAP_LIFETIME).timestamp())
    assert document["token"].startswith(f"{CONTRACT['tokenPrefix']}.")


def test_issued_token_only_authorizes_local_registration_for_the_returned_public_key(
    tmp_path: Path,
) -> None:
    issued_at = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)

    bootstrap = provision_local_registration_bootstrap(tmp_path, now=issued_at)

    assert isinstance(bootstrap, LocalRegistrationBootstrap)
    verifier = Ed25519BootstrapTokenVerifier(
        base64_url_decode(bootstrap.public_key),
    )
    verified = verifier.verify(str(read_document(tmp_path)["token"]))
    assert verified.grant.purpose is BootstrapPurpose.REGISTER_INSTALLATION
    assert verified.grant.environment_id == DemoEnvironmentId.parse(LOCAL_ENVIRONMENT_ID)
    assert verified.grant.not_before == issued_at
    assert verified.grant.expires_at - verified.grant.not_before == LOCAL_BOOTSTRAP_LIFETIME
    assert timedelta(minutes=10) >= LOCAL_BOOTSTRAP_LIFETIME


def base64_url_decode(value: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def test_signing_key_never_reaches_disk_or_the_returned_grant(tmp_path: Path) -> None:
    bootstrap = provision_local_registration_bootstrap(tmp_path)

    written = sorted(entry.name for entry in tmp_path.iterdir())
    assert written == [HANDOFF_FILE_NAME]
    assert set(vars(type(bootstrap)).get("__slots__", ())) <= {"environment_id", "public_key"}
    body = (tmp_path / HANDOFF_FILE_NAME).read_bytes()
    assert b"PRIVATE KEY" not in body


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_handoff_file_and_directory_stay_private_to_the_owner(tmp_path: Path) -> None:
    directory = tmp_path / "app-data"

    provision_local_registration_bootstrap(directory)

    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / HANDOFF_FILE_NAME).stat().st_mode) == 0o600


def test_restart_replaces_the_previous_grant_and_leaves_no_temporary_file(
    tmp_path: Path,
) -> None:
    first = provision_local_registration_bootstrap(tmp_path)
    first_token = read_document(tmp_path)["token"]

    second = provision_local_registration_bootstrap(tmp_path)

    assert second.public_key != first.public_key
    assert read_document(tmp_path)["token"] != first_token
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [HANDOFF_FILE_NAME]


def test_unwritable_app_data_directory_fails_closed_without_reflecting_the_path(
    tmp_path: Path,
) -> None:
    occupied = tmp_path / "app-data"
    occupied.write_text("not a directory", encoding="utf-8")

    with pytest.raises(LocalProvisioningUnavailable) as failure:
        provision_local_registration_bootstrap(occupied)

    assert str(occupied) not in str(failure.value)


def test_local_app_data_directory_matches_the_frozen_app_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = CONTRACT["appIdentifier"]

    directory = local_app_data_directory()

    assert directory.name == identifier
    if sys.platform == "darwin":
        assert directory == Path.home() / "Library" / "Application Support" / identifier
    elif sys.platform == "win32":
        assert directory == Path(os.environ["APPDATA"]) / identifier
    else:
        monkeypatch.setenv("XDG_DATA_HOME", "/tmp/automation-tool-xdg")
        assert local_app_data_directory() == Path("/tmp/automation-tool-xdg") / identifier

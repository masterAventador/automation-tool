"""LE-18 T3: the frozen Worker owns the complete local material transaction."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

import automation_tool.executor.local_editing_worker_process as process_module
from automation_tool.executor.local_editing_worker import (
    LocalEditingWorkerBootstrap,
    LocalMaterialForgetCommand,
    LocalMaterialImportCommand,
    LocalMaterialStatusCommand,
    LocalMaterialWorkerFailureCode,
    LocalMaterialWorkerStatus,
)
from automation_tool.executor.local_editing_worker_process import (
    LocalMaterialOperationRejected,
    execute_local_material_forget,
    execute_local_material_import,
    execute_local_material_status,
)
from automation_tool.executor.material_probe import (
    MaterialFacts,
    MaterialPathRegistry,
    MaterialPathRegistryRejected,
    MaterialPathRegistryRejection,
    MaterialProbeRejected,
    MaterialProbeRejection,
    PackagedMediaTools,
    ProbedMaterialKind,
)

MATERIAL_ID = UUID("00000000-0000-4000-8000-000000000041")
TOKEN = bytes.fromhex("41" * 32)


def _executable(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(b"controlled executable")
    path.chmod(0o700)
    return path


def _bootstrap(asset_root: Path) -> LocalEditingWorkerBootstrap:
    tools = asset_root / "tools"
    tools.mkdir(mode=0o700)
    return LocalEditingWorkerBootstrap(
        asset_root=asset_root,
        media_tools=PackagedMediaTools(
            ffmpeg_path=_executable(tools, "ffmpeg"),
            ffprobe_path=_executable(tools, "ffprobe"),
        ),
        _session_token=TOKEN,
    )


def _facts() -> MaterialFacts:
    return MaterialFacts(
        kind=ProbedMaterialKind.VIDEO,
        duration_ms=1000,
        width=720,
        height=1280,
        video_codec="h264",
        audio_codec="aac",
        has_audio=True,
        audio_loudness_lufs=-16.5,
        content_digest="ab" * 32,
    )


def test_import_executes_the_public_four_step_recipe_with_one_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset_root = tmp_path / "app-data"
    asset_root.mkdir(mode=0o700)
    source = (tmp_path / "private source.mp4").resolve()
    source.write_bytes(b"source")
    approved = source.stat()
    events: list[object] = []

    class Registry:
        def __init__(self, *, state_directory: Path) -> None:
            events.append(("registry", state_directory))

        def register(self, material_id: UUID, selected: Path) -> None:
            events.append(("register", material_id, selected))

        def forget(self, material_id: UUID) -> None:
            events.append(("forget", material_id))

    def approve(selected: Path) -> tuple[Path, os.stat_result]:
        events.append(("approve", selected))
        return selected, approved

    def probe(tools: PackagedMediaTools, selected: Path) -> MaterialFacts:
        events.append(("probe", tools, selected))
        return _facts()

    def unchanged(selected: Path, first: os.stat_result) -> tuple[Path, os.stat_result]:
        events.append(("unchanged", selected, first))
        return selected, first

    monkeypatch.setattr(process_module, "MaterialPathRegistry", Registry)
    monkeypatch.setattr(process_module, "approve_source", approve)
    monkeypatch.setattr(process_module, "probe_material", probe)
    monkeypatch.setattr(process_module, "require_source_unchanged", unchanged)
    bootstrap = _bootstrap(asset_root)

    result = execute_local_material_import(
        bootstrap, LocalMaterialImportCommand(MATERIAL_ID, source)
    )

    assert result == _facts()
    assert events == [
        ("registry", asset_root / "local-executor" / "state"),
        ("approve", source),
        ("probe", bootstrap.media_tools, source),
        ("register", MATERIAL_ID, source),
        ("unchanged", source, approved),
    ]
    state = asset_root / "local-executor" / "state"
    assert state.is_dir()
    if os.name != "nt":
        assert state.stat().st_mode & 0o777 == 0o700


def test_final_unchanged_rejection_compensates_the_persisted_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset_root = tmp_path / "app-data"
    asset_root.mkdir(mode=0o700)
    source = (tmp_path / "private source.mp4").resolve()
    source.write_bytes(b"source")
    approved = source.stat()
    events: list[str] = []

    class Registry:
        def __init__(self, *, state_directory: Path) -> None:
            pass

        def register(self, material_id: UUID, selected: Path) -> None:
            events.append("register")

        def forget(self, material_id: UUID) -> None:
            events.append("forget")

    monkeypatch.setattr(process_module, "MaterialPathRegistry", Registry)
    monkeypatch.setattr(process_module, "approve_source", lambda selected: (selected, approved))
    monkeypatch.setattr(process_module, "probe_material", lambda tools, selected: _facts())

    def changed(selected: Path, first: os.stat_result) -> None:
        events.append("unchanged")
        raise MaterialProbeRejected(MaterialProbeRejection.SOURCE_NOT_AT_REST)

    monkeypatch.setattr(process_module, "require_source_unchanged", changed)

    with pytest.raises(LocalMaterialOperationRejected) as rejected:
        execute_local_material_import(
            _bootstrap(asset_root), LocalMaterialImportCommand(MATERIAL_ID, source)
        )

    assert rejected.value.code is LocalMaterialWorkerFailureCode.SOURCE_NOT_AT_REST
    assert str(rejected.value) == "local material operation rejected"
    assert repr(rejected.value) == "LocalMaterialOperationRejected(<redacted>)"
    assert events == ["register", "unchanged", "forget"]


def test_failed_compensation_is_not_reported_as_if_the_mapping_were_forgotten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset_root = tmp_path / "app-data"
    asset_root.mkdir(mode=0o700)
    source = (tmp_path / "private source.mp4").resolve()
    source.write_bytes(b"source")
    approved = source.stat()

    class Registry:
        def __init__(self, *, state_directory: Path) -> None:
            pass

        def register(self, material_id: UUID, selected: Path) -> None:
            pass

        def forget(self, material_id: UUID) -> None:
            raise MaterialPathRegistryRejected(MaterialPathRegistryRejection.REGISTRY_UNWRITABLE)

    monkeypatch.setattr(process_module, "MaterialPathRegistry", Registry)
    monkeypatch.setattr(process_module, "approve_source", lambda selected: (selected, approved))
    monkeypatch.setattr(process_module, "probe_material", lambda tools, selected: _facts())
    monkeypatch.setattr(
        process_module,
        "require_source_unchanged",
        lambda selected, first: (_ for _ in ()).throw(
            MaterialProbeRejected(MaterialProbeRejection.SOURCE_NOT_AT_REST)
        ),
    )

    with pytest.raises(LocalMaterialOperationRejected) as rejected:
        execute_local_material_import(
            _bootstrap(asset_root), LocalMaterialImportCommand(MATERIAL_ID, source)
        )

    assert rejected.value.code is LocalMaterialWorkerFailureCode.REGISTRY_UNWRITABLE


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (
            MaterialProbeRejected(MaterialProbeRejection.UNDECODABLE),
            LocalMaterialWorkerFailureCode.UNDECODABLE,
        ),
        (
            MaterialPathRegistryRejected(MaterialPathRegistryRejection.REGISTRY_FULL),
            LocalMaterialWorkerFailureCode.REGISTRY_FULL,
        ),
    ],
)
def test_import_translates_only_closed_probe_and_registry_reasons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raised: Exception,
    expected: LocalMaterialWorkerFailureCode,
) -> None:
    asset_root = tmp_path / "app-data"
    asset_root.mkdir(mode=0o700)
    source = (tmp_path / "private source.mp4").resolve()
    source.write_bytes(b"source")
    monkeypatch.setattr(
        process_module,
        "MaterialPathRegistry",
        lambda **kwargs: cast(object, None),
    )
    monkeypatch.setattr(
        process_module,
        "approve_source",
        lambda selected: (_ for _ in ()).throw(raised),
    )

    with pytest.raises(LocalMaterialOperationRejected) as rejected:
        execute_local_material_import(
            _bootstrap(asset_root), LocalMaterialImportCommand(MATERIAL_ID, source)
        )

    assert rejected.value.code is expected
    assert str(source) not in str(rejected.value)


def test_explicit_forget_is_idempotent_and_survives_restart(tmp_path: Path) -> None:
    asset_root = tmp_path / "app-data"
    asset_root.mkdir(mode=0o700)
    bootstrap = _bootstrap(asset_root)
    source = (tmp_path / "private source.mp4").resolve()
    source.write_bytes(b"source")
    state = asset_root / "local-executor" / "state"
    state.mkdir(parents=True, mode=0o700)
    if os.name != "nt":
        state.chmod(0o700)
    MaterialPathRegistry(state_directory=state).register(MATERIAL_ID, source)
    command = LocalMaterialForgetCommand(MATERIAL_ID)

    execute_local_material_forget(bootstrap, command)
    execute_local_material_forget(bootstrap, command)

    with pytest.raises(MaterialPathRegistryRejected) as rejected:
        MaterialPathRegistry(state_directory=state).resolve(MATERIAL_ID)
    assert rejected.value.rejection is MaterialPathRegistryRejection.NOT_REGISTERED


def test_existing_non_private_state_directory_is_rejected_not_repaired(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows uses the inherited private ACL rather than POSIX mode bits")
    asset_root = tmp_path / "app-data"
    asset_root.mkdir(mode=0o700)
    bootstrap = _bootstrap(asset_root)
    state = asset_root / "local-executor" / "state"
    state.mkdir(parents=True, mode=0o755)
    state.chmod(0o755)

    with pytest.raises(LocalMaterialOperationRejected) as rejected:
        execute_local_material_forget(bootstrap, LocalMaterialForgetCommand(MATERIAL_ID))

    assert rejected.value.code is LocalMaterialWorkerFailureCode.REGISTRY_UNREADABLE
    assert state.stat().st_mode & 0o777 == 0o755


def test_status_resolves_only_availability_and_discards_the_private_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset_root = tmp_path / "app-data"
    asset_root.mkdir(mode=0o700)
    private_source = (tmp_path / "operator private source.mp4").resolve()
    approved = private_source.stat() if private_source.exists() else os.stat_result((0,) * 10)
    events: list[object] = []

    class Registry:
        def __init__(self, *, state_directory: Path) -> None:
            events.append(("registry", state_directory))

        def resolve(self, material_id: UUID) -> tuple[Path, os.stat_result]:
            events.append(("resolve", material_id))
            return private_source, approved

    monkeypatch.setattr(process_module, "MaterialPathRegistry", Registry)

    status = execute_local_material_status(
        _bootstrap(asset_root), LocalMaterialStatusCommand(MATERIAL_ID)
    )

    assert status is LocalMaterialWorkerStatus.AVAILABLE
    assert str(private_source) not in repr(status)
    assert events == [
        ("registry", asset_root / "local-executor" / "state"),
        ("resolve", MATERIAL_ID),
    ]


@pytest.mark.parametrize(
    "rejection",
    list(MaterialPathRegistryRejection),
)
def test_status_returns_each_closed_registry_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rejection: MaterialPathRegistryRejection,
) -> None:
    asset_root = tmp_path / "app-data"
    asset_root.mkdir(mode=0o700)

    class Registry:
        def __init__(self, *, state_directory: Path) -> None:
            pass

        def resolve(self, material_id: UUID) -> tuple[Path, os.stat_result]:
            raise MaterialPathRegistryRejected(rejection)

    monkeypatch.setattr(process_module, "MaterialPathRegistry", Registry)

    assert execute_local_material_status(
        _bootstrap(asset_root), LocalMaterialStatusCommand(MATERIAL_ID)
    ) is LocalMaterialWorkerStatus(rejection.value)

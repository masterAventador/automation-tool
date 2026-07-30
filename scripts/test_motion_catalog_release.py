#!/usr/bin/env python3
"""BM-14 tests: release lock contract, composition rules and the read-only release gate."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from gate_prerequisites import by_name, require  # noqa: E402

BUILD = ROOT / "scripts/build_motion_catalog_release.py"
CHECK = ROOT / "scripts/check_motion_catalog_release.py"
RELEASE_LOCK = ROOT / "contracts/video/motion-catalog-release.v1.json"
DEP_LOCK = ROOT / "contracts/video/offline-motion-dependencies.v1.json"
CATALOG = ROOT / "contracts/quality/motion-catalog.v1.json"
RIGHTS = ROOT / "contracts/quality/motion-catalog-rights.v1.json"
OVERLAY = ROOT / "contracts/quality/motion-asset-overlay.v1.json"
# Declared once in `scripts/gate_prerequisites.py`, alongside the command that
# produces it, so the remedy this test prints cannot drift from the path it
# checks. The staged tree is a build input, not something this test can
# synthesise: BM-14 rebuilds the release from it twice and requires the two
# results to be byte-identical.
STAGED_ROOT = ROOT / by_name("offline-motion-catalog").produces[0]
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def load_module(path: Path):
    assert path.is_file(), f"{path.relative_to(ROOT)} is missing"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path: Path) -> dict:
    assert path.is_file(), f"{path.relative_to(ROOT)} is missing"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), f"{path.name} must contain an object"
    return value


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def test_release_lock_contract() -> None:
    lock = load_json(RELEASE_LOCK)
    overlay = load_json(OVERLAY)
    assert lock["schemaVersion"] == 1
    assert re.match(r"^\d+\.\d+\.\d+$", lock["catalogVersion"])
    layout = lock["layout"]
    assert layout["releaseRoot"].startswith(".local/"), "release output must stay out of git"
    assert layout["itemRoot"] == "items" and layout["dependencyRoot"] == "offline-deps"

    # Every composition input is pinned by digest; drift must fail the build.
    expected_inputs = {
        "offlineDependenciesLock": DEP_LOCK,
        "motionCatalog": CATALOG,
        "motionCatalogRights": RIGHTS,
        "motionAssetOverlay": OVERLAY,
    }
    assert set(lock["inputs"]) == set(expected_inputs)
    for key, path in expected_inputs.items():
        record = lock["inputs"][key]
        assert record["path"] == path.relative_to(ROOT).as_posix(), key
        assert record["sha256"] == sha256_file(path), f"{key} digest pin drifted"

    # The scan vocabulary must cover exactly the frozen overlay indicators.
    indicators = {
        rule["indicator"] for item in overlay["items"] for rule in item["trademarkReplacements"]
    }
    forms = lock["trademarkScan"]["forms"]
    assert set(forms) == indicators, "literal forms must cover every overlay indicator"
    for token, literals in forms.items():
        assert literals and all(
            literal == literal.lower() and literal.strip() == literal for literal in literals
        ), token

    keeplist = lock["trademarkScan"]["technicalKeeplist"]
    for required in ("-apple-system", "Apple Color Emoji", "__hyperframes"):
        assert required in keeplist, f"technical keeplist must protect {required!r}"
    all_literals = {literal for literals in forms.values() for literal in literals}
    for entry in keeplist:
        lowered = entry.lower()
        assert any(literal in lowered for literal in all_literals), (
            f"keeplist entry {entry!r} protects no known indicator form"
        )

    generated = lock["generated"]
    assert generated["fileCount"] > 0
    assert SHA256_PATTERN.match(generated["aggregateSha256"])
    runtime_items = lock["runtimeDataInlining"]["items"]
    assert {entry["name"] for entry in runtime_items} == {
        "spain-map",
        "us-map",
        "us-map-bubble",
        "us-map-flow",
        "vfx-iphone-device",
        "world-map",
    }
    assert sum(len(entry["references"]) for entry in runtime_items) == 7
    content_rewrites = lock["contentRewrites"]["items"]
    assert {entry["name"] for entry in content_rewrites} == {
        "liquid-glass-notification",
        "liquid-glass-widgets",
        "texture-mask-text",
    }


def test_runtime_data_is_inlined_only_in_the_release_tree() -> None:
    build = load_module(BUILD)
    with tempfile.TemporaryDirectory(prefix="automation-tool-pc24-test-") as temporary:
        root = Path(temporary)
        document = root / "items/map/map.html"
        source = root / "offline-deps/data/map.json"
        document.parent.mkdir(parents=True)
        source.parent.mkdir(parents=True)
        document.write_text('fetch("../../offline-deps/data/map.json")', encoding="utf-8")
        source.write_bytes(b'{"kind":"map"}')
        rule = {
            "encoding": "data-url-base64",
            "items": [
                {
                    "name": "map",
                    "document": "items/map/map.html",
                    "references": [
                        {
                            "literal": "../../offline-deps/data/map.json",
                            "source": "offline-deps/data/map.json",
                            "mediaType": "application/json",
                        }
                    ],
                }
            ],
        }
        applied = build.inline_runtime_data(root, rule)
        expected = "data:application/json;base64," + base64.b64encode(source.read_bytes()).decode(
            "ascii"
        )
        assert document.read_text(encoding="utf-8") == f'fetch("{expected}")'
        assert applied == {"documents": 1, "references": 1, "sourceBytes": 14}


def test_content_rewrites_are_exact_and_closed() -> None:
    build = load_module(BUILD)
    check = load_module(CHECK)
    with tempfile.TemporaryDirectory(prefix="automation-tool-bm13-repair-test-") as temporary:
        root = Path(temporary)
        document = root / "items/demo/demo.html"
        document.parent.mkdir(parents=True)
        document.write_text("Legacy.Canvas /assets/demo/mask.png", encoding="utf-8")
        contract = {
            "items": [
                {
                    "name": "demo",
                    "document": "items/demo/demo.html",
                    "replacements": [
                        {
                            "literal": "Legacy.Canvas",
                            "replacement": "Neutral.Canvas",
                            "occurrences": 1,
                        },
                        {
                            "literal": "/assets/demo/",
                            "replacement": "./",
                            "occurrences": 1,
                        },
                    ],
                }
            ]
        }
        assert build.apply_content_rewrites(root, contract) == {
            "documents": 1,
            "replacements": 2,
        }
        assert document.read_text(encoding="utf-8") == "Neutral.Canvas ./mask.png"
        assert check.verify_content_rewrites(root, contract) == {
            "documents": 1,
            "replacements": 2,
        }
        try:
            build.apply_content_rewrites(root, contract)
        except build.BuildError:
            pass
        else:
            raise AssertionError("an already-rewritten or drifted document must fail closed")
        document.write_text("Legacy.Canvas ./mask.png", encoding="utf-8", newline="\n")
        try:
            check.verify_content_rewrites(root, contract)
        except check.CheckError:
            pass
        else:
            raise AssertionError("the independent gate must reject a restored source literal")


def test_trademark_rules() -> None:
    build = load_module(BUILD)
    lock = load_json(RELEASE_LOCK)
    replacements = {
        "vscode": "代码编辑器",
        "apple": "星云科技",
        "hyperframes": "动效画布",
        "sf_pro": "开放界面字体",
    }
    item_names = ["vscode-dark-2026", "apple-money-count"]
    ruleset = build.trademark_ruleset(lock, replacements, item_names)
    sample = "\n".join(
        (
            '<div class="vscode-theme-scene" data-composition-id="vscode-dark-2026">',
            "window.createVSCodeThemeComposition(compositionId, themeOrId);",
            'window.__timelines["apple-money-count"] = tl;',
            'font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;',
            'font-family: "Apple Color Emoji", "Segoe UI Emoji";',
            "var vars = window.__hyperframes && window.__hyperframes.getVariants();",
            "<span>HyperFrames</span>",
            "<title>Apple Terminal Basic</title>",
        )
    )
    replaced = build.apply_trademark(sample, ruleset)
    assert 'class="代码编辑器-theme-scene"' in replaced
    assert "createVSCodeThemeComposition" in replaced, "camel-case identifiers must survive"
    assert 'data-composition-id="vscode-dark-2026"' in replaced, "item ids must survive"
    assert '__timelines["apple-money-count"]' in replaced, "item ids must survive"
    assert "-apple-system" in replaced, "CSS system font keyword must survive"
    assert '"Apple Color Emoji"' in replaced, "emoji font stack entry must survive"
    assert replaced.count("window.__hyperframes") == 2, "runtime API global must survive"
    assert "<span>动效画布</span>" in replaced
    assert "<title>星云科技 Terminal Basic</title>" in replaced
    assert '"开放界面字体 Text"' in replaced

    assert build.find_trademark_leftovers(sample, ruleset), "original text must be flagged"
    assert build.find_trademark_leftovers(replaced, ruleset) == []
    assert build.apply_trademark(replaced, ruleset) == replaced, "replacement must be idempotent"


def test_composed_asset_path() -> None:
    build = load_module(BUILD)
    cases = (
        # Literally referenced files take the neutral replacement basename.
        (("assets/avatar.jpg", "generated/avatar.png", True), "assets/avatar.png"),
        (("models/iphone.glb", "models/portable-device.glb", True), "models/portable-device.glb"),
        # Dynamically addressed files keep their upstream basename (same extension only).
        (("lava.png", "generated/textures/lava.png", False), "lava.png"),
        (("concrete-042-a.png", "generated/textures/concrete.png", False), "concrete-042-a.png"),
        # Unreferenced files whose media type changes take the replacement basename.
        (
            ("assets/icons/messages.jpg", "ui/messages.svg", False),
            "assets/icons/messages.svg",
        ),
        (
            ("models/hyperframes-desktop.png", "ui/desktop-panel.svg", False),
            "models/desktop-panel.svg",
        ),
    )
    for arguments, expected in cases:
        assert build.composed_asset_path(*arguments) == expected, arguments


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o444)


def _mini_fixture(root: Path) -> tuple[Path, dict, dict, dict, dict]:
    release_root = root / "release"
    html = (
        '<html><body>\n<img src="assets/photo.svg" alt="示例" />\n<p>图片社区</p>\n</body></html>\n'
    ).encode()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="4" height="4"/></svg>\n'
    script = b"// docs: https://docs.example-lib.org/guide\nconsole.log('offline');\n"
    _write(release_root / "items/demo-social/demo-social.html", html)
    _write(release_root / "items/demo-social/assets/photo.svg", svg)
    _write(release_root / "offline-deps/js/demo.js", script)
    files = [
        {
            "path": "items/demo-social/assets/photo.svg",
            "sha256": sha256_bytes(svg),
            "bytes": len(svg),
        },
        {
            "path": "items/demo-social/demo-social.html",
            "sha256": sha256_bytes(html),
            "bytes": len(html),
        },
        {"path": "offline-deps/js/demo.js", "sha256": sha256_bytes(script), "bytes": len(script)},
    ]
    aggregate = sha256_bytes(
        "".join(
            f"{f['path']} {f['sha256']}\n" for f in sorted(files, key=lambda f: f["path"])
        ).encode("utf-8")
    )
    manifest = {
        "schemaVersion": 1,
        "catalogVersion": "1.0.0",
        "counts": {"items": 1, "files": len(files)},
        "items": [
            {
                "name": "demo-social",
                "type": "block",
                "files": [
                    "items/demo-social/assets/photo.svg",
                    "items/demo-social/demo-social.html",
                ],
                "assetReplacements": [
                    {
                        "sourcePath": "assets/photo.jpg",
                        "composedPath": "assets/photo.svg",
                        "assetId": "ui-photo",
                    }
                ],
                "trademarkReplacements": 1,
            }
        ],
        "runtimeDataInlining": {
            "documents": 0,
            "references": 0,
            "sourceBytes": 0,
        },
        "contentRewrites": {
            "documents": 0,
            "replacements": 0,
        },
        "files": files,
    }
    manifest_path = release_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest_path.chmod(0o444)
    release_lock = {
        "schemaVersion": 1,
        "catalogVersion": "1.0.0",
        "layout": {
            "releaseRoot": ".local/motion-catalog-release",
            "itemRoot": "items",
            "dependencyRoot": "offline-deps",
        },
        "trademarkScan": {
            "forms": {"instagram": ["instagram"]},
            "technicalKeeplist": ["-apple-system"],
        },
        "runtimeDataInlining": {
            "encoding": "data-url-base64",
            "items": [],
        },
        "contentRewrites": {
            "items": [],
        },
        "generated": {"fileCount": len(files), "aggregateSha256": aggregate},
    }
    dep_lock = {
        "layout": {"itemRoot": "items", "dependencyRoot": "offline-deps"},
        "embeddedDocumentationUrlDomains": ["docs.example-lib.org"],
    }
    catalog_contract = {
        "items": [
            {
                "name": "demo-social",
                "type": "block",
                "files": [{"path": "demo-social.html"}, {"path": "assets/photo.jpg"}],
            }
        ]
    }
    overlay = {
        "assetRoot": "assets/motion-catalog-overlay",
        "assets": [
            {
                "id": "ui-photo",
                "path": "ui/photo.svg",
                "sha256": sha256_bytes(svg),
                "bytes": len(svg),
            }
        ],
        "items": [
            {
                "name": "demo-social",
                "type": "block",
                "assetReplacements": [
                    {
                        "sourcePath": "assets/photo.jpg",
                        "sourceKind": "image",
                        "assetId": "ui-photo",
                        "replacementPath": "ui/photo.svg",
                    }
                ],
                "trademarkReplacements": [{"indicator": "instagram", "replacement": "图片社区"}],
            }
        ],
    }
    return release_root, release_lock, dep_lock, catalog_contract, overlay


def _rewrite_declared(release_root: Path, relative: str, data: bytes) -> None:
    target = release_root / relative
    target.chmod(0o644)
    target.write_bytes(data)
    target.chmod(0o444)
    manifest_path = release_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest["files"]:
        if record["path"] == relative:
            record["sha256"] = sha256_bytes(data)
            record["bytes"] = len(data)
    manifest_path.chmod(0o644)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    manifest_path.chmod(0o444)


def test_release_gate_tamper_matrix() -> None:
    check = load_module(CHECK)

    with tempfile.TemporaryDirectory(prefix="automation-tool-bm14-test-") as temporary:
        release_root, lock, dep_lock, contract, overlay = _mini_fixture(Path(temporary))
        check.verify_release(release_root, lock, dep_lock, contract, overlay)

    def expect_failure(name: str, mutate) -> None:
        with tempfile.TemporaryDirectory(prefix="automation-tool-bm14-test-") as temporary:
            release_root, lock, dep_lock, contract, overlay = _mini_fixture(Path(temporary))
            mutate(release_root, lock, dep_lock, contract, overlay)
            try:
                check.verify_release(release_root, lock, dep_lock, contract, overlay)
            except check.CheckError:
                return
            raise AssertionError(f"{name}: tampered release must fail")

    def digest_drift(release_root: Path, *_args) -> None:
        target = release_root / "items/demo-social/demo-social.html"
        target.chmod(0o644)
        target.write_bytes(b"<html>tampered</html>")
        target.chmod(0o444)

    def extra_file(release_root: Path, *_args) -> None:
        _write(release_root / "items/demo-social/extra.js", b"console.log('x');\n")

    def missing_file(release_root: Path, *_args) -> None:
        (release_root / "offline-deps/js/demo.js").chmod(0o644)
        (release_root / "offline-deps/js/demo.js").unlink()

    def remote_url(release_root: Path, *_args) -> None:
        _rewrite_declared(
            release_root,
            "items/demo-social/demo-social.html",
            b'<script src="https://cdn.jsdelivr.net/npm/x@1/x.js"></script>',
        )

    def trademark_leftover(release_root: Path, *_args) -> None:
        _rewrite_declared(
            release_root,
            "items/demo-social/demo-social.html",
            b"<html><p>Follow us on Instagram</p></html>",
        )

    def writable_file(release_root: Path, *_args) -> None:
        (release_root / "items/demo-social/demo-social.html").chmod(0o644)

    def unapplied_asset(release_root: Path, _lock, _dep, _contract, overlay) -> None:
        overlay["assets"][0]["sha256"] = "1" * 64

    def missing_replacement_entry(release_root: Path, *_args) -> None:
        manifest_path = release_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["items"][0]["assetReplacements"] = []
        manifest_path.chmod(0o644)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        manifest_path.chmod(0o444)

    def missing_item(release_root: Path, _lock, _dep, contract, _overlay) -> None:
        contract["items"].append(
            {"name": "second-item", "type": "block", "files": [{"path": "second-item.html"}]}
        )

    def aggregate_drift(_release_root: Path, lock, *_args) -> None:
        lock["generated"]["aggregateSha256"] = "0" * 64

    def symlink_file(release_root: Path, *_args) -> None:
        item = release_root / "items/demo-social"
        if os.name == "nt":
            completed = subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(item / "linked-assets"),
                    str(item / "assets"),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            assert completed.returncode == 0, "mklink /J failed"
            return
        (item / "link.svg").symlink_to(item / "assets/photo.svg")

    def casefold_collision(release_root: Path, *_args) -> None:
        manifest_path = release_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        duplicate = dict(manifest["files"][0])
        duplicate["path"] = duplicate["path"].upper()
        manifest["files"].append(duplicate)
        manifest_path.chmod(0o644)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
            newline="\n",
        )
        manifest_path.chmod(0o444)

    expect_failure("digest drift", digest_drift)
    expect_failure("undeclared extra file", extra_file)
    expect_failure("declared file missing", missing_file)
    expect_failure("remote URL with matching digest", remote_url)
    expect_failure("trademark indicator leftover", trademark_leftover)
    expect_failure("writable file breaks the read-only guarantee", writable_file)
    expect_failure("composed asset no longer matches the overlay digest", unapplied_asset)
    expect_failure("asset replacement entry hidden from the manifest", missing_replacement_entry)
    expect_failure("missing catalog item", missing_item)
    expect_failure("aggregate drift against the release lock", aggregate_drift)
    expect_failure("link/reparse point in the release tree", symlink_file)
    expect_failure("case-insensitive manifest path collision", casefold_collision)


def test_windows_unicode_and_read_only_path_semantics() -> None:
    if os.name != "nt":
        return
    with tempfile.TemporaryDirectory(prefix="automation-tool-bm14-unicode-") as temporary:
        root = Path(temporary)
        path = root / "目录-Ångström" / "头像-É.svg"
        path.parent.mkdir()
        path.write_bytes(b"<svg/>")
        path.chmod(0o444)
        metadata = path.stat()
        assert not metadata.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        assert metadata.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        case_variant = root / "目录-ångström" / "头像-é.svg"
        assert case_variant.samefile(path), "NTFS case folding must resolve one file"
        assert path.relative_to(root).as_posix() == "目录-Ångström/头像-É.svg"
        assert path.read_bytes() == b"<svg/>"
        path.chmod(0o644)


def test_real_release_build_is_reproducible() -> None:
    build = load_module(BUILD)
    check = load_module(CHECK)
    require("offline-motion-catalog")
    lock = load_json(RELEASE_LOCK)
    dep_lock = load_json(DEP_LOCK)
    catalog_contract = load_json(CATALOG)
    rights = load_json(RIGHTS)
    overlay = load_json(OVERLAY)

    with tempfile.TemporaryDirectory(prefix="automation-tool-bm14-test-") as temporary:
        first_root = Path(temporary) / "first"
        second_root = Path(temporary) / "second"
        first = build.build_release(
            lock, dep_lock, catalog_contract, rights, overlay, STAGED_ROOT, first_root
        )
        build.build_release(
            lock, dep_lock, catalog_contract, rights, overlay, STAGED_ROOT, second_root
        )
        first_manifest = (first_root / "manifest.json").read_bytes()
        second_manifest = (second_root / "manifest.json").read_bytes()
        assert first_manifest == second_manifest, "two builds must be byte-identical"
        assert first["counts"]["items"] == 134
        applied = sum(len(item["assetReplacements"]) for item in first["items"])
        assert applied == overlay["counts"]["assetReplacementReferences"]
        aggregate = build.aggregate_digest(first["files"])
        assert aggregate == lock["generated"]["aggregateSha256"], (
            "release aggregate must match the locked digest"
        )
        for record in first["files"]:
            path = first_root / record["path"]
            metadata = path.stat()
            assert not metadata.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH), (
                f"release file must be read-only: {record['path']}"
            )
            if os.name == "nt":
                assert metadata.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
            if path.suffix.lower() in {".html", ".js", ".css", ".svg"}:
                assert b"\r\n" not in path.read_bytes(), (
                    f"generated text must use LF on every platform: {record['path']}"
                )
        check.verify_release(first_root, lock, dep_lock, catalog_contract, overlay)
        for root in (first_root, second_root):
            for path in sorted(root.rglob("*"), reverse=True):
                if path.is_file():
                    path.chmod(0o644)


def main() -> None:
    test_release_lock_contract()
    test_runtime_data_is_inlined_only_in_the_release_tree()
    test_content_rewrites_are_exact_and_closed()
    test_trademark_rules()
    test_composed_asset_path()
    test_release_gate_tamper_matrix()
    test_windows_unicode_and_read_only_path_semantics()
    test_real_release_build_is_reproducible()
    print("motion catalog release tests passed")
    print("executed checks: 8")


if __name__ == "__main__":
    main()

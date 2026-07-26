#!/usr/bin/env python3
"""BM-12 tests: offline dependency lock manifest, builder rewrites and scan gate."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts/build_offline_motion_catalog.py"
CHECK = ROOT / "scripts/check_offline_motion_catalog.py"
LOCK = ROOT / "contracts/video/offline-motion-dependencies.v1.json"
CATALOG = ROOT / "contracts/quality/motion-catalog.v1.json"
RIGHTS = ROOT / "contracts/quality/motion-catalog-rights.v1.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LOCALIZABLE_CATEGORIES = {
    "jsdelivr",
    "google_fonts_css",
    "google_fonts_static",
    "cloudflare_cdn",
    "gstatic_draco",
}


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


def test_lock_manifest_contract() -> None:
    lock = load_json(LOCK)
    catalog = load_json(CATALOG)
    rights = load_json(RIGHTS)
    assert lock["schemaVersion"] == 1
    assert lock["source"] == {
        "id": catalog["source"]["id"],
        "commit": catalog["source"]["commit"],
        "tag": catalog["source"]["tag"],
    }, "lock manifest must pin the same upstream source as the frozen catalog"

    # Every remote package from the frozen rights ledger must be verified here.
    ledger_packages = {entry["package"] for entry in rights["remoteDependencyPackages"]}
    lock_packages = {entry["package"]: entry for entry in lock["packages"]}
    assert set(lock_packages) == ledger_packages, (
        f"lock packages {sorted(lock_packages)} != ledger packages {sorted(ledger_packages)}"
    )
    for name, record in lock_packages.items():
        assert record["license"] and record["license"].lower() != "unverified", name
        assert record["verification"].startswith("verified_"), (
            f"{name} license must be verified, not assumed"
        )
        assert record["redistributable"] is True, name
        assert record["evidence"]["licenseFiles"] or record["evidence"]["packageJsonLicense"], (
            f"{name} needs concrete license evidence"
        )

    # Every Google Fonts family used by the 134 items must have a verified license.
    ledger_families = {
        family for item in rights["items"] for family in item.get("googleFontFamilies", [])
    }
    lock_families = {entry["family"]: entry for entry in lock["fontFamilies"]}
    assert set(lock_families) == ledger_families
    for family, record in lock_families.items():
        assert record["license"] == "OFL-1.1", f"{family} must carry a redistributable license"
        assert record["redistributable"] is True, family
        assert SHA256_PATTERN.match(record["licenseFileSha256"]), family
        assert (
            SHA256_PATTERN.match(record["googleFontsCommit"][:40] + "0" * 24)
            or len(record["googleFontsCommit"]) == 40
        ), family

    # Artifacts must be canonical, unique and digest-locked.
    seen_paths: set[str] = set()
    artifact_paths: set[str] = set()
    original_urls: set[str] = set()
    for artifact in lock["artifacts"]:
        path = artifact["localPath"]
        assert path.startswith("offline-deps/") and ".." not in path.split("/"), path
        assert path not in seen_paths, f"duplicate artifact path: {path}"
        seen_paths.add(path)
        artifact_paths.add(path)
        assert SHA256_PATTERN.match(artifact["sha256"]), path
        assert artifact["bytes"] > 0, path
        assert artifact["downloadUrl"].startswith("https://"), path
        original_urls.update(artifact["originalUrls"])

    # Stylesheets must cover css2 URLs and reference locked woff2 artifacts only.
    stylesheet_urls = set()
    for sheet in lock["stylesheets"]:
        stylesheet_urls.add(sheet["originalUrl"])
        assert sheet["localPath"].startswith("offline-deps/fonts/css/")
        assert sheet["faces"], sheet["originalUrl"]
        for face in sheet["faces"]:
            assert face["subset"] in lock["keptFontSubsets"], face
            assert face["artifactPath"] in artifact_paths, (
                f"stylesheet face references unknown artifact: {face['artifactPath']}"
            )
            assert face["family"] in lock_families, face["family"]

    # Full localizable URL coverage: nothing from the frozen audit may be left over.
    prefix_rules = [rule["from"] for rule in lock["rewrites"]["prefixReplacements"]]
    text_rules = [rule["from"] for rule in lock["rewrites"]["textReplacements"]]
    remove_domains = set(lock["rewrites"]["removeLinkDomains"])
    uncovered: set[str] = set()
    for item in rights["items"]:
        for url in (item.get("remoteDependencies") or {}).get("urls", []):
            domain = url.split("/")[2]
            if url in original_urls or url in stylesheet_urls or url in text_rules:
                continue
            if any(url.startswith(prefix) for prefix in prefix_rules):
                continue
            if url == f"https://{domain}" and domain in remove_domains:
                continue
            uncovered.add(url)
    assert not uncovered, f"audited remote URLs without localization rule: {sorted(uncovered)}"


def test_builder_rewrites() -> None:
    build = load_module(BUILD)
    lock = load_json(LOCK)
    rules = build.rewrite_rules(lock)
    sample = "\n".join(
        (
            '<link rel="preconnect" href="https://fonts.googleapis.com" />',
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />',
            '<link href="https://fonts.googleapis.com/css2?family=Anton&display=swap"'
            ' rel="stylesheet" />',
            '<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>',
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js">'
            "</script>",
            'draco.setDecoderPath("https://www.gstatic.com/draco/versioned/decoders/1.5.6/");',
            'var url = "https://cdn.jsdelivr.net/npm/us-atlas@3/states-10m.json";',
            "<span>Fetching https://api.example.com/status</span>",
            '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
        )
    )
    rewritten = build.rewrite_text(sample, rules, depth=2)
    assert "fonts.googleapis.com" not in rewritten
    assert "fonts.gstatic.com" not in rewritten
    assert "preconnect" not in rewritten
    assert '"../../offline-deps/js/gsap-3.14.2/gsap.min.js"' in rewritten
    assert '"../../offline-deps/js/three.js-r128/three.min.js"' in rewritten
    assert '"../../offline-deps/draco/1.5.6/"' in rewritten
    assert "states-10m.json" in rewritten and "cdn.jsdelivr.net" not in rewritten
    assert "Fetching api.example.com/status" in rewritten
    assert 'xmlns="http://www.w3.org/2000/svg"' in rewritten, "w3 namespaces must survive"
    leftovers = [
        url
        for url in re.findall(r"https?://[^\s\"'`<>)\\]+", rewritten)
        if url.split("/")[2] != "www.w3.org"
    ]
    assert not leftovers, f"rewrite left remote URLs: {leftovers}"

    try:
        build.rewrite_text('<script src="https://evil.example.net/x.js"></script>', rules, depth=2)
    except build.BuildError:
        pass
    else:
        raise AssertionError("unknown remote URL must fail the build")

    css = build.stylesheet_css(lock["stylesheets"][0])
    assert css == build.stylesheet_css(lock["stylesheets"][0]), "css must be deterministic"
    assert "@font-face" in css and "url(../woff2/" in css
    assert "https://" not in css and "http://" not in css


def _mini_fixture(root: Path) -> tuple[dict, dict, dict]:
    catalog_root = root / "catalog"
    item_dir = catalog_root / "items/demo-item"
    item_dir.mkdir(parents=True)
    dep_dir = catalog_root / "offline-deps/js"
    dep_dir.mkdir(parents=True)
    html = "<html><body>offline</body></html>"
    (item_dir / "demo-item.html").write_text(html, encoding="utf-8", newline="\n")
    script = "// docs: https://docs.example-lib.org/guide\nconsole.log('offline');"
    (dep_dir / "demo.js").write_text(script, encoding="utf-8", newline="\n")
    files = [
        {
            "path": "items/demo-item/demo-item.html",
            "sha256": sha256_bytes(html.encode()),
            "bytes": len(html.encode()),
        },
        {
            "path": "offline-deps/js/demo.js",
            "sha256": sha256_bytes(script.encode()),
            "bytes": len(script.encode()),
        },
    ]
    aggregate = sha256_bytes(
        "".join(
            f"{f['path']} {f['sha256']}\n" for f in sorted(files, key=lambda f: f["path"])
        ).encode()
    )
    manifest = {
        "schemaVersion": 1,
        "source": {"id": "hyperframes", "commit": "0" * 40, "tag": "v0"},
        "counts": {"items": 1, "files": len(files)},
        "items": [
            {
                "name": "demo-item",
                "type": "block",
                "pendingAssetReplacement": True,
                "files": ["items/demo-item/demo-item.html"],
            }
        ],
        "files": files,
    }
    (catalog_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    lock = {
        "layout": {"itemRoot": "items", "dependencyRoot": "offline-deps"},
        "embeddedDocumentationUrlDomains": ["docs.example-lib.org"],
        "generated": {"fileCount": len(files), "aggregateSha256": aggregate},
    }
    catalog_contract = {
        "items": [
            {
                "name": "demo-item",
                "type": "block",
                "files": [{"path": "demo-item.html"}],
            }
        ]
    }
    rights = {
        "items": [{"name": "demo-item", "conclusion": "needs_localization_and_asset_replacement"}]
    }
    return lock, catalog_contract, rights


def test_check_tamper_matrix() -> None:
    check = load_module(CHECK)

    def expect_failure(name: str, mutate) -> None:
        with tempfile.TemporaryDirectory(prefix="automation-tool-bm12-test-") as temporary:
            root = Path(temporary)
            lock, catalog_contract, rights = _mini_fixture(root)
            mutate(root / "catalog", lock, catalog_contract, rights)
            try:
                check.verify_catalog(root / "catalog", lock, catalog_contract, rights)
            except check.CheckError:
                return
            raise AssertionError(f"{name}: tampered catalog must fail")

    with tempfile.TemporaryDirectory(prefix="automation-tool-bm12-test-") as temporary:
        root = Path(temporary)
        lock, catalog_contract, rights = _mini_fixture(root)
        check.verify_catalog(root / "catalog", lock, catalog_contract, rights)

    def digest_drift(catalog_root: Path, *_args) -> None:
        (catalog_root / "items/demo-item/demo-item.html").write_text(
            "<html>tampered</html>", encoding="utf-8", newline="\n"
        )

    def extra_file(catalog_root: Path, *_args) -> None:
        (catalog_root / "offline-deps/js/extra.js").write_text("x", encoding="utf-8", newline="\n")

    def missing_file(catalog_root: Path, *_args) -> None:
        (catalog_root / "offline-deps/js/demo.js").unlink()

    def remote_url(catalog_root: Path, *_args) -> None:
        path = catalog_root / "items/demo-item/demo-item.html"
        body = '<script src="https://cdn.jsdelivr.net/npm/x@1/x.js"></script>'
        path.write_text(body, encoding="utf-8", newline="\n")
        manifest = json.loads((catalog_root / "manifest.json").read_text(encoding="utf-8"))
        for record in manifest["files"]:
            if record["path"].endswith("demo-item.html"):
                record["sha256"] = sha256_bytes(body.encode())
                record["bytes"] = len(body.encode())
        (catalog_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
            newline="\n",
        )

    def documentation_url_in_item_file(catalog_root: Path, *_args) -> None:
        path = catalog_root / "items/demo-item/demo-item.html"
        body = "<html><!-- https://docs.example-lib.org/guide --></html>"
        path.write_text(body, encoding="utf-8", newline="\n")
        manifest = json.loads((catalog_root / "manifest.json").read_text(encoding="utf-8"))
        for record in manifest["files"]:
            if record["path"].endswith("demo-item.html"):
                record["sha256"] = sha256_bytes(body.encode())
                record["bytes"] = len(body.encode())
        (catalog_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
            newline="\n",
        )

    def unlisted_domain_in_dependency(catalog_root: Path, *_args) -> None:
        path = catalog_root / "offline-deps/js/demo.js"
        body = "// docs: https://unreviewed.example.net/guide\nconsole.log('offline');"
        path.write_text(body, encoding="utf-8", newline="\n")
        manifest = json.loads((catalog_root / "manifest.json").read_text(encoding="utf-8"))
        for record in manifest["files"]:
            if record["path"].endswith("demo.js"):
                record["sha256"] = sha256_bytes(body.encode())
                record["bytes"] = len(body.encode())
        (catalog_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
            newline="\n",
        )

    def hidden_pending_item(catalog_root: Path, *_args) -> None:
        manifest = json.loads((catalog_root / "manifest.json").read_text(encoding="utf-8"))
        manifest["items"][0]["pendingAssetReplacement"] = False
        (catalog_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
            newline="\n",
        )

    def missing_item(catalog_root: Path, _lock, catalog_contract, _rights) -> None:
        catalog_contract["items"].append(
            {"name": "second-item", "type": "block", "files": [{"path": "second-item.html"}]}
        )

    def aggregate_drift(_catalog_root: Path, lock, *_args) -> None:
        lock["generated"]["aggregateSha256"] = "0" * 64

    expect_failure("digest drift", digest_drift)
    expect_failure("extra undeclared file", extra_file)
    expect_failure("missing file", missing_file)
    expect_failure("remote URL leak with matching digest", remote_url)
    expect_failure(
        "documentation URL is still remote inside item files", documentation_url_in_item_file
    )
    expect_failure("unreviewed embedded domain in dependency file", unlisted_domain_in_dependency)
    expect_failure("pending BM-13 item hidden as done", hidden_pending_item)
    expect_failure("missing catalog item", missing_item)
    expect_failure("aggregate drift against lock", aggregate_drift)


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_: object) -> bool:
        return False


def _with_patched_urlopen(build, responder):
    """Swap the transport `fetch` uses, and always put the real one back.

    `fetch` is a module-level function over the shared `urllib` module, so the
    patch is process-wide while it is installed. Restoring it in a `finally` is
    what keeps a failure in one of these cases from silently disabling every
    download in the ones that follow.
    """
    import contextlib
    import urllib.request

    @contextlib.contextmanager
    def patched():
        original = urllib.request.urlopen
        urllib.request.urlopen = responder
        try:
            yield
        finally:
            urllib.request.urlopen = original

    return patched()


def test_fetch_survives_transient_download_failures() -> None:
    """A download that fails once must not fail the whole build.

    Measured on 2026-07-26 while building this catalog from a clean tree: 6
    requests, 5 succeeded, 1 failed, and because `fetch` made exactly one
    attempt the build aborted. Only the per-file download cache made a rerun
    cheap enough to get through -- it took four runs. Any gate that names this
    builder as its prerequisite inherits that failure rate, so the retry has to
    exist before the prerequisite is declared, not after.
    """
    build = load_module(BUILD)
    build.DOWNLOAD_RETRY_BACKOFF_SECONDS = (0.0, 0.0, 0.0)
    attempts: list[str] = []

    def flaky(request, timeout=None):  # noqa: ANN001
        attempts.append(request.full_url)
        if len(attempts) < 3:
            raise OSError("[Errno 54] Connection reset by peer")
        return _FakeResponse(b"payload")

    payload = None
    failure: BaseException | None = None
    with _with_patched_urlopen(build, flaky):
        try:
            payload = build.fetch("https://example.invalid/asset.js")
        except BaseException as error:  # noqa: BLE001
            failure = error

    assert payload == b"payload", (
        "fetch must retry a transient network failure rather than abort the "
        f"whole build on the first one; attempts={len(attempts)} failure={failure!r}"
    )
    assert len(attempts) == 3, f"expected 3 attempts, made {len(attempts)}"


def test_fetch_gives_up_loudly_instead_of_retrying_forever() -> None:
    build = load_module(BUILD)
    build.DOWNLOAD_RETRY_BACKOFF_SECONDS = (0.0, 0.0, 0.0)
    attempts: list[str] = []

    def always_down(request, timeout=None):  # noqa: ANN001
        attempts.append(request.full_url)
        raise OSError("[Errno 60] Operation timed out")

    failure: BaseException | None = None
    with _with_patched_urlopen(build, always_down):
        try:
            build.fetch("https://example.invalid/asset.js")
        except BaseException as error:  # noqa: BLE001
            failure = error

    assert isinstance(failure, build.BuildError), (
        f"a download that never recovers must fail the build, got {failure!r}"
    )
    assert len(attempts) == 4, (
        f"the retry budget must be bounded and exhausted, made {len(attempts)} attempts"
    )
    assert "4 attempt" in str(failure), (
        f"the failure must say how hard it tried: {failure}"
    )


def test_fetch_does_not_retry_a_url_that_will_never_exist() -> None:
    """A 404 is an answer, not a hiccup. Retrying it only delays the report."""
    import urllib.error

    build = load_module(BUILD)
    build.DOWNLOAD_RETRY_BACKOFF_SECONDS = (0.0, 0.0, 0.0)
    attempts: list[str] = []

    def not_found(request, timeout=None):  # noqa: ANN001
        attempts.append(request.full_url)
        raise urllib.error.HTTPError(
            request.full_url, 404, "Not Found", {}, None  # type: ignore[arg-type]
        )

    failure: BaseException | None = None
    with _with_patched_urlopen(build, not_found):
        try:
            build.fetch("https://example.invalid/gone.js")
        except BaseException as error:  # noqa: BLE001
            failure = error

    assert isinstance(failure, build.BuildError), f"unexpected outcome: {failure!r}"
    assert len(attempts) == 1, (
        f"a permanent 404 must be reported at once, made {len(attempts)} attempts"
    )


def main() -> None:
    test_lock_manifest_contract()
    test_builder_rewrites()
    test_check_tamper_matrix()
    test_fetch_survives_transient_download_failures()
    test_fetch_gives_up_loudly_instead_of_retrying_forever()
    test_fetch_does_not_retry_a_url_that_will_never_exist()
    print("offline motion catalog tests passed")
    print("executed checks: 3")


if __name__ == "__main__":
    main()

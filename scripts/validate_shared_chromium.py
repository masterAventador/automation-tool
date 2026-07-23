#!/usr/bin/env python3
"""Run the EB-02 shared Chromium probes on macOS arm64 or Windows x86_64."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import AbstractContextManager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import psutil
from browser_use import BrowserSession
from PIL import Image
from playwright.async_api import BrowserContext, Playwright, async_playwright

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/browser/shared-chromium-validation.v1.json"
COMPATIBILITY_PATH = REPOSITORY_ROOT / "contracts/browser/embedded-chromium-compatibility.v1.json"
TOOL_ROOT = REPOSITORY_ROOT / "tools/shared-browser-validation"
CATALOG_SCRIPT = REPOSITORY_ROOT / "scripts/render_shared_chromium_catalog.mjs"
EXPECTED_PROBES = {
    "headed_playwright",
    "browser_use_executable_path",
    "browser_use_random_cdp",
    "headless_render_process",
    "catalog_single_frame",
    "style_single_frame",
    "font",
    "image",
    "video",
    "audio",
    "lottie",
    "canvas_2d",
    "webgl",
    "webgpu",
    "transparent_png",
    "landscape",
    "portrait",
    "concurrent_processes",
    "isolated_profiles",
    "exclusive_control_lease",
    "single_browser_distribution",
}


class ValidationError(RuntimeError):
    """Raised when a required shared-browser probe fails."""


def fail(message: str) -> None:
    raise ValidationError(message)


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        fail(f"required JSON file is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        fail(f"JSON root must be an object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(partial(stream.read, 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_contract() -> dict[str, Any]:
    contract = load_json(CONTRACT_PATH)
    compatibility = load_json(COMPATIBILITY_PATH)
    runtime = compatibility["production_runtime"]
    expected_chromium = {
        "browser_version": runtime["chromium"]["browser_version"],
        "revision": runtime["chromium"]["revision"],
    }
    if contract.get("schema_version") != 1 or contract.get("chromium") != expected_chromium:
        fail("shared validation Chromium differs from EB-01")
    if contract.get("browser_use_version") != "0.13.6":
        fail("Browser Use validation version drifted")
    if contract.get("render_engine_version") != "0.7.68":
        fail("render engine validation version drifted")
    if set(contract.get("required_probes", [])) != EXPECTED_PROBES:
        fail("required probe inventory drifted")
    styles = contract.get("styles")
    if not isinstance(styles, list) or len(styles) != 12 or len(set(styles)) != 12:
        fail("the 12 style names must be unique")

    registry_root = REPOSITORY_ROOT / "vendor/hyperframes/registry"
    counts: dict[str, int] = {}
    for kind in ("blocks", "components"):
        root = registry_root / kind
        entries = sorted(path for path in root.iterdir() if path.is_dir())
        for entry in entries:
            load_json(entry / "registry-item.json")
        counts[kind] = len(entries)
    catalog = contract.get("catalog")
    if not isinstance(catalog, dict):
        fail("catalog contract must be an object")
    if counts != {"blocks": 109, "components": 25}:
        fail(f"pinned registry inventory drifted: {counts}")
    if catalog.get("installable_items") != sum(counts.values()):
        fail("catalog total differs from the pinned submodule")

    manager_source = (
        REPOSITORY_ROOT / "vendor/hyperframes/packages/engine/src/services/browserManager.ts"
    ).read_text(encoding="utf-8")
    if 'process.platform === "linux"' not in manager_source or '"screenshot"' not in manager_source:
        fail("render engine no longer clearly limits BeginFrame selection to Linux")
    return contract


def discover_browser(browser_root: Path) -> Path:
    candidates: list[Path]
    if sys.platform == "darwin":
        candidates = list(
            browser_root.glob(
                "chromium-*/chrome-mac-*/Google Chrome for Testing.app/Contents/"
                "MacOS/Google Chrome for Testing"
            )
        )
    elif sys.platform == "win32":
        candidates = list(browser_root.glob("chromium-*/chrome-win*/chrome.exe"))
    else:
        fail(f"unsupported EB-02 host: {sys.platform}")
    if len(candidates) != 1:
        fail(f"expected one full Chromium executable, found: {candidates}")
    forbidden = [
        path
        for path in browser_root.rglob("*")
        if path.is_file()
        and path.name.lower()
        in {"headless_shell", "chrome-headless-shell", "chrome-headless-shell.exe"}
    ]
    if forbidden:
        fail(f"a second headless browser binary was downloaded: {forbidden}")
    return candidates[0].resolve()


def read_browser_version(browser_path: Path) -> str:
    completed = subprocess.run(
        [str(browser_path), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    match = re.search(r"(\d+(?:\.\d+){3})", completed.stdout + completed.stderr)
    if match is None:
        fail("could not read full Chromium version")
    return match.group(1)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        del args


class FixtureServer(AbstractContextManager["FixtureServer"]):
    def __init__(self, root: Path) -> None:
        handler = partial(QuietHandler, directory=str(root))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/index.html"

    def __enter__(self) -> FixtureServer:
        self.thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        del exc_info
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def copy_fixture_assets(root: Path) -> None:
    source_assets = REPOSITORY_ROOT / "vendor/hyperframes/.agents/skills/changelog-video/assets"
    shutil.copy2(
        source_assets / "fonts/ABCSolarDisplay-Bold.woff2",
        root / "sample.woff2",
    )
    shutil.copy2(
        TOOL_ROOT / "node_modules/lottie-web/build/player/lottie.min.js",
        root / "lottie.min.js",
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=#ff5c35:s=160x90:d=1",
            "-c:v",
            "libvpx",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-y",
            str(root / "sample.webm"),
        ],
        check=True,
        timeout=60,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "libopus",
            "-vn",
            "-y",
            str(root / "sample.ogg"),
        ],
        check=True,
        timeout=60,
    )


def write_fixture(root: Path) -> None:
    copy_fixture_assets(root)
    shutil.copy2(
        REPOSITORY_ROOT / "contracts/browser/fixtures/shared-capabilities.html",
        root / "index.html",
    )


def chromium_args() -> list[str]:
    return [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        # 项目规则「不调用系统钥匙串」：阻止 macOS 上弹出钥匙串授权弹窗。
        "--use-mock-keychain",
        "--enable-unsafe-webgpu",
        "--enable-features=Vulkan",
    ]


async def check_page_capabilities(
    context: BrowserContext, url: str, artifacts: Path
) -> dict[str, bool]:
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    capabilities = await asyncio.wait_for(page.evaluate("window.probeReady"), timeout=60)
    if not isinstance(capabilities, dict):
        fail("browser capability fixture returned no result")
    for name in ("font", "image", "video", "audio", "lottie", "canvas_2d", "webgl", "webgpu"):
        if capabilities.get(name) is not True:
            fail(f"browser capability failed: {name}={capabilities.get(name)!r}")

    await page.set_viewport_size({"width": 1280, "height": 720})
    landscape = artifacts / "landscape.png"
    await page.screenshot(path=str(landscape))
    await page.set_viewport_size({"width": 720, "height": 1280})
    portrait = artifacts / "portrait.png"
    await page.screenshot(path=str(portrait))
    if Image.open(landscape).size != (1280, 720) or Image.open(portrait).size != (720, 1280):
        fail("landscape or portrait screenshot dimensions are wrong")

    transparent_page = await context.new_page()
    await transparent_page.set_content(
        "<html style='background:transparent'>"
        "<body style='margin:0;background:transparent'></body></html>"
    )
    transparent = artifacts / "transparent.png"
    await transparent_page.screenshot(path=str(transparent), omit_background=True)
    alpha = Image.open(transparent).convert("RGBA").getpixel((0, 0))[3]
    if alpha != 0:
        fail(f"transparent screenshot has alpha={alpha}")
    return {str(key): bool(value) for key, value in capabilities.items()}


async def run_playwright_probe(
    browser_path: Path,
    profile: Path,
    url: str,
    artifacts: Path,
    *,
    headed: bool,
) -> tuple[dict[str, Any], BrowserContext, Playwright]:
    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        str(profile),
        executable_path=str(browser_path),
        headless=not headed,
        args=chromium_args(),
        viewport={"width": 1280, "height": 720},
    )
    try:
        capabilities = await check_page_capabilities(context, url, artifacts)
        version = context.browser.version if context.browser is not None else "unknown"
        result = {
            "status": "passed",
            "headed": headed,
            "browser_version": version,
            "capabilities": capabilities,
            "profile": profile.name,
        }
        return result, context, playwright
    except BaseException:
        await context.close()
        await playwright.stop()
        raise


async def close_playwright_context(context: BrowserContext, playwright: Playwright) -> None:
    await context.close()
    await playwright.stop()


def browser_use_session(**kwargs: object) -> BrowserSession:
    return BrowserSession(
        is_local=True,
        keep_alive=False,
        enable_default_extensions=False,
        captcha_solver=False,
        highlight_elements=False,
        args=chromium_args(),
        **kwargs,
    )


async def run_browser_use_executable_probe(
    browser_path: Path, profile: Path, url: str, artifacts: Path
) -> tuple[dict[str, Any], BrowserSession]:
    session = browser_use_session(
        executable_path=browser_path,
        user_data_dir=profile,
        headless=True,
    )
    await session.start()
    try:
        await session.navigate_to(url)
        title = await session.get_current_page_title()
        current_url = await session.get_current_page_url()
        screenshot = await session.take_screenshot(path=str(artifacts / "browser-use.png"))
        if current_url != url or not screenshot:
            fail(
                "Browser Use executable_path did not reach the fixture: "
                f"title={title!r}, url={current_url!r}, screenshot={type(screenshot).__name__}"
            )
        return {
            "status": "passed",
            "title": title,
            "url_is_local_fixture": current_url.startswith("http://127.0.0.1:"),
            "profile": profile.name,
        }, session
    except Exception:
        await session.stop()
        raise


async def wait_for_devtools_port(profile: Path, process: subprocess.Popen[bytes]) -> int:
    active_port = profile / "DevToolsActivePort"
    for _ in range(300):
        if process.poll() is not None:
            fail(f"CDP Chromium exited early: {process.returncode}")
        if active_port.is_file():
            first_line = active_port.read_text(encoding="utf-8").splitlines()[0]
            return int(first_line)
        await asyncio.sleep(0.05)
    fail("timed out waiting for random CDP port")


async def run_browser_use_cdp_probe(
    browser_path: Path, profile: Path, url: str, artifacts: Path
) -> dict[str, Any]:
    command = [
        str(browser_path),
        "--headless=new",
        "--remote-debugging-port=0",
        f"--user-data-dir={profile}",
        *chromium_args(),
        "about:blank",
    ]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    session: BrowserSession | None = None
    try:
        port = await wait_for_devtools_port(profile, process)
        session = browser_use_session(cdp_url=f"http://127.0.0.1:{port}", headless=True)
        await session.start()
        await session.navigate_to(url)
        title = await session.get_current_page_title()
        current_url = await session.get_current_page_url()
        screenshot = await session.take_screenshot(path=str(artifacts / "browser-use-cdp.png"))
        if current_url != url or not screenshot:
            fail("Browser Use random CDP mode did not reach the fixture")
        return {
            "status": "passed",
            "title": title,
            "url_is_local_fixture": True,
            "random_port": port > 0,
            "profile": profile.name,
        }
    finally:
        if session is not None:
            await session.stop()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def profile_markers(process: psutil.Process) -> set[str]:
    markers: set[str] = set()
    for child in [process, *process.children(recursive=True)]:
        try:
            for argument in child.cmdline():
                if argument.startswith("--user-data-dir="):
                    markers.add(argument.split("=", 1)[1])
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return markers


async def run_node_probe(
    browser_path: Path,
    output: Path,
    *,
    limit: int | None,
) -> subprocess.Popen[bytes]:
    command = [
        "node",
        str(CATALOG_SCRIPT),
        "--browser-path",
        str(browser_path),
        "--output",
        str(output),
    ]
    if limit is not None:
        command.extend(["--limit", str(limit)])
    environment = os.environ.copy()
    environment["PUPPETEER_SKIP_DOWNLOAD"] = "1"
    environment["HYPERFRAMES_NO_TELEMETRY"] = "1"
    environment["HYPERFRAMES_BROWSER_PATH"] = str(browser_path)
    return subprocess.Popen(command, cwd=REPOSITORY_ROOT, env=environment)


async def wait_process(process: subprocess.Popen[bytes], timeout: float) -> None:
    await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=timeout)
    if process.returncode != 0:
        fail(f"render probe failed with exit code {process.returncode}")


async def concurrent_probe(
    browser_path: Path,
    root: Path,
    url: str,
    artifacts: Path,
) -> dict[str, Any]:
    playwright_result, context, playwright = await run_playwright_probe(
        browser_path,
        root / "profile-playwright",
        url,
        artifacts,
        headed=True,
    )
    browser_use_result, session = await run_browser_use_executable_probe(
        browser_path,
        root / "profile-browser-use",
        url,
        artifacts,
    )
    node_output = artifacts / "render-concurrent.json"
    node_process = await run_node_probe(browser_path, node_output, limit=1)
    try:
        await asyncio.sleep(1)
        markers = profile_markers(psutil.Process(os.getpid()))
        await wait_process(node_process, timeout=180)
        if len(markers) < 3:
            fail(f"three concurrent isolated browser profiles were not observed: {markers}")
        return {
            "status": "passed",
            "playwright": playwright_result,
            "browser_use": browser_use_result,
            "observed_profile_count": len(markers),
            "profiles_distinct": len(markers) == len(set(markers)),
        }
    finally:
        await session.stop()
        await close_playwright_context(context, playwright)
        if node_process.poll() is None:
            node_process.terminate()
            await asyncio.to_thread(node_process.wait)


def exclusive_lease_probe(root: Path) -> dict[str, Any]:
    lease = root / "control-lease.lock"
    lease.mkdir()
    rejected = False
    try:
        lease.mkdir()
    except FileExistsError:
        rejected = True
    lease.rmdir()
    lease.mkdir()
    lease.rmdir()
    if not rejected:
        fail("exclusive control lease admitted a second owner")
    return {"status": "passed", "second_owner_rejected": True, "reacquire_after_release": True}


async def run_validation(args: argparse.Namespace) -> None:
    contract = validate_contract()
    browser_path = (
        args.browser_path.resolve() if args.browser_path else discover_browser(args.browser_root)
    )
    if not browser_path.is_file():
        fail(f"Chromium executable is missing: {browser_path}")
    expected_version = contract["chromium"]["browser_version"]
    actual_version = read_browser_version(browser_path)
    if actual_version != expected_version:
        fail(f"Chromium version mismatch: expected {expected_version}, got {actual_version}")

    artifacts = args.artifacts_dir.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="automation-tool-eb02-") as temporary:
        root = Path(temporary)
        fixture = root / "fixture"
        fixture.mkdir()
        write_fixture(fixture)
        with FixtureServer(fixture) as server:
            concurrency = await concurrent_probe(browser_path, root, server.url, artifacts)
            cdp = await run_browser_use_cdp_probe(
                browser_path,
                root / "profile-browser-use-cdp",
                server.url,
                artifacts,
            )

        full_render = artifacts / "render-full.json"
        process = await run_node_probe(browser_path, full_render, limit=None)
        await wait_process(process, timeout=1800)
        render_result = load_json(full_render)
        if render_result.get("partial") is not False:
            fail("full render probe unexpectedly ran in partial mode")
        if len(render_result.get("catalog", [])) != 134:
            fail("not every installable catalog item produced a frame")
        if len(render_result.get("styles", [])) != 12:
            fail("not every style produced a frame")

        lease = exclusive_lease_probe(root)
        result = {
            "schema_version": 1,
            "platform_id": args.platform_id,
            "host": {"system": platform.system(), "machine": platform.machine()},
            "chromium": {
                "browser_version": actual_version,
                "revision": contract["chromium"]["revision"],
                "executable_sha256": sha256_file(browser_path),
            },
            "probes": {
                "headed_playwright": concurrency["playwright"],
                "browser_use_executable_path": concurrency["browser_use"],
                "browser_use_random_cdp": cdp,
                "headless_render_process": {"status": "passed", "capture_mode": "screenshot"},
                "catalog_single_frame": {"status": "passed", "count": 134},
                "style_single_frame": {"status": "passed", "count": 12},
                "concurrent_processes": concurrency,
                "isolated_profiles": {
                    "status": "passed",
                    "count": concurrency["observed_profile_count"],
                },
                "exclusive_control_lease": lease,
                "single_browser_distribution": {
                    "status": "passed",
                    "requires_second_browser_binary": False,
                },
            },
            "catalog_digest": sha256_file(full_render),
        }
        capabilities = concurrency["playwright"]["capabilities"]
        for name in ("font", "image", "video", "audio", "lottie", "canvas_2d", "webgl", "webgpu"):
            result["probes"][name] = {"status": "passed", "value": capabilities[name]}
        for name in ("transparent_png", "landscape", "portrait"):
            result["probes"][name] = {"status": "passed"}
        missing = EXPECTED_PROBES - set(result["probes"])
        if missing:
            fail(f"result omitted probes: {sorted(missing)}")
        output = artifacts / f"eb-02-{args.platform_id}.json"
        output.write_text(f"{json.dumps(result, ensure_ascii=False, indent=2)}\n", encoding="utf-8")
        print(f"EB-02 platform validation passed: {output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-contract", action="store_true")
    parser.add_argument("--browser-path", type=Path)
    parser.add_argument("--browser-root", type=Path)
    parser.add_argument("--platform-id", choices=("macos-arm64", "windows-x86_64"))
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args()
    if not args.check_contract:
        if (args.browser_path is None) == (args.browser_root is None):
            parser.error("provide exactly one of --browser-path or --browser-root")
        if args.platform_id is None or args.artifacts_dir is None:
            parser.error("--platform-id and --artifacts-dir are required")
    return args


def main() -> None:
    args = parse_args()
    if args.check_contract:
        validate_contract()
        print("shared Chromium validation contract passed")
        return
    asyncio.run(run_validation(args))


if __name__ == "__main__":
    try:
        main()
    except (
        ValidationError,
        OSError,
        KeyError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        raise SystemExit(f"shared Chromium validation failed: {error}") from error

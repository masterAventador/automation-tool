#!/usr/bin/env python3
"""Build one distributable package, with every release gate on the path.

Until now no such command existed. The steps that assemble a complete package —
staging the digest-locked Chromium, building the signed Local Executor, building
the three video runtime resources, installing all five into the built bundle and
re-sealing it — lived inside `scripts/run_eb_16_acceptance.py` and had no other
caller. Producing a shippable artifact meant running an acceptance suite, and no
workflow ran that suite, so nothing anywhere refused a package with resources
missing. On 2026-07-26 three of them were missing and a user found out.

This is that path as a command. It reuses the same assembler
(`scripts/release_assembly.py`), the same resource builder
(`scripts/prepare_video_runtime.py`) and the same contract-driven configuration
writer (`scripts/release_configuration.py`) — nothing here is a second
implementation of any of them. The acceptance script now calls into this module
for the steps it used to own, so the verified path and the shipped path cannot
drift apart again.

What this command does *not* do is the acceptance work: it does not install the
package, launch it, probe it or uninstall it. That is `run_eb_16_acceptance.py`,
which wraps this command and adds those checks.

Usage:
    python3 scripts/build_release_package.py --platform macos
    python3 scripts/build_release_package.py --platform macos --work-dir DIR
"""

from __future__ import annotations

import argparse
import json
import os
import platform as platform_module
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"

sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from build_embedded_browser_distribution import (  # noqa: E402
    build_distribution_manifest,
)
from build_embedded_chromium_staging import (  # noqa: E402
    MANIFEST_NAME as STAGING_MANIFEST_NAME,
)
from build_embedded_chromium_staging import (  # noqa: E402
    build_staging,
    generate_manifest,
    load_staging_contract,
    sha256_file,
)
from check_packaged_javascript_runtimes import (  # noqa: E402
    BROWSER_PROBE_EXPECTED,
    BROWSER_PROBE_EXPRESSION,
    collect_runtime_failures,
    find_javascript_runtimes,
    probe_embedded_browsers,
    summarise_jit_grants,
)
from check_embedded_browser_package import (  # noqa: E402
    audit_embedded_browser_package,
    browser_resource_root,
)
from customer_demo_release import (  # noqa: E402
    CustomerDemoMaterial,
    customer_demo_material,
    describe_deployment,
    require_compiled_deployment,
)
from build_motion_catalog_release import (  # noqa: E402
    stage_for_release as stage_motion_catalog,
)
from prepare_video_runtime import prepare as prepare_video_runtime  # noqa: E402
from production_assets import (  # noqa: E402
    AUDITED_DISTRIBUTION_NAME,
    snapshot_production_assets,
)
from release_assembly import (  # noqa: E402
    SigningIdentity,
    install_and_seal,
    install_motion_catalog,
    install_video_runtime,
    inventoried_payloads,
    load_signing_identity,
    notarize_and_staple,
    require_distributable_artifact,
    require_packaged_browser,
    require_packaged_motion_catalog,
    require_packaged_video_runtime,
    sign_tree,
)
from release_configuration import (  # noqa: E402
    effective_configuration,
    write_macos_release_configuration,
)
from release_identity import (  # noqa: E402
    ReleaseIdentityRejected,
    SourceFacts,
    materialize_repository_snapshot,
    repository_source_facts,
)
from run_p9_03_acceptance import (  # noqa: E402
    CARGO_MANIFEST,
    EXECUTOR_RESOURCE,
    app_binary,
    executor_signing_material,
    one_directory,
    pnpm_executable,
    release_environment,
    run_checked,
    verify_manifest_signature,
)

STAGING_CONTRACT = REPOSITORY_ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
_EB_03_CACHE = ".local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip"
DEFAULT_WORK_DIRECTORY = REPOSITORY_ROOT / ".local/release"
RELEASE_CONFIGURATION_NAME = "tauri.release.generated.json"
EFFECTIVE_CONFIGURATION_NAME = "tauri.release.effective.json"
RELEASE_IDENTITY_KEY = "AutomationToolReleaseIdentity"
RELEASE_IDENTITY_SCHEMA = "automation-tool.release-identity.v1"
SOURCE_SNAPSHOT_ENVIRONMENT = "AUTOMATION_TOOL_RELEASE_SOURCE_SNAPSHOT"
SOURCE_SNAPSHOT_IDENTITY_ENVIRONMENT = "AUTOMATION_TOOL_RELEASE_SOURCE_IDENTITY"
SOURCE_SNAPSHOT_CAPABILITY_ENVIRONMENT = (
    "AUTOMATION_TOOL_RELEASE_SOURCE_CAPABILITY_FD"
)
SOURCE_SNAPSHOT_CAPABILITY_MAGIC = b"automation-tool.release-source-snapshot.v1"
SOURCE_SNAPSHOT_CAPABILITY_MAX_BYTES = 4096


class ReleaseFailed(RuntimeError):
    """The release package could not be produced."""


def announce(message: str) -> None:
    print(f"[release] {message}", flush=True)


def require_source_stable_work_directory(path: Path) -> Path:
    """Reject repository outputs that the signed source snapshot would ingest."""

    resolved = path.resolve()
    try:
        relative = resolved.relative_to(REPOSITORY_ROOT)
    except ValueError:
        return resolved
    if relative == Path(".") or not relative.parts or relative.parts[0] == ".git":
        raise ReleaseFailed("release work directory would change the source snapshot")
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "--", relative.as_posix()],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if ignored.returncode == 0:
        return resolved
    if ignored.returncode == 1:
        raise ReleaseFailed("release work directory would change the source snapshot")
    raise ReleaseFailed("release work directory ignore policy is unavailable")


def _first_existing(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


DEFAULT_ARCHIVES = {
    # The EB-03 archive cache lives in the primary checkout's .local; resolve
    # it both from the primary checkout itself and from a wt/<task> worktree.
    "macos-arm64": _first_existing(
        REPOSITORY_ROOT / _EB_03_CACHE,
        REPOSITORY_ROOT.parent.parent / _EB_03_CACHE,
    ),
    "macos-x86_64": _first_existing(
        REPOSITORY_ROOT / ".local/eb-mac-x64/chrome-mac-x64.zip",
        REPOSITORY_ROOT.parent.parent / ".local/eb-mac-x64/chrome-mac-x64.zip",
    ),
}


def require_macos_target() -> tuple[str, str]:
    if platform_module.system() != "Darwin":
        raise ReleaseFailed("the macOS release package must be built on macOS")
    machine = platform_module.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "macos-arm64", "aarch64"
    if machine in {"x86_64", "amd64"}:
        return "macos-x86_64", "x86_64"
    raise ReleaseFailed("this macOS architecture is unsupported")


def refresh_staging_inventory(staging: Path, target_id: str) -> int:
    """Re-take the staged tree's digest inventory after it has been signed.

    `build_staging` inventories the tree as it comes out of the digest-locked
    archive, and `build_distribution_manifest` copies that inventory forward.
    Signing rewrites the bytes of every Mach-O it touches and adds a
    `_CodeSignature` directory to each nested bundle, so an inventory taken
    before signing describes a tree that no longer exists: `verify_distribution`
    would report "file digest mismatch", and if it somehow did not, the Rust
    resolver on the customer's machine would.

    What still ties this tree to the locked upstream archive is
    `source.archive_sha256`, which is carried through untouched and checked
    against the contract by both the manifest builder and the verifier.
    """
    contract = load_staging_contract(STAGING_CONTRACT)
    target = contract.targets.get(target_id)
    if target is None:
        raise ReleaseFailed(f"unknown staging target: {target_id}")
    manifest_path = staging / STAGING_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = generate_manifest(staging, root_entry=target.root_entry)
    manifest["entries"] = entries
    manifest["fileCount"] = sum(1 for entry in entries if entry["type"] == "file")
    manifest["totalBytes"] = sum(
        size if isinstance(size := entry.get("size", 0), int) else 0
        for entry in entries
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return int(manifest["fileCount"])


def stage_browser_distribution(
    target_id: str, archive: Path, output: Path, identity: SigningIdentity
) -> None:
    announce(f"Staging the digest-locked {target_id} Chromium from {archive.name}")
    if not archive.is_file():
        raise ReleaseFailed(f"locked archive is not downloaded yet: {archive}")
    contract = load_staging_contract(STAGING_CONTRACT)
    build_staging(
        contract=contract,
        target_id=target_id,
        archive_path=archive,
        archive_sha256=sha256_file(archive),
        output=output,
    )
    # Chrome for Testing arrives ad-hoc/linker-signed — `codesign -dvv` reports
    # `TeamIdentifier=not set`, so there is no upstream Developer ID signature
    # to preserve and nothing here can be notarised as it stands. Every Mach-O
    # in the tree is re-signed under our identity, and only then is the digest
    # inventory taken, because the manifest has to describe the shipped bytes.
    announce("Signing the embedded browser before its manifest is taken")
    signed = sign_tree(root=output, component="embedded-browser", identity=identity)
    files = refresh_staging_inventory(output, target_id)
    announce(f"Signed {len(signed)} browser code nodes across {files} files")
    build_distribution_manifest(staging=output, target_id=target_id)


def build_executor_candidate(
    output: Path, architecture: str, build_id: str, identity: SigningIdentity
) -> tuple[Path, str, Any]:
    from automation_tool.executor.macos_candidate import build_macos_executor_candidate
    from automation_tool.executor.package_manifest import (
        write_signed_executor_manifest,
    )

    announce("Building the real signed Local Executor candidate")
    build_macos_executor_candidate(backend_root=BACKEND_ROOT, output_directory=output)
    # The candidate builder applies ad-hoc signatures. They cannot be notarised,
    # so every Mach-O is re-signed under the Developer ID here — and it has to
    # happen now, before the inventory below: `executor-manifest.v1.json` records
    # a SHA-256 for each of the package's files and the Rust bootstrap re-checks
    # every one of them on the customer's machine.
    signed = sign_tree(root=output, component="local-executor", identity=identity)
    announce(f"Signed {len(signed)} Local Executor binaries before inventorying them")
    seed, public_key, private_key = executor_signing_material()
    write_signed_executor_manifest(
        bundle_directory=output,
        executor_version="0.1.0",
        build_id=build_id,
        target_platform="macos",
        target_architecture=architecture,
        signing_private_key=seed,
    )
    return output, public_key, private_key


def build_release_package(
    *, configuration: Path, environment: dict[str, str], target: Path
) -> Path:
    announce("Building one production-mode .app (no test features)")
    bundle_root = target / "release/bundle"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    run_checked(
        [
            pnpm_executable(),
            "exec",
            "tauri",
            "build",
            "--bundles",
            "app",
            "--config",
            os.fspath(configuration),
            "--ci",
        ],
        environment=environment,
    )
    return one_directory(target / "release/bundle/macos", ".app")


def sign_installed_video_runtime(
    installed: dict[str, Path], identity: SigningIdentity, target_id: str
) -> None:
    """Sign the three video resources where they landed, innermost first.

    They are signed in the bundle rather than in the shared per-machine cache
    `prepare_video_runtime` maintains, so a signing run cannot leave a cache
    entry that a later build would reuse without knowing how it was signed.

    `media-toolchain` then needs its manifest re-taken. `video_media_toolchain.rs`
    verifies a SHA-256 for every file it declares and rejects the package if one
    file is missing, extra or altered — and signing ffmpeg alters it.
    """
    for name, location in sorted(installed.items()):
        signed = sign_tree(root=location, component=name, identity=identity)
        announce(f"Signed {len(signed)} {name} binaries")
    toolchain = installed["media-toolchain"]
    announce("Re-taking the media toolchain manifest over the signed binaries")
    run_checked(
        [
            sys.executable,
            os.fspath(REPOSITORY_ROOT / "scripts/write_video_media_toolchain_manifest.py"),
            os.fspath(toolchain),
            target_id,
        ]
    )


def install_runtime_resources_and_sign(
    application: Path,
    staging: Path,
    target_id: str,
    video_runtime: Path,
    motion_catalog: Path,
    identity: SigningIdentity,
) -> None:
    """Run the shared release assembly step, the same one a release uses.

    A release must not keep its own copy of this: when the acceptance script
    did, the verified path and the shipped path were different paths, and the
    shipped one had no browser in it at all.

    Ordering matters three times over. The video runtime is installed and signed
    *before* `install_and_seal`, because that call seals the bundle at the end
    and a signature taken before a resource lands does not cover it. The browser
    is installed last for the same reason it is installed here at all: the macOS
    bundler destroys its symlinked framework, so it cannot be declared under
    `bundle.resources` and has to arrive afterwards. And the seal itself is the
    outermost signature of the whole package, so it is applied once everything
    inside is already signed.
    """
    announce("Installing the video runtime resources into the built bundle")
    installed = install_video_runtime(
        application=application, staging=video_runtime, platform="macos"
    )
    announce(f"Video runtime installed: {sorted(installed)}")
    sign_installed_video_runtime(installed, identity, target_id)
    # PC-16. The catalog carries no Mach-O, so it is not signed on its own —
    # the outermost seal below covers it. It has to land before that seal for
    # the same reason everything else does.
    announce("Installing the frozen catalog of animation parts")
    catalog = install_motion_catalog(
        application=application, staging=motion_catalog, platform="macos"
    )
    announce(f"Catalog installed: {sorted(catalog)}")
    announce("Installing the embedded browser, verifying it, then sealing the bundle")

    def seal_application(bundle: Path) -> None:
        sign_tree(
            root=bundle,
            component="application",
            identity=identity,
            exclude=inventoried_payloads(bundle, "macos"),
        )

    install_and_seal(
        application=application,
        staging=staging,
        target_id=target_id,
        platform="macos",
        # The outermost signature. Everything nested is signed by now, so this
        # seal covers a tree that will not change again — and it must leave the
        # inventoried payloads untouched, or the manifests taken over them stop
        # describing what the package actually carries.
        seal=seal_application,
    )


def embed_release_identity(
    *,
    application: Path,
    source: SourceFacts,
    build_id: str,
    target_id: str,
    architecture: str,
    deployment_profile_id: str,
) -> None:
    """Place release provenance under the outer Developer ID resource seal."""
    information_path = application / "Contents" / "Info.plist"
    if information_path.is_symlink() or not information_path.is_file():
        raise ReleaseFailed("the release App Info.plist is unavailable")
    try:
        with information_path.open("rb") as source_file:
            information = plistlib.load(source_file)
    except (OSError, plistlib.InvalidFileException) as error:
        raise ReleaseFailed("the release App Info.plist is invalid") from error
    if not isinstance(information, dict) or RELEASE_IDENTITY_KEY in information:
        raise ReleaseFailed("the release App identity slot is invalid")
    original_mode = stat.S_IMODE(information_path.stat().st_mode)
    information[RELEASE_IDENTITY_KEY] = {
        "architecture": architecture,
        "buildId": build_id,
        "deploymentProfileId": deployment_profile_id,
        "schema": RELEASE_IDENTITY_SCHEMA,
        "sourceGitCommit": source.git_commit,
        "sourceTreeSha256": source.tree_sha256,
        "target": target_id,
    }
    try:
        with tempfile.NamedTemporaryFile(
            dir=information_path.parent,
            prefix=".Info.plist.",
            delete=False,
        ) as target:
            temporary = Path(target.name)
            # NamedTemporaryFile created this path in our private staging
            # directory, so it cannot be a symlink.  Omitting follow_symlinks
            # keeps this release helper compatible with Windows Python 3.12.
            os.chmod(temporary, original_mode)
            plistlib.dump(information, target, sort_keys=True)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, information_path)
    except BaseException:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)
        raise


def writable_image_command(
    *, volume_name: str, megabytes: int, output: Path
) -> list[str]:
    # No `-quiet` anywhere in this file. It suppresses the progress lines *and*
    # the failure reason: measured, hdiutil prints zero bytes when it refuses
    # under `-quiet`. A release build that died here would hand the operator an
    # exit code and nothing else, which is how the 2026-07-27 failure cost an
    # hour before the reason could be recovered by hand.
    return [
        "hdiutil",
        "create",
        "-size",
        f"{megabytes}m",
        "-fs",
        "HFS+",
        "-volname",
        volume_name,
        "-type",
        "UDIF",
        "-o",
        os.fspath(output),
    ]


def attach_command(*, image: Path, mountpoint: Path) -> list[str]:
    # An explicit `-mountpoint` outside `/Volumes`, plus `-nobrowse`. Two
    # reasons, both measured: parallel release lines would otherwise collide on
    # `/Volumes/<volume name>`, and a volume that never appears in `/Volumes`
    # cannot be left behind as a stale mount for the next build to trip over.
    return [
        "hdiutil",
        "attach",
        os.fspath(image),
        "-mountpoint",
        os.fspath(mountpoint),
        "-nobrowse",
    ]


def image_megabytes(source: Path) -> int:
    """Room for `source` plus filesystem overhead, in whole megabytes."""
    total = 0
    for path in source.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        total += path.stat().st_size
    # HFS+ metadata, the catalog, and the slack a full volume needs to accept
    # the last file. Generous on purpose: the image is compressed afterwards,
    # so unused space costs nothing in the artifact the customer downloads,
    # while too little space fails the build.
    return max(64, int(total / (1024 * 1024) * 1.25) + 64)


def fill_disk_image(*, source: Path, volume_name: str, output: Path) -> Path:
    """Build a compressed image holding everything in `source`.

    `hdiutil create -srcfolder` used to do this in one call and can no longer
    do it at all. Measured 2026-07-27 with the real signed and notarised
    bundle: it fails with `could not access /Volumes/<vol>/<app>.app -
    Operation not permitted`. The same staging directory succeeds when the
    bundle is renamed so it does not end in `.app`, and a synthetic `Probe.app`
    stub also succeeds — so it is neither the name nor the size, it is a
    genuine application bundle. It fails for the pre-T84 form (handing
    `-srcfolder` the .app directly) too, so this is not a regression T84
    introduced; that path had simply stopped working by the time it was next
    run.

    `ditto` in this process copies the very same bundle onto the very same
    mounted volume without complaint, so the refusal belongs to the helper
    hdiutil delegates its copying to. Hence: create the volume empty, fill it
    ourselves, then compress.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as scratch_root:
        scratch = Path(scratch_root)
        writable = scratch / "writable.dmg"
        mountpoint = scratch / "volume"
        mountpoint.mkdir()
        run_checked(
            writable_image_command(
                volume_name=volume_name,
                megabytes=image_megabytes(source),
                output=writable,
            )
        )
        run_checked(attach_command(image=writable, mountpoint=mountpoint))
        try:
            for entry in sorted(source.iterdir()):
                destination = mountpoint / entry.name
                if entry.is_symlink():
                    destination.symlink_to(os.readlink(entry))
                else:
                    # `ditto` preserves the signature; a plain copy does not
                    # reliably.
                    run_checked(
                        ["ditto", os.fspath(entry), os.fspath(destination)]
                    )
        finally:
            # Always, including on failure: a leaked mount survives this
            # process and the next build inherits it.
            run_checked(["hdiutil", "detach", os.fspath(mountpoint)])
        run_checked(
            [
                "hdiutil",
                "convert",
                os.fspath(writable),
                "-format",
                "UDZO",
                "-o",
                os.fspath(output),
            ]
        )
    return output


def create_disk_image(
    application: Path, output: Path, target_id: str, identity: SigningIdentity
) -> Path:
    # A bundle without a verified browser must not reach a distributable
    # artifact; this is the gate an ordinary candidate build fails.
    require_packaged_browser(
        application=application, target_id=target_id, platform="macos"
    )
    # The same gate for the video runtime. Without it a package ships whose
    # video features fail on the user's machine while every acceptance run
    # stays green, which is exactly what happened.
    require_packaged_video_runtime(application=application, platform="macos")
    # And for the parts. A package without them renders every film from the
    # four built-in layouts and never says why the catalog was ignored.
    require_packaged_motion_catalog(application=application, platform="macos")
    # Notarised and stapled before the disk image is built, so the ticket
    # travels inside the .app the customer drags out of it. A ticket stapled
    # only to the disk image is not carried by the copied application, which
    # then needs to reach Apple to open — and a demo on a bad network shows the
    # customer the same refusal as an unsigned build.
    announce("Notarising the application bundle (this waits on Apple)")
    submission = notarize_and_staple(artifact=application, identity=identity)
    announce(f"Application notarised and stapled (submission {submission})")
    announce("Creating the release disk image from the final App bundle")
    # Before anything stages under it. `tauri build --bundles app` produces only
    # `bundle/macos/` — its DMG bundler never runs — so `bundle/dmg/` does not
    # exist on the first image of a build, and creating it by hand does not
    # survive: `tauri build` rebuilds `bundle/` wholesale on every run.
    output.parent.mkdir(parents=True, exist_ok=True)
    # Image a staging directory rather than the bare .app, so the volume also
    # carries the `Applications` symlink the customer drags onto. A volume
    # holding nothing but the bundle is what shipped on 2026-07-26: no symlink,
    # no drag target, and no way to install without opening a second Finder
    # window.
    #
    # This cannot be fixed in `tauri.conf.json`. The build runs
    # `tauri build --bundles app`, so Tauri's DMG bundler never executes and
    # its `bundle.macOS.dmg` settings are never read. The order is deliberate:
    # the .app is notarised and stapled above, before imaging, so the ticket
    # travels inside the bundle the customer drags out.
    with tempfile.TemporaryDirectory(dir=output.parent) as staging_root:
        staging = Path(staging_root) / "image"
        staging.mkdir()
        # `ditto` preserves the signature; a plain copy does not reliably.
        run_checked(["ditto", os.fspath(application), os.fspath(staging / application.name)])
        (staging / "Applications").symlink_to("/Applications")
        fill_disk_image(
            source=staging, volume_name=application.stem, output=output
        )
    # The disk image is itself a downloaded artifact, so it carries its own
    # signature and its own ticket; the customer's first Gatekeeper prompt is
    # about this file, not about the bundle inside it.
    announce("Signing and notarising the disk image")
    run_checked(
        [
            "codesign",
            "--force",
            "--sign",
            identity.certificate,
            "--timestamp",
            os.fspath(output),
        ]
    )
    submission = notarize_and_staple(artifact=output, identity=identity)
    announce(f"Disk image notarised and stapled (submission {submission})")
    return output


def require_distributable_release(disk_image: Path) -> str:
    """The last gate: what the customer's machine will say about this file.

    Everything before this is evidence about the build. This is the only step
    that asks the question the customer actually asks, and it asks it of the
    artifact the customer actually receives, in the state they receive it in —
    quarantined, exactly as a browser download arrives.

    "The notary service accepted the submission" is not this claim. Submission
    and openability are different facts, and this project has already shipped a
    package once on the strength of a green run that exercised something other
    than what the user got.
    """
    announce("Gate: assessing the disk image as a quarantined download")
    verdict = require_distributable_artifact(artifact=disk_image)
    for line in verdict.strip().splitlines():
        announce(f"  {line.strip()}")
    return verdict


def audit_release_artifact(
    *,
    application: Path,
    target_id: str,
    environment: dict[str, str],
    configuration: Path,
    audited_assets: Path,
) -> None:
    """Every content gate the release has, run against the finished bundle."""
    announce("Auditing the built binary, configuration and whole bundle content")
    # Before anything that only looks at files: run the JavaScript runtimes.
    # On 2026-07-26 a package passed every gate below and still shipped two
    # `node` binaries that could not evaluate an expression, because signing had
    # applied the hardened runtime without allow-jit. Everything downstream asks
    # whether files exist and are non-empty; nothing executed them.
    runtime_failures = collect_runtime_failures(application)
    if runtime_failures:
        detail = "\n".join(
            f"  {failure.path}\n    exit {failure.returncode}: {failure.output}"
            for failure in runtime_failures
        )
        raise ReleaseFailed(
            "a JavaScript runtime inside this package cannot evaluate an "
            f"expression:\n{detail}"
        )
    announce(
        "Packaged JavaScript runtimes executed an expression: "
        f"{len(find_javascript_runtimes(application))} runtime(s)"
    )
    # The other half of the same lesson. Until T104 the two `node` binaries were
    # the only engines made to prove themselves; the embedded Chromium — the
    # largest one in the package, and the one every RPA path depends on — was
    # only ever looked at. Its allow-jit grant was a claim nobody checked, and
    # the sole thing that would have caught a bad one was a human scanning a QR
    # code the day before a demo.
    browser_probe = probe_embedded_browsers(application)
    if browser_probe.failures:
        detail = "\n".join(
            f"  {failure.path}\n    exit {failure.returncode}: {failure.output}"
            for failure in browser_probe.failures
        )
        raise ReleaseFailed(
            "the embedded browser inside this package cannot evaluate an "
            f"expression:\n{detail}"
        )
    announce(
        f"Embedded browser evaluated {BROWSER_PROBE_EXPRESSION} to "
        f"{BROWSER_PROBE_EXPECTED!r} using {len(browser_probe.executed)} binaries"
    )
    announce(
        summarise_jit_grants(
            application,
            exercised=[
                *find_javascript_runtimes(application),
                *browser_probe.executed,
            ],
        )
    )
    report = audit_embedded_browser_package(
        bundle_root=application, target_id=target_id, platform="macos"
    )
    announce(
        f"Package payload verified: {report.browser_files} browser files "
        f"({report.browser_bytes} bytes) inside {report.package_files} package "
        f"files ({report.package_bytes} bytes)"
    )
    run_checked(
        [
            "node",
            "scripts/audit-production-package.mjs",
            "--binary",
            os.fspath(app_binary(application)),
            "--cargo-manifest",
            os.fspath(CARGO_MANIFEST),
            "--tauri-config",
            os.fspath(configuration),
            "--dist",
            os.fspath(audited_assets),
            # Without this the audit only ever sees a binary, and every
            # statement it makes about the resources a package carries is
            # vacuous — which is how an empty `bundle.resources` passed.
            "--package-root",
            os.fspath(application),
            "--package-platform",
            "macos",
        ],
        environment=environment,
    )
    run_checked(
        [
            "node",
            "scripts/audit-release-bundle.mjs",
            "--bundle-root",
            os.fspath(application),
            "--executor-package",
            os.fspath(application / "Contents/Resources" / EXECUTOR_RESOURCE),
            "--embedded-browser",
            os.fspath(browser_resource_root(application, "macos")),
            "--platform",
            "macos",
        ]
    )


def build_macos_release(
    *,
    work_directory: Path,
    archive: Path | None,
    build_id: str,
    deployment: CustomerDemoMaterial | None = None,
    update_endpoint: str | None = None,
    update_public_key: str | None = None,
) -> dict[str, object]:
    """Produce one distributable macOS package and pass every release gate.

    With `deployment` supplied the package is a customer Demo one: it carries
    the signed deployment profile, so it addresses that Control Plane and holds
    its workbench behind a product account login, and it trusts that
    deployment's action authorization key. Without it the package is the
    ordinary local-profile release. One path, one set of gates, either way.
    """
    work_directory = require_source_stable_work_directory(work_directory)
    target_id, architecture = require_macos_target()
    # Resolved before anything is built. There is one identity and one reader
    # for it: no environment variable or build mode can make an acceptance run
    # and a customer build sign with different material, because that class of
    # divergence is precisely what let a package ship with no browser in it.
    identity = load_signing_identity()
    announce(f"Signing this release as {identity.certificate}")
    announce("Checking the locked read-only third-party source checkouts")
    run_checked(
        [
            sys.executable,
            os.fspath(REPOSITORY_ROOT / "scripts/check_third_party_sources.py"),
        ],
        cwd=REPOSITORY_ROOT,
    )
    source_identity = repository_source_facts(REPOSITORY_ROOT)
    resolved_archive = archive or DEFAULT_ARCHIVES[target_id]
    build_directory = work_directory / "build"
    cargo_target = work_directory / "cargo-target"
    work_directory.mkdir(parents=True, exist_ok=True)
    if build_directory.exists():
        shutil.rmtree(build_directory)
    build_directory.mkdir(parents=True)

    browser = build_directory / "embedded-browser"
    stage_browser_distribution(target_id, resolved_archive, browser, identity)
    executor = build_directory / "executor" / "automation-tool-executor"
    _, public_key, private_key = build_executor_candidate(
        executor, architecture, build_id, identity
    )
    (build_directory / "executor-verifying-key").write_text(
        public_key, encoding="utf-8"
    )
    configuration = write_macos_release_configuration(
        directory=build_directory, executor=executor, name=RELEASE_CONFIGURATION_NAME
    )
    effective = effective_configuration(
        overlay=configuration,
        directory=build_directory,
        name=EFFECTIVE_CONFIGURATION_NAME,
    )
    environment = release_environment(
        cargo_target,
        public_key,
        deployment_profile=None if deployment is None else deployment.environment(),
        action_authorization_public_key=(
            None if deployment is None else deployment.action_authorization_public_key
        ),
        update_endpoint=update_endpoint,
        update_public_key=update_public_key,
    )
    if deployment is not None:
        announce(f"Building for the deployment at {deployment.base_url}")
    application = build_release_package(
        configuration=configuration, environment=environment, target=cargo_target
    )
    if deployment is not None:
        # Read back out of the finished binary. Everything before this was an
        # instruction to a compiler that a stale cache may decline to follow.
        require_compiled_deployment(app_binary(application), deployment)
        announce(f"Binary carries the deployment profile for {deployment.base_url}")
    # Frozen next to the artifact it belongs to: the shared `frontend/dist` can
    # be rewritten by any concurrent build while the audits are still running.
    audited_assets = snapshot_production_assets(
        build_directory / AUDITED_DISTRIBUTION_NAME
    )
    announce("Preparing the pinned video runtime resources (cached per machine)")
    video_runtime = prepare_video_runtime(platform="macos")
    announce("Staging the frozen catalog of animation parts")
    motion_catalog = stage_motion_catalog(staging=build_directory / "catalog").parent
    if repository_source_facts(REPOSITORY_ROOT) != source_identity:
        raise ReleaseFailed("release sources changed while the App was being built")
    embed_release_identity(
        application=application,
        source=source_identity,
        build_id=build_id,
        target_id=target_id,
        architecture=architecture,
        deployment_profile_id="local" if deployment is None else deployment.profile_id,
    )
    install_runtime_resources_and_sign(
        application, browser, target_id, video_runtime, motion_catalog, identity
    )
    # Every content gate runs against the sealed bundle *before* notarisation.
    # Notarising twice costs about ten minutes, and a package that is going to
    # be refused for its contents should be refused now rather than after them.
    audit_release_artifact(
        application=application,
        target_id=target_id,
        environment=environment,
        configuration=effective,
        audited_assets=audited_assets,
    )
    verify_manifest_signature(
        application / "Contents/Resources" / EXECUTOR_RESOURCE, private_key
    )
    disk_image = create_disk_image(
        application,
        cargo_target / "release/bundle/dmg" / f"{application.stem}_0.1.0.dmg",
        target_id,
        identity,
    )
    gatekeeper = require_distributable_release(disk_image)
    result: dict[str, object] = {
        "application": os.fspath(application),
        "architecture": architecture,
        "disk_image": os.fspath(disk_image),
        "disk_image_bytes": disk_image.stat().st_size,
        "gatekeeper": gatekeeper.strip().splitlines(),
        "signed_by": identity.certificate,
        "target": target_id,
    }
    if deployment is not None:
        result["deployment"] = describe_deployment(deployment)
    return result


DEPLOYMENT_ARGUMENTS = (
    "deployment_profile",
    "profile_signing_key",
    "action_authorization_key",
)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("macos", "windows"), default="macos")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIRECTORY)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--build-id", default="macos-release")
    parser.add_argument(
        "--update-endpoint",
        help=(
            "HTTPS update feed template containing target, arch and current_version "
            "placeholders; omit together with --update-public-key-file to disable updates"
        ),
    )
    parser.add_argument(
        "--update-public-key-file",
        type=Path,
        help=(
            "path to the canonical Base64-wrapped Minisign public key; omit together "
            "with --update-endpoint to disable updates"
        ),
    )
    # Paths only. The two key files hold private material that must not reach
    # argv, the environment, a log line or this repository.
    parser.add_argument(
        "--deployment-profile",
        type=Path,
        help="JSON file declaring profileId, baseUrl and allowedHosts",
    )
    parser.add_argument(
        "--profile-signing-key",
        type=Path,
        help="path to the Ed25519 seed that signs the deployment profile (mode 0600)",
    )
    parser.add_argument(
        "--action-authorization-key",
        type=Path,
        help=(
            "path to the Ed25519 seed the Control Plane holds as its "
            "action-authorization-private-key Secret (mode 0600)"
        ),
    )
    arguments = parser.parse_args(argv)
    # Every step of a release runs a subprocess with `cwd=frontend/`, so a
    # relative path given on the command line would be re-interpreted against
    # a directory the operator never named. Bind them all to the invocation
    # directory once, here, rather than at each of the places they are used.
    for name in (
        "work_dir",
        "archive",
        "update_public_key_file",
        *DEPLOYMENT_ARGUMENTS,
    ):
        path = getattr(arguments, name)
        if path is not None:
            setattr(arguments, name, path.resolve())
    return arguments


def resolve_update_configuration(
    arguments: argparse.Namespace,
) -> tuple[str | None, str | None]:
    endpoint = arguments.update_endpoint
    public_key_file = arguments.update_public_key_file
    if endpoint is None and public_key_file is None:
        return None, None
    if endpoint is None or public_key_file is None:
        raise ReleaseFailed(
            "release updates require both --update-endpoint and --update-public-key-file"
        )
    try:
        public_key = public_key_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ReleaseFailed("the release update public key is unreadable") from error
    if not public_key:
        raise ReleaseFailed("the release update public key is empty")
    return endpoint, public_key


def resolve_deployment(arguments: argparse.Namespace) -> CustomerDemoMaterial | None:
    """All three of the customer Demo inputs, or none of them."""
    supplied = {
        name: getattr(arguments, name)
        for name in DEPLOYMENT_ARGUMENTS
        if getattr(arguments, name) is not None
    }
    if not supplied:
        return None
    if len(supplied) != len(DEPLOYMENT_ARGUMENTS):
        missing = [
            f"--{name.replace('_', '-')}"
            for name in DEPLOYMENT_ARGUMENTS
            if name not in supplied
        ]
        # A package built with two of the three would either fail to compile or,
        # worse, ship pointing at a deployment whose action authorizations it
        # cannot verify. There is no useful partial form.
        raise ReleaseFailed(f"a customer Demo release also requires {missing}")
    return customer_demo_material(
        deployment_path=arguments.deployment_profile,
        profile_signing_key_path=arguments.profile_signing_key,
        action_authorization_key_path=arguments.action_authorization_key,
    )


def _link_snapshot_build_dependency(snapshot: Path, relative: Path) -> None:
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ReleaseFailed("release source snapshot dependency path is unsafe")
    source = REPOSITORY_ROOT / relative
    if not source.exists() or source.is_symlink():
        return
    target = snapshot / relative
    if target.exists() or target.is_symlink():
        raise ReleaseFailed("release source snapshot dependency path is occupied")
    # These fixed build-only paths are directories in the operator checkout, so
    # their trailing-slash .gitignore rules work there.  Inside the detached
    # snapshot they become symlinks, and Git deliberately does not match a
    # directory-only rule against a symlink.  Exclude the exact anchored path in
    # this disposable clone's private metadata before identity is recomputed;
    # otherwise a real build sees the dependency as unreviewed source.
    git_directory = snapshot / ".git"
    exclude_path = git_directory / "info" / "exclude"
    try:
        git_metadata = git_directory.lstat()
        exclude_metadata = exclude_path.lstat()
        if (
            stat.S_ISLNK(git_metadata.st_mode)
            or not stat.S_ISDIR(git_metadata.st_mode)
            or stat.S_ISLNK(exclude_metadata.st_mode)
            or not stat.S_ISREG(exclude_metadata.st_mode)
        ):
            raise ReleaseFailed("release source snapshot Git metadata is invalid")
        with exclude_path.open("a", encoding="utf-8") as exclude_file:
            exclude_file.write(f"\n/{relative.as_posix()}\n")
    except OSError as error:
        raise ReleaseFailed("release source snapshot Git metadata is unavailable") from error
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=True)


def _read_source_snapshot_capability() -> tuple[Path, str]:
    rendered_descriptor = os.environ.pop(
        SOURCE_SNAPSHOT_CAPABILITY_ENVIRONMENT,
        None,
    )
    if (
        rendered_descriptor is None
        or not rendered_descriptor.isascii()
        or not rendered_descriptor.isdigit()
        or str(int(rendered_descriptor)) != rendered_descriptor
    ):
        raise ReleaseFailed("release source snapshot capability is unavailable")
    descriptor = int(rendered_descriptor)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISFIFO(metadata.st_mode):
            raise ReleaseFailed("release source snapshot capability is invalid")
        os.set_blocking(descriptor, False)
        try:
            payload = os.read(descriptor, SOURCE_SNAPSHOT_CAPABILITY_MAX_BYTES + 1)
            trailing = os.read(descriptor, 1)
        except BlockingIOError as error:
            raise ReleaseFailed("release source snapshot capability is incomplete") from error
    except OSError as error:
        raise ReleaseFailed("release source snapshot capability is unavailable") from error
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    if (
        not payload
        or len(payload) > SOURCE_SNAPSHOT_CAPABILITY_MAX_BYTES
        or trailing
    ):
        raise ReleaseFailed("release source snapshot capability is invalid")
    fields = payload.split(b"\0")
    if len(fields) != 4 or fields[0] != SOURCE_SNAPSHOT_CAPABILITY_MAGIC:
        raise ReleaseFailed("release source snapshot capability is invalid")
    try:
        parent_process_id = int(fields[1].decode("ascii"))
        declared = Path(os.fsdecode(fields[2]))
        expected_identity = fields[3].removesuffix(b"\n").decode("ascii")
    except (UnicodeDecodeError, ValueError) as error:
        raise ReleaseFailed("release source snapshot capability is invalid") from error
    if (
        fields[3] != expected_identity.encode("ascii") + b"\n"
        or parent_process_id != os.getppid()
        or len(expected_identity) != 64
        or any(character not in "0123456789abcdef" for character in expected_identity)
    ):
        raise ReleaseFailed("release source snapshot capability is invalid")
    return declared, expected_identity


def require_snapshot_repository_layout(snapshot: Path, work_directory: Path) -> None:
    """Require the private detached clone shape created by this release entrypoint.

    A pipe proves only that the writer is our direct parent; a caller can also
    be a direct parent.  The child therefore accepts the capability only when
    its own source root is the dedicated detached repository beneath the exact
    requested release work directory, never an ordinary mutable checkout.
    """

    work = require_source_stable_work_directory(work_directory)
    container = snapshot.parent
    suffix = container.name.removeprefix("source-snapshot-")
    if (
        snapshot.name != "repository"
        or container.parent != work
        or not container.name.startswith("source-snapshot-")
        or len(suffix) < 6
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in suffix)
    ):
        raise ReleaseFailed("release source snapshot layout is invalid")
    git_directory = snapshot / ".git"
    try:
        container_metadata = container.lstat()
        snapshot_metadata = snapshot.lstat()
        git_metadata = git_directory.lstat()
    except OSError as error:
        raise ReleaseFailed("release source snapshot layout is unavailable") from error
    if (
        stat.S_ISLNK(container_metadata.st_mode)
        or not stat.S_ISDIR(container_metadata.st_mode)
        or container_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(container_metadata.st_mode) != 0o700
        or stat.S_ISLNK(snapshot_metadata.st_mode)
        or not stat.S_ISDIR(snapshot_metadata.st_mode)
        or snapshot_metadata.st_uid != os.geteuid()
        or stat.S_ISLNK(git_metadata.st_mode)
        or not stat.S_ISDIR(git_metadata.st_mode)
        or git_metadata.st_uid != os.geteuid()
    ):
        raise ReleaseFailed("release source snapshot layout is invalid")
    try:
        git_root = subprocess.run(
            ["git", "-C", os.fspath(snapshot), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        absolute_git = subprocess.run(
            ["git", "-C", os.fspath(snapshot), "rev-parse", "--absolute-git-dir"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        symbolic_head = subprocess.run(
            ["git", "-C", os.fspath(snapshot), "symbolic-ref", "-q", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ReleaseFailed("release source snapshot layout is unavailable") from error
    if (
        Path(git_root).resolve(strict=True) != snapshot
        or Path(absolute_git).resolve(strict=True) != git_directory
        or symbolic_head.returncode != 1
        or symbolic_head.stdout
    ):
        raise ReleaseFailed("release source snapshot layout is invalid")


def require_materialized_source_snapshot(work_directory: Path) -> None:
    if (
        SOURCE_SNAPSHOT_ENVIRONMENT in os.environ
        or SOURCE_SNAPSHOT_IDENTITY_ENVIRONMENT in os.environ
    ):
        raise ReleaseFailed("release source snapshot capability cannot come from public state")
    declared, expected_identity = _read_source_snapshot_capability()
    try:
        snapshot = declared.resolve(strict=True)
        if snapshot != REPOSITORY_ROOT or Path(__file__).resolve().parent.parent != snapshot:
            raise ReleaseFailed("release build did not enter its source snapshot")
        require_snapshot_repository_layout(snapshot, work_directory)
        observed = repository_source_facts(snapshot)
    except (OSError, ReleaseIdentityRejected) as error:
        raise ReleaseFailed("release source snapshot is unavailable") from error
    if observed.tree_sha256 != expected_identity:
        raise ReleaseFailed("release source snapshot identity changed")


def run_from_materialized_source_snapshot(arguments: argparse.Namespace) -> int:
    """Restart this command in a detached copy of the exact reviewed inputs."""

    invocation_directory = Path.cwd().resolve(strict=True)
    work_directory = require_source_stable_work_directory(arguments.work_dir)
    work_directory.mkdir(parents=True, exist_ok=True)
    container = Path(
        tempfile.mkdtemp(prefix="source-snapshot-", dir=work_directory)
    ).resolve(strict=True)
    snapshot = container / "repository"
    try:
        source = repository_source_facts(REPOSITORY_ROOT)
        materialize_repository_snapshot(
            REPOSITORY_ROOT,
            snapshot,
            expected=source,
        )
        for dependency in (
            Path(".local"),
            Path("frontend/node_modules"),
            Path("backend/.venv"),
        ):
            _link_snapshot_build_dependency(snapshot, dependency)
        if repository_source_facts(snapshot) != source:
            raise ReleaseFailed("release source snapshot changed while dependencies were linked")
        environment = os.environ.copy()
        for name in (
            SOURCE_SNAPSHOT_ENVIRONMENT,
            SOURCE_SNAPSHOT_IDENTITY_ENVIRONMENT,
            SOURCE_SNAPSHOT_CAPABILITY_ENVIRONMENT,
        ):
            environment.pop(name, None)
        read_descriptor, write_descriptor = os.pipe()
        try:
            capability = b"\0".join(
                (
                    SOURCE_SNAPSHOT_CAPABILITY_MAGIC,
                    str(os.getpid()).encode("ascii"),
                    os.fsencode(snapshot),
                    source.tree_sha256.encode("ascii") + b"\n",
                )
            )
            written = os.write(write_descriptor, capability)
            if written != len(capability):
                raise ReleaseFailed("release source snapshot capability could not be created")
        finally:
            os.close(write_descriptor)
        try:
            environment[SOURCE_SNAPSHOT_CAPABILITY_ENVIRONMENT] = str(read_descriptor)
            completed = subprocess.run(
                [
                    sys.executable,
                    os.fspath(snapshot / "scripts/build_release_package.py"),
                    *sys.argv[1:],
                ],
                # Preserve the operator's relative-path base. The child parses the
                # original argv again from the detached script, and changing cwd
                # here would silently retarget --work-dir, --archive and key paths.
                cwd=invocation_directory,
                env=environment,
                pass_fds=(read_descriptor,),
                check=False,
            )
        finally:
            os.close(read_descriptor)
        return completed.returncode
    except ReleaseIdentityRejected as error:
        raise ReleaseFailed("release source snapshot could not be materialized") from error
    finally:
        shutil.rmtree(container)


def main() -> int:
    arguments = parse_arguments()
    if arguments.platform == "windows":
        # Refused rather than half-implemented: the Windows release still runs
        # through `scripts/run_eb_16_windows_acceptance.py`, which owns the NSIS
        # payload layout and the installer round trip.
        raise ReleaseFailed(
            "the Windows release still runs through "
            "scripts/run_eb_16_windows_acceptance.py; this command builds macOS "
            "packages only"
        )
    if SOURCE_SNAPSHOT_CAPABILITY_ENVIRONMENT not in os.environ:
        if (
            SOURCE_SNAPSHOT_ENVIRONMENT in os.environ
            or SOURCE_SNAPSHOT_IDENTITY_ENVIRONMENT in os.environ
        ):
            raise ReleaseFailed("release source snapshot capability is unavailable")
        return run_from_materialized_source_snapshot(arguments)
    require_materialized_source_snapshot(arguments.work_dir)
    # Resolved before anything is built: a deployment the App would reject is
    # refused now rather than twenty minutes from now.
    deployment = resolve_deployment(arguments)
    update_endpoint, update_public_key = resolve_update_configuration(arguments)
    result = build_macos_release(
        work_directory=arguments.work_dir,
        archive=arguments.archive,
        build_id=arguments.build_id,
        deployment=deployment,
        update_endpoint=update_endpoint,
        update_public_key=update_public_key,
    )
    (arguments.work_dir / "release-package.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    announce(f"Built application: {result['application']}")
    announce(f"Built disk image: {result['disk_image']} ({result['disk_image_bytes']} bytes)")
    announce("Release package built and every release gate passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseFailed as error:
        print(f"release failed: {error}")
        raise SystemExit(1) from error

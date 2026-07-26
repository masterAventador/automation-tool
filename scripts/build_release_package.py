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
import shutil
import sys
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
from prepare_video_runtime import prepare as prepare_video_runtime  # noqa: E402
from production_assets import (  # noqa: E402
    AUDITED_DISTRIBUTION_NAME,
    snapshot_production_assets,
)
from release_assembly import (  # noqa: E402
    SigningIdentity,
    install_and_seal,
    install_video_runtime,
    inventoried_payloads,
    load_signing_identity,
    notarize_and_staple,
    require_distributable_artifact,
    require_packaged_browser,
    require_packaged_video_runtime,
    sign_tree,
)
from release_configuration import (  # noqa: E402
    effective_configuration,
    write_macos_release_configuration,
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


class ReleaseFailed(RuntimeError):
    """The release package could not be produced."""


def announce(message: str) -> None:
    print(f"[release] {message}", flush=True)


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
    manifest["totalBytes"] = sum(int(entry.get("size", 0)) for entry in entries)
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
    announce("Installing the embedded browser, verifying it, then sealing the bundle")
    install_and_seal(
        application=application,
        staging=staging,
        target_id=target_id,
        platform="macos",
        # The outermost signature. Everything nested is signed by now, so this
        # seal covers a tree that will not change again — and it must leave the
        # inventoried payloads untouched, or the manifests taken over them stop
        # describing what the package actually carries.
        seal=lambda bundle: sign_tree(
            root=bundle,
            component="application",
            identity=identity,
            exclude=inventoried_payloads(bundle, "macos"),
        ),
    )


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
    # Notarised and stapled before the disk image is built, so the ticket
    # travels inside the .app the customer drags out of it. A ticket stapled
    # only to the disk image is not carried by the copied application, which
    # then needs to reach Apple to open — and a demo on a bad network shows the
    # customer the same refusal as an unsigned build.
    announce("Notarising the application bundle (this waits on Apple)")
    submission = notarize_and_staple(artifact=application, identity=identity)
    announce(f"Application notarised and stapled (submission {submission})")
    announce("Creating the release disk image from the final App bundle")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    run_checked(
        [
            "hdiutil",
            "create",
            "-volname",
            application.stem,
            "-srcfolder",
            os.fspath(application),
            "-fs",
            "HFS+",
            "-format",
            "UDZO",
            "-quiet",
            os.fspath(output),
        ]
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
) -> dict[str, object]:
    """Produce one distributable macOS package and pass every release gate.

    With `deployment` supplied the package is a customer Demo one: it carries
    the signed deployment profile, so it addresses that Control Plane and holds
    its workbench behind a product account login, and it trusts that
    deployment's action authorization key. Without it the package is the
    ordinary local-profile release. One path, one set of gates, either way.
    """
    target_id, architecture = require_macos_target()
    # Resolved before anything is built. There is one identity and one reader
    # for it: no environment variable or build mode can make an acceptance run
    # and a customer build sign with different material, because that class of
    # divergence is precisely what let a package ship with no browser in it.
    identity = load_signing_identity()
    announce(f"Signing this release as {identity.certificate}")
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
    install_runtime_resources_and_sign(
        application, browser, target_id, video_runtime, identity
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
    for name in ("work_dir", "archive", *DEPLOYMENT_ARGUMENTS):
        path = getattr(arguments, name)
        if path is not None:
            setattr(arguments, name, path.resolve())
    return arguments


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
    # Resolved before anything is built: a deployment the App would reject is
    # refused now rather than twenty minutes from now.
    deployment = resolve_deployment(arguments)
    result = build_macos_release(
        work_directory=arguments.work_dir,
        archive=arguments.archive,
        build_id=arguments.build_id,
        deployment=deployment,
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

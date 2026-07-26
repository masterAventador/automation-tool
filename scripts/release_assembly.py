#!/usr/bin/env python3
"""The production release assembly step: put the browser in, prove it, seal it.

`tauri.conf.json` deliberately does not declare the embedded browser under
`bundle.resources`. EB-16 measured what happens when it does: the bundler
follows symlinks while copying, which drops the Chrome for Testing framework's
`Resources`, `Libraries` and `Helpers` links, duplicates its 230 MB binary and
invalidates the upstream signature — the resulting package is judged
"browser component damaged" by the production resolver on the user's machine.

So the browser has to be installed after the bundle is built. That step used
to live inside `scripts/run_eb_16_acceptance.py` and nowhere else, which meant
a package built by the ordinary candidate path (P9-03/P9-04) shipped with no
browser at all and nothing refused to ship it. The startup gate caught it, but
that is a runtime backstop on the user's machine, not a release gate.

This module is that step, on a reusable path, with the verification made
mandatory: a bundle only becomes distributable after its installed browser has
been re-verified file-by-file against the EB-05 manifest. Sealing happens
strictly after the install, because a signature taken before the browser lands
does not cover it.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from build_embedded_browser_distribution import (
    DistributionRejected,
    verify_distribution,
)
from check_embedded_browser_package import browser_resource_root


class ReleaseAssemblyRejected(RuntimeError):
    """The bundle cannot be assembled into a distributable release."""


def _reject(message: str) -> None:
    raise ReleaseAssemblyRejected(f"release assembly rejected: {message}")


def require_packaged_browser(
    *,
    application: Path,
    target_id: str,
    platform: str,
    enforce_archive_lock: bool = True,
) -> Path:
    """Fail closed unless the bundle carries a complete, verified browser.

    This is the release gate. An ordinary candidate build produces a bundle
    that fails here, which is the point: such a bundle must never reach a disk
    image, an installer or a user.
    """
    installed = browser_resource_root(application, platform)
    if not installed.is_dir():
        _reject(
            f"the bundle carries no embedded browser at {installed} — it was built "
            "without the release assembly step"
        )
    try:
        verify_distribution(
            staging=installed,
            target_id=target_id,
            enforce_archive_lock=enforce_archive_lock,
        )
    except DistributionRejected as error:
        _reject(f"the packaged browser does not match its manifest: {error}")
    return installed


def install_and_seal(
    *,
    application: Path,
    staging: Path,
    target_id: str,
    platform: str,
    seal: Callable[[Path], None],
    enforce_archive_lock: bool = True,
) -> Path:
    """Install the staged browser into a built bundle, verify it, then seal.

    `seal` is required rather than defaulted. It used to default to an ad-hoc
    signature, and once every release path passed its own Developer ID seal that
    default had no callers left — it could only hand a future one a bundle
    Gatekeeper offers the customer "Move to Trash" for, silently.

    On any rejection the partially installed tree is removed and nothing is
    sealed, so a failed assembly cannot leave behind a bundle that later steps
    would mistake for a finished one.
    """
    from build_embedded_browser_distribution import install_distribution

    installed = browser_resource_root(application, platform)
    if installed.is_symlink() or installed.exists():
        _reject(f"the bundle already carries an embedded browser at {installed}")
    try:
        install_distribution(staging=staging, destination=installed)
        require_packaged_browser(
            application=application,
            target_id=target_id,
            platform=platform,
            enforce_archive_lock=enforce_archive_lock,
        )
    except BaseException:
        shutil.rmtree(installed, ignore_errors=True)
        raise
    seal(application)
    return installed


class _VideoResource:
    """One runtime resource the production video code resolves from Resources.

    `staging_name` is what the build scripts produce; `installed_parts` is where
    the production resolver actually looks, which is not always the same shape —
    both Workers are read from `<name>/package/...` while the media toolchain is
    read from `<name>/...` directly. Getting this mapping wrong produces a
    bundle that looks complete and still fails at runtime, so the mapping is
    declared once here rather than reconstructed at each call site.
    """

    def __init__(
        self,
        *,
        staging_name: str,
        installed_parts: tuple[str, ...],
        required_files: tuple[str, ...],
        windows_executables: tuple[str, ...] = (),
    ) -> None:
        self.staging_name = staging_name
        self.installed_parts = installed_parts
        self.required_files = required_files
        self.windows_executables = windows_executables

    def required_for(self, platform: str) -> tuple[str, ...]:
        if platform != "windows":
            return self.required_files
        return tuple(
            f"{name}.exe" if name in self.windows_executables else name
            for name in self.required_files
        )


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
# One declaration of what a distributable package must carry, shared with
# `frontend/scripts/audit-production-package.mjs` and
# `scripts/check_release_package_wiring.py`. Each gate used to know its own hand
# copied subset of this list, which is how three video runtime resources reached
# a user while every gate stayed green.
RELEASE_RESOURCE_CONTRACT = (
    REPOSITORY_ROOT / "contracts/quality/release-package-resources.v1.json"
)


def load_release_resources() -> tuple[dict[str, object], ...]:
    """Return every resource a distributable package must carry, in order."""
    document = json.loads(RELEASE_RESOURCE_CONTRACT.read_text(encoding="utf-8"))
    resources = document.get("resources")
    if not isinstance(resources, list) or not resources:
        _reject("the release package resource contract declares no resources")
    return tuple(resources)


RELEASE_PACKAGE_RESOURCES: tuple[dict[str, object], ...] = load_release_resources()

VIDEO_RUNTIME_RESOURCES: tuple[_VideoResource, ...] = tuple(
    _VideoResource(
        staging_name=str(resource["name"]),
        installed_parts=tuple(resource["installedParts"]),
        required_files=tuple(resource["requiredFiles"]),
        windows_executables=tuple(resource["windowsExecutables"]),
    )
    for resource in RELEASE_PACKAGE_RESOURCES
    if resource["category"] == "video"
)


def resource_directory(application: Path, platform: str) -> Path:
    """Return the directory the production resolver treats as its resource root."""
    if platform == "macos":
        return application.joinpath("Contents", "Resources")
    if platform == "windows":
        return application
    _reject(f"unsupported package platform: {platform}")
    raise AssertionError("unreachable")


def require_packaged_video_runtime(
    *, application: Path, platform: str
) -> dict[str, Path]:
    """Fail closed unless the bundle carries all three video runtime resources.

    This is the second half of the same release gate as the browser. The video
    resources went missing for the same reason the browser did — every BM/IM
    acceptance script builds ffmpeg and both Workers itself and hands the paths
    to a `video-studio-e2e` build through environment variables, while the
    production build reads this directory and nothing ever wrote to it.
    """
    root = resource_directory(application, platform)
    installed: dict[str, Path] = {}
    for resource in VIDEO_RUNTIME_RESOURCES:
        location = root.joinpath(*resource.installed_parts)
        if not location.is_dir():
            _reject(
                f"the bundle carries no {resource.staging_name} at {location} — it "
                "was built without the video runtime assembly step"
            )
        for name in resource.required_for(platform):
            payload = location / name
            if not payload.is_file() or payload.stat().st_size == 0:
                _reject(
                    f"{resource.staging_name} is incomplete: {name} is missing or "
                    "empty, so the production resolver would find the directory "
                    "and still fail to launch"
                )
        installed[resource.staging_name] = location
    return installed


def install_video_runtime(
    *, application: Path, staging: Path, platform: str
) -> dict[str, Path]:
    """Install the three video runtime resources, then verify them as a set.

    On any rejection every tree installed by this call is removed, so a failed
    assembly cannot leave a partially populated bundle for a later step to
    mistake for a finished one.
    """
    root = resource_directory(application, platform)
    written: list[Path] = []
    try:
        for resource in VIDEO_RUNTIME_RESOURCES:
            source = staging / resource.staging_name
            if not source.is_dir():
                _reject(
                    f"the staging tree carries no {resource.staging_name} at {source}"
                )
            destination = root.joinpath(*resource.installed_parts)
            if destination.is_symlink() or destination.exists():
                _reject(
                    f"the bundle already carries {resource.staging_name} at "
                    f"{destination}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            # The video resources contain no symlinked frameworks, so a plain
            # copy is correct here; the browser needs its own installer because
            # its framework links must survive.
            shutil.copytree(source, destination, symlinks=True)
            written.append(root / resource.installed_parts[0])
        return require_packaged_video_runtime(
            application=application, platform=platform
        )
    except BaseException:
        for path in written:
            shutil.rmtree(path, ignore_errors=True)
        raise


# ---------------------------------------------------------------------------
# Developer ID signing, notarisation, and the gate that decides distributability
# ---------------------------------------------------------------------------
#
# Every package this project has produced so far carried `codesign --sign -`.
# On a machine that did not build it, Gatekeeper reports such a bundle as
# damaged and offers exactly one default button: "Move to Trash". That is what
# a customer sees, and it is not something a release gate may leave to whoever
# happens to run the build.
#
# Apple's requirement is not merely "signed". A notarised package must have
# every Mach-O in it signed by one Developer ID certificate, with the hardened
# runtime enabled and a secure timestamp, and the signatures must be applied
# from the inside out — sealing a bundle covers the bytes of everything inside
# it, so a nested helper signed after its container invalidates the container.

MACOS_SIGNING_CONTRACT = REPOSITORY_ROOT / "contracts/quality/macos-release-signing.v1.json"

# What `xattr` writes onto anything downloaded with a browser. Reproducing it is
# the whole point of the gate: an artifact assessed without it is assessed under
# rules the customer's machine will not use.
QUARANTINE_ATTRIBUTE = "com.apple.quarantine"
QUARANTINE_DOWNLOADED = "0083;0;Safari;"

# The only verdict that means "the customer can open this". `accepted` on its
# own is not enough — an unnotarised Developer ID build is also `accepted` on
# the machine that signed it, and rejected everywhere else.
NOTARIZED_SOURCE = "source=Notarized Developer ID"

_MACH_O_MAGICS = frozenset(
    {
        b"\xcf\xfa\xed\xfe",  # 64-bit, little endian
        b"\xce\xfa\xed\xfe",  # 32-bit, little endian
        b"\xfe\xed\xfa\xcf",  # 64-bit, big endian
        b"\xfe\xed\xfa\xce",  # 32-bit, big endian
        b"\xca\xfe\xba\xbe",  # universal
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    }
)
_CODE_BUNDLE_SUFFIXES = (".app", ".framework")

# `codesign` writes these itself. Handing one back to it, or carrying one into a
# digest inventory taken before signing, produces a tree that disagrees with
# its own manifest.
_SIGNATURE_ARTEFACTS = ("_CodeSignature", "CodeResources")

Runner = Callable[[list[str]], str]


@dataclass(frozen=True)
class SigningIdentity:
    """The one identity a distributable macOS package is signed under."""

    certificate: str
    team_id: str
    notary_profile: str


def _signing_contract() -> dict[str, object]:
    document = json.loads(MACOS_SIGNING_CONTRACT.read_text(encoding="utf-8"))
    if document.get("schemaVersion") != 1 or document.get("policy") != "fail_closed":
        _reject("the macOS signing contract shape is invalid")
    return document


def load_signing_identity() -> SigningIdentity:
    """Read the release signing identity. One declaration, one reader.

    The identity is a configuration value, so it lives in a contract rather than
    in code; what must not vary is the path that reads it. There is no
    environment variable, build mode or feature flag here that would let an
    acceptance run and a customer build resolve different identities — that
    class of divergence is what shipped a package with no browser in it.
    """
    document = _signing_contract()
    certificate = document.get("certificate")
    team_id = document.get("teamId")
    notary_profile = document.get("notaryKeychainProfile")
    if not isinstance(certificate, str) or not certificate.startswith(
        "Developer ID Application:"
    ):
        _reject("the signing contract declares no Developer ID Application certificate")
    if not isinstance(team_id, str) or not team_id:
        _reject("the signing contract declares no Team ID")
    if not isinstance(notary_profile, str) or not notary_profile:
        _reject("the signing contract declares no notary keychain profile")
    return SigningIdentity(
        certificate=certificate, team_id=team_id, notary_profile=notary_profile
    )


def entitlements_for(component: str) -> Path | None:
    """Return the entitlements plist a component is signed with, if any.

    A component with no entry gets no entitlements at all. That is the default
    and it is the one to keep: every grant weakens the hardened runtime for the
    process that receives it, so the contract requires each one to name the
    component and the observed failure that forced it.
    """
    components = _signing_contract().get("components")
    if not isinstance(components, dict):
        _reject("the signing contract declares no components")
    declared = components.get(component)
    if declared is None:
        _reject(f"{component} is not a declared signing component")
    entitlements = declared.get("entitlements")
    if entitlements is None:
        return None
    plist = entitlements.get("plist")
    reasons = entitlements.get("reasons")
    if not isinstance(plist, str) or not isinstance(reasons, dict) or not reasons:
        _reject(f"{component} declares entitlements without a justification")
    path = REPOSITORY_ROOT / plist
    if not path.is_file():
        _reject(f"{component} declares an entitlements file that does not exist")
    granted = set(plistlib.loads(path.read_bytes()))
    if granted != set(reasons):
        _reject(
            f"{component} grants {sorted(granted - set(reasons))} without a reason "
            f"and justifies {sorted(set(reasons) - granted)} it does not grant"
        )
    return path


def _is_mach_o(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) in _MACH_O_MAGICS
    except OSError:
        return False


def inventoried_payloads(
    application: Path, platform: str
) -> tuple[Path, ...]:
    """Where each release resource that carries its own digest manifest lands.

    Derived from the single resource declaration rather than listed here, so a
    resource added to the contract cannot be left out of the exclusion by
    omission — which would re-sign it after its manifest was taken and leave
    the product rejecting its own payload on the customer's machine.
    """
    root = resource_directory(application, platform)
    return tuple(
        root.joinpath(*resource["installedParts"])
        for resource in RELEASE_PACKAGE_RESOURCES
    )


def signable_nodes(
    root: Path, exclude: tuple[Path, ...] = ()
) -> tuple[Path, ...]:
    """Every piece of code in a tree, ordered innermost first.

    Three rules decide what is returned and in what order, and each comes from
    a measurement this repository has already paid for:

    * Nested code is signed before whatever contains it. Sorting by path depth,
      deepest first, guarantees it — a contained path always has more
      components than its container.
    * Symlinks are never handed to `codesign`, which would replace the link
      with a regular file. The Chrome for Testing framework is held together by
      `Versions/Current`, `Resources`, `Libraries` and `Helpers` links, and
      EB-16 measured that losing them leaves the browser "damaged" on the
      customer's machine.
    * Anything under `exclude` is left alone. The browser, the Local Executor
      and the media toolchain are each signed before their own digest manifest
      is taken; visiting them again from the outer seal would rewrite the bytes
      those manifests describe.
    """
    nodes: set[Path] = set()
    if root.is_dir() and root.suffix in _CODE_BUNDLE_SUFFIXES:
        nodes.add(root)
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in _SIGNATURE_ARTEFACTS for part in relative.parts):
            continue
        if any(path.is_relative_to(payload) for payload in exclude):
            continue
        if path.is_dir():
            if path.suffix in _CODE_BUNDLE_SUFFIXES:
                nodes.add(path)
        elif path.is_file() and _is_mach_o(path):
            nodes.add(path)
    return tuple(sorted(nodes, key=lambda path: (-len(path.parts), os.fspath(path))))


def _run_tool(command: list[str]) -> str:
    """Run one release tool, returning everything it said.

    `spctl` writes its verdict to stderr, so both streams are captured and
    returned together; a gate that read only stdout would see an empty string
    and have to guess.
    """
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        _reject(f"{Path(command[0]).name} failed: {output.strip()}")
    return output


def sign_tree(
    *,
    root: Path,
    component: str,
    identity: SigningIdentity,
    run: Runner = _run_tool,
    exclude: tuple[Path, ...] = (),
) -> tuple[Path, ...]:
    """Sign every piece of code in one release payload, innermost first."""
    entitlements = entitlements_for(component)
    nodes = signable_nodes(root, exclude=exclude)
    if not nodes:
        _reject(f"{component} carries no signable code at {root}")
    for node in nodes:
        command = [
            "codesign",
            "--force",
            "--sign",
            identity.certificate,
            # Both are notarisation requirements, not preferences: a signature
            # without a hardened runtime or without a secure timestamp is
            # rejected however well formed it otherwise is.
            "--options",
            "runtime",
            "--timestamp",
        ]
        if entitlements is not None:
            command += ["--entitlements", os.fspath(entitlements)]
        command.append(os.fspath(node))
        run(command)
    return nodes


def notarize_and_staple(
    *, artifact: Path, identity: SigningIdentity, run: Runner = _run_tool
) -> str:
    """Submit one artifact, wait for the verdict, and staple the ticket.

    Stapling is the half that is easy to skip and expensive to skip: without a
    stapled ticket the customer's machine has to reach Apple to learn that the
    package was notarised, so a demo on a bad network shows the same refusal as
    an unsigned build.
    """
    submission = artifact
    archive: Path | None = None
    if artifact.suffix == ".app":
        # The notary service takes an archive, and only `ditto` preserves the
        # symlinks and extended attributes the Chrome framework depends on.
        archive = artifact.with_name(f"{artifact.name}.notarization.zip")
        archive.unlink(missing_ok=True)
        run(["ditto", "-c", "-k", "--keepParent", os.fspath(artifact), os.fspath(archive)])
        submission = archive
    try:
        output = run(
            [
                "xcrun",
                "notarytool",
                "submit",
                os.fspath(submission),
                "--keychain-profile",
                identity.notary_profile,
                "--wait",
                "--output-format",
                "json",
            ]
        )
        result = _notarization_result(output)
        identifier = str(result.get("id", ""))
        if result.get("status") != "Accepted":
            _reject(
                f"the notary service returned {result.get('status')!r} for "
                f"{artifact.name}; read the details with: xcrun notarytool log "
                f"{identifier} --keychain-profile {identity.notary_profile}"
            )
    finally:
        if archive is not None:
            archive.unlink(missing_ok=True)
    run(["xcrun", "stapler", "staple", os.fspath(artifact)])
    return identifier


def _notarization_result(output: str) -> dict[str, object]:
    """Pull the submission record out of whatever `notarytool` printed.

    `--output-format json` is not a promise that the reply is one line: with
    `--wait` the tool also prints progress, and the JSON itself may be
    pretty-printed. Scanning for the last decodable object handles every shape,
    which matters because a parse failure here would read as a rejected
    notarisation and fail a release that Apple actually accepted.
    """
    decoder = json.JSONDecoder()
    for position in reversed(
        [index for index, character in enumerate(output) if character == "{"]
    ):
        try:
            parsed, _ = decoder.raw_decode(output[position:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "status" in parsed:
            return parsed
    _reject(f"the notary service returned no readable result: {output.strip()[-800:]}")
    raise AssertionError("unreachable")


def require_distributable_artifact(
    *, artifact: Path, run: Runner = _run_tool
) -> str:
    """The release gate: refuse anything the customer's machine would refuse.

    "The notary service accepted the submission" and "the customer can open the
    download" are different claims. This project has already shipped a package
    on the strength of a green acceptance run that exercised something other
    than the artifact a user received, so the gate does not ask about the
    submission. It marks the artifact exactly as a browser download would, and
    then asks Gatekeeper the question the customer's machine asks.
    """
    run(
        [
            "xattr",
            "-w",
            QUARANTINE_ATTRIBUTE,
            QUARANTINE_DOWNLOADED,
            os.fspath(artifact),
        ]
    )
    verdict = run(
        [
            "spctl",
            "--assess",
            "-vvv",
            "--type",
            "open",
            "--context",
            "context:primary-signature",
            os.fspath(artifact),
        ]
    )
    if "accepted" not in verdict:
        _reject(
            f"Gatekeeper refuses {artifact.name} once it carries the quarantine flag "
            f"a download gets, so it must not be distributed: {verdict.strip()}"
        )
    if NOTARIZED_SOURCE not in verdict:
        _reject(
            f"{artifact.name} is not notarised — Gatekeeper reports "
            f"{verdict.strip()!r}. It opens only on machines that already trust "
            "this build, which does not include the customer's."
        )
    return verdict


__all__ = [
    "MACOS_SIGNING_CONTRACT",
    "NOTARIZED_SOURCE",
    "QUARANTINE_ATTRIBUTE",
    "QUARANTINE_DOWNLOADED",
    "RELEASE_PACKAGE_RESOURCES",
    "RELEASE_RESOURCE_CONTRACT",
    "REPOSITORY_ROOT",
    "VIDEO_RUNTIME_RESOURCES",
    "ReleaseAssemblyRejected",
    "SigningIdentity",
    "entitlements_for",
    "install_and_seal",
    "inventoried_payloads",
    "install_video_runtime",
    "load_release_resources",
    "load_signing_identity",
    "notarize_and_staple",
    "require_distributable_artifact",
    "require_packaged_browser",
    "require_packaged_video_runtime",
    "resource_directory",
    "sign_tree",
    "signable_nodes",
]

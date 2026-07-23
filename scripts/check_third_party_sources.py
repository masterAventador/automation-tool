#!/usr/bin/env python3
"""Fail closed when locked third-party source or rights metadata drifts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPOSITORY_ROOT / "contracts/quality/third-party-sources.v1.json"
RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/asset-rights-policy.v1.json"
SBOM_PATH = REPOSITORY_ROOT / "third_party/source-submodules.cdx.json"


def fail(message: str) -> None:
    raise SystemExit(f"third-party source check failed: {message}")


def load_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {path.relative_to(REPOSITORY_ROOT)}: {error}")
    if not isinstance(value, dict):
        fail(f"{path.relative_to(REPOSITORY_ROOT)} must contain an object")
    return value


def run_git(*args: str, cwd: Path = REPOSITORY_ROOT) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        fail(detail)
    return result.stdout.strip()


def run_git_bytes(*args: str, cwd: Path = REPOSITORY_ROOT) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip() or "git command failed"
        fail(detail)
    return result.stdout


def safe_repository_path(value: object, field: str) -> tuple[str, Path]:
    if not isinstance(value, str) or not value:
        fail(f"{field} must be a non-empty repository-relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or str(pure) != value:
        fail(f"{field} is not canonical")
    return value, REPOSITORY_ROOT.joinpath(*pure.parts)


def validate_source(source: object) -> dict[str, str]:
    if not isinstance(source, dict):
        fail("every source lock must be an object")
    required_strings = ("id", "path", "url", "tag", "commit")
    values: dict[str, str] = {}
    for field in required_strings:
        value = source.get(field)
        if not isinstance(value, str) or not value:
            fail(f"source {field} must be a non-empty string")
        values[field] = value
    if len(values["commit"]) != 40 or any(
        character not in "0123456789abcdef" for character in values["commit"]
    ):
        fail(f"{values['id']} commit must be a full lowercase SHA-1")
    if not values["url"].startswith("https://github.com/") or not values["url"].endswith(".git"):
        fail(f"{values['id']} source URL must be an official HTTPS GitHub repository")

    relative_path, source_root = safe_repository_path(values["path"], "source.path")
    if not (source_root / ".git").is_file():
        fail(f"{values['id']} submodule is not initialized")
    if run_git("rev-parse", "HEAD", cwd=source_root) != values["commit"]:
        fail(f"{values['id']} checkout does not match the locked commit")
    if run_git("status", "--porcelain", "--untracked-files=all", cwd=source_root):
        fail(f"{values['id']} submodule is dirty; upstream source is read-only")
    if run_git("remote", "get-url", "origin", cwd=source_root) != values["url"]:
        fail(f"{values['id']} origin does not match the lock")
    if run_git("describe", "--tags", "--exact-match", "HEAD", cwd=source_root) != values["tag"]:
        fail(f"{values['id']} checkout is not the locked release tag")

    gitlink = run_git("ls-files", "--stage", "--", relative_path).split()
    if len(gitlink) < 4 or gitlink[0] != "160000" or gitlink[1] != values["commit"]:
        fail(f"{values['id']} parent gitlink does not match the locked commit")
    module_prefix = f"submodule.{relative_path}"
    configured_path = run_git("config", "--file", ".gitmodules", "--get", f"{module_prefix}.path")
    if configured_path != relative_path:
        fail(f"{values['id']} .gitmodules path drifted")
    if run_git("config", "--file", ".gitmodules", "--get", f"{module_prefix}.url") != values["url"]:
        fail(f"{values['id']} .gitmodules URL drifted")

    license_record = source.get("license")
    if not isinstance(license_record, dict):
        fail(f"{values['id']} license record is missing")
    spdx = license_record.get("spdx")
    if not isinstance(spdx, str) or not spdx:
        fail(f"{values['id']} SPDX identifier is missing")
    license_relative, _license_path = safe_repository_path(
        license_record.get("path"), f"{values['id']}.license.path"
    )
    expected_license_hash = license_record.get("sha256")
    if not isinstance(expected_license_hash, str) or len(expected_license_hash) != 64:
        fail(f"{values['id']} license SHA-256 is invalid")
    checked_out_license = source_root / license_relative
    if not checked_out_license.is_file() or checked_out_license.is_symlink():
        fail(f"{values['id']} license must be a regular checked-out file")
    # Bind the license to the locked commit's exact blob. Git may materialize
    # that blob with CRLF in a clean Windows checkout when core.autocrlf is
    # enabled; hashing the working-tree bytes would make the same commit appear
    # to have different license text on different platforms.
    license_blob = run_git_bytes("show", f"HEAD:{license_relative}", cwd=source_root)
    actual_license_hash = hashlib.sha256(license_blob).hexdigest()
    if actual_license_hash != expected_license_hash:
        fail(f"{values['id']} license text drifted")

    tag_object = source.get("tagObject")
    if tag_object is not None and (
        not isinstance(tag_object, str)
        or run_git("rev-parse", f"{values['tag']}^{{tag}}", cwd=source_root) != tag_object
    ):
        fail(f"{values['id']} annotated tag object drifted")

    for url_field in ("releaseUrl", "securityAdvisoriesUrl"):
        url = source.get(url_field)
        if not isinstance(url, str) or not url.startswith("https://github.com/"):
            fail(f"{values['id']} {url_field} is invalid")
    return {
        **values,
        "license": spdx,
        "licenseSha256": actual_license_hash,
    }


def validate_policy(lock: dict[str, object]) -> list[dict[str, str]]:
    if lock.get("schemaVersion") != 1:
        fail("source lock schemaVersion must be 1")
    policy = lock.get("policy")
    if not isinstance(policy, dict):
        fail("source policy is missing")
    required_policy = {
        "floatingReferencesAllowed": False,
        "runtimeSourceUpdatesAllowed": False,
        "submoduleModificationsAllowed": False,
        "upgradeMode": "independent_reviewed_task",
        "securityReviewCadence": "before_release_and_weekly",
    }
    for field, expected in required_policy.items():
        if policy.get(field) != expected:
            fail(f"source policy {field} must be {expected!r}")
    sources = lock.get("sources")
    if not isinstance(sources, list) or len(sources) != 2:
        fail("exactly two source submodules must be locked")
    validated = [validate_source(source) for source in sources]
    if len({source["id"] for source in validated}) != len(validated):
        fail("source ids must be unique")
    module_paths = run_git(
        "config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"
    ).splitlines()
    configured_paths = {line.split(maxsplit=1)[1] for line in module_paths}
    if configured_paths != {source["path"] for source in validated}:
        fail(".gitmodules must contain exactly the locked source paths")
    branch_result = subprocess.run(
        [
            "git",
            "config",
            "--file",
            ".gitmodules",
            "--get-regexp",
            r"^submodule\..*\.branch$",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if branch_result.returncode == 0 and branch_result.stdout.strip():
        fail("submodules must not track branches")
    if branch_result.returncode not in (0, 1):
        fail("cannot inspect .gitmodules branch configuration")
    return validated


def validate_asset_rights() -> None:
    rights = load_object(RIGHTS_PATH)
    if rights.get("schemaVersion") != 1 or rights.get("defaultDecision") != "deny":
        fail("asset rights must use schema v1 and deny unregistered assets")
    fields = rights.get("distributionRequiredFields")
    if not isinstance(fields, list) or len(fields) != len(set(fields)):
        fail("distributionRequiredFields must be a unique list")
    categories = rights.get("requiredCategories")
    expected = {"font", "stock_media", "music_sfx", "codec_binary", "map_3d", "generated"}
    if not isinstance(categories, dict) or set(categories) != expected:
        fail("asset rights categories are incomplete")
    entries = rights.get("entries")
    if not isinstance(entries, list):
        fail("asset rights entries must be a list")


def validate_sbom(sources: list[dict[str, str]]) -> None:
    sbom = load_object(SBOM_PATH)
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.6":
        fail("source SBOM must be CycloneDX 1.6")
    components = sbom.get("components")
    if not isinstance(components, list) or len(components) != len(sources):
        fail("source SBOM component count does not match source locks")
    by_commit: dict[str, dict[str, object]] = {}
    for component in components:
        if not isinstance(component, dict):
            fail("source SBOM component must be an object")
        properties = component.get("properties")
        if not isinstance(properties, list):
            fail("source SBOM component properties are missing")
        property_map = {
            item.get("name"): item.get("value") for item in properties if isinstance(item, dict)
        }
        commit = property_map.get("automation-tool:gitCommit")
        if not isinstance(commit, str):
            fail("source SBOM component git commit is missing")
        by_commit[commit] = component
    for source in sources:
        component = by_commit.get(source["commit"])
        if component is None or component.get("version") != source["tag"]:
            fail(f"{source['id']} source SBOM version drifted")
        licenses = component.get("licenses")
        if not isinstance(licenses, list) or not any(
            isinstance(item, dict)
            and isinstance(item.get("license"), dict)
            and item["license"].get("id") == source["license"]
            for item in licenses
        ):
            fail(f"{source['id']} source SBOM license drifted")


def main() -> None:
    sources = validate_policy(load_object(LOCK_PATH))
    validate_asset_rights()
    validate_sbom(sources)
    print("third-party source locks, licenses, rights policy and SBOM are valid")


if __name__ == "__main__":
    main()

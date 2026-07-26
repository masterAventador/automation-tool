#!/usr/bin/env python3
"""VE-04 real-cloud and production-App acceptance entrypoint.

Runs the deterministic VE-04 gates, then the real-credential acceptance:

1. real OSS staging round-trip against the user-provided same-region bucket
   (upload by production staging-plan keys, dedup, lifecycle configure/restore,
   RAM rejection probes, full object cleanup);
2. real ICE gateway signature acceptance through the production Rust path
   (`cargo test --test video_editing_service_settings_real`);
3. hidden isolated production App: save real credentials from the settings
   page, reload, run a real connection test and clear the configuration.

Credentials are read from `.local/secrets/aliyun-video-editing.json` (override
with `VE04_CREDENTIALS_FILE`) and never enter Git, logs or build artifacts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets as random_secrets
import shutil
import subprocess
import urllib.error
import urllib.request
from email.utils import formatdate
from pathlib import Path

from desktop_e2e_prerequisites import video_studio_startup_harness
from run_vf_06_acceptance import (
    APP_IDENTIFIER,
    FRONTEND,
    TAURI_CONFIG,
    app_data_directory,
    pnpm_executable,
    require_port_closed,
    unused_loopback_port,
)

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
STAGING_PREFIX = "editing-staging/v1/"
SPECS = ("./e2e-tauri/video-editing-service.spec.ts",)

LIFECYCLE_RULE_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<LifecycleConfiguration><Rule>"
    "<ID>automation-tool-editing-staging-v1</ID>"
    f"<Prefix>{STAGING_PREFIX}</Prefix>"
    "<Status>Enabled</Status>"
    "<Expiration><Days>7</Days></Expiration>"
    "</Rule></LifecycleConfiguration>"
)


def credentials_file() -> Path:
    override = os.environ.get("VE04_CREDENTIALS_FILE")
    path = Path(override) if override else ROOT / ".local/secrets/aliyun-video-editing.json"
    if not path.is_file():
        raise SystemExit(
            "VE-04 real acceptance needs the local Aliyun credentials JSON; "
            "copy it to .local/secrets/aliyun-video-editing.json "
            "(see docs/credentials-aliyun-video-editing.md) or set VE04_CREDENTIALS_FILE"
        )
    return path


class OssClient:
    """Minimal stdlib OSS client using header-based signatures.

    Only used by this acceptance script; the product never talks to OSS yet.
    """

    def __init__(self, access_key_id: str, access_key_secret: str, endpoint: str) -> None:
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self._endpoint = endpoint

    def request(
        self,
        method: str,
        bucket: str,
        object_key: str = "",
        subresource: str = "",
        query: str = "",
        body: bytes = b"",
        content_type: str = "",
        with_md5: bool = False,
    ) -> tuple[int, bytes]:
        date = formatdate(usegmt=True)
        content_md5 = base64.b64encode(hashlib.md5(body).digest()).decode() if with_md5 else ""
        resource = f"/{bucket}/{object_key}{subresource}"
        string_to_sign = f"{method}\n{content_md5}\n{content_type}\n{date}\n{resource}"
        signature = base64.b64encode(
            hmac.new(
                self._access_key_secret.encode(), string_to_sign.encode(), hashlib.sha1
            ).digest()
        ).decode()
        url = f"https://{bucket}.{self._endpoint}/{object_key}{subresource}"
        if query:
            url += ("&" if subresource else "?") + query
        request = urllib.request.Request(url, method=method, data=body if body else None)
        request.add_header("Date", date)
        request.add_header("Authorization", f"OSS {self._access_key_id}:{signature}")
        if content_type:
            request.add_header("Content-Type", content_type)
        if content_md5:
            request.add_header("Content-MD5", content_md5)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def assert_no_secret(self, payload: bytes) -> None:
        text = payload.decode("utf-8", errors="replace")
        if self._access_key_secret in text:
            raise SystemExit("VE-04 FATAL: OSS response reflected the access key secret")


def production_staging_keys(assets: list[dict[str, object]]) -> dict[str, str]:
    """Compute object keys with the production domain code, not a re-implementation."""
    snippet = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from automation_tool.control_plane.domain.aliyun_ims_editing_staging import (\n"
        "    AliyunImsRegion, StagingAsset, build_media_staging_plan,\n"
        "    load_aliyun_ims_editing_staging_contract,\n"
        ")\n"
        "specs = json.loads(sys.argv[1])\n"
        "contract = load_aliyun_ims_editing_staging_contract(\n"
        "    Path(sys.argv[2]) / 'contracts/video/aliyun-ims-editing-staging.v1.json'\n"
        ")\n"
        "plan = build_media_staging_plan(\n"
        "    contract=contract,\n"
        "    service_region=AliyunImsRegion.CN_BEIJING,\n"
        "    bucket_region=AliyunImsRegion.CN_BEIJING,\n"
        "    assets=tuple(StagingAsset(**spec) for spec in specs),\n"
        ")\n"
        "print(json.dumps({\n"
        "    'object_count': len(plan.objects),\n"
        "    'keys': {spec['logical_id']: plan.object_key_for(spec['logical_id'])\n"
        "             for spec in specs},\n"
        "}))\n"
    )
    completed = subprocess.run(
        ["uv", "run", "python", "-c", snippet, json.dumps(assets), str(ROOT)],
        cwd=BACKEND,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    return result["keys"]


def run_real_oss_staging_acceptance(credentials: dict[str, str]) -> None:
    bucket = credentials["ossBucket"]
    region = credentials["region"]
    client = OssClient(
        credentials["accessKeyId"], credentials["accessKeySecret"], credentials["ossEndpoint"]
    )

    # 1. Same-region check against the real bucket location.
    status, payload = client.request("GET", bucket, subresource="?location")
    client.assert_no_secret(payload)
    if (
        status != 200
        or f"<LocationConstraint>oss-{region}</LocationConstraint>" not in payload.decode()
    ):
        raise SystemExit(f"VE-04 bucket location check failed with HTTP {status}")
    print(f"real OSS: bucket location matches oss-{region}")

    # 2. Build test payloads (unique per run) and production staging keys.
    run_nonce = random_secrets.token_bytes(16)
    payload_video = b"VE-04 staging acceptance video payload\n" + run_nonce + b"v" * 4096
    payload_image = b"VE-04 staging acceptance image payload\n" + run_nonce + b"i" * 2048
    digest_video = hashlib.sha256(payload_video).hexdigest()
    digest_image = hashlib.sha256(payload_image).hexdigest()
    assets: list[dict[str, object]] = [
        {
            "logical_id": "ve04-clip",
            "sha256_hex": digest_video,
            "size_bytes": len(payload_video),
            "extension": ".mp4",
        },
        {
            "logical_id": "ve04-clip-duplicate",
            "sha256_hex": digest_video,
            "size_bytes": len(payload_video),
            "extension": ".mp4",
        },
        {
            "logical_id": "ve04-cover",
            "sha256_hex": digest_image,
            "size_bytes": len(payload_image),
            "extension": ".png",
        },
    ]
    keys = production_staging_keys(assets)
    if keys["ve04-clip"] != keys["ve04-clip-duplicate"]:
        raise SystemExit("VE-04 digest deduplication produced two keys for one digest")
    video_key = keys["ve04-clip"]
    image_key = keys["ve04-cover"]
    for key, digest, extension in (
        (video_key, digest_video, ".mp4"),
        (image_key, digest_image, ".png"),
    ):
        if key != f"{STAGING_PREFIX}{digest}{extension}":
            raise SystemExit("VE-04 staging key does not follow the contract template")
    print("real OSS: production staging plan produced deduplicated contract keys")

    uploaded: list[str] = []
    lifecycle_written = False
    try:
        # 3. Upload both objects, verify final state and dedup idempotency.
        for key, body in ((video_key, payload_video), (image_key, payload_image)):
            status, payload = client.request(
                "PUT", bucket, object_key=key, body=body, content_type="application/octet-stream"
            )
            client.assert_no_secret(payload)
            if status != 200:
                raise SystemExit(f"VE-04 staging upload failed with HTTP {status}")
            uploaded.append(key)
        status, payload = client.request(
            "PUT",
            bucket,
            object_key=video_key,
            body=payload_video,
            content_type="application/octet-stream",
        )
        if status != 200:
            raise SystemExit(f"VE-04 dedup re-upload failed with HTTP {status}")
        for key, body in ((video_key, payload_video), (image_key, payload_image)):
            status, _ = client.request("HEAD", bucket, object_key=key)
            if status != 200:
                raise SystemExit(f"VE-04 staged object missing after upload (HTTP {status})")
        status, payload = client.request(
            "GET", bucket, query=f"prefix={STAGING_PREFIX}&max-keys=100"
        )
        client.assert_no_secret(payload)
        listing = payload.decode()
        if status != 200 or listing.count("<Key>") != 2:
            raise SystemExit("VE-04 staging prefix does not contain exactly the two test objects")
        print("real OSS: two deduplicated objects uploaded, verified and re-put idempotently")

        # 4. Lifecycle rule is configurable exactly as the contract describes,
        #    then the bucket is restored to its previous no-lifecycle state.
        status, payload = client.request("GET", bucket, subresource="?lifecycle")
        client.assert_no_secret(payload)
        if status != 404 or b"NoSuchLifecycle" not in payload:
            raise SystemExit(
                f"VE-04 expected a bucket without lifecycle rules before the check (HTTP {status})"
            )
        status, payload = client.request(
            "PUT",
            bucket,
            subresource="?lifecycle",
            body=LIFECYCLE_RULE_XML.encode(),
            content_type="application/xml",
            with_md5=True,
        )
        client.assert_no_secret(payload)
        if status != 200:
            raise SystemExit(f"VE-04 lifecycle configuration failed with HTTP {status}")
        lifecycle_written = True
        status, payload = client.request("GET", bucket, subresource="?lifecycle")
        client.assert_no_secret(payload)
        lifecycle = payload.decode()
        if (
            status != 200
            or f"<Prefix>{STAGING_PREFIX}</Prefix>" not in lifecycle
            or "<Days>7</Days>" not in lifecycle
        ):
            raise SystemExit("VE-04 lifecycle rule readback does not match the contract rule")
        status, payload = client.request("DELETE", bucket, subresource="?lifecycle")
        if status != 204:
            raise SystemExit(f"VE-04 lifecycle restore failed with HTTP {status}")
        lifecycle_written = False
        status, payload = client.request("GET", bucket, subresource="?lifecycle")
        if status != 404:
            raise SystemExit("VE-04 bucket lifecycle was not restored to its original state")
        print("real OSS: contract lifecycle rule configured, verified and restored")

        # 5. Real RAM rejection probes: a foreign bucket and an absent bucket
        #    must both be rejected; no secret may appear in error bodies.
        status, payload = client.request("GET", "test", subresource="?acl")
        client.assert_no_secret(payload)
        if status != 403 or b"AccessDenied" not in payload:
            raise SystemExit(f"VE-04 foreign bucket access was not denied (HTTP {status})")
        absent_bucket = f"automation-tool-ve04-absent-{random_secrets.token_hex(6)}"
        status, payload = client.request("GET", absent_bucket, subresource="?location")
        client.assert_no_secret(payload)
        if status != 404 or b"NoSuchBucket" not in payload:
            raise SystemExit(f"VE-04 absent bucket access was not rejected (HTTP {status})")
        print("real OSS: foreign bucket denied (403) and absent bucket rejected (404)")
    finally:
        # 6. Cleanup: delete the test objects and any accidental lifecycle rule,
        #    then prove the staging prefix is empty again.
        if lifecycle_written:
            client.request("DELETE", bucket, subresource="?lifecycle")
        for key in uploaded:
            status, _ = client.request("DELETE", bucket, object_key=key)
            if status not in (204, 404):
                raise SystemExit(f"VE-04 cleanup failed to delete a staged object (HTTP {status})")
    for key in uploaded:
        status, _ = client.request("HEAD", bucket, object_key=key)
        if status != 404:
            raise SystemExit("VE-04 staged test object still exists after cleanup")
    status, payload = client.request("GET", bucket, query=f"prefix={STAGING_PREFIX}&max-keys=100")
    if status != 200 or payload.decode().count("<Key>") != 0:
        raise SystemExit("VE-04 staging prefix is not empty after cleanup")
    print("real OSS: staging objects deleted, bucket restored to its original state")


def run_real_gateway_rust_acceptance(credentials_path: Path) -> None:
    environment = dict(os.environ)
    environment["VE04_REAL_CREDENTIALS_FILE"] = str(credentials_path)
    subprocess.run(
        [
            "cargo",
            "test",
            "--locked",
            "--test",
            "video_editing_service_settings_real",
            "--",
            "--test-threads=1",
        ],
        cwd=FRONTEND / "src-tauri",
        env=environment,
        check=True,
    )
    print("real ICE: production ACS3-HMAC-SHA256 path accepted; tampered secret sanitized")


def run_desktop_acceptance(credentials_path: Path) -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("VE-04 acceptance must use its hidden isolated App")
    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    port = unused_loopback_port()
    environment = {key: value for key, value in os.environ.items() if key != "TAURI_WEBDRIVER_PORT"}
    environment["TAURI_WEBDRIVER_PORT"] = str(port)
    environment["VE04_SECRETS_FILE"] = str(credentials_path)
    spec_arguments: list[str] = []
    for spec in SPECS:
        spec_arguments.extend(["--spec", spec])
    try:
        with video_studio_startup_harness(
            private_app_data,
            environment=environment,
        ) as environment:
            subprocess.run(
                [pnpm_executable(), "build:tauri:video-studio-test"],
                cwd=FRONTEND,
                env=environment,
                check=True,
            )
            require_port_closed(port)
            subprocess.run(
                [
                    pnpm_executable(),
                    "exec",
                    "wdio",
                    "run",
                    "wdio.video-studio.conf.ts",
                    *spec_arguments,
                ],
                cwd=FRONTEND,
                env=environment,
                check=True,
            )
            require_port_closed(port)
    finally:
        restore = subprocess.run(
            [pnpm_executable(), "build"],
            cwd=FRONTEND,
            env=environment,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if private_app_data.exists():
            shutil.rmtree(private_app_data)
        require_port_closed(port)
        if restore.returncode != 0:
            raise RuntimeError("VE-04 failed to restore production Vite assets")


def main() -> int:
    required = (
        ROOT / "contracts/video/aliyun-ims-editing-staging.v1.json",
        ROOT / "backend/src/automation_tool/control_plane/domain/aliyun_ims_editing_staging.py",
        ROOT / "backend/tests/unit/control_plane/domain/test_aliyun_ims_editing_staging.py",
        ROOT / "frontend/src-tauri/src/video_editing_service_settings.rs",
        ROOT / "frontend/src-tauri/tests/video_editing_service_settings.rs",
        ROOT / "frontend/src-tauri/tests/video_editing_service_settings_real.rs",
        ROOT / "frontend/src/features/settings/VideoEditingServiceSettings.tsx",
        ROOT / "frontend/src/platform/tauri/video-editing-service-gateway.ts",
        ROOT / "frontend/e2e-tauri/video-editing-service.spec.ts",
        ROOT / "docs/development/VE-04.md",
    )
    missing = [path.relative_to(ROOT).as_posix() for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"VE-04 missing deliverables: {', '.join(missing)}")

    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(encoding="utf-8")
    rows = [line for line in roadmap.splitlines() if line.startswith("| VE-04 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise SystemExit("VE-04 roadmap row is missing, duplicated or incomplete")

    credentials_path = credentials_file()
    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    for field in ("accessKeyId", "accessKeySecret", "region", "ossBucket", "ossEndpoint"):
        if not isinstance(credentials.get(field), str) or not credentials[field]:
            raise SystemExit(f"VE-04 credentials file is missing field {field}")
    if credentials["region"] != "cn-beijing":
        raise SystemExit("VE-04 acceptance currently expects the cn-beijing staging setup")

    subprocess.run(
        [
            "uv",
            "run",
            "pytest",
            "tests/unit/control_plane/domain/test_aliyun_ims_editing_staging.py",
            "-q",
        ],
        cwd=BACKEND,
        check=True,
    )
    subprocess.run(
        [
            pnpm_executable(),
            "--dir",
            "frontend",
            "exec",
            "vitest",
            "run",
            "src/features/settings/VideoEditingServiceSettings.test.tsx",
            "src/platform/tauri/video-editing-service-gateway.test.ts",
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        ["cargo", "test", "--locked", "--test", "video_editing_service_settings"],
        cwd=FRONTEND / "src-tauri",
        check=True,
    )

    run_real_oss_staging_acceptance(credentials)
    run_real_gateway_rust_acceptance(credentials_path)
    run_desktop_acceptance(credentials_path)
    print("VE-04 aliyun editing credentials and media staging acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Repeatable single-host deployment of the customer Demo Control Plane.

Runs **on the cloud host** with nothing but the standard library. It is
idempotent: every run reuses the persisted secrets, the persisted PostgreSQL
volume and the already-registered Demo account, and only replaces the Control
Plane image, the schema and the Nginx site.

The host already serves unrelated business from its own Nginx and owns ports
80/443, so this deployment never binds a public port. The Control Plane binds
loopback only and the shared Nginx reverse proxies one dedicated server block
to it; PostgreSQL binds no host port at all.

    python3 deploy/cloud/deploy_cloud_demo.py --vcs-ref <40-hex> [--skip-build]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

CLOUD_ROOT: Final = Path(__file__).resolve().parent
REPOSITORY_ROOT: Final = CLOUD_ROOT.parents[1]
ENVIRONMENT_FILE: Final = CLOUD_ROOT / "demo-environment.json"
COMPOSE_FILE: Final = CLOUD_ROOT / "compose.yaml"
NGINX_TEMPLATE_FILE: Final = CLOUD_ROOT / "nginx-site.conf.template"
POSTGRESQL_ROOT: Final = REPOSITORY_ROOT / "deploy" / "postgresql"
BACKEND_ROOT: Final = REPOSITORY_ROOT / "backend"

STATE_DIRECTORY: Final = Path("/etc/automation-tool-demo")
SECRET_STATE_FILE: Final = STATE_DIRECTORY / "secrets.json"
DEPLOY_ENVIRONMENT_FILE: Final = STATE_DIRECTORY / "deploy.env"
BACKUP_DIRECTORY: Final = Path("/var/backups/automation-tool-demo")
NGINX_AVAILABLE: Final = Path("/etc/nginx/sites-available/automation-tool-demo.conf")
NGINX_ENABLED: Final = Path("/etc/nginx/sites-enabled/automation-tool-demo.conf")

RUNTIME_UID: Final = 65532
POSTGRES_UID: Final = 999
SECRET_FILE_MODE: Final = 0o400
DATABASE_NETWORK_ALIAS: Final = "postgres"
DATABASE_PORT: Final = 5432
CONTROL_PLANE_CONTAINER_PORT: Final = 8000

RUNTIME_SECRET_FILE_NAMES: Final = (
    "database-url",
    "account-password-pepper",
    "account-fingerprint-key",
    "account-operations-capability-digest",
    "action-authorization-private-key",
)
DATABASE_ROLES: Final = (
    "automation_tool_migrator",
    "automation_tool_app",
    "automation_tool_backup",
)
CAPABILITY_PREFIX: Final = "atoc1."
DEMO_LOGIN_NAME: Final = "xuanbai.demo"

_HOST_PATTERN: Final = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", re.ASCII
)
_ROLE_PATTERN: Final = re.compile(r"[a-z_][a-z0-9_]{0,62}", re.ASCII)
_REVISION_PATTERN: Final = re.compile(r"[0-9a-f]{40}", re.ASCII)
_LOOPBACK_ADDRESS: Final = "127.0.0.1"
_MINIMUM_UNPRIVILEGED_PORT: Final = 1024
_MAXIMUM_PORT: Final = 65535

_VOLUME_WRITER: Final = r"""import json
import os
import sys

payload = json.load(sys.stdin)
if not isinstance(payload, dict) or not payload:
    raise SystemExit(2)
for name, specification in payload.items():
    if (
        not isinstance(name, str)
        or "/" in name
        or not isinstance(specification, dict)
        or set(specification) != {"value", "uid", "mode"}
    ):
        raise SystemExit(2)
    value = specification["value"]
    uid = specification["uid"]
    mode = specification["mode"]
    if not isinstance(value, str) or not value or not isinstance(uid, int) or not isinstance(mode, int):
        raise SystemExit(2)
    descriptor = os.open(f"/target/{name}", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, value.encode("utf-8"))
        os.fchown(descriptor, uid, uid)
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)
"""

_ED25519_PUBLIC_KEY: Final = r"""import base64
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

seed = base64.urlsafe_b64decode(sys.argv[1] + "=")
public = ed25519.Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw
)
sys.stdout.write(base64.urlsafe_b64encode(public).rstrip(b"=").decode("ascii"))
"""


class DeploymentFailure(RuntimeError):
    """A deployment step failed without reflecting a secret value."""


# --------------------------------------------------------------------------
# Pure helpers (unit tested by deploy/cloud/test_cloud_deployment.py)
# --------------------------------------------------------------------------


def compose_service_block(compose: str, service: str) -> str:
    """Return the raw YAML block of one Compose service."""

    lines = compose.splitlines()
    header = f"  {service}:"
    if header not in lines:
        raise KeyError(service)
    collected: list[str] = []
    for line in lines[lines.index(header) + 1 :]:
        if line.strip() and not line.startswith("    "):
            break
        collected.append(line)
    return "\n".join(collected)


def base64url(value: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def database_url(*, role: str, password: str) -> str:
    """Build the async PostgreSQL URL used by every Control Plane process."""

    if _ROLE_PATTERN.fullmatch(role) is None:
        raise ValueError("database role is invalid")
    quoted = urllib.parse.quote(password, safe="")
    return (
        f"postgresql+asyncpg://{role}:{quoted}"
        f"@{DATABASE_NETWORK_ALIAS}:{DATABASE_PORT}/{database_name()}"
    )


def render_nginx_site(*, host: str, bind_address: str, port: int, template: str) -> str:
    """Render the shared-host server block from validated inputs only."""

    if not isinstance(host, str) or len(host) > 253 or _HOST_PATTERN.fullmatch(host) is None:
        raise ValueError("demo host is invalid")
    if bind_address != _LOOPBACK_ADDRESS:
        raise ValueError("demo bind address is invalid")
    if (
        isinstance(port, bool)
        or not isinstance(port, int)
        or port < _MINIMUM_UNPRIVILEGED_PORT
        or port > _MAXIMUM_PORT
    ):
        raise ValueError("control plane port is invalid")
    rendered = template.replace("__DEMO_HOST__", host).replace(
        "__CONTROL_PLANE_ENDPOINT__", f"{bind_address}:{port}"
    )
    if "__" in rendered:
        raise ValueError("nginx site rendering left an unresolved token")
    return rendered


def container_environment_names() -> tuple[str, ...]:
    """Names of every environment variable handed to a running container."""

    return (
        "AUTOMATION_TOOL_WORKERS",
        "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER_VERSION",
        "AUTOMATION_TOOL_ACCOUNT_OPERATIONS_ACTOR_ID",
        "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID",
        "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY",
        "AUTOMATION_TOOL_ACTION_MINIMUM_INTERVAL_SECONDS",
        "AUTOMATION_TOOL_ACTION_TASK_LIMIT",
        "AUTOMATION_TOOL_ACTION_DAILY_LIMIT",
        "AUTOMATION_TOOL_ACTION_CONSECUTIVE_FAILURE_THRESHOLD",
    )


def load_environment() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(ENVIRONMENT_FILE.read_text(encoding="utf-8")))


def database_name() -> str:
    return cast(str, load_environment()["databaseName"])


# --------------------------------------------------------------------------
# Process helpers
# --------------------------------------------------------------------------


def run(
    arguments: Sequence[str],
    *,
    input_text: str | None = None,
    check: bool = True,
    timeout_seconds: float = 1800,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(arguments),
            input=input_text,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=None if environment is None else dict(environment),
        )
    except subprocess.TimeoutExpired:
        raise DeploymentFailure(f"command timed out: {arguments[0]}") from None
    if check and result.returncode != 0:
        raise DeploymentFailure(
            f"command failed: {' '.join(arguments[:3])} :: {result.stderr.strip()[:400]}"
        )
    return result


def run_binary(
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float = 900,
) -> bytes:
    try:
        result = subprocess.run(
            list(arguments),
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
            env=None if environment is None else dict(environment),
        )
    except subprocess.TimeoutExpired:
        raise DeploymentFailure(f"command timed out: {arguments[0]}") from None
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[:400]
        raise DeploymentFailure(f"command failed: {' '.join(arguments[:3])} :: {detail}")
    return result.stdout


def report(message: str) -> None:
    print(f"[automation-tool-demo] {message}", flush=True)


# --------------------------------------------------------------------------
# Deployment steps
# --------------------------------------------------------------------------


def compose_environment(
    environment: Mapping[str, Any], *, image: str, state: Mapping[str, str]
) -> dict[str, str]:
    resources = environment["resources"]
    limits = environment["actionLimits"]
    return {
        "AUTOMATION_TOOL_DEMO_POSTGRES_IMAGE": str(environment["postgresImage"]),
        "AUTOMATION_TOOL_DEMO_CONTROL_PLANE_IMAGE": image,
        "AUTOMATION_TOOL_DEMO_CONTROL_PLANE_PORT": str(environment["controlPlanePort"]),
        "AUTOMATION_TOOL_DEMO_BIND_ADDRESS": str(environment["bindAddress"]),
        "AUTOMATION_TOOL_DEMO_NETWORK": str(resources["network"]),
        "AUTOMATION_TOOL_DEMO_POSTGRES_DATA_VOLUME": str(resources["postgresDataVolume"]),
        "AUTOMATION_TOOL_DEMO_POSTGRES_SECRETS_VOLUME": str(resources["postgresSecretsVolume"]),
        "AUTOMATION_TOOL_DEMO_RUNTIME_SECRETS_VOLUME": str(resources["runtimeSecretsVolume"]),
        "AUTOMATION_TOOL_DEMO_MIGRATION_SECRETS_VOLUME": str(
            resources["migrationSecretsVolume"]
        ),
        "AUTOMATION_TOOL_DEMO_ACCOUNT_PASSWORD_PEPPER_VERSION": str(
            environment["accountPasswordPepperVersion"]
        ),
        "AUTOMATION_TOOL_DEMO_ACCOUNT_OPERATIONS_ACTOR_ID": state["operationsActorId"],
        "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": state["demoEnvironmentId"],
        "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": state["bootstrapPublicKey"],
        "AUTOMATION_TOOL_DEMO_ACTION_MINIMUM_INTERVAL_SECONDS": str(
            limits["minimumIntervalSeconds"]
        ),
        "AUTOMATION_TOOL_DEMO_ACTION_TASK_LIMIT": str(limits["taskLimit"]),
        "AUTOMATION_TOOL_DEMO_ACTION_DAILY_LIMIT": str(limits["dailyLimit"]),
        "AUTOMATION_TOOL_DEMO_ACTION_CONSECUTIVE_FAILURE_THRESHOLD": str(
            limits["consecutiveFailureThreshold"]
        ),
    }


def _compose_argv(arguments: Sequence[str]) -> list[str]:
    """The single place a Compose command line is built.

    Every Compose call needs the AUTOMATION_TOOL_DEMO_* values or the manifest
    refuses to interpolate; keeping argv construction and environment merging in
    one pair of helpers is what stops a second call site from forgetting them.
    """

    return [
        "docker",
        "compose",
        "--file",
        str(COMPOSE_FILE),
        *arguments,
    ]


def _compose_environment(environment: Mapping[str, str]) -> dict[str, str]:
    merged = os.environ.copy()
    merged.update(environment)
    return merged


def compose(*arguments: str, environment: Mapping[str, str], **keywords: Any) -> Any:
    return run(
        _compose_argv(arguments),
        environment=_compose_environment(environment),
        **keywords,
    )


def compose_binary(
    *arguments: str, environment: Mapping[str, str], timeout_seconds: float = 900
) -> bytes:
    """Run a Compose command whose stdout is binary (pg_dump), same argv path."""

    return run_binary(
        _compose_argv(arguments),
        environment=_compose_environment(environment),
        timeout_seconds=timeout_seconds,
    )


def read_secret_state() -> dict[str, str]:
    if not SECRET_STATE_FILE.is_file():
        return {}
    return cast(dict[str, str], json.loads(SECRET_STATE_FILE.read_text(encoding="utf-8")))


def write_secret_state(state: Mapping[str, str]) -> None:
    STATE_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIRECTORY, 0o700)
    temporary = SECRET_STATE_FILE.with_suffix(".json.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(dict(state), stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, SECRET_STATE_FILE)
    os.chmod(SECRET_STATE_FILE, 0o600)


def ensure_secret_state(image: str) -> dict[str, str]:
    """Generate every missing long-lived secret exactly once and persist it.

    Regenerating the pepper or the fingerprint key would invalidate every
    stored password hash and every issued Session, so a redeploy must reuse
    whatever a previous deployment already created.
    """

    state = read_secret_state()
    created: list[str] = []

    def ensure(name: str, factory: Any) -> None:
        if not state.get(name):
            state[name] = factory()
            created.append(name)

    ensure("postgresSuperuserPassword", lambda: secrets.token_urlsafe(32))
    for role in DATABASE_ROLES:
        ensure(f"{role}Password", lambda: secrets.token_urlsafe(32))
    ensure("accountPasswordPepper", lambda: base64url(secrets.token_bytes(32)))
    ensure("accountFingerprintKey", lambda: base64url(secrets.token_bytes(32)))
    ensure("actionAuthorizationPrivateKey", lambda: base64url(secrets.token_bytes(32)))
    ensure(
        "operationsCapability",
        lambda: CAPABILITY_PREFIX + base64url(secrets.token_bytes(32)),
    )
    ensure("operationsActorId", lambda: str(uuid.uuid4()))
    ensure("demoEnvironmentId", lambda: "demo-xuanbai")
    ensure("bootstrapPrivateSeed", lambda: base64url(secrets.token_bytes(32)))
    if not state.get("bootstrapPublicKey"):
        state["bootstrapPublicKey"] = run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "python",
                image,
                "-c",
                _ED25519_PUBLIC_KEY,
                state["bootstrapPrivateSeed"],
            ],
            timeout_seconds=120,
        ).stdout.strip()
        created.append("bootstrapPublicKey")
    if created:
        write_secret_state(state)
        report(f"generated {len(created)} new secret(s): {', '.join(sorted(created))}")
    else:
        report("reused every persisted secret")
    return state


def operations_capability_digest(capability: str) -> str:
    return base64url(hashlib.sha256(capability.encode("ascii")).digest())


def write_volume(*, image: str, volume: str, values: Mapping[str, tuple[str, int, int]]) -> None:
    payload = {
        name: {"value": value, "uid": uid, "mode": mode}
        for name, (value, uid, mode) in values.items()
    }
    run(
        [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--user",
            "0:0",
            "--mount",
            f"type=volume,source={volume},target=/target",
            "--entrypoint",
            "python",
            image,
            "-c",
            _VOLUME_WRITER,
        ],
        input_text=json.dumps(payload, separators=(",", ":")),
        timeout_seconds=180,
    )


def ensure_docker_resources(environment: Mapping[str, Any]) -> None:
    resources = environment["resources"]
    network = str(resources["network"])
    existing_networks = run(
        ["docker", "network", "ls", "--format", "{{.Name}}"]
    ).stdout.split()
    if network not in existing_networks:
        run(["docker", "network", "create", network])
        report(f"created network {network}")
    existing_volumes = run(["docker", "volume", "ls", "--format", "{{.Name}}"]).stdout.split()
    for key in (
        "postgresDataVolume",
        "postgresSecretsVolume",
        "runtimeSecretsVolume",
        "migrationSecretsVolume",
    ):
        volume = str(resources[key])
        if volume not in existing_volumes:
            run(["docker", "volume", "create", volume])
            report(f"created volume {volume}")


def build_arguments(*, image: str, app_version: str, vcs_ref: str) -> list[str]:
    """Assemble the image build command from validated, non-secret inputs."""

    if _REVISION_PATTERN.fullmatch(vcs_ref) is None:
        raise ValueError("the revision must be a full 40 character commit hash")
    return [
        "docker",
        "build",
        "--file",
        str(BACKEND_ROOT / "Dockerfile"),
        "--tag",
        image,
        "--build-arg",
        f"APP_VERSION={app_version}",
        "--build-arg",
        f"VCS_REF={vcs_ref}",
        str(REPOSITORY_ROOT),
    ]


def build_image(*, image: str, app_version: str, vcs_ref: str) -> None:
    report(f"building {image}")
    run(
        build_arguments(image=image, app_version=app_version, vcs_ref=vcs_ref),
        timeout_seconds=7200,
    )


def image_labels(image: str) -> dict[str, str]:
    payload = json.loads(run(["docker", "image", "inspect", image]).stdout)
    config = payload[0].get("Config") if isinstance(payload, list) and payload else None
    labels = config.get("Labels") if isinstance(config, dict) else None
    return cast(dict[str, str], labels or {})


def wait_for_healthy(container: str, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        payload = json.loads(run(["docker", "container", "inspect", container]).stdout)
        state = payload[0].get("State") if isinstance(payload, list) and payload else None
        if isinstance(state, dict):
            health = state.get("Health")
            if isinstance(health, dict) and health.get("Status") == "healthy":
                return
            if state.get("Running") is False:
                raise DeploymentFailure(f"{container} exited before becoming healthy")
        time.sleep(2)
    raise DeploymentFailure(f"{container} did not become healthy in time")


def compose_container(service: str, environment: Mapping[str, str]) -> str:
    identities = compose(
        "ps", "--quiet", service, environment=environment
    ).stdout.split()
    if len(identities) != 1:
        raise DeploymentFailure(f"{service} does not have exactly one container")
    return identities[0]


def psql(
    *,
    database: str,
    sql: str,
    environment: Mapping[str, str],
    quiet: bool = False,
) -> str:
    arguments = ["exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", database]
    if quiet:
        arguments += ["-tA"]
    return cast(str, compose(*arguments, input_text=sql, environment=environment).stdout)


def provision_database(state: Mapping[str, str], environment: Mapping[str, str]) -> None:
    report("provisioning PostgreSQL roles, database and privileges")
    psql(
        database="postgres",
        sql=(POSTGRESQL_ROOT / "roles.sql").read_text(encoding="utf-8"),
        environment=environment,
    )
    role_statements = "\n".join(
        f"ALTER ROLE {role} PASSWORD '{state[f'{role}Password']}';" for role in DATABASE_ROLES
    )
    psql(database="postgres", sql=role_statements, environment=environment)
    exists = psql(
        database="postgres",
        sql=f"SELECT 1 FROM pg_database WHERE datname = '{database_name()}';",
        environment=environment,
        quiet=True,
    ).strip()
    if not exists:
        psql(
            database="postgres",
            sql=(
                f"CREATE DATABASE {database_name()} OWNER automation_tool_migrator;"
            ),
            environment=environment,
        )
        report(f"created database {database_name()}")
    psql(
        database="postgres",
        sql="\n".join(
            [
                f"REVOKE ALL ON DATABASE {database_name()} FROM PUBLIC;",
                f"GRANT CONNECT ON DATABASE {database_name()} TO "
                "automation_tool_migrator, automation_tool_app, automation_tool_backup;",
            ]
        ),
        environment=environment,
    )
    psql(
        database=database_name(),
        sql=(POSTGRESQL_ROOT / "privileges.sql").read_text(encoding="utf-8"),
        environment=environment,
    )


def backup_database(environment: Mapping[str, str]) -> dict[str, str]:
    """Take a verified pre-migration dump, or record why there is nothing yet."""

    has_schema = psql(
        database=database_name(),
        sql="SELECT to_regclass('public.alembic_version') IS NOT NULL;",
        environment=environment,
        quiet=True,
    ).strip()
    if has_schema != "t":
        report("skipped backup: the schema does not exist yet")
        return {"status": "skipped", "reason": "empty-database"}
    BACKUP_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    dump = compose_binary(
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "-U",
        "postgres",
        "-d",
        database_name(),
        "-Fc",
        environment=environment,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    artifact = BACKUP_DIRECTORY / f"{database_name()}-{stamp}.dump"
    descriptor = os.open(artifact, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(dump)
    digest = hashlib.sha256(dump).hexdigest()
    receipt = {
        "version": "customer-demo-backup-receipt.v1",
        "status": "verified",
        "database": database_name(),
        "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "sha256": digest,
    }
    receipt_file = artifact.with_suffix(".receipt.json")
    receipt_descriptor = os.open(receipt_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(receipt_descriptor, "w", encoding="utf-8") as stream:
        json.dump(receipt, stream, separators=(",", ":"), sort_keys=True)
    report(f"verified backup {artifact.name} ({len(dump)} bytes)")
    return {"status": "verified", "artifact": artifact.name, "sha256": digest}


def install_nginx_site(environment: Mapping[str, Any]) -> None:
    """Add one server block to the shared Nginx, never touching existing ones."""

    host = str(environment["demoHost"])
    certificate = Path(f"/etc/letsencrypt/live/{host}/fullchain.pem")
    if not certificate.exists():
        raise DeploymentFailure("the TLS certificate lineage for the demo host is missing")
    rendered = render_nginx_site(
        host=host,
        bind_address=str(environment["bindAddress"]),
        port=int(environment["controlPlanePort"]),
        template=NGINX_TEMPLATE_FILE.read_text(encoding="utf-8"),
    )
    previous = NGINX_AVAILABLE.read_text(encoding="utf-8") if NGINX_AVAILABLE.is_file() else None
    NGINX_AVAILABLE.write_text(rendered, encoding="utf-8")
    os.chmod(NGINX_AVAILABLE, 0o644)
    if not NGINX_ENABLED.is_symlink():
        NGINX_ENABLED.symlink_to(NGINX_AVAILABLE)
    validation = run(["nginx", "-t"], check=False)
    if validation.returncode != 0:
        if previous is None:
            NGINX_ENABLED.unlink(missing_ok=True)
            NGINX_AVAILABLE.unlink(missing_ok=True)
        else:
            NGINX_AVAILABLE.write_text(previous, encoding="utf-8")
        recovery = run(["nginx", "-t"], check=False)
        raise DeploymentFailure(
            "nginx rejected the demo server block and it was rolled back "
            f"(rollback valid={recovery.returncode == 0}): {validation.stderr.strip()[:400]}"
        )
    run(["systemctl", "reload", "nginx"])
    report(f"installed and reloaded the Nginx server block for {host}")


def loopback_request(
    *, environment: Mapping[str, Any], path: str, method: str = "GET", payload: Any = None,
    token: str | None = None,
) -> tuple[int, Any]:
    url = f"http://{environment['bindAddress']}:{environment['controlPlanePort']}{path}"
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("accept", "application/json")
    if body is not None:
        request.add_header("content-type", "application/json")
    if token is not None:
        request.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            encoded = response.read(256 * 1024)
            return response.status, json.loads(encoded) if encoded else None
    except urllib.error.HTTPError as error:
        encoded = error.read(256 * 1024)
        try:
            return error.code, json.loads(encoded) if encoded else None
        except json.JSONDecodeError:
            return error.code, None
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
        raise DeploymentFailure(f"loopback request to {path} failed: {error}") from None


def ensure_demo_account(
    state: dict[str, str], environment: Mapping[str, Any], compose_env: Mapping[str, str]
) -> dict[str, str]:
    login_name = state.get("demoLoginName") or DEMO_LOGIN_NAME
    password = state.get("demoPassword")
    if password:
        status, _ = loopback_request(
            environment=environment,
            path="/api/v1/account-sessions",
            method="POST",
            payload={"loginName": login_name, "password": password},
        )
        if status == 201:
            report(f"reused the existing Demo account {login_name}")
            return {"loginName": login_name, "status": "reused"}
        raise DeploymentFailure(
            "the persisted Demo account credentials no longer authenticate "
            f"(status={status}); resolve manually before continuing"
        )
    password = secrets.token_urlsafe(24)
    result = compose(
        "--profile",
        "operations",
        "run",
        "--rm",
        "-T",
        "account-operations",
        "create",
        "--login-name",
        login_name,
        "--request-id",
        str(uuid.uuid4()),
        input_text=json.dumps(
            {"capability": state["operationsCapability"], "password": password}
        ),
        environment=compose_env,
        check=False,
        timeout_seconds=300,
    )
    if result.returncode != 0:
        raise DeploymentFailure(
            f"the account operations job failed: {result.stderr.strip()[:400]}"
        )
    created = json.loads(result.stdout.strip().splitlines()[-1])
    state["demoLoginName"] = login_name
    state["demoPassword"] = password
    state["demoUserId"] = created["userId"]
    write_secret_state(state)
    report(f"created the Demo account {login_name} ({created['status']})")
    return {"loginName": login_name, "status": "created", "userId": created["userId"]}


def write_deploy_environment(values: Mapping[str, str]) -> None:
    STATE_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    text = "".join(f"{name}={value}\n" for name, value in sorted(values.items()))
    descriptor = os.open(DEPLOY_ENVIRONMENT_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)


def require_state_directory() -> None:
    STATE_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIRECTORY, 0o700)
    metadata = STATE_DIRECTORY.stat()
    if stat.S_IMODE(metadata.st_mode) != 0o700 or metadata.st_uid != 0:
        raise DeploymentFailure("the deployment state directory is not root-private")


def deploy(arguments: argparse.Namespace) -> dict[str, Any]:
    if _REVISION_PATTERN.fullmatch(arguments.vcs_ref) is None:
        raise DeploymentFailure("--vcs-ref must be a full 40 character commit hash")
    if os.geteuid() != 0:
        raise DeploymentFailure("this deployment must run as root on the cloud host")
    require_state_directory()
    environment = load_environment()
    resources = environment["resources"]
    image = f"{resources['imageRepository']}:{arguments.vcs_ref[:12]}"
    app_version = arguments.app_version

    if not arguments.skip_build:
        build_image(image=image, app_version=app_version, vcs_ref=arguments.vcs_ref)
    labels = image_labels(image)
    if (
        labels.get("org.opencontainers.image.version") != app_version
        or labels.get("org.opencontainers.image.revision") != arguments.vcs_ref
    ):
        raise DeploymentFailure("the built image does not carry the expected OCI identity")

    ensure_docker_resources(environment)
    state = ensure_secret_state(image)
    compose_env = compose_environment(environment, image=image, state=state)
    write_deploy_environment(compose_env)

    write_volume(
        image=image,
        volume=str(resources["postgresSecretsVolume"]),
        values={
            "postgres-password": (
                state["postgresSuperuserPassword"],
                POSTGRES_UID,
                SECRET_FILE_MODE,
            )
        },
    )
    write_volume(
        image=image,
        volume=str(resources["migrationSecretsVolume"]),
        values={
            "database-url": (
                database_url(
                    role="automation_tool_migrator",
                    password=state["automation_tool_migratorPassword"],
                ),
                RUNTIME_UID,
                SECRET_FILE_MODE,
            )
        },
    )
    write_volume(
        image=image,
        volume=str(resources["runtimeSecretsVolume"]),
        values={
            "database-url": (
                database_url(
                    role="automation_tool_app",
                    password=state["automation_tool_appPassword"],
                ),
                RUNTIME_UID,
                SECRET_FILE_MODE,
            ),
            "account-password-pepper": (
                state["accountPasswordPepper"],
                RUNTIME_UID,
                SECRET_FILE_MODE,
            ),
            "account-fingerprint-key": (
                state["accountFingerprintKey"],
                RUNTIME_UID,
                SECRET_FILE_MODE,
            ),
            "account-operations-capability-digest": (
                operations_capability_digest(state["operationsCapability"]),
                RUNTIME_UID,
                SECRET_FILE_MODE,
            ),
            "action-authorization-private-key": (
                state["actionAuthorizationPrivateKey"],
                RUNTIME_UID,
                SECRET_FILE_MODE,
            ),
        },
    )
    report("wrote every runtime secret into its read-only volume")

    compose("up", "--detach", "postgres", environment=compose_env)
    wait_for_healthy(compose_container("postgres", compose_env), timeout_seconds=180)
    provision_database(state, compose_env)
    backup = backup_database(compose_env)

    report("running the one-shot Alembic migration")
    compose(
        "--profile", "migration", "run", "--rm", "migration", environment=compose_env,
        timeout_seconds=900,
    )
    revision = psql(
        database=database_name(),
        sql="SELECT version_num FROM alembic_version;",
        environment=compose_env,
        quiet=True,
    ).strip()

    compose("up", "--detach", "--no-deps", "control-plane", environment=compose_env)
    control_plane = compose_container("control-plane", compose_env)
    wait_for_healthy(control_plane, timeout_seconds=180)

    install_nginx_site(environment)

    health_status, health = loopback_request(environment=environment, path="/api/v1/health")
    version_status, version = loopback_request(environment=environment, path="/api/v1/version")
    if health_status != 200 or version_status != 200:
        raise DeploymentFailure("the deployed Control Plane failed its loopback verification")
    account = ensure_demo_account(state, environment, compose_env)

    return {
        "alembicRevision": revision,
        "backup": backup,
        "controlPlaneContainer": control_plane,
        "demoAccount": account,
        "health": health,
        "image": image,
        "loopbackPort": environment["controlPlanePort"],
        "version": version,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Deploy the customer Demo Control Plane")
    result.add_argument("--vcs-ref", required=True)
    result.add_argument("--app-version", default="0.1.0")
    result.add_argument("--skip-build", action="store_true")
    return result


def main(arguments: Sequence[str] | None = None) -> None:
    try:
        summary = deploy(parser().parse_args(arguments))
    except DeploymentFailure as error:
        print(f"[automation-tool-demo] FAILED: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

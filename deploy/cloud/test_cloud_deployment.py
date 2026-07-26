#!/usr/bin/env python3
"""T18-cloud-deploy: contract tests for the single real cloud Demo deployment.

These tests guard the properties that would silently break the customer Demo:
resource isolation (every Docker object carries the project prefix), exposure
(PostgreSQL never reaches the public interface, the Control Plane only reaches
loopback), restart survivability, migration/runtime separation, and the edge
Nginx boundary that replaces the container ingress on this host.

Run with:

    python3 -m unittest discover --start-directory deploy/cloud --top-level-directory deploy/cloud
"""

from __future__ import annotations

import inspect
import io
import json
import re
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CLOUD_ROOT = REPOSITORY_ROOT / "deploy" / "cloud"

sys.path.insert(0, str(CLOUD_ROOT))

import deploy_cloud_demo  # noqa: E402


def _load_environment() -> dict[str, object]:
    return json.loads((CLOUD_ROOT / "demo-environment.json").read_text(encoding="utf-8"))


def _load_compose() -> str:
    return (CLOUD_ROOT / "compose.yaml").read_text(encoding="utf-8")


def _load_nginx_template() -> str:
    return (CLOUD_ROOT / "nginx-site.conf.template").read_text(encoding="utf-8")


class DemoEnvironmentContract(unittest.TestCase):
    """The single non-secret parameter file both the script and Nginx read."""

    def setUp(self) -> None:
        self.environment = _load_environment()

    def test_declares_the_customer_demo_host_and_loopback_only_binding(self) -> None:
        self.assertEqual(self.environment["version"], "automation-tool-cloud-demo.v1")
        self.assertEqual(self.environment["demoHost"], "at.xuanbai.tech")
        self.assertEqual(self.environment["bindAddress"], "127.0.0.1")

    def test_control_plane_port_is_a_project_owned_unprivileged_port(self) -> None:
        port = self.environment["controlPlanePort"]
        self.assertIsInstance(port, int)
        self.assertGreaterEqual(port, 1024)
        self.assertLessEqual(port, 65535)
        # Ports already owned by unrelated business on the shared host.
        self.assertNotIn(port, {80, 443, 22, 3000, 3100, 5432})

    def test_every_docker_resource_name_carries_the_project_prefix(self) -> None:
        resources = self.environment["resources"]
        self.assertIsInstance(resources, dict)
        self.assertEqual(resources["composeProject"], "automation-tool-demo")
        for key, value in resources.items():
            self.assertIsInstance(value, str, key)
            self.assertTrue(
                value.startswith("automation-tool-demo"),
                f"{key}={value} is not namespaced to this project",
            )

    def test_postgres_image_is_pinned_to_the_same_tag_as_local_development(self) -> None:
        self.assertEqual(self.environment["postgresImage"], "postgres:18.4-bookworm")

    def test_contains_no_secret_material(self) -> None:
        # Resource names legitimately mention secret volumes and the public
        # pepper rotation version. What must never be committed is a *value*
        # that could be key material, so every string stays short and readable.
        def walk(node: object) -> None:
            if isinstance(node, dict):
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
            elif isinstance(node, str):
                self.assertLessEqual(len(node), 64, node)
                self.assertIsNone(re.fullmatch(r"[A-Za-z0-9_-]{43}", node), node)

        walk(self.environment)


class ImageIdentityContract(unittest.TestCase):
    """The deployed image must be traceable to one commit and one version.

    The deployment refuses to start anything whose OCI labels disagree with the
    revision it was asked to deploy, so the build arguments carrying those
    labels are part of the contract rather than an incidental detail.
    """

    def test_build_arguments_stamp_the_commit_and_version_into_the_image(self) -> None:
        arguments = deploy_cloud_demo.build_arguments(
            image="automation-tool-demo-control-plane:test",
            app_version="0.1.0",
            vcs_ref="0" * 40,
        )
        self.assertIn("APP_VERSION=0.1.0", arguments)
        self.assertIn(f"VCS_REF={'0' * 40}", arguments)
        self.assertIn(str(REPOSITORY_ROOT / "backend" / "Dockerfile"), arguments)

    def test_the_lock_file_pins_artifact_digests(self) -> None:
        # Every artifact is pinned by sha256, which is what makes rewriting the
        # download host safe: content that does not match the lock is rejected.
        lock = (REPOSITORY_ROOT / "backend" / "uv.lock").read_text(encoding="utf-8")
        self.assertGreater(lock.count('hash = "sha256:'), 100)
        dockerfile = (REPOSITORY_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "uv sync --locked --no-dev --no-group executor --no-editable",
            dockerfile,
        )


class PackageDownloadHost(unittest.TestCase):
    """The build downloads from a path-identical mirror, on one single path.

    `files.pythonhosted.org` serves this host at ~22 KB/s, which turns a build
    into a 40+ minute operation. The Tsinghua mirror exposes the byte-identical
    artifacts under the byte-identical `/packages/<a>/<b>/<digest>/<file>` path,
    so rewriting only the hostname keeps every pinned URL resolvable.
    """

    MIRROR_HOST = "pypi.tuna.tsinghua.edu.cn"
    CANONICAL_HOST = "files.pythonhosted.org"

    def setUp(self) -> None:
        self.dockerfile = (REPOSITORY_ROOT / "backend" / "Dockerfile").read_text(
            encoding="utf-8"
        )

    def test_the_build_rewrites_only_the_download_host(self) -> None:
        self.assertIn(
            f"sed -i \"s|https://{self.CANONICAL_HOST.replace('.', chr(92) + '.')}/"
            f"|https://{self.MIRROR_HOST}/|g\" uv.lock",
            self.dockerfile,
        )
        # The rewrite must happen in the same layer as, and before, the sync.
        rewrite = self.dockerfile.index(self.MIRROR_HOST)
        sync = self.dockerfile.index("uv sync --locked")
        self.assertLess(rewrite, sync)

    def test_integrity_still_comes_from_the_lock_not_from_the_host(self) -> None:
        # `--locked` plus the pinned sha256 digests are what verify the bytes.
        # Losing either of them would turn the mirror into a trust decision.
        self.assertIn(
            "uv sync --locked --no-dev --no-group executor --no-editable",
            self.dockerfile,
        )
        self.assertNotIn("--no-verify", self.dockerfile)
        self.assertNotIn("--allow-insecure-host", self.dockerfile)
        self.assertNotIn("trusted-host", self.dockerfile)

    def test_the_repository_lock_stays_the_canonical_source_of_truth(self) -> None:
        # Only the copy inside the image is rewritten; the committed lock keeps
        # pointing at the canonical host so it stays diffable against upstream.
        lock = (REPOSITORY_ROOT / "backend" / "uv.lock").read_text(encoding="utf-8")
        self.assertIn(self.CANONICAL_HOST, lock)
        self.assertNotIn(self.MIRROR_HOST, lock)

    def test_there_is_exactly_one_build_path_for_every_builder(self) -> None:
        # A per-environment switch here is precisely the "test build resolves
        # dependencies differently from the shipped build" shape that this
        # repository already had one production incident from, so the host is a
        # committed constant rather than a build argument or an env override.
        for switch in ("ARG PYTHON_INDEX_URL", "ARG PYPI", "UV_DEFAULT_INDEX", "UV_INDEX_URL"):
            self.assertNotIn(switch, self.dockerfile, switch)
        arguments = deploy_cloud_demo.build_arguments(
            image="automation-tool-demo-control-plane:test",
            app_version="0.1.0",
            vcs_ref="0" * 40,
        )
        self.assertNotIn(self.MIRROR_HOST, " ".join(arguments))
        self.assertEqual(
            [value for value in arguments if value.startswith("PYTHON_")],
            [],
            "the deployment must not inject a host that differs from other builders",
        )

    def test_rejects_a_revision_that_is_not_a_full_commit_hash(self) -> None:
        for revision in ("0" * 39, "z" * 40, "", "HEAD"):
            with self.assertRaises(ValueError, msg=revision):
                deploy_cloud_demo.build_arguments(
                    image="automation-tool-demo-control-plane:test",
                    app_version="0.1.0",
                    vcs_ref=revision,
                )


class ComposeInvocationContract(unittest.TestCase):
    """Every Compose call must carry the interpolation environment, and every
    failure must say why.

    Both assertions come from one real defect. `backup_database` built its own
    `docker compose ... pg_dump` argv without the AUTOMATION_TOOL_DEMO_* values,
    so Compose refused to interpolate the manifest and exited 1. That path only
    runs once a schema exists, so the first deployment passed and the *second*
    one failed -- and the failure said only "command failed: docker compose
    --file", because the binary runner dropped stderr on the floor.
    """

    def test_compose_argv_is_built_in_exactly_one_place(self) -> None:
        source = (CLOUD_ROOT / "deploy_cloud_demo.py").read_text(encoding="utf-8")
        self.assertEqual(
            source.count('"docker",\n        "compose",'),
            1,
            "a second hand-built Compose argv is how the environment gets dropped",
        )

    def test_binary_compose_helper_requires_the_environment(self) -> None:
        signature = inspect.signature(deploy_cloud_demo.compose_binary)
        self.assertIn("environment", signature.parameters)
        self.assertIs(
            signature.parameters["environment"].default,
            inspect.Parameter.empty,
            "the environment must not be optional, or it will be forgotten again",
        )

    def test_binary_command_failures_report_the_underlying_stderr(self) -> None:
        with self.assertRaises(deploy_cloud_demo.DeploymentFailure) as raised:
            deploy_cloud_demo.run_binary(
                ["sh", "-c", "echo interpolation-exploded >&2; exit 1"]
            )
        self.assertIn("interpolation-exploded", str(raised.exception))

    def test_text_command_failures_also_report_the_underlying_stderr(self) -> None:
        with self.assertRaises(deploy_cloud_demo.DeploymentFailure) as raised:
            deploy_cloud_demo.run(["sh", "-c", "echo text-path-exploded >&2; exit 1"])
        self.assertIn("text-path-exploded", str(raised.exception))


class SourceTransferContract(unittest.TestCase):
    """What gets deployed must be exactly one commit's tree, and nothing else.

    Two real defects motivate every assertion here. Shipping the working tree
    with macOS `tar` emitted 289 AppleDouble `._*` sidecar files; Alembic globs
    `versions/*.py`, matched `._0001_baseline.py`, and died on its null bytes.
    Separately, labelling an image with `git rev-parse HEAD` while shipping the
    working tree stamps a revision whose tree is not what was actually built.
    `git archive <commit>` removes both: it emits tracked content only, and the
    bytes shipped are by construction the bytes of the commit in the label.
    """

    def setUp(self) -> None:
        self.script = (CLOUD_ROOT / "deploy.sh").read_text(encoding="utf-8")

    def test_ships_a_commit_tree_rather_than_the_working_tree(self) -> None:
        self.assertIn("git archive", self.script)
        self.assertNotIn("tar -cz", self.script)
        self.assertNotIn("--exclude", self.script)

    def test_refuses_to_deploy_when_the_shipped_paths_are_uncommitted(self) -> None:
        # Scoped to the deployable paths on purpose: other work lines keep
        # unrelated files dirty, and blocking on those would be noise.
        self.assertIn("git status --porcelain --", self.script)
        for path in ("backend", "deploy/cloud", "deploy/postgresql", "deploy/secrets"):
            self.assertIn(path, self.script, path)

    def test_labels_the_image_with_the_commit_it_actually_ships(self) -> None:
        self.assertIn("--vcs-ref", self.script)
        self.assertIn('VCS_REF="$(git rev-parse --verify', self.script)

    def test_git_archive_emits_no_apple_double_sidecar_files(self) -> None:
        # The executable proof that the transfer mechanism fixes the corruption
        # class: archive a directory that previously carried the junk.
        listing = subprocess.run(
            ["git", "archive", "HEAD", "backend/migrations"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            check=True,
        ).stdout
        names = subprocess.run(
            ["tar", "-tf", "-"], input=listing, capture_output=True, check=True
        ).stdout.decode("utf-8")
        self.assertIn("backend/migrations/versions/", names)
        offenders = [name for name in names.splitlines() if Path(name).name.startswith("._")]
        self.assertEqual(offenders, [], "AppleDouble sidecars would break Alembic")

    def test_every_shipped_migration_is_valid_python_source(self) -> None:
        # Null bytes are only one way a transfer can corrupt a migration; parse
        # what would actually be shipped rather than trusting the file list.
        archive = subprocess.run(
            ["git", "archive", "HEAD", "backend/migrations"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            check=True,
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            sources = [
                member
                for member in bundle.getmembers()
                if member.isfile() and member.name.endswith(".py")
            ]
            self.assertGreater(len(sources), 5)
            for member in sources:
                stream = bundle.extractfile(member)
                assert stream is not None
                compile(stream.read(), member.name, "exec")


class ComposeManifestContract(unittest.TestCase):
    """Exposure, isolation and restart properties of the deployed containers."""

    def setUp(self) -> None:
        self.compose = _load_compose()
        self.environment = _load_environment()

    def test_uses_the_explicit_project_name_instead_of_the_directory_default(self) -> None:
        self.assertIn("name: automation-tool-demo\n", self.compose)

    def test_postgres_publishes_no_host_port_at_all(self) -> None:
        postgres = deploy_cloud_demo.compose_service_block(self.compose, "postgres")
        self.assertNotIn("ports:", postgres)
        self.assertIn("restart: unless-stopped", postgres)

    def test_control_plane_publishes_only_the_loopback_deployment_port(self) -> None:
        control_plane = deploy_cloud_demo.compose_service_block(self.compose, "control-plane")
        self.assertIn("host_ip: ${AUTOMATION_TOOL_DEMO_BIND_ADDRESS", control_plane)
        self.assertIn("published: ${AUTOMATION_TOOL_DEMO_CONTROL_PLANE_PORT", control_plane)
        self.assertIn("target: 8000", control_plane)
        self.assertIn("restart: unless-stopped", control_plane)

    def test_control_plane_keeps_the_hardened_container_boundary(self) -> None:
        control_plane = deploy_cloud_demo.compose_service_block(self.compose, "control-plane")
        for directive in (
            "user: 65532:65532",
            "read_only: true",
            "no-new-privileges=true",
            "healthcheck:",
            "stop_grace_period:",
        ):
            self.assertIn(directive, control_plane, directive)
        self.assertIn("- ALL", control_plane)

    def test_migration_is_a_one_shot_profile_that_never_restarts(self) -> None:
        migration = deploy_cloud_demo.compose_service_block(self.compose, "migration")
        self.assertIn("profiles:", migration)
        self.assertIn("- migration", migration)
        self.assertIn('restart: "no"', migration)
        self.assertIn("entrypoint:", migration)
        self.assertIn("upgrade", migration)

    def test_account_operations_is_a_separate_one_shot_profile(self) -> None:
        operations = deploy_cloud_demo.compose_service_block(self.compose, "account-operations")
        self.assertIn("- operations", operations)
        self.assertIn('restart: "no"', operations)
        self.assertIn("automation-tool-account-operations", operations)

    def test_secret_volumes_are_mounted_read_only_under_run_secrets(self) -> None:
        for service in ("control-plane", "migration", "account-operations"):
            block = deploy_cloud_demo.compose_service_block(self.compose, service)
            self.assertIn("target: /run/secrets", block, service)
            self.assertIn("read_only: true", block, service)

    def test_all_volumes_and_networks_are_externally_provisioned(self) -> None:
        # One network plus four volumes, each provisioned by the deployment
        # script rather than by a Compose default name that could collide with
        # another project on this shared host.
        self.assertEqual(self.compose.count("external: true"), 5)
        for variable in (
            "AUTOMATION_TOOL_DEMO_NETWORK",
            "AUTOMATION_TOOL_DEMO_POSTGRES_DATA_VOLUME",
            "AUTOMATION_TOOL_DEMO_POSTGRES_SECRETS_VOLUME",
            "AUTOMATION_TOOL_DEMO_RUNTIME_SECRETS_VOLUME",
            "AUTOMATION_TOOL_DEMO_MIGRATION_SECRETS_VOLUME",
        ):
            self.assertIn(f"name: ${{{variable}", self.compose, variable)

    def test_compose_variables_resolve_to_the_pinned_project_resource_names(self) -> None:
        resolved = deploy_cloud_demo.compose_environment(
            self.environment,
            image="automation-tool-demo-control-plane:test",
            state={
                "operationsActorId": "00000000-0000-4000-8000-000000000000",
                "demoEnvironmentId": "demo-xuanbai",
                "bootstrapPublicKey": "0" * 43,
            },
        )
        resources = self.environment["resources"]
        self.assertEqual(resolved["AUTOMATION_TOOL_DEMO_NETWORK"], resources["network"])
        self.assertEqual(
            resolved["AUTOMATION_TOOL_DEMO_POSTGRES_DATA_VOLUME"],
            resources["postgresDataVolume"],
        )
        self.assertEqual(
            resolved["AUTOMATION_TOOL_DEMO_RUNTIME_SECRETS_VOLUME"],
            resources["runtimeSecretsVolume"],
        )
        self.assertEqual(
            resolved["AUTOMATION_TOOL_DEMO_CONTROL_PLANE_PORT"],
            str(self.environment["controlPlanePort"]),
        )
        self.assertEqual(resolved["AUTOMATION_TOOL_DEMO_BIND_ADDRESS"], "127.0.0.1")

    def test_declares_no_inline_secret_values(self) -> None:
        self.assertNotIn("POSTGRES_PASSWORD:", self.compose)
        self.assertIn("POSTGRES_PASSWORD_FILE: /run/secrets/postgres-password", self.compose)
        self.assertNotIn("AUTOMATION_TOOL_DATABASE_URL", self.compose)


class NginxEdgeContract(unittest.TestCase):
    """The host Nginx server block is the only public edge for this domain."""

    def setUp(self) -> None:
        self.template = _load_nginx_template()

    def test_is_rendered_from_tokens_rather_than_hard_coded_targets(self) -> None:
        self.assertIn("__DEMO_HOST__", self.template)
        self.assertIn("__CONTROL_PLANE_ENDPOINT__", self.template)

    def test_never_claims_the_default_server_of_the_shared_host(self) -> None:
        self.assertNotIn("default_server", self.template)

    def test_terminates_tls_with_the_existing_certbot_lineage(self) -> None:
        self.assertIn(
            "ssl_certificate /etc/letsencrypt/live/__DEMO_HOST__/fullchain.pem;",
            self.template,
        )
        self.assertIn(
            "ssl_certificate_key /etc/letsencrypt/live/__DEMO_HOST__/privkey.pem;",
            self.template,
        )
        self.assertIn("ssl_protocols TLSv1.2 TLSv1.3;", self.template)

    def test_keeps_the_ingress_request_bounds_and_security_headers(self) -> None:
        for directive in (
            "client_max_body_size 1m;",
            "client_header_timeout 10s;",
            "client_body_timeout 10s;",
            "proxy_connect_timeout 5s;",
            "proxy_read_timeout 60s;",
            "proxy_buffering off;",
            "proxy_http_version 1.1;",
            "limit_req_status 429;",
            "limit_conn_status 429;",
            "server_tokens off;",
            "Strict-Transport-Security",
            "X-Content-Type-Options",
            "Content-Security-Policy",
            "Referrer-Policy",
        ):
            self.assertIn(directive, self.template, directive)

    def test_namespaces_every_shared_http_symbol_to_this_project(self) -> None:
        # A shared Nginx already serves other business; duplicate zone or map
        # names would make `nginx -t` fail and take those sites down with us.
        for symbol in re.findall(r"zone=([A-Za-z0-9_]+)", self.template):
            self.assertTrue(symbol.startswith("automation_tool_demo"), symbol)
        for symbol in re.findall(r"^\s*map\s+\$\S+\s+\$([A-Za-z0-9_]+)", self.template, re.M):
            self.assertTrue(symbol.startswith("automation_tool_demo"), symbol)
        self.assertIn("shared:automation_tool_demo", self.template)

    def test_proxies_to_the_loopback_control_plane_endpoint(self) -> None:
        self.assertIn("proxy_pass http://__CONTROL_PLANE_ENDPOINT__;", self.template)
        self.assertNotIn("proxy_pass $", self.template)

    def test_forwards_the_caller_request_id_instead_of_replacing_it(self) -> None:
        # The desktop App correlates every reply by comparing the echoed
        # x-request-id against the one it sent, and treats a mismatch as a
        # protocol violation. An edge that overwrites a supplied header makes
        # every App call fail after the Control Plane already accepted it.
        self.assertNotIn("proxy_set_header X-Request-ID $request_id;", self.template)
        self.assertIn(
            "proxy_set_header X-Request-ID $automation_tool_demo_request_id;",
            self.template,
        )
        self.assertRegex(
            self.template,
            r"map\s+\$http_x_request_id\s+\$automation_tool_demo_request_id\s*\{",
        )


class RenderedNginxSite(unittest.TestCase):
    """Rendering must be closed over validated inputs, not free-form strings."""

    def test_renders_every_token_for_a_valid_host_and_port(self) -> None:
        rendered = deploy_cloud_demo.render_nginx_site(
            host="at.xuanbai.tech",
            bind_address="127.0.0.1",
            port=18800,
            template=_load_nginx_template(),
        )
        self.assertNotIn("__", rendered)
        self.assertIn("server_name at.xuanbai.tech;", rendered)
        self.assertIn("proxy_pass http://127.0.0.1:18800;", rendered)
        self.assertIn("/etc/letsencrypt/live/at.xuanbai.tech/fullchain.pem", rendered)

    def test_rejects_hosts_ports_and_addresses_that_could_inject_directives(self) -> None:
        template = _load_nginx_template()
        for host in ("at.xuanbai.tech;", "at.xuanbai.tech localhost", "", "AT.xuanbai.tech", "*"):
            with self.assertRaises(ValueError, msg=host):
                deploy_cloud_demo.render_nginx_site(
                    host=host, bind_address="127.0.0.1", port=18800, template=template
                )
        for port in (0, -1, 65536, 443):
            with self.assertRaises(ValueError, msg=str(port)):
                deploy_cloud_demo.render_nginx_site(
                    host="at.xuanbai.tech",
                    bind_address="127.0.0.1",
                    port=port,
                    template=template,
                )
        for address in ("0.0.0.0", "127.0.0.1;", ""):
            with self.assertRaises(ValueError, msg=address):
                deploy_cloud_demo.render_nginx_site(
                    host="at.xuanbai.tech",
                    bind_address=address,
                    port=18800,
                    template=template,
                )


class DatabaseUrlComposition(unittest.TestCase):
    """Generated credentials must survive URL composition without corruption."""

    def test_builds_an_async_postgres_url_for_the_private_network_alias(self) -> None:
        url = deploy_cloud_demo.database_url(role="automation_tool_app", password="plain")
        self.assertEqual(
            url,
            "postgresql+asyncpg://automation_tool_app:plain@postgres:5432/automation_tool_demo",
        )

    def test_percent_encodes_reserved_characters_in_generated_passwords(self) -> None:
        url = deploy_cloud_demo.database_url(role="automation_tool_app", password="a/b:c@d?e#f")
        self.assertIn("a%2Fb%3Ac%40d%3Fe%23f", url)
        self.assertTrue(url.endswith("@postgres:5432/automation_tool_demo"))

    def test_rejects_role_names_that_are_not_fixed_identifiers(self) -> None:
        for role in ("automation_tool_app; DROP", "", "postgres user"):
            with self.assertRaises(ValueError, msg=role):
                deploy_cloud_demo.database_url(role=role, password="plain")


class SecretDeliveryContract(unittest.TestCase):
    """Secret file names must stay identical to the frozen C10-05 inventory."""

    def test_runtime_secret_file_names_match_the_inventory(self) -> None:
        inventory = json.loads(
            (REPOSITORY_ROOT / "deploy" / "secrets" / "inventory.v1.json").read_text(
                encoding="utf-8"
            )
        )
        expected = {secret["fileName"] for secret in inventory["secrets"]}
        self.assertEqual(set(deploy_cloud_demo.RUNTIME_SECRET_FILE_NAMES), expected)

    def test_secret_files_are_written_owner_read_only_for_the_runtime_user(self) -> None:
        self.assertEqual(deploy_cloud_demo.RUNTIME_UID, 65532)
        self.assertEqual(deploy_cloud_demo.SECRET_FILE_MODE, 0o400)

    def test_secret_values_never_travel_through_argv_or_container_environment(self) -> None:
        source = (CLOUD_ROOT / "deploy_cloud_demo.py").read_text(encoding="utf-8")
        compose = _load_compose()
        # The env-mode secret delivery names must appear nowhere: the deployment
        # is pinned to the file-only mode baked into the Control Plane image.
        for name in (
            "AUTOMATION_TOOL_DATABASE_URL",
            "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER",
            "AUTOMATION_TOOL_ACCOUNT_FINGERPRINT_KEY",
            "AUTOMATION_TOOL_ACCOUNT_OPERATIONS_CAPABILITY_DIGEST",
            "AUTOMATION_TOOL_ACTION_AUTHORIZATION_PRIVATE_KEY",
        ):
            self.assertNotIn(f"{name}=", source, name)
            self.assertNotIn(f"{name}:", compose, name)
        # The container-visible environment carries only non-secret policy values.
        for name in deploy_cloud_demo.container_environment_names():
            self.assertNotIn("PASSWORD", name.replace("PASSWORD_PEPPER_VERSION", ""))
            self.assertNotIn("SECRET", name)


if __name__ == "__main__":
    unittest.main()

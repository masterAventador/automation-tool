import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPOSITORY_ROOT / "compose.yaml"
ENV_EXAMPLE = REPOSITORY_ROOT / ".env.example"

REQUIRED_ENV = {
    "AUTOMATION_TOOL_DEV_DB_USER": "automation_tool_dev",
    "AUTOMATION_TOOL_DEV_DB_PASSWORD": "f104-dev-contract-password",
    "AUTOMATION_TOOL_DEV_DB_NAME": "automation_tool_dev",
    "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_test",
    "AUTOMATION_TOOL_TEST_DB_PASSWORD": "f104-test-contract-password",
    "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_test",
}


def compose_command() -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        os.devnull,
        "--file",
        str(COMPOSE_FILE),
        "config",
        "--format",
        "json",
    ]


def render_compose() -> dict[str, Any]:
    environment = os.environ.copy()
    environment.update(REQUIRED_ENV)
    result = subprocess.run(
        compose_command(),
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )
    return cast(dict[str, Any], json.loads(result.stdout))


def test_compose_and_non_secret_environment_template_exist() -> None:
    assert COMPOSE_FILE.is_file()
    assert ENV_EXAMPLE.is_file()


def test_compose_requires_all_database_credentials_without_defaults() -> None:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }

    result = subprocess.run(
        compose_command(),
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert result.returncode != 0
    assert "AUTOMATION_TOOL_" in result.stderr


def test_development_and_test_databases_are_isolated_and_healthy() -> None:
    rendered = render_compose()
    services = rendered["services"]

    assert set(services) == {"postgres-dev", "postgres-test"}
    assert services["postgres-dev"]["image"] == "postgres:18.4-bookworm"
    assert services["postgres-test"]["image"] == "postgres:18.4-bookworm"
    assert services["postgres-dev"]["environment"]["POSTGRES_DB"] == "automation_tool_dev"
    assert services["postgres-test"]["environment"]["POSTGRES_DB"] == "automation_tool_test"
    assert (
        services["postgres-dev"]["environment"]["POSTGRES_USER"]
        != services["postgres-test"]["environment"]["POSTGRES_USER"]
    )
    assert (
        services["postgres-dev"]["environment"]["POSTGRES_PASSWORD"]
        != services["postgres-test"]["environment"]["POSTGRES_PASSWORD"]
    )
    assert "healthcheck" in services["postgres-dev"]
    assert "healthcheck" in services["postgres-test"]


def test_database_ports_are_loopback_only_and_storage_is_not_shared() -> None:
    services = render_compose()["services"]

    for service in services.values():
        assert len(service["ports"]) == 1
        assert service["ports"][0]["host_ip"] == "127.0.0.1"
        assert service["ports"][0]["target"] == 5432

    dev_storage = services["postgres-dev"]["volumes"]
    test_storage = services["postgres-test"]["tmpfs"]
    assert dev_storage[0]["type"] == "volume"
    assert dev_storage[0]["target"] == "/var/lib/postgresql"
    assert "/var/lib/postgresql" in test_storage


def test_repository_contains_no_weak_database_password_or_trust_authentication() -> None:
    compose_text = COMPOSE_FILE.read_text()
    example_text = ENV_EXAMPLE.read_text()

    assert "POSTGRES_HOST_AUTH_METHOD" not in compose_text
    assert "POSTGRES_PASSWORD:-" not in compose_text
    assert "AUTOMATION_TOOL_DEV_DB_PASSWORD=\n" in example_text
    assert "AUTOMATION_TOOL_TEST_DB_PASSWORD=\n" in example_text
    for weak_value in ("password", "postgres", "changeme", "secret"):
        assert f"={weak_value}\n" not in example_text.lower()

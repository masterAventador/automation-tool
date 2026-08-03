import json
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
OPENAPI = REPOSITORY_ROOT / "contracts/openapi/control-plane.v1.json"
REVOCATION_CONTRACT = REPOSITORY_ROOT / "contracts/security/customer-demo-revocation-v1.json"
MUTATING_METHODS = frozenset({"post", "put", "delete"})


def mutating_operations(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for path, item in cast(dict[str, dict[str, Any]], openapi["paths"]).items():
        for method, operation in item.items():
            if method in MUTATING_METHODS:
                operations.append(
                    {
                        "method": method,
                        "operationId": operation["operationId"],
                        "path": path,
                        "security": operation.get("security", []),
                    }
                )
    return operations


def test_customer_demo_has_no_anonymous_business_write_operation() -> None:
    openapi = json.loads(OPENAPI.read_text(encoding="utf-8"))
    contract = json.loads(REVOCATION_CONTRACT.read_text(encoding="utf-8"))
    operations = mutating_operations(openapi)
    anonymous_mutation_allowlist = set(contract["anonymousMutationAllowlist"])
    anonymous = {operation["operationId"] for operation in operations if not operation["security"]}

    assert contract["anonymousBusinessWrites"] == 0
    assert anonymous == anonymous_mutation_allowlist == {"loginAccountSession"}
    assert [
        (operation["method"], operation["path"])
        for operation in operations
        if operation["operationId"] == "loginAccountSession"
    ] == [("post", "/api/v1/account-sessions")]

    required_schemes = set(contract["requiredSecuritySchemes"])
    observed_schemes: set[str] = set()
    for operation in operations:
        if operation["operationId"] in anonymous_mutation_allowlist:
            continue
        security = operation["security"]
        assert isinstance(security, list) and len(security) == 1
        requirement = security[0]
        assert isinstance(requirement, dict) and len(requirement) == 1
        scheme, scopes = next(iter(requirement.items()))
        assert scheme in required_schemes
        assert scopes == []
        observed_schemes.add(scheme)
    assert observed_schemes == required_schemes

    app_business_writes = {
        operation["operationId"]
        for operation in operations
        if operation["security"] == [{"AppSession": []}]
    }
    assert app_business_writes == {
        "applySmartEditMaterialWriteback",
        "cancelBilibiliPublishSession",
        "cancelTask",
        "confirmTaskTargetPreview",
        "createEditingProject",
        "createTask",
        "deleteEditingMaterial",
        "emergencyStopTask",
        "pauseTask",
        "prepareBilibiliPublish",
        "prepareDouyinPlatformSessionLogout",
        "registerEditingMaterial",
        "replaceTaskTargetExclusions",
        "resumeTask",
        "saveEditingProjectTimeline",
        "startTaskDiscovery",
        "submitBilibiliPublish",
        "submitEditingJob",
        "updateEditingMaterialDescription",
        "uploadBilibiliPublishVideo",
    }

use std::{fs, path::PathBuf};

use serde_json::Value;

fn read_json(relative_path: &str) -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(relative_path);
    let content = fs::read_to_string(path).expect("configuration must be readable");
    serde_json::from_str(&content).expect("configuration must be valid JSON")
}

#[test]
fn production_configuration_keeps_test_capabilities_disabled() {
    let config = read_json("tauri.conf.json");

    assert_eq!(config["app"]["withGlobalTauri"], false);
    assert_eq!(
        config["app"]["security"]["capabilities"],
        serde_json::json!(["main"])
    );
}

#[test]
fn desktop_e2e_configuration_enables_only_explicit_test_capabilities() {
    let config = read_json("tauri.test.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("test capabilities must be an array");
    let capability = &capabilities[1];

    assert_eq!(config["app"]["withGlobalTauri"], true);
    assert_eq!(capabilities[0], "main");
    assert_eq!(capability["identifier"], "wdio");
    assert_eq!(capability["windows"], serde_json::json!(["main"]));
    assert!(capability["permissions"]
        .as_array()
        .expect("permissions must be an array")
        .iter()
        .any(|permission| permission == "wdio-webdriver:default"));
}

#[test]
fn installation_revocation_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.installation-revocation-e2e.conf.json");
    let windows = config["app"]["windows"]
        .as_array()
        .expect("acceptance windows must be an array");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.i214acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "visible": false }])
    );
    assert_eq!(windows.len(), 1);
    assert_eq!(capabilities[0], "main");
    assert_eq!(
        capabilities[1]["identifier"],
        "wdio-installation-revocation"
    );
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn task_creation_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.task-creation-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.t306acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "visible": false }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-task-creation");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn task_query_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.task-query-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.t307acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "visible": false }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-task-query");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

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
fn browser_settings_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.browser-settings-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.b504acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{
            "label": "main",
            "title": "自动化运营工具",
            "visible": false
        }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-browser-settings");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
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
fn task_discovery_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.task-discovery-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.d610acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "visible": false }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-task-discovery");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn task_target_preview_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.task-target-preview-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.d611acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "visible": false }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-task-target-preview");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn task_target_preview_ui_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.task-target-preview-ui-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.d612acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "title": "自动化运营工具", "visible": false }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-task-target-preview-ui");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn task_restart_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.task-restart-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.t320acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "title": "自动化运营工具", "visible": false }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-task-restart");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn app_crash_recovery_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.app-crash-recovery-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.h804acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "title": "自动化运营工具", "visible": false }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-app-crash-recovery");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn executor_crash_recovery_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.executor-crash-recovery-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.h805acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "title": "自动化运营工具", "visible": false }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(
        capabilities[1]["identifier"],
        "wdio-executor-crash-recovery"
    );
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

#[test]
fn task_event_stream_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.task-event-stream-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.t312acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "visible": false }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-task-event-stream");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn task_control_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.task-control-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.t313acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "visible": false }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-task-control");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn task_termination_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.task-termination-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.t314acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "visible": false }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-task-termination");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn task_projection_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.task-projection-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.t315acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "visible": false }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-task-projection");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn workbench_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.workbench-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.t316acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{
            "label": "main",
            "title": "自动化运营工具",
            "visible": false
        }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-workbench");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn workbench_metrics_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.workbench-metrics-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.h814acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{
            "label": "main",
            "title": "自动化运营工具",
            "width": 1280,
            "height": 1600,
            "visible": false
        }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-workbench-metrics");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn task_create_form_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.task-create-form-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.t317acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{
            "label": "main",
            "title": "自动化运营工具",
            "visible": false
        }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-task-create-form");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn task_run_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.task-run-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.t318acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "title": "自动化运营工具", "visible": false }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-task-run");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

#[test]
fn task_lifecycle_acceptance_is_isolated_and_hidden() {
    let config = read_json("tauri.task-lifecycle-e2e.conf.json");
    let capabilities = config["app"]["security"]["capabilities"]
        .as_array()
        .expect("acceptance capabilities must be an array");

    assert_eq!(
        config["identifier"],
        "com.aventador.automationtool.t319acceptance"
    );
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([{ "label": "main", "title": "自动化运营工具", "visible": false }])
    );
    assert_eq!(capabilities[0], "main");
    assert_eq!(capabilities[1]["identifier"], "wdio-task-lifecycle");
    assert_eq!(capabilities[1]["windows"], serde_json::json!(["main"]));
}

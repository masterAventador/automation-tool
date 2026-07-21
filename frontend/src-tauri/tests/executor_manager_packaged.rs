use std::fs;
use std::path::PathBuf;
use std::time::Duration;

use automation_tool_desktop_lib::executor_manager::{
    ExecutorLaunchConfiguration, ExecutorManager, ExecutorManagerState, ExecutorRestartPolicy,
};

#[cfg(all(feature = "control-plane-e2e", windows))]
use automation_tool_desktop_lib::executor_manager::ExecutorManagerErrorCode;
use automation_tool_desktop_lib::executor_package::ExecutorPackageVerifier;
use ed25519_dalek::SigningKey;
use serde::Deserialize;

const TEST_SEED: [u8; 32] = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25,
    26, 27, 28, 29, 30, 31,
];

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RealAcceptanceConfiguration {
    package_root: PathBuf,
    websocket_url: String,
    session_token: String,
    installation_id: String,
    executor_id: String,
    state_directory: PathBuf,
}

fn manager_for_root(package_root: PathBuf) -> ExecutorManager {
    manager_for_root_with_timeouts(
        package_root,
        Duration::from_secs(10),
        Duration::from_secs(10),
    )
}

fn manager_for_root_with_timeouts(
    package_root: PathBuf,
    start_timeout: Duration,
    stop_timeout: Duration,
) -> ExecutorManager {
    let verifying_key = SigningKey::from_bytes(&TEST_SEED)
        .verifying_key()
        .to_bytes();
    let verifier =
        ExecutorPackageVerifier::new(verifying_key, "=0.1.0", None).expect("test verifier");
    let restart_policy =
        ExecutorRestartPolicy::new(2, Duration::from_millis(10), Duration::from_millis(10))
            .expect("restart policy");
    ExecutorManager::new(
        package_root,
        verifier,
        start_timeout,
        stop_timeout,
        restart_policy,
    )
    .expect("manager")
}

#[test]
#[ignore = "requires the E4-07 PyInstaller/Uvicorn acceptance orchestrator"]
fn real_packaged_executor_uses_the_public_manager_lifecycle() {
    let configuration_path = std::env::var_os("AUTOMATION_TOOL_E407_CONFIGURATION")
        .map(PathBuf::from)
        .expect("E4-07 configuration path");
    let configuration: RealAcceptanceConfiguration = serde_json::from_slice(
        &fs::read(configuration_path).expect("read private acceptance configuration"),
    )
    .expect("strict acceptance configuration");
    let manager = manager_for_root(configuration.package_root);
    let launch = ExecutorLaunchConfiguration::new(
        configuration.websocket_url,
        configuration.session_token,
        configuration.installation_id,
        configuration.executor_id,
        configuration.state_directory,
        1,
    )
    .expect("real launch configuration");

    assert_eq!(
        manager.start(launch).expect("real packaged start").state(),
        ExecutorManagerState::Running,
    );
    assert_eq!(
        manager.status().expect("real status").state(),
        ExecutorManagerState::Running,
    );
    assert_eq!(
        manager.stop().expect("real packaged stop").state(),
        ExecutorManagerState::Stopped,
    );
}
#[cfg(feature = "control-plane-e2e")]
fn wait_for_status(manager: &ExecutorManager, state: ExecutorManagerState, restart_count: u8) {
    let deadline = std::time::Instant::now() + Duration::from_secs(20);
    loop {
        let status = manager.status().expect("supervised status");
        if status.state() == state && status.restart_count() == restart_count {
            return;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "supervisor did not converge to {state:?} at restart {restart_count}"
        );
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(feature = "control-plane-e2e")]
#[test]
#[ignore = "requires the E4-08 PyInstaller/Uvicorn acceptance orchestrator"]
fn real_packaged_executor_enforces_bounded_restart_policy() {
    let configuration_path = std::env::var_os("AUTOMATION_TOOL_E407_CONFIGURATION")
        .map(PathBuf::from)
        .expect("E4-08 configuration path");
    let configuration: RealAcceptanceConfiguration = serde_json::from_slice(
        &fs::read(configuration_path).expect("read private acceptance configuration"),
    )
    .expect("strict acceptance configuration");
    let manager = manager_for_root(configuration.package_root);
    let launch = ExecutorLaunchConfiguration::new(
        configuration.websocket_url,
        configuration.session_token,
        configuration.installation_id,
        configuration.executor_id,
        configuration.state_directory,
        1,
    )
    .expect("real launch configuration");

    manager.start(launch).expect("initial packaged start");
    for restart_count in 1..=2 {
        manager
            .inject_crash_for_acceptance()
            .expect("inject packaged crash");
        wait_for_status(&manager, ExecutorManagerState::Running, restart_count);
    }
    manager
        .inject_crash_for_acceptance()
        .expect("inject terminal packaged crash");
    wait_for_status(&manager, ExecutorManagerState::Stopped, 2);
}
#[cfg(feature = "control-plane-e2e")]
fn launch_for(
    configuration: &RealAcceptanceConfiguration,
    state_directory: PathBuf,
) -> ExecutorLaunchConfiguration {
    ExecutorLaunchConfiguration::new(
        configuration.websocket_url.clone(),
        configuration.session_token.clone(),
        configuration.installation_id.clone(),
        configuration.executor_id.clone(),
        state_directory,
        1,
    )
    .expect("E4-09 launch configuration")
}

#[cfg(all(feature = "control-plane-e2e", windows))]
fn prepare_case(base: &std::path::Path, name: &str, mode: &str) -> PathBuf {
    let state_directory = base.join(name);
    fs::create_dir_all(&state_directory).expect("E4-09 state directory");
    fs::write(state_directory.join("e4-09-mode"), mode).expect("E4-09 mode");
    state_directory
}

#[cfg(all(feature = "control-plane-e2e", windows))]
fn descendant_pids(state_directory: &std::path::Path) -> Vec<u32> {
    fs::read_to_string(state_directory.join("descendant-pids"))
        .unwrap_or_default()
        .lines()
        .filter_map(|line| line.trim().parse().ok())
        .collect()
}

#[cfg(all(feature = "control-plane-e2e", windows))]
fn wait_for_descendants(state_directory: &std::path::Path, expected: usize) -> Vec<u32> {
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    loop {
        let process_ids = descendant_pids(state_directory);
        if process_ids.len() >= expected {
            return process_ids;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "E4-09 descendant marker did not reach {expected}"
        );
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(all(feature = "control-plane-e2e", windows))]
fn process_exists(process_id: u32) -> bool {
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };

    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, process_id) };
    if handle.is_null() {
        return false;
    }
    let mut exit_code = 0_u32;
    let active = unsafe { GetExitCodeProcess(handle, &mut exit_code) } != 0 && exit_code == 259;
    unsafe {
        CloseHandle(handle);
    }
    active
}

#[cfg(all(feature = "control-plane-e2e", windows))]
fn wait_for_descendant_exit(process_id: u32) {
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    while process_exists(process_id) && std::time::Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(
        !process_exists(process_id),
        "E4-09 descendant survived Job cleanup"
    );
}

#[cfg(all(feature = "control-plane-e2e", windows))]
#[test]
#[ignore = "requires the E4-09 packaged process-tree orchestrator"]
fn real_packaged_executor_cleans_its_windows_job_tree() {
    let configuration_path = std::env::var_os("AUTOMATION_TOOL_E407_CONFIGURATION")
        .map(PathBuf::from)
        .expect("E4-09 configuration path");
    let configuration: RealAcceptanceConfiguration = serde_json::from_slice(
        &fs::read(configuration_path).expect("read private acceptance configuration"),
    )
    .expect("strict acceptance configuration");
    let package_root = configuration.package_root.clone();
    let state_root = configuration.state_directory.clone();

    let explicit = prepare_case(&state_root, "explicit-stop", "healthy");
    let manager = manager_for_root(package_root.clone());
    manager
        .start(launch_for(&configuration, explicit.clone()))
        .expect("start explicit-stop probe");
    let explicit_pid = wait_for_descendants(&explicit, 1)[0];
    manager.stop().expect("stop complete Windows Job");
    wait_for_descendant_exit(explicit_pid);

    let hung = prepare_case(&state_root, "hung-stop", "healthy");
    let manager = manager_for_root(package_root.clone());
    manager
        .start(launch_for(&configuration, hung.clone()))
        .expect("start hung probe");
    let hung_pid = wait_for_descendants(&hung, 1)[0];
    manager
        .inject_hang_for_acceptance()
        .expect("suspend packaged probe");
    manager.stop().expect("stop suspended Windows Job");
    wait_for_descendant_exit(hung_pid);

    let silent = prepare_case(&state_root, "startup-timeout", "silent");
    let manager = manager_for_root_with_timeouts(
        package_root.clone(),
        Duration::from_secs(2),
        Duration::from_secs(10),
    );
    let error = manager
        .start(launch_for(&configuration, silent.clone()))
        .expect_err("silent packaged probe must time out");
    assert_eq!(error.code(), ExecutorManagerErrorCode::TimedOut);
    let silent_pid = wait_for_descendants(&silent, 1)[0];
    wait_for_descendant_exit(silent_pid);

    let dropped = prepare_case(&state_root, "manager-drop", "healthy");
    let manager = manager_for_root(package_root.clone());
    manager
        .start(launch_for(&configuration, dropped.clone()))
        .expect("start drop probe");
    let dropped_pid = wait_for_descendants(&dropped, 1)[0];
    drop(manager);
    wait_for_descendant_exit(dropped_pid);

    let restarted = prepare_case(&state_root, "crash-restart", "healthy");
    let manager = manager_for_root(package_root);
    manager
        .start(launch_for(&configuration, restarted.clone()))
        .expect("start crash probe");
    let first_pid = wait_for_descendants(&restarted, 1)[0];
    manager
        .inject_crash_for_acceptance()
        .expect("crash packaged probe");
    wait_for_status(&manager, ExecutorManagerState::Running, 1);
    let process_ids = wait_for_descendants(&restarted, 2);
    wait_for_descendant_exit(first_pid);
    assert!(
        process_exists(process_ids[1]),
        "restarted descendant must run"
    );
    manager.stop().expect("stop restarted Windows Job");
    wait_for_descendant_exit(process_ids[1]);
}
#[cfg(feature = "control-plane-e2e")]
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DiagnosticFixtureDocument {
    cases: Vec<DiagnosticFixtureCase>,
    fixture_version: String,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DiagnosticFixtureCase {
    expected: String,
    input: String,
    #[serde(rename = "name")]
    _name: String,
}

#[cfg(feature = "control-plane-e2e")]
fn prepare_diagnostic_case(base: &std::path::Path, name: &str, mode: &str) -> PathBuf {
    let state_directory = base.join(name);
    fs::create_dir_all(&state_directory).expect("E4-10 state directory");
    fs::write(state_directory.join("e4-10-mode"), mode).expect("E4-10 mode");
    state_directory
}

#[cfg(feature = "control-plane-e2e")]
fn wait_for_changed_diagnostic_tail(
    manager: &ExecutorManager,
    previous: &[String],
    expected_tail: &str,
) -> Vec<String> {
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    loop {
        let diagnostics = manager.diagnostics().expect("safe diagnostics");
        if diagnostics != previous && diagnostics.last().is_some_and(|line| line == expected_tail) {
            return diagnostics;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "E4-10 diagnostic reader did not converge: len={}, tail={:?}, changed={}",
            diagnostics.len(),
            diagnostics.last(),
            diagnostics != previous,
        );
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(feature = "control-plane-e2e")]
#[test]
#[ignore = "requires the E4-10 packaged stderr orchestrator"]
fn real_packaged_executor_bounds_and_redacts_stderr() {
    let configuration_path = std::env::var_os("AUTOMATION_TOOL_E407_CONFIGURATION")
        .map(PathBuf::from)
        .expect("E4-10 configuration path");
    let configuration: RealAcceptanceConfiguration = serde_json::from_slice(
        &fs::read(configuration_path).expect("read private acceptance configuration"),
    )
    .expect("strict acceptance configuration");
    let package_root = configuration.package_root.clone();
    let state_root = configuration.state_directory.clone();
    let fixture_source = include_str!("../../../contracts/fixtures/executor-diagnostics-v1.json");
    let fixture: DiagnosticFixtureDocument =
        serde_json::from_str(fixture_source).expect("strict diagnostic fixture");
    assert_eq!(fixture.fixture_version, "2");

    let shared = prepare_diagnostic_case(&state_root, "shared-fixture", "shared-fixture");
    fs::write(shared.join("diagnostic-inputs.json"), fixture_source)
        .expect("shared diagnostic inputs");
    let manager = manager_for_root(package_root.clone());
    manager
        .start(launch_for(&configuration, shared))
        .expect("start shared diagnostic probe");
    manager
        .emergency_stop()
        .expect("hard-stop shared diagnostic probe");
    assert_eq!(
        manager.diagnostics().expect("shared safe diagnostics"),
        fixture
            .cases
            .iter()
            .map(|case| case.expected.clone())
            .collect::<Vec<_>>()
    );
    assert!(fixture
        .cases
        .iter()
        .any(|case| case.input.contains("C:\\Users")));

    let limits = prepare_diagnostic_case(&state_root, "limits", "limits");
    let manager = manager_for_root(package_root);
    manager
        .start(launch_for(&configuration, limits))
        .expect("start bounded diagnostic probe");
    let first = wait_for_changed_diagnostic_tail(&manager, &[], "E4-10-finished-1");
    manager
        .inject_crash_for_acceptance()
        .expect("crash diagnostic probe");
    wait_for_status(&manager, ExecutorManagerState::Running, 1);
    let diagnostics = wait_for_changed_diagnostic_tail(&manager, &first, "E4-10-finished-2");
    manager
        .emergency_stop()
        .expect("hard-stop restarted diagnostic probe");

    assert!(diagnostics.len() <= 200);
    assert!(diagnostics.iter().map(String::len).sum::<usize>() <= 64 * 1024);
    assert!(diagnostics.iter().all(|line| line.len() <= 4096));
    assert!(diagnostics.iter().any(|line| line == "[TRUNCATED]"));
    assert!(diagnostics.iter().any(|line| line == "[REDACTED]"));
    assert!(!diagnostics
        .join("\n")
        .contains("private-diagnostic-session"));
}

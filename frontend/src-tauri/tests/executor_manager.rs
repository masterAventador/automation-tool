#![cfg(target_os = "macos")]

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Barrier};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::executor_bootstrap::LocalPlatformCommand;
use automation_tool_desktop_lib::executor_manager::{
    ExecutorLaunchConfiguration, ExecutorManager, ExecutorManagerErrorCode, ExecutorManagerState,
    ExecutorRestartPolicy,
};
use automation_tool_desktop_lib::executor_package::ExecutorPackageVerifier;
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use ed25519_dalek::{Signer, SigningKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const TEST_SEED: [u8; 32] = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25,
    26, 27, 28, 29, 30, 31,
];
static TEMPORARY_PACKAGE_SEQUENCE: AtomicU64 = AtomicU64::new(0);

const HEALTHY_FIXTURE: &str = r#"#!/usr/bin/env python3
import base64, hashlib, hmac, json, signal, sys, time
bootstrap = json.loads(sys.stdin.readline())
key = bytes.fromhex(bootstrap["local_session_token"])
stopping = False
def proof(event):
    message = b"automation-tool.local-executor-event.v1\0" + event.encode() + b"\0" + b"1.0"
    return "atlep1." + base64.urlsafe_b64encode(hmac.digest(key, message, hashlib.sha256)).rstrip(b"=").decode()
def emit(event):
    print(json.dumps({"authenticationProof": proof(event), "event": event, "protocolVersion": "1.0"}, separators=(",", ":")), flush=True)
def stop(_signum, _frame):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM, stop)
emit("executor.healthy")
while not stopping:
    time.sleep(0.01)
emit("executor.stopped")
"#;

const BAD_PROOF_FIXTURE: &str = r#"#!/usr/bin/env python3
import json, sys, time
json.loads(sys.stdin.readline())
print('{"authenticationProof":"atlep1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","event":"executor.healthy","protocolVersion":"1.0"}', flush=True)
time.sleep(30)
"#;

const SILENT_FIXTURE: &str = r#"#!/usr/bin/env python3
import json, sys, time
json.loads(sys.stdin.readline())
time.sleep(30)
"#;

const PLATFORM_COMMAND_FIXTURE: &str = r#"#!/usr/bin/env python3
import base64, hashlib, hmac, json, signal, sys
bootstrap = json.loads(sys.stdin.readline())
key = bytes.fromhex(bootstrap["local_session_token"])
def encoded(domain, parts):
    message = domain + b"\0".join(part.encode() for part in parts)
    return "atlcp1." + base64.urlsafe_b64encode(hmac.digest(key, message, hashlib.sha256)).rstrip(b"=").decode()
def lifecycle(event):
    message = b"automation-tool.local-executor-event.v1\0" + event.encode() + b"\0" + b"1.0"
    proof = "atlep1." + base64.urlsafe_b64encode(hmac.digest(key, message, hashlib.sha256)).rstrip(b"=").decode()
    print(json.dumps({"authenticationProof": proof, "event": event, "protocolVersion": "1.0"}, separators=(",", ":")), flush=True)
signal.signal(signal.SIGTERM, lambda _signum, _frame: None)
lifecycle("executor.healthy")
for line in sys.stdin:
    command = json.loads(line)
    parts = [command["commandId"], command["commandType"], command["executablePath"], command["profileDirectory"], "1" if command["headless"] else "0", command["protocolVersion"]]
    assert hmac.compare_digest(command["authenticationProof"], encoded(b"automation-tool.local-executor-command.v1\0", parts))
    state = "awaiting_scan" if command["commandType"] == "douyin.login.open" else "healthy"
    result = {"authenticationProof": encoded(b"automation-tool.local-executor-result.v1\0", [command["commandId"], state, "1.0"]), "commandId": command["commandId"], "event": "platform.command.completed", "flowVersion": "douyin.qr-login.v2", "platform": "douyin", "protocolVersion": "1.0", "state": state}
    print(json.dumps(result, separators=(",", ":"), sort_keys=True), flush=True)
lifecycle("executor.stopped")
"#;

struct DescendantMarker {
    path: PathBuf,
}

impl DescendantMarker {
    fn new() -> Self {
        Self {
            path: std::env::temp_dir().join(format!(
                "automation-tool-e4-09-descendant-{}-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .expect("system time")
                    .as_nanos(),
                TEMPORARY_PACKAGE_SEQUENCE.fetch_add(1, Ordering::Relaxed),
            )),
        }
    }

    fn wait_for_pid(&self) -> i32 {
        self.wait_for_count(1)[0]
    }

    fn wait_for_count(&self, expected: usize) -> Vec<i32> {
        let deadline = std::time::Instant::now() + Duration::from_secs(3);
        while self.pids().len() < expected && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        let process_ids = self.pids();
        assert_eq!(process_ids.len(), expected, "descendant marker count");
        process_ids
    }

    fn pids(&self) -> Vec<i32> {
        fs::read_to_string(&self.path)
            .unwrap_or_default()
            .split_whitespace()
            .filter_map(|value| value.parse().ok())
            .collect()
    }

    fn wait_for_exit(&self, process_id: i32) {
        let deadline = std::time::Instant::now() + Duration::from_secs(3);
        while process_exists(process_id) && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(
            !process_exists(process_id),
            "descendant process must be terminated"
        );
    }
}

impl Drop for DescendantMarker {
    fn drop(&mut self) {
        for process_id in self.pids() {
            unsafe {
                libc::kill(process_id, libc::SIGKILL);
            }
        }
        let _ = fs::remove_file(&self.path);
    }
}

fn process_exists(process_id: i32) -> bool {
    if unsafe { libc::kill(process_id, 0) } == 0 {
        return true;
    }
    std::io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH)
}

fn process_tree_fixture(marker: &DescendantMarker, after_spawn: &str) -> String {
    let marker_path = serde_json::to_string(&marker.path.to_string_lossy()).expect("marker path");
    format!(
        r#"#!/usr/bin/env python3
import base64, hashlib, hmac, json, signal, subprocess, sys, time
bootstrap = json.loads(sys.stdin.readline())
key = bytes.fromhex(bootstrap["local_session_token"])
stopping = False
def proof(event):
    message = b"automation-tool.local-executor-event.v1\0" + event.encode() + b"\0" + b"1.0"
    return "atlep1." + base64.urlsafe_b64encode(hmac.digest(key, message, hashlib.sha256)).rstrip(b"=").decode()
def emit(event):
    print(json.dumps({{"authenticationProof": proof(event), "event": event, "protocolVersion": "1.0"}}, separators=(",", ":")), flush=True)
def stop(_signum, _frame):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM, stop)
descendant_source = "import os,pathlib,signal,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
subprocess.Popen([sys.executable, "-c", descendant_source, {marker_path}], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
{after_spawn}
"#,
    )
}

fn diagnostic_fixture(before_healthy: &str) -> String {
    format!(
        r#"#!/usr/bin/env python3
import base64, hashlib, hmac, json, signal, sys, time
bootstrap = json.loads(sys.stdin.readline())
key = bytes.fromhex(bootstrap["local_session_token"])
stopping = False
def proof(event):
    message = b"automation-tool.local-executor-event.v1\0" + event.encode() + b"\0" + b"1.0"
    return "atlep1." + base64.urlsafe_b64encode(hmac.digest(key, message, hashlib.sha256)).rstrip(b"=").decode()
def emit(event):
    print(json.dumps({{"authenticationProof": proof(event), "event": event, "protocolVersion": "1.0"}}, separators=(",", ":")), flush=True)
def stop(_signum, _frame):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM, stop)
{before_healthy}
emit("executor.healthy")
while not stopping:
    time.sleep(0.01)
emit("executor.stopped")
"#,
    )
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DiagnosticFixtureDocument {
    fixture_version: String,
    cases: Vec<DiagnosticFixtureCase>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DiagnosticFixtureCase {
    expected: String,
    input: String,
    name: String,
}

fn diagnostic_fixture_document() -> DiagnosticFixtureDocument {
    serde_json::from_str(include_str!(
        "../../../contracts/fixtures/executor-diagnostics-v1.json"
    ))
    .expect("strict diagnostic fixture")
}

fn wait_for_diagnostic(manager: &ExecutorManager, expected: &str) -> Vec<String> {
    let deadline = std::time::Instant::now() + Duration::from_secs(3);
    loop {
        let diagnostics = manager.diagnostics().expect("safe diagnostics");
        if diagnostics.iter().any(|line| line == expected) {
            return diagnostics;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "diagnostic reader did not retain the expected line"
        );
        std::thread::sleep(Duration::from_millis(10));
    }
}

fn silent_process_tree_fixture(marker: &DescendantMarker) -> String {
    let marker_path = marker.path.to_string_lossy().replace('\'', "'\"'\"'");
    format!(
        r#"#!/bin/sh
IFS= read -r bootstrap
/bin/sh -c 'trap "" TERM; sleep 30' </dev/null >/dev/null 2>&1 &
printf "%s\n" "$!" > '{marker_path}'
sleep 30
"#,
    )
}

struct TemporaryPackage {
    root: PathBuf,
}

impl TemporaryPackage {
    fn new(source: &str) -> Self {
        let root = std::env::temp_dir().join(format!(
            "automation-tool-e4-07-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            TEMPORARY_PACKAGE_SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        fs::create_dir_all(&root).expect("package root");
        let entrypoint = root.join("automation-tool-executor");
        fs::write(&entrypoint, source).expect("fixture executable");
        fs::set_permissions(&entrypoint, fs::Permissions::from_mode(0o700))
            .expect("fixture permissions");
        write_signed_manifest(&root, &entrypoint);
        Self { root }
    }
}

impl Drop for TemporaryPackage {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[derive(Serialize)]
struct ManifestFile {
    path: String,
    sha256: String,
    size: u64,
}

#[derive(Serialize)]
struct Manifest {
    architecture: &'static str,
    build_id: &'static str,
    entrypoint: &'static str,
    executor_version: &'static str,
    files: Vec<ManifestFile>,
    manifest_version: &'static str,
    package_sha256: String,
    package_size: u64,
    platform: &'static str,
}

fn write_signed_manifest(root: &Path, entrypoint: &Path) {
    let contents = fs::read(entrypoint).expect("fixture contents");
    let file_hash = Sha256::digest(&contents);
    let mut package_digest = Sha256::new();
    package_digest.update(b"automation-tool.executor-package.v1\0");
    let path = b"automation-tool-executor";
    package_digest.update((path.len() as u32).to_be_bytes());
    package_digest.update(path);
    package_digest.update((contents.len() as u64).to_be_bytes());
    package_digest.update(file_hash);
    let manifest = Manifest {
        architecture: if cfg!(target_arch = "x86_64") {
            "x86_64"
        } else {
            "aarch64"
        },
        build_id: "e4-07-test",
        entrypoint: "automation-tool-executor",
        executor_version: "0.1.0",
        files: vec![ManifestFile {
            path: "automation-tool-executor".to_owned(),
            sha256: hex(&file_hash),
            size: contents.len() as u64,
        }],
        manifest_version: "1",
        package_sha256: hex(&package_digest.finalize()),
        package_size: contents.len() as u64,
        platform: "macos",
    };
    let mut manifest_bytes = serde_json::to_vec(&manifest).expect("manifest JSON");
    manifest_bytes.push(b'\n');
    let signature = SigningKey::from_bytes(&TEST_SEED).sign(&manifest_bytes);
    fs::write(root.join("executor-manifest.v1.json"), &manifest_bytes).expect("manifest");
    fs::write(
        root.join("executor-manifest.v1.sig"),
        format!("atems1.{}\n", URL_SAFE_NO_PAD.encode(signature.to_bytes())),
    )
    .expect("signature");
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn manager(package: &TemporaryPackage) -> Arc<ExecutorManager> {
    manager_for_root(package.root.clone())
}

fn manager_for_root(package_root: PathBuf) -> Arc<ExecutorManager> {
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
) -> Arc<ExecutorManager> {
    let verifying_key = SigningKey::from_bytes(&TEST_SEED)
        .verifying_key()
        .to_bytes();
    let verifier =
        ExecutorPackageVerifier::new(verifying_key, "=0.1.0", None).expect("test verifier");
    let restart_policy =
        ExecutorRestartPolicy::new(2, Duration::from_millis(10), Duration::from_millis(10))
            .expect("restart policy");
    Arc::new(
        ExecutorManager::new(
            package_root,
            verifier,
            start_timeout,
            stop_timeout,
            restart_policy,
        )
        .expect("manager"),
    )
}

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

fn launch(state_directory: PathBuf) -> ExecutorLaunchConfiguration {
    ExecutorLaunchConfiguration::new(
        "ws://127.0.0.1:8765/api/v1/executors/connect".to_owned(),
        "atds1.private-control-plane-session".to_owned(),
        "123e4567-e89b-42d3-a456-426614174003".to_owned(),
        "123e4567-e89b-42d3-a456-426614174004".to_owned(),
        state_directory,
        1,
    )
    .expect("launch configuration")
}

#[test]
fn verified_process_starts_reports_status_and_stops_with_authenticated_events() {
    let package = TemporaryPackage::new(HEALTHY_FIXTURE);
    let manager = manager(&package);

    let started = manager
        .start(launch(package.root.join("executor-state")))
        .expect("start");
    assert_eq!(started.state(), ExecutorManagerState::Running);
    assert_eq!(started.version(), Some("0.1.0"));
    assert_eq!(started.build_id(), Some("e4-07-test"));
    assert_eq!(manager.status().expect("status"), started);

    let stopped = manager.stop().expect("stop");
    assert_eq!(stopped.state(), ExecutorManagerState::Stopped);
    assert_eq!(manager.stop().expect("idempotent stop"), stopped);
}

#[test]
fn authenticated_platform_commands_reuse_the_running_executor_stdio_channel() {
    let package = TemporaryPackage::new(PLATFORM_COMMAND_FIXTURE);
    let manager = manager(&package);
    manager
        .start(launch(package.root.join("executor-state")))
        .expect("start platform command fixture");

    let opened = manager
        .execute_platform_command(
            LocalPlatformCommand::OpenDouyinLogin,
            package.root.join("automation-tool-executor"),
            package.root.clone(),
            true,
        )
        .expect("open login command");
    let healthy = manager
        .execute_platform_command(
            LocalPlatformCommand::RecheckDouyinLogin,
            package.root.join("automation-tool-executor"),
            package.root.clone(),
            true,
        )
        .expect("recheck login command");

    assert_eq!(opened.state(), "awaiting_scan");
    assert_eq!(healthy.state(), "healthy");
    manager.stop().expect("stop platform command fixture");
}

#[test]
fn concurrent_start_is_linearized_to_one_process() {
    let package = TemporaryPackage::new(HEALTHY_FIXTURE);
    let manager = manager(&package);
    let barrier = Arc::new(Barrier::new(8));
    let state_directory = package.root.join("executor-state");
    let threads = (0..8)
        .map(|_| {
            let manager = Arc::clone(&manager);
            let barrier = Arc::clone(&barrier);
            let state_directory = state_directory.clone();
            std::thread::spawn(move || {
                barrier.wait();
                manager.start(launch(state_directory))
            })
        })
        .collect::<Vec<_>>();
    let results = threads
        .into_iter()
        .map(|thread| thread.join().expect("start thread"))
        .collect::<Vec<_>>();

    assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
    assert_eq!(
        results
            .iter()
            .filter(|result| result
                .as_ref()
                .is_err_and(|error| error.code() == ExecutorManagerErrorCode::AlreadyRunning))
            .count(),
        7,
    );
    manager.stop().expect("cleanup");
}

#[test]
fn invalid_package_configuration_and_health_proof_fail_closed_without_leaks() {
    let missing = TemporaryPackage::new(HEALTHY_FIXTURE);
    fs::write(missing.root.join("automation-tool-executor"), b"tampered").expect("tamper package");
    let error = manager(&missing)
        .start(launch(missing.root.join("executor-state")))
        .expect_err("tampered package");
    assert_eq!(error.code(), ExecutorManagerErrorCode::PackageRejected);
    assert_eq!(error.to_string(), "Local Executor lifecycle is unavailable");
    assert!(!format!("{error:?}").contains("private"));

    let bad_proof = TemporaryPackage::new(BAD_PROOF_FIXTURE);
    let error = manager(&bad_proof)
        .start(launch(bad_proof.root.join("executor-state")))
        .expect_err("bad proof");
    assert_eq!(
        error.code(),
        ExecutorManagerErrorCode::AuthenticationRejected
    );
    assert!(!format!("{error:?}").contains("atds1"));

    assert!(ExecutorLaunchConfiguration::new(
        "https://invalid.example".to_owned(),
        "private session".to_owned(),
        "invalid".to_owned(),
        "invalid".to_owned(),
        PathBuf::from("relative-state"),
        0,
    )
    .is_err());

    for policy in [
        ExecutorRestartPolicy::new(9, Duration::from_millis(10), Duration::from_millis(10)),
        ExecutorRestartPolicy::new(2, Duration::ZERO, Duration::from_millis(10)),
        ExecutorRestartPolicy::new(2, Duration::from_millis(10), Duration::from_secs(61)),
    ] {
        assert_eq!(
            policy.expect_err("invalid restart policy").code(),
            ExecutorManagerErrorCode::ConfigurationInvalid,
        );
    }
}

#[test]
fn startup_timeout_force_stops_the_child_and_leaves_the_manager_stopped() {
    let package = TemporaryPackage::new(SILENT_FIXTURE);
    let manager = manager_for_root_with_timeouts(
        package.root.clone(),
        Duration::from_millis(100),
        Duration::from_secs(1),
    );

    let error = manager
        .start(launch(package.root.join("executor-state")))
        .expect_err("silent startup timeout");
    assert_eq!(error.code(), ExecutorManagerErrorCode::TimedOut);
    assert_eq!(
        manager.status().expect("status after timeout").state(),
        ExecutorManagerState::Stopped,
    );
}

#[test]
fn explicit_stop_terminates_the_complete_executor_process_tree() {
    let marker = DescendantMarker::new();
    let package = TemporaryPackage::new(&process_tree_fixture(
        &marker,
        r#"emit("executor.healthy")
while not stopping:
    time.sleep(0.01)
emit("executor.stopped")"#,
    ));
    let manager = manager(&package);

    manager
        .start(launch(package.root.join("executor-state")))
        .expect("start process tree");
    let descendant_id = marker.wait_for_pid();
    manager.stop().expect("stop process tree");

    marker.wait_for_exit(descendant_id);
}

#[test]
fn hung_stop_times_out_and_terminates_the_complete_executor_process_tree() {
    let marker = DescendantMarker::new();
    let package = TemporaryPackage::new(&process_tree_fixture(
        &marker,
        r#"signal.signal(signal.SIGTERM, signal.SIG_IGN)
emit("executor.healthy")
time.sleep(30)"#,
    ));
    let manager = manager_for_root_with_timeouts(
        package.root.clone(),
        Duration::from_secs(10),
        Duration::from_millis(100),
    );

    manager
        .start(launch(package.root.join("executor-state")))
        .expect("start hung process tree");
    let descendant_id = marker.wait_for_pid();
    let error = manager.stop().expect_err("hung stop must time out");

    assert_eq!(error.code(), ExecutorManagerErrorCode::TimedOut);
    marker.wait_for_exit(descendant_id);
}

#[test]
fn emergency_stop_immediately_terminates_the_complete_executor_process_tree() {
    let marker = DescendantMarker::new();
    let package = TemporaryPackage::new(&process_tree_fixture(
        &marker,
        r#"signal.signal(signal.SIGTERM, signal.SIG_IGN)
emit("executor.healthy")
time.sleep(30)"#,
    ));
    let manager = manager_for_root_with_timeouts(
        package.root.clone(),
        Duration::from_secs(10),
        Duration::from_secs(10),
    );

    manager
        .start(launch(package.root.join("executor-state")))
        .expect("start emergency-stop process tree");
    let descendant_id = marker.wait_for_pid();
    let started_at = Instant::now();
    let stopped = manager.emergency_stop().expect("hard emergency stop");

    assert_eq!(stopped.state(), ExecutorManagerState::Stopped);
    assert!(started_at.elapsed() < Duration::from_secs(2));
    marker.wait_for_exit(descendant_id);
}

#[test]
fn startup_timeout_terminates_the_complete_executor_process_tree() {
    let marker = DescendantMarker::new();
    let package = TemporaryPackage::new(&silent_process_tree_fixture(&marker));
    let manager = manager_for_root_with_timeouts(
        package.root.clone(),
        Duration::from_secs(10),
        Duration::from_secs(1),
    );

    let error = manager
        .start(launch(package.root.join("executor-state")))
        .expect_err("silent process tree");
    let descendant_id = marker.wait_for_pid();

    assert_eq!(error.code(), ExecutorManagerErrorCode::TimedOut);
    marker.wait_for_exit(descendant_id);
}

#[test]
fn dropping_the_manager_terminates_the_complete_executor_process_tree() {
    let marker = DescendantMarker::new();
    let package = TemporaryPackage::new(&process_tree_fixture(
        &marker,
        r#"emit("executor.healthy")
while not stopping:
    time.sleep(0.01)
emit("executor.stopped")"#,
    ));
    let manager = manager(&package);

    manager
        .start(launch(package.root.join("executor-state")))
        .expect("start process tree");
    let descendant_id = marker.wait_for_pid();
    drop(manager);

    marker.wait_for_exit(descendant_id);
}

#[test]
fn real_executor_stderr_is_redacted_by_the_manager_before_diagnostics_are_exposed() {
    let document = diagnostic_fixture_document();
    assert_eq!(document.fixture_version, "1");
    let inputs = document
        .cases
        .iter()
        .map(|case| case.input.clone())
        .collect::<Vec<_>>();
    let encoded_inputs = serde_json::to_string(&inputs).expect("diagnostic inputs");
    let package = TemporaryPackage::new(&diagnostic_fixture(&format!(
        "for line in {encoded_inputs}: print(line, file=sys.stderr, flush=True)"
    )));
    let manager = manager(&package);

    manager
        .start(launch(package.root.join("executor-state")))
        .expect("start diagnostic fixture");
    let diagnostics = wait_for_diagnostic(
        &manager,
        &document.cases.last().expect("last case").expected,
    );
    manager.stop().expect("stop diagnostic fixture");

    assert_eq!(diagnostics.len(), document.cases.len());
    for (actual, expected) in diagnostics.iter().zip(document.cases.iter()) {
        assert_eq!(actual, &expected.expected, "{}", expected.name);
    }
}

#[test]
fn real_executor_stderr_retention_is_bounded_before_and_after_redaction() {
    let package = TemporaryPackage::new(&diagnostic_fixture(
        r#"for index in range(400):
    print("token=secret-%s %s" % (index, "x" * 1000), file=sys.stderr, flush=True)
print("y" * 5000, file=sys.stderr, flush=True)
print("diagnostic-complete", file=sys.stderr, flush=True)"#,
    ));
    let manager = manager(&package);

    manager
        .start(launch(package.root.join("executor-state")))
        .expect("start bounded diagnostics");
    let diagnostics = wait_for_diagnostic(&manager, "diagnostic-complete");
    manager.stop().expect("stop bounded diagnostics");

    assert!(!diagnostics.is_empty());
    assert!(diagnostics.len() <= 200);
    assert!(diagnostics.iter().all(|line| line.len() <= 4096));
    assert!(diagnostics.iter().map(String::len).sum::<usize>() <= 64 * 1024);
    assert!(diagnostics.iter().all(|line| !line.contains("secret-")));
    assert!(diagnostics.iter().any(|line| line == "[TRUNCATED]"));
}

struct LaunchCounter {
    path: PathBuf,
}

impl LaunchCounter {
    fn new() -> Self {
        Self {
            path: std::env::temp_dir().join(format!(
                "automation-tool-e4-08-counter-{}-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .expect("system time")
                    .as_nanos(),
                TEMPORARY_PACKAGE_SEQUENCE.fetch_add(1, Ordering::Relaxed),
            )),
        }
    }

    fn value(&self) -> u8 {
        fs::read_to_string(&self.path)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(0)
    }

    fn wait_for(&self, expected: u8) {
        let deadline = std::time::Instant::now() + Duration::from_secs(3);
        while self.value() != expected && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(self.value(), expected);
    }
}

impl Drop for LaunchCounter {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

fn supervised_fixture(counter: &LaunchCounter, after_healthy: &str) -> String {
    let counter_path =
        serde_json::to_string(&counter.path.to_string_lossy()).expect("counter path");
    format!(
        r#"#!/usr/bin/env python3
import base64, hashlib, hmac, json, os, pathlib, signal, sys, time
counter = pathlib.Path({counter_path})
count = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(count))
bootstrap = json.loads(sys.stdin.readline())
key = bytes.fromhex(bootstrap["local_session_token"])
stopping = False
def proof(event):
    message = b"automation-tool.local-executor-event.v1\0" + event.encode() + b"\0" + b"1.0"
    return "atlep1." + base64.urlsafe_b64encode(hmac.digest(key, message, hashlib.sha256)).rstrip(b"=").decode()
def emit(event):
    print(json.dumps({{"authenticationProof": proof(event), "event": event, "protocolVersion": "1.0"}}, separators=(",", ":")), flush=True)
def stop(_signum, _frame):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM, stop)
emit("executor.healthy")
{after_healthy}
while not stopping:
    time.sleep(0.01)
emit("executor.stopped")
"#,
    )
}

fn supervised_process_tree_fixture(counter: &LaunchCounter, marker: &DescendantMarker) -> String {
    let counter_path =
        serde_json::to_string(&counter.path.to_string_lossy()).expect("counter path");
    let marker_path = serde_json::to_string(&marker.path.to_string_lossy()).expect("marker path");
    format!(
        r#"#!/usr/bin/env python3
import base64, hashlib, hmac, json, os, pathlib, signal, subprocess, sys, time
counter = pathlib.Path({counter_path})
count = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(count))
marker = pathlib.Path({marker_path})
bootstrap = json.loads(sys.stdin.readline())
key = bytes.fromhex(bootstrap["local_session_token"])
stopping = False
def proof(event):
    message = b"automation-tool.local-executor-event.v1\0" + event.encode() + b"\0" + b"1.0"
    return "atlep1." + base64.urlsafe_b64encode(hmac.digest(key, message, hashlib.sha256)).rstrip(b"=").decode()
def emit(event):
    print(json.dumps({{"authenticationProof": proof(event), "event": event, "protocolVersion": "1.0"}}, separators=(",", ":")), flush=True)
def stop(_signum, _frame):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM, stop)
descendant_source = "import os,pathlib,signal,sys,time; pathlib.Path(sys.argv[1]).open('a').write(str(os.getpid()) + '\\n'); signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
subprocess.Popen([sys.executable, "-c", descendant_source, {marker_path}], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while not marker.exists() or len(marker.read_text().splitlines()) < count:
    time.sleep(0.01)
emit("executor.healthy")
if count == 1:
    os.kill(os.getpid(), signal.SIGKILL)
while not stopping:
    time.sleep(0.01)
emit("executor.stopped")
"#,
    )
}

fn wait_for_state(manager: &ExecutorManager, expected: ExecutorManagerState) {
    let deadline = std::time::Instant::now() + Duration::from_secs(3);
    while manager.status().expect("supervised status").state() != expected
        && std::time::Instant::now() < deadline
    {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert_eq!(
        manager.status().expect("final supervised status").state(),
        expected,
    );
}

#[cfg(feature = "control-plane-e2e")]
fn wait_for_restart_count(manager: &ExecutorManager, expected: u8) {
    let deadline = std::time::Instant::now() + Duration::from_secs(3);
    loop {
        let status = manager.status().expect("supervised status");
        if status.state() == ExecutorManagerState::Running && status.restart_count() == expected {
            return;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "Executor did not finish its supervised restart"
        );
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(feature = "control-plane-e2e")]
#[test]
fn acceptance_fault_injection_crashes_recovers_and_hangs_the_real_process() {
    let package = TemporaryPackage::new(HEALTHY_FIXTURE);
    let manager = manager_for_root_with_timeouts(
        package.root.clone(),
        Duration::from_secs(10),
        Duration::from_millis(100),
    );

    manager
        .start(launch(package.root.join("executor-state")))
        .expect("start acceptance process");
    manager
        .inject_crash_for_acceptance()
        .expect("inject abnormal process exit");
    wait_for_restart_count(&manager, 1);

    manager
        .inject_hang_for_acceptance()
        .expect("suspend the real process");
    let error = manager.stop().expect_err("hung process stop must time out");
    assert_eq!(error.code(), ExecutorManagerErrorCode::TimedOut);
    assert_eq!(
        manager.status().expect("stopped after timeout").state(),
        ExecutorManagerState::Stopped,
    );
}

#[test]
fn background_supervisor_recovers_two_crashes_and_reports_the_consumed_budget() {
    let counter = LaunchCounter::new();
    let package = TemporaryPackage::new(&supervised_fixture(
        &counter,
        "if count <= 2: os.kill(os.getpid(), signal.SIGKILL)",
    ));
    let manager = manager(&package);

    manager
        .start(launch(package.root.join("executor-state")))
        .expect("initial start");
    counter.wait_for(3);
    wait_for_state(&manager, ExecutorManagerState::Running);
    assert_eq!(
        manager.status().expect("restarted status").restart_count(),
        2
    );

    manager.stop().expect("explicit stop after recovery");
    std::thread::sleep(Duration::from_millis(100));
    assert_eq!(counter.value(), 3);
}

#[test]
fn crash_recovery_cleans_the_previous_process_tree_before_relaunching() {
    let counter = LaunchCounter::new();
    let marker = DescendantMarker::new();
    let package = TemporaryPackage::new(&supervised_process_tree_fixture(&counter, &marker));
    let manager = manager(&package);

    manager
        .start(launch(package.root.join("executor-state")))
        .expect("initial process tree");
    counter.wait_for(2);
    let process_ids = marker.wait_for_count(2);
    wait_for_state(&manager, ExecutorManagerState::Running);

    marker.wait_for_exit(process_ids[0]);
    assert!(process_exists(process_ids[1]), "restarted descendant runs");
    manager.stop().expect("stop restarted process tree");
    marker.wait_for_exit(process_ids[1]);
}

#[test]
fn exhausted_restart_budget_converges_to_stopped_without_a_crash_loop() {
    let counter = LaunchCounter::new();
    let package = TemporaryPackage::new(&supervised_fixture(
        &counter,
        "os.kill(os.getpid(), signal.SIGKILL)",
    ));
    let manager = manager(&package);

    manager
        .start(launch(package.root.join("executor-state")))
        .expect("initial start");
    counter.wait_for(3);
    wait_for_state(&manager, ExecutorManagerState::Stopped);
    std::thread::sleep(Duration::from_millis(100));
    assert_eq!(counter.value(), 3);
}

#[test]
fn explicit_stop_never_consumes_restart_budget_or_relaunches() {
    let counter = LaunchCounter::new();
    let package = TemporaryPackage::new(&supervised_fixture(&counter, ""));
    let manager = manager(&package);

    manager
        .start(launch(package.root.join("executor-state")))
        .expect("initial start");
    counter.wait_for(1);
    manager.stop().expect("explicit stop");
    std::thread::sleep(Duration::from_millis(100));

    assert_eq!(counter.value(), 1);
    assert_eq!(manager.status().expect("stopped status").restart_count(), 0);
}

#[test]
fn normal_and_fixed_failure_exits_are_not_restartable() {
    for exit in ["sys.exit(0)", "sys.exit(1)"] {
        let counter = LaunchCounter::new();
        let package = TemporaryPackage::new(&supervised_fixture(&counter, exit));
        let manager = manager(&package);

        manager
            .start(launch(package.root.join("executor-state")))
            .expect("initial start");
        counter.wait_for(1);
        wait_for_state(&manager, ExecutorManagerState::Stopped);
        std::thread::sleep(Duration::from_millis(100));
        assert_eq!(counter.value(), 1);
    }
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

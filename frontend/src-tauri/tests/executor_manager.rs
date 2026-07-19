#![cfg(target_os = "macos")]

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Barrier};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

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
}

fn launch() -> ExecutorLaunchConfiguration {
    ExecutorLaunchConfiguration::new(
        "ws://127.0.0.1:8765/api/v1/executors/connect".to_owned(),
        "atds1.private-control-plane-session".to_owned(),
        "123e4567-e89b-42d3-a456-426614174003".to_owned(),
        "123e4567-e89b-42d3-a456-426614174004".to_owned(),
        1,
    )
    .expect("launch configuration")
}

#[test]
fn verified_process_starts_reports_status_and_stops_with_authenticated_events() {
    let package = TemporaryPackage::new(HEALTHY_FIXTURE);
    let manager = manager(&package);

    let started = manager.start(launch()).expect("start");
    assert_eq!(started.state(), ExecutorManagerState::Running);
    assert_eq!(started.version(), Some("0.1.0"));
    assert_eq!(started.build_id(), Some("e4-07-test"));
    assert_eq!(manager.status().expect("status"), started);

    let stopped = manager.stop().expect("stop");
    assert_eq!(stopped.state(), ExecutorManagerState::Stopped);
    assert_eq!(manager.stop().expect("idempotent stop"), stopped);
}

#[test]
fn concurrent_start_is_linearized_to_one_process() {
    let package = TemporaryPackage::new(HEALTHY_FIXTURE);
    let manager = manager(&package);
    let barrier = Arc::new(Barrier::new(8));
    let threads = (0..8)
        .map(|_| {
            let manager = Arc::clone(&manager);
            let barrier = Arc::clone(&barrier);
            std::thread::spawn(move || {
                barrier.wait();
                manager.start(launch())
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
        .start(launch())
        .expect_err("tampered package");
    assert_eq!(error.code(), ExecutorManagerErrorCode::PackageRejected);
    assert_eq!(error.to_string(), "Local Executor lifecycle is unavailable");
    assert!(!format!("{error:?}").contains("private"));

    let bad_proof = TemporaryPackage::new(BAD_PROOF_FIXTURE);
    let error = manager(&bad_proof).start(launch()).expect_err("bad proof");
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

    let error = manager.start(launch()).expect_err("silent startup timeout");
    assert_eq!(error.code(), ExecutorManagerErrorCode::TimedOut);
    assert_eq!(
        manager.status().expect("status after timeout").state(),
        ExecutorManagerState::Stopped,
    );
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

#[test]
fn background_supervisor_recovers_two_crashes_and_reports_the_consumed_budget() {
    let counter = LaunchCounter::new();
    let package = TemporaryPackage::new(&supervised_fixture(
        &counter,
        "if count <= 2: os.kill(os.getpid(), signal.SIGKILL)",
    ));
    let manager = manager(&package);

    manager.start(launch()).expect("initial start");
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
fn exhausted_restart_budget_converges_to_stopped_without_a_crash_loop() {
    let counter = LaunchCounter::new();
    let package = TemporaryPackage::new(&supervised_fixture(
        &counter,
        "os.kill(os.getpid(), signal.SIGKILL)",
    ));
    let manager = manager(&package);

    manager.start(launch()).expect("initial start");
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

    manager.start(launch()).expect("initial start");
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

        manager.start(launch()).expect("initial start");
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

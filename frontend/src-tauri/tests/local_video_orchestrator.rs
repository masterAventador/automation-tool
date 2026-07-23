#![cfg(unix)]

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::local_video_orchestrator::{
    LocalVideoOrchestrator, VideoWorkerErrorCode, VideoWorkerKind, VideoWorkerLaunch,
    VideoWorkerRestartPolicy, VideoWorkerState,
};
use uuid::Uuid;

static TEMPORARY_WORKER_SEQUENCE: AtomicU64 = AtomicU64::new(0);
const WORKER_VERSION: &str = "1.2.3";

const HEALTHY_WORKER: &str = r#"#!/usr/bin/env python3
import base64, hashlib, hmac, json, socket, sys, threading

bootstrap = json.loads(sys.stdin.readline())
key = bytes.fromhex(bootstrap["localSessionToken"])
kind = bootstrap["workerKind"]
protocol = bootstrap["protocolVersion"]
version = "1.2.3"

def proof(event, detail):
    message = b"automation-tool.video-worker-event.v1\0" + b"\0".join(
        value.encode() for value in [event, kind, protocol, version, detail]
    )
    digest = hmac.digest(key, message, hashlib.sha256)
    return "atvwp1." + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

server = socket.socket()
server.bind(("127.0.0.1", 0))
server.listen()
port = server.getsockname()[1]
stopping = False

def serve():
    while not stopping:
        try:
            connection, _ = server.accept()
        except OSError:
            return
        request = connection.recv(8192).decode(errors="replace")
        authorized = "Authorization: Bearer " + bootstrap["localSessionToken"] in request
        if authorized and request.startswith("GET /health HTTP/1.1"):
            body = json.dumps({
                "authenticationProof": proof("worker.health", str(port)),
                "event": "worker.health",
                "protocolVersion": protocol,
                "workerKind": kind,
                "workerVersion": version,
                "port": port,
            }, separators=(",", ":"))
            response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body.encode())) + "\r\nConnection: close\r\n\r\n" + body
        else:
            response = "HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        connection.sendall(response.encode())
        connection.close()

threading.Thread(target=serve, daemon=True).start()
print(json.dumps({
    "authenticationProof": proof("worker.ready", str(port)),
    "event": "worker.ready",
    "protocolVersion": protocol,
    "workerKind": kind,
    "workerVersion": version,
    "port": port,
}, separators=(",", ":")), flush=True)

for line in sys.stdin:
    command = json.loads(line)
    job_id = command.get("jobId", "")
    expected = hmac.digest(
        key,
        b"automation-tool.video-worker-command.v1\0" + b"\0".join(
            value.encode() for value in ["worker.cancel", kind, protocol, job_id]
        ),
        hashlib.sha256,
    )
    supplied = command.get("authenticationProof", "")
    encoded = "atvwc1." + base64.urlsafe_b64encode(expected).rstrip(b"=").decode()
    if command.get("command") == "worker.cancel" and hmac.compare_digest(supplied, encoded):
        print(json.dumps({
            "authenticationProof": proof("worker.cancelled", job_id),
            "event": "worker.cancelled",
            "jobId": job_id,
            "protocolVersion": protocol,
            "workerKind": kind,
            "workerVersion": version,
        }, separators=(",", ":")), flush=True)

stopping = True
server.close()
"#;

struct TemporaryWorker {
    root: PathBuf,
    executable: PathBuf,
}

impl TemporaryWorker {
    fn new(source: &str) -> Self {
        let root = std::env::temp_dir().join(format!(
            "automation-tool-vf02-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            TEMPORARY_WORKER_SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        fs::create_dir_all(&root).expect("worker root");
        let root = fs::canonicalize(root).expect("canonical worker root");
        let executable = root.join("video-worker");
        fs::write(&executable, source).expect("worker source");
        fs::set_permissions(&executable, fs::Permissions::from_mode(0o700))
            .expect("worker permissions");
        Self { root, executable }
    }

    fn launch(&self, kind: VideoWorkerKind) -> VideoWorkerLaunch {
        self.launch_with_policy(
            kind,
            VideoWorkerRestartPolicy::new(2, Duration::from_millis(10)).expect("restart policy"),
        )
    }

    fn launch_with_policy(
        &self,
        kind: VideoWorkerKind,
        restart_policy: VideoWorkerRestartPolicy,
    ) -> VideoWorkerLaunch {
        VideoWorkerLaunch::new(
            kind,
            self.executable.clone(),
            self.root.clone(),
            WORKER_VERSION.to_owned(),
            restart_policy,
        )
        .expect("worker launch")
    }
}

impl Drop for TemporaryWorker {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn orchestrator() -> LocalVideoOrchestrator {
    LocalVideoOrchestrator::new(Duration::from_secs(3), Duration::from_secs(3))
        .expect("orchestrator")
}

fn wait_until_stopped(process_id: u32) {
    let deadline = Instant::now() + Duration::from_secs(3);
    while process_exists(process_id) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(!process_exists(process_id), "worker process remained alive");
}

fn process_exists(process_id: u32) -> bool {
    let result = unsafe { libc::kill(process_id as i32, 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

#[test]
fn bundled_node_candidate_uses_packaged_runtime_and_protocol() {
    let Some(package_root) = std::env::var_os("BM02_PACKAGE_ROOT").map(PathBuf::from) else {
        return;
    };
    let asset_root = package_root.join("acceptance-assets");
    fs::create_dir(&asset_root).expect("asset root");
    let launch = VideoWorkerLaunch::bundled_node(
        &package_root,
        asset_root,
        VideoWorkerRestartPolicy::new(0, Duration::ZERO).expect("restart policy"),
    )
    .expect("bundled Node launch");
    let orchestrator = orchestrator();
    let status = orchestrator
        .start(launch)
        .expect("start bundled Node Worker");
    assert_eq!(status.state(), VideoWorkerState::Running);
    assert_eq!(status.worker_version(), Some("0.7.68"));
    assert_eq!(status.host(), Some("127.0.0.1"));
    orchestrator.health(VideoWorkerKind::Node).expect("health");
    orchestrator
        .cancel(
            VideoWorkerKind::Node,
            Uuid::parse_str("92cb8938-b8ad-4a32-8c32-f359beb20919").expect("UUID v4"),
        )
        .expect("authenticated cancellation");
    let process_id = status.process_id().expect("process id");
    orchestrator.stop(VideoWorkerKind::Node).expect("stop");
    wait_until_stopped(process_id);
}

fn crashing_worker(counter: &Path, crash_limit: &str) -> String {
    let counter_json = serde_json::to_string(&counter.to_string_lossy()).expect("counter path");
    HEALTHY_WORKER
        .replace(
            "print(json.dumps({\n    \"authenticationProof\": proof(\"worker.ready\", str(port)),",
            &format!(
                "import os, pathlib, time\n_counter = pathlib.Path({counter_json})\n_count = int(_counter.read_text()) + 1 if _counter.exists() else 1\n_counter.write_text(str(_count))\nprint(json.dumps({{\n    \"authenticationProof\": proof(\"worker.ready\", str(port)),"
            ),
        )
        .replace(
            "}, separators=(\",\", \":\")), flush=True)\n\nfor line in sys.stdin:",
            &format!(
                "}}, separators=(\",\", \":\")), flush=True)\nif {crash_limit}:\n    time.sleep(0.05)\n    os._exit(17)\n\nfor line in sys.stdin:"
            ),
        )
}

#[test]
fn starts_python_and_node_workers_on_distinct_authenticated_loopback_ports() {
    let fixture = TemporaryWorker::new(HEALTHY_WORKER);
    let orchestrator = orchestrator();

    let python = orchestrator
        .start(fixture.launch(VideoWorkerKind::Python))
        .expect("start Python worker");
    let node = orchestrator
        .start(fixture.launch(VideoWorkerKind::Node))
        .expect("start Node worker");

    assert_eq!(python.state(), VideoWorkerState::Running);
    assert_eq!(python.worker_version(), Some(WORKER_VERSION));
    assert_eq!(python.host(), Some("127.0.0.1"));
    assert_ne!(python.port(), node.port());
    assert!(python.port().is_some_and(|port| port > 0));
    assert!(node.port().is_some_and(|port| port > 0));
    orchestrator
        .health(VideoWorkerKind::Python)
        .expect("authenticated Python health");
    orchestrator
        .health(VideoWorkerKind::Node)
        .expect("authenticated Node health");

    let job_id = Uuid::parse_str("123e4567-e89b-42d3-a456-426614174100").expect("job ID");
    orchestrator
        .cancel(VideoWorkerKind::Python, job_id)
        .expect("authenticated cancellation");
    let python_process_id = python.process_id().expect("Python process ID");
    let node_process_id = node.process_id().expect("Node process ID");
    orchestrator.stop_all().expect("stop both workers");
    wait_until_stopped(python_process_id);
    wait_until_stopped(node_process_id);
}

#[test]
fn rejects_relative_symlink_duplicate_and_version_mismatch_launches() {
    let fixture = TemporaryWorker::new(HEALTHY_WORKER);
    let relative = VideoWorkerLaunch::new(
        VideoWorkerKind::Python,
        Path::new("video-worker").to_path_buf(),
        fixture.root.clone(),
        WORKER_VERSION.to_owned(),
        VideoWorkerRestartPolicy::new(1, Duration::from_millis(10)).expect("restart policy"),
    )
    .err()
    .expect("relative worker must be rejected");
    assert_eq!(relative.code(), VideoWorkerErrorCode::ConfigurationInvalid);

    let relative_asset_root = VideoWorkerLaunch::new(
        VideoWorkerKind::Python,
        fixture.executable.clone(),
        Path::new("assets").to_path_buf(),
        WORKER_VERSION.to_owned(),
        VideoWorkerRestartPolicy::new(1, Duration::from_millis(10)).expect("restart policy"),
    )
    .err()
    .expect("relative asset root must be rejected");
    assert_eq!(
        relative_asset_root.code(),
        VideoWorkerErrorCode::ConfigurationInvalid
    );

    let symlink = fixture.root.join("worker-link");
    std::os::unix::fs::symlink(&fixture.executable, &symlink).expect("worker symlink");
    let linked = VideoWorkerLaunch::new(
        VideoWorkerKind::Python,
        symlink,
        fixture.root.clone(),
        WORKER_VERSION.to_owned(),
        VideoWorkerRestartPolicy::new(1, Duration::from_millis(10)).expect("restart policy"),
    )
    .err()
    .expect("symlink worker must be rejected");
    assert_eq!(linked.code(), VideoWorkerErrorCode::ConfigurationInvalid);

    let asset_link = fixture.root.parent().expect("worker parent").join(format!(
        "automation-tool-vf02-asset-link-{}-{}",
        std::process::id(),
        TEMPORARY_WORKER_SEQUENCE.fetch_add(1, Ordering::Relaxed),
    ));
    std::os::unix::fs::symlink(&fixture.root, &asset_link).expect("asset root symlink");
    let linked_asset = VideoWorkerLaunch::new(
        VideoWorkerKind::Python,
        fixture.executable.clone(),
        asset_link.clone(),
        WORKER_VERSION.to_owned(),
        VideoWorkerRestartPolicy::new(1, Duration::from_millis(10)).expect("restart policy"),
    )
    .err()
    .expect("linked asset root must be rejected");
    assert_eq!(
        linked_asset.code(),
        VideoWorkerErrorCode::ConfigurationInvalid
    );
    fs::remove_file(asset_link).expect("remove asset root symlink");

    let ancestor_link = fixture.root.parent().expect("worker parent").join(format!(
        "automation-tool-vf02-parent-link-{}-{}",
        std::process::id(),
        TEMPORARY_WORKER_SEQUENCE.fetch_add(1, Ordering::Relaxed),
    ));
    std::os::unix::fs::symlink(&fixture.root, &ancestor_link).expect("ancestor symlink");
    let replaced_ancestor = VideoWorkerLaunch::new(
        VideoWorkerKind::Python,
        ancestor_link.join("video-worker"),
        fixture.root.clone(),
        WORKER_VERSION.to_owned(),
        VideoWorkerRestartPolicy::new(1, Duration::from_millis(10)).expect("restart policy"),
    )
    .err()
    .expect("symlinked ancestor must be rejected");
    assert_eq!(
        replaced_ancestor.code(),
        VideoWorkerErrorCode::ConfigurationInvalid,
    );
    fs::remove_file(&ancestor_link).expect("remove ancestor symlink");

    let orchestrator = orchestrator();
    orchestrator
        .start(fixture.launch(VideoWorkerKind::Python))
        .expect("first start");
    let duplicate = orchestrator
        .start(fixture.launch(VideoWorkerKind::Python))
        .expect_err("duplicate kind must be rejected");
    assert_eq!(duplicate.code(), VideoWorkerErrorCode::AlreadyRunning);

    let mismatch = VideoWorkerLaunch::new(
        VideoWorkerKind::Node,
        fixture.executable.clone(),
        fixture.root.clone(),
        "9.9.9".to_owned(),
        VideoWorkerRestartPolicy::new(1, Duration::from_millis(10)).expect("restart policy"),
    )
    .expect("mismatch launch");
    let error = orchestrator
        .start(mismatch)
        .expect_err("version mismatch must fail closed");
    assert_eq!(error.code(), VideoWorkerErrorCode::VersionMismatch);
    assert_eq!(
        orchestrator
            .status(VideoWorkerKind::Node)
            .expect("stopped Node status")
            .state(),
        VideoWorkerState::Stopped,
    );
}

#[test]
fn restarts_once_after_a_real_crash_and_drop_stops_the_replacement() {
    let counter_root = std::env::temp_dir().join(format!(
        "automation-tool-vf02-counter-{}-{}",
        std::process::id(),
        TEMPORARY_WORKER_SEQUENCE.fetch_add(1, Ordering::Relaxed),
    ));
    let crashing = crashing_worker(&counter_root, "_count == 1");
    let fixture = TemporaryWorker::new(&crashing);
    let orchestrator = orchestrator();
    orchestrator
        .start(fixture.launch(VideoWorkerKind::Python))
        .expect("initial start");

    let deadline = Instant::now() + Duration::from_secs(3);
    let recovered = loop {
        let status = orchestrator
            .status(VideoWorkerKind::Python)
            .expect("reconcile crashed worker");
        if status.state() == VideoWorkerState::Running && status.restart_count() == 1 {
            break status;
        }
        assert!(Instant::now() < deadline, "worker did not recover");
        std::thread::sleep(Duration::from_millis(10));
    };
    let replacement_process_id = recovered.process_id().expect("replacement process ID");
    drop(orchestrator);
    wait_until_stopped(replacement_process_id);
    let _ = fs::remove_file(counter_root);
}

#[test]
fn rejects_forged_ready_proof_and_cleans_the_failed_process() {
    let marker = std::env::temp_dir().join(format!(
        "automation-tool-vf02-forged-{}-{}",
        std::process::id(),
        TEMPORARY_WORKER_SEQUENCE.fetch_add(1, Ordering::Relaxed),
    ));
    let marker_json = serde_json::to_string(&marker.to_string_lossy()).expect("marker path");
    let forged = HEALTHY_WORKER
        .replace(
            "bootstrap = json.loads(sys.stdin.readline())",
            &format!(
                "import os, pathlib\npathlib.Path({marker_json}).write_text(str(os.getpid()))\nbootstrap = json.loads(sys.stdin.readline())"
            ),
        )
        .replace(
            "return \"atvwp1.\" + base64.urlsafe_b64encode(digest).rstrip(b\"=\").decode()",
            "return \"atvwp1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\"",
        );
    let fixture = TemporaryWorker::new(&forged);
    let error = orchestrator()
        .start(fixture.launch(VideoWorkerKind::Python))
        .expect_err("forged proof must fail closed");
    assert_eq!(error.code(), VideoWorkerErrorCode::AuthenticationRejected);
    let process_id = fs::read_to_string(&marker)
        .expect("failed worker marker")
        .parse()
        .expect("failed worker process ID");
    wait_until_stopped(process_id);
    let _ = fs::remove_file(marker);
}

#[test]
fn startup_timeout_fails_closed() {
    let silent = r#"#!/bin/sh
IFS= read -r bootstrap
sleep 30
"#;
    let fixture = TemporaryWorker::new(silent);
    let orchestrator =
        LocalVideoOrchestrator::new(Duration::from_millis(500), Duration::from_millis(500))
            .expect("short-timeout orchestrator");
    let error = orchestrator
        .start(fixture.launch(VideoWorkerKind::Python))
        .expect_err("silent worker must time out");
    assert_eq!(error.code(), VideoWorkerErrorCode::TimedOut);
}

#[test]
fn stops_after_the_crash_recovery_budget_is_exhausted() {
    let counter = std::env::temp_dir().join(format!(
        "automation-tool-vf02-exhausted-{}-{}",
        std::process::id(),
        TEMPORARY_WORKER_SEQUENCE.fetch_add(1, Ordering::Relaxed),
    ));
    let fixture = TemporaryWorker::new(&crashing_worker(&counter, "True"));
    let orchestrator = orchestrator();
    let policy =
        VideoWorkerRestartPolicy::new(1, Duration::from_millis(10)).expect("one restart policy");
    orchestrator
        .start(fixture.launch_with_policy(VideoWorkerKind::Python, policy))
        .expect("initial worker");

    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        let status = orchestrator
            .status(VideoWorkerKind::Python)
            .expect("reconcile finite restarts");
        if status.state() == VideoWorkerState::Stopped {
            assert_eq!(status.restart_count(), 1);
            break;
        }
        assert!(
            Instant::now() < deadline,
            "restart budget was not exhausted"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
    assert_eq!(fs::read_to_string(&counter).expect("launch count"), "2",);
    let _ = fs::remove_file(counter);
}

#[test]
fn crash_recovery_kills_descendants_before_joining_worker_pipes() {
    let counter = std::env::temp_dir().join(format!(
        "automation-tool-vf02-tree-counter-{}-{}",
        std::process::id(),
        TEMPORARY_WORKER_SEQUENCE.fetch_add(1, Ordering::Relaxed),
    ));
    let descendant_marker = std::env::temp_dir().join(format!(
        "automation-tool-vf02-tree-child-{}-{}",
        std::process::id(),
        TEMPORARY_WORKER_SEQUENCE.fetch_add(1, Ordering::Relaxed),
    ));
    let marker_json =
        serde_json::to_string(&descendant_marker.to_string_lossy()).expect("descendant marker");
    let source = crashing_worker(&counter, "_count == 1").replace(
        "    time.sleep(0.05)\n    os._exit(17)",
        &format!(
            "    import subprocess\n    child = subprocess.Popen([\"sleep\", \"5\"])\n    pathlib.Path({marker_json}).write_text(str(child.pid))\n    time.sleep(0.05)\n    os._exit(17)"
        ),
    );
    let fixture = TemporaryWorker::new(&source);
    let orchestrator = orchestrator();
    orchestrator
        .start(fixture.launch(VideoWorkerKind::Python))
        .expect("initial worker");

    let marker_deadline = Instant::now() + Duration::from_secs(2);
    while !descendant_marker.exists() && Instant::now() < marker_deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    let descendant_process_id = fs::read_to_string(&descendant_marker)
        .expect("descendant process marker")
        .parse()
        .expect("descendant process ID");
    let recovery_started = Instant::now();
    let deadline = recovery_started + Duration::from_secs(2);
    loop {
        let status = orchestrator
            .status(VideoWorkerKind::Python)
            .expect("recover process tree crash");
        if status.restart_count() == 1 {
            break;
        }
        assert!(Instant::now() < deadline, "worker tree recovery timed out");
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(
        recovery_started.elapsed() < Duration::from_secs(2),
        "recovery waited for a leaked descendant to close inherited pipes",
    );
    wait_until_stopped(descendant_process_id);
    let _ = fs::remove_file(counter);
    let _ = fs::remove_file(descendant_marker);
}

#[test]
fn tauri_composition_root_owns_the_orchestrator_without_a_webview_command() {
    let source = fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("src")
            .join("lib.rs"),
    )
    .expect("Tauri composition root");
    assert!(source.contains("app.manage(local_video_orchestrator::LocalVideoOrchestrator::new("));
    assert!(!source.contains("start_video_worker"));
    assert!(!source.contains("stop_video_worker"));
}

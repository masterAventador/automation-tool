#![cfg(unix)]

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::local_video_orchestrator::{
    LocalVideoOrchestrator, VideoWorkerErrorCode, VideoWorkerKind, VideoWorkerLaunch,
    VideoWorkerRenderBrowserConfiguration, VideoWorkerRenderCanvas, VideoWorkerRenderSandboxRequest,
    VideoWorkerRestartPolicy, VideoWorkerState,
};
use automation_tool_desktop_lib::motion_video_studio::{
    cancel_marker_file_name, TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR, TEMPLATE_CANVAS_HEIGHT,
    TEMPLATE_CANVAS_WIDTH,
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
    // Ten seconds keeps slow interpreter start-up under full parallel test
    // load from being misread as a worker start timeout.
    LocalVideoOrchestrator::new(Duration::from_secs(10), Duration::from_secs(10))
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
#[ignore = "requires the BM-02 packaged Node candidate; run via scripts/run_bm_02_acceptance.py"]
fn bundled_node_candidate_uses_packaged_runtime_and_protocol() {
    let package_root = std::env::var_os("BM02_PACKAGE_ROOT")
        .map(PathBuf::from)
        .expect("BM02_PACKAGE_ROOT is staged by scripts/run_bm_02_acceptance.py");
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

/// Extend the healthy fake with `worker.render.verify` support so the Rust
/// render channel can be proven end to end: the fake records the bootstrap
/// `renderBrowser.executablePath` into a marker file and answers with either
/// a verified event (major taken from the bootstrap) or a failed event.
fn render_worker(marker: &Path, failure_reason: Option<&str>, forge_proof: bool) -> String {
    let marker_json = serde_json::to_string(&marker.to_string_lossy()).expect("marker path");
    let reply = match failure_reason {
        Some(reason) => format!(
            "        detail = job_id + \"\\0\" + \"{reason}\"\n\
             \x20       body = {{\n\
             \x20           \"authenticationProof\": proof(\"worker.render.failed\", detail),\n\
             \x20           \"event\": \"worker.render.failed\",\n\
             \x20           \"jobId\": job_id,\n\
             \x20           \"protocolVersion\": protocol,\n\
             \x20           \"reasonCode\": \"{reason}\",\n\
             \x20           \"workerKind\": kind,\n\
             \x20           \"workerVersion\": version,\n\
             \x20       }}\n"
        ),
        None => "        major = browser[\"chromiumMajor\"]\n\
             \x20       detail = job_id + \"\\0\" + str(major)\n\
             \x20       body = {\n\
             \x20           \"authenticationProof\": proof(\"worker.render.verified\", detail),\n\
             \x20           \"chromiumMajor\": major,\n\
             \x20           \"event\": \"worker.render.verified\",\n\
             \x20           \"jobId\": job_id,\n\
             \x20           \"protocolVersion\": protocol,\n\
             \x20           \"workerKind\": kind,\n\
             \x20           \"workerVersion\": version,\n\
             \x20       }\n"
            .to_owned(),
    };
    let forge = if forge_proof {
        "        body[\"authenticationProof\"] = \"atvwp1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\"\n"
    } else {
        ""
    };
    HEALTHY_WORKER.replace(
        "    if command.get(\"command\") == \"worker.cancel\" and hmac.compare_digest(supplied, encoded):",
        &format!(
            "    if command.get(\"command\") == \"worker.render.verify\":\n\
             \x20       import pathlib\n\
             \x20       render_expected = hmac.digest(\n\
             \x20           key,\n\
             \x20           b\"automation-tool.video-worker-command.v1\\0\" + b\"\\0\".join(\n\
             \x20               value.encode() for value in [\"worker.render.verify\", kind, protocol, job_id]\n\
             \x20           ),\n\
             \x20           hashlib.sha256,\n\
             \x20       )\n\
             \x20       render_encoded = \"atvwc1.\" + base64.urlsafe_b64encode(render_expected).rstrip(b\"=\").decode()\n\
             \x20       if not hmac.compare_digest(supplied, render_encoded):\n\
             \x20           continue\n\
             \x20       browser = bootstrap[\"renderBrowser\"]\n\
             \x20       pathlib.Path({marker_json}).write_text(browser[\"executablePath\"])\n\
             {reply}{forge}\
             \x20       print(json.dumps(body, separators=(\",\", \":\")), flush=True)\n\
             \x20       continue\n\
             \x20   if command.get(\"command\") == \"worker.cancel\" and hmac.compare_digest(supplied, encoded):"
        ),
    )
}

fn executable_fixture(root: &Path, name: &str) -> PathBuf {
    let path = root.join(name);
    fs::write(&path, "#!/bin/sh\nexit 0\n").expect("fixture executable");
    fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).expect("fixture permissions");
    path
}

fn render_configuration(executable: &Path) -> VideoWorkerRenderBrowserConfiguration {
    VideoWorkerRenderBrowserConfiguration::new(
        executable.to_path_buf(),
        149,
        Duration::from_secs(20),
    )
    .expect("render browser configuration")
}

/// Extend the healthy fake with `worker.render.sandbox` support. The fake
/// re-derives the canonical-sandbox-bound command proof (proving the Rust
/// producer bound the HMAC to the exact same canonical JSON), then answers
/// with a `worker.render.sandboxed` event whose block counters are fixed and
/// whose `framesCaptured` echoes the requested `frameCount`. When `forge_proof`
/// is set it corrupts the event proof so the Rust side must fail closed.
fn sandbox_worker(forge_proof: bool) -> String {
    let forge = if forge_proof {
        "        body[\"authenticationProof\"] = \"atvwp1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\"\n"
    } else {
        ""
    };
    HEALTHY_WORKER.replace(
        "    if command.get(\"command\") == \"worker.cancel\" and hmac.compare_digest(supplied, encoded):",
        &format!(
            "    if command.get(\"command\") == \"worker.render.sandbox\":\n\
             \x20       canonical = json.dumps(\n\
             \x20           command[\"sandbox\"], sort_keys=True, separators=(\",\", \":\"), ensure_ascii=False\n\
             \x20       )\n\
             \x20       sandbox_expected = hmac.digest(\n\
             \x20           key,\n\
             \x20           b\"automation-tool.video-worker-command.v1\\0\" + b\"\\0\".join(\n\
             \x20               value.encode()\n\
             \x20               for value in [\"worker.render.sandbox\", kind, protocol, job_id, canonical]\n\
             \x20           ),\n\
             \x20           hashlib.sha256,\n\
             \x20       )\n\
             \x20       sandbox_encoded = \"atvwc1.\" + base64.urlsafe_b64encode(sandbox_expected).rstrip(b\"=\").decode()\n\
             \x20       if not hmac.compare_digest(supplied, sandbox_encoded):\n\
             \x20           continue\n\
             \x20       major = bootstrap[\"renderBrowser\"][\"chromiumMajor\"]\n\
             \x20       frames = command[\"sandbox\"][\"frameCount\"]\n\
             \x20       detail = \"\\0\".join(str(part) for part in [job_id, major, frames, 4096, 3, 1, 1, 1, 1])\n\
             \x20       body = {{\n\
             \x20           \"authenticationProof\": proof(\"worker.render.sandboxed\", detail),\n\
             \x20           \"blockedDialogs\": 1,\n\
             \x20           \"blockedDownloads\": 1,\n\
             \x20           \"blockedNavigations\": 1,\n\
             \x20           \"blockedPopups\": 1,\n\
             \x20           \"blockedRequests\": 3,\n\
             \x20           \"chromiumMajor\": major,\n\
             \x20           \"event\": \"worker.render.sandboxed\",\n\
             \x20           \"framesCaptured\": frames,\n\
             \x20           \"jobId\": job_id,\n\
             \x20           \"outputBytes\": 4096,\n\
             \x20           \"protocolVersion\": protocol,\n\
             \x20           \"workerKind\": kind,\n\
             \x20           \"workerVersion\": version,\n\
             \x20       }}\n\
             {forge}\
             \x20       print(json.dumps(body, separators=(\",\", \":\")), flush=True)\n\
             \x20       continue\n\
             \x20   if command.get(\"command\") == \"worker.cancel\" and hmac.compare_digest(supplied, encoded):"
        ),
    )
}

fn sandbox_request(workspace: &Path) -> VideoWorkerRenderSandboxRequest {
    VideoWorkerRenderSandboxRequest::new(
        workspace.to_path_buf(),
        "entry.html".to_owned(),
        cancel_marker_file_name()
            .expect("declared cancellation marker")
            .to_owned(),
        VideoWorkerRenderCanvas::new(
            TEMPLATE_CANVAS_WIDTH,
            TEMPLATE_CANVAS_HEIGHT,
            TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR,
        )
        .expect("the template canvas is inside the declared bounds"),
        vec!["assets/style.css".to_owned(), "assets/logo.png".to_owned()],
        6,
        20,
        20,
        1024,
        50_000_000,
    )
    .expect("sandbox request")
}

#[test]
fn rejects_invalid_render_browser_configurations() {
    let fixture = TemporaryWorker::new(HEALTHY_WORKER);
    let executable = executable_fixture(&fixture.root, "fake-chromium");
    let plain = fixture.root.join("plain-file");
    fs::write(&plain, "not executable").expect("plain file");
    let link = fixture.root.join("chromium-link");
    std::os::unix::fs::symlink(&executable, &link).expect("chromium symlink");

    let invalid = [
        (
            Path::new("fake-chromium").to_path_buf(),
            149,
            Duration::from_secs(20),
        ),
        (
            fixture.root.join("missing-chromium"),
            149,
            Duration::from_secs(20),
        ),
        (plain, 149, Duration::from_secs(20)),
        (link, 149, Duration::from_secs(20)),
        (executable.clone(), 99, Duration::from_secs(20)),
        (executable.clone(), 1000, Duration::from_secs(20)),
        (executable.clone(), 149, Duration::ZERO),
        (executable.clone(), 149, Duration::from_secs(61)),
        (executable.clone(), 149, Duration::from_millis(1500)),
    ];
    for (path, major, timeout) in invalid {
        let error = VideoWorkerRenderBrowserConfiguration::new(path, major, timeout)
            .err()
            .expect("invalid render browser configuration must be rejected");
        assert_eq!(error.code(), VideoWorkerErrorCode::ConfigurationInvalid);
    }
    render_configuration(&executable);
}

#[test]
fn render_bootstrap_channel_round_trips_through_a_worker() {
    let marker = std::env::temp_dir().join(format!(
        "automation-tool-bm03-marker-{}-{}",
        std::process::id(),
        TEMPORARY_WORKER_SEQUENCE.fetch_add(1, Ordering::Relaxed),
    ));
    let fixture = TemporaryWorker::new(&render_worker(&marker, None, false));
    let browser = executable_fixture(&fixture.root, "fake-chromium");
    let orchestrator = orchestrator();
    let status = orchestrator
        .start(
            fixture
                .launch(VideoWorkerKind::Node)
                .with_render_browser(render_configuration(&browser)),
        )
        .expect("start render-capable worker");
    let job_id = Uuid::parse_str("6f1d9fbc-4b64-4f0e-9f0d-3a6a1b2c4d5e").expect("job ID");
    let major = orchestrator
        .render_verify(VideoWorkerKind::Node, job_id)
        .expect("authenticated render verification");
    assert_eq!(major, 149);
    assert_eq!(
        fs::read_to_string(&marker).expect("render marker"),
        browser.to_string_lossy(),
        "worker must receive exactly the Rust-verified Chromium path",
    );
    let process_id = status.process_id().expect("process id");
    orchestrator.stop(VideoWorkerKind::Node).expect("stop");
    wait_until_stopped(process_id);
    let _ = fs::remove_file(marker);
}

#[test]
fn render_failure_and_forged_render_proof_fail_closed() {
    let marker = std::env::temp_dir().join(format!(
        "automation-tool-bm03-failed-{}-{}",
        std::process::id(),
        TEMPORARY_WORKER_SEQUENCE.fetch_add(1, Ordering::Relaxed),
    ));
    let job_id = Uuid::parse_str("0b7c9d28-6c4f-4d34-9f6a-2b1e0c9d8e7f").expect("job ID");

    let failing = TemporaryWorker::new(&render_worker(
        &marker,
        Some("chromium_major_mismatch"),
        false,
    ));
    let browser = executable_fixture(&failing.root, "fake-chromium");
    let orchestrator = orchestrator();
    orchestrator
        .start(
            failing
                .launch(VideoWorkerKind::Node)
                .with_render_browser(render_configuration(&browser)),
        )
        .expect("start failing render worker");
    let error = orchestrator
        .render_verify(VideoWorkerKind::Node, job_id)
        .expect_err("render failure must fail closed");
    assert_eq!(error.code(), VideoWorkerErrorCode::RenderRejected);
    orchestrator
        .stop(VideoWorkerKind::Node)
        .expect("stop failing worker");

    let forged = TemporaryWorker::new(&render_worker(&marker, None, true));
    let forged_browser = executable_fixture(&forged.root, "fake-chromium");
    orchestrator
        .start(
            forged
                .launch(VideoWorkerKind::Node)
                .with_render_browser(render_configuration(&forged_browser)),
        )
        .expect("start forged render worker");
    let error = orchestrator
        .render_verify(VideoWorkerKind::Node, job_id)
        .expect_err("forged render proof must fail closed");
    assert_eq!(error.code(), VideoWorkerErrorCode::AuthenticationRejected);
    orchestrator
        .stop(VideoWorkerKind::Node)
        .expect("stop forged worker");
    let _ = fs::remove_file(marker);
}

#[test]
fn render_verify_without_a_configured_browser_is_rejected_without_ipc() {
    let fixture = TemporaryWorker::new(HEALTHY_WORKER);
    let orchestrator = orchestrator();
    orchestrator
        .start(fixture.launch(VideoWorkerKind::Node))
        .expect("start renderless worker");
    let error = orchestrator
        .render_verify(
            VideoWorkerKind::Node,
            Uuid::parse_str("9e8d7c6b-5a49-4321-8765-4321fedcba98").expect("job ID"),
        )
        .expect_err("render verify without a configured browser must be rejected");
    assert_eq!(error.code(), VideoWorkerErrorCode::ConfigurationInvalid);
    orchestrator
        .health(VideoWorkerKind::Node)
        .expect("worker must stay healthy");
}

#[test]
fn render_sandbox_rejects_invalid_requests() {
    let root = std::env::temp_dir();
    let invalid: [(&str, Vec<&str>, u32, u32, u32, u32, u64); 8] = [
        (
            "entry.html",
            vec!["assets/style.css"],
            0,
            20,
            20,
            1024,
            50_000_000,
        ),
        (
            "entry.html",
            vec!["assets/style.css"],
            601,
            20,
            20,
            1024,
            50_000_000,
        ),
        (
            "entry.html",
            vec!["assets/style.css"],
            6,
            0,
            20,
            1024,
            50_000_000,
        ),
        (
            "entry.html",
            vec!["assets/style.css"],
            6,
            20,
            20,
            64,
            50_000_000,
        ),
        ("entry.html", vec!["assets/style.css"], 6, 20, 20, 1024, 0),
        (
            "../escape.html",
            vec!["assets/style.css"],
            6,
            20,
            20,
            1024,
            50_000_000,
        ),
        (
            "/absolute.html",
            vec!["assets/style.css"],
            6,
            20,
            20,
            1024,
            50_000_000,
        ),
        (
            "entry.html",
            vec!["../secret.css"],
            6,
            20,
            20,
            1024,
            50_000_000,
        ),
    ];
    for (entry, assets, frames, duration, cpu, memory, output) in invalid {
        let error = VideoWorkerRenderSandboxRequest::new(
            root.join("workspace"),
            entry.to_owned(),
            cancel_marker_file_name()
                .expect("declared cancellation marker")
                .to_owned(),
        VideoWorkerRenderCanvas::new(
            TEMPLATE_CANVAS_WIDTH,
            TEMPLATE_CANVAS_HEIGHT,
            TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR,
        )
        .expect("the template canvas is inside the declared bounds"),
            assets.into_iter().map(str::to_owned).collect(),
            frames,
            duration,
            cpu,
            memory,
            output,
        )
        .err()
        .expect("invalid sandbox request must be rejected");
        assert_eq!(error.code(), VideoWorkerErrorCode::ConfigurationInvalid);
    }
    // A relative workspace is also rejected.
    let error = VideoWorkerRenderSandboxRequest::new(
        PathBuf::from("relative/workspace"),
        "entry.html".to_owned(),
        cancel_marker_file_name()
            .expect("declared cancellation marker")
            .to_owned(),
        VideoWorkerRenderCanvas::new(
            TEMPLATE_CANVAS_WIDTH,
            TEMPLATE_CANVAS_HEIGHT,
            TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR,
        )
        .expect("the template canvas is inside the declared bounds"),
        Vec::new(),
        6,
        20,
        20,
        1024,
        50_000_000,
    )
    .err()
    .expect("relative workspace must be rejected");
    assert_eq!(error.code(), VideoWorkerErrorCode::ConfigurationInvalid);
}

/// The declared ceiling on average core occupancy for one render, stated here
/// independently of the orchestrator and kept in step with
/// `contracts/video/motion-render-sandbox-budget.v1.json`.
const SANDBOX_CPU_PARALLELISM_MAXIMUM: u32 = 8;

/// CPU seconds and wall-clock seconds are different quantities: a render that
/// saturates several cores accrues CPU seconds many times faster than wall
/// clock. The admissible CPU budget is therefore the wall-clock budget times
/// the declared maximum average core occupancy — sharing the wall-clock ceiling
/// both rejects legitimate multi-core budgets and, on short budgets, admits a
/// figure no host can reach, leaving the CPU guard inert.
#[test]
fn render_sandbox_scales_the_cpu_budget_with_the_wall_clock_budget() {
    let root = std::env::temp_dir();
    let request = |duration: u32, cpu: u32| {
        VideoWorkerRenderSandboxRequest::new(
            root.join("workspace"),
            "entry.html".to_owned(),
            cancel_marker_file_name()
                .expect("declared cancellation marker")
                .to_owned(),
        VideoWorkerRenderCanvas::new(
            TEMPLATE_CANVAS_WIDTH,
            TEMPLATE_CANVAS_HEIGHT,
            TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR,
        )
        .expect("the template canvas is inside the declared bounds"),
            vec!["assets/style.css".to_owned()],
            6,
            duration,
            cpu,
            1024,
            50_000_000,
        )
    };
    let parallelism = SANDBOX_CPU_PARALLELISM_MAXIMUM;
    for (duration, cpu) in [
        (120, 480),
        (120, 120 * parallelism),
        (1, parallelism),
        (300, 300 * parallelism),
    ] {
        assert!(
            request(duration, cpu).is_ok(),
            "cpu={cpu} inside a {duration}s wall budget must be accepted"
        );
    }
    for (duration, cpu) in [
        (1, parallelism + 1),
        (1, 300),
        (30, 300),
        (120, 120 * parallelism + 1),
    ] {
        let error = request(duration, cpu)
            .expect_err("a CPU budget beyond the wall budget must be rejected");
        assert_eq!(
            error.code(),
            VideoWorkerErrorCode::ConfigurationInvalid,
            "cpu={cpu} beyond a {duration}s wall budget must be rejected"
        );
    }
}

#[test]
fn render_sandbox_binds_canonical_proof_and_parses_summary() {
    let fixture = TemporaryWorker::new(&sandbox_worker(false));
    let browser = executable_fixture(&fixture.root, "fake-chromium");
    let orchestrator = orchestrator();
    orchestrator
        .start(
            fixture
                .launch(VideoWorkerKind::Node)
                .with_render_browser(render_configuration(&browser)),
        )
        .expect("start sandbox-capable worker");
    let job_id = Uuid::parse_str("5c9f1e0a-2d3b-4c5d-8e6f-7a8b9c0d1e2f").expect("job ID");
    let summary = orchestrator
        .render_sandbox(
            VideoWorkerKind::Node,
            job_id,
            &sandbox_request(&fixture.root),
        )
        .expect("authenticated render sandbox summary");
    assert_eq!(summary.chromium_major, 149);
    assert_eq!(summary.frames_captured, 6);
    assert_eq!(summary.output_bytes, 4096);
    assert_eq!(summary.blocked_requests, 3);
    assert_eq!(summary.blocked_navigations, 1);
    assert_eq!(summary.blocked_downloads, 1);
    assert_eq!(summary.blocked_popups, 1);
    assert_eq!(summary.blocked_dialogs, 1);
    orchestrator.stop(VideoWorkerKind::Node).expect("stop");
}

#[test]
fn render_sandbox_forged_summary_proof_fails_closed() {
    let fixture = TemporaryWorker::new(&sandbox_worker(true));
    let browser = executable_fixture(&fixture.root, "fake-chromium");
    let orchestrator = orchestrator();
    orchestrator
        .start(
            fixture
                .launch(VideoWorkerKind::Node)
                .with_render_browser(render_configuration(&browser)),
        )
        .expect("start forged sandbox worker");
    let job_id = Uuid::parse_str("11112222-3333-4444-8555-666677778888").expect("job ID");
    let error = orchestrator
        .render_sandbox(
            VideoWorkerKind::Node,
            job_id,
            &sandbox_request(&fixture.root),
        )
        .expect_err("forged sandbox summary must fail closed");
    assert_eq!(error.code(), VideoWorkerErrorCode::AuthenticationRejected);
    orchestrator.stop(VideoWorkerKind::Node).expect("stop");
}

#[test]
fn render_sandbox_without_a_configured_browser_is_rejected_without_ipc() {
    let fixture = TemporaryWorker::new(HEALTHY_WORKER);
    let orchestrator = orchestrator();
    orchestrator
        .start(fixture.launch(VideoWorkerKind::Node))
        .expect("start renderless worker");
    let error = orchestrator
        .render_sandbox(
            VideoWorkerKind::Node,
            Uuid::parse_str("aaaabbbb-cccc-4ddd-8eee-ffff00001111").expect("job ID"),
            &sandbox_request(&fixture.root),
        )
        .expect_err("render sandbox without a configured browser must be rejected");
    assert_eq!(error.code(), VideoWorkerErrorCode::ConfigurationInvalid);
    orchestrator
        .health(VideoWorkerKind::Node)
        .expect("worker must stay healthy");
}

/// Real vertical chain, activated by `scripts/run_bm_03_acceptance.py`: the real
/// `worker.mjs` on the isolated Node runtime receives the staged Chrome for
/// Testing path from Rust and launches it headless inside a RenderJob directory.
#[test]
#[ignore = "requires the BM-03 staged Chromium and Node runtime; run via scripts/run_bm_03_acceptance.py"]
fn real_worker_render_verify_launches_the_locked_chromium() {
    let browser = std::env::var_os("BM03_RENDER_BROWSER")
        .map(PathBuf::from)
        .expect("BM03_RENDER_BROWSER is staged by scripts/run_bm_03_acceptance.py");
    let major = std::env::var("BM03_CHROMIUM_MAJOR")
        .ok()
        .and_then(|value| value.parse::<u32>().ok())
        .expect("BM03_CHROMIUM_MAJOR is staged by scripts/run_bm_03_acceptance.py");
    let node = std::env::var_os("BM03_NODE")
        .map(PathBuf::from)
        .expect("BM03_NODE is staged by scripts/run_bm_03_acceptance.py");
    let worker = fs::canonicalize(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../workers/motion_composition/worker.mjs"),
    )
    .expect("worker entrypoint");
    let fixture = TemporaryWorker::new(&format!(
        "#!/bin/sh\nexec {} {}\n",
        shell_quote(&node),
        shell_quote(worker.as_path()),
    ));
    let launch = VideoWorkerLaunch::new(
        VideoWorkerKind::Node,
        fixture.executable.clone(),
        fixture.root.clone(),
        "0.7.68".to_owned(),
        VideoWorkerRestartPolicy::new(0, Duration::ZERO).expect("restart policy"),
    )
    .expect("real worker launch")
    .with_render_browser(
        VideoWorkerRenderBrowserConfiguration::new(browser, major, Duration::from_secs(30))
            .expect("real render browser configuration"),
    );
    let orchestrator =
        LocalVideoOrchestrator::new(Duration::from_secs(30), Duration::from_secs(10))
            .expect("orchestrator");
    let status = orchestrator.start(launch).expect("start real Node worker");
    assert_eq!(status.worker_version(), Some("0.7.68"));
    orchestrator.health(VideoWorkerKind::Node).expect("health");
    let job_id = Uuid::parse_str("3f2504e0-4f89-41d3-9a0c-0305e82c3301").expect("job ID");
    let verified_major = orchestrator
        .render_verify(VideoWorkerKind::Node, job_id)
        .expect("real Chromium render verification");
    assert_eq!(verified_major, major);
    let process_id = status.process_id().expect("process id");
    orchestrator.stop(VideoWorkerKind::Node).expect("stop");
    wait_until_stopped(process_id);
}

/// Real render-sandbox chain, activated by `scripts/run_bm_04_acceptance.py`:
/// the real `worker.mjs` renders a hostile HTML fixture through the real
/// staged Chromium and must report the blocked navigation/request/download/
/// popup/dialog actions while still capturing the requested frames.
#[test]
#[ignore = "requires the BM-04 staged Chromium, Node runtime and hostile workspace; run via scripts/run_bm_04_acceptance.py"]
fn real_worker_render_sandbox_isolates_malicious_html() {
    let browser = std::env::var_os("BM04_RENDER_BROWSER")
        .map(PathBuf::from)
        .expect("BM04_RENDER_BROWSER is staged by scripts/run_bm_04_acceptance.py");
    let major = std::env::var("BM04_CHROMIUM_MAJOR")
        .ok()
        .and_then(|value| value.parse::<u32>().ok())
        .expect("BM04_CHROMIUM_MAJOR is staged by scripts/run_bm_04_acceptance.py");
    let node = std::env::var_os("BM04_NODE")
        .map(PathBuf::from)
        .expect("BM04_NODE is staged by scripts/run_bm_04_acceptance.py");
    let workspace = std::env::var_os("BM04_WORKSPACE")
        .map(PathBuf::from)
        .expect("BM04_WORKSPACE is staged by scripts/run_bm_04_acceptance.py");
    let worker = fs::canonicalize(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../workers/motion_composition/worker.mjs"),
    )
    .expect("worker entrypoint");
    let fixture = TemporaryWorker::new(&format!(
        "#!/bin/sh\nexec {} {}\n",
        shell_quote(&node),
        shell_quote(worker.as_path()),
    ));
    let launch = VideoWorkerLaunch::new(
        VideoWorkerKind::Node,
        fixture.executable.clone(),
        fixture.root.clone(),
        "0.7.68".to_owned(),
        VideoWorkerRestartPolicy::new(0, Duration::ZERO).expect("restart policy"),
    )
    .expect("real worker launch")
    .with_render_browser(
        VideoWorkerRenderBrowserConfiguration::new(browser, major, Duration::from_secs(30))
            .expect("real render browser configuration"),
    );
    let orchestrator =
        LocalVideoOrchestrator::new(Duration::from_secs(30), Duration::from_secs(15))
            .expect("orchestrator");
    let status = orchestrator.start(launch).expect("start real Node worker");
    orchestrator.health(VideoWorkerKind::Node).expect("health");
    let request = VideoWorkerRenderSandboxRequest::new(
        workspace.clone(),
        "entry.html".to_owned(),
        cancel_marker_file_name()
            .expect("declared cancellation marker")
            .to_owned(),
        VideoWorkerRenderCanvas::new(
            TEMPLATE_CANVAS_WIDTH,
            TEMPLATE_CANVAS_HEIGHT,
            TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR,
        )
        .expect("the template canvas is inside the declared bounds"),
        vec!["assets/style.css".to_owned()],
        3,
        60,
        60,
        2048,
        50_000_000,
    )
    .expect("real sandbox request");
    let job_id = Uuid::parse_str("6f9619ff-8b86-4d01-b42d-00cf4fc964ff").expect("job ID");
    let summary = orchestrator
        .render_sandbox(VideoWorkerKind::Node, job_id, &request)
        .expect("real malicious HTML render sandbox");
    assert_eq!(summary.chromium_major, major);
    assert_eq!(
        summary.frames_captured, 3,
        "declared frames must be captured"
    );
    assert!(summary.output_bytes > 0, "frames must produce PNG output");
    assert!(
        summary.blocked_navigations >= 1,
        "the hostile top-level navigation must be blocked",
    );
    assert!(
        summary.blocked_requests >= 1,
        "the remote subresource requests must be blocked",
    );
    assert!(
        summary.blocked_downloads >= 1,
        "the programmatic download must be blocked",
    );
    assert!(
        summary.blocked_popups >= 1,
        "the popup window must be blocked"
    );
    assert!(
        summary.blocked_dialogs >= 1,
        "the modal dialog must be blocked"
    );
    let frames = workspace.join("frames");
    assert!(
        frames.is_dir(),
        "the sandbox must write frames into the workspace"
    );
    let process_id = status.process_id().expect("process id");
    orchestrator.stop(VideoWorkerKind::Node).expect("stop");
    wait_until_stopped(process_id);
}

fn shell_quote(path: &Path) -> String {
    format!("'{}'", path.to_string_lossy().replace('\'', "'\\''"))
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
fn rejects_worker_environment_values_that_are_not_packaged_executables() {
    use std::collections::BTreeMap;

    let fixture = TemporaryWorker::new(HEALTHY_WORKER);
    let ffmpeg = fixture.root.join("ffmpeg");
    fs::write(&ffmpeg, b"packaged ffmpeg\n").expect("packaged binary");
    fs::set_permissions(&ffmpeg, fs::Permissions::from_mode(0o700)).expect("binary permissions");
    let missing = fixture.root.join("absent");
    let unreadable = fixture.root.join("notes.txt");
    fs::write(&unreadable, b"not a program\n").expect("plain file");
    fs::set_permissions(&unreadable, fs::Permissions::from_mode(0o600)).expect("file permissions");

    for (name, value) in [
        // A variable pointing at nothing is worse than none at all: the
        // upstream engines hand it to a process spawn instead of resolving it,
        // so the failure would surface far from its cause.
        ("PACKAGED_FFMPEG_EXE", missing.as_path()),
        ("PACKAGED_FFMPEG_EXE", unreadable.as_path()),
        ("PACKAGED_FFMPEG_EXE", Path::new("ffmpeg")),
        // Names that decide which program or library a process loads are not
        // dependency paths and may never be injected.
        ("PATH", ffmpeg.as_path()),
        ("LD_PRELOAD", ffmpeg.as_path()),
        ("NODE_OPTIONS", ffmpeg.as_path()),
    ] {
        let rejected = fixture
            .launch(VideoWorkerKind::Python)
            .with_environment(BTreeMap::from([(name, value)]))
            .err()
            .unwrap_or_else(|| panic!("{name} must be rejected"));
        assert_eq!(rejected.code(), VideoWorkerErrorCode::ConfigurationInvalid);
    }

    fixture
        .launch(VideoWorkerKind::Python)
        .with_environment(BTreeMap::from([("PACKAGED_FFMPEG_EXE", ffmpeg.as_path())]))
        .expect("a verified packaged executable is accepted");
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

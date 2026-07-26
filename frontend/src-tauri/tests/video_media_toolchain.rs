use automation_tool_desktop_lib::video_media_toolchain::VideoMediaToolchain;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(1);

struct TempDirectory(PathBuf);

impl TempDirectory {
    fn new(label: &str) -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-vf04-{label}-{}-{}",
            std::process::id(),
            TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }
}

impl Drop for TempDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn write_package(resource_root: &Path, target_id: &str) -> PathBuf {
    let root = resource_root.join("media-toolchain");
    fs::create_dir_all(root.join("bin")).unwrap();
    fs::create_dir_all(root.join("source")).unwrap();
    let suffix = if target_id == "windows-x86_64" {
        ".exe"
    } else {
        ""
    };
    let files = [
        ("BUILD-INFO.txt".to_owned(), b"locked build\n".to_vec()),
        ("COPYING.GPLv3".to_owned(), b"GPLv3\n".to_vec()),
        ("NOTICE.txt".to_owned(), b"FFmpeg and x264\n".to_vec()),
        (format!("bin/ffmpeg{suffix}"), b"ffmpeg binary\n".to_vec()),
        (format!("bin/ffprobe{suffix}"), b"ffprobe binary\n".to_vec()),
        (
            "source/ffmpeg-8.1.2.tar.xz".to_owned(),
            b"ffmpeg source\n".to_vec(),
        ),
        (
            "source/x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55.tar.gz".to_owned(),
            b"x264 source\n".to_vec(),
        ),
    ];
    let mut entries = Vec::new();
    for (relative, bytes) in files {
        let path = root.join(&relative);
        fs::write(&path, &bytes).unwrap();
        #[cfg(unix)]
        if relative.starts_with("bin/") {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        }
        entries.push(json!({"path": relative, "size": bytes.len(), "sha256": sha256(&bytes)}));
    }
    fs::write(
        root.join("manifest.json"),
        serde_json::to_vec_pretty(&json!({
            "schema_version": 1,
            "target_id": target_id,
            "version": "8.1.2",
            "license": "GPL-3.0-or-later",
            "files": entries,
        }))
        .unwrap(),
    )
    .unwrap();
    root
}

#[test]
fn resolves_one_verified_pair_into_both_worker_environment_contracts() {
    let temp = TempDirectory::new("shared-pair");
    let root = write_package(&temp.0, "macos-arm64");
    let toolchain = VideoMediaToolchain::load_for_target(&temp.0, "macos-arm64").unwrap();

    assert_eq!(toolchain.package_root(), fs::canonicalize(root).unwrap());
    assert_eq!(
        toolchain.intelligent_material_environment()["IMAGEIO_FFMPEG_EXE"],
        toolchain.ffmpeg_path()
    );
    assert_eq!(
        toolchain.brand_motion_environment()["HYPERFRAMES_FFMPEG_PATH"],
        toolchain.ffmpeg_path()
    );
    assert_eq!(
        toolchain.brand_motion_environment()["HYPERFRAMES_FFPROBE_PATH"],
        toolchain.ffprobe_path()
    );
    let debug = format!("{toolchain:?}");
    assert!(debug.contains("macos-arm64"));
    assert!(!debug.contains(temp.0.to_string_lossy().as_ref()));
}

#[test]
fn accepts_the_locked_windows_layout_without_using_system_path() {
    let temp = TempDirectory::new("windows-layout");
    write_package(&temp.0, "windows-x86_64");
    let toolchain = VideoMediaToolchain::load_for_target(&temp.0, "windows-x86_64").unwrap();
    assert!(toolchain.ffmpeg_path().ends_with("bin/ffmpeg.exe"));
    assert!(toolchain.ffprobe_path().ends_with("bin/ffprobe.exe"));
}

#[test]
fn rejects_digest_tampering_missing_extra_and_target_drift() {
    for scenario in ["digest", "missing", "extra", "target"] {
        let temp = TempDirectory::new(scenario);
        let root = write_package(&temp.0, "macos-arm64");
        match scenario {
            "digest" => fs::write(root.join("bin/ffmpeg"), b"same-ish tamper").unwrap(),
            "missing" => fs::remove_file(root.join("NOTICE.txt")).unwrap(),
            "extra" => fs::write(root.join("untracked"), b"extra").unwrap(),
            "target" => {
                let manifest_path = root.join("manifest.json");
                let mut manifest: serde_json::Value =
                    serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
                manifest["target_id"] = json!("windows-x86_64");
                fs::write(manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
            }
            _ => unreachable!(),
        }
        assert!(VideoMediaToolchain::load_for_target(&temp.0, "macos-arm64").is_err());
    }
}

#[cfg(unix)]
#[test]
fn rejects_linked_roots_files_and_non_executable_programs() {
    use std::os::unix::fs::{symlink, PermissionsExt};

    let temp = TempDirectory::new("links");
    let root = write_package(&temp.0, "macos-arm64");
    let original = root.join("NOTICE.txt");
    let replacement = temp.0.join("replacement");
    fs::rename(&original, &replacement).unwrap();
    symlink(&replacement, &original).unwrap();
    assert!(VideoMediaToolchain::load_for_target(&temp.0, "macos-arm64").is_err());

    fs::remove_file(&original).unwrap();
    fs::rename(&replacement, &original).unwrap();
    fs::set_permissions(root.join("bin/ffmpeg"), fs::Permissions::from_mode(0o600)).unwrap();
    assert!(VideoMediaToolchain::load_for_target(&temp.0, "macos-arm64").is_err());
}

#[test]
fn rejects_traversal_unknown_fields_and_unsupported_target() {
    let temp = TempDirectory::new("schema");
    let root = write_package(&temp.0, "macos-arm64");
    let manifest_path = root.join("manifest.json");
    let mut manifest: serde_json::Value =
        serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
    manifest["unexpected"] = json!(true);
    fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
    assert!(VideoMediaToolchain::load_for_target(&temp.0, "macos-arm64").is_err());
    assert!(VideoMediaToolchain::load_for_target(&temp.0, "linux-x86_64").is_err());

    write_package(&temp.0, "macos-arm64");
    let mut manifest: serde_json::Value =
        serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
    manifest["files"][0]["path"] = json!("../escape");
    fs::write(manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
    assert!(VideoMediaToolchain::load_for_target(&temp.0, "macos-arm64").is_err());
}

#[test]
fn source_is_not_exposed_as_a_webview_command_or_system_path_fallback() {
    let source = include_str!("../src/video_media_toolchain.rs");
    assert!(!source.contains("#[tauri::command]"));
    assert!(!source.contains("std::env::var(\"PATH\")"));
    assert!(!source.contains("which::"));
    assert!(!source.contains("download"));
}

/// A stand-in Worker that records the environment its own process received
/// before it answers the real bootstrap handshake.
///
/// Recording it from inside the spawned process is the only evidence that
/// matters here: the packaged FFmpeg was resolved and verified for months
/// while no Worker was ever told about it, so an assertion on the launch
/// configuration — or on the resolver being called — would have stayed green
/// throughout. Both upstream video engines fall back to the user's own FFmpeg
/// when their variable is absent, and only the child's environment can tell
/// the two situations apart.
const ENVIRONMENT_PROBE_WORKER: &str = r#"#!/usr/bin/python3
import base64, hashlib, hmac, json, os, socket, sys, threading

with open(__MARKER_PATH__, "w") as handle:
    json.dump(dict(os.environ), handle)

bootstrap = json.loads(sys.stdin.readline())
key = bytes.fromhex(bootstrap["localSessionToken"])
kind = bootstrap["workerKind"]
protocol = bootstrap["protocolVersion"]
version = "__WORKER_VERSION__"

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

def serve():
    while True:
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
    pass
"#;

/// The version `material_video_studio` expects from the smart-material Worker.
#[cfg(unix)]
const MATERIAL_WORKER_VERSION: &str = "1.3.2";

#[cfg(unix)]
fn write_environment_probe_worker(path: &Path, version: &str, marker: &Path) {
    use std::os::unix::fs::PermissionsExt;

    // An absolute interpreter is required: the brand-motion Worker is launched
    // with a cleared environment, so `/usr/bin/env` would have no PATH to
    // search.
    let source = ENVIRONMENT_PROBE_WORKER
        .replace(
            "__MARKER_PATH__",
            &serde_json::to_string(&marker.to_string_lossy()).unwrap(),
        )
        .replace("__WORKER_VERSION__", version);
    fs::write(path, source).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
}

#[cfg(unix)]
fn recorded_environment(marker: &Path) -> BTreeMap<String, String> {
    serde_json::from_slice(&fs::read(marker).expect("the Worker recorded its environment")).unwrap()
}

#[cfg(unix)]
fn probe_directory(temp: &TempDirectory, name: &str) -> PathBuf {
    let path = temp.0.join(name);
    fs::create_dir_all(&path).unwrap();
    fs::canonicalize(path).unwrap()
}

#[cfg(unix)]
#[test]
fn the_smart_material_worker_process_receives_the_packaged_ffmpeg() {
    use automation_tool_desktop_lib::local_video_orchestrator::{
        LocalVideoOrchestrator, VideoWorkerKind, VideoWorkerState,
    };
    use automation_tool_desktop_lib::material_video_studio;
    use std::time::Duration;

    let temp = TempDirectory::new("material-environment");
    write_package(&temp.0, "macos-arm64");
    let toolchain = VideoMediaToolchain::load_for_target(&temp.0, "macos-arm64").unwrap();
    let asset_root = probe_directory(&temp, "material-assets");
    let marker = asset_root.join("environment.json");
    let executable = probe_directory(&temp, "material-worker").join("worker");
    write_environment_probe_worker(&executable, MATERIAL_WORKER_VERSION, &marker);

    let launch = material_video_studio::material_worker_launch(executable, asset_root, &toolchain)
        .expect("the studio builds its Worker launch from the packaged toolchain");
    let orchestrator = LocalVideoOrchestrator::new(Duration::from_secs(10), Duration::from_secs(10))
        .expect("orchestrator");
    let status = orchestrator.start(launch).expect("start the probe Worker");
    assert_eq!(status.state(), VideoWorkerState::Running);
    let environment = recorded_environment(&marker);
    orchestrator.stop(VideoWorkerKind::Python).expect("stop");

    assert_eq!(
        environment.get("IMAGEIO_FFMPEG_EXE").map(String::as_str),
        Some(toolchain.ffmpeg_path().to_string_lossy().as_ref()),
        "the smart-material Worker resolves FFmpeg from this variable first and \
         falls back to the user's own installation when it is missing"
    );
}

#[cfg(unix)]
#[test]
fn the_brand_motion_worker_process_receives_the_packaged_pair_and_nothing_else() {
    use automation_tool_desktop_lib::local_video_orchestrator::{
        LocalVideoOrchestrator, VideoWorkerKind, VideoWorkerState,
    };
    use std::os::unix::fs::PermissionsExt;
    use std::time::Duration;

    let temp = TempDirectory::new("motion-environment");
    write_package(&temp.0, "macos-arm64");
    let toolchain = VideoMediaToolchain::load_for_target(&temp.0, "macos-arm64").unwrap();
    let asset_root = probe_directory(&temp, "motion-assets");
    let marker = asset_root.join("environment.json");
    let package = probe_directory(&temp, "motion-package");
    fs::create_dir_all(package.join("runtime")).unwrap();
    fs::create_dir_all(package.join("app")).unwrap();
    fs::write(package.join("app/worker.mjs"), b"// probe\n").unwrap();
    // The Worker's Node runtime is replaced by the probe; the orchestrator
    // launches whatever the packaged layout names, so the handshake is real.
    write_environment_probe_worker(&package.join("runtime/node"), "0.7.68", &marker);
    let browser = temp.0.join("chromium");
    fs::write(&browser, b"probe browser\n").unwrap();
    fs::set_permissions(&browser, fs::Permissions::from_mode(0o700)).unwrap();
    let browser = fs::canonicalize(browser).unwrap();

    let launch = automation_tool_desktop_lib::motion_worker_launch(
        package,
        asset_root,
        browser,
        143,
        toolchain.brand_motion_environment(),
    )
    .expect("the studio builds its Worker launch from the packaged toolchain");
    let orchestrator = LocalVideoOrchestrator::new(Duration::from_secs(10), Duration::from_secs(10))
        .expect("orchestrator");
    let status = orchestrator.start(launch).expect("start the probe Worker");
    assert_eq!(status.state(), VideoWorkerState::Running);
    let environment = recorded_environment(&marker);
    orchestrator.stop(VideoWorkerKind::Node).expect("stop");

    assert_eq!(
        environment.get("HYPERFRAMES_FFMPEG_PATH").map(String::as_str),
        Some(toolchain.ffmpeg_path().to_string_lossy().as_ref()),
    );
    assert_eq!(
        environment
            .get("HYPERFRAMES_FFPROBE_PATH")
            .map(String::as_str),
        Some(toolchain.ffprobe_path().to_string_lossy().as_ref()),
    );
    // The brand-motion Worker runs with a cleared environment. Injection must
    // happen after that clearing, and must not smuggle the host's search path
    // back in: with a PATH present the upstream engine would look there first
    // whenever a variable is ever dropped.
    assert!(!environment.contains_key("PATH"), "{environment:?}");
}

/// Walk a source file for one item's body, so a wiring assertion cannot be
/// satisfied by an unrelated occurrence elsewhere in the file.
fn function_body(source: &str, header: &str) -> String {
    let lines: Vec<&str> = source.lines().collect();
    let start = lines
        .iter()
        .position(|line| line.trim_start().starts_with(header))
        .unwrap_or_else(|| panic!("{header} must exist"));
    let mut depth = 0i32;
    let mut opened = false;
    let mut body = Vec::new();
    for line in &lines[start..] {
        body.push(*line);
        depth += line.matches('{').count() as i32;
        depth -= line.matches('}').count() as i32;
        opened |= line.contains('{');
        if opened && depth <= 0 {
            break;
        }
    }
    body.join("\n")
}

#[test]
fn every_video_worker_launch_carries_the_packaged_environment_from_its_entry_point() {
    let material = include_str!("../src/material_video_studio.rs");
    let motion = include_str!("../src/lib.rs");
    let orchestrator = include_str!("../src/local_video_orchestrator.rs");
    for (source, item, required) in [
        // The defect this pins: the resolver existed and was correct, but no
        // entry point called it, so the packaged binary was never used.
        (material, "pub(crate) fn open(", "material_worker_launch("),
        (
            material,
            "pub fn material_worker_launch(",
            "intelligent_material_environment()",
        ),
        // Both submit paths reach the Worker through one launch site, so the
        // wiring is pinned there and the reachability of that site is pinned
        // from each entry point. Pinning the launch inside one command left the
        // other command — the one-sentence path — covered by nothing.
        (
            motion,
            "async fn submit_motion_video_draft(",
            "start_motion_render(",
        ),
        (
            motion,
            "async fn submit_motion_video_brief(",
            "start_motion_render(",
        ),
        (motion, "fn start_motion_render(", "motion_worker_launch("),
        (motion, "fn start_motion_render(", "brand_motion_environment()"),
        (motion, "pub fn motion_worker_launch(", "with_environment("),
        // ... and the stored map has to reach the process itself.
        (orchestrator, "fn spawn_worker(", "command.env("),
    ] {
        assert!(
            function_body(source, item).contains(required),
            "{item} no longer contains {required}, so a video Worker can start \
             without being told where the packaged FFmpeg is"
        );
    }
}

#![cfg(windows)]

use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::local_video_orchestrator::{
    LocalVideoOrchestrator, VideoWorkerErrorCode, VideoWorkerKind, VideoWorkerLaunch,
    VideoWorkerRenderBrowserConfiguration, VideoWorkerRestartPolicy, VideoWorkerState,
};
use uuid::Uuid;
use windows_sys::Win32::Foundation::CloseHandle;
use windows_sys::Win32::System::Threading::{
    GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
};

fn process_exists(process_id: u32) -> bool {
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

fn wait_until_stopped(process_id: u32) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while process_exists(process_id) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(
        !process_exists(process_id),
        "bundled Node Worker survived Windows Job cleanup"
    );
}

#[test]
fn render_browser_configuration_rejects_junction_ancestor() {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock")
        .as_nanos();
    let root = std::env::temp_dir().join(format!(
        "automation-tool-bm03-{}-{nonce}",
        std::process::id()
    ));
    let real = root.join("real");
    let junction = root.join("junction");
    fs::create_dir_all(&real).expect("real browser directory");
    fs::write(real.join("fake-browser.exe"), b"MZ").expect("fake PE");
    let output = Command::new("cmd.exe")
        .args([
            "/d",
            "/c",
            "mklink",
            "/J",
            junction.to_str().expect("junction path"),
            real.to_str().expect("real path"),
        ])
        .output()
        .expect("create browser junction");
    assert!(output.status.success(), "mklink /J failed");
    let error = VideoWorkerRenderBrowserConfiguration::new(
        junction.join("fake-browser.exe"),
        149,
        Duration::from_secs(30),
    )
    .expect_err("render browser junction ancestor must fail");
    fs::remove_dir(&junction).expect("remove junction without following it");
    fs::remove_dir_all(&root).expect("remove fixture");
    assert_eq!(error.code(), VideoWorkerErrorCode::ConfigurationInvalid);
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
    let orchestrator =
        LocalVideoOrchestrator::new(Duration::from_secs(10), Duration::from_secs(10))
            .expect("orchestrator");
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

#[test]
fn real_worker_render_verify_launches_the_locked_chromium() {
    let (Some(package_root), Some(browser), Some(major)) = (
        std::env::var_os("BM03_PACKAGE_ROOT").map(PathBuf::from),
        std::env::var_os("BM03_RENDER_BROWSER").map(PathBuf::from),
        std::env::var("BM03_CHROMIUM_MAJOR")
            .ok()
            .and_then(|value| value.parse::<u32>().ok()),
    ) else {
        return;
    };
    let asset_root = package_root.join("acceptance-render-assets");
    fs::create_dir(&asset_root).expect("asset root");
    let launch = VideoWorkerLaunch::bundled_node(
        &package_root,
        asset_root,
        VideoWorkerRestartPolicy::new(0, Duration::ZERO).expect("restart policy"),
    )
    .expect("bundled Node launch")
    .with_render_browser(
        VideoWorkerRenderBrowserConfiguration::new(browser, major, Duration::from_secs(30))
            .expect("real render browser configuration"),
    );
    let orchestrator =
        LocalVideoOrchestrator::new(Duration::from_secs(30), Duration::from_secs(10))
            .expect("orchestrator");
    let status = orchestrator.start(launch).expect("start real Node Worker");
    assert_eq!(status.worker_version(), Some("0.7.68"));
    orchestrator.health(VideoWorkerKind::Node).expect("health");
    let verified_major = orchestrator
        .render_verify(
            VideoWorkerKind::Node,
            Uuid::parse_str("3f2504e0-4f89-41d3-9a0c-0305e82c3301").expect("job ID"),
        )
        .expect("real Chromium render verification");
    assert_eq!(verified_major, major);
    let process_id = status.process_id().expect("process id");
    orchestrator.stop(VideoWorkerKind::Node).expect("stop");
    wait_until_stopped(process_id);
}

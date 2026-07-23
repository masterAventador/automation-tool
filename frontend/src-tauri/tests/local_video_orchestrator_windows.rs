#![cfg(windows)]

use std::fs;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use automation_tool_desktop_lib::local_video_orchestrator::{
    LocalVideoOrchestrator, VideoWorkerKind, VideoWorkerLaunch, VideoWorkerRestartPolicy,
    VideoWorkerState,
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

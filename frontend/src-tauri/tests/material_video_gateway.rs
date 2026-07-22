use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::local_video_orchestrator::{
    LocalVideoOrchestrator, VideoWorkerKind, VideoWorkerLaunch, VideoWorkerRestartPolicy,
    VideoWorkerState,
};
use uuid::Uuid;

static DIRECTORY_SEQUENCE: AtomicU64 = AtomicU64::new(0);

struct TemporaryAssetRoot(PathBuf);

impl TemporaryAssetRoot {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-im03-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            DIRECTORY_SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        fs::create_dir_all(path.join("inputs")).expect("asset root");
        fs::write(path.join("inputs/clip.mp4"), b"real-process-fixture").expect("asset fixture");
        Self(fs::canonicalize(path).expect("canonical asset root"))
    }
}

impl Drop for TemporaryAssetRoot {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn frozen_worker_uses_the_authenticated_loopback_gateway_end_to_end() {
    let Some(executable) = std::env::var_os("AUTOMATION_TOOL_IM03_WORKER") else {
        eprintln!("real frozen worker is exercised by scripts/run_im_03_acceptance.py");
        return;
    };
    let executable = PathBuf::from(executable);
    assert!(executable.is_absolute());
    assert!(executable.is_file());
    let assets = TemporaryAssetRoot::new();
    let policy = VideoWorkerRestartPolicy::new(0, Duration::ZERO).expect("restart policy");
    let launch = VideoWorkerLaunch::new(
        VideoWorkerKind::Python,
        executable,
        assets.0.clone(),
        "1.3.2".to_owned(),
        policy,
    )
    .expect("secure launch configuration");
    let orchestrator =
        LocalVideoOrchestrator::new(Duration::from_secs(30), Duration::from_secs(10))
            .expect("orchestrator");

    let running = orchestrator.start(launch).expect("start frozen worker");
    assert_eq!(running.state(), VideoWorkerState::Running);
    assert_eq!(running.worker_version(), Some("1.3.2"));
    assert_eq!(running.host(), Some("127.0.0.1"));
    assert!(running.port().is_some_and(|port| port > 0));
    orchestrator
        .health(VideoWorkerKind::Python)
        .expect("authenticated health request");
    orchestrator
        .cancel(
            VideoWorkerKind::Python,
            Uuid::parse_str("123e4567-e89b-42d3-a456-426614174321").expect("job id"),
        )
        .expect("authenticated cancellation");
    orchestrator
        .stop(VideoWorkerKind::Python)
        .expect("stop worker and descendants");
    assert_eq!(
        orchestrator
            .status(VideoWorkerKind::Python)
            .expect("stopped status")
            .state(),
        VideoWorkerState::Stopped
    );
}

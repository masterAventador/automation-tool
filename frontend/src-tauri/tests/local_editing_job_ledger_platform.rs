use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::local_editing_job_ledger::{
    LocalEditingJobScheduler, LocalEditingJobStatus,
};
use automation_tool_desktop_lib::local_video_orchestrator::VideoWorkerLocalEditingJobRequest;
use automation_tool_desktop_lib::video_job_workspace::{
    VideoJobWorkspacePolicy, VideoJobWorkspaceStore,
};
use uuid::Uuid;

static TEMPORARY_SEQUENCE: AtomicU64 = AtomicU64::new(0);

struct TemporaryAppData {
    path: PathBuf,
}

impl TemporaryAppData {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-le12-ledger-platform-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            TEMPORARY_SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        fs::create_dir(&path).expect("AppData fixture");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700))
                .expect("private AppData fixture");
        }
        let path = fs::canonicalize(path).expect("canonical AppData fixture");
        Self { path }
    }
}

impl Drop for TemporaryAppData {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

fn store(root: &Path) -> VideoJobWorkspaceStore {
    VideoJobWorkspaceStore::initialize(
        root,
        VideoJobWorkspacePolicy::new(16 * 1024 * 1024, 8 * 1024 * 1024, 8, 3600, 0)
            .expect("workspace policy"),
    )
    .expect("workspace store")
}

#[test]
fn native_platform_reopens_and_cancels_a_never_dispatched_job() {
    let app_data = TemporaryAppData::new();
    let store = store(&app_data.path);
    let scheduler = LocalEditingJobScheduler::new();
    let job_id = Uuid::parse_str("123e4567-e89b-42d3-a456-426614174100").expect("job ID");
    let request = VideoWorkerLocalEditingJobRequest::new(
        Uuid::parse_str("223e4567-e89b-42d3-a456-426614174101").expect("project ID"),
        Uuid::parse_str("323e4567-e89b-42d3-a456-426614174102").expect("timeline ID"),
        7,
    )
    .expect("editing request");

    let queued = scheduler
        .create(&store, job_id, &request)
        .expect("persist queued job");
    assert_eq!(queued.status(), LocalEditingJobStatus::Queued);
    assert_eq!(
        LocalEditingJobScheduler::new()
            .snapshot(&store, job_id)
            .expect("reopen queued job"),
        queued,
    );

    let orchestrator =
        automation_tool_desktop_lib::local_video_orchestrator::LocalVideoOrchestrator::new(
            std::time::Duration::from_secs(1),
            std::time::Duration::from_secs(1),
        )
        .expect("orchestrator");
    let cancelled = scheduler
        .request_cancel(&store, &orchestrator, job_id)
        .expect("cancel without dispatch");
    assert_eq!(cancelled.status(), LocalEditingJobStatus::Cancelled);
    assert_eq!(
        LocalEditingJobScheduler::new()
            .snapshot(&store, job_id)
            .expect("reopen cancellation"),
        cancelled,
    );
}

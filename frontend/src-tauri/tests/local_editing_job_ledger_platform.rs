use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::local_editing_job_ledger::{
    LocalEditingJobFailureCode, LocalEditingJobLedger, LocalEditingJobScheduler,
    LocalEditingJobStatus,
};
use automation_tool_desktop_lib::local_video_orchestrator::{
    VideoWorkerLocalEditingEvent, VideoWorkerLocalEditingFailureCode,
    VideoWorkerLocalEditingJobRequest,
};
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

fn job_id() -> Uuid {
    Uuid::parse_str("123e4567-e89b-42d3-a456-426614174100").expect("job ID")
}

fn other_job_id() -> Uuid {
    Uuid::parse_str("523e4567-e89b-42d3-a456-426614174104").expect("other job ID")
}

fn request() -> VideoWorkerLocalEditingJobRequest {
    VideoWorkerLocalEditingJobRequest::new(
        Uuid::parse_str("223e4567-e89b-42d3-a456-426614174101").expect("project ID"),
        Uuid::parse_str("323e4567-e89b-42d3-a456-426614174102").expect("timeline ID"),
        7,
    )
    .expect("editing request")
}

#[test]
fn cross_platform_workspace_failure_maps_to_resource_exhausted() {
    let app_data = TemporaryAppData::new();
    let store = store(&app_data.path);
    let ledger = LocalEditingJobLedger::new();
    ledger.create(&store, job_id(), &request()).unwrap();
    ledger.mark_running(&store, job_id(), 0).unwrap();

    let failed = ledger
        .apply_event(
            &store,
            job_id(),
            &VideoWorkerLocalEditingEvent::Failed {
                failure_code: VideoWorkerLocalEditingFailureCode::WorkspaceUnusable,
            },
        )
        .expect("persist workspace failure");

    assert_eq!(failed.status(), LocalEditingJobStatus::Failed);
    assert_eq!(
        failed.failure_code(),
        Some(LocalEditingJobFailureCode::ResourceExhausted),
    );
    assert_ne!(
        failed.failure_code(),
        Some(LocalEditingJobFailureCode::MaterialUnsupported),
    );
}

#[test]
fn cross_platform_cancellation_races_settle_once() {
    let app_data = TemporaryAppData::new();
    let store = store(&app_data.path);
    let ledger = LocalEditingJobLedger::new();

    ledger.create(&store, job_id(), &request()).unwrap();
    let queued_cancel = ledger.request_cancel(&store, job_id()).unwrap();
    assert_eq!(queued_cancel.status(), LocalEditingJobStatus::Cancelling);
    let never_dispatched = ledger.confirm_cancelled(&store, job_id()).unwrap();
    assert_eq!(never_dispatched.status(), LocalEditingJobStatus::Cancelled);

    ledger.create(&store, other_job_id(), &request()).unwrap();
    ledger.mark_running(&store, other_job_id(), 0).unwrap();
    ledger.request_cancel(&store, other_job_id()).unwrap();
    let cancelled = ledger
        .apply_event(
            &store,
            other_job_id(),
            &VideoWorkerLocalEditingEvent::Cancelled,
        )
        .unwrap();
    assert_eq!(cancelled.status(), LocalEditingJobStatus::Cancelled);
}

#[test]
fn native_platform_reopens_and_cancels_a_never_dispatched_job() {
    let app_data = TemporaryAppData::new();
    let store = store(&app_data.path);
    let scheduler = LocalEditingJobScheduler::new();
    let job_id = job_id();
    let request = request();

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

#[test]
fn startup_reconciliation_enumerates_only_local_editing_checkpoints() {
    let app_data = TemporaryAppData::new();
    let store = store(&app_data.path);
    let scheduler = LocalEditingJobScheduler::new();
    let job_id = Uuid::parse_str("123e4567-e89b-42d3-a456-426614174100").expect("job ID");
    let foreign_job_id =
        Uuid::parse_str("523e4567-e89b-42d3-a456-426614174104").expect("foreign job ID");
    let request = VideoWorkerLocalEditingJobRequest::new(
        Uuid::parse_str("223e4567-e89b-42d3-a456-426614174101").expect("project ID"),
        Uuid::parse_str("323e4567-e89b-42d3-a456-426614174102").expect("timeline ID"),
        7,
    )
    .expect("editing request");
    scheduler
        .create(&store, job_id, &request)
        .expect("persist local editing job");
    store
        .create(foreign_job_id)
        .expect("unrelated workspace without editing checkpoint");
    let orchestrator =
        automation_tool_desktop_lib::local_video_orchestrator::LocalVideoOrchestrator::new(
            std::time::Duration::from_secs(1),
            std::time::Duration::from_secs(1),
        )
        .expect("orchestrator");

    let reconciled = scheduler
        .reconcile_all(
            &store,
            &orchestrator,
            automation_tool_desktop_lib::local_editing_job_ledger::LocalEditingJobRecoveryPolicy::new(2)
                .expect("recovery policy"),
        )
        .expect("enumerate local editing checkpoints");
    assert_eq!(reconciled.len(), 1);
    assert_eq!(reconciled[0].job_id(), job_id);
    assert_eq!(reconciled[0].status(), LocalEditingJobStatus::Queued);
}

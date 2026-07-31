#![cfg(unix)]

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::local_editing_job_ledger::{
    LocalEditingJobFailureCode, LocalEditingJobLedger, LocalEditingJobLedgerErrorCode,
    LocalEditingJobStatus,
};
use automation_tool_desktop_lib::local_video_orchestrator::{
    VideoWorkerLocalEditingEvent, VideoWorkerLocalEditingFailureCode,
    VideoWorkerLocalEditingJobRequest, VideoWorkerLocalEditingPhase,
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
            "automation-tool-le12-ledger-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            TEMPORARY_SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        fs::create_dir(&path).expect("app data");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).expect("private app data");
        Self {
            path: fs::canonicalize(path).expect("canonical app data"),
        }
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

fn checkpoint(root: &Path, job_id: Uuid) -> PathBuf {
    root.join("video-workspaces-v1")
        .join("jobs")
        .join(job_id.hyphenated().to_string())
        .join("checkpoints")
        .join("local-editing-job.checkpoint")
}

#[test]
fn authenticated_events_are_durable_monotonic_and_idempotent() {
    let app_data = TemporaryAppData::new();
    let store = store(&app_data.path);
    let ledger = LocalEditingJobLedger::new();

    let queued = ledger
        .create(&store, job_id(), &request())
        .expect("create queued ledger");
    assert_eq!(queued.status(), LocalEditingJobStatus::Queued);
    assert_eq!(queued.revision(), 1);
    assert_eq!(queued.timeline_revision(), 7);
    assert_eq!(format!("{queued:?}"), "LocalEditingJobSnapshot(<redacted>)");

    let running = ledger
        .mark_running(&store, job_id(), 2)
        .expect("durable dispatch intent");
    assert_eq!(running.status(), LocalEditingJobStatus::Running);
    assert_eq!(running.worker_generation(), 2);
    assert_eq!(running.revision(), 2);

    let preparing = VideoWorkerLocalEditingEvent::Progress {
        phase: VideoWorkerLocalEditingPhase::Preparing,
        progress_per_mille: 0,
    };
    let first = ledger
        .apply_event(&store, job_id(), &preparing)
        .expect("persist preparing progress");
    assert_eq!(first.revision(), 3);
    assert_eq!(first.phase(), Some(VideoWorkerLocalEditingPhase::Preparing));
    assert_eq!(first.progress_per_mille(), 0);
    let duplicate = ledger
        .apply_event(&store, job_id(), &preparing)
        .expect("same progress is idempotent");
    assert_eq!(duplicate.revision(), first.revision());

    for (phase, progress) in [
        (VideoWorkerLocalEditingPhase::Rendering, 600),
        (VideoWorkerLocalEditingPhase::Publishing, 1000),
    ] {
        ledger
            .apply_event(
                &store,
                job_id(),
                &VideoWorkerLocalEditingEvent::Progress {
                    phase,
                    progress_per_mille: progress,
                },
            )
            .expect("persist monotonic progress");
    }
    let artifact = Uuid::parse_str("423e4567-e89b-42d3-a456-426614174103").expect("artifact ID");
    let succeeded = ledger
        .apply_event(
            &store,
            job_id(),
            &VideoWorkerLocalEditingEvent::Succeeded {
                output_artifact_id: artifact,
            },
        )
        .expect("persist success before exposing it");
    assert_eq!(succeeded.status(), LocalEditingJobStatus::Succeeded);
    assert_eq!(succeeded.output_artifact_id(), Some(artifact));
    assert_eq!(succeeded.revision(), 6);

    let reopened = LocalEditingJobLedger::new()
        .load(&store, job_id())
        .expect("reopen durable terminal snapshot");
    assert_eq!(reopened, succeeded);
    assert_eq!(
        ledger
            .apply_event(&store, job_id(), &preparing)
            .expect_err("terminal state cannot be revived")
            .code(),
        LocalEditingJobLedgerErrorCode::Conflict,
    );
    assert_eq!(
        fs::metadata(checkpoint(&app_data.path, job_id()))
            .expect("checkpoint metadata")
            .permissions()
            .mode()
            & 0o777,
        0o600,
    );
}

#[test]
fn workspace_failure_maps_to_resource_exhausted_not_material_damage() {
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
fn cancellation_races_follow_the_editing_job_state_machine() {
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
fn only_the_app_can_classify_a_missing_worker() {
    let app_data = TemporaryAppData::new();
    let store = store(&app_data.path);
    let ledger = LocalEditingJobLedger::new();
    ledger.create(&store, job_id(), &request()).unwrap();
    ledger.mark_running(&store, job_id(), 4).unwrap();

    let lost = ledger.fail_worker_lost(&store, job_id()).unwrap();

    assert_eq!(lost.status(), LocalEditingJobStatus::Failed);
    assert_eq!(
        lost.failure_code(),
        Some(LocalEditingJobFailureCode::WorkerLost)
    );
}

#[test]
fn invalid_identity_and_premature_worker_loss_are_rejected_as_domain_conflicts() {
    let app_data = TemporaryAppData::new();
    let store = store(&app_data.path);
    let ledger = LocalEditingJobLedger::new();
    let project_id = Uuid::parse_str("223e4567-e89b-42d3-a456-426614174101").expect("project ID");

    assert_eq!(
        ledger
            .create(&store, project_id, &request())
            .expect_err("a job cannot reuse its project identity")
            .code(),
        LocalEditingJobLedgerErrorCode::ConfigurationInvalid,
    );

    ledger.create(&store, job_id(), &request()).unwrap();
    assert_eq!(
        ledger
            .fail_worker_lost(&store, job_id())
            .expect_err("a never-dispatched job did not lose a Worker")
            .code(),
        LocalEditingJobLedgerErrorCode::Conflict,
    );
}

#[test]
fn checkpoint_schema_requires_every_key_and_rejects_duplicate_or_drifted_facts() {
    let app_data = TemporaryAppData::new();
    let store = store(&app_data.path);
    let ledger = LocalEditingJobLedger::new();
    ledger.create(&store, job_id(), &request()).unwrap();
    let path = checkpoint(&app_data.path, job_id());
    let valid = fs::read_to_string(&path).expect("valid checkpoint");
    let cases = [
        valid.replace("\"failureCode\":null,", ""),
        valid.replace("\"revision\":1", "\"revision\":1,\"revision\":2"),
        valid.replace("local-editing-job.v1", "local-editing-job.v2"),
        valid.replace(
            "123e4567-e89b-42d3-a456-426614174100",
            "523e4567-e89b-42d3-a456-426614174104",
        ),
        "{".to_owned(),
    ];
    assert!(cases.iter().all(|candidate| candidate != &valid));

    for candidate in cases {
        fs::write(&path, candidate).expect("corrupt checkpoint");
        assert_eq!(
            ledger
                .load(&store, job_id())
                .expect_err("non-exact checkpoint must fail closed")
                .code(),
            LocalEditingJobLedgerErrorCode::DataRejected,
        );
        fs::write(&path, &valid).expect("restore checkpoint");
    }
}

#[test]
fn checkpoint_with_broad_permissions_or_excess_size_fails_closed() {
    let app_data = TemporaryAppData::new();
    let store = store(&app_data.path);
    let ledger = LocalEditingJobLedger::new();
    ledger.create(&store, job_id(), &request()).unwrap();
    let path = checkpoint(&app_data.path, job_id());

    fs::set_permissions(&path, fs::Permissions::from_mode(0o640))
        .expect("broaden checkpoint permissions");
    assert_eq!(
        ledger
            .load(&store, job_id())
            .expect_err("broad checkpoint permissions must fail closed")
            .code(),
        LocalEditingJobLedgerErrorCode::StorageUnavailable,
    );

    fs::set_permissions(&path, fs::Permissions::from_mode(0o600))
        .expect("restore private checkpoint permissions");
    fs::write(&path, vec![b'x'; 1024 * 1024 + 1]).expect("oversized checkpoint");
    assert_eq!(
        ledger
            .load(&store, job_id())
            .expect_err("oversized checkpoint must fail closed")
            .code(),
        LocalEditingJobLedgerErrorCode::StorageUnavailable,
    );
}

#[test]
fn malformed_or_linked_checkpoints_fail_closed_without_path_disclosure() {
    let app_data = TemporaryAppData::new();
    let store = store(&app_data.path);
    let ledger = LocalEditingJobLedger::new();
    ledger.create(&store, job_id(), &request()).unwrap();
    let path = checkpoint(&app_data.path, job_id());
    fs::write(
        &path,
        br#"{"schemaVersion":"local-editing-job.v1","extra":true}"#,
    )
    .expect("replace with malformed checkpoint");

    let malformed = ledger
        .load(&store, job_id())
        .expect_err("unknown fields must fail closed");
    assert_eq!(
        malformed.code(),
        LocalEditingJobLedgerErrorCode::DataRejected
    );
    assert!(!malformed
        .to_string()
        .contains(app_data.path.to_string_lossy().as_ref()));

    fs::remove_file(&path).expect("remove malformed checkpoint");
    let target = app_data.path.join("outside");
    fs::write(&target, b"{}").expect("outside file");
    std::os::unix::fs::symlink(&target, &path).expect("linked checkpoint");
    let linked = ledger
        .load(&store, job_id())
        .expect_err("linked checkpoint must fail closed");
    assert_eq!(
        linked.code(),
        LocalEditingJobLedgerErrorCode::StorageUnavailable,
    );
    assert!(!format!("{linked:?}").contains(app_data.path.to_string_lossy().as_ref()));
}

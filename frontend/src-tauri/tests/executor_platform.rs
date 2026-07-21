use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

use automation_tool_desktop_lib::executor_platform::{
    ExecutorPlatformErrorCode, ExecutorPlatformPaths, ExecutorPlatformService,
    LocalExecutorIdentity,
};

const TASK_ID: &str = "123e4567-e89b-42d3-a456-426614174005";
const EMERGENCY_STOP_KEY: &str = "task:emergency-stop:h8-03";

struct TemporaryAppData {
    path: PathBuf,
}

impl TemporaryAppData {
    fn new() -> Self {
        Self {
            path: std::env::temp_dir().join(format!(
                "automation-tool-e4-13-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .expect("system time")
                    .as_nanos(),
            )),
        }
    }
}

impl Drop for TemporaryAppData {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

#[test]
fn app_data_owns_stable_executor_identity_and_fixed_private_paths() {
    let app_data = TemporaryAppData::new();

    let first = LocalExecutorIdentity::load_or_create(&app_data.path)
        .expect("create local Executor identity");
    let second = LocalExecutorIdentity::load_or_create(&app_data.path)
        .expect("reopen local Executor identity");
    let paths =
        ExecutorPlatformPaths::from_app_data(&app_data.path).expect("fixed local Executor paths");

    assert_eq!(first.executor_id(), second.executor_id());
    assert_eq!(
        paths.package_root(),
        app_data.path.join("local-executor/package")
    );
    assert_eq!(
        paths.state_directory(),
        app_data.path.join("local-executor/state")
    );
    assert!(
        uuid::Uuid::parse_str(first.executor_id()).is_ok_and(|value| {
            value.get_version_num() == 4 && value.get_variant() == uuid::Variant::RFC4122
        })
    );
    #[cfg(unix)]
    {
        let identity_path = app_data.path.join("local-executor/executor-id-v1");
        assert_eq!(
            identity_path
                .metadata()
                .expect("identity metadata")
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        assert_eq!(
            identity_path
                .parent()
                .expect("identity parent")
                .metadata()
                .expect("parent metadata")
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
    }
}

#[test]
fn rejects_relative_app_data_and_corrupt_persisted_identity() {
    assert!(ExecutorPlatformPaths::from_app_data(&PathBuf::from("relative")).is_err());

    let app_data = TemporaryAppData::new();
    let identity = LocalExecutorIdentity::load_or_create(&app_data.path)
        .expect("create local Executor identity");
    std::fs::write(
        app_data.path.join("local-executor/executor-id-v1"),
        b"not-a-canonical-uuid",
    )
    .expect("corrupt identity fixture");
    assert!(LocalExecutorIdentity::load_or_create(&app_data.path).is_err());
    assert!(!identity.executor_id().is_empty());
}

#[test]
fn task_emergency_stop_intent_is_private_idempotent_and_survives_app_restart() {
    let app_data = TemporaryAppData::new();
    let first = ExecutorPlatformService::initialize(&app_data.path).expect("initialize service");

    let stopped = first
        .engage_task_emergency_stop(TASK_ID, EMERGENCY_STOP_KEY)
        .expect("engage local emergency stop");
    let replayed = first
        .engage_task_emergency_stop(TASK_ID, EMERGENCY_STOP_KEY)
        .expect("replay local emergency stop");
    let pending = first
        .pending_task_emergency_stop()
        .expect("read pending emergency stop")
        .expect("pending emergency stop");

    assert_eq!(stopped.state(), replayed.state());
    assert_eq!(pending.task_id(), TASK_ID);
    assert_eq!(pending.idempotency_key(), EMERGENCY_STOP_KEY);
    assert_eq!(
        first
            .engage_task_emergency_stop(
                "223e4567-e89b-42d3-a456-426614174005",
                "task:emergency-stop:conflict",
            )
            .expect_err("conflicting pending stop")
            .code(),
        ExecutorPlatformErrorCode::ConfigurationInvalid,
    );
    drop(first);

    let restarted =
        ExecutorPlatformService::initialize(&app_data.path).expect("reopen emergency stop state");
    let restored = restarted
        .pending_task_emergency_stop()
        .expect("read restored emergency stop")
        .expect("restored emergency stop");
    assert_eq!(restored, pending);

    #[cfg(unix)]
    assert_eq!(
        app_data
            .path
            .join("local-executor/task-emergency-stop-v1")
            .metadata()
            .expect("emergency stop marker metadata")
            .permissions()
            .mode()
            & 0o777,
        0o600,
    );
}

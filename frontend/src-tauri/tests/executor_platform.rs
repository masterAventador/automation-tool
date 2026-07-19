use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::executor_platform::{
    ExecutorPlatformPaths, LocalExecutorIdentity,
};

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
        use std::os::unix::fs::PermissionsExt;
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

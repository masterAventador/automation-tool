use automation_tool_desktop_lib::startup_environment::{
    AppDataStartupState, EmbeddedBrowserStartupState, ExecutorStartupState,
    StartupEnvironmentService, StartupEnvironmentSnapshot,
};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(0);

struct TemporaryAppData(PathBuf);

impl TemporaryAppData {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-h8-16e-startup-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed),
        ));
        std::fs::create_dir(&path).expect("create isolated AppData");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700))
                .expect("private AppData permissions");
        }
        Self(path)
    }
}

impl Drop for TemporaryAppData {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[test]
fn snapshot_is_exact_path_free_and_component_typed() {
    let snapshot = StartupEnvironmentSnapshot::new(
        AppDataStartupState::Ready,
        ExecutorStartupState::ConfigurationRequired,
        EmbeddedBrowserStartupState::VersionIncompatible,
    );

    assert_eq!(
        serde_json::to_value(snapshot).expect("serialize snapshot"),
        serde_json::json!({
            "appData": "ready",
            "executor": "configuration_required",
            "embeddedBrowser": "version_incompatible",
        })
    );
}

#[test]
fn app_data_probe_revalidates_private_directory_without_exposing_it() {
    let app_data = TemporaryAppData::new();
    let service = StartupEnvironmentService::initialize(&app_data.0).expect("startup service");

    assert_eq!(service.app_data_state(), AppDataStartupState::Ready);
    assert_eq!(
        format!("{service:?}"),
        "StartupEnvironmentService(<redacted>)"
    );

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&app_data.0, std::fs::Permissions::from_mode(0o755))
            .expect("broaden AppData permissions");
        assert_eq!(service.app_data_state(), AppDataStartupState::Unavailable);
    }
}

#[test]
fn unsafe_or_relative_app_data_is_rejected_without_path_reflection() {
    let relative = StartupEnvironmentService::initialize(std::path::Path::new("relative"))
        .expect_err("relative AppData must fail");
    assert_eq!(relative.to_string(), "startup environment is unavailable");
    assert!(!format!("{relative:?}").contains("relative"));

    #[cfg(unix)]
    {
        use std::os::unix::fs::symlink;
        let app_data = TemporaryAppData::new();
        let link = app_data.0.with_extension("link");
        symlink(&app_data.0, &link).expect("create AppData symlink");
        let error =
            StartupEnvironmentService::initialize(&link).expect_err("symlink AppData must fail");
        assert_eq!(error.to_string(), "startup environment is unavailable");
        std::fs::remove_file(link).expect("remove AppData symlink");
    }
}

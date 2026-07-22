use std::sync::{Arc, Condvar, Mutex};

use automation_tool_desktop_lib::app_update_cache::DownloadSource;
use automation_tool_desktop_lib::app_update_installation::{
    AppUpdateInstallationCoordinator, UpdateInstallError, UpdateInstallErrorCode,
    UpdateInstallLifecycle, UpdatePackageInstaller, VerifiedUpdatePackageProvider,
};
use automation_tool_desktop_lib::app_updates::{parse_update_release, UpdateRelease};

fn release() -> UpdateRelease {
    parse_update_release(
        "0.2.0",
        &serde_json::json!({
            "version": "0.2.0",
            "url": "https://updates.example.test/automation-tool.tar.gz",
            "signature": "signed-package",
            "update_contract": {
                "version": 1,
                "channel": "stable",
                "policy": "optional",
                "artifact": {
                    "target": "darwin",
                    "arch": "aarch64",
                    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "size_bytes": 7
                }
            }
        }),
    )
    .expect("release")
}

fn source() -> DownloadSource {
    DownloadSource::new(
        "https://updates.example.test/automation-tool.tar.gz"
            .parse()
            .expect("url"),
        "signed-package".to_owned(),
    )
    .expect("source")
}

#[derive(Clone)]
struct EventLog(Arc<Mutex<Vec<&'static str>>>);

impl EventLog {
    fn new() -> Self {
        Self(Arc::new(Mutex::new(Vec::new())))
    }

    fn push(&self, event: &'static str) {
        self.0.lock().expect("event log").push(event);
    }

    fn snapshot(&self) -> Vec<&'static str> {
        self.0.lock().expect("event log").clone()
    }
}

struct PackageProvider {
    events: EventLog,
    fail: bool,
}

impl VerifiedUpdatePackageProvider for PackageProvider {
    fn read_verified_package(
        &self,
        _release: &UpdateRelease,
        _source: &DownloadSource,
    ) -> Result<Vec<u8>, UpdateInstallError> {
        self.events.push("read");
        if self.fail {
            Err(UpdateInstallError::new(
                UpdateInstallErrorCode::PackageUnavailable,
            ))
        } else {
            Ok(b"package".to_vec())
        }
    }
}

struct Lifecycle {
    events: EventLog,
    fail_prepare: bool,
}

impl UpdateInstallLifecycle for Lifecycle {
    fn prepare_for_install(&self) -> Result<(), UpdateInstallError> {
        self.events.push("prepare");
        if self.fail_prepare {
            Err(UpdateInstallError::new(
                UpdateInstallErrorCode::RuntimeShutdownFailed,
            ))
        } else {
            Ok(())
        }
    }

    fn complete_install(&self) -> Result<(), UpdateInstallError> {
        self.events.push("complete");
        Ok(())
    }

    fn recover_after_failure(&self) {
        self.events.push("recover");
    }
}

struct Installer {
    events: EventLog,
    fail: bool,
}

impl UpdatePackageInstaller for Installer {
    fn install(&self, bytes: Vec<u8>) -> Result<(), UpdateInstallError> {
        assert_eq!(bytes, b"package");
        self.events.push("install");
        if self.fail {
            Err(UpdateInstallError::new(
                UpdateInstallErrorCode::InstallerFailed,
            ))
        } else {
            Ok(())
        }
    }
}

fn coordinator(
    events: &EventLog,
    package_fails: bool,
    prepare_fails: bool,
) -> AppUpdateInstallationCoordinator {
    AppUpdateInstallationCoordinator::new(
        Arc::new(PackageProvider {
            events: events.clone(),
            fail: package_fails,
        }),
        Arc::new(Lifecycle {
            events: events.clone(),
            fail_prepare: prepare_fails,
        }),
    )
}

#[test]
fn verified_package_is_read_before_runtime_shutdown_and_official_install() {
    let events = EventLog::new();
    coordinator(&events, false, false)
        .install(
            &release(),
            &source(),
            Arc::new(Installer {
                events: events.clone(),
                fail: false,
            }),
        )
        .expect("installation handoff");

    assert_eq!(
        events.snapshot(),
        ["read", "prepare", "install", "complete"]
    );
}

#[test]
fn missing_or_corrupt_cache_never_stops_the_running_app() {
    let events = EventLog::new();
    let error = coordinator(&events, true, false)
        .install(
            &release(),
            &source(),
            Arc::new(Installer {
                events: events.clone(),
                fail: false,
            }),
        )
        .expect_err("cache failure must reject installation");

    assert_eq!(error.code(), UpdateInstallErrorCode::PackageUnavailable);
    assert_eq!(events.snapshot(), ["read"]);
}

#[test]
fn shutdown_or_installer_failure_is_closed_and_recovers_the_window() {
    let prepare_events = EventLog::new();
    let prepare_error = coordinator(&prepare_events, false, true)
        .install(
            &release(),
            &source(),
            Arc::new(Installer {
                events: prepare_events.clone(),
                fail: false,
            }),
        )
        .expect_err("shutdown failure must reject installation");
    assert_eq!(
        prepare_error.code(),
        UpdateInstallErrorCode::RuntimeShutdownFailed
    );
    assert_eq!(prepare_events.snapshot(), ["read", "prepare", "recover"]);

    let install_events = EventLog::new();
    let install_error = coordinator(&install_events, false, false)
        .install(
            &release(),
            &source(),
            Arc::new(Installer {
                events: install_events.clone(),
                fail: true,
            }),
        )
        .expect_err("installer failure must reject installation");
    assert_eq!(
        install_error.code(),
        UpdateInstallErrorCode::InstallerFailed
    );
    assert_eq!(
        install_events.snapshot(),
        ["read", "prepare", "install", "recover"]
    );
}

struct BlockingInstaller {
    entered: Arc<(Mutex<bool>, Condvar)>,
    release: Arc<(Mutex<bool>, Condvar)>,
}

impl UpdatePackageInstaller for BlockingInstaller {
    fn install(&self, _bytes: Vec<u8>) -> Result<(), UpdateInstallError> {
        let (entered, entered_signal) = &*self.entered;
        *entered.lock().expect("entered") = true;
        entered_signal.notify_one();
        let (release, release_signal) = &*self.release;
        let mut released = release.lock().expect("release");
        while !*released {
            released = release_signal.wait(released).expect("release wait");
        }
        Ok(())
    }
}

#[test]
fn overlapping_install_requests_are_coalesced() {
    let events = EventLog::new();
    let entered = Arc::new((Mutex::new(false), Condvar::new()));
    let release_install = Arc::new((Mutex::new(false), Condvar::new()));
    let coordinator = Arc::new(AppUpdateInstallationCoordinator::new(
        Arc::new(PackageProvider {
            events: events.clone(),
            fail: false,
        }),
        Arc::new(Lifecycle {
            events,
            fail_prepare: false,
        }),
    ));
    let installer: Arc<dyn UpdatePackageInstaller> = Arc::new(BlockingInstaller {
        entered: Arc::clone(&entered),
        release: Arc::clone(&release_install),
    });
    let first = {
        let coordinator = Arc::clone(&coordinator);
        let installer = Arc::clone(&installer);
        std::thread::spawn(move || coordinator.install(&release(), &source(), installer))
    };
    let (entered_lock, entered_signal) = &*entered;
    let mut has_entered = entered_lock.lock().expect("entered");
    while !*has_entered {
        has_entered = entered_signal.wait(has_entered).expect("entered wait");
    }
    drop(has_entered);

    let second = coordinator
        .install(&release(), &source(), Arc::clone(&installer))
        .expect_err("overlapping install must be rejected");
    assert_eq!(
        second.code(),
        UpdateInstallErrorCode::InstallationInProgress
    );

    let (release_lock, release_signal) = &*release_install;
    *release_lock.lock().expect("release") = true;
    release_signal.notify_one();
    first.join().expect("first thread").expect("first install");
}

#[test]
fn public_install_errors_do_not_reflect_paths_packages_or_internal_errors() {
    assert_eq!(
        UpdateInstallError::new(UpdateInstallErrorCode::PackageUnavailable).to_string(),
        "update installation unavailable"
    );
}

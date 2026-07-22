use std::error::Error;
use std::fmt::{Debug, Display, Formatter};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use tauri::Manager as _;

use crate::app_update_cache::{AppUpdateCache, DownloadSource};
use crate::app_updates::UpdateRelease;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum UpdateInstallErrorCode {
    PackageUnavailable,
    RuntimeShutdownFailed,
    InstallerFailed,
    InstallationInProgress,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct UpdateInstallError(UpdateInstallErrorCode);

impl UpdateInstallError {
    pub fn new(code: UpdateInstallErrorCode) -> Self {
        Self(code)
    }

    pub fn code(self) -> UpdateInstallErrorCode {
        self.0
    }
}

impl Display for UpdateInstallError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("update installation unavailable")
    }
}

impl Error for UpdateInstallError {}

pub trait VerifiedUpdatePackageProvider: Send + Sync {
    fn read_verified_package(
        &self,
        release: &UpdateRelease,
        source: &DownloadSource,
    ) -> Result<Vec<u8>, UpdateInstallError>;
}

impl VerifiedUpdatePackageProvider for AppUpdateCache {
    fn read_verified_package(
        &self,
        release: &UpdateRelease,
        source: &DownloadSource,
    ) -> Result<Vec<u8>, UpdateInstallError> {
        self.read_verified_package(release, source)
            .map_err(|_| UpdateInstallError::new(UpdateInstallErrorCode::PackageUnavailable))
    }
}

pub trait UpdateInstallLifecycle: Send + Sync {
    fn prepare_for_install(&self) -> Result<(), UpdateInstallError>;
    fn complete_install(&self) -> Result<(), UpdateInstallError>;
    fn recover_after_failure(&self);
}

pub trait UpdatePackageInstaller: Send + Sync {
    fn install(&self, bytes: Vec<u8>) -> Result<(), UpdateInstallError>;
}

pub struct OfficialUpdatePackageInstaller {
    update: tauri_plugin_updater::Update,
}

impl OfficialUpdatePackageInstaller {
    pub fn new(update: tauri_plugin_updater::Update) -> Self {
        Self { update }
    }
}

impl Debug for OfficialUpdatePackageInstaller {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("OfficialUpdatePackageInstaller(<redacted>)")
    }
}

impl UpdatePackageInstaller for OfficialUpdatePackageInstaller {
    fn install(&self, bytes: Vec<u8>) -> Result<(), UpdateInstallError> {
        self.update
            .install(bytes)
            .map_err(|_| UpdateInstallError::new(UpdateInstallErrorCode::InstallerFailed))
    }
}

pub struct TauriUpdateInstallLifecycle {
    app: tauri::AppHandle<tauri::Wry>,
    hidden_windows: Mutex<Vec<String>>,
    #[cfg(all(
        debug_assertions,
        feature = "desktop-e2e",
        not(feature = "control-plane-e2e")
    ))]
    install_probe: bool,
}

impl TauriUpdateInstallLifecycle {
    pub fn new(app: tauri::AppHandle<tauri::Wry>, install_probe: bool) -> Self {
        #[cfg(not(all(
            debug_assertions,
            feature = "desktop-e2e",
            not(feature = "control-plane-e2e")
        )))]
        let _ = install_probe;
        Self {
            app,
            hidden_windows: Mutex::new(Vec::new()),
            #[cfg(all(
                debug_assertions,
                feature = "desktop-e2e",
                not(feature = "control-plane-e2e")
            ))]
            install_probe,
        }
    }

    fn hide_windows(&self) -> Result<(), UpdateInstallError> {
        let mut hidden = self
            .hidden_windows
            .lock()
            .map_err(|_| UpdateInstallError::new(UpdateInstallErrorCode::RuntimeShutdownFailed))?;
        hidden.clear();
        for (label, window) in self.app.webview_windows() {
            let visible = window.is_visible().map_err(|_| {
                UpdateInstallError::new(UpdateInstallErrorCode::RuntimeShutdownFailed)
            })?;
            if visible {
                window.hide().map_err(|_| {
                    UpdateInstallError::new(UpdateInstallErrorCode::RuntimeShutdownFailed)
                })?;
                hidden.push(label);
            }
        }
        Ok(())
    }
}

impl UpdateInstallLifecycle for TauriUpdateInstallLifecycle {
    fn prepare_for_install(&self) -> Result<(), UpdateInstallError> {
        if let Err(error) = self.hide_windows() {
            self.recover_after_failure();
            return Err(error);
        }
        let Some(platform) = self
            .app
            .try_state::<crate::executor_platform::ExecutorPlatformService>()
        else {
            return Err(UpdateInstallError::new(
                UpdateInstallErrorCode::RuntimeShutdownFailed,
            ));
        };
        platform
            .shutdown_for_app_exit()
            .map_err(|_| UpdateInstallError::new(UpdateInstallErrorCode::RuntimeShutdownFailed))
    }

    fn complete_install(&self) -> Result<(), UpdateInstallError> {
        #[cfg(all(
            debug_assertions,
            feature = "desktop-e2e",
            not(feature = "control-plane-e2e")
        ))]
        if self.install_probe {
            return Ok(());
        }
        self.app.restart()
    }

    fn recover_after_failure(&self) {
        let labels = self
            .hidden_windows
            .lock()
            .map(|mut labels| std::mem::take(&mut *labels))
            .unwrap_or_default();
        for label in labels {
            if let Some(window) = self.app.get_webview_window(&label) {
                let _ = window.show();
            }
        }
    }
}

impl Debug for TauriUpdateInstallLifecycle {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("TauriUpdateInstallLifecycle(<redacted>)")
    }
}

pub struct AppUpdateInstallationCoordinator {
    packages: Arc<dyn VerifiedUpdatePackageProvider>,
    lifecycle: Arc<dyn UpdateInstallLifecycle>,
    installation_in_progress: AtomicBool,
}

impl AppUpdateInstallationCoordinator {
    pub fn new(
        packages: Arc<dyn VerifiedUpdatePackageProvider>,
        lifecycle: Arc<dyn UpdateInstallLifecycle>,
    ) -> Self {
        Self {
            packages,
            lifecycle,
            installation_in_progress: AtomicBool::new(false),
        }
    }

    pub fn install(
        &self,
        release: &UpdateRelease,
        source: &DownloadSource,
        installer: Arc<dyn UpdatePackageInstaller>,
    ) -> Result<(), UpdateInstallError> {
        if self
            .installation_in_progress
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err(UpdateInstallError::new(
                UpdateInstallErrorCode::InstallationInProgress,
            ));
        }
        let _guard = InstallationGuard(&self.installation_in_progress);
        let package = self.packages.read_verified_package(release, source)?;
        if let Err(error) = self.lifecycle.prepare_for_install() {
            self.lifecycle.recover_after_failure();
            return Err(error);
        }
        if let Err(error) = installer.install(package) {
            self.lifecycle.recover_after_failure();
            return Err(error);
        }
        if let Err(error) = self.lifecycle.complete_install() {
            self.lifecycle.recover_after_failure();
            return Err(error);
        }
        Ok(())
    }
}

impl Debug for AppUpdateInstallationCoordinator {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("AppUpdateInstallationCoordinator(<redacted>)")
    }
}

struct InstallationGuard<'a>(&'a AtomicBool);

impl Drop for InstallationGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

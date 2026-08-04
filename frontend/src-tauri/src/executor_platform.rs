//! App-private composition root for the one Local Executor instance.

use std::error::Error;
use std::fmt::{Display, Formatter};
use std::fs;
use std::path::{Component, Path, PathBuf};
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::sync::Mutex;
use std::time::Duration;

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine as _;
use uuid::{Uuid, Variant};

use crate::browser_profiles::{
    BrowserProfile, BrowserProfileError, BrowserProfileErrorCode, BrowserProfileLease,
};
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
use crate::control_plane::ExecutorConnectionMaterial;
use crate::executor_bootstrap::{
    ExecutorActionRuntimeInput, LocalPlatformCommand, LocalPlatformCommandResult,
};
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
use crate::executor_manager::ExecutorLaunchConfiguration;
use crate::executor_manager::{
    ExecutorManager, ExecutorManagerError, ExecutorManagerErrorCode, ExecutorManagerStatus,
    ExecutorRestartPolicy,
};
use crate::executor_package::ExecutorPackageVerifier;
use crate::runtime_compatibility::EXECUTOR_RUNTIME_VERSION_REQUIREMENT;
use crate::secure_store::{AppDataSecretStore, SecretStore};
use crate::startup_environment::ExecutorStartupState;
use serde::{Deserialize, Serialize};

const EXECUTOR_DIRECTORY: &str = "local-executor";
const EXECUTOR_IDENTITY_FILE: &str = "executor-id-v1";
const EXECUTOR_PACKAGE_DIRECTORY: &str = "package";
const EXECUTOR_STATE_DIRECTORY: &str = "state";
const TASK_EMERGENCY_STOP_FILE: &str = "task-emergency-stop-v1";
const TASK_EMERGENCY_STOP_VERSION: &str = "1";
const BROWSER_DIAGNOSTIC_SETTINGS_FILE: &str = "browser-diagnostic-settings-v1";
const BROWSER_DIAGNOSTIC_SETTINGS_VERSION: &str = "1";
const PREVIOUS_BROWSER_DIAGNOSTIC_SETTINGS_VERSION: &str = "0";
const EXECUTOR_START_TIMEOUT_SECONDS: u64 = 30;
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
const HEARTBEAT_INTERVAL_SECONDS: u8 = 15;

// This public key corresponds only to the checked-in development fixture signer. Release builds
// fail closed unless the real release verification key is supplied by the packaging pipeline.
#[cfg(debug_assertions)]
const DEVELOPMENT_EXECUTOR_VERIFYING_KEY: [u8; 32] = [
    3, 161, 7, 191, 243, 206, 16, 190, 29, 112, 221, 24, 231, 75, 192, 153, 103, 228, 214, 48, 155,
    165, 13, 95, 29, 220, 134, 100, 18, 85, 49, 184,
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExecutorPlatformErrorCode {
    ConfigurationInvalid,
    StorageUnavailable,
    AlreadyRunning,
    AuthenticationRejected,
    PackageRejected,
    ProcessUnavailable,
    TimedOut,
    /// The operations Profile still carries the lock marker of a run that never
    /// released it, so no lease can be taken until the operator clears it. It is
    /// separate from `StorageUnavailable` because it is the one Profile failure
    /// the operator can actually do something about.
    RecoveryRequired,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ExecutorPlatformError {
    code: ExecutorPlatformErrorCode,
}

impl ExecutorPlatformError {
    const fn new(code: ExecutorPlatformErrorCode) -> Self {
        Self { code }
    }

    pub const fn code(self) -> ExecutorPlatformErrorCode {
        self.code
    }
}

impl Display for ExecutorPlatformError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("Local Executor platform operation is unavailable")
    }
}

impl Error for ExecutorPlatformError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutorPlatformPaths {
    package_root: PathBuf,
    state_directory: PathBuf,
}

impl ExecutorPlatformPaths {
    pub fn from_app_data(app_data_directory: &Path) -> Result<Self, ExecutorPlatformError> {
        require_absolute_private_root(app_data_directory)?;
        let executor_root = app_data_directory.join(EXECUTOR_DIRECTORY);
        Self::from_app_data_and_package_root(
            app_data_directory,
            &executor_root.join(EXECUTOR_PACKAGE_DIRECTORY),
        )
    }

    pub fn from_app_data_and_package_root(
        app_data_directory: &Path,
        package_root: &Path,
    ) -> Result<Self, ExecutorPlatformError> {
        require_absolute_private_root(app_data_directory)?;
        require_absolute_private_root(package_root)?;
        let state_directory = app_data_directory
            .join(EXECUTOR_DIRECTORY)
            .join(EXECUTOR_STATE_DIRECTORY);
        if package_root == state_directory
            || package_root.starts_with(&state_directory)
            || state_directory.starts_with(package_root)
        {
            return Err(configuration_invalid());
        }
        Ok(Self {
            package_root: package_root.to_path_buf(),
            state_directory,
        })
    }

    pub fn package_root(&self) -> &Path {
        &self.package_root
    }

    pub fn state_directory(&self) -> &Path {
        &self.state_directory
    }
}

#[derive(Clone)]
pub struct LocalExecutorIdentity {
    executor_id: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PendingTaskEmergencyStop {
    version: String,
    task_id: String,
    idempotency_key: String,
}

impl PendingTaskEmergencyStop {
    fn new(task_id: &str, idempotency_key: &str) -> Result<Self, ExecutorPlatformError> {
        crate::control_plane::validate_task_control_input(task_id, idempotency_key)
            .map_err(|_| configuration_invalid())?;
        Ok(Self {
            version: TASK_EMERGENCY_STOP_VERSION.to_owned(),
            task_id: task_id.to_owned(),
            idempotency_key: idempotency_key.to_owned(),
        })
    }

    fn validate(self) -> Result<Self, ExecutorPlatformError> {
        if self.version != TASK_EMERGENCY_STOP_VERSION {
            return Err(storage_unavailable());
        }
        Self::new(&self.task_id, &self.idempotency_key).map_err(|_| storage_unavailable())
    }

    pub fn task_id(&self) -> &str {
        &self.task_id
    }

    pub fn idempotency_key(&self) -> &str {
        &self.idempotency_key
    }
}

struct TaskEmergencyStopState {
    store: AppDataSecretStore,
    pending: Option<PendingTaskEmergencyStop>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BrowserDiagnosticSettingsSnapshot {
    capture_successful_runs: bool,
}

impl BrowserDiagnosticSettingsSnapshot {
    pub const fn capture_successful_runs(self) -> bool {
        self.capture_successful_runs
    }
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct StoredBrowserDiagnosticSettings {
    version: String,
    capture_successful_runs: bool,
}

/// How much of a stored diagnostics document this build could still use.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum StoredSettingsReading {
    Current,
    /// An older layout whose value carried over unchanged.
    Migrated,
    /// Nothing usable, so the switch went back to its default.
    Reset,
}

impl StoredSettingsReading {
    fn recovery_event(self) -> Option<crate::app_logging::DesktopLogEvent> {
        match self {
            Self::Current => None,
            Self::Migrated => {
                Some(crate::app_logging::DesktopLogEvent::BrowserDiagnosticSettingsMigrated)
            }
            Self::Reset => {
                Some(crate::app_logging::DesktopLogEvent::BrowserDiagnosticSettingsReset)
            }
        }
    }
}

/// Reads the successful-run capture switch out of whatever is on disk.
///
/// A version this build does not know is what a rollback leaves behind, and a
/// malformed document is what a half-written or hand-edited file looks like.
/// Neither can stop the App from launching: the closed state for a switch that
/// decides whether to keep screenshots of successful runs is `false`, which is
/// exactly where a machine with no settings file at all starts. Refusing to
/// launch captures nothing either - it just also takes the product away.
fn read_browser_diagnostic_settings(source: &[u8]) -> (bool, StoredSettingsReading) {
    let Ok(stored) = serde_json::from_slice::<StoredBrowserDiagnosticSettings>(source) else {
        return (false, StoredSettingsReading::Reset);
    };
    match stored.version.as_str() {
        BROWSER_DIAGNOSTIC_SETTINGS_VERSION => (
            stored.capture_successful_runs,
            StoredSettingsReading::Current,
        ),
        PREVIOUS_BROWSER_DIAGNOSTIC_SETTINGS_VERSION => (
            stored.capture_successful_runs,
            StoredSettingsReading::Migrated,
        ),
        _ => (false, StoredSettingsReading::Reset),
    }
}

struct BrowserDiagnosticSettingsState {
    store: AppDataSecretStore,
    capture_successful_runs: bool,
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
struct TaskEmergencyStopReconciliationGuard {
    active: Arc<AtomicBool>,
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
impl Drop for TaskEmergencyStopReconciliationGuard {
    fn drop(&mut self) {
        self.active.store(false, Ordering::Release);
    }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
pub(crate) struct TaskEmergencyStopReconciliationClaim {
    _guard: TaskEmergencyStopReconciliationGuard,
    pending: PendingTaskEmergencyStop,
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
impl TaskEmergencyStopReconciliationClaim {
    pub(crate) fn pending(&self) -> &PendingTaskEmergencyStop {
        &self.pending
    }
}

impl LocalExecutorIdentity {
    pub fn load_or_create(app_data_directory: &Path) -> Result<Self, ExecutorPlatformError> {
        require_absolute_private_root(app_data_directory)?;
        let store = AppDataSecretStore::new(
            &app_data_directory.join(EXECUTOR_DIRECTORY),
            EXECUTOR_IDENTITY_FILE,
        )
        .map_err(|_| storage_unavailable())?;
        let executor_id = match store.load().map_err(|_| storage_unavailable())? {
            Some(stored) => {
                let value = std::str::from_utf8(&stored).map_err(|_| storage_unavailable())?;
                require_canonical_uuid_v4(value)?;
                value.to_owned()
            }
            None => {
                let value = generate_uuid_v4()?;
                store
                    .save(value.as_bytes())
                    .map_err(|_| storage_unavailable())?;
                value
            }
        };
        Ok(Self { executor_id })
    }

    pub fn executor_id(&self) -> &str {
        &self.executor_id
    }
}

#[derive(Clone)]
pub struct ExecutorPlatformService {
    manager: Arc<ExecutorManager>,
    platform_profile_lease: Arc<Mutex<Option<BrowserProfileLease>>>,
    task_emergency_stop: Arc<Mutex<TaskEmergencyStopState>>,
    browser_diagnostic_settings: Arc<Mutex<BrowserDiagnosticSettingsState>>,
    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    task_emergency_stop_reconciliation_active: Arc<AtomicBool>,
    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    paths: ExecutorPlatformPaths,
    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    identity: LocalExecutorIdentity,
}

/// Whether a preflight outcome means the filled page must stay open.
///
/// Only a *ready* preflight is waiting for the operator: it holds a filled form
/// that the approval will act on, so the profile stays leased. Every other
/// outcome - blocked, handed off, or anything unrecognized - is finished with
/// the browser, and a lease nobody will ever spend would lock the operations
/// profile until the App restarts.
pub fn publish_keeps_the_browser(state: &str) -> bool {
    state == "publish_pre_submit_ready"
}

impl ExecutorPlatformService {
    pub fn initialize(app_data_directory: &Path) -> Result<Self, ExecutorPlatformError> {
        let paths = ExecutorPlatformPaths::from_app_data(app_data_directory)?;
        Self::initialize_with_paths(app_data_directory, paths)
    }

    pub fn initialize_with_package_root(
        app_data_directory: &Path,
        package_root: &Path,
    ) -> Result<Self, ExecutorPlatformError> {
        let paths = ExecutorPlatformPaths::from_app_data_and_package_root(
            app_data_directory,
            package_root,
        )?;
        Self::initialize_with_paths(app_data_directory, paths)
    }

    fn initialize_with_paths(
        app_data_directory: &Path,
        paths: ExecutorPlatformPaths,
    ) -> Result<Self, ExecutorPlatformError> {
        #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
        let identity = LocalExecutorIdentity::load_or_create(app_data_directory)?;
        ensure_private_directory(paths.state_directory())?;
        let verifier = ExecutorPackageVerifier::new(
            executor_verifying_key()?,
            EXECUTOR_RUNTIME_VERSION_REQUIREMENT,
            None,
        )
        .map_err(|_| ExecutorPlatformError::new(ExecutorPlatformErrorCode::ConfigurationInvalid))?;
        let restart_policy =
            ExecutorRestartPolicy::new(2, Duration::from_secs(1), Duration::from_secs(1))
                .map_err(map_manager_error)?;
        let manager = ExecutorManager::new(
            paths.package_root().to_path_buf(),
            verifier,
            Duration::from_secs(EXECUTOR_START_TIMEOUT_SECONDS),
            Duration::from_secs(10),
            restart_policy,
        )
        .map_err(map_manager_error)?;
        let task_emergency_stop_store = AppDataSecretStore::new(
            &app_data_directory.join(EXECUTOR_DIRECTORY),
            TASK_EMERGENCY_STOP_FILE,
        )
        .map_err(|_| storage_unavailable())?;
        let pending_task_emergency_stop = match task_emergency_stop_store
            .load()
            .map_err(|_| storage_unavailable())?
        {
            None => None,
            Some(source) => {
                let pending = serde_json::from_slice::<PendingTaskEmergencyStop>(&source)
                    .ok()
                    .and_then(|record| record.validate().ok());
                if pending.is_none() {
                    // A record written by a build whose format this one does not
                    // know names a task we cannot identify, so there is nothing
                    // left to reconcile from it. Refusing to launch would not
                    // reconcile it either - it would only take away the App the
                    // user needs to stop the task again.
                    let _ = task_emergency_stop_store.delete();
                    crate::app_logging::record(
                        crate::app_logging::DesktopLogEvent::TaskEmergencyStopRecordDropped,
                    );
                }
                pending
            }
        };
        let browser_diagnostic_settings_store = AppDataSecretStore::new(
            &app_data_directory.join(EXECUTOR_DIRECTORY),
            BROWSER_DIAGNOSTIC_SETTINGS_FILE,
        )
        .map_err(|_| storage_unavailable())?;
        let capture_successful_runs = match browser_diagnostic_settings_store
            .load()
            .map_err(|_| storage_unavailable())?
        {
            None => false,
            Some(source) => {
                let (capture_successful_runs, reading) = read_browser_diagnostic_settings(&source);
                if let Some(event) = reading.recovery_event() {
                    // Best effort on purpose: a machine with no settings file at
                    // all launches without writing anything, so a rewrite that
                    // fails here must not be worse than that. The value above is
                    // already the recovered one, and the next launch recovers
                    // again from the same file.
                    if let Ok(rewritten) = serde_json::to_vec(&StoredBrowserDiagnosticSettings {
                        version: BROWSER_DIAGNOSTIC_SETTINGS_VERSION.to_owned(),
                        capture_successful_runs,
                    }) {
                        let _ = browser_diagnostic_settings_store.save(&rewritten);
                    }
                    crate::app_logging::record(event);
                }
                capture_successful_runs
            }
        };
        Ok(Self {
            manager: Arc::new(manager),
            platform_profile_lease: Arc::new(Mutex::new(None)),
            task_emergency_stop: Arc::new(Mutex::new(TaskEmergencyStopState {
                store: task_emergency_stop_store,
                pending: pending_task_emergency_stop,
            })),
            browser_diagnostic_settings: Arc::new(Mutex::new(BrowserDiagnosticSettingsState {
                store: browser_diagnostic_settings_store,
                capture_successful_runs,
            })),
            #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
            task_emergency_stop_reconciliation_active: Arc::new(AtomicBool::new(false)),
            #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
            paths,
            #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
            identity,
        })
    }

    pub fn status(&self) -> Result<ExecutorManagerStatus, ExecutorPlatformError> {
        self.manager.status().map_err(map_manager_error)
    }

    pub fn startup_environment_state(&self) -> ExecutorStartupState {
        match ExecutorActionRuntimeInput::from_compile_time_configuration() {
            Ok(Some(_)) => crate::app_logging::record(
                crate::app_logging::DesktopLogEvent::StartupExecutorConfigurationReady,
            ),
            Ok(None) => {
                crate::app_logging::record(
                    crate::app_logging::DesktopLogEvent::StartupExecutorConfigurationRejected,
                );
                return ExecutorStartupState::ConfigurationRequired;
            }
            Err(_) => {
                crate::app_logging::record(
                    crate::app_logging::DesktopLogEvent::StartupExecutorConfigurationRejected,
                );
                return ExecutorStartupState::Unavailable;
            }
        }
        if self.manager.status().is_err() {
            crate::app_logging::record(
                crate::app_logging::DesktopLogEvent::StartupExecutorManagerStatusRejected,
            );
            return ExecutorStartupState::Unavailable;
        }
        crate::app_logging::record(
            crate::app_logging::DesktopLogEvent::StartupExecutorManagerStatusReady,
        );
        if self.manager.validate_installed_package().is_err() {
            crate::app_logging::record(
                crate::app_logging::DesktopLogEvent::StartupExecutorPackageRejected,
            );
            return ExecutorStartupState::Unavailable;
        }
        crate::app_logging::record(
            crate::app_logging::DesktopLogEvent::StartupExecutorPackageReady,
        );
        ExecutorStartupState::Ready
    }

    /// The verified Executor entrypoint, for a one-shot run of that binary.
    pub fn verified_entrypoint(&self) -> Result<std::path::PathBuf, ExecutorPlatformError> {
        self.manager
            .verified_entrypoint()
            .map_err(map_manager_error)
    }

    pub fn diagnostics(&self) -> Result<Vec<String>, ExecutorPlatformError> {
        self.manager.diagnostics().map_err(map_manager_error)
    }

    pub fn browser_diagnostic_settings(
        &self,
    ) -> Result<BrowserDiagnosticSettingsSnapshot, ExecutorPlatformError> {
        self.browser_diagnostic_settings
            .lock()
            .map(|state| BrowserDiagnosticSettingsSnapshot {
                capture_successful_runs: state.capture_successful_runs,
            })
            .map_err(|_| storage_unavailable())
    }

    pub fn set_capture_successful_diagnostics(
        &self,
        enabled: bool,
    ) -> Result<BrowserDiagnosticSettingsSnapshot, ExecutorPlatformError> {
        let mut state = self
            .browser_diagnostic_settings
            .lock()
            .map_err(|_| storage_unavailable())?;
        let serialized = serde_json::to_vec(&StoredBrowserDiagnosticSettings {
            version: BROWSER_DIAGNOSTIC_SETTINGS_VERSION.to_owned(),
            capture_successful_runs: enabled,
        })
        .map_err(|_| storage_unavailable())?;
        state
            .store
            .save(&serialized)
            .map_err(|_| storage_unavailable())?;
        state.capture_successful_runs = enabled;
        Ok(BrowserDiagnosticSettingsSnapshot {
            capture_successful_runs: enabled,
        })
    }

    #[cfg(feature = "control-plane-e2e")]
    pub fn inject_raw_diagnostic_for_acceptance(&self, raw: &[u8]) {
        self.manager.inject_raw_diagnostic_for_acceptance(raw);
    }

    pub fn emergency_stop(&self) -> Result<ExecutorManagerStatus, ExecutorPlatformError> {
        let result = self.manager.emergency_stop().map_err(map_manager_error);
        let release = self.release_platform_profile();
        match (result, release) {
            (Ok(status), Ok(())) => Ok(status),
            (Err(error), _) | (_, Err(error)) => Err(error),
        }
    }

    pub fn engage_task_emergency_stop(
        &self,
        task_id: &str,
        idempotency_key: &str,
    ) -> Result<ExecutorManagerStatus, ExecutorPlatformError> {
        let requested = PendingTaskEmergencyStop::new(task_id, idempotency_key)?;
        {
            let mut state = self
                .task_emergency_stop
                .lock()
                .map_err(|_| storage_unavailable())?;
            match state.pending.as_ref() {
                Some(current) if current == &requested => {}
                Some(_) => return Err(configuration_invalid()),
                None => {
                    let serialized =
                        serde_json::to_vec(&requested).map_err(|_| storage_unavailable())?;
                    state
                        .store
                        .save(&serialized)
                        .map_err(|_| storage_unavailable())?;
                    state.pending = Some(requested);
                }
            }
        }
        self.emergency_stop()
    }

    pub fn pending_task_emergency_stop(
        &self,
    ) -> Result<Option<PendingTaskEmergencyStop>, ExecutorPlatformError> {
        self.task_emergency_stop
            .lock()
            .map(|state| state.pending.clone())
            .map_err(|_| storage_unavailable())
    }

    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    pub(crate) fn begin_task_emergency_stop_reconciliation(
        &self,
    ) -> Result<Option<TaskEmergencyStopReconciliationClaim>, ExecutorPlatformError> {
        let Some(guard) = self
            .task_emergency_stop_reconciliation_active
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .ok()
            .map(|_| TaskEmergencyStopReconciliationGuard {
                active: Arc::clone(&self.task_emergency_stop_reconciliation_active),
            })
        else {
            return Ok(None);
        };
        let pending = self.pending_task_emergency_stop()?;
        Ok(pending.map(|pending| TaskEmergencyStopReconciliationClaim {
            _guard: guard,
            pending,
        }))
    }

    pub fn shutdown_for_app_exit(&self) -> Result<(), ExecutorPlatformError> {
        let stop = self.manager.stop().map_err(map_manager_error);
        let release = self.release_platform_profile();
        match (stop, release) {
            (Ok(_), Ok(())) => Ok(()),
            (Err(error), _) | (_, Err(error)) => Err(error),
        }
    }

    pub fn execute_platform_command(
        &self,
        command: LocalPlatformCommand,
        executable_path: PathBuf,
        profile: BrowserProfile,
        headless: bool,
    ) -> Result<LocalPlatformCommandResult, ExecutorPlatformError> {
        let profile_directory = profile.directory().to_path_buf();
        // A login that reached `healthy` is finished with the browser; a login
        // still waiting for a scan is not, and must keep the profile.
        self.under_profile_lease(
            profile,
            |manager| {
                manager
                    .execute_platform_command(command, executable_path, profile_directory, headless)
                    .map_err(map_manager_error)
            },
            |state| state == "healthy",
        )
    }

    /// Open the publish page and stop before submission.
    ///
    /// Unlike a login, a *ready* preflight deliberately keeps the profile:
    /// the filled page has to still be there when the operator approves.
    #[allow(clippy::too_many_arguments)]
    pub fn execute_publish_command(
        &self,
        publish_job_id: String,
        executable_path: PathBuf,
        profile: BrowserProfile,
        headless: bool,
        artifact_path: PathBuf,
        title: String,
        description: String,
    ) -> Result<LocalPlatformCommandResult, ExecutorPlatformError> {
        let profile_directory = profile.directory().to_path_buf();
        self.under_profile_lease(
            profile,
            |manager| {
                manager
                    .execute_publish_command(
                        publish_job_id,
                        executable_path,
                        profile_directory,
                        headless,
                        artifact_path,
                        title,
                        description,
                    )
                    .map_err(map_manager_error)
            },
            |state| !publish_keeps_the_browser(state),
        )
    }

    /// Spend one approval on one click; the page is already open and leased.
    pub fn execute_publish_dispatch_command(
        &self,
        publish_job_id: String,
        confirmation_id: String,
    ) -> Result<LocalPlatformCommandResult, ExecutorPlatformError> {
        let result = self
            .manager
            .execute_publish_dispatch_command(publish_job_id, confirmation_id)
            .map_err(map_manager_error);
        // Whatever the outcome, the click has been spent and the page is done.
        self.settle_lease(result, |_| true)
    }

    /// Give the operations browser back without publishing anything.
    pub fn release_publish_surface(
        &self,
    ) -> Result<LocalPlatformCommandResult, ExecutorPlatformError> {
        let result = self
            .manager
            .execute_session_command(LocalPlatformCommand::ReleaseDouyinPublishSurface)
            .map_err(map_manager_error);
        self.settle_lease(result, |_| true)
    }

    /// Run one command while this service owns the operations profile.
    ///
    /// A second controller asking for a different profile is refused rather
    /// than served: there is one visible operations browser, and two owners of
    /// it is how a publish ends up typed into somebody else's page.
    fn under_profile_lease(
        &self,
        profile: BrowserProfile,
        run: impl FnOnce(&ExecutorManager) -> Result<LocalPlatformCommandResult, ExecutorPlatformError>,
        finished: impl FnOnce(&str) -> bool,
    ) -> Result<LocalPlatformCommandResult, ExecutorPlatformError> {
        let profile_id = profile.profile_id().to_owned();
        let profile_directory = profile.directory().to_path_buf();
        let mut lease = self
            .platform_profile_lease
            .lock()
            .map_err(|_| storage_unavailable())?;
        if let Some(current) = lease.as_ref() {
            if current.profile_id() != profile_id || current.directory() != profile_directory {
                // Someone else owns the one operations browser. Their lease is
                // theirs; failing here must not reach into it.
                return Err(configuration_invalid());
            }
        } else {
            *lease = Some(
                profile
                    .try_acquire_owned_lock()
                    .map_err(map_profile_error)?,
            );
        }
        let result = run(&self.manager);
        let release = match result.as_ref() {
            Err(_) => true,
            Ok(value) => finished(value.state()),
        };
        if release {
            let held = lease.take();
            drop(lease);
            if let Some(held) = held {
                held.release().map_err(map_profile_error)?;
            }
        }
        result
    }

    /// Release the held profile when this outcome means the browser is done.
    fn settle_lease(
        &self,
        result: Result<LocalPlatformCommandResult, ExecutorPlatformError>,
        finished: impl FnOnce(&str) -> bool,
    ) -> Result<LocalPlatformCommandResult, ExecutorPlatformError> {
        let release = match result.as_ref() {
            Err(_) => true,
            Ok(value) => finished(value.state()),
        };
        if release {
            let mut lease = self
                .platform_profile_lease
                .lock()
                .map_err(|_| storage_unavailable())?;
            let held = lease.take();
            drop(lease);
            if let Some(held) = held {
                held.release().map_err(map_profile_error)?;
            }
        }
        result
    }

    pub fn execute_session_command(
        &self,
        command: LocalPlatformCommand,
    ) -> Result<LocalPlatformCommandResult, ExecutorPlatformError> {
        if command != LocalPlatformCommand::CompleteDouyinLogout {
            return Err(configuration_invalid());
        }
        let result = self
            .manager
            .execute_session_command(command)
            .map_err(map_manager_error);
        // PC-25：注销完成即结算 App 持有的 Profile 租约。此前只有紧急停止会
        // 顺带清租约，于是「不杀执行器的安全注销」删 Profile 时撞上自己的锁
        //（b5_13 实测 profile_in_use ×7）。语义与 under_profile_lease 一致：
        // logged_out 是完成态，错误路径上执行器也已尽力关闭浏览器——都放。
        self.settle_lease(result, |state| state == "logged_out")
    }

    #[cfg(feature = "control-plane-e2e")]
    pub fn inject_crash_for_acceptance(&self) -> Result<(), ExecutorPlatformError> {
        self.manager
            .inject_crash_for_acceptance()
            .map_err(map_manager_error)
    }

    #[cfg(feature = "control-plane-e2e")]
    pub fn inject_hang_for_acceptance(&self) -> Result<(), ExecutorPlatformError> {
        self.manager
            .inject_hang_for_acceptance()
            .map_err(map_manager_error)
    }

    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    pub(crate) fn restart(
        &self,
        connection: ExecutorConnectionMaterial,
    ) -> Result<ExecutorManagerStatus, ExecutorPlatformError> {
        if self.pending_task_emergency_stop()?.is_some() {
            return Err(configuration_invalid());
        }
        self.manager.stop().map_err(map_manager_error)?;
        self.release_platform_profile()?;
        let (websocket_url, session_token, installation_id) = connection.into_parts();
        let launch = ExecutorLaunchConfiguration::new_with_secret(
            websocket_url,
            session_token,
            installation_id,
            self.identity.executor_id().to_owned(),
            self.paths.state_directory().to_path_buf(),
            HEARTBEAT_INTERVAL_SECONDS,
        )
        .map_err(map_manager_error)?
        .with_capture_successful_diagnostics(
            self.browser_diagnostic_settings()?
                .capture_successful_runs(),
        );
        self.manager.start(launch).map_err(map_manager_error)
    }

    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    pub(crate) fn restart_for_task_emergency_stop(
        &self,
        connection: ExecutorConnectionMaterial,
        expected: &PendingTaskEmergencyStop,
    ) -> Result<ExecutorManagerStatus, ExecutorPlatformError> {
        if self.pending_task_emergency_stop()?.as_ref() != Some(expected) {
            return Err(configuration_invalid());
        }
        self.manager.emergency_stop().map_err(map_manager_error)?;
        self.release_platform_profile()?;
        let (websocket_url, session_token, installation_id) = connection.into_parts();
        let launch = ExecutorLaunchConfiguration::new_emergency_report_with_secret(
            websocket_url,
            session_token,
            installation_id,
            self.identity.executor_id().to_owned(),
            self.paths.state_directory().to_path_buf(),
            HEARTBEAT_INTERVAL_SECONDS,
        )
        .map_err(map_manager_error)?;
        let status = self.manager.start(launch).map_err(map_manager_error)?;
        let cleared = (|| {
            let mut state = self
                .task_emergency_stop
                .lock()
                .map_err(|_| storage_unavailable())?;
            if state.pending.as_ref() != Some(expected) {
                return Err(configuration_invalid());
            }
            state.store.delete().map_err(|_| storage_unavailable())?;
            state.pending = None;
            Ok(status)
        })();
        if cleared.is_err() {
            let _ = self.manager.emergency_stop();
        }
        cleared
    }

    fn release_platform_profile(&self) -> Result<(), ExecutorPlatformError> {
        let held = self
            .platform_profile_lease
            .lock()
            .map_err(|_| storage_unavailable())?
            .take();
        if let Some(held) = held {
            held.release().map_err(map_profile_error)?;
        }
        Ok(())
    }
}

fn map_profile_error(error: BrowserProfileError) -> ExecutorPlatformError {
    match error.code() {
        BrowserProfileErrorCode::ProfileInUse => {
            ExecutorPlatformError::new(ExecutorPlatformErrorCode::AlreadyRunning)
        }
        BrowserProfileErrorCode::RecoveryRequired => {
            ExecutorPlatformError::new(ExecutorPlatformErrorCode::RecoveryRequired)
        }
        BrowserProfileErrorCode::InvalidProfileId
        | BrowserProfileErrorCode::ProfileNotFound
        | BrowserProfileErrorCode::UnsafeDirectory
        | BrowserProfileErrorCode::IdentityChanged
        | BrowserProfileErrorCode::StorageUnavailable => storage_unavailable(),
    }
}

fn require_absolute_private_root(path: &Path) -> Result<(), ExecutorPlatformError> {
    let encoded = path.to_str().ok_or_else(configuration_invalid)?;
    if !path.is_absolute()
        || path.parent().is_none()
        || encoded.is_empty()
        || encoded.len() > 4096
        || encoded.chars().any(|character| {
            let codepoint = character as u32;
            character.is_control()
                || (0x202a..=0x202e).contains(&codepoint)
                || (0x2066..=0x2069).contains(&codepoint)
        })
        || path
            .components()
            .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
    {
        return Err(configuration_invalid());
    }
    Ok(())
}

fn generate_uuid_v4() -> Result<String, ExecutorPlatformError> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes).map_err(|_| storage_unavailable())?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(Uuid::from_bytes(bytes).hyphenated().to_string())
}

fn require_canonical_uuid_v4(source: &str) -> Result<(), ExecutorPlatformError> {
    let parsed = Uuid::parse_str(source).map_err(|_| storage_unavailable())?;
    if parsed.get_version_num() != 4
        || parsed.get_variant() != Variant::RFC4122
        || parsed.hyphenated().to_string() != source
    {
        return Err(storage_unavailable());
    }
    Ok(())
}

fn ensure_private_directory(path: &Path) -> Result<(), ExecutorPlatformError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
            return Err(storage_unavailable());
        }
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            create_private_directory(path)?;
        }
        Err(_) => return Err(storage_unavailable()),
    }
    set_private_directory_permissions(path)
}

#[cfg(unix)]
fn create_private_directory(path: &Path) -> Result<(), ExecutorPlatformError> {
    use std::os::unix::fs::DirBuilderExt;

    let mut builder = fs::DirBuilder::new();
    builder.recursive(true).mode(0o700);
    builder.create(path).map_err(|_| storage_unavailable())
}

#[cfg(not(unix))]
fn create_private_directory(path: &Path) -> Result<(), ExecutorPlatformError> {
    fs::create_dir_all(path).map_err(|_| storage_unavailable())
}

#[cfg(unix)]
fn set_private_directory_permissions(path: &Path) -> Result<(), ExecutorPlatformError> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(|_| storage_unavailable())
}

#[cfg(not(unix))]
fn set_private_directory_permissions(_path: &Path) -> Result<(), ExecutorPlatformError> {
    Ok(())
}

#[cfg(debug_assertions)]
fn executor_verifying_key() -> Result<[u8; 32], ExecutorPlatformError> {
    configured_debug_executor_verifying_key(option_env!("AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY"))
}

#[cfg(debug_assertions)]
fn configured_debug_executor_verifying_key(
    configured: Option<&str>,
) -> Result<[u8; 32], ExecutorPlatformError> {
    configured.map_or(
        Ok(DEVELOPMENT_EXECUTOR_VERIFYING_KEY),
        decode_executor_verifying_key,
    )
}

#[cfg(not(debug_assertions))]
fn executor_verifying_key() -> Result<[u8; 32], ExecutorPlatformError> {
    let encoded =
        option_env!("AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY").ok_or_else(configuration_invalid)?;
    decode_executor_verifying_key(encoded)
}

fn decode_executor_verifying_key(encoded: &str) -> Result<[u8; 32], ExecutorPlatformError> {
    let decoded = URL_SAFE_NO_PAD
        .decode(encoded)
        .map_err(|_| configuration_invalid())?;
    decoded.try_into().map_err(|_| configuration_invalid())
}

fn map_manager_error(error: ExecutorManagerError) -> ExecutorPlatformError {
    let code = match error.code() {
        ExecutorManagerErrorCode::AlreadyRunning => ExecutorPlatformErrorCode::AlreadyRunning,
        ExecutorManagerErrorCode::AuthenticationRejected => {
            ExecutorPlatformErrorCode::AuthenticationRejected
        }
        ExecutorManagerErrorCode::ConfigurationInvalid => {
            ExecutorPlatformErrorCode::ConfigurationInvalid
        }
        ExecutorManagerErrorCode::PackageRejected => ExecutorPlatformErrorCode::PackageRejected,
        ExecutorManagerErrorCode::ProcessUnavailable => {
            ExecutorPlatformErrorCode::ProcessUnavailable
        }
        ExecutorManagerErrorCode::TimedOut => ExecutorPlatformErrorCode::TimedOut,
    };
    ExecutorPlatformError::new(code)
}

const fn configuration_invalid() -> ExecutorPlatformError {
    ExecutorPlatformError::new(ExecutorPlatformErrorCode::ConfigurationInvalid)
}

const fn storage_unavailable() -> ExecutorPlatformError {
    ExecutorPlatformError::new(ExecutorPlatformErrorCode::StorageUnavailable)
}

#[cfg(all(test, any(not(feature = "desktop-e2e"), feature = "control-plane-e2e")))]
mod tests {
    #[cfg(debug_assertions)]
    use super::{configured_debug_executor_verifying_key, DEVELOPMENT_EXECUTOR_VERIFYING_KEY};
    use super::{map_profile_error, ExecutorPlatformService};
    use crate::browser_profiles::BrowserProfileError;
    use crate::startup_environment::ExecutorStartupState;
    #[cfg(debug_assertions)]
    use base64::engine::general_purpose::URL_SAFE_NO_PAD;
    #[cfg(debug_assertions)]
    use base64::Engine as _;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    #[cfg(debug_assertions)]
    #[test]
    fn debug_package_uses_the_configured_executor_verifying_key_when_present() {
        let expected = [7_u8; 32];
        let encoded = URL_SAFE_NO_PAD.encode(expected);

        assert_eq!(
            configured_debug_executor_verifying_key(Some(&encoded))
                .expect("configured acceptance key"),
            expected
        );
        assert_eq!(
            configured_debug_executor_verifying_key(None).expect("development fixture key"),
            DEVELOPMENT_EXECUTOR_VERIFYING_KEY
        );
    }

    /// T109: an abandoned Profile lease is recoverable, and saying so is the
    /// whole point.
    ///
    /// An App that exits while it holds the operations Profile leaves the
    /// on-disk lock marker `active` — `browser_profile_locks.rs` pins that
    /// deliberately. The next lease then fails with `RecoveryRequired`, for both
    /// 抖音 buttons, on every press, until someone runs 安全注销. Folding it into
    /// the generic storage failure removed the one fact the operator needed and
    /// left the page telling them to retry something that can never succeed.
    #[test]
    fn a_profile_that_needs_explicit_recovery_keeps_its_own_reason() {
        let recovery = map_profile_error(BrowserProfileError::recovery_required());
        let storage = map_profile_error(BrowserProfileError::storage_unavailable());

        assert_ne!(
            recovery.code(),
            storage.code(),
            "a 浏览器档案 awaiting explicit recovery must not arrive as a generic storage \
             failure: the operator can clear the first with 安全注销 and can do nothing about \
             the second"
        );
        assert_eq!(
            crate::map_executor_platform_error(recovery).code,
            "profile_recovery_required",
            "the App must name the recoverable state so the page can say how to clear it"
        );
    }

    static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(0);

    struct TemporaryAppData(PathBuf);

    impl TemporaryAppData {
        fn new() -> Self {
            // 必须是解析过的真实路径：Profile 目录逐级用 `O_NOFOLLOW` 打开，而
            // macOS 的 `$TMPDIR` 挂在 `/var` 这个符号链接下，未解析时 ELOOP。
            Self(
                std::env::temp_dir()
                    .canonicalize()
                    .unwrap_or_else(|_| std::env::temp_dir())
                    .join(format!(
                        "automation-tool-h8-03-reconciliation-{}-{}-{}",
                        std::process::id(),
                        SystemTime::now()
                            .duration_since(UNIX_EPOCH)
                            .expect("system time")
                            .as_nanos(),
                        NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed),
                    )),
            )
        }
    }

    impl Drop for TemporaryAppData {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn emergency_stop_reconciliation_claim_owns_the_pending_snapshot_and_gate() {
        let app_data = TemporaryAppData::new();
        let service = ExecutorPlatformService::initialize(&app_data.0).expect("initialize service");
        service
            .engage_task_emergency_stop(
                "123e4567-e89b-42d3-a456-426614174005",
                "task:emergency-stop:h8-03-race",
            )
            .expect("engage emergency stop");

        let claim = service
            .begin_task_emergency_stop_reconciliation()
            .expect("read pending stop")
            .expect("claim pending stop");
        assert_eq!(
            claim.pending().task_id(),
            "123e4567-e89b-42d3-a456-426614174005"
        );
        assert!(service
            .begin_task_emergency_stop_reconciliation()
            .expect("busy reconciliation gate")
            .is_none());

        drop(claim);
        assert!(service
            .begin_task_emergency_stop_reconciliation()
            .expect("released reconciliation gate")
            .is_some());
    }

    #[test]
    fn successful_diagnostic_capture_defaults_off_and_persists_inside_app_data() {
        let app_data = TemporaryAppData::new();
        let service = ExecutorPlatformService::initialize(&app_data.0).expect("initialize service");
        assert!(!service
            .browser_diagnostic_settings()
            .expect("read settings")
            .capture_successful_runs());

        let updated = service
            .set_capture_successful_diagnostics(true)
            .expect("save settings");
        assert!(updated.capture_successful_runs());
        drop(service);

        let reopened = ExecutorPlatformService::initialize(&app_data.0).expect("reopen service");
        assert!(reopened
            .browser_diagnostic_settings()
            .expect("read persisted settings")
            .capture_successful_runs());
        let stored = std::fs::read(
            app_data
                .0
                .join("local-executor/browser-diagnostic-settings-v1"),
        )
        .expect("settings file");
        assert_eq!(
            std::str::from_utf8(&stored).expect("UTF-8 settings"),
            r#"{"version":"1","capture_successful_runs":true}"#
        );
    }

    #[test]
    fn previous_browser_diagnostic_settings_version_is_migrated_and_rewritten() {
        let app_data = TemporaryAppData::new();
        let service = ExecutorPlatformService::initialize(&app_data.0).expect("initialize service");
        service
            .set_capture_successful_diagnostics(true)
            .expect("create settings file");
        drop(service);
        let settings_path = app_data
            .0
            .join("local-executor/browser-diagnostic-settings-v1");
        std::fs::write(
            &settings_path,
            br#"{"version":"0","capture_successful_runs":true}"#,
        )
        .expect("write previous settings version");

        let reopened = ExecutorPlatformService::initialize(&app_data.0);
        assert!(
            reopened.is_ok(),
            "previous settings version must migrate instead of aborting startup"
        );
        assert!(reopened
            .expect("migrated service")
            .browser_diagnostic_settings()
            .expect("migrated settings")
            .capture_successful_runs());
        assert_eq!(
            std::fs::read_to_string(settings_path).expect("rewritten settings"),
            r#"{"version":"1","capture_successful_runs":true}"#
        );
    }

    #[test]
    fn a_diagnostic_settings_version_this_build_does_not_know_falls_back_to_capturing_nothing() {
        // Written by a newer build, read after a rollback. Refusing to launch is
        // not the closed state for a capture switch - `false` is, which is also
        // where a machine with no settings file at all starts.
        let app_data = TemporaryAppData::new();
        let service = ExecutorPlatformService::initialize(&app_data.0).expect("initialize service");
        service
            .set_capture_successful_diagnostics(true)
            .expect("create settings file");
        drop(service);
        let settings_path = app_data
            .0
            .join("local-executor/browser-diagnostic-settings-v1");
        std::fs::write(
            &settings_path,
            br#"{"version":"future","capture_successful_runs":true}"#,
        )
        .expect("write unknown settings version");

        let reopened = ExecutorPlatformService::initialize(&app_data.0);
        assert!(
            reopened.is_ok(),
            "an unknown settings version must fall back instead of aborting startup"
        );
        assert!(!reopened
            .expect("recovered service")
            .browser_diagnostic_settings()
            .expect("recovered settings")
            .capture_successful_runs());
        assert_eq!(
            std::fs::read_to_string(settings_path).expect("rewritten settings"),
            r#"{"version":"1","capture_successful_runs":false}"#
        );
    }

    #[test]
    fn a_malformed_diagnostic_settings_file_falls_back_to_capturing_nothing() {
        let app_data = TemporaryAppData::new();
        let service = ExecutorPlatformService::initialize(&app_data.0).expect("initialize service");
        service
            .set_capture_successful_diagnostics(true)
            .expect("save settings");
        drop(service);
        let settings_path = app_data
            .0
            .join("local-executor/browser-diagnostic-settings-v1");
        std::fs::write(
            &settings_path,
            br#"{"version":"1","capture_successful_runs":1}"#,
        )
        .expect("corrupt settings");

        let reopened = ExecutorPlatformService::initialize(&app_data.0);
        assert!(
            reopened.is_ok(),
            "malformed settings must fall back instead of aborting startup"
        );
        assert!(!reopened
            .expect("recovered service")
            .browser_diagnostic_settings()
            .expect("recovered settings")
            .capture_successful_runs());
        assert_eq!(
            std::fs::read_to_string(settings_path).expect("rewritten settings"),
            r#"{"version":"1","capture_successful_runs":false}"#
        );
    }

    #[test]
    fn a_pending_emergency_stop_record_this_build_cannot_read_is_dropped_not_fatal() {
        // Keeping the App shut leaves the stop unreconciled and takes away the
        // one control the user has left. Dropping the record loses the pending
        // reconciliation, but the executor died with the App and the user can
        // stop the task again from a workbench that opens.
        let app_data = TemporaryAppData::new();
        let service = ExecutorPlatformService::initialize(&app_data.0).expect("initialize service");
        service
            .engage_task_emergency_stop(
                "123e4567-e89b-42d3-a456-426614174005",
                "task:emergency-stop:t63-unreadable",
            )
            .expect("engage emergency stop");
        drop(service);
        let record_path = app_data.0.join("local-executor/task-emergency-stop-v1");
        std::fs::write(
            &record_path,
            br#"{"version":"future","task_id":"123e4567-e89b-42d3-a456-426614174005","idempotency_key":"task:emergency-stop:t63-unreadable"}"#,
        )
        .expect("write unreadable pending stop");

        let reopened = ExecutorPlatformService::initialize(&app_data.0);
        assert!(
            reopened.is_ok(),
            "an unreadable pending stop must be dropped instead of aborting startup"
        );
        assert!(reopened
            .expect("recovered service")
            .begin_task_emergency_stop_reconciliation()
            .expect("reconciliation gate")
            .is_none());
        assert!(!record_path.exists());
    }

    #[test]
    fn startup_diagnostic_reports_missing_action_trust_without_starting_executor() {
        let app_data = TemporaryAppData::new();
        let service = ExecutorPlatformService::initialize(&app_data.0).expect("initialize service");

        assert_eq!(
            service.startup_environment_state(),
            ExecutorStartupState::ConfigurationRequired
        );
        assert_eq!(
            service.status().expect("manager status").state(),
            crate::executor_manager::ExecutorManagerState::Stopped
        );
    }

    /// PB-07 遗留项：租约的**拒绝**分支。
    ///
    /// `under_profile_lease` 的注释写着「第二个控制者要别的 Profile 时应被拒绝而
    /// 不是被服务」，但此前没有任何测试走过那一条 `return`——已有覆盖全部落在
    /// 「拿得到租约」的顺路径上。这条分支有两件事必须同时成立，缺一件就等于
    /// 把一次发布打进别人的页面：
    ///
    /// 1. 换一个 Profile 的请求被拒；
    /// 2. **这次拒绝不能碰已持有的那份租约**——`return` 走在释放逻辑之前正是为此，
    ///    而这一点用「返回了错误」是断言不出来的，必须去看租约还在不在、还能不能用。
    ///
    /// 测试用真实执行器进程：租约只有在命令**成功**且状态要求留住浏览器时才会
    /// 存活，任何替身在这里都等于自己编造前提。
    #[cfg(target_os = "macos")]
    #[test]
    fn a_second_controller_asking_for_another_profile_is_refused_without_touching_the_held_lease() {
        use super::{ExecutorPlatformErrorCode, LocalPlatformCommand};
        use crate::browser_profiles::BrowserProfileStore;
        use crate::executor_manager::ExecutorLaunchConfiguration;

        let app_data = TemporaryAppData::new();
        write_publish_fixture_package(&app_data.0.join("local-executor/package"));
        let service = ExecutorPlatformService::initialize(&app_data.0).expect("initialize service");
        service
            .manager
            .start(
                ExecutorLaunchConfiguration::new(
                    "ws://127.0.0.1:8765/api/v1/executors/connect".to_owned(),
                    "atds1.private-control-plane-session".to_owned(),
                    "123e4567-e89b-42d3-a456-426614174003".to_owned(),
                    "123e4567-e89b-42d3-a456-426614174004".to_owned(),
                    app_data.0.join("local-executor/state"),
                    1,
                )
                .expect("launch configuration"),
            )
            .expect("start publish fixture executor");

        let profiles = BrowserProfileStore::initialize(&app_data.0).expect("profile store");
        let held = profiles.create_douyin_profile().expect("first profile");
        let other = profiles.create_douyin_profile().expect("second profile");
        let held_id = held.profile_id().to_owned();
        let held_directory = held.directory().to_path_buf();
        assert_ne!(held_id, other.profile_id(), "两个 Profile 必须真的不同");

        let entrypoint = app_data
            .0
            .join("local-executor/package/automation-tool-executor");
        let artifact = app_data.0.join("finished.mp4");
        std::fs::write(&artifact, b"finished video bytes").expect("write artifact");
        let publish_job_id = "3f155810-eb7c-42d5-8f15-9ab345036f1e";

        // 第一个控制者把浏览器停在填好的发布页上，租约按 PB-07 的语义留住。
        let ready = service
            .execute_publish_command(
                publish_job_id.to_owned(),
                entrypoint.clone(),
                held,
                true,
                artifact.clone(),
                "标题".to_owned(),
                "简介".to_owned(),
            )
            .expect("preflight keeps the operations browser");
        assert_eq!(ready.state(), "publish_pre_submit_ready");

        // 第二个控制者要另一个 Profile：拒绝。
        let refused = service
            .execute_platform_command(
                LocalPlatformCommand::OpenDouyinLogin,
                entrypoint.clone(),
                other,
                true,
            )
            .expect_err("a different profile must be refused while one is leased");
        assert_eq!(
            refused.code(),
            ExecutorPlatformErrorCode::ConfigurationInvalid
        );

        // 拒绝之后租约必须原封不动地还在第一个控制者手上。
        {
            let lease = service
                .platform_profile_lease
                .lock()
                .expect("read the lease after the refusal");
            let still_held = lease
                .as_ref()
                .expect("被拒的请求把别人的租约释放掉了：那正是这个分支要防的事");
            assert_eq!(still_held.profile_id(), held_id);
            assert_eq!(still_held.directory(), held_directory);
        }

        // 同一个 Profile 回来要继续用，应当被服务而不是被拒——这是判据的另一半，
        // 否则「一律拒绝」也能让上面三条断言通过。
        let same_profile = profiles
            .open_douyin_profile(&held_id)
            .expect("reopen the leased profile");
        let served = service
            .execute_publish_command(
                publish_job_id.to_owned(),
                entrypoint,
                same_profile,
                true,
                artifact,
                "标题".to_owned(),
                "简介".to_owned(),
            )
            .expect("the controller that holds the lease must still be served");
        assert_eq!(served.state(), "publish_pre_submit_ready");

        // 并且它仍然花得掉：拒绝没有让这条链路变成一把只能看的锁。
        let dispatched = service
            .execute_publish_dispatch_command(
                publish_job_id.to_owned(),
                "9c1e4f4f-1077-4f4a-a51c-da7c14edb07b".to_owned(),
            )
            .expect("dispatch the approval the lease was held for");
        assert_eq!(dispatched.state(), "publish_verified");
        assert!(
            service
                .platform_profile_lease
                .lock()
                .expect("read the settled lease")
                .is_none(),
            "派发花掉了这次批准，浏览器必须交还"
        );

        service.manager.stop().expect("stop publish fixture");
    }

    /// 认得发布预检与派发的执行器桩，由**开发签名者**签名——调试构建的
    /// `executor_verifying_key` 只认这一把，所以包必须真的通过验证才能起进程。
    #[cfg(target_os = "macos")]
    const PUBLISH_LEASE_FIXTURE: &str = r#"#!/usr/bin/env python3
import base64, hashlib, hmac, json, signal, sys
bootstrap = json.loads(sys.stdin.readline())
key = bytes.fromhex(bootstrap["local_session_token"])
def encoded(domain, parts):
    message = domain + b"\0".join(part.encode() for part in parts)
    return "atlcp1." + base64.urlsafe_b64encode(hmac.digest(key, message, hashlib.sha256)).rstrip(b"=").decode()
def lifecycle(event):
    message = b"automation-tool.local-executor-event.v1\0" + event.encode() + b"\0" + b"1.0"
    proof = "atlep1." + base64.urlsafe_b64encode(hmac.digest(key, message, hashlib.sha256)).rstrip(b"=").decode()
    print(json.dumps({"authenticationProof": proof, "event": event, "protocolVersion": "1.0"}, separators=(",", ":")), flush=True)
signal.signal(signal.SIGTERM, lambda _signum, _frame: None)
lifecycle("executor.healthy")
for line in sys.stdin:
    command = json.loads(line)
    kind = command["commandType"]
    if kind == "douyin.publish.preflight":
        parts = [command["commandId"], kind, command["executablePath"], command["profileDirectory"],
                 "1" if command["headless"] else "0", command["publishJobId"], command["artifactPath"],
                 command["title"], command["description"], command["protocolVersion"]]
        domain = b"automation-tool.local-executor-publish-command.v1\0"
        state = "publish_pre_submit_ready"
        flow = "douyin.publish-preflight.v1"
    elif kind == "douyin.publish.dispatch":
        parts = [command["commandId"], kind, command["publishJobId"], command["confirmationId"], command["protocolVersion"]]
        domain = b"automation-tool.local-executor-publish-dispatch.v1\0"
        state = "publish_verified"
        flow = "douyin.publish-release.v1"
    else:
        # 被拒的那条请求永远不该走到这里；真到了就让测试当场看见。
        raise AssertionError(kind)
    assert hmac.compare_digest(command["authenticationProof"], encoded(domain, parts)), kind
    result = {"authenticationProof": encoded(b"automation-tool.local-executor-result.v1\0", [command["commandId"], state, "1.0"]),
              "commandId": command["commandId"], "event": "platform.command.completed",
              "flowVersion": flow, "platform": "douyin",
              "protocolVersion": "1.0", "state": state}
    print(json.dumps(result, separators=(",", ":"), sort_keys=True), flush=True)
lifecycle("executor.stopped")
"#;

    #[cfg(target_os = "macos")]
    fn write_publish_fixture_package(package_root: &std::path::Path) {
        use base64::engine::general_purpose::URL_SAFE_NO_PAD;
        use base64::Engine as _;
        use ed25519_dalek::{Signer, SigningKey};
        use sha2::{Digest, Sha256};
        use std::os::unix::fs::PermissionsExt;

        // 开发夹具签名者：其公钥即 `DEVELOPMENT_EXECUTOR_VERIFYING_KEY`。
        const DEVELOPMENT_SIGNER_SEED: [u8; 32] = [
            0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
            24, 25, 26, 27, 28, 29, 30, 31,
        ];
        const ENTRYPOINT: &str = "automation-tool-executor";

        std::fs::create_dir_all(package_root).expect("fixture package root");
        let entrypoint = package_root.join(ENTRYPOINT);
        std::fs::write(&entrypoint, PUBLISH_LEASE_FIXTURE).expect("fixture entrypoint");
        std::fs::set_permissions(&entrypoint, std::fs::Permissions::from_mode(0o755))
            .expect("executable fixture");

        let contents = std::fs::read(&entrypoint).expect("fixture contents");
        let file_hash = Sha256::digest(&contents);
        let mut package_digest = Sha256::new();
        package_digest.update(b"automation-tool.executor-package.v1\0");
        package_digest.update((ENTRYPOINT.len() as u32).to_be_bytes());
        package_digest.update(ENTRYPOINT.as_bytes());
        package_digest.update((contents.len() as u64).to_be_bytes());
        package_digest.update(file_hash);
        let hex =
            |bytes: &[u8]| -> String { bytes.iter().map(|byte| format!("{byte:02x}")).collect() };
        let manifest = serde_json::json!({
            "architecture": if cfg!(target_arch = "x86_64") { "x86_64" } else { "aarch64" },
            "build_id": "pb-07-lease-refusal",
            "entrypoint": ENTRYPOINT,
            "executor_version": "0.1.0",
            "files": [{
                "path": ENTRYPOINT,
                "sha256": hex(&file_hash),
                "size": contents.len(),
            }],
            "manifest_version": "1",
            "package_sha256": hex(&package_digest.finalize()),
            "package_size": contents.len(),
            "platform": "macos",
        });
        let mut manifest_bytes = serde_json::to_vec(&manifest).expect("manifest JSON");
        manifest_bytes.push(b'\n');
        let signature = SigningKey::from_bytes(&DEVELOPMENT_SIGNER_SEED).sign(&manifest_bytes);
        std::fs::write(
            package_root.join("executor-manifest.v1.json"),
            &manifest_bytes,
        )
        .expect("manifest");
        std::fs::write(
            package_root.join("executor-manifest.v1.sig"),
            format!("atems1.{}\n", URL_SAFE_NO_PAD.encode(signature.to_bytes())),
        )
        .expect("signature");
    }
}

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

#[cfg(not(debug_assertions))]
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
#[cfg(not(debug_assertions))]
use base64::Engine as _;
use uuid::{Uuid, Variant};

use crate::browser_profiles::{
    BrowserProfile, BrowserProfileError, BrowserProfileErrorCode, BrowserProfileLease,
};
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
use crate::control_plane::ExecutorConnectionMaterial;
use crate::executor_bootstrap::{LocalPlatformCommand, LocalPlatformCommandResult};
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
use crate::executor_manager::ExecutorLaunchConfiguration;
use crate::executor_manager::{
    ExecutorManager, ExecutorManagerError, ExecutorManagerErrorCode, ExecutorManagerStatus,
    ExecutorRestartPolicy,
};
use crate::executor_package::ExecutorPackageVerifier;
use crate::secure_store::{AppDataSecretStore, SecretStore};
use serde::{Deserialize, Serialize};

const EXECUTOR_DIRECTORY: &str = "local-executor";
const EXECUTOR_IDENTITY_FILE: &str = "executor-id-v1";
const EXECUTOR_PACKAGE_DIRECTORY: &str = "package";
const EXECUTOR_STATE_DIRECTORY: &str = "state";
const TASK_EMERGENCY_STOP_FILE: &str = "task-emergency-stop-v1";
const TASK_EMERGENCY_STOP_VERSION: &str = "1";
const EXECUTOR_START_TIMEOUT_SECONDS: u64 = 30;
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
const HEARTBEAT_INTERVAL_SECONDS: u8 = 15;
const ALLOWED_EXECUTOR_VERSION: &str = "=0.1.0";

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
        Ok(Self {
            package_root: executor_root.join(EXECUTOR_PACKAGE_DIRECTORY),
            state_directory: executor_root.join(EXECUTOR_STATE_DIRECTORY),
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
    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    task_emergency_stop_reconciliation_active: Arc<AtomicBool>,
    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    paths: ExecutorPlatformPaths,
    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    identity: LocalExecutorIdentity,
}

impl ExecutorPlatformService {
    pub fn initialize(app_data_directory: &Path) -> Result<Self, ExecutorPlatformError> {
        let paths = ExecutorPlatformPaths::from_app_data(app_data_directory)?;
        #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
        let identity = LocalExecutorIdentity::load_or_create(app_data_directory)?;
        ensure_private_directory(paths.state_directory())?;
        let verifier =
            ExecutorPackageVerifier::new(executor_verifying_key()?, ALLOWED_EXECUTOR_VERSION, None)
                .map_err(|_| {
                    ExecutorPlatformError::new(ExecutorPlatformErrorCode::ConfigurationInvalid)
                })?;
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
        let pending_task_emergency_stop = task_emergency_stop_store
            .load()
            .map_err(|_| storage_unavailable())?
            .map(|source| {
                serde_json::from_slice::<PendingTaskEmergencyStop>(&source)
                    .map_err(|_| storage_unavailable())?
                    .validate()
            })
            .transpose()?;
        Ok(Self {
            manager: Arc::new(manager),
            platform_profile_lease: Arc::new(Mutex::new(None)),
            task_emergency_stop: Arc::new(Mutex::new(TaskEmergencyStopState {
                store: task_emergency_stop_store,
                pending: pending_task_emergency_stop,
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

    pub fn diagnostics(&self) -> Result<Vec<String>, ExecutorPlatformError> {
        self.manager.diagnostics().map_err(map_manager_error)
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

    pub fn shutdown_for_app_exit(&self) {
        let _ = self.manager.stop();
        let _ = self.release_platform_profile();
    }

    pub fn execute_platform_command(
        &self,
        command: LocalPlatformCommand,
        executable_path: PathBuf,
        profile: BrowserProfile,
        headless: bool,
    ) -> Result<LocalPlatformCommandResult, ExecutorPlatformError> {
        let profile_id = profile.profile_id().to_owned();
        let profile_directory = profile.directory().to_path_buf();
        let mut lease = self
            .platform_profile_lease
            .lock()
            .map_err(|_| storage_unavailable())?;
        if let Some(current) = lease.as_ref() {
            if current.profile_id() != profile_id || current.directory() != profile_directory {
                return Err(configuration_invalid());
            }
        } else {
            *lease = Some(
                profile
                    .try_acquire_owned_lock()
                    .map_err(map_profile_error)?,
            );
        }
        let result = self
            .manager
            .execute_platform_command(command, executable_path, profile_directory, headless)
            .map_err(map_manager_error);
        let release = result.is_err()
            || result
                .as_ref()
                .is_ok_and(|value| value.state() == "healthy");
        if release {
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
        self.manager
            .execute_session_command(command)
            .map_err(map_manager_error)
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
        .map_err(map_manager_error)?;
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
        BrowserProfileErrorCode::InvalidProfileId
        | BrowserProfileErrorCode::ProfileNotFound
        | BrowserProfileErrorCode::UnsafeDirectory
        | BrowserProfileErrorCode::IdentityChanged
        | BrowserProfileErrorCode::RecoveryRequired
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
    Ok(DEVELOPMENT_EXECUTOR_VERIFYING_KEY)
}

#[cfg(not(debug_assertions))]
fn executor_verifying_key() -> Result<[u8; 32], ExecutorPlatformError> {
    let encoded =
        option_env!("AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY").ok_or_else(configuration_invalid)?;
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
    use super::ExecutorPlatformService;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    struct TemporaryAppData(PathBuf);

    impl TemporaryAppData {
        fn new() -> Self {
            Self(std::env::temp_dir().join(format!(
                "automation-tool-h8-03-reconciliation-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .expect("system time")
                    .as_nanos(),
            )))
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
}

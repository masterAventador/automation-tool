//! Bounded desktop diagnostics made only from a closed set of fixed events.
//!
//! Callers can only enqueue an enum; they cannot attach strings or fields.
//! Errors are accepted only to make the safe call site obvious, and their
//! `Display`, `Debug`, and source chains are never evaluated. Disk I/O runs on
//! a dedicated bounded worker so diagnostics cannot delay product requests.

use std::error::Error;
use std::fs::{self, File, OpenOptions};
use std::io::{self, ErrorKind, Write};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, SyncSender, TrySendError};
use std::sync::OnceLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const LOG_DIRECTORY_NAME: &str = "logs";
const LOG_FILE_PREFIX: &str = "desktop-";
const LOG_FILE_MARKER: &str = ".log";
const MAX_LOG_FILE_BYTES: u64 = 1024 * 1024;
const RETAINED_ROTATIONS_PER_PROCESS_DAY: usize = 2;
const MAX_RETAINED_LOG_FILES: usize = 8;
const LOG_RETENTION_DAYS: u64 = 7;
const LOG_QUEUE_CAPACITY: usize = 256;
const LOG_MAINTENANCE_INTERVAL: Duration = Duration::from_secs(60);
const SECONDS_PER_DAY: u64 = 24 * 60 * 60;

static DESKTOP_LOG: OnceLock<DesktopLogDispatcher> = OnceLock::new();

#[derive(Clone, Copy)]
pub(crate) enum DesktopLogEvent {
    AppSetupStarted,
    ControlPlaneClientInitialized,
    ProfileDataDirectoryReady,
    UpdateCoordinatorInitialized,
    LocalServicesInitialized,
    WorkspaceInitialized,
    ExecutorServiceInitialized,
    CredentialsInitialized,
    AppSetupCompleted,
    // Startup found persisted state it could not use and put the App back into
    // a state it can launch from. Recovery that leaves no trace is the failure
    // this project keeps meeting, so each of these is worth one line.
    UpdatePolicyDocumentMigrated,
    UpdatePolicyDocumentReplaced,
    UpdateCacheStateRecovered,
    BrowserDiagnosticSettingsMigrated,
    BrowserDiagnosticSettingsReset,
    TaskEmergencyStopRecordDropped,
    ControlPlaneRequestFailed,
    ControlPlaneEventStreamFailed,
    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    ExecutorRestartRequested,
    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    ExecutorAutoStartRequested,
    ExecutorEmergencyStopRequested,
    // PC-25：安全注销的删除流程逐步打点。b5_13 的 profile_identity_changed
    // 用四轮插桩都定位不到是哪一步，因为删除的五个阶段在日志里是一片空白；
    // 这五个事件与上面的执行器生命周期事件同轴，一份日志给出完整时间线。
    ProfileRemovalStarted,
    ProfileRemovalStaged,
    ProfileRemovalDeleted,
    ProfileRemovalCompleted,
    ProfileRemovalRejected,
    ExecutorProcessStartRequested,
    ExecutorProcessStartSucceeded,
    ExecutorProcessStartFailed,
    ExecutorProcessExited,
    ExecutorRestartScheduled,
    AppShutdownStarted,
    TaskStatusDraft,
    TaskStatusValidating,
    TaskStatusAwaitingDevice,
    TaskStatusAwaitingPlatformLogin,
    TaskStatusDiscoveringTargets,
    TaskStatusAwaitingConfirmation,
    TaskStatusQueued,
    TaskStatusRunning,
    TaskStatusPaused,
    TaskStatusAwaitingHuman,
    TaskStatusCancelling,
    TaskStatusSucceeded,
    TaskStatusPartiallySucceeded,
    TaskStatusFailed,
    TaskStatusCancelled,
    TaskStatusOutcomeUncertain,
    TaskStatusUnknown,
}

impl DesktopLogEvent {
    const fn as_str(self) -> &'static str {
        match self {
            Self::AppSetupStarted => "app.setup.started",
            Self::ControlPlaneClientInitialized => "app.setup.control_plane_client.initialized",
            Self::ProfileDataDirectoryReady => "app.setup.profile_data_directory.ready",
            Self::UpdateCoordinatorInitialized => "app.setup.update_coordinator.initialized",
            Self::LocalServicesInitialized => "app.setup.local_services.initialized",
            Self::WorkspaceInitialized => "app.setup.workspace.initialized",
            Self::ExecutorServiceInitialized => "app.setup.executor_service.initialized",
            Self::CredentialsInitialized => "app.setup.credentials.initialized",
            Self::AppSetupCompleted => "app.setup.completed",
            Self::UpdatePolicyDocumentMigrated => "app_update.policy_document.migrated",
            Self::UpdatePolicyDocumentReplaced => "app_update.policy_document.replaced",
            Self::UpdateCacheStateRecovered => "app_update.cache_state.recovered",
            Self::BrowserDiagnosticSettingsMigrated => {
                "executor.browser_diagnostic_settings.migrated"
            }
            Self::BrowserDiagnosticSettingsReset => "executor.browser_diagnostic_settings.reset",
            Self::TaskEmergencyStopRecordDropped => "executor.task_emergency_stop_record.dropped",
            Self::ControlPlaneRequestFailed => "control_plane.request.failed",
            Self::ControlPlaneEventStreamFailed => "control_plane.event_stream.failed",
            #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
            Self::ExecutorRestartRequested => "executor.restart.requested",
            #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
            Self::ExecutorAutoStartRequested => "executor.auto_start.requested",
            Self::ExecutorEmergencyStopRequested => "executor.emergency_stop.requested",
            Self::ProfileRemovalStarted => "profile.removal.started",
            Self::ProfileRemovalStaged => "profile.removal.staged",
            Self::ProfileRemovalDeleted => "profile.removal.deleted",
            Self::ProfileRemovalCompleted => "profile.removal.completed",
            Self::ProfileRemovalRejected => "profile.removal.rejected",
            Self::ExecutorProcessStartRequested => "executor.process.start.requested",
            Self::ExecutorProcessStartSucceeded => "executor.process.start.succeeded",
            Self::ExecutorProcessStartFailed => "executor.process.start.failed",
            Self::ExecutorProcessExited => "executor.process.exited",
            Self::ExecutorRestartScheduled => "executor.restart.scheduled",
            Self::AppShutdownStarted => "app.shutdown.started",
            Self::TaskStatusDraft => "task.status.draft",
            Self::TaskStatusValidating => "task.status.validating",
            Self::TaskStatusAwaitingDevice => "task.status.awaiting_device",
            Self::TaskStatusAwaitingPlatformLogin => "task.status.awaiting_platform_login",
            Self::TaskStatusDiscoveringTargets => "task.status.discovering_targets",
            Self::TaskStatusAwaitingConfirmation => "task.status.awaiting_confirmation",
            Self::TaskStatusQueued => "task.status.queued",
            Self::TaskStatusRunning => "task.status.running",
            Self::TaskStatusPaused => "task.status.paused",
            Self::TaskStatusAwaitingHuman => "task.status.awaiting_human",
            Self::TaskStatusCancelling => "task.status.cancelling",
            Self::TaskStatusSucceeded => "task.status.succeeded",
            Self::TaskStatusPartiallySucceeded => "task.status.partially_succeeded",
            Self::TaskStatusFailed => "task.status.failed",
            Self::TaskStatusCancelled => "task.status.cancelled",
            Self::TaskStatusOutcomeUncertain => "task.status.outcome_uncertain",
            Self::TaskStatusUnknown => "task.status.unknown",
        }
    }
}

#[derive(Clone, Copy)]
struct LogPolicy {
    max_file_bytes: u64,
    retained_rotations_per_process_day: usize,
    max_retained_files: usize,
    retention_days: u64,
}

impl LogPolicy {
    const PRODUCTION: Self = Self {
        max_file_bytes: MAX_LOG_FILE_BYTES,
        retained_rotations_per_process_day: RETAINED_ROTATIONS_PER_PROCESS_DAY,
        max_retained_files: MAX_RETAINED_LOG_FILES,
        retention_days: LOG_RETENTION_DAYS,
    };
}

struct DesktopLogDispatcher {
    sender: SyncSender<DesktopLogEvent>,
}

impl DesktopLogDispatcher {
    fn try_record(&self, event: DesktopLogEvent) {
        match self.sender.try_send(event) {
            Ok(()) | Err(TrySendError::Full(_)) | Err(TrySendError::Disconnected(_)) => {}
        }
    }
}

struct DesktopLog {
    directory: PathBuf,
    process_id: u32,
    policy: LogPolicy,
}

impl DesktopLog {
    fn initialize(app_data_directory: &Path) -> io::Result<Self> {
        Self::initialize_with_policy(app_data_directory, LogPolicy::PRODUCTION)
    }

    fn initialize_with_policy(app_data_directory: &Path, policy: LogPolicy) -> io::Result<Self> {
        let directory = app_data_directory.join(LOG_DIRECTORY_NAME);
        ensure_private_directory(&directory)?;
        let mut logger = Self {
            directory,
            process_id: std::process::id(),
            policy,
        };
        logger.maintain(SystemTime::now())?;
        Ok(logger)
    }

    fn path(&self, day: u64, rotation: usize) -> PathBuf {
        let base = format!(
            "{LOG_FILE_PREFIX}{day}-{}{LOG_FILE_MARKER}",
            self.process_id
        );
        if rotation == 0 {
            self.directory.join(base)
        } else {
            self.directory.join(format!("{base}.{rotation}"))
        }
    }

    fn record(&mut self, event: DesktopLogEvent) -> io::Result<()> {
        self.record_at(event, SystemTime::now())
    }

    fn record_at(&mut self, event: DesktopLogEvent, now: SystemTime) -> io::Result<()> {
        self.maintain(now)?;
        let timestamp_millis = unix_duration(now).as_millis();
        let line = format!(
            "{{\"timestampUnixMs\":{timestamp_millis},\"event\":\"{}\"}}\n",
            event.as_str()
        );
        if line.len() as u64 > self.policy.max_file_bytes {
            return Err(io::Error::new(
                ErrorKind::InvalidInput,
                "fixed log entry exceeds file limit",
            ));
        }
        let day = unix_day(now);
        let path = self.path(day, 0);
        if safe_file_length(&path)?.is_some_and(|length| {
            length.saturating_add(line.len() as u64) > self.policy.max_file_bytes
        }) {
            self.rotate(day)?;
        }
        let mut file = open_private_log_file(&path)?;
        file.write_all(line.as_bytes())?;
        file.flush()?;
        self.maintain(now)
    }

    #[cfg(test)]
    fn record_failure(
        &mut self,
        event: DesktopLogEvent,
        _sensitive_error: &dyn Error,
    ) -> io::Result<()> {
        // Do not format, inspect or classify the error. Even a nominally safe
        // transport error can carry a URL or an upstream source chain.
        self.record(event)
    }

    fn rotate(&self, day: u64) -> io::Result<()> {
        if self.policy.retained_rotations_per_process_day == 0 {
            remove_regular_file_if_present(&self.path(day, 0))?;
            return Ok(());
        }
        remove_regular_file_if_present(
            &self.path(day, self.policy.retained_rotations_per_process_day),
        )?;
        for rotation in (1..self.policy.retained_rotations_per_process_day).rev() {
            rename_regular_file_if_present(
                &self.path(day, rotation),
                &self.path(day, rotation + 1),
            )?;
        }
        rename_regular_file_if_present(&self.path(day, 0), &self.path(day, 1))
    }

    fn maintain(&mut self, now: SystemTime) -> io::Result<()> {
        let current_day = unix_day(now);
        let mut retained = Vec::new();
        for entry in fs::read_dir(&self.directory)? {
            let entry = entry?;
            let Some((day, _, _)) = parse_log_file_name(&entry.file_name()) else {
                continue;
            };
            let path = entry.path();
            let metadata = safe_regular_file_metadata(&path)?.ok_or_else(|| {
                io::Error::new(ErrorKind::NotFound, "log disappeared during maintenance")
            })?;
            if day.saturating_add(self.policy.retention_days) <= current_day {
                fs::remove_file(path)?;
                continue;
            }
            retained.push((
                metadata.modified().unwrap_or(UNIX_EPOCH),
                entry.file_name(),
                path,
            ));
        }
        retained.sort_by(|left, right| left.0.cmp(&right.0).then_with(|| left.1.cmp(&right.1)));
        let excess = retained
            .len()
            .saturating_sub(self.policy.max_retained_files);
        for (_, _, path) in retained.into_iter().take(excess) {
            remove_regular_file_if_present(&path)?;
        }
        Ok(())
    }
}

pub(crate) fn initialize(app_data_directory: &Path) -> io::Result<()> {
    if DESKTOP_LOG.get().is_some() {
        return Ok(());
    }
    let mut logger = DesktopLog::initialize(app_data_directory)?;
    let (sender, receiver) = mpsc::sync_channel(LOG_QUEUE_CAPACITY);
    std::thread::Builder::new()
        .name("automation-tool-desktop-log".to_owned())
        .spawn(move || loop {
            match receiver.recv_timeout(LOG_MAINTENANCE_INTERVAL) {
                Ok(event) => {
                    let _ = logger.record(event);
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    let _ = logger.maintain(SystemTime::now());
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
            }
        })
        .map_err(io::Error::other)?;
    let _ = DESKTOP_LOG.set(DesktopLogDispatcher { sender });
    Ok(())
}

pub(crate) fn record(event: DesktopLogEvent) {
    if let Some(logger) = DESKTOP_LOG.get() {
        logger.try_record(event);
    }
}

pub(crate) fn record_failure(event: DesktopLogEvent, _sensitive_error: &dyn Error) {
    record(event);
}

pub(crate) fn record_task_status(status: &str) {
    record(task_status_event(status));
}

fn task_status_event(status: &str) -> DesktopLogEvent {
    match status {
        "draft" => DesktopLogEvent::TaskStatusDraft,
        "validating" => DesktopLogEvent::TaskStatusValidating,
        "awaiting_device" => DesktopLogEvent::TaskStatusAwaitingDevice,
        "awaiting_platform_login" => DesktopLogEvent::TaskStatusAwaitingPlatformLogin,
        "discovering_targets" => DesktopLogEvent::TaskStatusDiscoveringTargets,
        "awaiting_confirmation" => DesktopLogEvent::TaskStatusAwaitingConfirmation,
        "queued" => DesktopLogEvent::TaskStatusQueued,
        "running" => DesktopLogEvent::TaskStatusRunning,
        "paused" => DesktopLogEvent::TaskStatusPaused,
        "awaiting_human" => DesktopLogEvent::TaskStatusAwaitingHuman,
        "cancelling" => DesktopLogEvent::TaskStatusCancelling,
        "succeeded" => DesktopLogEvent::TaskStatusSucceeded,
        "partially_succeeded" => DesktopLogEvent::TaskStatusPartiallySucceeded,
        "failed" => DesktopLogEvent::TaskStatusFailed,
        "cancelled" => DesktopLogEvent::TaskStatusCancelled,
        "outcome_uncertain" => DesktopLogEvent::TaskStatusOutcomeUncertain,
        _ => DesktopLogEvent::TaskStatusUnknown,
    }
}

fn unix_duration(now: SystemTime) -> Duration {
    now.duration_since(UNIX_EPOCH).unwrap_or(Duration::ZERO)
}

fn unix_day(now: SystemTime) -> u64 {
    unix_duration(now).as_secs() / SECONDS_PER_DAY
}

fn parse_log_file_name(name: &std::ffi::OsStr) -> Option<(u64, u32, usize)> {
    let name = name.to_str()?.strip_prefix(LOG_FILE_PREFIX)?;
    let (identity, rotation) = match name.rsplit_once(LOG_FILE_MARKER) {
        Some((identity, "")) => (identity, 0),
        Some((identity, suffix)) => {
            let rotation = suffix.strip_prefix('.')?.parse::<usize>().ok()?;
            (identity, rotation)
        }
        None => return None,
    };
    let (day, process_id) = identity.rsplit_once('-')?;
    Some((day.parse().ok()?, process_id.parse().ok()?, rotation))
}

fn safe_regular_file_metadata(path: &Path) -> io::Result<Option<fs::Metadata>> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_file() => {
            Err(io::Error::new(ErrorKind::InvalidData, "unsafe log file"))
        }
        Ok(metadata) => Ok(Some(metadata)),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn safe_file_length(path: &Path) -> io::Result<Option<u64>> {
    safe_regular_file_metadata(path).map(|metadata| metadata.map(|item| item.len()))
}

fn ensure_private_directory(path: &Path) -> io::Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
            return Err(io::Error::new(
                ErrorKind::InvalidData,
                "unsafe log directory",
            ));
        }
        Ok(_) => {}
        Err(error) if error.kind() == ErrorKind::NotFound => create_private_directory(path)?,
        Err(error) => return Err(error),
    }
    secure_existing_directory(path)
}

#[cfg(unix)]
fn create_private_directory(path: &Path) -> io::Result<()> {
    use std::os::unix::fs::DirBuilderExt;

    let mut builder = fs::DirBuilder::new();
    builder.mode(0o700);
    builder.create(path)
}

#[cfg(not(unix))]
fn create_private_directory(path: &Path) -> io::Result<()> {
    fs::create_dir(path)
}

#[cfg(unix)]
fn secure_existing_directory(path: &Path) -> io::Result<()> {
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};

    let expected = fs::symlink_metadata(path)?;
    let directory = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)?;
    let opened = directory.metadata()?;
    if !opened.is_dir()
        || opened.dev() != expected.dev()
        || opened.ino() != expected.ino()
        || expected.file_type().is_symlink()
    {
        return Err(io::Error::new(
            ErrorKind::InvalidData,
            "log directory identity changed",
        ));
    }
    if unsafe { libc::fchmod(directory.as_raw_fd(), 0o700) } != 0 {
        return Err(io::Error::last_os_error());
    }
    if directory.metadata()?.permissions().mode() & 0o7777 != 0o700 {
        return Err(io::Error::new(
            ErrorKind::PermissionDenied,
            "log directory is not private",
        ));
    }
    Ok(())
}

#[cfg(windows)]
fn secure_existing_directory(path: &Path) -> io::Result<()> {
    use std::os::windows::fs::{MetadataExt, OpenOptionsExt};

    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;

    let directory = OpenOptions::new()
        .read(true)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS)
        .open(path)?;
    let metadata = directory.metadata()?;
    if !metadata.is_dir() || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(io::Error::new(
            ErrorKind::InvalidData,
            "unsafe log directory",
        ));
    }
    Ok(())
}

#[cfg(not(any(unix, windows)))]
fn secure_existing_directory(path: &Path) -> io::Result<()> {
    if fs::metadata(path)?.is_dir() {
        Ok(())
    } else {
        Err(io::Error::new(
            ErrorKind::InvalidData,
            "unsafe log directory",
        ))
    }
}

fn remove_regular_file_if_present(path: &Path) -> io::Result<()> {
    if safe_regular_file_metadata(path)?.is_some() {
        match fs::remove_file(path) {
            Ok(()) => {}
            Err(error) if error.kind() == ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

fn rename_regular_file_if_present(source: &Path, target: &Path) -> io::Result<()> {
    if safe_regular_file_metadata(source)?.is_none() {
        return Ok(());
    }
    remove_regular_file_if_present(target)?;
    match fs::rename(source, target) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

#[cfg(unix)]
fn open_private_log_file(path: &Path) -> io::Result<File> {
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};

    let expected = safe_regular_file_metadata(path)?;
    let file = OpenOptions::new()
        .create(true)
        .append(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .mode(0o600)
        .open(path)?;
    let opened = file.metadata()?;
    if !opened.is_file()
        || expected
            .as_ref()
            .is_some_and(|item| item.dev() != opened.dev() || item.ino() != opened.ino())
    {
        return Err(io::Error::new(
            ErrorKind::InvalidData,
            "log file identity changed",
        ));
    }
    if unsafe { libc::fchmod(file.as_raw_fd(), 0o600) } != 0 {
        return Err(io::Error::last_os_error());
    }
    if file.metadata()?.permissions().mode() & 0o077 != 0 {
        return Err(io::Error::new(
            ErrorKind::PermissionDenied,
            "log file is not private",
        ));
    }
    Ok(file)
}

#[cfg(windows)]
fn open_private_log_file(path: &Path) -> io::Result<File> {
    use std::os::windows::fs::{MetadataExt, OpenOptionsExt};

    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;

    let file = OpenOptions::new()
        .create(true)
        .append(true)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(io::Error::new(ErrorKind::InvalidData, "unsafe log file"));
    }
    Ok(file)
}

#[cfg(not(any(unix, windows)))]
fn open_private_log_file(path: &Path) -> io::Result<File> {
    safe_regular_file_metadata(path)?;
    OpenOptions::new().create(true).append(true).open(path)
}

#[cfg(test)]
mod tests {
    use std::fmt;
    use std::time::{SystemTime, UNIX_EPOCH};

    use super::*;

    struct SensitiveFailure(String);

    impl fmt::Display for SensitiveFailure {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter.write_str(&self.0)
        }
    }

    impl fmt::Debug for SensitiveFailure {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter.write_str(&self.0)
        }
    }

    impl Error for SensitiveFailure {}

    struct TestDirectory(PathBuf);

    impl TestDirectory {
        fn new(name: &str) -> Self {
            let nonce = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock after epoch")
                .as_nanos();
            let path = std::env::temp_dir().join(format!(
                "automation-tool-t69-{name}-{}-{nonce}",
                std::process::id()
            ));
            fs::create_dir(&path).expect("isolated App data");
            Self(path)
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn test_policy() -> LogPolicy {
        LogPolicy {
            max_file_bytes: 128,
            retained_rotations_per_process_day: 2,
            max_retained_files: 8,
            retention_days: 7,
        }
    }

    fn day(value: u64) -> SystemTime {
        UNIX_EPOCH + Duration::from_secs(value * SECONDS_PER_DAY)
    }

    #[test]
    fn a_token_bearing_error_cannot_reach_the_persisted_log() {
        let app_data = TestDirectory::new("privacy");
        let mut logger = DesktopLog::initialize(&app_data.0).expect("desktop logger");
        let secrets = [
            "Cookie=session-cookie",
            "token=executor-token",
            "message=private-platform-message",
            "contact=private-contact",
            "page=private-page-text",
            "path=/Users/customer/private-profile",
            "privateKey=private-device-key",
            "atas1.private-access-secret",
            "atrs1.private-refresh-secret",
        ];
        let failure = SensitiveFailure(secrets.join(" "));

        logger
            .record_failure(DesktopLogEvent::ControlPlaneRequestFailed, &failure)
            .expect("record fixed failure");

        let path = logger.path(unix_day(SystemTime::now()), 0);
        let persisted = fs::read_to_string(path).expect("persisted desktop log");
        assert!(persisted.contains("control_plane.request.failed"));
        for secret in secrets {
            assert!(
                !persisted.contains(secret),
                "sensitive error detail reached the desktop log: {secret}"
            );
        }
    }

    #[test]
    fn record_path_enforces_size_rotation_and_total_file_bounds() {
        let app_data = TestDirectory::new("rotation");
        let policy = test_policy();
        let mut logger =
            DesktopLog::initialize_with_policy(&app_data.0, policy).expect("desktop logger");

        for _ in 0..20 {
            logger
                .record_at(DesktopLogEvent::AppSetupStarted, day(100))
                .expect("bounded record");
        }

        let files = fs::read_dir(&logger.directory)
            .expect("log directory")
            .collect::<Result<Vec<_>, _>>()
            .expect("log entries");
        assert!(files.len() <= policy.max_retained_files);
        assert!(files.len() <= policy.retained_rotations_per_process_day + 1);
        for entry in files {
            assert!(entry.metadata().expect("retained log").len() <= policy.max_file_bytes);
        }
    }

    #[test]
    fn record_path_removes_a_previous_day_at_the_seven_day_boundary() {
        let app_data = TestDirectory::new("retention");
        let mut logger =
            DesktopLog::initialize_with_policy(&app_data.0, test_policy()).expect("desktop logger");
        logger
            .record_at(DesktopLogEvent::AppSetupStarted, day(100))
            .expect("old record");
        let old_path = logger.path(100, 0);
        assert!(old_path.is_file());

        logger
            .record_at(DesktopLogEvent::AppSetupCompleted, day(107))
            .expect("new record and maintenance");

        assert!(!old_path.exists());
        assert!(logger.path(107, 0).is_file());
    }

    #[test]
    fn arbitrary_task_status_text_is_never_selected_as_an_event() {
        assert_eq!(
            DesktopLogEvent::TaskStatusUnknown.as_str(),
            task_status_event("atas1.private-access-secret").as_str()
        );
    }

    #[test]
    fn a_full_dispatch_queue_drops_events_instead_of_waiting() {
        let (sender, _receiver) = mpsc::sync_channel(1);
        let dispatcher = DesktopLogDispatcher { sender };
        dispatcher.try_record(DesktopLogEvent::AppSetupStarted);
        dispatcher.try_record(DesktopLogEvent::AppSetupCompleted);
    }

    #[cfg(unix)]
    #[test]
    fn directory_and_file_links_are_rejected_without_touching_their_targets() {
        use std::os::unix::fs::symlink;

        let app_data = TestDirectory::new("links");
        let outside = app_data.0.join("outside");
        fs::create_dir(&outside).expect("outside directory");
        let sentinel = outside.join("sentinel");
        fs::write(&sentinel, b"must-survive").expect("outside sentinel");
        symlink(&outside, app_data.0.join(LOG_DIRECTORY_NAME)).expect("linked log directory");
        assert!(DesktopLog::initialize(&app_data.0).is_err());
        fs::remove_file(app_data.0.join(LOG_DIRECTORY_NAME)).expect("remove directory link");

        let mut logger = DesktopLog::initialize(&app_data.0).expect("desktop logger");
        let path = logger.path(unix_day(SystemTime::now()), 0);
        symlink(&sentinel, &path).expect("linked log file");
        assert!(logger.record(DesktopLogEvent::AppSetupStarted).is_err());
        assert_eq!(fs::read(sentinel).expect("outside data"), b"must-survive");
    }

    #[cfg(unix)]
    #[test]
    fn log_directory_and_file_are_private() {
        use std::os::unix::fs::PermissionsExt;

        let app_data = TestDirectory::new("permissions");
        let mut logger = DesktopLog::initialize(&app_data.0).expect("desktop logger");
        logger
            .record(DesktopLogEvent::AppSetupStarted)
            .expect("desktop record");

        assert_eq!(
            fs::metadata(&logger.directory)
                .expect("directory metadata")
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(logger.path(unix_day(SystemTime::now()), 0))
                .expect("file metadata")
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
}

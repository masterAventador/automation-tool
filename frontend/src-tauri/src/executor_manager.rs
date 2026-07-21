//! Linearized lifecycle for one verified Local Executor process.

use crate::executor_bootstrap::{
    ExecutorBootstrapErrorCode, ExecutorBootstrapInput, LocalExecutorEvent, LocalPlatformCommand,
    LocalPlatformCommandResult, LocalSessionToken,
};
use crate::executor_diagnostics::{ExecutorDiagnostics, MAX_DIAGNOSTIC_LINE_BYTES};
use crate::executor_package::{ExecutorPackageVerifier, VerifiedExecutorPackage};
use serde::{Deserialize, Serialize};
use std::fmt;
use std::io::{self, BufRead, BufReader, Read};
use std::path::PathBuf;
use std::process::{Child, ChildStderr, ChildStdin, ChildStdout, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};
use uuid::Uuid;
use zeroize::Zeroizing;

const EXECUTOR_PROTOCOL_VERSION: &str = "1.0";
const MAX_LIFECYCLE_LINE_BYTES: usize = 4096;
const MAX_LIFECYCLE_TIMEOUT: Duration = Duration::from_secs(60);
const MAX_RESTARTS: u8 = 8;
const PLATFORM_COMMAND_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExecutorManagerErrorCode {
    AlreadyRunning,
    AuthenticationRejected,
    ConfigurationInvalid,
    PackageRejected,
    ProcessUnavailable,
    TimedOut,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct ExecutorManagerError {
    code: ExecutorManagerErrorCode,
}

impl ExecutorManagerError {
    const fn new(code: ExecutorManagerErrorCode) -> Self {
        Self { code }
    }

    pub const fn code(self) -> ExecutorManagerErrorCode {
        self.code
    }
}

impl fmt::Debug for ExecutorManagerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ExecutorManagerError")
            .field("code", &self.code)
            .finish()
    }
}

impl fmt::Display for ExecutorManagerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Local Executor lifecycle is unavailable")
    }
}

impl std::error::Error for ExecutorManagerError {}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutorManagerState {
    Running,
    Restarting,
    Stopped,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecutorManagerStatus {
    state: ExecutorManagerState,
    version: Option<String>,
    build_id: Option<String>,
    restart_count: u8,
}

impl ExecutorManagerStatus {
    fn stopped(restart_count: u8) -> Self {
        Self {
            state: ExecutorManagerState::Stopped,
            version: None,
            build_id: None,
            restart_count,
        }
    }

    fn running(package: &VerifiedExecutorPackage, restart_count: u8) -> Self {
        Self {
            state: ExecutorManagerState::Running,
            version: Some(package.version().to_string()),
            build_id: Some(package.build_id().to_owned()),
            restart_count,
        }
    }

    fn restarting(running: &Self, restart_count: u8) -> Self {
        Self {
            state: ExecutorManagerState::Restarting,
            version: running.version.clone(),
            build_id: running.build_id.clone(),
            restart_count,
        }
    }

    pub const fn state(&self) -> ExecutorManagerState {
        self.state
    }

    pub fn version(&self) -> Option<&str> {
        self.version.as_deref()
    }

    pub fn build_id(&self) -> Option<&str> {
        self.build_id.as_deref()
    }

    pub const fn restart_count(&self) -> u8 {
        self.restart_count
    }
}

pub struct ExecutorLaunchConfiguration {
    websocket_url: String,
    control_plane_session: Zeroizing<String>,
    installation_id: String,
    executor_id: String,
    state_directory: PathBuf,
    heartbeat_interval_seconds: u8,
    local_emergency_stop: bool,
}

impl ExecutorLaunchConfiguration {
    pub fn new(
        websocket_url: String,
        control_plane_session: String,
        installation_id: String,
        executor_id: String,
        state_directory: PathBuf,
        heartbeat_interval_seconds: u8,
    ) -> Result<Self, ExecutorManagerError> {
        Self::new_with_secret(
            websocket_url,
            Zeroizing::new(control_plane_session),
            installation_id,
            executor_id,
            state_directory,
            heartbeat_interval_seconds,
        )
    }

    pub(crate) fn new_with_secret(
        websocket_url: String,
        control_plane_session: Zeroizing<String>,
        installation_id: String,
        executor_id: String,
        state_directory: PathBuf,
        heartbeat_interval_seconds: u8,
    ) -> Result<Self, ExecutorManagerError> {
        Self::new_with_secret_and_emergency_stop(
            websocket_url,
            control_plane_session,
            installation_id,
            executor_id,
            state_directory,
            heartbeat_interval_seconds,
            false,
        )
    }

    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    pub(crate) fn new_emergency_report_with_secret(
        websocket_url: String,
        control_plane_session: Zeroizing<String>,
        installation_id: String,
        executor_id: String,
        state_directory: PathBuf,
        heartbeat_interval_seconds: u8,
    ) -> Result<Self, ExecutorManagerError> {
        Self::new_with_secret_and_emergency_stop(
            websocket_url,
            control_plane_session,
            installation_id,
            executor_id,
            state_directory,
            heartbeat_interval_seconds,
            true,
        )
    }

    fn new_with_secret_and_emergency_stop(
        websocket_url: String,
        control_plane_session: Zeroizing<String>,
        installation_id: String,
        executor_id: String,
        state_directory: PathBuf,
        heartbeat_interval_seconds: u8,
        local_emergency_stop: bool,
    ) -> Result<Self, ExecutorManagerError> {
        let bootstrap = if local_emergency_stop {
            ExecutorBootstrapInput::new_emergency_report(
                &websocket_url,
                &control_plane_session,
                &installation_id,
                &executor_id,
                &state_directory,
                heartbeat_interval_seconds,
            )
        } else {
            ExecutorBootstrapInput::new(
                &websocket_url,
                &control_plane_session,
                &installation_id,
                &executor_id,
                &state_directory,
                heartbeat_interval_seconds,
            )
        };
        bootstrap.map_err(|_| {
            ExecutorManagerError::new(ExecutorManagerErrorCode::ConfigurationInvalid)
        })?;
        Ok(Self {
            websocket_url,
            control_plane_session,
            installation_id,
            executor_id,
            state_directory,
            heartbeat_interval_seconds,
            local_emergency_stop,
        })
    }

    fn bootstrap_input(
        &self,
        restart_count: u8,
    ) -> Result<ExecutorBootstrapInput<'_>, ExecutorManagerError> {
        let input = if self.local_emergency_stop {
            ExecutorBootstrapInput::new_emergency_report(
                &self.websocket_url,
                &self.control_plane_session,
                &self.installation_id,
                &self.executor_id,
                &self.state_directory,
                self.heartbeat_interval_seconds,
            )
        } else if restart_count > 0 {
            ExecutorBootstrapInput::new_crash_recovery(
                &self.websocket_url,
                &self.control_plane_session,
                &self.installation_id,
                &self.executor_id,
                &self.state_directory,
                self.heartbeat_interval_seconds,
            )
        } else {
            ExecutorBootstrapInput::new(
                &self.websocket_url,
                &self.control_plane_session,
                &self.installation_id,
                &self.executor_id,
                &self.state_directory,
                self.heartbeat_interval_seconds,
            )
        };
        input.map_err(|_| ExecutorManagerError::new(ExecutorManagerErrorCode::ConfigurationInvalid))
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ExecutorRestartPolicy {
    maximum_restarts: u8,
    monitor_interval: Duration,
    restart_delay: Duration,
}

impl ExecutorRestartPolicy {
    pub fn new(
        maximum_restarts: u8,
        monitor_interval: Duration,
        restart_delay: Duration,
    ) -> Result<Self, ExecutorManagerError> {
        if maximum_restarts > MAX_RESTARTS
            || monitor_interval.is_zero()
            || monitor_interval > MAX_LIFECYCLE_TIMEOUT
            || restart_delay.is_zero()
            || restart_delay > MAX_LIFECYCLE_TIMEOUT
        {
            return Err(ExecutorManagerError::new(
                ExecutorManagerErrorCode::ConfigurationInvalid,
            ));
        }
        Ok(Self {
            maximum_restarts,
            monitor_interval,
            restart_delay,
        })
    }
}

pub struct ExecutorManager {
    core: Arc<ExecutorManagerCore>,
    supervisor_shutdown: Arc<AtomicBool>,
    supervisor_wake: Sender<()>,
    supervisor_thread: Option<JoinHandle<()>>,
}

struct ExecutorManagerCore {
    package_root: PathBuf,
    verifier: ExecutorPackageVerifier,
    start_timeout: Duration,
    stop_timeout: Duration,
    restart_policy: ExecutorRestartPolicy,
    diagnostics: Arc<ExecutorDiagnostics>,
    slot: Mutex<ExecutorManagerSlot>,
}

struct ExecutorManagerSlot {
    lifecycle: Option<ManagedExecutorLifecycle>,
    status: ExecutorManagerStatus,
}

enum ManagedExecutorLifecycle {
    Running(Box<ManagedExecutor>),
    RestartPending(PendingExecutorRestart),
}

struct ManagedExecutor {
    process: RunningExecutor,
    launch: ExecutorLaunchConfiguration,
}

struct PendingExecutorRestart {
    launch: ExecutorLaunchConfiguration,
    restart_count: u8,
    not_before: Instant,
}

impl ExecutorManager {
    pub fn new(
        package_root: PathBuf,
        verifier: ExecutorPackageVerifier,
        start_timeout: Duration,
        stop_timeout: Duration,
        restart_policy: ExecutorRestartPolicy,
    ) -> Result<Self, ExecutorManagerError> {
        if package_root.as_os_str().is_empty()
            || start_timeout.is_zero()
            || start_timeout > MAX_LIFECYCLE_TIMEOUT
            || stop_timeout.is_zero()
            || stop_timeout > MAX_LIFECYCLE_TIMEOUT
        {
            return Err(ExecutorManagerError::new(
                ExecutorManagerErrorCode::ConfigurationInvalid,
            ));
        }
        let core = Arc::new(ExecutorManagerCore {
            package_root,
            verifier,
            start_timeout,
            stop_timeout,
            restart_policy,
            diagnostics: Arc::new(ExecutorDiagnostics::default()),
            slot: Mutex::new(ExecutorManagerSlot {
                lifecycle: None,
                status: ExecutorManagerStatus::stopped(0),
            }),
        });
        let supervisor_shutdown = Arc::new(AtomicBool::new(false));
        let (supervisor_wake, wake_receiver) = mpsc::channel();
        let thread_core = Arc::clone(&core);
        let thread_shutdown = Arc::clone(&supervisor_shutdown);
        let supervisor_thread = std::thread::Builder::new()
            .name("automation-tool-executor-supervisor".to_owned())
            .spawn(move || supervisor_loop(thread_core, thread_shutdown, wake_receiver))
            .map_err(|_| ExecutorManagerError::new(ExecutorManagerErrorCode::ProcessUnavailable))?;
        Ok(Self {
            core,
            supervisor_shutdown,
            supervisor_wake,
            supervisor_thread: Some(supervisor_thread),
        })
    }

    pub fn start(
        &self,
        launch: ExecutorLaunchConfiguration,
    ) -> Result<ExecutorManagerStatus, ExecutorManagerError> {
        let mut slot = self.lock_slot()?;
        reconcile_supervision(&self.core, &mut slot)?;
        if slot.lifecycle.is_some() {
            return Err(ExecutorManagerError::new(
                ExecutorManagerErrorCode::AlreadyRunning,
            ));
        }
        let package = self
            .core
            .verifier
            .verify_current(&self.core.package_root)
            .map_err(|_| ExecutorManagerError::new(ExecutorManagerErrorCode::PackageRejected))?;
        let status = ExecutorManagerStatus::running(&package, 0);
        let running = spawn_executor(
            package,
            &launch,
            self.core.start_timeout,
            0,
            Arc::clone(&self.core.diagnostics),
        )?;
        slot.status = status.clone();
        slot.lifecycle = Some(ManagedExecutorLifecycle::Running(Box::new(
            ManagedExecutor {
                process: running,
                launch,
            },
        )));
        let _ = self.supervisor_wake.send(());
        Ok(status)
    }

    pub fn execute_platform_command(
        &self,
        command: LocalPlatformCommand,
        executable_path: PathBuf,
        profile_directory: PathBuf,
        headless: bool,
    ) -> Result<LocalPlatformCommandResult, ExecutorManagerError> {
        let mut slot = self.lock_slot()?;
        reconcile_supervision(&self.core, &mut slot)?;
        let outcome = (|| {
            let Some(ManagedExecutorLifecycle::Running(managed)) = slot.lifecycle.as_mut() else {
                return Err(process_unavailable());
            };
            let command_id = generate_uuid_v4()?;
            let stdin = managed
                .process
                .stdin
                .as_mut()
                .ok_or_else(process_unavailable)?;
            managed
                .process
                .token
                .write_platform_command(
                    stdin,
                    &command_id,
                    command,
                    &executable_path,
                    &profile_directory,
                    headless,
                )
                .map_err(map_bootstrap_error)?;
            receive_platform_command_result(
                &managed.process,
                &command_id,
                command,
                PLATFORM_COMMAND_TIMEOUT,
            )
        })();
        if outcome.is_err() {
            let restart_count = slot.status.restart_count();
            if let Some(ManagedExecutorLifecycle::Running(mut managed)) = slot.lifecycle.take() {
                force_stop(&mut managed.process);
            }
            slot.status = ExecutorManagerStatus::stopped(restart_count);
        }
        outcome
    }

    pub fn execute_session_command(
        &self,
        command: LocalPlatformCommand,
    ) -> Result<LocalPlatformCommandResult, ExecutorManagerError> {
        let mut slot = self.lock_slot()?;
        reconcile_supervision(&self.core, &mut slot)?;
        let outcome = (|| {
            let Some(ManagedExecutorLifecycle::Running(managed)) = slot.lifecycle.as_mut() else {
                return Err(process_unavailable());
            };
            let command_id = generate_uuid_v4()?;
            let stdin = managed
                .process
                .stdin
                .as_mut()
                .ok_or_else(process_unavailable)?;
            managed
                .process
                .token
                .write_session_command(stdin, &command_id, command)
                .map_err(map_bootstrap_error)?;
            receive_platform_command_result(
                &managed.process,
                &command_id,
                command,
                PLATFORM_COMMAND_TIMEOUT,
            )
        })();
        if outcome.is_err() {
            let restart_count = slot.status.restart_count();
            if let Some(ManagedExecutorLifecycle::Running(mut managed)) = slot.lifecycle.take() {
                force_stop(&mut managed.process);
            }
            slot.status = ExecutorManagerStatus::stopped(restart_count);
        }
        outcome
    }

    pub fn status(&self) -> Result<ExecutorManagerStatus, ExecutorManagerError> {
        let mut slot = self.lock_slot()?;
        reconcile_supervision(&self.core, &mut slot)?;
        Ok(slot.status.clone())
    }

    pub fn diagnostics(&self) -> Result<Vec<String>, ExecutorManagerError> {
        self.core
            .diagnostics
            .snapshot()
            .map_err(|()| process_unavailable())
    }

    pub fn stop(&self) -> Result<ExecutorManagerStatus, ExecutorManagerError> {
        let mut slot = self.lock_slot()?;
        reconcile_supervision(&self.core, &mut slot)?;
        let restart_count = slot.status.restart_count();
        let Some(lifecycle) = slot.lifecycle.take() else {
            slot.status = ExecutorManagerStatus::stopped(restart_count);
            return Ok(slot.status.clone());
        };
        slot.status = ExecutorManagerStatus::stopped(restart_count);
        if let ManagedExecutorLifecycle::Running(mut managed) = lifecycle {
            stop_executor(&mut managed.process, self.core.stop_timeout)?;
        }
        Ok(slot.status.clone())
    }

    pub fn emergency_stop(&self) -> Result<ExecutorManagerStatus, ExecutorManagerError> {
        let mut slot = self.lock_slot()?;
        reconcile_supervision(&self.core, &mut slot)?;
        let restart_count = slot.status.restart_count();
        let lifecycle = slot.lifecycle.take();
        slot.status = ExecutorManagerStatus::stopped(restart_count);
        if let Some(ManagedExecutorLifecycle::Running(mut managed)) = lifecycle {
            managed.process.stdin.take();
            let stopped = managed.process.process_tree.terminate();
            let _ = managed.process.child.kill();
            let waited = managed
                .process
                .child
                .wait()
                .map_err(|_| process_unavailable());
            join_readers(&mut managed.process);
            stopped?;
            waited?;
        }
        Ok(slot.status.clone())
    }

    #[cfg(feature = "control-plane-e2e")]
    pub fn inject_crash_for_acceptance(&self) -> Result<(), ExecutorManagerError> {
        let mut slot = self.lock_slot()?;
        reconcile_supervision(&self.core, &mut slot)?;
        let Some(ManagedExecutorLifecycle::Running(managed)) = slot.lifecycle.as_mut() else {
            return Err(process_unavailable());
        };
        inject_abnormal_process_exit(&mut managed.process.child)?;
        let _ = self.supervisor_wake.send(());
        Ok(())
    }

    #[cfg(feature = "control-plane-e2e")]
    pub fn inject_hang_for_acceptance(&self) -> Result<(), ExecutorManagerError> {
        let mut slot = self.lock_slot()?;
        reconcile_supervision(&self.core, &mut slot)?;
        let Some(ManagedExecutorLifecycle::Running(managed)) = slot.lifecycle.as_mut() else {
            return Err(process_unavailable());
        };
        suspend_process_for_acceptance(&managed.process.child)
    }

    fn lock_slot(&self) -> Result<MutexGuard<'_, ExecutorManagerSlot>, ExecutorManagerError> {
        self.core
            .slot
            .lock()
            .map_err(|_| ExecutorManagerError::new(ExecutorManagerErrorCode::ProcessUnavailable))
    }
}

impl Drop for ExecutorManager {
    fn drop(&mut self) {
        self.supervisor_shutdown.store(true, Ordering::Release);
        let _ = self.supervisor_wake.send(());
        if let Some(thread) = self.supervisor_thread.take() {
            let _ = thread.join();
        }
        if let Ok(mut slot) = self.core.slot.lock() {
            if let Some(ManagedExecutorLifecycle::Running(mut managed)) = slot.lifecycle.take() {
                force_stop(&mut managed.process);
            }
            slot.status = ExecutorManagerStatus::stopped(slot.status.restart_count());
        }
    }
}

struct RunningExecutor {
    child: Child,
    stdin: Option<ChildStdin>,
    process_tree: ProcessTree,
    token: LocalSessionToken,
    lifecycle_events: Receiver<Result<String, ()>>,
    stdout_thread: Option<JoinHandle<()>>,
    stderr_thread: Option<JoinHandle<()>>,
    status: ExecutorManagerStatus,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ExecutorLifecycleEvent {
    authentication_proof: String,
    event: String,
    protocol_version: String,
}

struct ProcessTree {
    termination_requested: bool,
    #[cfg(unix)]
    process_group_id: i32,
    #[cfg(windows)]
    job: WindowsJob,
}

impl ProcessTree {
    #[cfg(unix)]
    fn attach(child: &Child) -> Result<Self, ExecutorManagerError> {
        let process_group_id = i32::try_from(child.id()).map_err(|_| process_unavailable())?;
        if process_group_id <= 0 {
            return Err(process_unavailable());
        }
        Ok(Self {
            termination_requested: false,
            process_group_id,
        })
    }

    #[cfg(windows)]
    fn attach(child: &Child) -> Result<Self, ExecutorManagerError> {
        Ok(Self {
            termination_requested: false,
            job: WindowsJob::attach(child)?,
        })
    }

    #[cfg(all(not(unix), not(windows)))]
    fn attach(_child: &Child) -> Result<Self, ExecutorManagerError> {
        Err(process_unavailable())
    }

    #[cfg(unix)]
    fn terminate(&mut self) -> Result<(), ExecutorManagerError> {
        if self.termination_requested {
            return Ok(());
        }
        let result = unsafe { libc::kill(-self.process_group_id, libc::SIGKILL) };
        if result == 0 || io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
            self.termination_requested = true;
            Ok(())
        } else {
            Err(process_unavailable())
        }
    }

    #[cfg(windows)]
    fn terminate(&mut self) -> Result<(), ExecutorManagerError> {
        if self.termination_requested {
            return Ok(());
        }
        self.job.terminate()?;
        self.termination_requested = true;
        Ok(())
    }

    #[cfg(all(not(unix), not(windows)))]
    fn terminate(&mut self) -> Result<(), ExecutorManagerError> {
        Err(process_unavailable())
    }
}

#[cfg(unix)]
fn configure_process_isolation(command: &mut Command) {
    use std::os::unix::process::CommandExt;

    command.process_group(0);
}

#[cfg(windows)]
fn configure_process_isolation(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    use windows_sys::Win32::System::Threading::CREATE_SUSPENDED;

    command.creation_flags(CREATE_SUSPENDED);
}

#[cfg(all(not(unix), not(windows)))]
fn configure_process_isolation(_command: &mut Command) {}

#[cfg(windows)]
struct WindowsJob {
    handle: windows_sys::Win32::Foundation::HANDLE,
}

#[cfg(windows)]
unsafe impl Send for WindowsJob {}

#[cfg(windows)]
impl WindowsJob {
    fn attach(child: &Child) -> Result<Self, ExecutorManagerError> {
        use std::mem::{size_of, zeroed};
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Foundation::{CloseHandle, FALSE};
        use windows_sys::Win32::System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        };

        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if handle.is_null() {
            return Err(process_unavailable());
        }
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let configured = unsafe {
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as _,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        let assigned = configured != FALSE
            && unsafe { AssignProcessToJobObject(handle, child.as_raw_handle() as _) } != FALSE;
        if !assigned || resume_suspended_process(child.id()).is_err() {
            unsafe {
                windows_sys::Win32::System::JobObjects::TerminateJobObject(handle, 1);
                CloseHandle(handle);
            }
            return Err(process_unavailable());
        }
        Ok(Self { handle })
    }

    fn terminate(&mut self) -> Result<(), ExecutorManagerError> {
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::JobObjects::TerminateJobObject;

        if self.handle.is_null() {
            return Ok(());
        }
        let terminated = unsafe { TerminateJobObject(self.handle, 1) } != 0;
        let closed = unsafe { CloseHandle(self.handle) } != 0;
        self.handle = std::ptr::null_mut();
        if terminated || closed {
            Ok(())
        } else {
            Err(process_unavailable())
        }
    }
}

#[cfg(windows)]
impl Drop for WindowsJob {
    fn drop(&mut self) {
        if !self.handle.is_null() {
            unsafe {
                windows_sys::Win32::Foundation::CloseHandle(self.handle);
            }
            self.handle = std::ptr::null_mut();
        }
    }
}

#[cfg(windows)]
fn resume_suspended_process(process_id: u32) -> Result<(), ExecutorManagerError> {
    use std::mem::{size_of, zeroed};
    use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
    };
    use windows_sys::Win32::System::Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME};

    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(process_unavailable());
    }
    let mut entry: THREADENTRY32 = unsafe { zeroed() };
    entry.dwSize = size_of::<THREADENTRY32>() as u32;
    let mut resumed = false;
    let mut has_entry = unsafe { Thread32First(snapshot, &mut entry) } != 0;
    while has_entry {
        if entry.th32OwnerProcessID == process_id {
            let thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
            if !thread.is_null() {
                let previous_count = unsafe { ResumeThread(thread) };
                unsafe {
                    CloseHandle(thread);
                }
                if previous_count != u32::MAX {
                    resumed = true;
                }
            }
        }
        has_entry = unsafe { Thread32Next(snapshot, &mut entry) } != 0;
    }
    unsafe {
        CloseHandle(snapshot);
    }
    if resumed {
        Ok(())
    } else {
        Err(process_unavailable())
    }
}

fn spawn_executor(
    package: VerifiedExecutorPackage,
    launch: &ExecutorLaunchConfiguration,
    start_timeout: Duration,
    restart_count: u8,
    diagnostics: Arc<ExecutorDiagnostics>,
) -> Result<RunningExecutor, ExecutorManagerError> {
    let token = LocalSessionToken::generate()
        .map_err(|_| ExecutorManagerError::new(ExecutorManagerErrorCode::ProcessUnavailable))?;
    let mut command = Command::new(package.entrypoint_path());
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    configure_process_isolation(&mut command);
    let mut child = command
        .spawn()
        .map_err(|_| ExecutorManagerError::new(ExecutorManagerErrorCode::ProcessUnavailable))?;
    let mut process_tree = match ProcessTree::attach(&child) {
        Ok(process_tree) => process_tree,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
    };
    let setup = (|| {
        let mut stdin = child.stdin.take().ok_or_else(process_unavailable)?;
        let stdout = child.stdout.take().ok_or_else(process_unavailable)?;
        let stderr = child.stderr.take().ok_or_else(process_unavailable)?;
        let (lifecycle_events, stdout_thread) = spawn_stdout_reader(stdout);
        let stderr_thread = spawn_stderr_drain(stderr, diagnostics);
        token
            .write_bootstrap(&mut stdin, &launch.bootstrap_input(restart_count)?)
            .map_err(map_bootstrap_error)?;
        Ok((stdin, lifecycle_events, stdout_thread, stderr_thread))
    })();
    let (stdin, lifecycle_events, stdout_thread, stderr_thread) = match setup {
        Ok(value) => value,
        Err(error) => {
            force_stop_child(&mut process_tree, &mut child);
            return Err(error);
        }
    };
    let status = ExecutorManagerStatus::running(&package, restart_count);
    let mut running = RunningExecutor {
        child,
        stdin: Some(stdin),
        process_tree,
        token,
        lifecycle_events,
        stdout_thread: Some(stdout_thread),
        stderr_thread: Some(stderr_thread),
        status,
    };
    if let Err(error) = receive_event(&running, LocalExecutorEvent::Healthy, start_timeout) {
        force_stop(&mut running);
        return Err(error);
    }
    Ok(running)
}

fn spawn_stdout_reader(stdout: ChildStdout) -> (Receiver<Result<String, ()>>, JoinHandle<()>) {
    let (sender, receiver) = mpsc::channel();
    let thread = std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        loop {
            match read_bounded_line(&mut reader) {
                Ok(Some(line)) => {
                    if sender.send(Ok(line)).is_err() {
                        break;
                    }
                }
                Ok(None) => break,
                Err(()) => {
                    let _ = sender.send(Err(()));
                    break;
                }
            }
        }
    });
    (receiver, thread)
}

fn spawn_stderr_drain(
    stderr: ChildStderr,
    diagnostics: Arc<ExecutorDiagnostics>,
) -> JoinHandle<()> {
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stderr);
        while let Ok(Some(line)) = read_bounded_diagnostic_line(&mut reader) {
            diagnostics.retain_raw_line(&line.bytes, line.truncated);
        }
    })
}

struct BoundedDiagnosticLine {
    bytes: Vec<u8>,
    truncated: bool,
}

fn read_bounded_diagnostic_line(
    reader: &mut impl BufRead,
) -> io::Result<Option<BoundedDiagnosticLine>> {
    let mut bytes = Vec::with_capacity(MAX_DIAGNOSTIC_LINE_BYTES);
    let mut truncated = false;
    let mut saw_input = false;
    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            return if saw_input {
                Ok(Some(BoundedDiagnosticLine { bytes, truncated }))
            } else {
                Ok(None)
            };
        }
        saw_input = true;
        let newline = available.iter().position(|byte| *byte == b'\n');
        let content_bytes = newline.unwrap_or(available.len());
        let remaining = MAX_DIAGNOSTIC_LINE_BYTES.saturating_sub(bytes.len());
        let retained_bytes = content_bytes.min(remaining);
        bytes.extend_from_slice(&available[..retained_bytes]);
        truncated |= content_bytes > retained_bytes;
        let consumed = newline.map_or(available.len(), |position| position + 1);
        reader.consume(consumed);
        if newline.is_some() {
            return Ok(Some(BoundedDiagnosticLine { bytes, truncated }));
        }
    }
}

fn read_bounded_line(reader: &mut impl BufRead) -> Result<Option<String>, ()> {
    let mut bytes = Vec::with_capacity(256);
    let mut limited = reader.take((MAX_LIFECYCLE_LINE_BYTES + 1) as u64);
    let count = limited.read_until(b'\n', &mut bytes).map_err(|_| ())?;
    if count == 0 {
        return Ok(None);
    }
    if bytes.len() > MAX_LIFECYCLE_LINE_BYTES || !bytes.ends_with(b"\n") {
        return Err(());
    }
    bytes.pop();
    if bytes.ends_with(b"\r") {
        return Err(());
    }
    String::from_utf8(bytes).map(Some).map_err(|_| ())
}

fn receive_event(
    running: &RunningExecutor,
    expected_event: LocalExecutorEvent,
    timeout: Duration,
) -> Result<(), ExecutorManagerError> {
    let line = running
        .lifecycle_events
        .recv_timeout(timeout)
        .map_err(|error| match error {
            mpsc::RecvTimeoutError::Timeout => {
                ExecutorManagerError::new(ExecutorManagerErrorCode::TimedOut)
            }
            mpsc::RecvTimeoutError::Disconnected => process_unavailable(),
        })?
        .map_err(|()| process_unavailable())?;
    let event: ExecutorLifecycleEvent =
        serde_json::from_str(&line).map_err(|_| process_unavailable())?;
    let expected_name = match expected_event {
        LocalExecutorEvent::Healthy => "executor.healthy",
        LocalExecutorEvent::Stopped => "executor.stopped",
    };
    if event.event != expected_name || event.protocol_version != EXECUTOR_PROTOCOL_VERSION {
        return Err(process_unavailable());
    }
    running
        .token
        .verify_event_proof(expected_event, &event.authentication_proof)
        .map_err(map_bootstrap_error)
}

fn receive_platform_command_result(
    running: &RunningExecutor,
    command_id: &str,
    command: LocalPlatformCommand,
    timeout: Duration,
) -> Result<LocalPlatformCommandResult, ExecutorManagerError> {
    let line = running
        .lifecycle_events
        .recv_timeout(timeout)
        .map_err(|error| match error {
            mpsc::RecvTimeoutError::Timeout => {
                ExecutorManagerError::new(ExecutorManagerErrorCode::TimedOut)
            }
            mpsc::RecvTimeoutError::Disconnected => process_unavailable(),
        })?
        .map_err(|()| process_unavailable())?;
    running
        .token
        .parse_platform_command_result(command_id, command, &line)
        .map_err(map_bootstrap_error)
}

fn stop_executor(
    running: &mut RunningExecutor,
    stop_timeout: Duration,
) -> Result<(), ExecutorManagerError> {
    running.stdin.take();
    if let Err(error) = request_graceful_stop(&mut running.child) {
        force_stop(running);
        return Err(error);
    }
    let proof_result = receive_stop_confirmation(running, stop_timeout);
    if proof_result.is_err() {
        force_stop(running);
        return proof_result;
    }
    let deadline = Instant::now() + stop_timeout;
    loop {
        match running.child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if Instant::now() < deadline => {
                std::thread::sleep(Duration::from_millis(10));
            }
            Ok(None) => {
                force_stop(running);
                return Err(ExecutorManagerError::new(
                    ExecutorManagerErrorCode::TimedOut,
                ));
            }
            Err(_) => {
                force_stop(running);
                return Err(process_unavailable());
            }
        }
    }
    running.process_tree.terminate()?;
    join_readers(running);
    Ok(())
}

#[cfg(not(windows))]
fn receive_stop_confirmation(
    running: &RunningExecutor,
    stop_timeout: Duration,
) -> Result<(), ExecutorManagerError> {
    receive_event(running, LocalExecutorEvent::Stopped, stop_timeout)
}

#[cfg(windows)]
fn receive_stop_confirmation(
    _running: &RunningExecutor,
    _stop_timeout: Duration,
) -> Result<(), ExecutorManagerError> {
    Ok(())
}

#[cfg(unix)]
fn request_graceful_stop(child: &mut Child) -> Result<(), ExecutorManagerError> {
    let process_id = i32::try_from(child.id()).map_err(|_| process_unavailable())?;
    if unsafe { libc::kill(process_id, libc::SIGTERM) } == 0 {
        Ok(())
    } else {
        Err(process_unavailable())
    }
}

#[cfg(windows)]
fn request_graceful_stop(child: &mut Child) -> Result<(), ExecutorManagerError> {
    child.kill().map_err(|_| process_unavailable())
}

#[cfg(all(not(unix), not(windows)))]
fn request_graceful_stop(_child: &mut Child) -> Result<(), ExecutorManagerError> {
    Err(process_unavailable())
}

fn supervisor_loop(core: Arc<ExecutorManagerCore>, shutdown: Arc<AtomicBool>, wake: Receiver<()>) {
    while !shutdown.load(Ordering::Acquire) {
        let _ = wake.recv_timeout(core.restart_policy.monitor_interval);
        if shutdown.load(Ordering::Acquire) {
            break;
        }
        let Ok(mut slot) = core.slot.lock() else {
            break;
        };
        let _ = reconcile_supervision(&core, &mut slot);
    }
}

fn reconcile_supervision(
    core: &ExecutorManagerCore,
    slot: &mut ExecutorManagerSlot,
) -> Result<(), ExecutorManagerError> {
    let Some(lifecycle) = slot.lifecycle.take() else {
        return Ok(());
    };
    match lifecycle {
        ManagedExecutorLifecycle::Running(mut managed) => {
            let exit_status = match managed.process.child.try_wait() {
                Ok(Some(status)) => status,
                Ok(None) => {
                    slot.lifecycle = Some(ManagedExecutorLifecycle::Running(managed));
                    return Ok(());
                }
                Err(_) => {
                    slot.lifecycle = Some(ManagedExecutorLifecycle::Running(managed));
                    return Err(process_unavailable());
                }
            };
            let process_tree_result = managed.process.process_tree.terminate();
            join_readers(&mut managed.process);
            let restart_count = managed.process.status.restart_count();
            if process_tree_result.is_err() {
                slot.status = ExecutorManagerStatus::stopped(restart_count);
                return Err(process_unavailable());
            }
            if restartable_exit(exit_status) && restart_count < core.restart_policy.maximum_restarts
            {
                let next_restart_count = restart_count + 1;
                slot.status =
                    ExecutorManagerStatus::restarting(&managed.process.status, next_restart_count);
                slot.lifecycle = Some(ManagedExecutorLifecycle::RestartPending(
                    PendingExecutorRestart {
                        launch: managed.launch,
                        restart_count: next_restart_count,
                        not_before: Instant::now() + core.restart_policy.restart_delay,
                    },
                ));
            } else {
                slot.status = ExecutorManagerStatus::stopped(restart_count);
            }
        }
        ManagedExecutorLifecycle::RestartPending(pending) => {
            if Instant::now() < pending.not_before {
                slot.lifecycle = Some(ManagedExecutorLifecycle::RestartPending(pending));
                return Ok(());
            }
            let restarted = core
                .verifier
                .verify_current(&core.package_root)
                .map_err(|_| ExecutorManagerError::new(ExecutorManagerErrorCode::PackageRejected))
                .and_then(|package| {
                    spawn_executor(
                        package,
                        &pending.launch,
                        core.start_timeout,
                        pending.restart_count,
                        Arc::clone(&core.diagnostics),
                    )
                });
            match restarted {
                Ok(process) => {
                    slot.status = process.status.clone();
                    slot.lifecycle = Some(ManagedExecutorLifecycle::Running(Box::new(
                        ManagedExecutor {
                            process,
                            launch: pending.launch,
                        },
                    )));
                }
                Err(_) => {
                    slot.status = ExecutorManagerStatus::stopped(pending.restart_count);
                }
            }
        }
    }
    Ok(())
}

#[cfg(unix)]
fn restartable_exit(status: ExitStatus) -> bool {
    use std::os::unix::process::ExitStatusExt;

    status.signal().is_some()
}

#[cfg(all(feature = "control-plane-e2e", unix))]
fn inject_abnormal_process_exit(child: &mut Child) -> Result<(), ExecutorManagerError> {
    let process_id = i32::try_from(child.id()).map_err(|_| process_unavailable())?;
    if unsafe { libc::kill(process_id, libc::SIGKILL) } == 0 {
        Ok(())
    } else {
        Err(process_unavailable())
    }
}

#[cfg(all(feature = "control-plane-e2e", unix))]
fn suspend_process_for_acceptance(child: &Child) -> Result<(), ExecutorManagerError> {
    let process_id = i32::try_from(child.id()).map_err(|_| process_unavailable())?;
    if unsafe { libc::kill(process_id, libc::SIGSTOP) } == 0 {
        Ok(())
    } else {
        Err(process_unavailable())
    }
}

#[cfg(all(feature = "control-plane-e2e", windows))]
fn inject_abnormal_process_exit(child: &mut Child) -> Result<(), ExecutorManagerError> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::Threading::TerminateProcess;

    const ACCESS_VIOLATION_STATUS: u32 = 0xc000_0005;
    if unsafe { TerminateProcess(child.as_raw_handle() as _, ACCESS_VIOLATION_STATUS) } != 0 {
        Ok(())
    } else {
        Err(process_unavailable())
    }
}

#[cfg(all(feature = "control-plane-e2e", windows))]
fn suspend_process_for_acceptance(child: &Child) -> Result<(), ExecutorManagerError> {
    use std::mem::{size_of, zeroed};
    use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
    };
    use windows_sys::Win32::System::Threading::{OpenThread, SuspendThread, THREAD_SUSPEND_RESUME};

    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(process_unavailable());
    }
    let mut entry: THREADENTRY32 = unsafe { zeroed() };
    entry.dwSize = size_of::<THREADENTRY32>() as u32;
    let mut found_thread = false;
    let mut suspended_all = true;
    let mut has_entry = unsafe { Thread32First(snapshot, &mut entry) } != 0;
    while has_entry {
        if entry.th32OwnerProcessID == child.id() {
            found_thread = true;
            let thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
            if thread.is_null() {
                suspended_all = false;
            } else {
                if unsafe { SuspendThread(thread) } == u32::MAX {
                    suspended_all = false;
                }
                unsafe {
                    CloseHandle(thread);
                }
            }
        }
        has_entry = unsafe { Thread32Next(snapshot, &mut entry) } != 0;
    }
    unsafe {
        CloseHandle(snapshot);
    }
    if found_thread && suspended_all {
        Ok(())
    } else {
        Err(process_unavailable())
    }
}

#[cfg(all(feature = "control-plane-e2e", not(any(unix, windows))))]
fn inject_abnormal_process_exit(_child: &mut Child) -> Result<(), ExecutorManagerError> {
    Err(process_unavailable())
}

#[cfg(all(feature = "control-plane-e2e", not(any(unix, windows))))]
fn suspend_process_for_acceptance(_child: &Child) -> Result<(), ExecutorManagerError> {
    Err(process_unavailable())
}

#[cfg(windows)]
fn restartable_exit(status: ExitStatus) -> bool {
    status.code().is_some_and(|code| code < 0)
}

#[cfg(all(not(unix), not(windows)))]
fn restartable_exit(_status: ExitStatus) -> bool {
    false
}

fn force_stop(running: &mut RunningExecutor) {
    running.stdin.take();
    force_stop_child(&mut running.process_tree, &mut running.child);
    join_readers(running);
}

fn force_stop_child(process_tree: &mut ProcessTree, child: &mut Child) {
    let _ = process_tree.terminate();
    let _ = child.kill();
    let _ = child.wait();
}

fn join_readers(running: &mut RunningExecutor) {
    if let Some(thread) = running.stdout_thread.take() {
        let _ = thread.join();
    }
    if let Some(thread) = running.stderr_thread.take() {
        let _ = thread.join();
    }
}

fn map_bootstrap_error(
    error: crate::executor_bootstrap::ExecutorBootstrapError,
) -> ExecutorManagerError {
    let code = match error.code() {
        ExecutorBootstrapErrorCode::AuthenticationRejected => {
            ExecutorManagerErrorCode::AuthenticationRejected
        }
        ExecutorBootstrapErrorCode::BootstrapRejected => {
            ExecutorManagerErrorCode::ProcessUnavailable
        }
    };
    ExecutorManagerError::new(code)
}

const fn process_unavailable() -> ExecutorManagerError {
    ExecutorManagerError::new(ExecutorManagerErrorCode::ProcessUnavailable)
}

fn generate_uuid_v4() -> Result<String, ExecutorManagerError> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes).map_err(|_| process_unavailable())?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(Uuid::from_bytes(bytes).hyphenated().to_string())
}

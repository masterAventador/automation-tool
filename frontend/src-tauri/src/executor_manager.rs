//! Linearized lifecycle for one verified Local Executor process.

use crate::executor_bootstrap::{
    ExecutorBootstrapErrorCode, ExecutorBootstrapInput, LocalExecutorEvent, LocalSessionToken,
};
use crate::executor_package::{ExecutorPackageVerifier, VerifiedExecutorPackage};
use serde::{Deserialize, Serialize};
use std::fmt;
use std::io::{self, BufRead, BufReader, Read};
use std::path::PathBuf;
use std::process::{Child, ChildStderr, ChildStdout, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::sync::Mutex;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};
use zeroize::Zeroizing;

const EXECUTOR_PROTOCOL_VERSION: &str = "1.0";
const MAX_LIFECYCLE_LINE_BYTES: usize = 4096;
const MAX_LIFECYCLE_TIMEOUT: Duration = Duration::from_secs(60);

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
    Stopped,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecutorManagerStatus {
    state: ExecutorManagerState,
    version: Option<String>,
    build_id: Option<String>,
}

impl ExecutorManagerStatus {
    fn stopped() -> Self {
        Self {
            state: ExecutorManagerState::Stopped,
            version: None,
            build_id: None,
        }
    }

    fn running(package: &VerifiedExecutorPackage) -> Self {
        Self {
            state: ExecutorManagerState::Running,
            version: Some(package.version().to_string()),
            build_id: Some(package.build_id().to_owned()),
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
}

pub struct ExecutorLaunchConfiguration {
    websocket_url: String,
    control_plane_session: Zeroizing<String>,
    installation_id: String,
    executor_id: String,
    heartbeat_interval_seconds: u8,
}

impl ExecutorLaunchConfiguration {
    pub fn new(
        websocket_url: String,
        control_plane_session: String,
        installation_id: String,
        executor_id: String,
        heartbeat_interval_seconds: u8,
    ) -> Result<Self, ExecutorManagerError> {
        ExecutorBootstrapInput::new(
            &websocket_url,
            &control_plane_session,
            &installation_id,
            &executor_id,
            heartbeat_interval_seconds,
        )
        .map_err(|_| ExecutorManagerError::new(ExecutorManagerErrorCode::ConfigurationInvalid))?;
        Ok(Self {
            websocket_url,
            control_plane_session: Zeroizing::new(control_plane_session),
            installation_id,
            executor_id,
            heartbeat_interval_seconds,
        })
    }

    fn bootstrap_input(&self) -> Result<ExecutorBootstrapInput<'_>, ExecutorManagerError> {
        ExecutorBootstrapInput::new(
            &self.websocket_url,
            &self.control_plane_session,
            &self.installation_id,
            &self.executor_id,
            self.heartbeat_interval_seconds,
        )
        .map_err(|_| ExecutorManagerError::new(ExecutorManagerErrorCode::ConfigurationInvalid))
    }
}

pub struct ExecutorManager {
    package_root: PathBuf,
    verifier: ExecutorPackageVerifier,
    start_timeout: Duration,
    stop_timeout: Duration,
    slot: Mutex<Option<RunningExecutor>>,
}

impl ExecutorManager {
    pub fn new(
        package_root: PathBuf,
        verifier: ExecutorPackageVerifier,
        start_timeout: Duration,
        stop_timeout: Duration,
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
        Ok(Self {
            package_root,
            verifier,
            start_timeout,
            stop_timeout,
            slot: Mutex::new(None),
        })
    }

    pub fn start(
        &self,
        launch: ExecutorLaunchConfiguration,
    ) -> Result<ExecutorManagerStatus, ExecutorManagerError> {
        let mut slot = self.lock_slot()?;
        refresh_exited(&mut slot)?;
        if slot.is_some() {
            return Err(ExecutorManagerError::new(
                ExecutorManagerErrorCode::AlreadyRunning,
            ));
        }
        let package = self
            .verifier
            .verify_current(&self.package_root)
            .map_err(|_| ExecutorManagerError::new(ExecutorManagerErrorCode::PackageRejected))?;
        let status = ExecutorManagerStatus::running(&package);
        let running = spawn_executor(package, launch, self.start_timeout)?;
        *slot = Some(running);
        Ok(status)
    }

    pub fn status(&self) -> Result<ExecutorManagerStatus, ExecutorManagerError> {
        let mut slot = self.lock_slot()?;
        refresh_exited(&mut slot)?;
        Ok(slot
            .as_ref()
            .map(|running| running.status.clone())
            .unwrap_or_else(ExecutorManagerStatus::stopped))
    }

    pub fn stop(&self) -> Result<ExecutorManagerStatus, ExecutorManagerError> {
        let mut slot = self.lock_slot()?;
        refresh_exited(&mut slot)?;
        let Some(mut running) = slot.take() else {
            return Ok(ExecutorManagerStatus::stopped());
        };
        stop_executor(&mut running, self.stop_timeout)?;
        Ok(ExecutorManagerStatus::stopped())
    }

    fn lock_slot(
        &self,
    ) -> Result<std::sync::MutexGuard<'_, Option<RunningExecutor>>, ExecutorManagerError> {
        self.slot
            .lock()
            .map_err(|_| ExecutorManagerError::new(ExecutorManagerErrorCode::ProcessUnavailable))
    }
}

impl Drop for ExecutorManager {
    fn drop(&mut self) {
        if let Ok(slot) = self.slot.get_mut() {
            if let Some(mut running) = slot.take() {
                force_stop(&mut running);
            }
        }
    }
}

struct RunningExecutor {
    child: Child,
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

fn spawn_executor(
    package: VerifiedExecutorPackage,
    launch: ExecutorLaunchConfiguration,
    start_timeout: Duration,
) -> Result<RunningExecutor, ExecutorManagerError> {
    let token = LocalSessionToken::generate()
        .map_err(|_| ExecutorManagerError::new(ExecutorManagerErrorCode::ProcessUnavailable))?;
    let mut child = Command::new(package.entrypoint_path())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|_| ExecutorManagerError::new(ExecutorManagerErrorCode::ProcessUnavailable))?;
    let setup = (|| {
        let mut stdin = child.stdin.take().ok_or_else(process_unavailable)?;
        let stdout = child.stdout.take().ok_or_else(process_unavailable)?;
        let stderr = child.stderr.take().ok_or_else(process_unavailable)?;
        let (lifecycle_events, stdout_thread) = spawn_stdout_reader(stdout);
        let stderr_thread = spawn_stderr_drain(stderr);
        token
            .write_bootstrap(&mut stdin, &launch.bootstrap_input()?)
            .map_err(map_bootstrap_error)?;
        drop(stdin);
        Ok((lifecycle_events, stdout_thread, stderr_thread))
    })();
    let (lifecycle_events, stdout_thread, stderr_thread) = match setup {
        Ok(value) => value,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
    };
    let status = ExecutorManagerStatus::running(&package);
    let mut running = RunningExecutor {
        child,
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

fn spawn_stderr_drain(stderr: ChildStderr) -> JoinHandle<()> {
    std::thread::spawn(move || {
        let mut stderr = stderr;
        let _ = io::copy(&mut stderr, &mut io::sink());
    })
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

fn stop_executor(
    running: &mut RunningExecutor,
    stop_timeout: Duration,
) -> Result<(), ExecutorManagerError> {
    if let Err(error) = request_graceful_stop(&mut running.child) {
        force_stop(running);
        return Err(error);
    }
    let proof_result = receive_event(running, LocalExecutorEvent::Stopped, stop_timeout);
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
    join_readers(running);
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

fn refresh_exited(slot: &mut Option<RunningExecutor>) -> Result<(), ExecutorManagerError> {
    let exited = match slot.as_mut() {
        Some(running) => running
            .child
            .try_wait()
            .map_err(|_| process_unavailable())?
            .is_some(),
        None => false,
    };
    if exited {
        if let Some(mut running) = slot.take() {
            join_readers(&mut running);
        }
    }
    Ok(())
}

fn force_stop(running: &mut RunningExecutor) {
    let _ = running.child.kill();
    let _ = running.child.wait();
    join_readers(running);
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

//! Authenticated, process-owned lifecycle for local video workers.

use crate::managed_process_tree::{configure_managed_process, ManagedProcessTree};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use hmac::{Hmac, KeyInit, Mac};
use semver::Version;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::collections::BTreeMap;
use std::fmt;
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStderr, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::sync::{Mutex, MutexGuard};
use std::thread::JoinHandle;
use std::time::Duration;
use uuid::Uuid;
use zeroize::{Zeroize, Zeroizing};

const BOOTSTRAP_VERSION: &str = "1";
const WORKER_PROTOCOL_VERSION: &str = "1.0";
const SESSION_TOKEN_BYTES: usize = 32;
const MAX_LINE_BYTES: usize = 16 * 1024;
const MAX_HTTP_RESPONSE_BYTES: u64 = 16 * 1024;
const MAX_VERSION_BYTES: usize = 128;
const MAX_PATH_BYTES: usize = 4096;
const MAX_RESTARTS: u8 = 8;
const MAX_TIMEOUT: Duration = Duration::from_secs(60);
const EVENT_PROOF_PREFIX: &str = "atvwp1.";
const COMMAND_PROOF_PREFIX: &str = "atvwc1.";
const EVENT_AUTHENTICATION_DOMAIN: &[u8] = b"automation-tool.video-worker-event.v1\0";
const COMMAND_AUTHENTICATION_DOMAIN: &[u8] = b"automation-tool.video-worker-command.v1\0";
const LOOPBACK_HOST: &str = "127.0.0.1";
const WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;

pub const DEFAULT_VIDEO_WORKER_START_TIMEOUT: Duration = Duration::from_secs(30);
pub const DEFAULT_VIDEO_WORKER_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

type HmacSha256 = Hmac<Sha256>;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VideoWorkerKind {
    Python,
    Node,
}

impl VideoWorkerKind {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Python => "python",
            Self::Node => "node",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VideoWorkerState {
    Running,
    Stopped,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VideoWorkerErrorCode {
    AlreadyRunning,
    AuthenticationRejected,
    ConfigurationInvalid,
    NotRunning,
    ProcessUnavailable,
    TimedOut,
    VersionMismatch,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct VideoWorkerError {
    code: VideoWorkerErrorCode,
}

impl VideoWorkerError {
    const fn new(code: VideoWorkerErrorCode) -> Self {
        Self { code }
    }

    pub const fn code(self) -> VideoWorkerErrorCode {
        self.code
    }
}

impl fmt::Debug for VideoWorkerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VideoWorkerError")
            .field("code", &self.code)
            .finish()
    }
}

impl fmt::Display for VideoWorkerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Local video worker lifecycle is unavailable")
    }
}

impl std::error::Error for VideoWorkerError {}

#[derive(Clone, Copy)]
pub struct VideoWorkerRestartPolicy {
    maximum_restarts: u8,
    restart_delay: Duration,
}

impl VideoWorkerRestartPolicy {
    pub fn new(maximum_restarts: u8, restart_delay: Duration) -> Result<Self, VideoWorkerError> {
        if maximum_restarts > MAX_RESTARTS || restart_delay > MAX_TIMEOUT {
            return Err(configuration_invalid());
        }
        Ok(Self {
            maximum_restarts,
            restart_delay,
        })
    }
}

#[derive(Clone)]
pub struct VideoWorkerLaunch {
    kind: VideoWorkerKind,
    executable_path: PathBuf,
    asset_root: PathBuf,
    expected_version: String,
    restart_policy: VideoWorkerRestartPolicy,
}

impl VideoWorkerLaunch {
    pub fn new(
        kind: VideoWorkerKind,
        executable_path: PathBuf,
        asset_root: PathBuf,
        expected_version: String,
        restart_policy: VideoWorkerRestartPolicy,
    ) -> Result<Self, VideoWorkerError> {
        validate_executable_path(&executable_path)?;
        validate_directory_path(&asset_root)?;
        if expected_version.is_empty()
            || expected_version.len() > MAX_VERSION_BYTES
            || Version::parse(&expected_version).is_err()
        {
            return Err(configuration_invalid());
        }
        Ok(Self {
            kind,
            executable_path,
            asset_root,
            expected_version,
            restart_policy,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct VideoWorkerStatus {
    kind: VideoWorkerKind,
    state: VideoWorkerState,
    worker_version: Option<String>,
    host: Option<&'static str>,
    port: Option<u16>,
    process_id: Option<u32>,
    restart_count: u8,
}

impl VideoWorkerStatus {
    fn stopped(kind: VideoWorkerKind, restart_count: u8) -> Self {
        Self {
            kind,
            state: VideoWorkerState::Stopped,
            worker_version: None,
            host: None,
            port: None,
            process_id: None,
            restart_count,
        }
    }

    fn running(
        kind: VideoWorkerKind,
        worker_version: String,
        port: u16,
        process_id: u32,
        restart_count: u8,
    ) -> Self {
        Self {
            kind,
            state: VideoWorkerState::Running,
            worker_version: Some(worker_version),
            host: Some(LOOPBACK_HOST),
            port: Some(port),
            process_id: Some(process_id),
            restart_count,
        }
    }

    pub const fn state(&self) -> VideoWorkerState {
        self.state
    }

    pub fn worker_version(&self) -> Option<&str> {
        self.worker_version.as_deref()
    }

    pub const fn host(&self) -> Option<&str> {
        self.host
    }

    pub const fn port(&self) -> Option<u16> {
        self.port
    }

    pub const fn process_id(&self) -> Option<u32> {
        self.process_id
    }

    pub const fn restart_count(&self) -> u8 {
        self.restart_count
    }
}

pub struct LocalVideoOrchestrator {
    start_timeout: Duration,
    request_timeout: Duration,
    workers: Mutex<BTreeMap<VideoWorkerKind, RunningVideoWorker>>,
}

impl LocalVideoOrchestrator {
    pub fn new(
        start_timeout: Duration,
        request_timeout: Duration,
    ) -> Result<Self, VideoWorkerError> {
        if start_timeout.is_zero()
            || request_timeout.is_zero()
            || start_timeout > MAX_TIMEOUT
            || request_timeout > MAX_TIMEOUT
        {
            return Err(configuration_invalid());
        }
        Ok(Self {
            start_timeout,
            request_timeout,
            workers: Mutex::new(BTreeMap::new()),
        })
    }

    pub fn start(&self, launch: VideoWorkerLaunch) -> Result<VideoWorkerStatus, VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        if workers.contains_key(&launch.kind) {
            return Err(VideoWorkerError::new(VideoWorkerErrorCode::AlreadyRunning));
        }
        let running = spawn_worker(launch.clone(), self.start_timeout, 0)?;
        let status = running.status.clone();
        workers.insert(launch.kind, running);
        Ok(status)
    }

    pub fn status(&self, kind: VideoWorkerKind) -> Result<VideoWorkerStatus, VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        let Some(running) = workers.get_mut(&kind) else {
            return Ok(VideoWorkerStatus::stopped(kind, 0));
        };
        let Some(exit_status) = running
            .child
            .try_wait()
            .map_err(|_| process_unavailable())?
        else {
            return Ok(running.status.clone());
        };
        let restart_count = running.status.restart_count;
        if exit_status.success() || restart_count >= running.launch.restart_policy.maximum_restarts
        {
            let mut stopped = workers.remove(&kind).expect("worker entry exists");
            finish_exited_worker(&mut stopped);
            return Ok(VideoWorkerStatus::stopped(kind, restart_count));
        }

        let launch = running.launch.clone();
        let next_restart_count = restart_count.saturating_add(1);
        let delay = launch.restart_policy.restart_delay;
        let mut crashed = workers.remove(&kind).expect("worker entry exists");
        finish_exited_worker(&mut crashed);
        if !delay.is_zero() {
            std::thread::sleep(delay);
        }
        let replacement = spawn_worker(launch, self.start_timeout, next_restart_count)?;
        let status = replacement.status.clone();
        workers.insert(kind, replacement);
        Ok(status)
    }

    pub fn health(&self, kind: VideoWorkerKind) -> Result<(), VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        let running = workers.get_mut(&kind).ok_or_else(not_running)?;
        if running
            .child
            .try_wait()
            .map_err(|_| process_unavailable())?
            .is_some()
        {
            return Err(process_unavailable());
        }
        verify_health(running, self.request_timeout)
    }

    pub fn cancel(&self, kind: VideoWorkerKind, job_id: Uuid) -> Result<(), VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        let running = workers.get_mut(&kind).ok_or_else(not_running)?;
        let job_id = job_id.hyphenated().to_string();
        let authentication_proof = running.token.command_proof(kind, &job_id)?;
        let command = VideoWorkerCommandDocument {
            command: "worker.cancel",
            job_id: &job_id,
            protocol_version: WORKER_PROTOCOL_VERSION,
            worker_kind: kind.as_str(),
            authentication_proof: &authentication_proof,
        };
        let mut bytes =
            Zeroizing::new(serde_json::to_vec(&command).map_err(|_| process_unavailable())?);
        bytes.push(b'\n');
        running
            .stdin
            .write_all(&bytes)
            .and_then(|()| running.stdin.flush())
            .map_err(|_| process_unavailable())?;
        let line = receive_line(&running.events, self.request_timeout)?;
        let event: VideoWorkerCancelledEvent =
            serde_json::from_str(&line).map_err(|_| authentication_rejected())?;
        if event.event != "worker.cancelled"
            || event.job_id != job_id
            || event.protocol_version != WORKER_PROTOCOL_VERSION
            || event.worker_kind != kind.as_str()
            || event.worker_version != running.launch.expected_version
            || !running.token.verify_event_proof(
                "worker.cancelled",
                kind,
                &event.worker_version,
                &event.job_id,
                &event.authentication_proof,
            )
        {
            return Err(authentication_rejected());
        }
        Ok(())
    }

    pub fn stop(&self, kind: VideoWorkerKind) -> Result<(), VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        let mut running = workers.remove(&kind).ok_or_else(not_running)?;
        force_stop(&mut running);
        Ok(())
    }

    pub fn stop_all(&self) -> Result<(), VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        for (_, mut running) in std::mem::take(&mut *workers) {
            force_stop(&mut running);
        }
        Ok(())
    }

    fn lock_workers(
        &self,
    ) -> Result<MutexGuard<'_, BTreeMap<VideoWorkerKind, RunningVideoWorker>>, VideoWorkerError>
    {
        self.workers.lock().map_err(|_| process_unavailable())
    }
}

impl Drop for LocalVideoOrchestrator {
    fn drop(&mut self) {
        if let Ok(mut workers) = self.workers.lock() {
            for (_, mut running) in std::mem::take(&mut *workers) {
                force_stop(&mut running);
            }
        }
    }
}

struct RunningVideoWorker {
    child: Child,
    stdin: ChildStdin,
    process_tree: ManagedProcessTree,
    token: VideoWorkerSessionToken,
    events: Receiver<Result<String, ()>>,
    stdout_thread: Option<JoinHandle<()>>,
    stderr_thread: Option<JoinHandle<()>>,
    launch: VideoWorkerLaunch,
    status: VideoWorkerStatus,
}

struct VideoWorkerSessionToken {
    bytes: [u8; SESSION_TOKEN_BYTES],
}

impl VideoWorkerSessionToken {
    fn generate() -> Result<Self, VideoWorkerError> {
        let mut bytes = [0_u8; SESSION_TOKEN_BYTES];
        getrandom::fill(&mut bytes).map_err(|_| process_unavailable())?;
        Ok(Self { bytes })
    }

    fn encoded(&self) -> Zeroizing<String> {
        use fmt::Write as _;

        let mut encoded = Zeroizing::new(String::with_capacity(SESSION_TOKEN_BYTES * 2));
        for byte in self.bytes {
            write!(&mut *encoded, "{byte:02x}").expect("writing to a String cannot fail");
        }
        encoded
    }

    fn verify_event_proof(
        &self,
        event: &str,
        kind: VideoWorkerKind,
        worker_version: &str,
        detail: &str,
        proof: &str,
    ) -> bool {
        let Some(encoded) = proof.strip_prefix(EVENT_PROOF_PREFIX) else {
            return false;
        };
        let Ok(presented) = URL_SAFE_NO_PAD.decode(encoded) else {
            return false;
        };
        let Ok(mut authenticator) = HmacSha256::new_from_slice(&self.bytes) else {
            return false;
        };
        update_authenticator(
            &mut authenticator,
            EVENT_AUTHENTICATION_DOMAIN,
            &[
                event,
                kind.as_str(),
                WORKER_PROTOCOL_VERSION,
                worker_version,
                detail,
            ],
        );
        authenticator.verify_slice(&presented).is_ok()
    }

    fn command_proof(
        &self,
        kind: VideoWorkerKind,
        job_id: &str,
    ) -> Result<Zeroizing<String>, VideoWorkerError> {
        let mut authenticator =
            HmacSha256::new_from_slice(&self.bytes).map_err(|_| process_unavailable())?;
        update_authenticator(
            &mut authenticator,
            COMMAND_AUTHENTICATION_DOMAIN,
            &[
                "worker.cancel",
                kind.as_str(),
                WORKER_PROTOCOL_VERSION,
                job_id,
            ],
        );
        Ok(Zeroizing::new(format!(
            "{COMMAND_PROOF_PREFIX}{}",
            URL_SAFE_NO_PAD.encode(authenticator.finalize().into_bytes())
        )))
    }
}

impl Drop for VideoWorkerSessionToken {
    fn drop(&mut self) {
        self.bytes.zeroize();
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct VideoWorkerBootstrapDocument<'a> {
    asset_root: &'a str,
    bootstrap_version: &'static str,
    local_session_token: &'a str,
    protocol_version: &'static str,
    worker_kind: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct VideoWorkerCommandDocument<'a> {
    command: &'static str,
    job_id: &'a str,
    protocol_version: &'static str,
    worker_kind: &'static str,
    authentication_proof: &'a str,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerReadyEvent {
    authentication_proof: String,
    event: String,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
    port: u16,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerHealthEvent {
    authentication_proof: String,
    event: String,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
    port: u16,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerCancelledEvent {
    authentication_proof: String,
    event: String,
    job_id: String,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
}

fn spawn_worker(
    launch: VideoWorkerLaunch,
    start_timeout: Duration,
    restart_count: u8,
) -> Result<RunningVideoWorker, VideoWorkerError> {
    let token = VideoWorkerSessionToken::generate()?;
    let mut command = Command::new(&launch.executable_path);
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    configure_managed_process(&mut command);
    let mut child = command.spawn().map_err(|_| process_unavailable())?;
    let mut process_tree = match ManagedProcessTree::attach(&child) {
        Ok(process_tree) => process_tree,
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(process_unavailable());
        }
    };
    let setup = (|| {
        let mut stdin = child.stdin.take().ok_or_else(process_unavailable)?;
        let stdout = child.stdout.take().ok_or_else(process_unavailable)?;
        let stderr = child.stderr.take().ok_or_else(process_unavailable)?;
        let (events, stdout_thread) = spawn_stdout_reader(stdout);
        let stderr_thread = spawn_stderr_drain(stderr);
        write_bootstrap(&token, &launch, &mut stdin)?;
        let line = receive_line(&events, start_timeout)?;
        let event: VideoWorkerReadyEvent =
            serde_json::from_str(&line).map_err(|_| authentication_rejected())?;
        validate_ready_event(&token, &launch, &event)?;
        let status = VideoWorkerStatus::running(
            launch.kind,
            event.worker_version,
            event.port,
            child.id(),
            restart_count,
        );
        Ok((stdin, events, stdout_thread, stderr_thread, status))
    })();
    let (stdin, events, stdout_thread, stderr_thread, status) = match setup {
        Ok(value) => value,
        Err(error) => {
            let _ = process_tree.terminate();
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
    };
    let mut running = RunningVideoWorker {
        child,
        stdin,
        process_tree,
        token,
        events,
        stdout_thread: Some(stdout_thread),
        stderr_thread: Some(stderr_thread),
        launch,
        status,
    };
    if let Err(error) = verify_health(&running, start_timeout) {
        force_stop(&mut running);
        return Err(error);
    }
    Ok(running)
}

fn write_bootstrap(
    token: &VideoWorkerSessionToken,
    launch: &VideoWorkerLaunch,
    stdin: &mut ChildStdin,
) -> Result<(), VideoWorkerError> {
    let encoded = token.encoded();
    let asset_root = launch
        .asset_root
        .to_str()
        .ok_or_else(configuration_invalid)?;
    let document = VideoWorkerBootstrapDocument {
        asset_root,
        bootstrap_version: BOOTSTRAP_VERSION,
        local_session_token: &encoded,
        protocol_version: WORKER_PROTOCOL_VERSION,
        worker_kind: launch.kind.as_str(),
    };
    let mut bytes =
        Zeroizing::new(serde_json::to_vec(&document).map_err(|_| process_unavailable())?);
    bytes.push(b'\n');
    if bytes.len() > MAX_LINE_BYTES {
        return Err(process_unavailable());
    }
    stdin
        .write_all(&bytes)
        .and_then(|()| stdin.flush())
        .map_err(|_| process_unavailable())
}

fn validate_ready_event(
    token: &VideoWorkerSessionToken,
    launch: &VideoWorkerLaunch,
    event: &VideoWorkerReadyEvent,
) -> Result<(), VideoWorkerError> {
    let port = event.port.to_string();
    if event.event != "worker.ready"
        || event.protocol_version != WORKER_PROTOCOL_VERSION
        || event.worker_kind != launch.kind.as_str()
        || !token.verify_event_proof(
            "worker.ready",
            launch.kind,
            &event.worker_version,
            &port,
            &event.authentication_proof,
        )
    {
        return Err(authentication_rejected());
    }
    if event.worker_version != launch.expected_version {
        return Err(VideoWorkerError::new(VideoWorkerErrorCode::VersionMismatch));
    }
    Ok(())
}

fn verify_health(running: &RunningVideoWorker, timeout: Duration) -> Result<(), VideoWorkerError> {
    let port = running.status.port.ok_or_else(process_unavailable)?;
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port);
    let mut stream = TcpStream::connect_timeout(&address, timeout).map_err(|error| {
        if error.kind() == std::io::ErrorKind::TimedOut {
            timed_out()
        } else {
            process_unavailable()
        }
    })?;
    stream
        .set_read_timeout(Some(timeout))
        .and_then(|()| stream.set_write_timeout(Some(timeout)))
        .map_err(|_| process_unavailable())?;
    let token = running.token.encoded();
    let request = Zeroizing::new(format!(
        "GET /health HTTP/1.1\r\nHost: {LOOPBACK_HOST}:{port}\r\nAuthorization: Bearer {}\r\nConnection: close\r\n\r\n",
        &*token,
    ));
    stream
        .write_all(request.as_bytes())
        .and_then(|()| stream.flush())
        .map_err(|_| process_unavailable())?;
    let mut response = Vec::new();
    stream
        .take(MAX_HTTP_RESPONSE_BYTES)
        .read_to_end(&mut response)
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::TimedOut {
                timed_out()
            } else {
                process_unavailable()
            }
        })?;
    let response = std::str::from_utf8(&response).map_err(|_| authentication_rejected())?;
    let (headers, body) = response
        .split_once("\r\n\r\n")
        .ok_or_else(authentication_rejected)?;
    if !headers.starts_with("HTTP/1.1 200 ") {
        return Err(authentication_rejected());
    }
    let event: VideoWorkerHealthEvent =
        serde_json::from_str(body).map_err(|_| authentication_rejected())?;
    let detail = port.to_string();
    if event.event != "worker.health"
        || event.protocol_version != WORKER_PROTOCOL_VERSION
        || event.worker_kind != running.launch.kind.as_str()
        || event.worker_version != running.launch.expected_version
        || event.port != port
        || !running.token.verify_event_proof(
            "worker.health",
            running.launch.kind,
            &event.worker_version,
            &detail,
            &event.authentication_proof,
        )
    {
        return Err(authentication_rejected());
    }
    Ok(())
}

fn update_authenticator(authenticator: &mut HmacSha256, domain: &[u8], parts: &[&str]) {
    authenticator.update(domain);
    for (index, part) in parts.iter().enumerate() {
        if index > 0 {
            authenticator.update(b"\0");
        }
        authenticator.update(part.as_bytes());
    }
}

fn spawn_stdout_reader(stdout: ChildStdout) -> (Receiver<Result<String, ()>>, JoinHandle<()>) {
    let (sender, receiver) = mpsc::channel();
    let thread = std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        loop {
            let mut bytes = Vec::new();
            let read = reader
                .by_ref()
                .take((MAX_LINE_BYTES + 1) as u64)
                .read_until(b'\n', &mut bytes);
            let Ok(count) = read else {
                let _ = sender.send(Err(()));
                return;
            };
            if count == 0 {
                return;
            }
            if bytes.len() > MAX_LINE_BYTES || !bytes.ends_with(b"\n") {
                let _ = sender.send(Err(()));
                return;
            }
            bytes.pop();
            if bytes.ends_with(b"\r") {
                bytes.pop();
            }
            let line = String::from_utf8(bytes).map_err(|_| ());
            if sender.send(line).is_err() {
                return;
            }
        }
    });
    (receiver, thread)
}

fn spawn_stderr_drain(stderr: ChildStderr) -> JoinHandle<()> {
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stderr);
        let mut buffer = [0_u8; 4096];
        while reader.read(&mut buffer).is_ok_and(|count| count > 0) {}
    })
}

fn receive_line(
    events: &Receiver<Result<String, ()>>,
    timeout: Duration,
) -> Result<String, VideoWorkerError> {
    events
        .recv_timeout(timeout)
        .map_err(|error| match error {
            mpsc::RecvTimeoutError::Timeout => timed_out(),
            mpsc::RecvTimeoutError::Disconnected => process_unavailable(),
        })?
        .map_err(|()| authentication_rejected())
}

fn force_stop(running: &mut RunningVideoWorker) {
    let _ = running.process_tree.terminate();
    let _ = running.child.kill();
    let _ = running.child.wait();
    join_readers(running);
}

fn finish_exited_worker(running: &mut RunningVideoWorker) {
    let _ = running.child.wait();
    let _ = running.process_tree.terminate();
    join_readers(running);
}

fn join_readers(running: &mut RunningVideoWorker) {
    if let Some(thread) = running.stdout_thread.take() {
        let _ = thread.join();
    }
    if let Some(thread) = running.stderr_thread.take() {
        let _ = thread.join();
    }
}

fn validate_executable_path(path: &Path) -> Result<(), VideoWorkerError> {
    if !path.is_absolute() || path.as_os_str().len() > MAX_PATH_BYTES {
        return Err(configuration_invalid());
    }
    for ancestor in path.ancestors() {
        let metadata = fs::symlink_metadata(ancestor).map_err(|_| configuration_invalid())?;
        #[cfg(windows)]
        let windows_file_attributes = {
            use std::os::windows::fs::MetadataExt;
            Some(metadata.file_attributes())
        };
        #[cfg(not(windows))]
        let windows_file_attributes = None;
        if unsafe_path_component(metadata.file_type().is_symlink(), windows_file_attributes) {
            return Err(configuration_invalid());
        }
    }
    let metadata = fs::metadata(path).map_err(|_| configuration_invalid())?;
    if !metadata.is_file() {
        return Err(configuration_invalid());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o111 == 0 {
            return Err(configuration_invalid());
        }
    }
    Ok(())
}

fn validate_directory_path(path: &Path) -> Result<(), VideoWorkerError> {
    if !path.is_absolute() || path.as_os_str().len() > MAX_PATH_BYTES {
        return Err(configuration_invalid());
    }
    for ancestor in path.ancestors() {
        let metadata = fs::symlink_metadata(ancestor).map_err(|_| configuration_invalid())?;
        #[cfg(windows)]
        let windows_file_attributes = {
            use std::os::windows::fs::MetadataExt;
            Some(metadata.file_attributes())
        };
        #[cfg(not(windows))]
        let windows_file_attributes = None;
        if unsafe_path_component(metadata.file_type().is_symlink(), windows_file_attributes) {
            return Err(configuration_invalid());
        }
    }
    if !fs::metadata(path)
        .map_err(|_| configuration_invalid())?
        .is_dir()
    {
        return Err(configuration_invalid());
    }
    Ok(())
}

const fn unsafe_path_component(is_symlink: bool, windows_file_attributes: Option<u32>) -> bool {
    is_symlink
        || match windows_file_attributes {
            Some(attributes) => attributes & WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT != 0,
            None => false,
        }
}

const fn configuration_invalid() -> VideoWorkerError {
    VideoWorkerError::new(VideoWorkerErrorCode::ConfigurationInvalid)
}

const fn authentication_rejected() -> VideoWorkerError {
    VideoWorkerError::new(VideoWorkerErrorCode::AuthenticationRejected)
}

const fn not_running() -> VideoWorkerError {
    VideoWorkerError::new(VideoWorkerErrorCode::NotRunning)
}

const fn process_unavailable() -> VideoWorkerError {
    VideoWorkerError::new(VideoWorkerErrorCode::ProcessUnavailable)
}

const fn timed_out() -> VideoWorkerError {
    VideoWorkerError::new(VideoWorkerErrorCode::TimedOut)
}

#[cfg(test)]
mod tests {
    use super::unsafe_path_component;

    #[test]
    fn windows_reparse_points_are_rejected_even_when_not_reported_as_symlinks() {
        assert!(unsafe_path_component(false, Some(0x400)));
        assert!(!unsafe_path_component(false, Some(0x20)));
        assert!(unsafe_path_component(true, None));
    }
}

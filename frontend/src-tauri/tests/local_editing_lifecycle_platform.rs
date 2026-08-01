use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::local_editing_job_ledger::{
    LocalEditingJobRecoveryPolicy, LocalEditingJobScheduler, LocalEditingJobStatus,
};
use automation_tool_desktop_lib::local_video_orchestrator::{
    LocalVideoOrchestrator, VideoWorkerErrorCode, VideoWorkerKind, VideoWorkerLaunch,
    VideoWorkerLocalEditingJobRequest, VideoWorkerMediaToolsConfiguration,
    VideoWorkerRestartPolicy, VideoWorkerState,
};
use automation_tool_desktop_lib::video_job_workspace::{
    VideoJobWorkspacePolicy, VideoJobWorkspaceStore,
};
use uuid::Uuid;

static TEMPORARY_SEQUENCE: AtomicU64 = AtomicU64::new(0);
const WORKER_VERSION: &str = "1.2.3";

struct TemporaryAppData {
    path: PathBuf,
}

impl TemporaryAppData {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-le12-native-lifecycle-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            TEMPORARY_SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        fs::create_dir(&path).expect("AppData fixture");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700))
                .expect("private AppData fixture");
        }
        Self {
            path: fs::canonicalize(path).expect("canonical AppData fixture"),
        }
    }
}

impl Drop for TemporaryAppData {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

fn required_executable(name: &str) -> PathBuf {
    let configured = std::env::var_os(name).unwrap_or_else(|| discover_executable(name));
    let path = fs::canonicalize(configured).expect("canonical configured executable");
    assert!(path.is_file(), "configured executable is not a file");
    path
}

fn discover_executable(name: &str) -> std::ffi::OsString {
    if name == "LE12_MEDIA_TOOL_A" {
        return required_executable("LE12_PYTHON_EXECUTABLE").into_os_string();
    }
    let candidates: &[&str] = match name {
        "LE12_PYTHON_EXECUTABLE" if cfg!(windows) => &["python.exe", "python3.exe"],
        "LE12_PYTHON_EXECUTABLE" => &["/usr/bin/python3", "python3"],
        "LE12_MEDIA_TOOL_B" if cfg!(windows) => &["cmd.exe", "powershell.exe"],
        "LE12_MEDIA_TOOL_B" => &["/usr/bin/true", "/bin/sh"],
        _ => &[],
    };
    for candidate in candidates {
        let direct = Path::new(candidate);
        if direct.is_absolute() && direct.is_file() {
            return direct.as_os_str().to_owned();
        }
        let locator = if cfg!(windows) { "where.exe" } else { "which" };
        let Ok(output) = Command::new(locator).arg(candidate).output() else {
            continue;
        };
        if !output.status.success() {
            continue;
        }
        if let Some(path) = String::from_utf8_lossy(&output.stdout)
            .lines()
            .map(str::trim)
            .find(|value| !value.is_empty())
        {
            return std::ffi::OsString::from(path);
        }
    }
    panic!("{name} executable discovery failed; use scripts/run_le12_native_lifecycle.py")
}

fn workspace_store(root: &Path) -> VideoJobWorkspaceStore {
    VideoJobWorkspaceStore::initialize(
        root,
        VideoJobWorkspacePolicy::new(16 * 1024 * 1024, 8 * 1024 * 1024, 8, 3600, 0)
            .expect("workspace policy"),
    )
    .expect("workspace store")
}

fn orchestrator_service() -> LocalVideoOrchestrator {
    LocalVideoOrchestrator::new(Duration::from_secs(10), Duration::from_secs(10))
        .expect("orchestrator")
}

fn request(revision: u32) -> VideoWorkerLocalEditingJobRequest {
    VideoWorkerLocalEditingJobRequest::new(
        Uuid::parse_str("223e4567-e89b-42d3-a456-426614174101").expect("project ID"),
        Uuid::parse_str("323e4567-e89b-42d3-a456-426614174102").expect("timeline ID"),
        revision,
    )
    .expect("editing request")
}

fn job_id() -> Uuid {
    Uuid::parse_str("123e4567-e89b-42d3-a456-426614174100").expect("job ID")
}

fn launch(root: &Path) -> VideoWorkerLaunch {
    let entrypoint = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("local_editing_lifecycle_worker.py");
    VideoWorkerLaunch::new(
        VideoWorkerKind::Python,
        required_executable("LE12_PYTHON_EXECUTABLE"),
        root.to_path_buf(),
        WORKER_VERSION.to_owned(),
        VideoWorkerRestartPolicy::new(2, Duration::from_millis(10)).expect("restart policy"),
    )
    .expect("Python Worker launch")
    .with_python_entrypoint(entrypoint)
    .expect("single Python entrypoint")
    .with_media_tools(
        VideoWorkerMediaToolsConfiguration::new(
            required_executable("LE12_MEDIA_TOOL_A"),
            required_executable("LE12_MEDIA_TOOL_B"),
        )
        .expect("two native executable fixtures"),
    )
    .expect("authenticated media tools")
}

fn next_snapshot(
    scheduler: &LocalEditingJobScheduler,
    store: &VideoJobWorkspaceStore,
    orchestrator: &LocalVideoOrchestrator,
) -> automation_tool_desktop_lib::local_editing_job_ledger::LocalEditingJobSnapshot {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(snapshot) = scheduler
            .poll_with_recovery(
                store,
                orchestrator,
                job_id(),
                LocalEditingJobRecoveryPolicy::new(2).unwrap(),
            )
            .expect("poll native Worker")
        {
            return snapshot;
        }
        assert!(Instant::now() < deadline, "native Worker event timed out");
        std::thread::sleep(Duration::from_millis(5));
    }
}

fn wait_until_stopped(process_id: u32) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while process_exists(process_id) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(!process_exists(process_id), "native Worker remained alive");
}

#[cfg(unix)]
fn process_exists(process_id: u32) -> bool {
    let result = unsafe { libc::kill(process_id as i32, 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

#[cfg(windows)]
fn process_exists(process_id: u32) -> bool {
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, process_id) };
    if handle.is_null() {
        return false;
    }
    let mut exit_code = 0_u32;
    let active = unsafe { GetExitCodeProcess(handle, &mut exit_code) } != 0 && exit_code == 259;
    unsafe {
        CloseHandle(handle);
    }
    active
}

#[test]
fn native_process_success_is_authenticated_and_durable() {
    let app_data = TemporaryAppData::new();
    let store = workspace_store(&app_data.path);
    let orchestrator = orchestrator_service();
    let scheduler = LocalEditingJobScheduler::new();
    let status = orchestrator
        .start(launch(&app_data.path))
        .expect("start native Python Worker");
    let process_id = status.process_id().expect("native process ID");
    assert_eq!(status.host(), Some("127.0.0.1"));
    scheduler
        .create(&store, job_id(), &request(7))
        .expect("persist queued job");
    scheduler
        .dispatch(&store, &orchestrator, job_id())
        .expect("dispatch native job");

    let mut terminal = next_snapshot(&scheduler, &store, &orchestrator);
    while terminal.status() != LocalEditingJobStatus::Succeeded {
        terminal = next_snapshot(&scheduler, &store, &orchestrator);
    }
    assert_eq!(
        LocalEditingJobScheduler::new()
            .snapshot(&store, job_id())
            .expect("reopen terminal checkpoint"),
        terminal,
    );
    orchestrator
        .stop(VideoWorkerKind::Python)
        .expect("stop native Worker after success");
    wait_until_stopped(process_id);
}

#[test]
fn python_entrypoint_is_single_assignment_and_rejects_other_worker_kinds() {
    let app_data = TemporaryAppData::new();
    let entrypoint = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("local_editing_lifecycle_worker.py");
    let base = VideoWorkerLaunch::new(
        VideoWorkerKind::Python,
        required_executable("LE12_PYTHON_EXECUTABLE"),
        app_data.path.clone(),
        WORKER_VERSION.to_owned(),
        VideoWorkerRestartPolicy::new(0, Duration::ZERO).expect("restart policy"),
    )
    .expect("Python launch");
    let configured = base
        .with_python_entrypoint(entrypoint.clone())
        .expect("first entrypoint");
    let duplicate = match configured.with_python_entrypoint(entrypoint.clone()) {
        Err(error) => error,
        Ok(_) => panic!("entrypoint is single-assignment"),
    };
    assert_eq!(duplicate.code(), VideoWorkerErrorCode::ConfigurationInvalid);
    let node = VideoWorkerLaunch::new(
        VideoWorkerKind::Node,
        required_executable("LE12_PYTHON_EXECUTABLE"),
        app_data.path.clone(),
        WORKER_VERSION.to_owned(),
        VideoWorkerRestartPolicy::new(0, Duration::ZERO).expect("restart policy"),
    )
    .expect("Node launch fixture");
    let wrong_kind = match node.with_python_entrypoint(entrypoint) {
        Err(error) => error,
        Ok(_) => panic!("Python entrypoint cannot attach to Node"),
    };
    assert_eq!(
        wrong_kind.code(),
        VideoWorkerErrorCode::ConfigurationInvalid
    );
}

#[test]
fn native_process_cooperative_cancel_and_emergency_stop_are_distinct() {
    let app_data = TemporaryAppData::new();
    let store = workspace_store(&app_data.path);
    let orchestrator = orchestrator_service();
    let scheduler = LocalEditingJobScheduler::new();
    let status = orchestrator
        .start(launch(&app_data.path))
        .expect("start cancellable native Worker");
    let process_id = status.process_id().expect("native process ID");
    scheduler
        .create(&store, job_id(), &request(8))
        .expect("persist cancellable job");
    scheduler
        .dispatch(&store, &orchestrator, job_id())
        .expect("dispatch cancellable job");
    assert_eq!(
        next_snapshot(&scheduler, &store, &orchestrator).progress_per_mille(),
        0
    );
    let cancelling = scheduler
        .request_cancel(&store, &orchestrator, job_id())
        .expect("request cooperative cancellation");
    assert_eq!(cancelling.status(), LocalEditingJobStatus::Cancelling);
    let cancelled = next_snapshot(&scheduler, &store, &orchestrator);
    assert_eq!(cancelled.status(), LocalEditingJobStatus::Cancelled);
    assert!(
        process_exists(process_id),
        "cooperative cancel killed the Worker"
    );
    orchestrator
        .stop(VideoWorkerKind::Python)
        .expect("stop after cooperative cancellation");
    wait_until_stopped(process_id);

    let emergency_root = TemporaryAppData::new();
    let emergency_store = workspace_store(&emergency_root.path);
    let emergency_orchestrator = orchestrator_service();
    let emergency_scheduler = LocalEditingJobScheduler::new();
    let emergency_status = emergency_orchestrator
        .start(launch(&emergency_root.path))
        .expect("start emergency-stop Worker");
    let emergency_process_id = emergency_status.process_id().expect("emergency process ID");
    emergency_scheduler
        .create(&emergency_store, job_id(), &request(11))
        .expect("persist emergency job");
    emergency_scheduler
        .dispatch(&emergency_store, &emergency_orchestrator, job_id())
        .expect("dispatch emergency job");
    next_snapshot(
        &emergency_scheduler,
        &emergency_store,
        &emergency_orchestrator,
    );
    let emergency = emergency_scheduler
        .emergency_stop(&emergency_store, &emergency_orchestrator, job_id())
        .expect("emergency-stop process tree");
    assert_eq!(emergency.status(), LocalEditingJobStatus::Cancelled);
    wait_until_stopped(emergency_process_id);
    assert_eq!(
        emergency_orchestrator
            .status(VideoWorkerKind::Python)
            .expect("stopped Worker status")
            .state(),
        VideoWorkerState::Stopped,
    );
}

#[test]
fn native_process_crash_recovers_with_a_new_generation() {
    let app_data = TemporaryAppData::new();
    let store = workspace_store(&app_data.path);
    let orchestrator = orchestrator_service();
    let scheduler = LocalEditingJobScheduler::new();
    let first = orchestrator
        .start(launch(&app_data.path))
        .expect("start crash-once native Worker");
    let first_process_id = first.process_id().expect("first process ID");
    scheduler
        .create(&store, job_id(), &request(9))
        .expect("persist crash-once job");
    scheduler
        .dispatch(&store, &orchestrator, job_id())
        .expect("dispatch crash-once job");
    next_snapshot(&scheduler, &store, &orchestrator);

    let mut terminal = next_snapshot(&scheduler, &store, &orchestrator);
    while terminal.status() != LocalEditingJobStatus::Succeeded {
        terminal = next_snapshot(&scheduler, &store, &orchestrator);
    }
    assert_eq!(terminal.recovery_attempts(), 1);
    assert_eq!(terminal.worker_generation(), 1);
    wait_until_stopped(first_process_id);
    let replacement_process_id = orchestrator
        .status(VideoWorkerKind::Python)
        .expect("replacement status")
        .process_id()
        .expect("replacement process ID");
    assert_ne!(replacement_process_id, first_process_id);
    orchestrator
        .stop(VideoWorkerKind::Python)
        .expect("stop replacement Worker");
    wait_until_stopped(replacement_process_id);
}

#[test]
fn native_process_running_job_recovers_after_app_components_restart() {
    let app_data = TemporaryAppData::new();
    let store = workspace_store(&app_data.path);
    let first_process_id = {
        let first_orchestrator = orchestrator_service();
        let first_scheduler = LocalEditingJobScheduler::new();
        let first = first_orchestrator
            .start(launch(&app_data.path))
            .expect("start first App Worker");
        let first_process_id = first.process_id().expect("first App process ID");
        first_scheduler
            .create(&store, job_id(), &request(10))
            .expect("persist App-restart job");
        first_scheduler
            .dispatch(&store, &first_orchestrator, job_id())
            .expect("dispatch App-restart job");
        next_snapshot(&first_scheduler, &store, &first_orchestrator);
        first_process_id
    };
    wait_until_stopped(first_process_id);

    let restarted_orchestrator = orchestrator_service();
    let replacement = restarted_orchestrator
        .start(launch(&app_data.path))
        .expect("start replacement App Worker");
    let replacement_process_id = replacement
        .process_id()
        .expect("replacement App process ID");
    let restarted_scheduler = LocalEditingJobScheduler::new();
    let recovered = restarted_scheduler
        .reconcile_job(
            &store,
            &restarted_orchestrator,
            job_id(),
            LocalEditingJobRecoveryPolicy::new(2).unwrap(),
        )
        .expect("reconcile after App restart");
    assert_eq!(recovered.status(), LocalEditingJobStatus::Running);
    assert_eq!(recovered.recovery_attempts(), 1);
    let mut terminal = next_snapshot(&restarted_scheduler, &store, &restarted_orchestrator);
    while terminal.status() != LocalEditingJobStatus::Succeeded {
        terminal = next_snapshot(&restarted_scheduler, &store, &restarted_orchestrator);
    }
    assert_eq!(terminal.recovery_attempts(), 1);
    restarted_orchestrator
        .stop(VideoWorkerKind::Python)
        .expect("stop replacement App Worker");
    wait_until_stopped(replacement_process_id);
}

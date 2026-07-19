#![cfg(any(target_os = "macos", target_os = "windows"))]

use automation_tool_desktop_lib::browser_profiles::BrowserProfileStore;
#[cfg(target_os = "windows")]
use automation_tool_desktop_lib::executor_manager::{
    ExecutorLaunchConfiguration, ExecutorManager, ExecutorManagerState, ExecutorRestartPolicy,
};
#[cfg(target_os = "windows")]
use automation_tool_desktop_lib::executor_package::ExecutorPackageVerifier;
#[cfg(target_os = "windows")]
use ed25519_dalek::SigningKey;
use std::fs;
use std::path::Path;
#[cfg(target_os = "windows")]
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
#[cfg(target_os = "windows")]
use std::time::Duration;
use std::time::{SystemTime, UNIX_EPOCH};

const PACKAGED_PROBE_ENVIRONMENT: &str = "AUTOMATION_TOOL_B507_PACKAGED_PROBE";
static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(0);

struct TemporaryAppData {
    path: std::path::PathBuf,
}

impl TemporaryAppData {
    fn new() -> Self {
        let path = std::env::temp_dir()
            .canonicalize()
            .expect("canonical temporary root")
            .join(format!(
                "automation-tool-b5-07-acceptance-{}-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .expect("system time")
                    .as_nanos(),
                NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed)
            ));
        fs::create_dir(&path).expect("create isolated AppData");
        Self { path }
    }
}

impl Drop for TemporaryAppData {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

fn run_packaged_probe(browser_executable: &Path) {
    let probe = std::env::var_os(PACKAGED_PROBE_ENVIRONMENT)
        .map(std::path::PathBuf::from)
        .expect("packaged probe path");
    assert!(probe.is_absolute());
    assert!(probe.is_file());

    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let profile = store.create_douyin_profile().expect("private profile");
    let lock = profile.try_acquire_lock().expect("exclusive profile lock");
    let output = Command::new(&probe)
        .arg(browser_executable)
        .arg(profile.directory())
        .output()
        .expect("run packaged browser probe");

    assert!(output.status.success());
    assert_eq!(output.stdout, b"browser.runtime.ready\n");
    assert!(output.stderr.is_empty());
    profile.revalidate().expect("profile identity after close");
    lock.release().expect("explicit profile lock release");
}

#[cfg(target_os = "macos")]
fn force_stop_packaged_probe_tree(browser_executable: &Path) {
    use std::io::{BufRead, BufReader, Read};
    use std::os::unix::process::CommandExt;
    use std::process::Stdio;

    let probe = std::env::var_os(PACKAGED_PROBE_ENVIRONMENT)
        .map(std::path::PathBuf::from)
        .expect("packaged probe path");
    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let profile = store.create_douyin_profile().expect("private profile");
    let lock = profile.try_acquire_lock().expect("exclusive profile lock");
    let mut child = Command::new(&probe)
        .arg(browser_executable)
        .arg(profile.directory())
        .arg("--hold-for-process-tree-test")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .expect("start held packaged browser probe");
    let process_group = child.id() as i32;
    let _held_stdin = child.stdin.take().expect("held probe stdin");
    let stdout = child.stdout.take().expect("held probe stdout");
    let mut reader = BufReader::new(stdout);
    let mut ready = String::new();
    let ready_result = reader.read_line(&mut ready);

    let kill_result = unsafe { libc::kill(-process_group, libc::SIGKILL) };
    let status = child.wait().expect("wait for killed browser process tree");
    let mut stderr = Vec::new();
    child
        .stderr
        .take()
        .expect("held probe stderr")
        .read_to_end(&mut stderr)
        .expect("read held probe stderr");
    let group_still_exists = unsafe { libc::kill(-process_group, 0) } == 0;
    profile
        .revalidate()
        .expect("profile identity after hard stop");
    lock.release().expect("explicit profile lock release");

    assert_eq!(ready_result.expect("read held probe ready"), ready.len());
    assert_eq!(ready, "browser.runtime.ready\n");
    assert_eq!(kill_result, 0);
    assert!(!status.success());
    assert!(!group_still_exists);
    assert!(stderr.is_empty());
}

#[cfg(target_os = "macos")]
#[test]
#[ignore = "requires the B5-07 signed-system-browser PyInstaller orchestrator"]
fn packaged_runtime_launches_a_trusted_browser_with_a_locked_private_profile() {
    use automation_tool_desktop_lib::browser_discovery::{
        discover_macos_browsers, revalidate_macos_browser,
    };

    let browsers = discover_macos_browsers().expect("trusted browser discovery");
    let browser = browsers.first().expect("installed trusted browser");
    revalidate_macos_browser(browser).expect("trusted browser before launch");
    run_packaged_probe(browser.executable_path());
    revalidate_macos_browser(browser).expect("trusted browser after launch");
}

#[cfg(target_os = "macos")]
#[test]
#[ignore = "requires the B5-08 signed-system-browser PyInstaller orchestrator"]
fn packaged_runtime_hard_stop_terminates_the_complete_browser_process_tree() {
    use automation_tool_desktop_lib::browser_discovery::{
        discover_macos_browsers, revalidate_macos_browser,
    };

    let browsers = discover_macos_browsers().expect("trusted browser discovery");
    let browser = browsers.first().expect("installed trusted browser");
    revalidate_macos_browser(browser).expect("trusted browser before hard stop");
    force_stop_packaged_probe_tree(browser.executable_path());
    revalidate_macos_browser(browser).expect("trusted browser after hard stop");
}

#[cfg(target_os = "windows")]
#[test]
#[ignore = "requires the B5-07 signed-system-browser PyInstaller orchestrator"]
fn packaged_runtime_launches_a_trusted_browser_with_a_locked_private_profile() {
    use automation_tool_desktop_lib::browser_discovery::{
        discover_windows_browsers, revalidate_windows_browser,
    };

    let browsers = discover_windows_browsers().expect("trusted browser discovery");
    let browser = browsers.first().expect("installed trusted browser");
    revalidate_windows_browser(browser).expect("trusted browser before launch");
    run_packaged_probe(browser.executable_path());
    revalidate_windows_browser(browser).expect("trusted browser after launch");
}
#[cfg(target_os = "windows")]
const TEST_SIGNING_SEED: [u8; 32] = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25,
    26, 27, 28, 29, 30, 31,
];
#[cfg(target_os = "windows")]
const MANAGER_BROWSER_FILE: &str = "b5-08-browser-path";
#[cfg(target_os = "windows")]
const MANAGER_PROFILE_FILE: &str = "b5-08-profile-path";
#[cfg(target_os = "windows")]
const MANAGER_DESCENDANTS_FILE: &str = "b5-08-descendant-pids";

#[cfg(target_os = "windows")]
fn manager_for_browser_probe(package_root: PathBuf) -> ExecutorManager {
    let verifier = ExecutorPackageVerifier::new(
        SigningKey::from_bytes(&TEST_SIGNING_SEED)
            .verifying_key()
            .to_bytes(),
        "=0.1.0",
        None,
    )
    .expect("B5-08 package verifier");
    let restart_policy =
        ExecutorRestartPolicy::new(0, Duration::from_millis(10), Duration::from_millis(10))
            .expect("B5-08 restart policy");
    ExecutorManager::new(
        package_root,
        verifier,
        Duration::from_secs(20),
        Duration::from_secs(10),
        restart_policy,
    )
    .expect("B5-08 Executor Manager")
}

#[cfg(target_os = "windows")]
fn wait_for_browser_descendants(state_directory: &Path) -> Vec<u32> {
    let marker = state_directory.join(MANAGER_DESCENDANTS_FILE);
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    loop {
        let process_ids = fs::read_to_string(&marker)
            .unwrap_or_default()
            .lines()
            .filter_map(|line| line.trim().parse().ok())
            .collect::<Vec<_>>();
        if process_ids.len() >= 2 {
            return process_ids;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "B5-08 browser descendant marker was not ready"
        );
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(target_os = "windows")]
fn process_image_name(process_id: u32) -> Option<String> {
    use std::os::windows::ffi::OsStringExt;
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::Threading::{
        OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
    };

    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, process_id) };
    if handle.is_null() {
        return None;
    }
    let mut source = vec![0_u16; 32_768];
    let mut length = source.len() as u32;
    let queried =
        unsafe { QueryFullProcessImageNameW(handle, 0, source.as_mut_ptr(), &mut length) } != 0;
    unsafe {
        CloseHandle(handle);
    }
    if !queried {
        return None;
    }
    PathBuf::from(std::ffi::OsString::from_wide(&source[..length as usize]))
        .file_name()
        .map(|name| name.to_string_lossy().to_lowercase())
}

#[cfg(target_os = "windows")]
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

#[cfg(target_os = "windows")]
fn wait_for_process_exit(process_id: u32) {
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    while process_exists(process_id) && std::time::Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(
        !process_exists(process_id),
        "B5-08 browser descendant survived Windows Job cleanup"
    );
}

#[cfg(target_os = "windows")]
#[test]
#[ignore = "requires the B5-08 signed-system-browser PyInstaller orchestrator"]
fn packaged_runtime_hard_stop_terminates_the_complete_browser_process_tree() {
    use automation_tool_desktop_lib::browser_discovery::{
        discover_windows_browsers, revalidate_windows_browser,
    };

    let probe = std::env::var_os(PACKAGED_PROBE_ENVIRONMENT)
        .map(PathBuf::from)
        .expect("packaged probe path");
    let package_root = probe.parent().expect("packaged probe root").to_path_buf();
    let browsers = discover_windows_browsers().expect("trusted browser discovery");
    let browser = browsers.first().expect("installed trusted browser");
    revalidate_windows_browser(browser).expect("trusted browser before hard stop");

    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let profile = store.create_douyin_profile().expect("private profile");
    let lock = profile.try_acquire_lock().expect("exclusive profile lock");
    let state_directory = app_data.path.join("executor-state");
    fs::create_dir(&state_directory).expect("B5-08 Executor state");
    fs::write(
        state_directory.join(MANAGER_BROWSER_FILE),
        browser.executable_path().to_string_lossy().as_bytes(),
    )
    .expect("B5-08 browser path");
    fs::write(
        state_directory.join(MANAGER_PROFILE_FILE),
        profile.directory().to_string_lossy().as_bytes(),
    )
    .expect("B5-08 Profile path");

    let manager = manager_for_browser_probe(package_root);
    let launch = ExecutorLaunchConfiguration::new(
        "ws://127.0.0.1:9/api/v1/executors/connect".to_owned(),
        "atds1.private-control-plane-session".to_owned(),
        "11111111-1111-4111-8111-111111111111".to_owned(),
        "22222222-2222-4222-8222-222222222222".to_owned(),
        state_directory.clone(),
        1,
    )
    .expect("B5-08 launch configuration");
    assert_eq!(
        manager
            .start(launch)
            .expect("start browser Executor")
            .state(),
        ExecutorManagerState::Running
    );
    let process_ids = wait_for_browser_descendants(&state_directory);
    let expected_browser_name = browser
        .executable_path()
        .file_name()
        .expect("trusted browser filename")
        .to_string_lossy()
        .to_lowercase();
    assert!(
        process_ids
            .iter()
            .any(|process_id| process_image_name(*process_id).as_deref()
                == Some(&expected_browser_name)),
        "B5-08 descendant tree did not contain the trusted browser"
    );

    assert_eq!(
        manager.stop().expect("hard-stop browser Executor").state(),
        ExecutorManagerState::Stopped
    );
    for process_id in process_ids {
        wait_for_process_exit(process_id);
    }
    profile
        .revalidate()
        .expect("Profile identity after Windows Job hard stop");
    lock.release().expect("explicit Profile lock release");
    revalidate_windows_browser(browser).expect("trusted browser after hard stop");
}

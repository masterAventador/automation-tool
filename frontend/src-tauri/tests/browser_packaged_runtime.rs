#![cfg(any(target_os = "macos", target_os = "windows"))]

use automation_tool_desktop_lib::browser_profiles::BrowserProfileStore;
use std::fs;
use std::path::Path;
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
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

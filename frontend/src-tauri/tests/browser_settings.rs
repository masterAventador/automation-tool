#[cfg(target_os = "macos")]
use automation_tool_desktop_lib::browser_discovery::discover_macos_browsers;
#[cfg(target_os = "windows")]
use automation_tool_desktop_lib::browser_discovery::discover_windows_browsers;
use automation_tool_desktop_lib::browser_discovery::SupportedBrowser;
use automation_tool_desktop_lib::browser_settings::BrowserSettingsService;
use std::fs;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(0);

struct TemporaryAppData {
    path: std::path::PathBuf,
}

impl TemporaryAppData {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-b5-04-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed)
        ));
        Self { path }
    }
}

impl Drop for TemporaryAppData {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
#[test]
fn production_service_persists_only_an_available_browser_enum() {
    let app_data = TemporaryAppData::new();
    let service = BrowserSettingsService::initialize(&app_data.path).expect("settings service");
    let initial = service.snapshot().expect("discover browser settings");
    let selected = *initial
        .available_browsers()
        .first()
        .expect("test host must provide Chrome or Edge");

    service.select_browser(selected).expect("persist selection");
    let reopened = BrowserSettingsService::initialize(&app_data.path).expect("reopen service");
    let persisted = reopened.snapshot().expect("reload selection");
    assert_eq!(persisted.selected_browser(), Some(selected));
    assert!(persisted.available_browsers().contains(&selected));
    let executable = reopened
        .selected_executable_path()
        .expect("selected trusted executable");
    assert!(executable.is_absolute());
    assert!(executable.is_file());

    #[cfg(target_os = "macos")]
    assert!(!discover_macos_browsers()
        .expect("real macOS discovery")
        .is_empty());
    #[cfg(target_os = "windows")]
    assert!(!discover_windows_browsers()
        .expect("real Windows discovery")
        .is_empty());
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
#[test]
fn unavailable_supported_enum_is_rejected_without_replacing_the_selection() {
    let app_data = TemporaryAppData::new();
    let service = BrowserSettingsService::initialize(&app_data.path).expect("settings service");
    let initial = service.snapshot().expect("discover browser settings");
    let available = *initial
        .available_browsers()
        .first()
        .expect("test host must provide Chrome or Edge");
    let unavailable = match available {
        SupportedBrowser::GoogleChrome => SupportedBrowser::MicrosoftEdge,
        SupportedBrowser::MicrosoftEdge => SupportedBrowser::GoogleChrome,
    };
    service
        .select_browser(available)
        .expect("persist available browser");

    if !initial.available_browsers().contains(&unavailable) {
        assert!(service.select_browser(unavailable).is_err());
        assert_eq!(
            service
                .snapshot()
                .expect("reload selection")
                .selected_browser(),
            Some(available)
        );
    }
}

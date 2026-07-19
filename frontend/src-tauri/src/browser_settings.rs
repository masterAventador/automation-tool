#[cfg(target_os = "macos")]
use crate::browser_discovery::discover_macos_browsers;
#[cfg(target_os = "windows")]
use crate::browser_discovery::discover_windows_browsers;
use crate::browser_discovery::SupportedBrowser;
use crate::secure_store::{AppDataSecretStore, SecretStore};
use serde::{Deserialize, Serialize};
use std::error::Error;
use std::fmt::{Display, Formatter};
use std::path::Path;
use std::sync::Mutex;

const SETTINGS_DIRECTORY: &str = "settings";
const BROWSER_SELECTION_FILE: &str = "browser-selection-v1";
const SETTINGS_VERSION: u8 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrowserSettingsErrorCode {
    BrowserUnavailable,
    DiscoveryUnavailable,
    StorageUnavailable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BrowserSettingsError {
    code: BrowserSettingsErrorCode,
}

impl BrowserSettingsError {
    pub fn code(self) -> BrowserSettingsErrorCode {
        self.code
    }

    fn browser_unavailable() -> Self {
        Self {
            code: BrowserSettingsErrorCode::BrowserUnavailable,
        }
    }

    fn discovery_unavailable() -> Self {
        Self {
            code: BrowserSettingsErrorCode::DiscoveryUnavailable,
        }
    }

    fn storage_unavailable() -> Self {
        Self {
            code: BrowserSettingsErrorCode::StorageUnavailable,
        }
    }
}

impl Display for BrowserSettingsError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("browser settings unavailable")
    }
}

impl Error for BrowserSettingsError {}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BrowserSettingsSnapshot {
    available_browsers: Vec<SupportedBrowser>,
    selected_browser: Option<SupportedBrowser>,
}

impl BrowserSettingsSnapshot {
    pub fn available_browsers(&self) -> &[SupportedBrowser] {
        &self.available_browsers
    }

    pub fn selected_browser(&self) -> Option<SupportedBrowser> {
        self.selected_browser
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct StoredBrowserSelection {
    browser: SupportedBrowser,
    version: u8,
}

pub struct BrowserSettingsService {
    store: Mutex<AppDataSecretStore>,
}

impl BrowserSettingsService {
    pub fn initialize(app_data_directory: &Path) -> Result<Self, BrowserSettingsError> {
        if !app_data_directory.is_absolute() {
            return Err(BrowserSettingsError::storage_unavailable());
        }
        let store = AppDataSecretStore::new(
            &app_data_directory.join(SETTINGS_DIRECTORY),
            BROWSER_SELECTION_FILE,
        )
        .map_err(|_| BrowserSettingsError::storage_unavailable())?;
        Ok(Self {
            store: Mutex::new(store),
        })
    }

    pub fn snapshot(&self) -> Result<BrowserSettingsSnapshot, BrowserSettingsError> {
        self.snapshot_with_available(discover_available_browsers()?)
    }

    pub fn select_browser(
        &self,
        browser: SupportedBrowser,
    ) -> Result<BrowserSettingsSnapshot, BrowserSettingsError> {
        let available_browsers = discover_available_browsers()?;
        if !available_browsers.contains(&browser) {
            return Err(BrowserSettingsError::browser_unavailable());
        }
        let document = StoredBrowserSelection {
            browser,
            version: SETTINGS_VERSION,
        };
        let encoded = serde_json::to_vec(&document)
            .map_err(|_| BrowserSettingsError::storage_unavailable())?;
        self.store
            .lock()
            .map_err(|_| BrowserSettingsError::storage_unavailable())?
            .save(&encoded)
            .map_err(|_| BrowserSettingsError::storage_unavailable())?;
        Ok(BrowserSettingsSnapshot {
            available_browsers,
            selected_browser: Some(browser),
        })
    }

    fn snapshot_with_available(
        &self,
        available_browsers: Vec<SupportedBrowser>,
    ) -> Result<BrowserSettingsSnapshot, BrowserSettingsError> {
        let selected_browser = self.load_selection()?;
        Ok(BrowserSettingsSnapshot {
            selected_browser: selected_browser
                .filter(|browser| available_browsers.contains(browser)),
            available_browsers,
        })
    }

    fn load_selection(&self) -> Result<Option<SupportedBrowser>, BrowserSettingsError> {
        let stored = self
            .store
            .lock()
            .map_err(|_| BrowserSettingsError::storage_unavailable())?
            .load()
            .map_err(|_| BrowserSettingsError::storage_unavailable())?;
        let Some(stored) = stored else {
            return Ok(None);
        };
        let document: StoredBrowserSelection = serde_json::from_slice(&stored)
            .map_err(|_| BrowserSettingsError::storage_unavailable())?;
        if document.version != SETTINGS_VERSION
            || serde_json::to_vec(&document)
                .map_err(|_| BrowserSettingsError::storage_unavailable())?
                != stored.as_slice()
        {
            return Err(BrowserSettingsError::storage_unavailable());
        }
        Ok(Some(document.browser))
    }
}

#[cfg(target_os = "macos")]
fn discover_available_browsers() -> Result<Vec<SupportedBrowser>, BrowserSettingsError> {
    discover_macos_browsers()
        .map(|browsers| {
            browsers
                .into_iter()
                .map(|browser| browser.browser())
                .collect()
        })
        .map_err(|_| BrowserSettingsError::discovery_unavailable())
}

#[cfg(target_os = "windows")]
fn discover_available_browsers() -> Result<Vec<SupportedBrowser>, BrowserSettingsError> {
    discover_windows_browsers()
        .map(|browsers| {
            browsers
                .into_iter()
                .map(|browser| browser.browser())
                .collect()
        })
        .map_err(|_| BrowserSettingsError::discovery_unavailable())
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn discover_available_browsers() -> Result<Vec<SupportedBrowser>, BrowserSettingsError> {
    Err(BrowserSettingsError::discovery_unavailable())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(0);

    struct TemporaryAppData {
        path: std::path::PathBuf,
    }

    impl TemporaryAppData {
        fn new() -> Self {
            Self {
                path: std::env::temp_dir().join(format!(
                    "automation-tool-b5-04-unit-{}-{}-{}",
                    std::process::id(),
                    SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .expect("system time")
                        .as_nanos(),
                    NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed)
                )),
            }
        }
    }

    impl Drop for TemporaryAppData {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    #[test]
    fn canonical_selection_round_trips_without_storing_any_path() {
        let app_data = TemporaryAppData::new();
        let service = BrowserSettingsService::initialize(&app_data.path).expect("service");
        let document = StoredBrowserSelection {
            browser: SupportedBrowser::GoogleChrome,
            version: SETTINGS_VERSION,
        };
        service
            .store
            .lock()
            .expect("store lock")
            .save(&serde_json::to_vec(&document).expect("serialize"))
            .expect("save");

        assert_eq!(
            service
                .snapshot_with_available(vec![SupportedBrowser::GoogleChrome])
                .expect("snapshot")
                .selected_browser(),
            Some(SupportedBrowser::GoogleChrome)
        );
        let bytes = fs::read(
            app_data
                .path
                .join(SETTINGS_DIRECTORY)
                .join(BROWSER_SELECTION_FILE),
        )
        .expect("settings bytes");
        assert_eq!(bytes, br#"{"browser":"google_chrome","version":1}"#);
    }

    #[test]
    fn corrupt_or_noncanonical_settings_fail_closed_without_rewrite() {
        let app_data = TemporaryAppData::new();
        let service = BrowserSettingsService::initialize(&app_data.path).expect("service");
        let corrupt = br#"{"version":1,"browser":"google_chrome"}"#;
        service
            .store
            .lock()
            .expect("store lock")
            .save(corrupt)
            .expect("save corrupt fixture");

        assert!(service
            .snapshot_with_available(vec![SupportedBrowser::GoogleChrome])
            .is_err());
        assert_eq!(
            fs::read(
                app_data
                    .path
                    .join(SETTINGS_DIRECTORY)
                    .join(BROWSER_SELECTION_FILE)
            )
            .expect("unchanged corrupt bytes"),
            corrupt
        );
    }

    #[test]
    fn unavailable_stored_browser_is_not_projected_as_selected() {
        let app_data = TemporaryAppData::new();
        let service = BrowserSettingsService::initialize(&app_data.path).expect("service");
        let document = StoredBrowserSelection {
            browser: SupportedBrowser::MicrosoftEdge,
            version: SETTINGS_VERSION,
        };
        service
            .store
            .lock()
            .expect("store lock")
            .save(&serde_json::to_vec(&document).expect("serialize"))
            .expect("save");

        assert_eq!(
            service
                .snapshot_with_available(vec![SupportedBrowser::GoogleChrome])
                .expect("snapshot")
                .selected_browser(),
            None
        );
    }
}

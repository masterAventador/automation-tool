use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri_plugin_updater::UpdaterExt as _;

use crate::app_update_cache::{AppUpdateCache, DownloadSource, UpdateDownloadErrorCode};
use crate::app_update_installation::{
    AppUpdateInstallationCoordinator, OfficialUpdatePackageInstaller, UpdateInstallErrorCode,
    UpdatePackageInstaller,
};
use crate::app_update_policy::{UpdatePolicyErrorCode, UpdatePolicyService};
use crate::app_updates::{
    parse_official_update, UpdateCheckTrigger, UpdateDecision, UpdateErrorCode, UpdateErrorStage,
    UpdatePolicyAction, UpdateRelease, UpdateState,
};

pub const MIN_UPDATE_POLL_INTERVAL: Duration = Duration::from_secs(15 * 60);
pub const DEFAULT_UPDATE_POLL_INTERVAL: Duration = Duration::from_secs(6 * 60 * 60);
pub const MAX_UPDATE_POLL_INTERVAL: Duration = Duration::from_secs(24 * 60 * 60);
const UPDATE_REQUEST_TIMEOUT: Duration = Duration::from_secs(20);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum UpdateCheckErrorCode {
    ConfigurationInvalid,
    CheckUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct UpdateCheckError(UpdateCheckErrorCode);

impl UpdateCheckError {
    pub fn code(self) -> UpdateCheckErrorCode {
        self.0
    }

    fn configuration_invalid() -> Self {
        Self(UpdateCheckErrorCode::ConfigurationInvalid)
    }

    fn check_unavailable() -> Self {
        Self(UpdateCheckErrorCode::CheckUnavailable)
    }
}

impl std::fmt::Display for UpdateCheckError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("update check unavailable")
    }
}

impl std::error::Error for UpdateCheckError {}

#[derive(Clone)]
pub struct CheckedUpdate {
    release: UpdateRelease,
    source: DownloadSource,
    installer: Arc<dyn UpdatePackageInstaller>,
}

impl CheckedUpdate {
    pub fn new(
        release: UpdateRelease,
        source: DownloadSource,
        installer: Arc<dyn UpdatePackageInstaller>,
    ) -> Self {
        Self {
            release,
            source,
            installer,
        }
    }
}

impl std::fmt::Debug for CheckedUpdate {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("CheckedUpdate(<redacted>)")
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum UpdateCoordinationErrorCode {
    OperationInProgress,
    DecisionUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct UpdateCoordinationError(UpdateCoordinationErrorCode);

impl UpdateCoordinationError {
    pub fn code(self) -> UpdateCoordinationErrorCode {
        self.0
    }

    fn operation_in_progress() -> Self {
        Self(UpdateCoordinationErrorCode::OperationInProgress)
    }

    fn decision_unavailable() -> Self {
        Self(UpdateCoordinationErrorCode::DecisionUnavailable)
    }
}

impl std::fmt::Display for UpdateCoordinationError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("update decision unavailable")
    }
}

impl std::error::Error for UpdateCoordinationError {}

pub trait UpdateCheckBackend: Send + Sync {
    fn check(
        &self,
    ) -> Pin<Box<dyn Future<Output = Result<Option<CheckedUpdate>, UpdateCheckError>> + Send + '_>>;
}

pub struct AppUpdateCoordinator {
    backend: Arc<dyn UpdateCheckBackend>,
    policy: Arc<UpdatePolicyService>,
    cache: Arc<AppUpdateCache>,
    installation: Arc<AppUpdateInstallationCoordinator>,
    download_client: reqwest::Client,
    state: Mutex<UpdateState>,
    pending_update: Mutex<Option<CheckedUpdate>>,
    check_in_progress: AtomicBool,
}

impl std::fmt::Debug for AppUpdateCoordinator {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("AppUpdateCoordinator(<redacted>)")
    }
}

impl AppUpdateCoordinator {
    pub fn new<B>(
        backend: Arc<B>,
        policy: Arc<UpdatePolicyService>,
        cache: Arc<AppUpdateCache>,
        installation: Arc<AppUpdateInstallationCoordinator>,
        download_client: reqwest::Client,
    ) -> Result<Self, UpdateCheckError>
    where
        B: UpdateCheckBackend + 'static,
    {
        Ok(Self {
            backend,
            policy,
            cache,
            installation,
            download_client,
            state: Mutex::new(UpdateState::Idle),
            pending_update: Mutex::new(None),
            check_in_progress: AtomicBool::new(false),
        })
    }

    pub fn state(&self) -> Result<UpdateState, UpdateCheckError> {
        self.state
            .lock()
            .map(|state| state.clone())
            .map_err(|_| UpdateCheckError::check_unavailable())
    }

    pub async fn check(&self, trigger: UpdateCheckTrigger) -> UpdateState {
        if self
            .check_in_progress
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return self.state().unwrap_or(UpdateState::Failed {
                stage: UpdateErrorStage::Check,
                code: UpdateErrorCode::TransportUnavailable,
                retryable: true,
            });
        }
        let _guard = CheckGuard(&self.check_in_progress);
        self.set_state(UpdateState::Checking { trigger });

        let checked = match self.backend.check().await {
            Ok(checked) => checked,
            Err(error) => {
                return self.fail(
                    if error.code() == UpdateCheckErrorCode::ConfigurationInvalid {
                        UpdateErrorStage::Configuration
                    } else {
                        UpdateErrorStage::Check
                    },
                    if error.code() == UpdateCheckErrorCode::ConfigurationInvalid {
                        UpdateErrorCode::ConfigurationInvalid
                    } else {
                        UpdateErrorCode::TransportUnavailable
                    },
                    error.code() == UpdateCheckErrorCode::CheckUnavailable,
                );
            }
        };
        let Some(checked) = checked else {
            if let Ok(mut pending) = self.pending_update.lock() {
                *pending = None;
            }
            return self.set_state(UpdateState::UpToDate { trigger });
        };

        self.set_state(UpdateState::Available {
            release: checked.release.clone(),
        });
        let evaluation = match self.policy.observe_release(checked.release.clone()) {
            Ok(evaluation) => evaluation,
            Err(error) => {
                let (stage, code, retryable) = match error.code() {
                    UpdatePolicyErrorCode::StorageUnavailable => (
                        UpdateErrorStage::Storage,
                        UpdateErrorCode::StorageUnavailable,
                        false,
                    ),
                    UpdatePolicyErrorCode::ConfigurationInvalid => (
                        UpdateErrorStage::Configuration,
                        UpdateErrorCode::ConfigurationInvalid,
                        false,
                    ),
                    _ => (
                        UpdateErrorStage::Check,
                        UpdateErrorCode::ManifestRejected,
                        false,
                    ),
                };
                return self.fail(stage, code, retryable);
            }
        };
        let action = evaluation.action();
        let cached_before = match self.cache.cached() {
            Ok(cached) => cached
                .as_ref()
                .is_some_and(|cached| cached.matches_release(&checked.release)),
            Err(_) => {
                return self.fail(
                    UpdateErrorStage::Storage,
                    UpdateErrorCode::StorageUnavailable,
                    false,
                );
            }
        };
        if let Ok(mut pending) = self.pending_update.lock() {
            *pending = Some(checked.clone());
        } else {
            return self.fail(
                UpdateErrorStage::Storage,
                UpdateErrorCode::StorageUnavailable,
                false,
            );
        }
        if trigger == UpdateCheckTrigger::Startup
            && cached_before
            && matches!(
                action,
                UpdatePolicyAction::Forced | UpdatePolicyAction::InstallRequested
            )
        {
            return self.install_checked(&checked);
        }
        if action == UpdatePolicyAction::Suppressed {
            return self.set_state(UpdateState::Ready {
                release: checked.release,
                action,
            });
        }

        let release_for_progress = checked.release.clone();
        let result = self
            .cache
            .download(
                &self.download_client,
                &checked.release,
                &checked.source,
                |downloaded_bytes, total_bytes| {
                    self.set_state(UpdateState::Downloading {
                        release: release_for_progress.clone(),
                        downloaded_bytes,
                        total_bytes: Some(total_bytes),
                    });
                },
            )
            .await;
        match result {
            Ok(_) => self.set_state(UpdateState::Ready {
                release: checked.release,
                action,
            }),
            Err(error) => {
                let (stage, code, retryable) = match error.code() {
                    UpdateDownloadErrorCode::TransportUnavailable => (
                        UpdateErrorStage::Download,
                        UpdateErrorCode::TransportUnavailable,
                        true,
                    ),
                    UpdateDownloadErrorCode::ManifestRejected => (
                        UpdateErrorStage::Download,
                        UpdateErrorCode::ManifestRejected,
                        false,
                    ),
                    UpdateDownloadErrorCode::SignatureRejected => (
                        UpdateErrorStage::Download,
                        UpdateErrorCode::SignatureRejected,
                        false,
                    ),
                    UpdateDownloadErrorCode::StorageUnavailable => (
                        UpdateErrorStage::Storage,
                        UpdateErrorCode::StorageUnavailable,
                        false,
                    ),
                };
                self.fail(stage, code, retryable)
            }
        }
    }

    pub fn decide(&self, decision: UpdateDecision) -> Result<UpdateState, UpdateCoordinationError> {
        if self
            .check_in_progress
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err(UpdateCoordinationError::operation_in_progress());
        }
        let _guard = CheckGuard(&self.check_in_progress);
        let current = self
            .state()
            .map_err(|_| UpdateCoordinationError::decision_unavailable())?;
        if !matches!(
            current,
            UpdateState::Ready {
                action: UpdatePolicyAction::Prompt,
                ..
            }
        ) {
            return Err(UpdateCoordinationError::decision_unavailable());
        }
        let checked = self
            .pending_update
            .lock()
            .map_err(|_| UpdateCoordinationError::decision_unavailable())?
            .clone()
            .ok_or_else(UpdateCoordinationError::decision_unavailable)?;
        let evaluation = self
            .policy
            .decide(decision)
            .map_err(|_| UpdateCoordinationError::decision_unavailable())?;
        if evaluation.release() != &checked.release {
            return Err(UpdateCoordinationError::decision_unavailable());
        }
        if evaluation.action() == UpdatePolicyAction::InstallRequested {
            return Ok(self.install_checked(&checked));
        }
        Ok(self.set_state(UpdateState::Ready {
            release: checked.release,
            action: evaluation.action(),
        }))
    }

    fn install_checked(&self, checked: &CheckedUpdate) -> UpdateState {
        self.set_state(UpdateState::Installing {
            release: checked.release.clone(),
        });
        match self.installation.install(
            &checked.release,
            &checked.source,
            Arc::clone(&checked.installer),
        ) {
            Ok(()) => self.set_state(UpdateState::InstallationLaunched {
                release: checked.release.clone(),
            }),
            Err(error) => self.fail(
                UpdateErrorStage::Install,
                match error.code() {
                    UpdateInstallErrorCode::PackageUnavailable => {
                        UpdateErrorCode::StorageUnavailable
                    }
                    UpdateInstallErrorCode::RuntimeShutdownFailed
                    | UpdateInstallErrorCode::InstallerFailed
                    | UpdateInstallErrorCode::InstallationInProgress => {
                        UpdateErrorCode::InstallationFailed
                    }
                },
                false,
            ),
        }
    }

    pub fn start_background(self: &Arc<Self>) {
        let startup = Arc::clone(self);
        tauri::async_runtime::spawn(async move {
            startup.check(UpdateCheckTrigger::Startup).await;
        });
        let periodic = Arc::clone(self);
        tauri::async_runtime::spawn(async move {
            loop {
                tokio::time::sleep(DEFAULT_UPDATE_POLL_INTERVAL).await;
                periodic.check(UpdateCheckTrigger::Periodic).await;
            }
        });
    }

    fn set_state(&self, next: UpdateState) -> UpdateState {
        match self.state.lock() {
            Ok(mut state) => {
                *state = next.clone();
                next
            }
            Err(_) => UpdateState::Failed {
                stage: UpdateErrorStage::Storage,
                code: UpdateErrorCode::StorageUnavailable,
                retryable: false,
            },
        }
    }

    fn fail(&self, stage: UpdateErrorStage, code: UpdateErrorCode, retryable: bool) -> UpdateState {
        self.set_state(UpdateState::Failed {
            stage,
            code,
            retryable,
        })
    }
}

struct CheckGuard<'a>(&'a AtomicBool);

impl Drop for CheckGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

pub struct OfficialUpdateCheckBackend {
    app: tauri::AppHandle<tauri::Wry>,
    endpoint: reqwest::Url,
    public_key: String,
    accept_invalid_tls: bool,
    #[cfg(all(
        debug_assertions,
        feature = "desktop-e2e",
        not(feature = "control-plane-e2e")
    ))]
    install_probe: bool,
}

impl OfficialUpdateCheckBackend {
    pub fn new(
        app: tauri::AppHandle<tauri::Wry>,
        endpoint: reqwest::Url,
        public_key: String,
        accept_invalid_tls: bool,
        install_probe: bool,
    ) -> Self {
        #[cfg(not(all(
            debug_assertions,
            feature = "desktop-e2e",
            not(feature = "control-plane-e2e")
        )))]
        let _ = install_probe;
        Self {
            app,
            endpoint,
            public_key,
            accept_invalid_tls,
            #[cfg(all(
                debug_assertions,
                feature = "desktop-e2e",
                not(feature = "control-plane-e2e")
            ))]
            install_probe,
        }
    }
}

impl UpdateCheckBackend for OfficialUpdateCheckBackend {
    fn check(
        &self,
    ) -> Pin<Box<dyn Future<Output = Result<Option<CheckedUpdate>, UpdateCheckError>> + Send + '_>>
    {
        Box::pin(async move {
            let builder = self
                .app
                .updater_builder()
                .endpoints(vec![self.endpoint.clone()])
                .map_err(|_| UpdateCheckError::configuration_invalid())?
                .pubkey(self.public_key.clone())
                .timeout(UPDATE_REQUEST_TIMEOUT)
                .no_proxy();
            let builder = if self.accept_invalid_tls {
                builder.configure_client(|client| client.danger_accept_invalid_certs(true))
            } else {
                builder
            };
            let update = builder
                .build()
                .map_err(|_| UpdateCheckError::configuration_invalid())?
                .check()
                .await
                .map_err(|_| UpdateCheckError::check_unavailable())?;
            let Some(update) = update else {
                return Ok(None);
            };
            let release = parse_official_update(&update)
                .map_err(|_| UpdateCheckError::configuration_invalid())?;
            let source = DownloadSource::new(update.download_url.clone(), update.signature.clone())
                .map_err(|_| UpdateCheckError::configuration_invalid())?;
            #[cfg(all(
                debug_assertions,
                feature = "desktop-e2e",
                not(feature = "control-plane-e2e")
            ))]
            let installer: Arc<dyn UpdatePackageInstaller> = if self.install_probe {
                Arc::new(UpdateInstallProbe)
            } else {
                Arc::new(OfficialUpdatePackageInstaller::new(update))
            };
            #[cfg(not(all(
                debug_assertions,
                feature = "desktop-e2e",
                not(feature = "control-plane-e2e")
            )))]
            let installer: Arc<dyn UpdatePackageInstaller> =
                Arc::new(OfficialUpdatePackageInstaller::new(update));
            Ok(Some(CheckedUpdate::new(release, source, installer)))
        })
    }
}

#[derive(Debug)]
#[cfg(all(
    debug_assertions,
    feature = "desktop-e2e",
    not(feature = "control-plane-e2e")
))]
struct UpdateInstallProbe;

#[cfg(all(
    debug_assertions,
    feature = "desktop-e2e",
    not(feature = "control-plane-e2e")
))]
impl UpdatePackageInstaller for UpdateInstallProbe {
    fn install(
        &self,
        bytes: Vec<u8>,
    ) -> Result<(), crate::app_update_installation::UpdateInstallError> {
        if bytes.is_empty() {
            return Err(crate::app_update_installation::UpdateInstallError::new(
                UpdateInstallErrorCode::InstallerFailed,
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub struct UpdateRuntimeConfiguration {
    endpoint: reqwest::Url,
    public_key: String,
    accept_invalid_tls: bool,
    #[cfg(all(
        debug_assertions,
        feature = "desktop-e2e",
        not(feature = "control-plane-e2e")
    ))]
    install_probe: bool,
}

fn is_reserved_update_host(host: &str) -> bool {
    let host = host.trim_end_matches('.');
    host.is_empty()
        || host == "invalid"
        || host == "test"
        || host == "example"
        || host == "localhost"
        || host == "example.com"
        || host == "example.net"
        || host == "example.org"
        || host.ends_with(".invalid")
        || host.ends_with(".test")
        || host.ends_with(".example")
        || host.ends_with(".localhost")
        || host.ends_with(".example.com")
        || host.ends_with(".example.net")
        || host.ends_with(".example.org")
}

#[cfg(test)]
mod update_runtime_configuration_tests {
    use super::is_reserved_update_host;

    #[test]
    fn reserved_placeholder_update_hosts_are_rejected() {
        for host in [
            "invalid",
            "updates.candidate.invalid",
            "test",
            "updates.acceptance.test",
            "example",
            "updates.preview.example",
            "localhost",
            "updates.localhost",
            "example.com",
            "updates.example.com",
            "example.net",
            "updates.example.net",
            "example.org",
            "updates.example.org",
            "updates.candidate.invalid.",
            "example.com.",
        ] {
            assert!(is_reserved_update_host(host), "{host}");
        }
        for host in ["updates.company.com", "updates.company.cn", "127.0.0.1"] {
            assert!(!is_reserved_update_host(host), "{host}");
        }
    }
}

impl UpdateRuntimeConfiguration {
    pub fn load() -> Result<Option<Self>, UpdateCheckError> {
        #[cfg(debug_assertions)]
        let values = (
            std::env::var("AUTOMATION_TOOL_UPDATE_ENDPOINT").ok(),
            std::env::var("AUTOMATION_TOOL_UPDATE_PUBLIC_KEY").ok(),
        );
        #[cfg(not(debug_assertions))]
        let values = (
            option_env!("AUTOMATION_TOOL_UPDATE_ENDPOINT").map(ToOwned::to_owned),
            option_env!("AUTOMATION_TOOL_UPDATE_PUBLIC_KEY").map(ToOwned::to_owned),
        );
        let (Some(endpoint), Some(public_key)) = values else {
            if values.0.is_some() || values.1.is_some() {
                return Err(UpdateCheckError::configuration_invalid());
            }
            return Ok(None);
        };
        if !endpoint.contains("{{target}}")
            || !endpoint.contains("{{arch}}")
            || !endpoint.contains("{{current_version}}")
            || public_key.is_empty()
            || public_key.len() > 8192
        {
            return Err(UpdateCheckError::configuration_invalid());
        }
        let endpoint = reqwest::Url::parse(&endpoint)
            .map_err(|_| UpdateCheckError::configuration_invalid())?;
        let host = endpoint.host_str().unwrap_or_default();
        if endpoint.scheme() != "https"
            || host.is_empty()
            || is_reserved_update_host(host)
            || !endpoint.username().is_empty()
            || endpoint.password().is_some()
            || endpoint.fragment().is_some()
        {
            return Err(UpdateCheckError::configuration_invalid());
        }
        #[cfg(all(
            debug_assertions,
            feature = "desktop-e2e",
            not(feature = "control-plane-e2e")
        ))]
        let accept_invalid_tls =
            std::env::var("AUTOMATION_TOOL_UPDATE_ACCEPT_INVALID_TLS").as_deref() == Ok("1");
        #[cfg(not(all(
            debug_assertions,
            feature = "desktop-e2e",
            not(feature = "control-plane-e2e")
        )))]
        let accept_invalid_tls = false;
        #[cfg(all(
            debug_assertions,
            feature = "desktop-e2e",
            not(feature = "control-plane-e2e")
        ))]
        let install_probe =
            std::env::var("AUTOMATION_TOOL_UPDATE_INSTALL_PROBE").as_deref() == Ok("1");
        Ok(Some(Self {
            endpoint,
            public_key,
            accept_invalid_tls,
            #[cfg(all(
                debug_assertions,
                feature = "desktop-e2e",
                not(feature = "control-plane-e2e")
            ))]
            install_probe,
        }))
    }

    pub fn endpoint(&self) -> &reqwest::Url {
        &self.endpoint
    }

    pub fn public_key(&self) -> &str {
        &self.public_key
    }

    pub fn accept_invalid_tls(&self) -> bool {
        self.accept_invalid_tls
    }

    pub fn install_probe(&self) -> bool {
        #[cfg(all(
            debug_assertions,
            feature = "desktop-e2e",
            not(feature = "control-plane-e2e")
        ))]
        {
            self.install_probe
        }
        #[cfg(not(all(
            debug_assertions,
            feature = "desktop-e2e",
            not(feature = "control-plane-e2e")
        )))]
        {
            false
        }
    }

    pub fn download_client(&self) -> Result<reqwest::Client, UpdateCheckError> {
        let mut builder = reqwest::Client::builder()
            .no_proxy()
            .timeout(UPDATE_REQUEST_TIMEOUT);
        if self.accept_invalid_tls {
            builder = builder.danger_accept_invalid_certs(true);
        }
        builder
            .build()
            .map_err(|_| UpdateCheckError::configuration_invalid())
    }
}

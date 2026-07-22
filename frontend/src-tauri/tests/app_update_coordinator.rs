use std::future::Future;
use std::path::PathBuf;
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::app_update_cache::AppUpdateCache;
use automation_tool_desktop_lib::app_update_coordinator::{
    AppUpdateCoordinator, CheckedUpdate, UpdateCheckBackend, UpdateCheckError,
    DEFAULT_UPDATE_POLL_INTERVAL, MAX_UPDATE_POLL_INTERVAL, MIN_UPDATE_POLL_INTERVAL,
};
use automation_tool_desktop_lib::app_update_policy::UpdatePolicyService;
use automation_tool_desktop_lib::app_updates::{UpdateCheckTrigger, UpdateState};
use base64::engine::general_purpose::STANDARD;
use base64::Engine as _;

static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(0);
const PUBLIC_KEY_TEXT: &str = "untrusted comment: minisign public key E7620F1842B4E81F\n\
RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3";

struct TemporaryAppData(PathBuf);

impl TemporaryAppData {
    fn new() -> Self {
        Self(std::env::temp_dir().join(format!(
            "automation-tool-h8-20-coordinator-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed),
        )))
    }
}

impl Drop for TemporaryAppData {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

struct NoUpdateBackend {
    calls: AtomicUsize,
    delay: Duration,
}

impl NoUpdateBackend {
    fn new(delay: Duration) -> Self {
        Self {
            calls: AtomicUsize::new(0),
            delay,
        }
    }
}

impl UpdateCheckBackend for NoUpdateBackend {
    fn check(
        &self,
    ) -> Pin<Box<dyn Future<Output = Result<Option<CheckedUpdate>, UpdateCheckError>> + Send + '_>>
    {
        Box::pin(async move {
            self.calls.fetch_add(1, Ordering::SeqCst);
            if !self.delay.is_zero() {
                tokio::time::sleep(self.delay).await;
            }
            Ok(None)
        })
    }
}

fn coordinator(backend: Arc<NoUpdateBackend>) -> (Arc<AppUpdateCoordinator>, TemporaryAppData) {
    let app_data = TemporaryAppData::new();
    let policy =
        Arc::new(UpdatePolicyService::initialize(&app_data.0, "0.1.0", "stable").expect("policy"));
    let cache = Arc::new(
        AppUpdateCache::initialize(&app_data.0, &STANDARD.encode(PUBLIC_KEY_TEXT)).expect("cache"),
    );
    let coordinator = Arc::new(
        AppUpdateCoordinator::new(backend, policy, cache, reqwest::Client::new())
            .expect("coordinator"),
    );
    (coordinator, app_data)
}

#[test]
fn startup_periodic_and_manual_checks_use_one_stateful_entrypoint() {
    let backend = Arc::new(NoUpdateBackend::new(Duration::ZERO));
    let (coordinator, _app_data) = coordinator(Arc::clone(&backend));

    tauri::async_runtime::block_on(async {
        for trigger in [
            UpdateCheckTrigger::Startup,
            UpdateCheckTrigger::Periodic,
            UpdateCheckTrigger::Manual,
        ] {
            assert_eq!(
                coordinator.check(trigger).await,
                UpdateState::UpToDate { trigger }
            );
        }
    });

    assert_eq!(backend.calls.load(Ordering::SeqCst), 3);
    assert_eq!(
        coordinator.state().expect("state"),
        UpdateState::UpToDate {
            trigger: UpdateCheckTrigger::Manual
        }
    );
}

#[test]
fn overlapping_triggers_are_coalesced_instead_of_starting_duplicate_network_checks() {
    let backend = Arc::new(NoUpdateBackend::new(Duration::from_millis(100)));
    let (coordinator, _app_data) = coordinator(Arc::clone(&backend));

    tauri::async_runtime::block_on(async {
        let startup = {
            let coordinator = Arc::clone(&coordinator);
            tauri::async_runtime::spawn(async move {
                coordinator.check(UpdateCheckTrigger::Startup).await
            })
        };
        tokio::time::sleep(Duration::from_millis(20)).await;
        assert_eq!(
            coordinator.check(UpdateCheckTrigger::Manual).await,
            UpdateState::Checking {
                trigger: UpdateCheckTrigger::Startup
            }
        );
        assert_eq!(
            startup.await.expect("startup task"),
            UpdateState::UpToDate {
                trigger: UpdateCheckTrigger::Startup
            }
        );
    });

    assert_eq!(backend.calls.load(Ordering::SeqCst), 1);
}

#[test]
fn default_period_is_bounded_and_not_a_busy_poll() {
    assert!(DEFAULT_UPDATE_POLL_INTERVAL >= MIN_UPDATE_POLL_INTERVAL);
    assert!(DEFAULT_UPDATE_POLL_INTERVAL <= MAX_UPDATE_POLL_INTERVAL);
    assert_eq!(
        DEFAULT_UPDATE_POLL_INTERVAL,
        Duration::from_secs(6 * 60 * 60)
    );
}

use std::future::Future;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::app_update_cache::{AppUpdateCache, DownloadSource};
use automation_tool_desktop_lib::app_update_coordinator::{
    AppUpdateCoordinator, CheckedUpdate, UpdateCheckBackend, UpdateCheckError,
    DEFAULT_UPDATE_POLL_INTERVAL, MAX_UPDATE_POLL_INTERVAL, MIN_UPDATE_POLL_INTERVAL,
};
use automation_tool_desktop_lib::app_update_installation::{
    AppUpdateInstallationCoordinator, UpdateInstallError, UpdateInstallLifecycle,
    UpdatePackageInstaller,
};
use automation_tool_desktop_lib::app_update_policy::UpdatePolicyService;
use automation_tool_desktop_lib::app_updates::{
    parse_update_release, UpdateCheckTrigger, UpdateDecision, UpdatePolicyAction, UpdateRelease,
    UpdateState,
};
use base64::engine::general_purpose::STANDARD;
use base64::Engine as _;
use serde_json::json;
use sha2::{Digest, Sha256};

static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(0);
const PUBLIC_KEY_TEXT: &str = "untrusted comment: minisign public key E7620F1842B4E81F\n\
RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3";
const SIGNATURE_TEXT: &str = "untrusted comment: signature from minisign secret key\n\
RUQf6LRCGA9i559r3g7V1qNyJDApGip8MfqcadIgT9CuhV3EMhHoN1mGTkUidF/z7SrlQgXdy8ofjb7bNJJylDOocrCo8KLzZwo=\n\
trusted comment: timestamp:1556193335\tfile:test\n\
y/rUw2y8/hOUYjZU71eHp/Wo1KZ40fGy2VJEDl34XMJM+TX48Ss/17u3IvIfbVR1FkZZSNCisQbuQY+bHwhEBg==";
const PAYLOAD: &[u8] = b"test";

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

#[derive(Clone)]
struct RecordingInstaller(Arc<AtomicUsize>);

impl UpdatePackageInstaller for RecordingInstaller {
    fn install(&self, bytes: Vec<u8>) -> Result<(), UpdateInstallError> {
        assert_eq!(bytes, PAYLOAD);
        self.0.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

struct FixedUpdateBackend {
    checked: CheckedUpdate,
}

impl UpdateCheckBackend for FixedUpdateBackend {
    fn check(
        &self,
    ) -> Pin<Box<dyn Future<Output = Result<Option<CheckedUpdate>, UpdateCheckError>> + Send + '_>>
    {
        Box::pin(async move { Ok(Some(self.checked.clone())) })
    }
}

fn release(policy: &str) -> UpdateRelease {
    let digest = Sha256::digest(PAYLOAD)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    parse_update_release(
        "0.2.0",
        &json!({
            "version": "0.2.0",
            "url": "https://updates.example.test/app.tar.gz",
            "signature": STANDARD.encode(SIGNATURE_TEXT),
            "update_contract": {
                "version": 1,
                "channel": "stable",
                "policy": policy,
                "artifact": {
                    "target": "darwin",
                    "arch": "aarch64",
                    "sha256": digest,
                    "size_bytes": PAYLOAD.len()
                }
            }
        }),
    )
    .expect("release")
}

fn one_shot_source() -> (DownloadSource, thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("isolated artifact port");
    let address = listener.local_addr().expect("artifact address");
    let worker = thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("artifact request");
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .expect("request timeout");
        let mut request = Vec::new();
        let mut byte = [0_u8; 1];
        while !request.ends_with(b"\r\n\r\n") {
            let count = stream.read(&mut byte).expect("request");
            if count == 0 {
                break;
            }
            request.push(byte[0]);
        }
        write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            PAYLOAD.len()
        )
        .expect("headers");
        stream.write_all(PAYLOAD).expect("payload");
        stream.flush().expect("flush");
    });
    (
        DownloadSource::new(
            format!("http://{address}/artifact")
                .parse()
                .expect("artifact URL"),
            STANDARD.encode(SIGNATURE_TEXT),
        )
        .expect("source"),
        worker,
    )
}

fn update_coordinator(
    app_data: &TemporaryAppData,
    checked: CheckedUpdate,
) -> Arc<AppUpdateCoordinator> {
    let policy =
        Arc::new(UpdatePolicyService::initialize(&app_data.0, "0.1.0", "stable").expect("policy"));
    let cache = Arc::new(
        AppUpdateCache::initialize(&app_data.0, &STANDARD.encode(PUBLIC_KEY_TEXT)).expect("cache"),
    );
    let installation = Arc::new(AppUpdateInstallationCoordinator::new(
        cache.clone(),
        Arc::new(NoopLifecycle),
    ));
    Arc::new(
        AppUpdateCoordinator::new(
            Arc::new(FixedUpdateBackend { checked }),
            policy,
            cache,
            installation,
            reqwest::Client::builder()
                .no_proxy()
                .timeout(Duration::from_secs(2))
                .build()
                .expect("client"),
        )
        .expect("coordinator"),
    )
}

struct NoopLifecycle;

impl UpdateInstallLifecycle for NoopLifecycle {
    fn prepare_for_install(&self) -> Result<(), UpdateInstallError> {
        Ok(())
    }

    fn complete_install(&self) -> Result<(), UpdateInstallError> {
        Ok(())
    }

    fn recover_after_failure(&self) {}
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
    let installation = Arc::new(AppUpdateInstallationCoordinator::new(
        cache.clone(),
        Arc::new(NoopLifecycle),
    ));
    let coordinator = Arc::new(
        AppUpdateCoordinator::new(backend, policy, cache, installation, reqwest::Client::new())
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

#[test]
fn optional_defer_reprompts_and_install_now_uses_the_verified_cached_package() {
    let app_data = TemporaryAppData::new();
    let installs = Arc::new(AtomicUsize::new(0));
    let (source, server) = one_shot_source();
    let checked = CheckedUpdate::new(
        release("optional"),
        source,
        Arc::new(RecordingInstaller(Arc::clone(&installs))),
    );
    let coordinator = update_coordinator(&app_data, checked);

    tauri::async_runtime::block_on(async {
        assert!(matches!(
            coordinator.check(UpdateCheckTrigger::Startup).await,
            UpdateState::Ready {
                action: UpdatePolicyAction::Prompt,
                ..
            }
        ));
        assert!(matches!(
            coordinator
                .decide(UpdateDecision::Defer)
                .expect("defer current prompt"),
            UpdateState::Ready {
                action: UpdatePolicyAction::Deferred,
                ..
            }
        ));
        assert!(matches!(
            coordinator.check(UpdateCheckTrigger::Periodic).await,
            UpdateState::Ready {
                action: UpdatePolicyAction::Prompt,
                ..
            }
        ));
        assert!(matches!(
            coordinator
                .decide(UpdateDecision::InstallNow)
                .expect("install current prompt"),
            UpdateState::InstallationLaunched { .. }
        ));
    });

    server.join().expect("artifact server");
    assert_eq!(installs.load(Ordering::SeqCst), 1);
}

#[test]
fn skipped_optional_version_stays_suppressed_without_reinstalling() {
    let app_data = TemporaryAppData::new();
    let installs = Arc::new(AtomicUsize::new(0));
    let (source, server) = one_shot_source();
    let checked = CheckedUpdate::new(
        release("optional"),
        source,
        Arc::new(RecordingInstaller(Arc::clone(&installs))),
    );
    let coordinator = update_coordinator(&app_data, checked);

    tauri::async_runtime::block_on(async {
        coordinator.check(UpdateCheckTrigger::Startup).await;
        assert!(matches!(
            coordinator
                .decide(UpdateDecision::SkipVersion)
                .expect("skip current prompt"),
            UpdateState::Ready {
                action: UpdatePolicyAction::Skipped,
                ..
            }
        ));
        assert!(matches!(
            coordinator.check(UpdateCheckTrigger::Manual).await,
            UpdateState::Ready {
                action: UpdatePolicyAction::Suppressed,
                ..
            }
        ));
        assert!(coordinator.decide(UpdateDecision::InstallNow).is_err());
    });

    server.join().expect("artifact server");
    assert_eq!(installs.load(Ordering::SeqCst), 0);
}

#[test]
fn a_fresh_forced_download_waits_but_the_next_startup_installs_without_redownloading() {
    let app_data = TemporaryAppData::new();
    let installs = Arc::new(AtomicUsize::new(0));
    let (source, server) = one_shot_source();
    let checked = CheckedUpdate::new(
        release("forced"),
        source,
        Arc::new(RecordingInstaller(Arc::clone(&installs))),
    );

    let first = update_coordinator(&app_data, checked.clone());
    let first_state = tauri::async_runtime::block_on(first.check(UpdateCheckTrigger::Startup));
    assert!(matches!(
        first_state,
        UpdateState::Ready {
            action: UpdatePolicyAction::Forced,
            ..
        }
    ));
    assert_eq!(installs.load(Ordering::SeqCst), 0);
    server.join().expect("single artifact request");
    drop(first);

    let reopened = update_coordinator(&app_data, checked);
    let reopened_state =
        tauri::async_runtime::block_on(reopened.check(UpdateCheckTrigger::Startup));
    assert!(matches!(
        reopened_state,
        UpdateState::InstallationLaunched { .. }
    ));
    assert_eq!(installs.load(Ordering::SeqCst), 1);
}

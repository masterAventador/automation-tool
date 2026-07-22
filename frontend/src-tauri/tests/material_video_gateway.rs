use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::local_video_orchestrator::{
    LocalVideoOrchestrator, VideoWorkerKind, VideoWorkerLaunch, VideoWorkerRestartPolicy,
    VideoWorkerState,
};
use automation_tool_desktop_lib::model_service_settings::{
    ConfigureModelServiceRequest, ModelServiceSettings,
};
use automation_tool_desktop_lib::secure_store::{SecretStore, SecureStoreError};
use uuid::Uuid;
use zeroize::Zeroizing;

static DIRECTORY_SEQUENCE: AtomicU64 = AtomicU64::new(0);

struct TemporaryAssetRoot(PathBuf);

#[derive(Clone, Default)]
struct MemoryStore(Arc<Mutex<Option<Vec<u8>>>>);

impl SecretStore for MemoryStore {
    fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, SecureStoreError> {
        Ok(self
            .0
            .lock()
            .expect("model store")
            .clone()
            .map(Zeroizing::new))
    }

    fn save(&self, secret: &[u8]) -> Result<(), SecureStoreError> {
        *self.0.lock().expect("model store") = Some(secret.to_vec());
        Ok(())
    }

    fn delete(&self) -> Result<(), SecureStoreError> {
        *self.0.lock().expect("model store") = None;
        Ok(())
    }
}

impl TemporaryAssetRoot {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-im03-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            DIRECTORY_SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        fs::create_dir_all(path.join("inputs")).expect("asset root");
        fs::write(path.join("inputs/clip.mp4"), b"real-process-fixture").expect("asset fixture");
        Self(fs::canonicalize(path).expect("canonical asset root"))
    }
}

impl Drop for TemporaryAssetRoot {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn frozen_worker_uses_the_authenticated_loopback_gateway_end_to_end() {
    let Some(executable) = std::env::var_os("AUTOMATION_TOOL_IM03_WORKER") else {
        eprintln!("real frozen worker is exercised by scripts/run_im_03_acceptance.py");
        return;
    };
    let executable = PathBuf::from(executable);
    assert!(executable.is_absolute());
    assert!(executable.is_file());
    let assets = TemporaryAssetRoot::new();
    let policy = VideoWorkerRestartPolicy::new(0, Duration::ZERO).expect("restart policy");
    let launch = VideoWorkerLaunch::new(
        VideoWorkerKind::Python,
        executable,
        assets.0.clone(),
        "1.3.2".to_owned(),
        policy,
    )
    .expect("secure launch configuration");
    let orchestrator =
        LocalVideoOrchestrator::new(Duration::from_secs(30), Duration::from_secs(10))
            .expect("orchestrator");

    let running = orchestrator.start(launch).expect("start frozen worker");
    assert_eq!(running.state(), VideoWorkerState::Running);
    assert_eq!(running.worker_version(), Some("1.3.2"));
    assert_eq!(running.host(), Some("127.0.0.1"));
    assert!(running.port().is_some_and(|port| port > 0));
    orchestrator
        .health(VideoWorkerKind::Python)
        .expect("authenticated health request");
    orchestrator
        .cancel(
            VideoWorkerKind::Python,
            Uuid::parse_str("123e4567-e89b-42d3-a456-426614174321").expect("job id"),
        )
        .expect("authenticated cancellation");
    orchestrator
        .stop(VideoWorkerKind::Python)
        .expect("stop worker and descendants");
    assert_eq!(
        orchestrator
            .status(VideoWorkerKind::Python)
            .expect("stopped status")
            .state(),
        VideoWorkerState::Stopped
    );
}

#[test]
fn app_script_settings_configure_the_real_frozen_worker_without_public_secrets() {
    let Some(executable) = std::env::var_os("AUTOMATION_TOOL_IM03_WORKER") else {
        eprintln!("real frozen worker is exercised by scripts/run_im_04_acceptance.py");
        return;
    };
    let api_key = "sk-im04-private-frozen-key-1234567890";
    let settings = ModelServiceSettings::new(
        MemoryStore::default(),
        MemoryStore::default(),
        reqwest::Client::new(),
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    .expect("model settings");
    let request: ConfigureModelServiceRequest = serde_json::from_value(serde_json::json!({
        "purpose": "script",
        "modelId": "qwen3.7-max-2026-06-08",
        "apiKey": api_key,
    }))
    .expect("configuration request");
    settings.configure(&request).expect("save script settings");
    let script_model = settings
        .material_video_script_model()
        .expect("native-only Worker projection");
    assert_eq!(script_model.model_id(), "qwen3.7-max-2026-06-08");
    assert!(!format!("{script_model:?}").contains(api_key));

    let executable = PathBuf::from(executable);
    let assets = TemporaryAssetRoot::new();
    let launch = VideoWorkerLaunch::new(
        VideoWorkerKind::Python,
        executable,
        assets.0.clone(),
        "1.3.2".to_owned(),
        VideoWorkerRestartPolicy::new(0, Duration::ZERO).expect("restart policy"),
    )
    .expect("secure launch configuration")
    .with_script_model(script_model);
    let orchestrator =
        LocalVideoOrchestrator::new(Duration::from_secs(30), Duration::from_secs(10))
            .expect("orchestrator");
    let running = orchestrator
        .start(launch)
        .expect("start configured frozen worker");
    assert_eq!(running.script_model_id(), Some("qwen3.7-max-2026-06-08"));
    let public = serde_json::to_string(&running).expect("public status");
    assert!(!public.contains(api_key));
    assert!(!public.contains("dashscope.aliyuncs.com"));
    assert!(!public.contains("127.0.0.1:"));
    orchestrator
        .health(VideoWorkerKind::Python)
        .expect("configured worker health");
    orchestrator
        .stop(VideoWorkerKind::Python)
        .expect("configured worker cleanup");
}

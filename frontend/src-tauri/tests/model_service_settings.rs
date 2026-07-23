use automation_tool_desktop_lib::model_service_settings::{
    BailianModelId, ConfigureModelServiceRequest, ModelServiceErrorCode, ModelServicePurpose,
    ModelServiceSettings,
};
use automation_tool_desktop_lib::secure_store::{SecretStore, SecureStoreError};
use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::{Arc, Mutex};
use std::thread;
use zeroize::Zeroizing;

const FIRST_KEY: &str = "sk-vf05-first-private-key-1234567890";
const SECOND_KEY: &str = "sk-vf05-second-private-key-0987654321";

#[derive(Clone, Default)]
struct MemoryStore {
    value: Arc<Mutex<Option<Vec<u8>>>>,
    fail: bool,
}

impl MemoryStore {
    fn failing() -> Self {
        Self {
            fail: true,
            ..Self::default()
        }
    }

    fn raw(&self) -> Option<Vec<u8>> {
        self.value.lock().unwrap().clone()
    }

    fn replace_raw(&self, value: &[u8]) {
        *self.value.lock().unwrap() = Some(value.to_vec());
    }
}

impl SecretStore for MemoryStore {
    fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, SecureStoreError> {
        if self.fail {
            return Err(SecureStoreError::Unavailable);
        }
        Ok(self.value.lock().unwrap().clone().map(Zeroizing::new))
    }

    fn save(&self, secret: &[u8]) -> Result<(), SecureStoreError> {
        if self.fail {
            return Err(SecureStoreError::Unavailable);
        }
        *self.value.lock().unwrap() = Some(secret.to_vec());
        Ok(())
    }

    fn delete(&self) -> Result<(), SecureStoreError> {
        if self.fail {
            return Err(SecureStoreError::Unavailable);
        }
        *self.value.lock().unwrap() = None;
        Ok(())
    }
}

fn request(purpose: &str, model_id: &str, api_key: &str) -> ConfigureModelServiceRequest {
    serde_json::from_value(serde_json::json!({
        "purpose": purpose,
        "modelId": model_id,
        "apiKey": api_key,
    }))
    .unwrap()
}

fn service(
    script: MemoryStore,
    video: MemoryStore,
    base_url: String,
) -> ModelServiceSettings<MemoryStore> {
    ModelServiceSettings::new(script, video, reqwest::Client::new(), base_url).unwrap()
}

fn one_response_server(
    status: &str,
    headers: &[(&str, &str)],
    body: &str,
) -> (String, thread::JoinHandle<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let status = status.to_owned();
    let headers = headers
        .iter()
        .map(|(name, value)| (name.to_string(), value.to_string()))
        .collect::<Vec<_>>();
    let body = body.to_owned();
    let handle = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(std::time::Duration::from_secs(2)))
            .unwrap();
        let mut request = vec![0_u8; 16 * 1024];
        let read = stream.read(&mut request).unwrap();
        let captured = String::from_utf8_lossy(&request[..read]).to_string();
        let mut response = format!(
            "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n",
            body.len()
        );
        for (name, value) in headers {
            response.push_str(&format!("{name}: {value}\r\n"));
        }
        response.push_str("\r\n");
        response.push_str(&body);
        stream.write_all(response.as_bytes()).unwrap();
        captured
    });
    (format!("http://{address}/v1"), handle)
}

#[test]
fn snapshots_and_debug_never_expose_credentials() {
    let script = MemoryStore::default();
    let video = MemoryStore::default();
    let settings = service(
        script.clone(),
        video,
        "https://dashscope.aliyuncs.com/compatible-mode/v1".to_owned(),
    );
    let snapshot = settings
        .configure(&request("script", "deepseek-v4-pro", FIRST_KEY))
        .unwrap();
    let public = serde_json::to_string(&snapshot).unwrap();
    assert!(public.contains("\"configured\":true"));
    assert!(public.contains("deepseek-v4-pro"));
    assert!(!public.contains(FIRST_KEY));
    assert!(String::from_utf8(script.raw().unwrap())
        .unwrap()
        .contains(FIRST_KEY));

    let credential = settings
        .credential_for_worker(ModelServicePurpose::Script)
        .unwrap();
    assert_eq!(credential.model_id(), BailianModelId::DeepseekV4Pro);
    assert_eq!(credential.api_key(), FIRST_KEY);
    assert!(!format!("{credential:?}").contains(FIRST_KEY));
    let material_video = settings.material_video_script_model().unwrap();
    assert_eq!(material_video.model_id(), "deepseek-v4-pro");
    assert!(!format!("{material_video:?}").contains(FIRST_KEY));
    assert!(!format!("{settings:?}").contains(FIRST_KEY));
}

#[test]
fn purposes_are_separate_but_script_credential_can_be_explicitly_reused() {
    let script = MemoryStore::default();
    let video = MemoryStore::default();
    let settings = service(
        script.clone(),
        video.clone(),
        "https://dashscope.aliyuncs.com/compatible-mode/v1".to_owned(),
    );
    settings
        .configure(&request("script", "glm-5.2", FIRST_KEY))
        .unwrap();
    let invalid = settings
        .configure(&request("video_creative", "glm-5.2", SECOND_KEY))
        .unwrap_err();
    assert_eq!(invalid.code(), ModelServiceErrorCode::ConfigurationInvalid);

    let reused = settings.reuse_script_for_video().unwrap();
    let public = serde_json::to_value(reused).unwrap();
    assert_eq!(public["sameCredential"], true);
    assert_eq!(public["videoCreative"]["modelId"], "qwen3.7-max-2026-06-08");
    assert_ne!(script.raw(), None);
    assert_ne!(video.raw(), None);

    let cleared = settings.clear(ModelServicePurpose::Script).unwrap();
    let public = serde_json::to_value(cleared).unwrap();
    assert_eq!(public["script"]["configured"], false);
    assert_eq!(public["videoCreative"]["configured"], true);
}

#[test]
fn corrupt_storage_invalid_keys_and_storage_errors_are_fixed_and_non_leaking() {
    let script = MemoryStore::default();
    script.replace_raw(
        br#"{"version":1,"purpose":"script","model_id":"glm-5.2","api_key":"secret"}"#,
    );
    let error = ModelServiceSettings::new(
        script,
        MemoryStore::default(),
        reqwest::Client::new(),
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    .unwrap_err();
    assert_eq!(error.code(), ModelServiceErrorCode::StorageUnavailable);
    assert!(!error.to_string().contains("secret"));

    let error = ModelServiceSettings::new(
        MemoryStore::failing(),
        MemoryStore::default(),
        reqwest::Client::new(),
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    .unwrap_err();
    assert_eq!(error.code(), ModelServiceErrorCode::StorageUnavailable);

    let settings = service(
        MemoryStore::default(),
        MemoryStore::default(),
        "https://dashscope.aliyuncs.com/compatible-mode/v1".to_owned(),
    );
    let error = settings
        .configure(&request("script", "glm-5.2", "password=private"))
        .unwrap_err();
    assert_eq!(error.code(), ModelServiceErrorCode::ConfigurationInvalid);
    assert!(!error.to_string().contains("private"));
}

#[test]
fn connection_test_uses_stored_key_fixed_route_model_and_bounded_quota() {
    tauri::async_runtime::block_on(async {
        let body = r#"{"id":"chatcmpl-test","object":"chat.completion","choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"O"}}],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}"#;
        let (base_url, handle) = one_response_server(
            "200 OK",
            &[
                ("x-ratelimit-remaining-requests", "42"),
                ("x-ratelimit-remaining-tokens", "1234"),
            ],
            body,
        );
        let settings = service(MemoryStore::default(), MemoryStore::default(), base_url);
        settings
            .configure(&request("script", "qwen3.7-max-2026-06-08", FIRST_KEY))
            .unwrap();
        let result = settings
            .test_connection(ModelServicePurpose::Script)
            .await
            .unwrap();
        let public = serde_json::to_value(result).unwrap();
        assert_eq!(public["status"], "connected");
        assert_eq!(public["quota"]["remainingRequests"], 42);
        assert_eq!(public["quota"]["remainingTokens"], 1234);
        assert!(!public.to_string().contains(FIRST_KEY));

        let captured = handle.join().unwrap();
        assert!(captured.starts_with("POST /v1/chat/completions HTTP/1.1"));
        assert!(captured
            .to_ascii_lowercase()
            .contains("authorization: bearer"));
        assert!(!captured.contains("用户正文"));
    });
}

#[test]
fn connection_failures_are_classified_without_response_or_key_reflection() {
    tauri::async_runtime::block_on(async {
        for (status, expected) in [
            (
                "401 Unauthorized",
                ModelServiceErrorCode::AuthenticationRejected,
            ),
            (
                "402 Payment Required",
                ModelServiceErrorCode::QuotaExhausted,
            ),
            ("429 Too Many Requests", ModelServiceErrorCode::RateLimited),
            ("404 Not Found", ModelServiceErrorCode::ModelUnavailable),
            (
                "503 Service Unavailable",
                ModelServiceErrorCode::TransportUnavailable,
            ),
        ] {
            let (base_url, handle) =
                one_response_server(status, &[], r#"{"error":"private-upstream"}"#);
            let settings = service(MemoryStore::default(), MemoryStore::default(), base_url);
            settings
                .configure(&request("script", "glm-5.2", FIRST_KEY))
                .unwrap();
            let error = settings
                .test_connection(ModelServicePurpose::Script)
                .await
                .unwrap_err();
            assert_eq!(error.code(), expected);
            assert!(!error.to_string().contains("private-upstream"));
            assert!(!error.to_string().contains(FIRST_KEY));
            handle.join().unwrap();
        }
    });
}

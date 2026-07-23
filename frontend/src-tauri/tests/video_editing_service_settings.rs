use automation_tool_desktop_lib::secure_store::{SecretStore, SecureStoreError};
use automation_tool_desktop_lib::video_editing_service_settings::{
    AliyunEditingRegion, ConfigureVideoEditingServiceRequest, VideoEditingServiceErrorCode,
    VideoEditingServiceSettings,
};
use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::{Arc, Mutex};
use std::thread;
use zeroize::Zeroizing;

const ACCESS_KEY_ID: &str = "LTAI5tVe04TestAccessKey";
const ACCESS_KEY_SECRET: &str = "ve04TestSecretValue1234567890";
const EMPTY_BODY_SHA256: &str = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

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

fn request(region: &str, key_id: &str, key_secret: &str) -> ConfigureVideoEditingServiceRequest {
    serde_json::from_value(serde_json::json!({
        "region": region,
        "accessKeyId": key_id,
        "accessKeySecret": key_secret,
    }))
    .unwrap()
}

fn service(
    store: MemoryStore,
    base_url: Option<String>,
) -> VideoEditingServiceSettings<MemoryStore> {
    VideoEditingServiceSettings::new(store, reqwest::Client::new(), base_url).unwrap()
}

fn one_response_server(status: &str, body: &str) -> (String, thread::JoinHandle<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let status = status.to_owned();
    let body = body.to_owned();
    let handle = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(std::time::Duration::from_secs(2)))
            .unwrap();
        let mut request = vec![0_u8; 16 * 1024];
        let read = stream.read(&mut request).unwrap();
        let captured = String::from_utf8_lossy(&request[..read]).to_string();
        let response = format!(
            "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        stream.write_all(response.as_bytes()).unwrap();
        captured
    });
    (format!("http://{address}"), handle)
}

#[test]
fn configure_snapshot_and_clear_never_expose_credentials() {
    let store = MemoryStore::default();
    let settings = service(store.clone(), None);

    let initial = serde_json::to_value(settings.snapshot().unwrap()).unwrap();
    assert_eq!(initial["provider"], "aliyun_ims");
    assert_eq!(initial["configured"], false);
    assert_eq!(initial["region"], serde_json::Value::Null);

    let snapshot = settings
        .configure(&request("cn-shanghai", ACCESS_KEY_ID, ACCESS_KEY_SECRET))
        .unwrap();
    let public = serde_json::to_string(&snapshot).unwrap();
    assert!(public.contains("\"configured\":true"));
    assert!(public.contains("cn-shanghai"));
    assert!(!public.contains(ACCESS_KEY_ID));
    assert!(!public.contains(ACCESS_KEY_SECRET));
    assert!(String::from_utf8(store.raw().unwrap())
        .unwrap()
        .contains(ACCESS_KEY_SECRET));

    let credential = settings.credential_for_adapter().unwrap();
    assert_eq!(credential.region(), AliyunEditingRegion::CnShanghai);
    assert_eq!(credential.access_key_id(), ACCESS_KEY_ID);
    assert_eq!(credential.access_key_secret(), ACCESS_KEY_SECRET);
    assert!(!format!("{credential:?}").contains(ACCESS_KEY_SECRET));
    assert!(!format!("{settings:?}").contains(ACCESS_KEY_SECRET));

    let cleared = serde_json::to_value(settings.clear().unwrap()).unwrap();
    assert_eq!(cleared["configured"], false);
    assert_eq!(store.raw(), None);
    let error = settings.credential_for_adapter().unwrap_err();
    assert_eq!(
        error.code(),
        VideoEditingServiceErrorCode::ConfigurationRequired
    );
}

#[test]
fn invalid_configurations_are_rejected_without_reflection() {
    let settings = service(MemoryStore::default(), None);
    for (region, key_id, key_secret) in [
        ("cn-shanghai", "short", ACCESS_KEY_SECRET),
        ("cn-shanghai", "AKIA1234567890123456", ACCESS_KEY_SECRET),
        ("cn-shanghai", ACCESS_KEY_ID, "short"),
        ("cn-shanghai", ACCESS_KEY_ID, "secret with spaces exceeding"),
    ] {
        let error = settings
            .configure(&request(region, key_id, key_secret))
            .unwrap_err();
        assert_eq!(
            error.code(),
            VideoEditingServiceErrorCode::ConfigurationInvalid
        );
        assert!(!error.to_string().contains(key_secret));
    }
    assert!(
        serde_json::from_value::<ConfigureVideoEditingServiceRequest>(serde_json::json!({
            "region": "cn-qingdao",
            "accessKeyId": ACCESS_KEY_ID,
            "accessKeySecret": ACCESS_KEY_SECRET,
        }))
        .is_err()
    );
    assert!(
        serde_json::from_value::<ConfigureVideoEditingServiceRequest>(serde_json::json!({
            "region": "cn-shanghai",
            "accessKeyId": ACCESS_KEY_ID,
            "accessKeySecret": ACCESS_KEY_SECRET,
            "extra": true,
        }))
        .is_err()
    );
}

#[test]
fn corrupt_or_unavailable_storage_fails_closed() {
    let corrupt = MemoryStore::default();
    corrupt.replace_raw(br#"{"version":9,"region":"cn-shanghai"}"#);
    let error =
        VideoEditingServiceSettings::new(corrupt, reqwest::Client::new(), None).unwrap_err();
    assert_eq!(
        error.code(),
        VideoEditingServiceErrorCode::StorageUnavailable
    );

    let error =
        VideoEditingServiceSettings::new(MemoryStore::failing(), reqwest::Client::new(), None)
            .unwrap_err();
    assert_eq!(
        error.code(),
        VideoEditingServiceErrorCode::StorageUnavailable
    );
}

#[test]
fn connection_test_signs_read_only_request_and_reports_connected() {
    tauri::async_runtime::block_on(async {
        let (base_url, handle) = one_response_server(
            "200 OK",
            r#"{"RequestId":"req-1","MediaInfos":[],"TotalCount":0}"#,
        );
        let settings = service(MemoryStore::default(), Some(base_url));
        settings
            .configure(&request("cn-shanghai", ACCESS_KEY_ID, ACCESS_KEY_SECRET))
            .unwrap();
        let result = settings.test_connection().await.unwrap();
        let public = serde_json::to_value(result).unwrap();
        assert_eq!(public["status"], "connected");
        assert_eq!(public["region"], "cn-shanghai");
        assert!(!public.to_string().contains(ACCESS_KEY_SECRET));

        let captured = handle.join().unwrap();
        assert!(captured.starts_with("GET /?PageSize=1 HTTP/1.1"));
        let lower = captured.to_ascii_lowercase();
        assert!(lower.contains("x-acs-action: listmediabasicinfos"));
        assert!(lower.contains("x-acs-version: 2020-11-09"));
        assert!(lower.contains(&format!("x-acs-content-sha256: {EMPTY_BODY_SHA256}")));
        assert!(lower.contains("x-acs-signature-nonce: "));
        assert!(lower.contains("authorization: acs3-hmac-sha256 credential="));
        assert!(captured.contains(&format!(
            "ACS3-HMAC-SHA256 Credential={ACCESS_KEY_ID},SignedHeaders="
        )));
        assert!(captured.contains("Signature="));
        assert!(!captured.contains(ACCESS_KEY_SECRET));
    });
}

#[test]
fn connection_test_requires_configuration_first() {
    tauri::async_runtime::block_on(async {
        let settings = service(MemoryStore::default(), None);
        let error = settings.test_connection().await.unwrap_err();
        assert_eq!(
            error.code(),
            VideoEditingServiceErrorCode::ConfigurationRequired
        );
    });
}

#[test]
fn connection_failures_map_to_fixed_codes_without_reflection() {
    tauri::async_runtime::block_on(async {
        for (status, body, expected) in [
            (
                "401 Unauthorized",
                r#"{"Code":"InvalidSecurityToken","Message":"private"}"#,
                VideoEditingServiceErrorCode::AuthenticationRejected,
            ),
            (
                "400 Bad Request",
                r#"{"Code":"SignatureDoesNotMatch","Message":"private"}"#,
                VideoEditingServiceErrorCode::AuthenticationRejected,
            ),
            (
                "404 Not Found",
                r#"{"Code":"InvalidAccessKeyId.NotFound","Message":"private"}"#,
                VideoEditingServiceErrorCode::AuthenticationRejected,
            ),
            (
                "403 Forbidden",
                r#"{"Code":"Forbidden.RAM","Message":"private"}"#,
                VideoEditingServiceErrorCode::PermissionDenied,
            ),
            (
                "429 Too Many Requests",
                r#"{"Code":"Throttling","Message":"private"}"#,
                VideoEditingServiceErrorCode::RateLimited,
            ),
            (
                "503 Service Unavailable",
                r#"{"Code":"ServiceUnavailable","Message":"private"}"#,
                VideoEditingServiceErrorCode::TransportUnavailable,
            ),
            (
                "400 Bad Request",
                r#"{"Code":"UnknownError","Message":"private"}"#,
                VideoEditingServiceErrorCode::InvalidResponse,
            ),
            (
                "200 OK",
                r#"{"NoRequestId":true}"#,
                VideoEditingServiceErrorCode::InvalidResponse,
            ),
        ] {
            let (base_url, handle) = one_response_server(status, body);
            let settings = service(MemoryStore::default(), Some(base_url));
            settings
                .configure(&request("cn-beijing", ACCESS_KEY_ID, ACCESS_KEY_SECRET))
                .unwrap();
            let error = settings.test_connection().await.unwrap_err();
            assert_eq!(error.code(), expected);
            assert!(!error.to_string().contains("private"));
            assert!(!error.to_string().contains(ACCESS_KEY_SECRET));
            handle.join().unwrap();
        }
    });
}

#[test]
fn production_base_url_override_must_be_loopback_in_tests() {
    let error = VideoEditingServiceSettings::new(
        MemoryStore::default(),
        reqwest::Client::new(),
        Some("https://attacker.example.com".to_owned()),
    )
    .unwrap_err();
    assert_eq!(
        error.code(),
        VideoEditingServiceErrorCode::ConfigurationInvalid
    );
}

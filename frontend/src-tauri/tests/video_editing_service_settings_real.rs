//! VE-04 real-gateway acceptance for the production connection-test path.
//!
//! These tests are opt-in: they only run when `VE04_REAL_CREDENTIALS_FILE`
//! points at a local credentials JSON (kept outside Git in `.local/secrets`).
//! They exercise the exact production signing path (`test_connection` with no
//! base-url override) against the real `ice.{region}.aliyuncs.com` gateway and
//! assert that failures stay sanitized. No key material is ever printed.

use automation_tool_desktop_lib::secure_store::{SecretStore, SecureStoreError};
use automation_tool_desktop_lib::video_editing_service_settings::{
    ConfigureVideoEditingServiceRequest, VideoEditingServiceErrorCode, VideoEditingServiceSettings,
};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use zeroize::Zeroizing;

#[derive(Clone, Default)]
struct MemoryStore {
    value: Arc<Mutex<Option<Vec<u8>>>>,
}

impl SecretStore for MemoryStore {
    fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, SecureStoreError> {
        Ok(self.value.lock().unwrap().clone().map(Zeroizing::new))
    }

    fn save(&self, secret: &[u8]) -> Result<(), SecureStoreError> {
        *self.value.lock().unwrap() = Some(secret.to_vec());
        Ok(())
    }

    fn delete(&self) -> Result<(), SecureStoreError> {
        *self.value.lock().unwrap() = None;
        Ok(())
    }
}

struct RealCredentials {
    region: String,
    access_key_id: Zeroizing<String>,
    access_key_secret: Zeroizing<String>,
    oss_bucket: Zeroizing<String>,
}

fn load_real_credentials() -> Option<RealCredentials> {
    let path = std::env::var("VE04_REAL_CREDENTIALS_FILE").ok()?;
    let raw = Zeroizing::new(std::fs::read_to_string(path).expect("credentials file readable"));
    let parsed: serde_json::Value =
        serde_json::from_str(&raw).expect("credentials file is valid JSON");
    let field = |name: &str| {
        Zeroizing::new(
            parsed
                .get(name)
                .and_then(serde_json::Value::as_str)
                .expect("credentials field present")
                .to_owned(),
        )
    };
    Some(RealCredentials {
        region: field("region").to_string(),
        access_key_id: field("accessKeyId"),
        access_key_secret: field("accessKeySecret"),
        oss_bucket: field("ossBucket"),
    })
}

fn production_like_client() -> reqwest::Client {
    reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(15))
        .timeout(Duration::from_secs(15))
        .https_only(true)
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .expect("client builds")
}

fn service_with(
    region: &str,
    key_id: &str,
    key_secret: &str,
    oss_bucket: &str,
) -> VideoEditingServiceSettings<MemoryStore> {
    let settings =
        VideoEditingServiceSettings::new(MemoryStore::default(), production_like_client(), None)
            .expect("settings initialize");
    let request: ConfigureVideoEditingServiceRequest = serde_json::from_value(serde_json::json!({
        "region": region,
        "accessKeyId": key_id,
        "accessKeySecret": key_secret,
        "ossBucket": oss_bucket,
    }))
    .expect("configure request deserializes");
    settings.configure(&request).expect("configure succeeds");
    settings
}

#[test]
fn real_gateway_accepts_production_signature() {
    let Some(credentials) = load_real_credentials() else {
        eprintln!("skipped: VE04_REAL_CREDENTIALS_FILE not set");
        return;
    };
    let settings = service_with(
        &credentials.region,
        &credentials.access_key_id,
        &credentials.access_key_secret,
        &credentials.oss_bucket,
    );
    let connection = tauri::async_runtime::block_on(settings.test_connection())
        .expect("real gateway accepts the ACS3-HMAC-SHA256 signed connection test");
    let serialized = serde_json::to_value(&connection).expect("snapshot serializes");
    assert_eq!(
        serialized,
        serde_json::json!({ "region": credentials.region, "status": "connected" })
    );
}

#[test]
fn real_gateway_rejects_tampered_secret_with_sanitized_error() {
    let Some(credentials) = load_real_credentials() else {
        eprintln!("skipped: VE04_REAL_CREDENTIALS_FILE not set");
        return;
    };
    let tampered = Zeroizing::new(format!("{}x", credentials.access_key_secret.as_str()));
    let settings = service_with(
        &credentials.region,
        &credentials.access_key_id,
        &tampered,
        &credentials.oss_bucket,
    );
    let error = tauri::async_runtime::block_on(settings.test_connection())
        .expect_err("real gateway must reject a tampered secret");
    assert_eq!(
        error.code(),
        VideoEditingServiceErrorCode::AuthenticationRejected
    );
    assert!(!error.retryable());
    let rendered = format!("{error} {error:?}");
    assert!(!rendered.contains(credentials.access_key_id.as_str()));
    assert!(!rendered.contains(credentials.access_key_secret.as_str()));
    assert!(!rendered.contains("aliyuncs.com"));
}

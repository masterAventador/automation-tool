//! Native-only Aliyun IMS/ICE editing credentials and sanitized connection test.
//!
//! VE-04: the video-editing service AccessKey pair lives exclusively in the
//! Rust-managed app-data secret store, exactly like the VF-05 model services.
//! React only ever sees "configured / not configured", the closed region id and
//! fixed error codes. The connection test issues one read-only, ACS3-HMAC-SHA256
//! signed `ListMediaBasicInfos` request against the closed regional endpoint
//! list from `contracts/video/aliyun-ims-editing-staging.v1.json` and never
//! reflects upstream bodies, keys or paths.

use crate::secure_store::{AppDataSecretStore, SecretStore, SecureStoreError};
use hmac::{Hmac, KeyInit, Mac};
use reqwest::{Client, StatusCode};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::error::Error;
use std::fmt::{self, Display, Formatter, Write as _};
use std::path::Path;
use std::time::Duration;
use zeroize::{Zeroize, Zeroizing};

const CREDENTIAL_FILE: &str = "video-editing-service-aliyun-v1";
const CONNECTION_TIMEOUT: Duration = Duration::from_secs(15);
const MAX_RESPONSE_BYTES: u64 = 64 * 1024;
const API_VERSION: &str = "2020-11-09";
const CONNECTION_TEST_ACTION: &str = "ListMediaBasicInfos";
const CONTRACT_JSON: &str =
    include_str!("../../../contracts/video/aliyun-ims-editing-staging.v1.json");
const EMPTY_BODY_SHA256: &str = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub enum AliyunEditingRegion {
    #[serde(rename = "cn-beijing")]
    CnBeijing,
    #[serde(rename = "cn-hangzhou")]
    CnHangzhou,
    #[serde(rename = "cn-shanghai")]
    CnShanghai,
    #[serde(rename = "cn-shenzhen")]
    CnShenzhen,
    #[serde(rename = "ap-southeast-1")]
    ApSoutheast1,
    #[serde(rename = "us-west-1")]
    UsWest1,
}

impl AliyunEditingRegion {
    pub const ALL: [Self; 6] = [
        Self::CnBeijing,
        Self::CnHangzhou,
        Self::CnShanghai,
        Self::CnShenzhen,
        Self::ApSoutheast1,
        Self::UsWest1,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Self::CnBeijing => "cn-beijing",
            Self::CnHangzhou => "cn-hangzhou",
            Self::CnShanghai => "cn-shanghai",
            Self::CnShenzhen => "cn-shenzhen",
            Self::ApSoutheast1 => "ap-southeast-1",
            Self::UsWest1 => "us-west-1",
        }
    }

    fn endpoint(self) -> String {
        format!("ice.{}.aliyuncs.com", self.as_str())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct VideoEditingServiceSnapshot {
    provider: &'static str,
    provider_label: &'static str,
    catalog_verified_at: &'static str,
    configured: bool,
    region: Option<AliyunEditingRegion>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct ConfigureVideoEditingServiceRequest {
    region: AliyunEditingRegion,
    access_key_id: String,
    access_key_secret: String,
}

impl Drop for ConfigureVideoEditingServiceRequest {
    fn drop(&mut self) {
        self.access_key_id.zeroize();
        self.access_key_secret.zeroize();
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct StoredCredential {
    version: u32,
    region: AliyunEditingRegion,
    access_key_id: String,
    access_key_secret: String,
}

impl Drop for StoredCredential {
    fn drop(&mut self) {
        self.access_key_id.zeroize();
        self.access_key_secret.zeroize();
    }
}

pub struct EditingServiceCredential {
    region: AliyunEditingRegion,
    access_key_id: Zeroizing<String>,
    access_key_secret: Zeroizing<String>,
}

impl fmt::Debug for EditingServiceCredential {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EditingServiceCredential")
            .field("region", &self.region)
            .finish_non_exhaustive()
    }
}

impl EditingServiceCredential {
    pub fn region(&self) -> AliyunEditingRegion {
        self.region
    }

    pub fn access_key_id(&self) -> &str {
        self.access_key_id.as_str()
    }

    pub fn access_key_secret(&self) -> &str {
        self.access_key_secret.as_str()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VideoEditingConnectionStatus {
    Connected,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct VideoEditingConnectionSnapshot {
    region: AliyunEditingRegion,
    status: VideoEditingConnectionStatus,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VideoEditingServiceErrorCode {
    AuthenticationRejected,
    ConfigurationInvalid,
    ConfigurationRequired,
    InvalidResponse,
    PermissionDenied,
    RateLimited,
    StorageUnavailable,
    TimedOut,
    TransportUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct VideoEditingServiceError {
    code: VideoEditingServiceErrorCode,
    retryable: bool,
}

impl VideoEditingServiceError {
    fn new(code: VideoEditingServiceErrorCode, retryable: bool) -> Self {
        Self { code, retryable }
    }

    pub fn code(self) -> VideoEditingServiceErrorCode {
        self.code
    }

    pub fn retryable(self) -> bool {
        self.retryable
    }
}

impl Display for VideoEditingServiceError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str("video editing service operation unavailable")
    }
}

impl Error for VideoEditingServiceError {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct VideoEditingServiceCommandError {
    code: VideoEditingServiceErrorCode,
    retryable: bool,
}

impl Serialize for VideoEditingServiceCommandError {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        crate::command_error::serialize(&self.code, Some(self.retryable), serializer)
    }
}

impl From<VideoEditingServiceError> for VideoEditingServiceCommandError {
    fn from(value: VideoEditingServiceError) -> Self {
        Self {
            code: value.code,
            retryable: value.retryable,
        }
    }
}

pub struct VideoEditingServiceSettings<S> {
    store: S,
    client: Client,
    base_url_override: Option<String>,
}

impl<S> fmt::Debug for VideoEditingServiceSettings<S> {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VideoEditingServiceSettings")
            .field("provider", &"aliyun_ims")
            .finish_non_exhaustive()
    }
}

impl<S> VideoEditingServiceSettings<S>
where
    S: SecretStore,
{
    pub fn new(
        store: S,
        client: Client,
        base_url_override: Option<String>,
    ) -> Result<Self, VideoEditingServiceError> {
        validate_contract()?;
        if let Some(base_url) = base_url_override.as_deref() {
            if !is_valid_override(base_url) {
                return Err(VideoEditingServiceError::new(
                    VideoEditingServiceErrorCode::ConfigurationInvalid,
                    false,
                ));
            }
        }
        let settings = Self {
            store,
            client,
            base_url_override,
        };
        let _ = settings.snapshot()?;
        Ok(settings)
    }

    pub fn snapshot(&self) -> Result<VideoEditingServiceSnapshot, VideoEditingServiceError> {
        let credential = self.load()?;
        Ok(VideoEditingServiceSnapshot {
            provider: "aliyun_ims",
            provider_label: "阿里云视频剪辑服务",
            catalog_verified_at: "2026-07-23",
            configured: credential.is_some(),
            region: credential.as_ref().map(|value| value.region),
        })
    }

    pub fn configure(
        &self,
        request: &ConfigureVideoEditingServiceRequest,
    ) -> Result<VideoEditingServiceSnapshot, VideoEditingServiceError> {
        if !is_valid_access_key_id(&request.access_key_id)
            || !is_valid_access_key_secret(&request.access_key_secret)
        {
            return Err(VideoEditingServiceError::new(
                VideoEditingServiceErrorCode::ConfigurationInvalid,
                false,
            ));
        }
        let credential = StoredCredential {
            version: 1,
            region: request.region,
            access_key_id: request.access_key_id.clone(),
            access_key_secret: request.access_key_secret.clone(),
        };
        let bytes = Zeroizing::new(serde_json::to_vec(&credential).map_err(|_| {
            VideoEditingServiceError::new(VideoEditingServiceErrorCode::ConfigurationInvalid, false)
        })?);
        self.store
            .save(bytes.as_slice())
            .map_err(map_storage_error)?;
        self.snapshot()
    }

    pub fn clear(&self) -> Result<VideoEditingServiceSnapshot, VideoEditingServiceError> {
        self.store.delete().map_err(map_storage_error)?;
        self.snapshot()
    }

    pub fn credential_for_adapter(
        &self,
    ) -> Result<EditingServiceCredential, VideoEditingServiceError> {
        let credential = self.load()?.ok_or_else(|| {
            VideoEditingServiceError::new(
                VideoEditingServiceErrorCode::ConfigurationRequired,
                false,
            )
        })?;
        Ok(EditingServiceCredential {
            region: credential.region,
            access_key_id: Zeroizing::new(credential.access_key_id.clone()),
            access_key_secret: Zeroizing::new(credential.access_key_secret.clone()),
        })
    }

    pub async fn test_connection(
        &self,
    ) -> Result<VideoEditingConnectionSnapshot, VideoEditingServiceError> {
        let credential = self.credential_for_adapter()?;
        let (origin, host) = match self.base_url_override.as_deref() {
            Some(base_url) => {
                let host = base_url
                    .strip_prefix("http://")
                    .unwrap_or(base_url)
                    .to_owned();
                (base_url.to_owned(), host)
            }
            None => {
                let endpoint = credential.region().endpoint();
                (format!("https://{endpoint}"), endpoint)
            }
        };
        let query = "PageSize=1";
        let timestamp = utc_timestamp();
        let mut nonce_bytes = [0_u8; 16];
        getrandom::fill(&mut nonce_bytes).map_err(|_| {
            VideoEditingServiceError::new(VideoEditingServiceErrorCode::TransportUnavailable, true)
        })?;
        let nonce = hex_encode(&nonce_bytes);
        let signed_headers = [
            ("x-acs-action", CONNECTION_TEST_ACTION.to_owned()),
            ("x-acs-content-sha256", EMPTY_BODY_SHA256.to_owned()),
            ("x-acs-date", timestamp),
            ("x-acs-signature-nonce", nonce),
            ("x-acs-version", API_VERSION.to_owned()),
        ];
        let authorization = build_authorization(
            "GET",
            "/",
            query,
            &host,
            &signed_headers,
            credential.access_key_id(),
            credential.access_key_secret(),
        );
        let mut request = self
            .client
            .get(format!("{origin}/?{query}"))
            .timeout(CONNECTION_TIMEOUT)
            .header("Authorization", authorization);
        for (name, value) in &signed_headers {
            request = request.header(*name, value);
        }
        let response = request.send().await.map_err(map_transport_error)?;
        let status = response.status();
        if response
            .content_length()
            .is_some_and(|length| length > MAX_RESPONSE_BYTES)
        {
            return Err(VideoEditingServiceError::new(
                VideoEditingServiceErrorCode::InvalidResponse,
                false,
            ));
        }
        let body = response.bytes().await.map_err(map_transport_error)?;
        if body.len() as u64 > MAX_RESPONSE_BYTES {
            return Err(VideoEditingServiceError::new(
                VideoEditingServiceErrorCode::InvalidResponse,
                false,
            ));
        }
        if !status.is_success() {
            return Err(classify_failure(status, &body));
        }
        let parsed: ConnectionResponse = serde_json::from_slice(&body).map_err(|_| {
            VideoEditingServiceError::new(VideoEditingServiceErrorCode::InvalidResponse, false)
        })?;
        if parsed.request_id.is_empty() {
            return Err(VideoEditingServiceError::new(
                VideoEditingServiceErrorCode::InvalidResponse,
                false,
            ));
        }
        Ok(VideoEditingConnectionSnapshot {
            region: credential.region(),
            status: VideoEditingConnectionStatus::Connected,
        })
    }

    fn load(&self) -> Result<Option<StoredCredential>, VideoEditingServiceError> {
        let Some(bytes) = self.store.load().map_err(map_storage_error)? else {
            return Ok(None);
        };
        let credential: StoredCredential =
            serde_json::from_slice(bytes.as_slice()).map_err(|_| {
                VideoEditingServiceError::new(
                    VideoEditingServiceErrorCode::StorageUnavailable,
                    false,
                )
            })?;
        if credential.version != 1
            || !is_valid_access_key_id(&credential.access_key_id)
            || !is_valid_access_key_secret(&credential.access_key_secret)
        {
            return Err(VideoEditingServiceError::new(
                VideoEditingServiceErrorCode::StorageUnavailable,
                false,
            ));
        }
        Ok(Some(credential))
    }
}

#[derive(Deserialize)]
struct ConnectionResponse {
    #[serde(rename = "RequestId")]
    request_id: String,
}

#[derive(Deserialize)]
struct FailureResponse {
    #[serde(rename = "Code")]
    code: Option<String>,
}

fn is_valid_access_key_id(value: &str) -> bool {
    (16..=64).contains(&value.len())
        && value.starts_with("LTAI")
        && value.bytes().all(|byte| byte.is_ascii_alphanumeric())
}

fn is_valid_access_key_secret(value: &str) -> bool {
    (20..=128).contains(&value.len())
        && value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'/' | b'+' | b'=' | b'-' | b'_')
        })
}

fn is_valid_override(value: &str) -> bool {
    if !cfg!(debug_assertions) {
        return false;
    }
    let Ok(url) = reqwest::Url::parse(value) else {
        return false;
    };
    url.scheme() == "http" && url.host_str() == Some("127.0.0.1")
}

fn validate_contract() -> Result<(), VideoEditingServiceError> {
    let invalid =
        || VideoEditingServiceError::new(VideoEditingServiceErrorCode::ConfigurationInvalid, false);
    let document: serde_json::Value = serde_json::from_str(CONTRACT_JSON).map_err(|_| invalid())?;
    if document.get("contract") != Some(&serde_json::json!("aliyun-ims-editing-staging"))
        || document.get("version") != Some(&serde_json::json!(1))
        || document.get("verified_at") != Some(&serde_json::json!("2026-07-23"))
    {
        return Err(invalid());
    }
    let regions = document
        .get("regions")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(invalid)?;
    if regions.len() != AliyunEditingRegion::ALL.len() {
        return Err(invalid());
    }
    for region in AliyunEditingRegion::ALL {
        let entry = regions
            .iter()
            .find(|entry| entry.get("region_id") == Some(&serde_json::json!(region.as_str())))
            .ok_or_else(invalid)?;
        if entry.get("endpoint") != Some(&serde_json::json!(region.endpoint())) {
            return Err(invalid());
        }
    }
    Ok(())
}

fn utc_timestamp() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let days = now / 86_400;
    let seconds_of_day = now % 86_400;
    let mut year = 1970_i64;
    let mut remaining = days as i64;
    loop {
        let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
        let length = if leap { 366 } else { 365 };
        if remaining < length {
            break;
        }
        remaining -= length;
        year += 1;
    }
    let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
    let month_lengths = [
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    let mut month = 1;
    for length in month_lengths {
        if remaining < length {
            break;
        }
        remaining -= length;
        month += 1;
    }
    format!(
        "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z",
        day = remaining + 1,
        hour = seconds_of_day / 3600,
        minute = (seconds_of_day % 3600) / 60,
        second = seconds_of_day % 60,
    )
}

fn hex_encode(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        let _ = write!(output, "{byte:02x}");
    }
    output
}

fn build_authorization(
    method: &str,
    canonical_uri: &str,
    query: &str,
    host: &str,
    signed_headers: &[(&str, String)],
    access_key_id: &str,
    access_key_secret: &str,
) -> String {
    let mut header_lines = vec![("host", host.to_owned())];
    for (name, value) in signed_headers {
        header_lines.push((name, value.clone()));
    }
    header_lines.sort_by(|left, right| left.0.cmp(right.0));
    let canonical_headers = header_lines
        .iter()
        .map(|(name, value)| format!("{name}:{value}\n"))
        .collect::<String>();
    let signed_header_names = header_lines
        .iter()
        .map(|(name, _)| (*name).to_owned())
        .collect::<Vec<_>>()
        .join(";");
    let canonical_request = format!(
        "{method}\n{canonical_uri}\n{query}\n{canonical_headers}\n{signed_header_names}\n{EMPTY_BODY_SHA256}"
    );
    let hashed_request = hex_encode(&Sha256::digest(canonical_request.as_bytes()));
    let string_to_sign = format!("ACS3-HMAC-SHA256\n{hashed_request}");
    let mut mac = Hmac::<Sha256>::new_from_slice(access_key_secret.as_bytes())
        .expect("HMAC accepts any key length");
    mac.update(string_to_sign.as_bytes());
    let signature = hex_encode(&mac.finalize().into_bytes());
    format!(
        "ACS3-HMAC-SHA256 Credential={access_key_id},SignedHeaders={signed_header_names},Signature={signature}"
    )
}

fn classify_failure(status: StatusCode, body: &[u8]) -> VideoEditingServiceError {
    if let Ok(parsed) = serde_json::from_slice::<FailureResponse>(body) {
        if let Some(code) = parsed.code.as_deref() {
            if code.starts_with("InvalidAccessKeyId")
                || code.starts_with("SignatureDoesNotMatch")
                || code.starts_with("IncompleteSignature")
                || code.starts_with("InvalidSecurityToken")
            {
                return VideoEditingServiceError::new(
                    VideoEditingServiceErrorCode::AuthenticationRejected,
                    false,
                );
            }
            if code.starts_with("Forbidden") || code.starts_with("NoPermission") {
                return VideoEditingServiceError::new(
                    VideoEditingServiceErrorCode::PermissionDenied,
                    false,
                );
            }
        }
    }
    match status {
        StatusCode::UNAUTHORIZED => VideoEditingServiceError::new(
            VideoEditingServiceErrorCode::AuthenticationRejected,
            false,
        ),
        StatusCode::FORBIDDEN => {
            VideoEditingServiceError::new(VideoEditingServiceErrorCode::PermissionDenied, false)
        }
        StatusCode::TOO_MANY_REQUESTS => {
            VideoEditingServiceError::new(VideoEditingServiceErrorCode::RateLimited, true)
        }
        status if status.is_server_error() => {
            VideoEditingServiceError::new(VideoEditingServiceErrorCode::TransportUnavailable, true)
        }
        _ => VideoEditingServiceError::new(VideoEditingServiceErrorCode::InvalidResponse, false),
    }
}

fn map_storage_error(_error: SecureStoreError) -> VideoEditingServiceError {
    VideoEditingServiceError::new(VideoEditingServiceErrorCode::StorageUnavailable, false)
}

fn map_transport_error(error: reqwest::Error) -> VideoEditingServiceError {
    if error.is_timeout() {
        VideoEditingServiceError::new(VideoEditingServiceErrorCode::TimedOut, true)
    } else {
        VideoEditingServiceError::new(VideoEditingServiceErrorCode::TransportUnavailable, true)
    }
}

pub(crate) type ProductionVideoEditingServiceSettings =
    VideoEditingServiceSettings<AppDataSecretStore>;

pub(crate) fn initialize_production_video_editing_service_settings(
    app_data_directory: &Path,
) -> Result<ProductionVideoEditingServiceSettings, VideoEditingServiceError> {
    let directory = app_data_directory.join("editing-services");
    let store = AppDataSecretStore::new(&directory, CREDENTIAL_FILE).map_err(map_storage_error)?;
    let client = Client::builder()
        .connect_timeout(CONNECTION_TIMEOUT)
        .timeout(CONNECTION_TIMEOUT)
        .https_only(true)
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| {
            VideoEditingServiceError::new(VideoEditingServiceErrorCode::ConfigurationInvalid, false)
        })?;
    VideoEditingServiceSettings::new(store, client, None)
}

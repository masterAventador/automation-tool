//! Native-only model credentials, capability selection and connection testing.

use crate::local_video_orchestrator::VideoWorkerScriptModelConfiguration;
use crate::secure_store::{AppDataSecretStore, SecretStore, SecureStoreError};
use reqwest::{Client, StatusCode};
use serde::{Deserialize, Serialize};
use std::error::Error;
use std::fmt::{self, Display, Formatter};
use std::path::Path;
use std::time::Duration;
use zeroize::{Zeroize, Zeroizing};

const SCRIPT_CREDENTIAL_FILE: &str = "model-service-script-v1";
const VIDEO_CREDENTIAL_FILE: &str = "model-service-video-creative-v1";
/// The one OpenAI-compatible endpoint every worker and agent is pointed at.
/// Declared once so a second spelling can never reach a model this product did
/// not configure.
pub const PRODUCTION_BASE_URL: &str = "https://dashscope.aliyuncs.com/compatible-mode/v1";
const CONNECTION_TIMEOUT: Duration = Duration::from_secs(15);
const MAX_API_KEY_LENGTH: usize = 256;
const MAX_RESPONSE_BYTES: u64 = 64 * 1024;
const CATALOG_JSON: &str = include_str!("../../../contracts/video/bailian-model-catalog.v1.json");

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelServicePurpose {
    Script,
    VideoCreative,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub enum BailianModelId {
    #[serde(rename = "deepseek-v4-pro")]
    DeepseekV4Pro,
    #[serde(rename = "glm-5.2")]
    Glm52,
    #[serde(rename = "qwen3.7-max-2026-06-08")]
    Qwen37Max20260608,
}

impl BailianModelId {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::DeepseekV4Pro => "deepseek-v4-pro",
            Self::Glm52 => "glm-5.2",
            Self::Qwen37Max20260608 => "qwen3.7-max-2026-06-08",
        }
    }

    fn allowed_for(self, purpose: ModelServicePurpose) -> bool {
        purpose == ModelServicePurpose::Script || self == Self::Qwen37Max20260608
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelPurposeSnapshot {
    purpose: ModelServicePurpose,
    configured: bool,
    model_id: BailianModelId,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelServiceSnapshot {
    provider: &'static str,
    provider_label: &'static str,
    catalog_verified_at: &'static str,
    script: ModelPurposeSnapshot,
    video_creative: ModelPurposeSnapshot,
    same_credential: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct ConfigureModelServiceRequest {
    purpose: ModelServicePurpose,
    model_id: BailianModelId,
    api_key: String,
}

impl Drop for ConfigureModelServiceRequest {
    fn drop(&mut self) {
        self.api_key.zeroize();
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct StoredCredential {
    version: u32,
    purpose: ModelServicePurpose,
    model_id: BailianModelId,
    api_key: String,
}

impl Drop for StoredCredential {
    fn drop(&mut self) {
        self.api_key.zeroize();
    }
}

pub struct WorkerModelCredential {
    model_id: BailianModelId,
    api_key: Zeroizing<String>,
}

impl fmt::Debug for WorkerModelCredential {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("WorkerModelCredential")
            .field("model_id", &self.model_id)
            .finish_non_exhaustive()
    }
}

impl WorkerModelCredential {
    pub fn model_id(&self) -> BailianModelId {
        self.model_id
    }

    pub fn api_key(&self) -> &str {
        self.api_key.as_str()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelConnectionStatus {
    Connected,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelQuotaSnapshot {
    remaining_requests: Option<u64>,
    remaining_tokens: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelConnectionSnapshot {
    purpose: ModelServicePurpose,
    model_id: BailianModelId,
    status: ModelConnectionStatus,
    quota: ModelQuotaSnapshot,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelServiceErrorCode {
    AuthenticationRejected,
    ConfigurationInvalid,
    ConfigurationRequired,
    InvalidResponse,
    ModelUnavailable,
    QuotaExhausted,
    RateLimited,
    StorageUnavailable,
    TimedOut,
    TransportUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ModelServiceError {
    code: ModelServiceErrorCode,
    retryable: bool,
}

impl ModelServiceError {
    fn new(code: ModelServiceErrorCode, retryable: bool) -> Self {
        Self { code, retryable }
    }

    pub fn code(self) -> ModelServiceErrorCode {
        self.code
    }

    pub fn retryable(self) -> bool {
        self.retryable
    }
}

impl Display for ModelServiceError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str("model service operation unavailable")
    }
}

impl Error for ModelServiceError {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ModelServiceCommandError {
    code: ModelServiceErrorCode,
    retryable: bool,
}

impl Serialize for ModelServiceCommandError {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        crate::command_error::serialize(&self.code, Some(self.retryable), serializer)
    }
}

impl From<ModelServiceError> for ModelServiceCommandError {
    fn from(value: ModelServiceError) -> Self {
        Self {
            code: value.code,
            retryable: value.retryable,
        }
    }
}

pub struct ModelServiceSettings<S> {
    script_store: S,
    video_store: S,
    client: Client,
    base_url: String,
}

impl<S> fmt::Debug for ModelServiceSettings<S> {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ModelServiceSettings")
            .field("provider", &"bailian")
            .finish_non_exhaustive()
    }
}

impl<S> ModelServiceSettings<S>
where
    S: SecretStore,
{
    pub fn new(
        script_store: S,
        video_store: S,
        client: Client,
        base_url: impl Into<String>,
    ) -> Result<Self, ModelServiceError> {
        validate_catalog()?;
        let base_url = base_url.into();
        if !is_valid_base_url(&base_url) {
            return Err(ModelServiceError::new(
                ModelServiceErrorCode::ConfigurationInvalid,
                false,
            ));
        }
        let service = Self {
            script_store,
            video_store,
            client,
            base_url,
        };
        let _ = service.snapshot()?;
        Ok(service)
    }

    pub fn snapshot(&self) -> Result<ModelServiceSnapshot, ModelServiceError> {
        let script = self.load(ModelServicePurpose::Script)?;
        let video = self.load(ModelServicePurpose::VideoCreative)?;
        let same_credential = match (&script, &video) {
            (Some(left), Some(right)) => left.api_key == right.api_key,
            _ => false,
        };
        Ok(ModelServiceSnapshot {
            provider: "bailian",
            provider_label: "阿里百炼",
            catalog_verified_at: "2026-07-31",
            script: purpose_snapshot(ModelServicePurpose::Script, script.as_ref()),
            video_creative: purpose_snapshot(ModelServicePurpose::VideoCreative, video.as_ref()),
            same_credential,
        })
    }

    pub fn configure(
        &self,
        request: &ConfigureModelServiceRequest,
    ) -> Result<ModelServiceSnapshot, ModelServiceError> {
        if !request.model_id.allowed_for(request.purpose) || !is_valid_api_key(&request.api_key) {
            return Err(ModelServiceError::new(
                ModelServiceErrorCode::ConfigurationInvalid,
                false,
            ));
        }
        let credential = StoredCredential {
            version: 1,
            purpose: request.purpose,
            model_id: request.model_id,
            api_key: request.api_key.clone(),
        };
        self.save(&credential)?;
        self.snapshot()
    }

    pub fn reuse_script_for_video(&self) -> Result<ModelServiceSnapshot, ModelServiceError> {
        let script = self.load(ModelServicePurpose::Script)?.ok_or_else(|| {
            ModelServiceError::new(ModelServiceErrorCode::ConfigurationRequired, false)
        })?;
        let video = StoredCredential {
            version: 1,
            purpose: ModelServicePurpose::VideoCreative,
            model_id: BailianModelId::Qwen37Max20260608,
            api_key: script.api_key.clone(),
        };
        self.save(&video)?;
        self.snapshot()
    }

    pub fn clear(
        &self,
        purpose: ModelServicePurpose,
    ) -> Result<ModelServiceSnapshot, ModelServiceError> {
        self.store(purpose).delete().map_err(map_storage_error)?;
        self.snapshot()
    }

    pub fn credential_for_worker(
        &self,
        purpose: ModelServicePurpose,
    ) -> Result<WorkerModelCredential, ModelServiceError> {
        let credential = self.load(purpose)?.ok_or_else(|| {
            ModelServiceError::new(ModelServiceErrorCode::ConfigurationRequired, false)
        })?;
        Ok(WorkerModelCredential {
            model_id: credential.model_id,
            api_key: Zeroizing::new(credential.api_key.clone()),
        })
    }

    pub fn material_video_script_model(
        &self,
    ) -> Result<VideoWorkerScriptModelConfiguration, ModelServiceError> {
        let credential = self.credential_for_worker(ModelServicePurpose::Script)?;
        VideoWorkerScriptModelConfiguration::bailian(
            credential.model_id().as_str(),
            credential.api_key().to_owned(),
        )
        .map_err(|_| ModelServiceError::new(ModelServiceErrorCode::ConfigurationInvalid, false))
    }

    pub async fn test_connection(
        &self,
        purpose: ModelServicePurpose,
    ) -> Result<ModelConnectionSnapshot, ModelServiceError> {
        let credential = self.credential_for_worker(purpose)?;
        let endpoint = format!("{}/chat/completions", self.base_url.trim_end_matches('/'));
        let response = self
            .client
            .post(endpoint)
            .timeout(CONNECTION_TIMEOUT)
            .bearer_auth(credential.api_key())
            .json(&serde_json::json!({
                "model": credential.model_id().as_str(),
                "messages": [{"role": "user", "content": "connection-check"}],
                "max_tokens": 1,
                "temperature": 0
            }))
            .send()
            .await
            .map_err(map_transport_error)?;
        let status = response.status();
        if !status.is_success() {
            return Err(map_http_status(status));
        }
        let quota = ModelQuotaSnapshot {
            remaining_requests: bounded_quota_header(&response, "x-ratelimit-remaining-requests"),
            remaining_tokens: bounded_quota_header(&response, "x-ratelimit-remaining-tokens"),
        };
        if response
            .content_length()
            .is_some_and(|length| length > MAX_RESPONSE_BYTES)
        {
            return Err(ModelServiceError::new(
                ModelServiceErrorCode::InvalidResponse,
                false,
            ));
        }
        let body = response.bytes().await.map_err(map_transport_error)?;
        if body.len() as u64 > MAX_RESPONSE_BYTES {
            return Err(ModelServiceError::new(
                ModelServiceErrorCode::InvalidResponse,
                false,
            ));
        }
        let parsed: ConnectionResponse = serde_json::from_slice(&body)
            .map_err(|_| ModelServiceError::new(ModelServiceErrorCode::InvalidResponse, false))?;
        if parsed.choices.is_empty() || parsed.usage.total_tokens == 0 {
            return Err(ModelServiceError::new(
                ModelServiceErrorCode::InvalidResponse,
                false,
            ));
        }
        Ok(ModelConnectionSnapshot {
            purpose,
            model_id: credential.model_id(),
            status: ModelConnectionStatus::Connected,
            quota,
        })
    }

    fn load(
        &self,
        purpose: ModelServicePurpose,
    ) -> Result<Option<StoredCredential>, ModelServiceError> {
        let Some(bytes) = self.store(purpose).load().map_err(map_storage_error)? else {
            return Ok(None);
        };
        let credential: StoredCredential =
            serde_json::from_slice(bytes.as_slice()).map_err(|_| {
                ModelServiceError::new(ModelServiceErrorCode::StorageUnavailable, false)
            })?;
        if credential.version != 1
            || credential.purpose != purpose
            || !credential.model_id.allowed_for(purpose)
            || !is_valid_api_key(&credential.api_key)
        {
            return Err(ModelServiceError::new(
                ModelServiceErrorCode::StorageUnavailable,
                false,
            ));
        }
        Ok(Some(credential))
    }

    fn save(&self, credential: &StoredCredential) -> Result<(), ModelServiceError> {
        let bytes = Zeroizing::new(serde_json::to_vec(credential).map_err(|_| {
            ModelServiceError::new(ModelServiceErrorCode::ConfigurationInvalid, false)
        })?);
        self.store(credential.purpose)
            .save(bytes.as_slice())
            .map_err(map_storage_error)
    }

    fn store(&self, purpose: ModelServicePurpose) -> &S {
        match purpose {
            ModelServicePurpose::Script => &self.script_store,
            ModelServicePurpose::VideoCreative => &self.video_store,
        }
    }
}

#[derive(Deserialize)]
struct ConnectionResponse {
    choices: Vec<ConnectionChoice>,
    usage: ConnectionUsage,
}

#[derive(Deserialize)]
struct ConnectionChoice {
    #[serde(rename = "finish_reason")]
    _finish_reason: Option<String>,
}

#[derive(Deserialize)]
struct ConnectionUsage {
    total_tokens: u64,
}

fn purpose_snapshot(
    purpose: ModelServicePurpose,
    credential: Option<&StoredCredential>,
) -> ModelPurposeSnapshot {
    ModelPurposeSnapshot {
        purpose,
        configured: credential.is_some(),
        model_id: credential.map_or(BailianModelId::Qwen37Max20260608, |value| value.model_id),
    }
}

fn is_valid_api_key(value: &str) -> bool {
    // Real Bailian workspace keys carry dot-separated segments (sk-ws-X.....).
    (20..=MAX_API_KEY_LENGTH).contains(&value.len())
        && value.starts_with("sk-")
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
}

fn is_valid_base_url(value: &str) -> bool {
    let Ok(url) = reqwest::Url::parse(value) else {
        return false;
    };
    if cfg!(debug_assertions) {
        return (url.scheme() == "http" && url.host_str() == Some("127.0.0.1"))
            || value == PRODUCTION_BASE_URL;
    }
    value == PRODUCTION_BASE_URL
}

fn validate_catalog() -> Result<(), ModelServiceError> {
    let document: serde_json::Value = serde_json::from_str(CATALOG_JSON)
        .map_err(|_| ModelServiceError::new(ModelServiceErrorCode::ConfigurationInvalid, false))?;
    if document.get("schema_version") != Some(&serde_json::json!(1))
        || document.get("provider") != Some(&serde_json::json!("bailian"))
        || document.get("base_url") != Some(&serde_json::json!(PRODUCTION_BASE_URL))
        || document.get("verified_at") != Some(&serde_json::json!("2026-07-31"))
    {
        return Err(ModelServiceError::new(
            ModelServiceErrorCode::ConfigurationInvalid,
            false,
        ));
    }
    Ok(())
}

fn map_storage_error(_error: SecureStoreError) -> ModelServiceError {
    ModelServiceError::new(ModelServiceErrorCode::StorageUnavailable, false)
}

fn map_transport_error(error: reqwest::Error) -> ModelServiceError {
    if error.is_timeout() {
        ModelServiceError::new(ModelServiceErrorCode::TimedOut, true)
    } else {
        ModelServiceError::new(ModelServiceErrorCode::TransportUnavailable, true)
    }
}

fn map_http_status(status: StatusCode) -> ModelServiceError {
    match status {
        StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN => {
            ModelServiceError::new(ModelServiceErrorCode::AuthenticationRejected, false)
        }
        StatusCode::TOO_MANY_REQUESTS => {
            ModelServiceError::new(ModelServiceErrorCode::RateLimited, true)
        }
        StatusCode::PAYMENT_REQUIRED => {
            ModelServiceError::new(ModelServiceErrorCode::QuotaExhausted, false)
        }
        StatusCode::NOT_FOUND | StatusCode::BAD_REQUEST => {
            ModelServiceError::new(ModelServiceErrorCode::ModelUnavailable, false)
        }
        status if status.is_server_error() => {
            ModelServiceError::new(ModelServiceErrorCode::TransportUnavailable, true)
        }
        _ => ModelServiceError::new(ModelServiceErrorCode::InvalidResponse, false),
    }
}

fn bounded_quota_header(response: &reqwest::Response, name: &str) -> Option<u64> {
    response
        .headers()
        .get(name)?
        .to_str()
        .ok()?
        .parse::<u64>()
        .ok()
        .filter(|value| *value <= 1_000_000_000_000)
}

pub(crate) type ProductionModelServiceSettings = ModelServiceSettings<AppDataSecretStore>;

pub(crate) fn initialize_production_model_service_settings(
    app_data_directory: &Path,
) -> Result<ProductionModelServiceSettings, ModelServiceError> {
    let directory = app_data_directory.join("model-services");
    let script_store =
        AppDataSecretStore::new(&directory, SCRIPT_CREDENTIAL_FILE).map_err(map_storage_error)?;
    let video_store =
        AppDataSecretStore::new(&directory, VIDEO_CREDENTIAL_FILE).map_err(map_storage_error)?;
    let client = Client::builder()
        .connect_timeout(CONNECTION_TIMEOUT)
        .timeout(CONNECTION_TIMEOUT)
        .https_only(true)
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| ModelServiceError::new(ModelServiceErrorCode::ConfigurationInvalid, false))?;
    ModelServiceSettings::new(script_store, video_store, client, PRODUCTION_BASE_URL)
}

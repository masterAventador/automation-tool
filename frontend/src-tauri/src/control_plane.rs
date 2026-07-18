use std::error::Error;
use std::fmt::{Display, Formatter};
use std::time::Duration;

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use reqwest::header::{ACCEPT, AUTHORIZATION, CACHE_CONTROL, CONTENT_TYPE};
use reqwest::redirect::Policy;
use serde::{Deserialize, Serialize};
use time::format_description::well_known::Rfc3339;
use time::{Duration as TimeDuration, OffsetDateTime, UtcOffset};
use uuid::Variant;
use zeroize::Zeroizing;

use crate::device_credentials::{
    DeviceCredentialErrorCode, DeviceCredentialVault, StoredDeviceCredential,
};
use crate::device_identity::ProductionDeviceIdentity;
use crate::secure_store::SecretStore;

const LOCAL_CONTROL_PLANE_ORIGIN: &str = "http://127.0.0.1:8765";
const REQUEST_ID_HEADER: &str = "x-request-id";
const IDEMPOTENCY_KEY_HEADER: &str = "idempotency-key";
const MAX_RESPONSE_LENGTH: usize = 64 * 1024;

#[derive(Clone, Copy)]
enum ControlPlaneOperation {
    GetSystemHealth,
    GetCurrentInstallationAccess,
    IssueInstallationRegistrationChallenge,
    CompleteInstallationRegistration,
    RotateDeviceCredential,
    RevokeDeviceCredential,
    ExchangeDeviceSession,
    CreateTask,
    ListTasks,
    GetTask,
}

impl ControlPlaneOperation {
    fn method(self) -> &'static str {
        match self {
            Self::GetSystemHealth
            | Self::GetCurrentInstallationAccess
            | Self::ListTasks
            | Self::GetTask => "GET",
            Self::IssueInstallationRegistrationChallenge
            | Self::CompleteInstallationRegistration
            | Self::RotateDeviceCredential
            | Self::RevokeDeviceCredential
            | Self::ExchangeDeviceSession
            | Self::CreateTask => "POST",
        }
    }

    fn path(self) -> &'static str {
        match self {
            Self::GetSystemHealth => "/api/v1/health",
            Self::GetCurrentInstallationAccess => "/api/v1/installations/current",
            Self::IssueInstallationRegistrationChallenge => {
                "/api/v1/installations/registration-challenges"
            }
            Self::CompleteInstallationRegistration => "/api/v1/installations",
            Self::RotateDeviceCredential => "/api/v1/device-credentials/rotations",
            Self::RevokeDeviceCredential => "/api/v1/device-credentials/revocations",
            Self::ExchangeDeviceSession => "/api/v1/device-sessions",
            Self::CreateTask => "/api/v1/tasks",
            Self::ListTasks => "/api/v1/tasks",
            Self::GetTask => "/api/v1/tasks/{task_id}",
        }
    }

    fn success_status(self) -> u16 {
        match self {
            Self::GetSystemHealth
            | Self::GetCurrentInstallationAccess
            | Self::RevokeDeviceCredential
            | Self::ListTasks
            | Self::GetTask => 200,
            Self::IssueInstallationRegistrationChallenge
            | Self::CompleteInstallationRegistration
            | Self::RotateDeviceCredential
            | Self::ExchangeDeviceSession
            | Self::CreateTask => 201,
        }
    }

    fn accepts_status(self, status: u16) -> bool {
        status == self.success_status() || matches!(self, Self::CreateTask) && status == 200
    }

    fn outcome_is_uncertain_on_transport_failure(self) -> bool {
        matches!(
            self,
            Self::CompleteInstallationRegistration
                | Self::RotateDeviceCredential
                | Self::RevokeDeviceCredential
                | Self::ExchangeDeviceSession
        )
    }
}

#[derive(Clone, Copy)]
enum ControlPlaneRequestTarget<'a> {
    TaskList { cursor: Option<&'a str>, limit: u16 },
    TaskDetail(&'a str),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ControlPlaneErrorCode {
    TransportUnavailable,
    ProtocolInvalid,
    RequestRejected,
    InstallationAccessDenied,
    CredentialMissing,
    IdentityUnavailable,
    StorageUnavailable,
    OutcomeUncertain,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ControlPlaneError {
    code: ControlPlaneErrorCode,
    retryable: bool,
}

impl ControlPlaneError {
    fn new(code: ControlPlaneErrorCode, retryable: bool) -> Self {
        Self { code, retryable }
    }

    pub fn retryable(&self) -> bool {
        self.retryable
    }

    pub fn code(&self) -> ControlPlaneErrorCode {
        self.code
    }
}

impl Display for ControlPlaneError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("Control Plane request failed")
    }
}

impl Error for ControlPlaneError {}

#[derive(Clone)]
struct ResponseMetadata {
    status: u16,
    request_id: Option<String>,
    content_type: Option<String>,
    cache_control: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HealthResponse {
    service: String,
    status: String,
    version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct InstallationAccessResponse {
    installation_id: String,
    status: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ControlPlaneHealth {
    status: &'static str,
    service_version: String,
}

impl ControlPlaneHealth {
    #[cfg(test)]
    fn service_version(&self) -> &str {
        &self.service_version
    }
}

pub struct ControlPlaneClient {
    client: reqwest::Client,
}

impl ControlPlaneClient {
    pub fn local() -> Result<Self, ControlPlaneError> {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(3))
            .timeout(Duration::from_secs(10))
            .redirect(Policy::none())
            .no_proxy()
            .user_agent("automation-tool-desktop/0.1")
            .build()
            .map_err(|_| {
                ControlPlaneError::new(ControlPlaneErrorCode::TransportUnavailable, true)
            })?;
        Ok(Self { client })
    }

    pub async fn check_health(&self) -> Result<ControlPlaneHealth, ControlPlaneError> {
        let body = self
            .execute(
                ControlPlaneOperation::GetSystemHealth,
                None,
                None,
                None,
                None,
            )
            .await?;
        parse_health_response(&body)
    }

    pub async fn check_installation_access_if_registered<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
    ) -> Result<(), ControlPlaneError>
    where
        S: SecretStore,
    {
        if vault.load().map_err(map_vault_error)?.is_none() {
            return Ok(());
        }
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let body = self
            .execute(
                ControlPlaneOperation::GetCurrentInstallationAccess,
                Some(session.token()),
                None,
                None,
                None,
            )
            .await?;
        parse_installation_access(&body)
    }

    pub async fn register_installation<S>(
        &self,
        bootstrap: &DemoBootstrap,
        identity: &ProductionDeviceIdentity,
        vault: &DeviceCredentialVault<S>,
    ) -> Result<InstallationRegistration, ControlPlaneError>
    where
        S: SecretStore,
    {
        if vault.load().map_err(map_vault_error)?.is_some() {
            return Err(ControlPlaneError::new(
                ControlPlaneErrorCode::RequestRejected,
                false,
            ));
        }
        let challenge_request = serde_json::to_value(RegistrationChallengeRequest {
            environment_id: bootstrap.environment_id(),
            device_public_key: URL_SAFE_NO_PAD.encode(identity.public_key()),
        })
        .map_err(|_| ControlPlaneError::new(ControlPlaneErrorCode::ProtocolInvalid, false))?;
        let challenge_body = self
            .execute(
                ControlPlaneOperation::IssueInstallationRegistrationChallenge,
                Some(bootstrap.token()),
                Some(&challenge_request),
                None,
                None,
            )
            .await?;
        let challenge = parse_registration_challenge(&challenge_body)?;
        let signing_payload = decode_canonical_base64url(&challenge.signing_payload, 1, 2048)?;
        let signature = identity.sign(&signing_payload).map_err(|_| {
            ControlPlaneError::new(ControlPlaneErrorCode::IdentityUnavailable, false)
        })?;
        let completion_request = serde_json::to_value(InstallationRegistrationRequest {
            challenge_id: challenge.challenge_id,
            environment_id: bootstrap.environment_id(),
            signing_payload: challenge.signing_payload,
            signature: URL_SAFE_NO_PAD.encode(signature),
        })
        .map_err(|_| ControlPlaneError::new(ControlPlaneErrorCode::ProtocolInvalid, false))?;
        let registration_body = self
            .execute(
                ControlPlaneOperation::CompleteInstallationRegistration,
                Some(bootstrap.token()),
                Some(&completion_request),
                None,
                None,
            )
            .await?;
        let registered = parse_installation_registration(&registration_body)?;
        let credential = Zeroizing::new(registered.device_credential.credential);
        vault
            .replace(&credential)
            .map_err(|error| match error.code() {
                DeviceCredentialErrorCode::InvalidCredential => {
                    ControlPlaneError::new(ControlPlaneErrorCode::ProtocolInvalid, false)
                }
                DeviceCredentialErrorCode::SecureStoreUnavailable
                | DeviceCredentialErrorCode::CorruptStoredCredential => {
                    ControlPlaneError::new(ControlPlaneErrorCode::OutcomeUncertain, false)
                }
            })?;
        Ok(InstallationRegistration {
            installation_id: registered.installation_id,
            credential_version: registered.device_credential.version,
        })
    }

    pub async fn rotate_device_credential<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
    ) -> Result<u32, ControlPlaneError>
    where
        S: SecretStore,
    {
        let credential = required_credential(vault)?;
        let response_body = self
            .execute(
                ControlPlaneOperation::RotateDeviceCredential,
                Some(credential.as_str()),
                None,
                None,
                None,
            )
            .await?;
        let rotated = parse_rotated_credential(&response_body)?;
        let replacement = Zeroizing::new(rotated.credential);
        vault
            .replace(&replacement)
            .map_err(|error| match error.code() {
                DeviceCredentialErrorCode::InvalidCredential => {
                    ControlPlaneError::new(ControlPlaneErrorCode::ProtocolInvalid, false)
                }
                DeviceCredentialErrorCode::SecureStoreUnavailable
                | DeviceCredentialErrorCode::CorruptStoredCredential => {
                    ControlPlaneError::new(ControlPlaneErrorCode::OutcomeUncertain, false)
                }
            })?;
        Ok(rotated.version)
    }

    pub async fn exchange_device_session<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        capability: DeviceSessionCapability,
    ) -> Result<DeviceSession, ControlPlaneError>
    where
        S: SecretStore,
    {
        let credential = required_credential(vault)?;
        let request_body = serde_json::to_value(DeviceSessionRequest { capability })
            .map_err(|_| ControlPlaneError::new(ControlPlaneErrorCode::ProtocolInvalid, false))?;
        let response_body = self
            .execute(
                ControlPlaneOperation::ExchangeDeviceSession,
                Some(credential.as_str()),
                Some(&request_body),
                None,
                None,
            )
            .await?;
        parse_device_session(&response_body, capability)
    }

    pub async fn revoke_device_credential<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
    ) -> Result<u32, ControlPlaneError>
    where
        S: SecretStore,
    {
        let credential = required_credential(vault)?;
        let response_body = self
            .execute(
                ControlPlaneOperation::RevokeDeviceCredential,
                Some(credential.as_str()),
                None,
                None,
                None,
            )
            .await?;
        let revoked = parse_revoked_credential(&response_body)?;
        vault
            .delete()
            .map_err(|_| ControlPlaneError::new(ControlPlaneErrorCode::OutcomeUncertain, false))?;
        Ok(revoked.version)
    }

    pub async fn create_task<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        idempotency_key: &str,
    ) -> Result<CreatedTask, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_idempotency_key(idempotency_key)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let request_body = serde_json::json!({});
        let response_body = self
            .execute(
                ControlPlaneOperation::CreateTask,
                Some(session.token()),
                Some(&request_body),
                Some(idempotency_key),
                None,
            )
            .await?;
        parse_created_task(&response_body)
    }

    pub async fn list_tasks<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        cursor: Option<&str>,
        limit: u16,
    ) -> Result<TaskListPage, ControlPlaneError>
    where
        S: SecretStore,
    {
        if !(1..=100).contains(&limit) {
            return Err(protocol_invalid());
        }
        if let Some(value) = cursor {
            require_list_cursor(value)?;
        }
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response_body = self
            .execute(
                ControlPlaneOperation::ListTasks,
                Some(session.token()),
                None,
                None,
                Some(ControlPlaneRequestTarget::TaskList { cursor, limit }),
            )
            .await?;
        parse_task_list(&response_body)
    }

    pub async fn get_task<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        task_id: &str,
    ) -> Result<TaskSnapshot, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(task_id)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response_body = self
            .execute(
                ControlPlaneOperation::GetTask,
                Some(session.token()),
                None,
                None,
                Some(ControlPlaneRequestTarget::TaskDetail(task_id)),
            )
            .await?;
        parse_task_snapshot_body(&response_body)
    }

    async fn execute(
        &self,
        operation: ControlPlaneOperation,
        bearer: Option<&str>,
        body: Option<&serde_json::Value>,
        idempotency_key: Option<&str>,
        target: Option<ControlPlaneRequestTarget<'_>>,
    ) -> Result<Zeroizing<Vec<u8>>, ControlPlaneError> {
        let request_id = new_request_id()?;
        let path = request_path(operation, target)?;
        let url = format!("{LOCAL_CONTROL_PLANE_ORIGIN}{path}");
        let mut request = match operation.method() {
            "GET" => self.client.get(url),
            "POST" => self.client.post(url),
            _ => {
                return Err(ControlPlaneError::new(
                    ControlPlaneErrorCode::ProtocolInvalid,
                    false,
                ));
            }
        }
        .header(ACCEPT, "application/json")
        .header(REQUEST_ID_HEADER, &request_id);
        if let Some(credential) = bearer {
            request = request.header(AUTHORIZATION, format!("Bearer {credential}"));
        }
        if let Some(key) = idempotency_key {
            request = request.header(IDEMPOTENCY_KEY_HEADER, key);
        }
        if let Some(payload) = body {
            request = request.json(payload);
        }

        let mut response = request
            .send()
            .await
            .map_err(|_| transport_error(operation))?;
        let metadata = ResponseMetadata {
            status: response.status().as_u16(),
            request_id: header_text(response.headers(), REQUEST_ID_HEADER),
            content_type: header_text(response.headers(), CONTENT_TYPE.as_str()),
            cache_control: header_text(response.headers(), CACHE_CONTROL.as_str()),
        };
        validate_response_metadata(operation, &request_id, &metadata)?;

        if response
            .content_length()
            .is_some_and(|length| length > MAX_RESPONSE_LENGTH as u64)
        {
            return Err(ControlPlaneError::new(
                ControlPlaneErrorCode::ProtocolInvalid,
                false,
            ));
        }
        let mut response_body = Zeroizing::new(Vec::new());
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| transport_error(operation))?
        {
            if response_body.len() + chunk.len() > MAX_RESPONSE_LENGTH {
                return Err(ControlPlaneError::new(
                    ControlPlaneErrorCode::ProtocolInvalid,
                    false,
                ));
            }
            response_body.extend_from_slice(&chunk);
        }
        Ok(response_body)
    }
}

fn request_path(
    operation: ControlPlaneOperation,
    target: Option<ControlPlaneRequestTarget<'_>>,
) -> Result<String, ControlPlaneError> {
    match (operation, target) {
        (
            ControlPlaneOperation::ListTasks,
            Some(ControlPlaneRequestTarget::TaskList { cursor, limit }),
        ) if (1..=100).contains(&limit) => {
            let mut path = format!("{}?limit={limit}", operation.path());
            if let Some(value) = cursor {
                require_list_cursor(value)?;
                path.push_str("&cursor=");
                path.push_str(value);
            }
            Ok(path)
        }
        (ControlPlaneOperation::GetTask, Some(ControlPlaneRequestTarget::TaskDetail(task_id))) => {
            require_canonical_uuid_v4(task_id)?;
            Ok(format!("/api/v1/tasks/{task_id}"))
        }
        (ControlPlaneOperation::ListTasks | ControlPlaneOperation::GetTask, _) | (_, Some(_)) => {
            Err(protocol_invalid())
        }
        (_, None) => Ok(operation.path().to_owned()),
    }
}

fn transport_error(operation: ControlPlaneOperation) -> ControlPlaneError {
    if operation.outcome_is_uncertain_on_transport_failure() {
        ControlPlaneError::new(ControlPlaneErrorCode::OutcomeUncertain, false)
    } else {
        ControlPlaneError::new(ControlPlaneErrorCode::TransportUnavailable, true)
    }
}

fn required_credential<S>(
    vault: &DeviceCredentialVault<S>,
) -> Result<StoredDeviceCredential, ControlPlaneError>
where
    S: SecretStore,
{
    vault
        .load()
        .map_err(map_vault_error)?
        .ok_or_else(|| ControlPlaneError::new(ControlPlaneErrorCode::CredentialMissing, false))
}

fn map_vault_error(error: crate::device_credentials::DeviceCredentialError) -> ControlPlaneError {
    let code = match error.code() {
        DeviceCredentialErrorCode::SecureStoreUnavailable => {
            ControlPlaneErrorCode::StorageUnavailable
        }
        DeviceCredentialErrorCode::InvalidCredential
        | DeviceCredentialErrorCode::CorruptStoredCredential => {
            ControlPlaneErrorCode::ProtocolInvalid
        }
    };
    ControlPlaneError::new(code, false)
}

fn header_text(headers: &reqwest::header::HeaderMap, name: &str) -> Option<String> {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned)
}

fn new_request_id() -> Result<String, ControlPlaneError> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes)
        .map_err(|_| ControlPlaneError::new(ControlPlaneErrorCode::TransportUnavailable, true))?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(uuid::Uuid::from_bytes(bytes).hyphenated().to_string())
}

fn validate_response_metadata(
    operation: ControlPlaneOperation,
    expected_request_id: &str,
    metadata: &ResponseMetadata,
) -> Result<(), ControlPlaneError> {
    let content_type_is_json = metadata
        .content_type
        .as_deref()
        .and_then(|value| value.split(';').next())
        .is_some_and(|value| value.trim().eq_ignore_ascii_case("application/json"));
    let cache_control_is_private = metadata
        .cache_control
        .as_deref()
        .is_some_and(|value| value.split(',').any(|part| part.trim() == "no-store"));
    if !operation.accepts_status(metadata.status)
        || metadata.request_id.as_deref() != Some(expected_request_id)
        || !content_type_is_json
        || !cache_control_is_private
    {
        let code = if operation.accepts_status(metadata.status) {
            ControlPlaneErrorCode::ProtocolInvalid
        } else if metadata.status == 401
            && matches!(
                operation,
                ControlPlaneOperation::ExchangeDeviceSession
                    | ControlPlaneOperation::GetCurrentInstallationAccess
                    | ControlPlaneOperation::CreateTask
                    | ControlPlaneOperation::ListTasks
                    | ControlPlaneOperation::GetTask
            )
        {
            ControlPlaneErrorCode::InstallationAccessDenied
        } else {
            ControlPlaneErrorCode::RequestRejected
        };
        return Err(ControlPlaneError::new(code, metadata.status >= 500));
    }
    Ok(())
}

fn parse_health_response(body: &[u8]) -> Result<ControlPlaneHealth, ControlPlaneError> {
    let response: HealthResponse = serde_json::from_slice(body)
        .map_err(|_| ControlPlaneError::new(ControlPlaneErrorCode::ProtocolInvalid, false))?;
    if response.service != "control-plane"
        || response.status != "ok"
        || response.version.is_empty()
        || response.version.len() > 64
    {
        return Err(ControlPlaneError::new(
            ControlPlaneErrorCode::ProtocolInvalid,
            false,
        ));
    }
    Ok(ControlPlaneHealth {
        status: "available",
        service_version: response.version,
    })
}

fn parse_installation_access(body: &[u8]) -> Result<(), ControlPlaneError> {
    let response: InstallationAccessResponse = parse_exact_json(body)?;
    require_canonical_uuid_v4(&response.installation_id)?;
    if response.status != "active" {
        return Err(protocol_invalid());
    }
    Ok(())
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TaskSnapshotResponse {
    task_id: String,
    status: String,
    revision: u32,
    created_at: String,
    updated_at: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TaskListResponse {
    items: Vec<TaskSnapshotResponse>,
    next_cursor: Option<String>,
}

pub struct CreatedTask {
    task_id: String,
    status: String,
    revision: u32,
}

impl CreatedTask {
    pub fn task_id(&self) -> &str {
        &self.task_id
    }

    pub fn status(&self) -> &str {
        &self.status
    }

    pub fn revision(&self) -> u32 {
        self.revision
    }
}

pub struct TaskSnapshot {
    task_id: String,
    status: String,
    revision: u32,
    updated_at: OffsetDateTime,
}

impl TaskSnapshot {
    pub fn task_id(&self) -> &str {
        &self.task_id
    }

    pub fn status(&self) -> &str {
        &self.status
    }

    pub fn revision(&self) -> u32 {
        self.revision
    }
}

pub struct TaskListPage {
    items: Vec<TaskSnapshot>,
    next_cursor: Option<String>,
}

impl TaskListPage {
    pub fn items(&self) -> &[TaskSnapshot] {
        &self.items
    }

    pub fn next_cursor(&self) -> Option<&str> {
        self.next_cursor.as_deref()
    }
}

fn require_idempotency_key(value: &str) -> Result<(), ControlPlaneError> {
    let mut bytes = value.bytes();
    let first = bytes.next();
    if value.len() > 128
        || !first.is_some_and(|byte| byte.is_ascii_alphanumeric())
        || !bytes.all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'/' | b'-')
        })
    {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn require_list_cursor(value: &str) -> Result<(), ControlPlaneError> {
    if value.is_empty()
        || value.len() > 256
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn valid_task_status(value: &str) -> bool {
    matches!(
        value,
        "draft"
            | "validating"
            | "awaiting_platform_login"
            | "awaiting_confirmation"
            | "queued"
            | "offered"
            | "running"
            | "paused"
            | "awaiting_human"
            | "cancelling"
            | "succeeded"
            | "partially_succeeded"
            | "failed"
            | "cancelled"
            | "rejected"
            | "outcome_uncertain"
    )
}

fn parse_task_snapshot(response: TaskSnapshotResponse) -> Result<TaskSnapshot, ControlPlaneError> {
    require_canonical_uuid_v4(&response.task_id)?;
    let created_at = require_bounded_timestamp(&response.created_at)?;
    let updated_at = require_bounded_timestamp(&response.updated_at)?;
    if !valid_task_status(&response.status) || response.revision == 0 || updated_at < created_at {
        return Err(protocol_invalid());
    }
    Ok(TaskSnapshot {
        task_id: response.task_id,
        status: response.status,
        revision: response.revision,
        updated_at,
    })
}

fn parse_task_snapshot_body(body: &[u8]) -> Result<TaskSnapshot, ControlPlaneError> {
    parse_task_snapshot(parse_exact_json(body)?)
}

fn parse_created_task(body: &[u8]) -> Result<CreatedTask, ControlPlaneError> {
    let snapshot = parse_task_snapshot_body(body)?;
    if snapshot.status != "draft" || snapshot.revision != 1 {
        return Err(protocol_invalid());
    }
    Ok(CreatedTask {
        task_id: snapshot.task_id,
        status: snapshot.status,
        revision: snapshot.revision,
    })
}

fn parse_task_list(body: &[u8]) -> Result<TaskListPage, ControlPlaneError> {
    let response: TaskListResponse = parse_exact_json(body)?;
    if response.items.len() > 100
        || response.items.is_empty() && response.next_cursor.is_some()
        || response
            .next_cursor
            .as_deref()
            .is_some_and(|cursor| require_list_cursor(cursor).is_err())
    {
        return Err(protocol_invalid());
    }
    let items = response
        .items
        .into_iter()
        .map(parse_task_snapshot)
        .collect::<Result<Vec<_>, _>>()?;
    for pair in items.windows(2) {
        let previous = &pair[0];
        let current = &pair[1];
        if previous.updated_at < current.updated_at
            || previous.updated_at == current.updated_at
                && previous.task_id.as_str() <= current.task_id.as_str()
        {
            return Err(protocol_invalid());
        }
    }
    Ok(TaskListPage {
        items,
        next_cursor: response.next_cursor,
    })
}

pub struct DemoBootstrap {
    token: Zeroizing<String>,
    environment_id: String,
}

impl DemoBootstrap {
    pub fn new(token: String, environment_id: String) -> Result<Self, ControlPlaneError> {
        let valid_environment = !environment_id.is_empty()
            && environment_id.len() <= 64
            && !environment_id.starts_with('-')
            && !environment_id.ends_with('-')
            && environment_id
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-');
        let token_segments = token.split('.').collect::<Vec<_>>();
        let valid_token = token.len() <= 4096
            && token_segments.len() == 3
            && token_segments[0] == "atb1"
            && token_segments[1..].iter().all(|segment| {
                !segment.is_empty()
                    && segment
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
            });
        if !valid_environment || !valid_token {
            return Err(ControlPlaneError::new(
                ControlPlaneErrorCode::ProtocolInvalid,
                false,
            ));
        }
        Ok(Self {
            token: Zeroizing::new(token),
            environment_id,
        })
    }

    fn token(&self) -> &str {
        self.token.as_str()
    }

    fn environment_id(&self) -> &str {
        &self.environment_id
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RegistrationChallengeRequest<'a> {
    environment_id: &'a str,
    device_public_key: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RegistrationChallengeResponse {
    challenge_id: String,
    signing_payload: String,
    expires_at: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct InstallationRegistrationRequest<'a> {
    challenge_id: String,
    environment_id: &'a str,
    signing_payload: String,
    signature: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct InstallationRegistrationResponse {
    installation_id: String,
    status: String,
    revision: u32,
    device_credential: IssuedCredentialResponse,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct IssuedCredentialResponse {
    credential: String,
    version: u32,
    scope: String,
}

pub struct InstallationRegistration {
    installation_id: String,
    credential_version: u32,
}

impl InstallationRegistration {
    pub fn installation_id(&self) -> &str {
        &self.installation_id
    }

    pub fn credential_version(&self) -> u32 {
        self.credential_version
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RotatedCredentialResponse {
    credential: String,
    version: u32,
    scope: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RevokedCredentialResponse {
    version: u32,
    status: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub enum DeviceSessionCapability {
    #[serde(rename = "app.control-plane")]
    AppControlPlane,
    #[serde(rename = "executor.connect")]
    ExecutorConnect,
}

impl DeviceSessionCapability {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::AppControlPlane => "app.control-plane",
            Self::ExecutorConnect => "executor.connect",
        }
    }
}

#[derive(Serialize)]
struct DeviceSessionRequest {
    capability: DeviceSessionCapability,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DeviceSessionResponse {
    session_token: String,
    capability: DeviceSessionCapability,
    issued_at: String,
    expires_at: String,
}

pub struct DeviceSession {
    token: Zeroizing<String>,
    capability: DeviceSessionCapability,
}

impl DeviceSession {
    pub fn token(&self) -> &str {
        self.token.as_str()
    }

    pub fn capability(&self) -> DeviceSessionCapability {
        self.capability
    }
}

fn parse_registration_challenge(
    body: &[u8],
) -> Result<RegistrationChallengeResponse, ControlPlaneError> {
    let response: RegistrationChallengeResponse = parse_exact_json(body)?;
    require_canonical_uuid_v4(&response.challenge_id)?;
    decode_canonical_base64url(&response.signing_payload, 1, 2048)?;
    require_bounded_timestamp(&response.expires_at)?;
    Ok(response)
}

fn parse_installation_registration(
    body: &[u8],
) -> Result<InstallationRegistrationResponse, ControlPlaneError> {
    let response: InstallationRegistrationResponse = parse_exact_json(body)?;
    require_canonical_uuid_v4(&response.installation_id)?;
    if response.status != "active"
        || response.revision != 1
        || response.device_credential.version != 1
        || response.device_credential.scope != "device.session.exchange"
    {
        return Err(protocol_invalid());
    }
    require_opaque_bearer(&response.device_credential.credential, "atdc1")?;
    Ok(response)
}

fn parse_rotated_credential(body: &[u8]) -> Result<RotatedCredentialResponse, ControlPlaneError> {
    let response: RotatedCredentialResponse = parse_exact_json(body)?;
    if response.version < 2 || response.scope != "device.session.exchange" {
        return Err(protocol_invalid());
    }
    require_opaque_bearer(&response.credential, "atdc1")?;
    Ok(response)
}

fn parse_revoked_credential(body: &[u8]) -> Result<RevokedCredentialResponse, ControlPlaneError> {
    let response: RevokedCredentialResponse = parse_exact_json(body)?;
    if response.version == 0 || response.status != "revoked" {
        return Err(protocol_invalid());
    }
    Ok(response)
}

fn parse_device_session(
    body: &[u8],
    expected_capability: DeviceSessionCapability,
) -> Result<DeviceSession, ControlPlaneError> {
    let response: DeviceSessionResponse = parse_exact_json(body)?;
    if response.capability != expected_capability {
        return Err(protocol_invalid());
    }
    require_opaque_bearer(&response.session_token, "atds1")?;
    let issued_at = require_bounded_timestamp(&response.issued_at)?;
    let expires_at = require_bounded_timestamp(&response.expires_at)?;
    let lifetime = expires_at - issued_at;
    if lifetime <= TimeDuration::ZERO || lifetime > TimeDuration::minutes(5) {
        return Err(protocol_invalid());
    }
    Ok(DeviceSession {
        token: Zeroizing::new(response.session_token),
        capability: response.capability,
    })
}

fn parse_exact_json<T>(body: &[u8]) -> Result<T, ControlPlaneError>
where
    T: for<'de> Deserialize<'de>,
{
    serde_json::from_slice(body).map_err(|_| protocol_invalid())
}

fn require_canonical_uuid_v4(value: &str) -> Result<(), ControlPlaneError> {
    let parsed = uuid::Uuid::parse_str(value).map_err(|_| protocol_invalid())?;
    if parsed.get_version_num() != 4
        || parsed.get_variant() != Variant::RFC4122
        || parsed.hyphenated().to_string() != value
    {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn decode_canonical_base64url(
    value: &str,
    minimum_length: usize,
    maximum_length: usize,
) -> Result<Zeroizing<Vec<u8>>, ControlPlaneError> {
    let decoded = URL_SAFE_NO_PAD
        .decode(value)
        .map_err(|_| protocol_invalid())?;
    if decoded.len() < minimum_length
        || decoded.len() > maximum_length
        || URL_SAFE_NO_PAD.encode(&decoded) != value
    {
        return Err(protocol_invalid());
    }
    Ok(Zeroizing::new(decoded))
}

fn require_opaque_bearer(value: &str, expected_prefix: &str) -> Result<(), ControlPlaneError> {
    if value.len() > 256 {
        return Err(protocol_invalid());
    }
    let mut segments = value.split('.');
    let (Some(prefix), Some(identifier), Some(secret), None) = (
        segments.next(),
        segments.next(),
        segments.next(),
        segments.next(),
    ) else {
        return Err(protocol_invalid());
    };
    if prefix != expected_prefix {
        return Err(protocol_invalid());
    }
    require_canonical_uuid_v4(identifier)?;
    let decoded = decode_canonical_base64url(secret, 32, 32)?;
    if decoded.len() != 32 {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn require_bounded_timestamp(value: &str) -> Result<OffsetDateTime, ControlPlaneError> {
    if value.is_empty() || value.len() > 64 {
        return Err(protocol_invalid());
    }
    let parsed = OffsetDateTime::parse(value, &Rfc3339).map_err(|_| protocol_invalid())?;
    if parsed.offset() != UtcOffset::UTC {
        return Err(protocol_invalid());
    }
    Ok(parsed)
}

fn protocol_invalid() -> ControlPlaneError {
    ControlPlaneError::new(ControlPlaneErrorCode::ProtocolInvalid, false)
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;
    use std::collections::HashSet;
    use std::error::Error;

    use base64::engine::general_purpose::URL_SAFE_NO_PAD;
    use base64::Engine;
    use zeroize::Zeroizing;

    use super::{
        new_request_id, parse_created_task, parse_device_session, parse_health_response,
        parse_installation_access, parse_installation_registration, parse_registration_challenge,
        parse_revoked_credential, parse_rotated_credential, parse_task_list,
        parse_task_snapshot_body, request_path, require_idempotency_key, require_list_cursor,
        required_credential, transport_error, validate_response_metadata, ControlPlaneErrorCode,
        ControlPlaneOperation, ControlPlaneRequestTarget, DemoBootstrap, DeviceSessionCapability,
        ResponseMetadata,
    };
    use crate::device_credentials::DeviceCredentialVault;
    use crate::secure_store::{SecretStore, SecureStoreError};

    const IDENTIFIER: &str = "f831a58a-a54c-4bd9-8f3e-0383c4df609d";

    struct MemorySecretStore {
        value: Option<Vec<u8>>,
        fail_load: Cell<bool>,
    }

    impl MemorySecretStore {
        fn with_value(value: &[u8]) -> Self {
            Self {
                value: Some(value.to_vec()),
                fail_load: Cell::new(false),
            }
        }
    }

    impl SecretStore for MemorySecretStore {
        fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, SecureStoreError> {
            if self.fail_load.get() {
                return Err(SecureStoreError::Unavailable);
            }
            Ok(self.value.clone().map(Zeroizing::new))
        }

        fn save(&self, _secret: &[u8]) -> Result<(), SecureStoreError> {
            Ok(())
        }

        fn delete(&self) -> Result<(), SecureStoreError> {
            Ok(())
        }
    }

    fn opaque_bearer(prefix: &str) -> String {
        format!(
            "{prefix}.{IDENTIFIER}.{}",
            URL_SAFE_NO_PAD.encode([7_u8; 32])
        )
    }

    fn bootstrap_error(
        result: Result<DemoBootstrap, super::ControlPlaneError>,
    ) -> super::ControlPlaneError {
        match result {
            Err(error) => error,
            Ok(_) => panic!("expected bootstrap input to fail"),
        }
    }

    fn credential_error(
        result: Result<crate::device_credentials::StoredDeviceCredential, super::ControlPlaneError>,
    ) -> super::ControlPlaneError {
        match result {
            Err(error) => error,
            Ok(_) => panic!("expected credential load to fail"),
        }
    }

    #[test]
    fn operations_are_a_closed_exact_allowlist() {
        let operations = [
            (
                ControlPlaneOperation::GetSystemHealth,
                "GET",
                "/api/v1/health",
                200,
            ),
            (
                ControlPlaneOperation::GetCurrentInstallationAccess,
                "GET",
                "/api/v1/installations/current",
                200,
            ),
            (
                ControlPlaneOperation::IssueInstallationRegistrationChallenge,
                "POST",
                "/api/v1/installations/registration-challenges",
                201,
            ),
            (
                ControlPlaneOperation::CompleteInstallationRegistration,
                "POST",
                "/api/v1/installations",
                201,
            ),
            (
                ControlPlaneOperation::RotateDeviceCredential,
                "POST",
                "/api/v1/device-credentials/rotations",
                201,
            ),
            (
                ControlPlaneOperation::RevokeDeviceCredential,
                "POST",
                "/api/v1/device-credentials/revocations",
                200,
            ),
            (
                ControlPlaneOperation::ExchangeDeviceSession,
                "POST",
                "/api/v1/device-sessions",
                201,
            ),
            (
                ControlPlaneOperation::CreateTask,
                "POST",
                "/api/v1/tasks",
                201,
            ),
            (
                ControlPlaneOperation::ListTasks,
                "GET",
                "/api/v1/tasks",
                200,
            ),
            (
                ControlPlaneOperation::GetTask,
                "GET",
                "/api/v1/tasks/{task_id}",
                200,
            ),
        ];

        for (operation, method, path, success_status) in operations {
            assert_eq!(operation.method(), method);
            assert_eq!(operation.path(), path);
            assert_eq!(operation.success_status(), success_status);
        }
    }

    #[test]
    fn task_query_targets_build_only_validated_fixed_paths() {
        let list_path = request_path(
            ControlPlaneOperation::ListTasks,
            Some(ControlPlaneRequestTarget::TaskList {
                cursor: Some("YWJj"),
                limit: 20,
            }),
        )
        .expect("valid list target");
        assert_eq!(list_path, "/api/v1/tasks?limit=20&cursor=YWJj");

        let detail_path = request_path(
            ControlPlaneOperation::GetTask,
            Some(ControlPlaneRequestTarget::TaskDetail(IDENTIFIER)),
        )
        .expect("valid detail target");
        assert_eq!(detail_path, format!("/api/v1/tasks/{IDENTIFIER}"));

        for invalid in [
            request_path(ControlPlaneOperation::ListTasks, None),
            request_path(
                ControlPlaneOperation::ListTasks,
                Some(ControlPlaneRequestTarget::TaskList {
                    cursor: None,
                    limit: 0,
                }),
            ),
            request_path(
                ControlPlaneOperation::GetTask,
                Some(ControlPlaneRequestTarget::TaskDetail("private-invalid")),
            ),
            request_path(
                ControlPlaneOperation::GetSystemHealth,
                Some(ControlPlaneRequestTarget::TaskDetail(IDENTIFIER)),
            ),
        ] {
            let error = invalid.expect_err("invalid target");
            assert_eq!(error.code(), ControlPlaneErrorCode::ProtocolInvalid);
        }
    }

    #[test]
    fn request_ids_are_distinct_canonical_uuid_v4_values() {
        let identifiers = (0..128)
            .map(|_| new_request_id().expect("system request ID"))
            .collect::<Vec<_>>();
        let unique = identifiers.iter().collect::<HashSet<_>>();

        assert_eq!(unique.len(), identifiers.len());
        for identifier in identifiers {
            let parsed = uuid::Uuid::parse_str(&identifier).expect("UUID request ID");
            assert_eq!(parsed.get_version_num(), 4);
            assert_eq!(parsed.hyphenated().to_string(), identifier);
        }
    }

    #[test]
    fn response_metadata_requires_matching_correlation_json_and_no_store() {
        let valid = ResponseMetadata {
            status: 200,
            request_id: Some("f831a58a-a54c-4bd9-8f3e-0383c4df609d".to_owned()),
            content_type: Some("application/json".to_owned()),
            cache_control: Some("no-store".to_owned()),
        };

        validate_response_metadata(
            ControlPlaneOperation::GetSystemHealth,
            "f831a58a-a54c-4bd9-8f3e-0383c4df609d",
            &valid,
        )
        .expect("valid metadata");

        for invalid in [
            ResponseMetadata {
                request_id: None,
                ..valid.clone()
            },
            ResponseMetadata {
                request_id: Some("8b68db87-5c97-43dd-ad0b-304506caaa03".to_owned()),
                ..valid.clone()
            },
            ResponseMetadata {
                content_type: Some("text/html".to_owned()),
                ..valid.clone()
            },
            ResponseMetadata {
                cache_control: None,
                ..valid.clone()
            },
            ResponseMetadata {
                status: 201,
                ..valid.clone()
            },
        ] {
            assert!(validate_response_metadata(
                ControlPlaneOperation::GetSystemHealth,
                "f831a58a-a54c-4bd9-8f3e-0383c4df609d",
                &invalid,
            )
            .is_err());
        }

        let access_denied = validate_response_metadata(
            ControlPlaneOperation::GetCurrentInstallationAccess,
            "f831a58a-a54c-4bd9-8f3e-0383c4df609d",
            &ResponseMetadata {
                status: 401,
                ..valid.clone()
            },
        )
        .expect_err("401 access denial");
        assert_eq!(
            access_denied.code(),
            ControlPlaneErrorCode::InstallationAccessDenied
        );
        assert!(!access_denied.retryable());

        let unavailable = validate_response_metadata(
            ControlPlaneOperation::GetCurrentInstallationAccess,
            "f831a58a-a54c-4bd9-8f3e-0383c4df609d",
            &ResponseMetadata {
                status: 503,
                ..valid
            },
        )
        .expect_err("503 access dependency failure");
        assert_eq!(unavailable.code(), ControlPlaneErrorCode::RequestRejected);
        assert!(unavailable.retryable());

        for status in [200, 201] {
            validate_response_metadata(
                ControlPlaneOperation::CreateTask,
                "f831a58a-a54c-4bd9-8f3e-0383c4df609d",
                &ResponseMetadata {
                    status,
                    request_id: Some("f831a58a-a54c-4bd9-8f3e-0383c4df609d".to_owned()),
                    content_type: Some("application/json".to_owned()),
                    cache_control: Some("no-store".to_owned()),
                },
            )
            .expect("task creation or replay response");
        }
    }

    #[test]
    fn health_response_is_exact_and_rejects_unknown_or_malformed_fields() {
        let health = parse_health_response(
            br#"{"service":"control-plane","status":"ok","version":"0.1.0"}"#,
        )
        .expect("valid health");
        assert_eq!(health.service_version(), "0.1.0");

        for invalid in [
            br#"{"service":"other","status":"ok","version":"0.1.0"}"#.as_slice(),
            br#"{"service":"control-plane","status":"ok","version":""}"#.as_slice(),
            br#"{"service":"control-plane","status":"ok","version":"0.1.0","extra":true}"#
                .as_slice(),
            b"private-invalid-json".as_slice(),
        ] {
            assert!(parse_health_response(invalid).is_err());
        }
    }

    #[test]
    fn bootstrap_input_is_bounded_and_errors_never_reflect_private_values() {
        DemoBootstrap::new(
            "atb1.cGF5bG9hZA.c2lnbmF0dXJl".to_owned(),
            "demo-cn-1".to_owned(),
        )
        .expect("valid bootstrap shape");

        for (token, environment_id) in [
            ("private-bootstrap", "demo-cn-1"),
            ("atb1.payload.signature", "INVALID"),
            ("atb1.payload.signature", "-invalid"),
            ("atb1.payload.signature", "invalid-"),
            ("atb1.payload.signature", ""),
        ] {
            let error = bootstrap_error(DemoBootstrap::new(
                token.to_owned(),
                environment_id.to_owned(),
            ));
            assert_eq!(error.code(), ControlPlaneErrorCode::ProtocolInvalid);
            assert_eq!(error.to_string(), "Control Plane request failed");
            assert!(!error.to_string().contains(token));
            assert!(error.source().is_none());
        }
    }

    #[test]
    fn response_parsers_accept_only_exact_bounded_contracts() {
        let device_value = opaque_bearer("atdc1");
        let session_value = opaque_bearer("atds1");
        let challenge = serde_json::json!({
            "challengeId": IDENTIFIER,
            "signingPayload": URL_SAFE_NO_PAD.encode(b"signing-payload"),
            "expiresAt": "2026-07-18T03:00:00Z"
        });
        parse_registration_challenge(&serde_json::to_vec(&challenge).expect("challenge JSON"))
            .expect("valid challenge");

        let registration = serde_json::json!({
            "installationId": IDENTIFIER,
            "status": "active",
            "revision": 1,
            "deviceCredential": {
                "credential": device_value,
                "version": 1,
                "scope": "device.session.exchange"
            }
        });
        parse_installation_registration(
            &serde_json::to_vec(&registration).expect("registration JSON"),
        )
        .expect("valid registration");

        let rotation = serde_json::json!({
            "credential": opaque_bearer("atdc1"),
            "version": 2,
            "scope": "device.session.exchange"
        });
        parse_rotated_credential(&serde_json::to_vec(&rotation).expect("rotation JSON"))
            .expect("valid rotation");

        let revocation = serde_json::json!({"version": 2, "status": "revoked"});
        parse_revoked_credential(&serde_json::to_vec(&revocation).expect("revocation JSON"))
            .expect("valid revocation");

        let access = serde_json::json!({
            "installationId": IDENTIFIER,
            "status": "active"
        });
        parse_installation_access(&serde_json::to_vec(&access).expect("access JSON"))
            .expect("valid access");

        let session = serde_json::json!({
            "sessionToken": session_value,
            "capability": "executor.connect",
            "issuedAt": "2026-07-18T02:00:00+00:00",
            "expiresAt": "2026-07-18T02:05:00+00:00"
        });
        let parsed = parse_device_session(
            &serde_json::to_vec(&session).expect("session JSON"),
            DeviceSessionCapability::ExecutorConnect,
        )
        .expect("valid session");
        assert_eq!(
            parsed.capability(),
            DeviceSessionCapability::ExecutorConnect
        );

        let task = serde_json::json!({
            "taskId": IDENTIFIER,
            "status": "draft",
            "revision": 1,
            "createdAt": "2026-07-18T02:00:00Z",
            "updatedAt": "2026-07-18T02:00:00Z"
        });
        let parsed_task =
            parse_created_task(&serde_json::to_vec(&task).expect("task JSON")).expect("valid task");
        assert_eq!(parsed_task.task_id(), IDENTIFIER);
        assert_eq!(parsed_task.status(), "draft");
        assert_eq!(parsed_task.revision(), 1);
    }

    #[test]
    fn response_parsers_fail_closed_on_unknown_invalid_or_cross_capability_data() {
        let invalid_registration = serde_json::json!({
            "installationId": IDENTIFIER,
            "status": "active",
            "revision": 1,
            "deviceCredential": {
                "credential": "atdc1.invalid.private",
                "version": 1,
                "scope": "device.session.exchange"
            }
        });
        assert!(parse_installation_registration(
            &serde_json::to_vec(&invalid_registration).expect("invalid registration JSON")
        )
        .is_err());

        let invalid_session = serde_json::json!({
            "sessionToken": opaque_bearer("atds1"),
            "capability": "app.control-plane",
            "issuedAt": "2026-07-18T02:00:00Z",
            "expiresAt": "2026-07-18T02:05:00Z"
        });
        assert!(parse_device_session(
            &serde_json::to_vec(&invalid_session).expect("invalid session JSON"),
            DeviceSessionCapability::ExecutorConnect,
        )
        .is_err());

        let reversed_session_time = serde_json::json!({
            "sessionToken": opaque_bearer("atds1"),
            "capability": "executor.connect",
            "issuedAt": "2026-07-18T02:05:00Z",
            "expiresAt": "2026-07-18T02:00:00Z"
        });
        assert!(parse_device_session(
            &serde_json::to_vec(&reversed_session_time).expect("reversed session JSON"),
            DeviceSessionCapability::ExecutorConnect,
        )
        .is_err());

        for invalid in [
            serde_json::json!({
                "challengeId": IDENTIFIER,
                "signingPayload": "not+base64url",
                "expiresAt": "2026-07-18T03:00:00Z"
            }),
            serde_json::json!({
                "challengeId": IDENTIFIER,
                "signingPayload": URL_SAFE_NO_PAD.encode(b"payload"),
                "expiresAt": "T+00:00"
            }),
            serde_json::json!({
                "challengeId": "f831a58a-a54c-4bd9-0f3e-0383c4df609d",
                "signingPayload": URL_SAFE_NO_PAD.encode(b"payload"),
                "expiresAt": "2026-07-18T03:00:00Z"
            }),
            serde_json::json!({
                "challengeId": IDENTIFIER,
                "signingPayload": URL_SAFE_NO_PAD.encode(b"payload"),
                "expiresAt": "2026-07-18T03:00:00Z",
                "unknown": true
            }),
        ] {
            assert!(parse_registration_challenge(
                &serde_json::to_vec(&invalid).expect("invalid challenge JSON")
            )
            .is_err());
        }

        for invalid in [
            serde_json::json!({"installationId": IDENTIFIER, "status": "revoked"}),
            serde_json::json!({"installationId": "private-invalid", "status": "active"}),
            serde_json::json!({
                "installationId": IDENTIFIER,
                "status": "active",
                "credential": "private"
            }),
        ] {
            assert!(parse_installation_access(
                &serde_json::to_vec(&invalid).expect("invalid access JSON")
            )
            .is_err());
        }

        for invalid in [
            serde_json::json!({
                "taskId": IDENTIFIER,
                "status": "ready",
                "revision": 1,
                "createdAt": "2026-07-18T02:00:00Z",
                "updatedAt": "2026-07-18T02:00:00Z"
            }),
            serde_json::json!({
                "taskId": IDENTIFIER,
                "status": "draft",
                "revision": 1,
                "createdAt": "2026-07-18T02:05:00Z",
                "updatedAt": "2026-07-18T02:00:00Z"
            }),
            serde_json::json!({
                "taskId": IDENTIFIER,
                "status": "draft",
                "revision": 1,
                "createdAt": "2026-07-18T02:00:00Z",
                "updatedAt": "2026-07-18T02:00:00Z",
                "unknown": true
            }),
        ] {
            assert!(
                parse_created_task(&serde_json::to_vec(&invalid).expect("invalid task JSON"))
                    .is_err()
            );
        }
    }

    #[test]
    fn task_idempotency_keys_use_the_exact_protocol_alphabet_and_bounds() {
        let longest_valid = "a".repeat(128);
        for valid in ["a", "task:create/demo_1-2.3", longest_valid.as_str()] {
            require_idempotency_key(valid).expect("valid idempotency key");
        }
        let too_long = "a".repeat(129);
        for invalid in [
            "",
            "-leading",
            "contains space",
            "private@value",
            too_long.as_str(),
        ] {
            let error = require_idempotency_key(invalid).expect_err("invalid idempotency key");
            assert_eq!(error.code(), ControlPlaneErrorCode::ProtocolInvalid);
            assert_eq!(error.to_string(), "Control Plane request failed");
            if !invalid.is_empty() {
                assert!(!error.to_string().contains(invalid));
            }
        }
    }

    #[test]
    fn task_list_cursors_use_bounded_base64url_shape() {
        let longest_valid = "a".repeat(256);
        for valid in ["a", "YWJj_123-xyz", longest_valid.as_str()] {
            require_list_cursor(valid).expect("valid list cursor");
        }
        let too_long = "a".repeat(257);
        for invalid in ["", "private+cursor", "contains space", too_long.as_str()] {
            let error = require_list_cursor(invalid).expect_err("invalid list cursor");
            assert_eq!(error.code(), ControlPlaneErrorCode::ProtocolInvalid);
            assert_eq!(error.to_string(), "Control Plane request failed");
        }
    }

    #[test]
    fn task_query_parsers_require_public_ordered_snapshots() {
        let older_id = "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc";
        let detail = serde_json::json!({
            "taskId": IDENTIFIER,
            "status": "running",
            "revision": 3,
            "createdAt": "2026-07-18T02:00:00Z",
            "updatedAt": "2026-07-18T03:00:00Z"
        });
        let parsed_detail =
            parse_task_snapshot_body(&serde_json::to_vec(&detail).expect("task detail JSON"))
                .expect("valid task detail");
        assert_eq!(parsed_detail.task_id(), IDENTIFIER);
        assert_eq!(parsed_detail.status(), "running");
        assert_eq!(parsed_detail.revision(), 3);

        let older = serde_json::json!({
            "taskId": older_id,
            "status": "draft",
            "revision": 1,
            "createdAt": "2026-07-18T02:00:00Z",
            "updatedAt": "2026-07-18T02:00:00Z"
        });
        let page = serde_json::json!({
            "items": [detail, older],
            "nextCursor": "YWJj"
        });
        let parsed_page = parse_task_list(&serde_json::to_vec(&page).expect("task list JSON"))
            .expect("valid task list");
        assert_eq!(parsed_page.items().len(), 2);
        assert_eq!(parsed_page.items()[1].task_id(), older_id);
        assert_eq!(parsed_page.next_cursor(), Some("YWJj"));
    }

    #[test]
    fn task_query_parsers_reject_invalid_status_order_cursor_and_unknown_fields() {
        let first = serde_json::json!({
            "taskId": IDENTIFIER,
            "status": "draft",
            "revision": 1,
            "createdAt": "2026-07-18T02:00:00Z",
            "updatedAt": "2026-07-18T02:00:00Z"
        });
        let later = serde_json::json!({
            "taskId": "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc",
            "status": "draft",
            "revision": 1,
            "createdAt": "2026-07-18T03:00:00Z",
            "updatedAt": "2026-07-18T03:00:00Z"
        });
        for invalid in [
            serde_json::json!({"items": [], "nextCursor": "YWJj"}),
            serde_json::json!({"items": [first.clone()], "nextCursor": "private+cursor"}),
            serde_json::json!({"items": [first.clone(), later], "nextCursor": null}),
            serde_json::json!({"items": [first.clone(), first.clone()], "nextCursor": null}),
            serde_json::json!({"items": [first.clone()], "nextCursor": null, "unknown": true}),
        ] {
            assert!(parse_task_list(
                &serde_json::to_vec(&invalid).expect("invalid task list JSON")
            )
            .is_err());
        }

        let invalid_status = serde_json::json!({
            "taskId": IDENTIFIER,
            "status": "private_unknown",
            "revision": 1,
            "createdAt": "2026-07-18T02:00:00Z",
            "updatedAt": "2026-07-18T02:00:00Z"
        });
        assert!(parse_task_snapshot_body(
            &serde_json::to_vec(&invalid_status).expect("invalid task detail JSON")
        )
        .is_err());
    }

    #[test]
    fn credential_loading_distinguishes_missing_corrupt_and_storage_failure() {
        let missing = DeviceCredentialVault::new(MemorySecretStore {
            value: None,
            fail_load: Cell::new(false),
        });
        let missing_error = credential_error(required_credential(&missing));
        assert_eq!(
            missing_error.code(),
            ControlPlaneErrorCode::CredentialMissing
        );

        let corrupt = DeviceCredentialVault::new(MemorySecretStore::with_value(b"private-bad"));
        let corrupt_error = credential_error(required_credential(&corrupt));
        assert_eq!(corrupt_error.code(), ControlPlaneErrorCode::ProtocolInvalid);

        let unavailable_store = MemorySecretStore {
            value: None,
            fail_load: Cell::new(true),
        };
        let unavailable = DeviceCredentialVault::new(unavailable_store);
        let unavailable_error = credential_error(required_credential(&unavailable));
        assert_eq!(
            unavailable_error.code(),
            ControlPlaneErrorCode::StorageUnavailable
        );
        for error in [missing_error, corrupt_error, unavailable_error] {
            assert_eq!(error.to_string(), "Control Plane request failed");
            assert!(!error.to_string().contains("private"));
        }
    }

    #[test]
    fn unregistered_installation_skips_the_authenticated_access_probe() {
        let missing = DeviceCredentialVault::new(MemorySecretStore {
            value: None,
            fail_load: Cell::new(false),
        });
        let client = super::ControlPlaneClient::local().expect("local client");

        tauri::async_runtime::block_on(client.check_installation_access_if_registered(&missing))
            .expect("unregistered App remains usable");
    }

    #[test]
    fn transport_failure_marks_only_stateful_or_issuing_operations_uncertain() {
        for operation in [
            ControlPlaneOperation::CompleteInstallationRegistration,
            ControlPlaneOperation::RotateDeviceCredential,
            ControlPlaneOperation::RevokeDeviceCredential,
            ControlPlaneOperation::ExchangeDeviceSession,
        ] {
            let error = transport_error(operation);
            assert_eq!(error.code(), ControlPlaneErrorCode::OutcomeUncertain);
            assert!(!error.retryable());
        }
        for operation in [
            ControlPlaneOperation::GetSystemHealth,
            ControlPlaneOperation::GetCurrentInstallationAccess,
            ControlPlaneOperation::IssueInstallationRegistrationChallenge,
            ControlPlaneOperation::CreateTask,
            ControlPlaneOperation::ListTasks,
            ControlPlaneOperation::GetTask,
        ] {
            let error = transport_error(operation);
            assert_eq!(error.code(), ControlPlaneErrorCode::TransportUnavailable);
            assert!(error.retryable());
        }
    }
}

//! One-shot authenticated stdin bootstrap for the packaged Local Executor.

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use hmac::{Hmac, KeyInit, Mac};
use reqwest::Url;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::fmt;
use std::io::Write;
use std::path::{Component, Path};
use uuid::{Uuid, Variant};
use zeroize::{Zeroize, Zeroizing};

const BOOTSTRAP_VERSION: &str = "1";
const EXECUTOR_PROTOCOL_VERSION: &str = "1.0";
const EXECUTOR_WEBSOCKET_PATH: &str = "/api/v1/executors/connect";
const LOCAL_SESSION_TOKEN_BYTES: usize = 32;
const MAX_BOOTSTRAP_BYTES: usize = 16 * 1024;
const MAX_ENDPOINT_BYTES: usize = 2048;
const MAX_CONTROL_PLANE_SESSION_BYTES: usize = 4096;
const MAX_STATE_DIRECTORY_BYTES: usize = 4096;
const PROOF_PREFIX: &str = "atlep1.";
const AUTHENTICATION_DOMAIN: &[u8] = b"automation-tool.local-executor-event.v1\0";
const COMMAND_AUTHENTICATION_DOMAIN: &[u8] = b"automation-tool.local-executor-command.v1\0";
const COMMAND_RESULT_AUTHENTICATION_DOMAIN: &[u8] = b"automation-tool.local-executor-result.v1\0";
const COMMAND_PROOF_PREFIX: &str = "atlcp1.";
const MAX_PLATFORM_COMMAND_BYTES: usize = 16 * 1024;
const ACTION_AUTHORIZATION_PUBLIC_KEY: Option<&str> =
    option_env!("AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY");
const LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS: Option<&str> =
    option_env!("AUTOMATION_TOOL_LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS");
const LOCAL_ACTION_TASK_LIMIT: Option<&str> =
    option_env!("AUTOMATION_TOOL_LOCAL_ACTION_TASK_LIMIT");

type HmacSha256 = Hmac<Sha256>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExecutorBootstrapErrorCode {
    AuthenticationRejected,
    BootstrapRejected,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct ExecutorBootstrapError {
    code: ExecutorBootstrapErrorCode,
}

impl ExecutorBootstrapError {
    fn bootstrap_rejected() -> Self {
        Self {
            code: ExecutorBootstrapErrorCode::BootstrapRejected,
        }
    }

    fn authentication_rejected() -> Self {
        Self {
            code: ExecutorBootstrapErrorCode::AuthenticationRejected,
        }
    }

    pub const fn code(self) -> ExecutorBootstrapErrorCode {
        self.code
    }
}

impl fmt::Debug for ExecutorBootstrapError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ExecutorBootstrapError")
            .field("code", &self.code)
            .finish()
    }
}

impl fmt::Display for ExecutorBootstrapError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = match self.code {
            ExecutorBootstrapErrorCode::AuthenticationRejected => {
                "Local Executor authentication is rejected"
            }
            ExecutorBootstrapErrorCode::BootstrapRejected => "Local Executor bootstrap is rejected",
        };
        formatter.write_str(message)
    }
}

impl std::error::Error for ExecutorBootstrapError {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LocalExecutorEvent {
    Healthy,
    Stopped,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LocalPlatformCommand {
    OpenDouyinLogin,
    RecheckDouyinLogin,
    CompleteDouyinLogout,
}

impl LocalPlatformCommand {
    const fn as_str(self) -> &'static str {
        match self {
            Self::OpenDouyinLogin => "douyin.login.open",
            Self::RecheckDouyinLogin => "douyin.login.recheck",
            Self::CompleteDouyinLogout => "douyin.logout.complete",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalPlatformCommandResult {
    platform: String,
    state: String,
    flow_version: String,
}

impl LocalPlatformCommandResult {
    pub fn state(&self) -> &str {
        &self.state
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct LocalPlatformCommandDocument<'a> {
    authentication_proof: &'a str,
    command_id: &'a str,
    command_type: &'static str,
    executable_path: &'a Path,
    headless: bool,
    profile_directory: &'a Path,
    protocol_version: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct LocalSessionCommandDocument<'a> {
    authentication_proof: &'a str,
    command_id: &'a str,
    command_type: &'static str,
    protocol_version: &'static str,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct LocalPlatformCommandResultDocument {
    authentication_proof: String,
    command_id: String,
    event: String,
    flow_version: String,
    platform: String,
    protocol_version: String,
    state: String,
}

impl LocalExecutorEvent {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Healthy => "executor.healthy",
            Self::Stopped => "executor.stopped",
        }
    }
}

pub struct LocalSessionToken {
    bytes: [u8; LOCAL_SESSION_TOKEN_BYTES],
}

impl LocalSessionToken {
    pub fn generate() -> Result<Self, ExecutorBootstrapError> {
        let mut bytes = [0_u8; LOCAL_SESSION_TOKEN_BYTES];
        getrandom::fill(&mut bytes).map_err(|_| ExecutorBootstrapError::bootstrap_rejected())?;
        Ok(Self { bytes })
    }

    fn encoded(&self) -> Zeroizing<String> {
        use fmt::Write as _;

        let mut encoded = Zeroizing::new(String::with_capacity(LOCAL_SESSION_TOKEN_BYTES * 2));
        for byte in &self.bytes {
            write!(&mut *encoded, "{byte:02x}").expect("writing to a String cannot fail");
        }
        encoded
    }

    pub fn write_bootstrap(
        &self,
        writer: &mut impl Write,
        input: &ExecutorBootstrapInput<'_>,
    ) -> Result<(), ExecutorBootstrapError> {
        let local_session_token = self.encoded();
        let document = ExecutorBootstrapDocument {
            bootstrap_version: BOOTSTRAP_VERSION,
            websocket_url: input.websocket_url,
            local_session_token: &local_session_token,
            session_token: input.control_plane_session,
            installation_id: input.installation_id.hyphenated().to_string(),
            executor_id: input.executor_id.hyphenated().to_string(),
            heartbeat_interval_seconds: input.heartbeat_interval_seconds,
            state_directory: input.state_directory,
            local_emergency_stop: input.local_emergency_stop,
            crash_recovery: input.crash_recovery,
            capture_successful_diagnostics: input.capture_successful_diagnostics,
            action_runtime: input.action_runtime.as_ref(),
        };
        let mut serialized = Zeroizing::new(
            serde_json::to_vec(&document)
                .map_err(|_| ExecutorBootstrapError::bootstrap_rejected())?,
        );
        serialized.push(b'\n');
        if serialized.len() > MAX_BOOTSTRAP_BYTES {
            return Err(ExecutorBootstrapError::bootstrap_rejected());
        }
        writer
            .write_all(&serialized)
            .and_then(|()| writer.flush())
            .map_err(|_| ExecutorBootstrapError::bootstrap_rejected())
    }

    pub fn verify_event_proof(
        &self,
        event: LocalExecutorEvent,
        presented_proof: &str,
    ) -> Result<(), ExecutorBootstrapError> {
        let encoded = presented_proof
            .strip_prefix(PROOF_PREFIX)
            .ok_or_else(ExecutorBootstrapError::authentication_rejected)?;
        let presented = URL_SAFE_NO_PAD
            .decode(encoded)
            .map_err(|_| ExecutorBootstrapError::authentication_rejected())?;
        if presented.len() != 32 || encoded.len() != 43 {
            return Err(ExecutorBootstrapError::authentication_rejected());
        }
        let mut authenticator = HmacSha256::new_from_slice(&self.bytes)
            .map_err(|_| ExecutorBootstrapError::authentication_rejected())?;
        authenticator.update(AUTHENTICATION_DOMAIN);
        authenticator.update(event.as_str().as_bytes());
        authenticator.update(b"\0");
        authenticator.update(EXECUTOR_PROTOCOL_VERSION.as_bytes());
        authenticator
            .verify_slice(&presented)
            .map_err(|_| ExecutorBootstrapError::authentication_rejected())
    }

    pub fn write_platform_command(
        &self,
        writer: &mut impl Write,
        command_id: &str,
        command: LocalPlatformCommand,
        executable_path: &Path,
        profile_directory: &Path,
        headless: bool,
    ) -> Result<(), ExecutorBootstrapError> {
        require_uuid_v4(command_id)?;
        require_local_path(executable_path)?;
        require_local_path(profile_directory)?;
        let executable = executable_path
            .to_str()
            .ok_or_else(ExecutorBootstrapError::bootstrap_rejected)?;
        let profile = profile_directory
            .to_str()
            .ok_or_else(ExecutorBootstrapError::bootstrap_rejected)?;
        let proof = self.command_proof(
            COMMAND_AUTHENTICATION_DOMAIN,
            &[
                command_id,
                command.as_str(),
                executable,
                profile,
                if headless { "1" } else { "0" },
                EXECUTOR_PROTOCOL_VERSION,
            ],
        )?;
        let document = LocalPlatformCommandDocument {
            authentication_proof: &proof,
            command_id,
            command_type: command.as_str(),
            executable_path,
            headless,
            profile_directory,
            protocol_version: EXECUTOR_PROTOCOL_VERSION,
        };
        let mut serialized = Zeroizing::new(
            serde_json::to_vec(&document)
                .map_err(|_| ExecutorBootstrapError::bootstrap_rejected())?,
        );
        serialized.push(b'\n');
        if serialized.len() > MAX_PLATFORM_COMMAND_BYTES {
            return Err(ExecutorBootstrapError::bootstrap_rejected());
        }
        writer
            .write_all(&serialized)
            .and_then(|()| writer.flush())
            .map_err(|_| ExecutorBootstrapError::bootstrap_rejected())
    }

    pub fn write_session_command(
        &self,
        writer: &mut impl Write,
        command_id: &str,
        command: LocalPlatformCommand,
    ) -> Result<(), ExecutorBootstrapError> {
        require_uuid_v4(command_id)?;
        if command != LocalPlatformCommand::CompleteDouyinLogout {
            return Err(ExecutorBootstrapError::bootstrap_rejected());
        }
        let proof = self.command_proof(
            COMMAND_AUTHENTICATION_DOMAIN,
            &[command_id, command.as_str(), EXECUTOR_PROTOCOL_VERSION],
        )?;
        let document = LocalSessionCommandDocument {
            authentication_proof: &proof,
            command_id,
            command_type: command.as_str(),
            protocol_version: EXECUTOR_PROTOCOL_VERSION,
        };
        let mut serialized = Zeroizing::new(
            serde_json::to_vec(&document)
                .map_err(|_| ExecutorBootstrapError::bootstrap_rejected())?,
        );
        serialized.push(b'\n');
        if serialized.len() > MAX_PLATFORM_COMMAND_BYTES {
            return Err(ExecutorBootstrapError::bootstrap_rejected());
        }
        writer
            .write_all(&serialized)
            .and_then(|()| writer.flush())
            .map_err(|_| ExecutorBootstrapError::bootstrap_rejected())
    }

    pub fn parse_platform_command_result(
        &self,
        expected_command_id: &str,
        expected_command: LocalPlatformCommand,
        source: &str,
    ) -> Result<LocalPlatformCommandResult, ExecutorBootstrapError> {
        require_uuid_v4(expected_command_id)?;
        if source.is_empty() || source.len() > MAX_LIFECYCLE_LINE_BYTES_COMPAT {
            return Err(ExecutorBootstrapError::authentication_rejected());
        }
        let document: LocalPlatformCommandResultDocument = serde_json::from_str(source)
            .map_err(|_| ExecutorBootstrapError::authentication_rejected())?;
        let expected_flow_version = match expected_command {
            LocalPlatformCommand::CompleteDouyinLogout => "douyin.session-control.v1",
            LocalPlatformCommand::OpenDouyinLogin | LocalPlatformCommand::RecheckDouyinLogin => {
                "douyin.qr-login.v2"
            }
        };
        let valid_state = match expected_command {
            LocalPlatformCommand::CompleteDouyinLogout => document.state == "logged_out",
            LocalPlatformCommand::OpenDouyinLogin | LocalPlatformCommand::RecheckDouyinLogin => {
                valid_platform_command_state(&document.state)
            }
        };
        if document.command_id != expected_command_id
            || document.event != "platform.command.completed"
            || document.flow_version != expected_flow_version
            || document.platform != "douyin"
            || document.protocol_version != EXECUTOR_PROTOCOL_VERSION
            || !valid_state
        {
            return Err(ExecutorBootstrapError::authentication_rejected());
        }
        self.verify_command_proof(
            COMMAND_RESULT_AUTHENTICATION_DOMAIN,
            &[
                expected_command_id,
                &document.state,
                EXECUTOR_PROTOCOL_VERSION,
            ],
            &document.authentication_proof,
        )?;
        Ok(LocalPlatformCommandResult {
            platform: document.platform,
            state: document.state,
            flow_version: document.flow_version,
        })
    }

    fn command_proof(
        &self,
        domain: &[u8],
        parts: &[&str],
    ) -> Result<Zeroizing<String>, ExecutorBootstrapError> {
        let mut authenticator = HmacSha256::new_from_slice(&self.bytes)
            .map_err(|_| ExecutorBootstrapError::authentication_rejected())?;
        authenticator.update(domain);
        for (index, part) in parts.iter().enumerate() {
            if index > 0 {
                authenticator.update(b"\0");
            }
            authenticator.update(part.as_bytes());
        }
        let encoded = URL_SAFE_NO_PAD.encode(authenticator.finalize().into_bytes());
        Ok(Zeroizing::new(format!("{COMMAND_PROOF_PREFIX}{encoded}")))
    }

    fn verify_command_proof(
        &self,
        domain: &[u8],
        parts: &[&str],
        presented: &str,
    ) -> Result<(), ExecutorBootstrapError> {
        let encoded = presented
            .strip_prefix(COMMAND_PROOF_PREFIX)
            .ok_or_else(ExecutorBootstrapError::authentication_rejected)?;
        let presented = URL_SAFE_NO_PAD
            .decode(encoded)
            .map_err(|_| ExecutorBootstrapError::authentication_rejected())?;
        if presented.len() != 32 || encoded.len() != 43 {
            return Err(ExecutorBootstrapError::authentication_rejected());
        }
        let mut authenticator = HmacSha256::new_from_slice(&self.bytes)
            .map_err(|_| ExecutorBootstrapError::authentication_rejected())?;
        authenticator.update(domain);
        for (index, part) in parts.iter().enumerate() {
            if index > 0 {
                authenticator.update(b"\0");
            }
            authenticator.update(part.as_bytes());
        }
        authenticator
            .verify_slice(&presented)
            .map_err(|_| ExecutorBootstrapError::authentication_rejected())
    }
}

const MAX_LIFECYCLE_LINE_BYTES_COMPAT: usize = 4096;

fn valid_platform_command_state(value: &str) -> bool {
    matches!(
        value,
        "login_required"
            | "awaiting_scan"
            | "awaiting_confirmation"
            | "qr_expired"
            | "healthy"
            | "handoff_required"
            | "unknown"
    )
}

fn require_local_path(source: &Path) -> Result<(), ExecutorBootstrapError> {
    let encoded = source
        .to_str()
        .ok_or_else(ExecutorBootstrapError::bootstrap_rejected)?;
    if !source.is_absolute()
        || source.parent().is_none()
        || encoded.is_empty()
        || encoded.len() > MAX_STATE_DIRECTORY_BYTES
        || encoded.chars().any(|character| {
            let codepoint = character as u32;
            character.is_control()
                || (0x202a..=0x202e).contains(&codepoint)
                || (0x2066..=0x2069).contains(&codepoint)
        })
        || source
            .components()
            .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
    {
        return Err(ExecutorBootstrapError::bootstrap_rejected());
    }
    Ok(())
}

impl fmt::Debug for LocalSessionToken {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("LocalSessionToken([REDACTED])")
    }
}

impl Drop for LocalSessionToken {
    fn drop(&mut self) {
        self.bytes.zeroize();
    }
}

pub struct ExecutorBootstrapInput<'a> {
    websocket_url: &'a str,
    control_plane_session: &'a str,
    installation_id: Uuid,
    executor_id: Uuid,
    heartbeat_interval_seconds: u8,
    state_directory: &'a Path,
    local_emergency_stop: bool,
    crash_recovery: bool,
    capture_successful_diagnostics: bool,
    action_runtime: Option<ExecutorActionRuntimeInput>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ExecutorActionRuntimeInput {
    authorization_public_key: String,
    minimum_interval_seconds: u16,
    task_action_limit: u8,
}

impl ExecutorActionRuntimeInput {
    pub fn new(
        authorization_public_key: &str,
        minimum_interval_seconds: u16,
        task_action_limit: u8,
    ) -> Result<Self, ExecutorBootstrapError> {
        let decoded = URL_SAFE_NO_PAD
            .decode(authorization_public_key)
            .map_err(|_| ExecutorBootstrapError::bootstrap_rejected())?;
        if decoded.len() != 32
            || decoded.iter().all(|byte| *byte == 0)
            || URL_SAFE_NO_PAD.encode(&decoded) != authorization_public_key
            || !(1..=3600).contains(&minimum_interval_seconds)
            || !(1..=100).contains(&task_action_limit)
        {
            return Err(ExecutorBootstrapError::bootstrap_rejected());
        }
        Ok(Self {
            authorization_public_key: authorization_public_key.to_owned(),
            minimum_interval_seconds,
            task_action_limit,
        })
    }

    pub(crate) fn from_compile_time_configuration() -> Result<Option<Self>, ExecutorBootstrapError>
    {
        let values = (
            ACTION_AUTHORIZATION_PUBLIC_KEY,
            LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS,
            LOCAL_ACTION_TASK_LIMIT,
        );
        match values {
            (None, None, None) => Ok(None),
            (Some(public_key), Some(minimum_interval), Some(task_limit)) => Self::new(
                public_key,
                minimum_interval
                    .parse()
                    .map_err(|_| ExecutorBootstrapError::bootstrap_rejected())?,
                task_limit
                    .parse()
                    .map_err(|_| ExecutorBootstrapError::bootstrap_rejected())?,
            )
            .map(Some),
            _ => Err(ExecutorBootstrapError::bootstrap_rejected()),
        }
    }
}

#[derive(Clone, Copy)]
enum ExecutorBootstrapMode {
    Normal,
    EmergencyReport,
    CrashRecovery,
}

impl<'a> ExecutorBootstrapInput<'a> {
    pub fn new(
        websocket_url: &'a str,
        control_plane_session: &'a str,
        installation_id: &str,
        executor_id: &str,
        state_directory: &'a Path,
        heartbeat_interval_seconds: u8,
    ) -> Result<Self, ExecutorBootstrapError> {
        Self::build(
            websocket_url,
            control_plane_session,
            installation_id,
            executor_id,
            state_directory,
            heartbeat_interval_seconds,
            ExecutorBootstrapMode::Normal,
        )
    }

    pub fn new_crash_recovery(
        websocket_url: &'a str,
        control_plane_session: &'a str,
        installation_id: &str,
        executor_id: &str,
        state_directory: &'a Path,
        heartbeat_interval_seconds: u8,
    ) -> Result<Self, ExecutorBootstrapError> {
        Self::build(
            websocket_url,
            control_plane_session,
            installation_id,
            executor_id,
            state_directory,
            heartbeat_interval_seconds,
            ExecutorBootstrapMode::CrashRecovery,
        )
    }

    pub fn new_emergency_report(
        websocket_url: &'a str,
        control_plane_session: &'a str,
        installation_id: &str,
        executor_id: &str,
        state_directory: &'a Path,
        heartbeat_interval_seconds: u8,
    ) -> Result<Self, ExecutorBootstrapError> {
        Self::build(
            websocket_url,
            control_plane_session,
            installation_id,
            executor_id,
            state_directory,
            heartbeat_interval_seconds,
            ExecutorBootstrapMode::EmergencyReport,
        )
    }

    pub fn with_capture_successful_diagnostics(mut self, enabled: bool) -> Self {
        self.capture_successful_diagnostics = enabled;
        self
    }

    pub fn with_action_runtime(mut self, action_runtime: ExecutorActionRuntimeInput) -> Self {
        self.action_runtime = Some(action_runtime);
        self
    }

    fn build(
        websocket_url: &'a str,
        control_plane_session: &'a str,
        installation_id: &str,
        executor_id: &str,
        state_directory: &'a Path,
        heartbeat_interval_seconds: u8,
        mode: ExecutorBootstrapMode,
    ) -> Result<Self, ExecutorBootstrapError> {
        require_endpoint(websocket_url)?;
        if control_plane_session.is_empty()
            || control_plane_session.len() > MAX_CONTROL_PLANE_SESSION_BYTES
            || control_plane_session.chars().any(char::is_whitespace)
            || !(1..=60).contains(&heartbeat_interval_seconds)
        {
            return Err(ExecutorBootstrapError::bootstrap_rejected());
        }
        let installation_id = require_uuid_v4(installation_id)?;
        let executor_id = require_uuid_v4(executor_id)?;
        require_state_directory(state_directory)?;
        Ok(Self {
            websocket_url,
            control_plane_session,
            installation_id,
            executor_id,
            heartbeat_interval_seconds,
            state_directory,
            local_emergency_stop: matches!(mode, ExecutorBootstrapMode::EmergencyReport),
            crash_recovery: matches!(mode, ExecutorBootstrapMode::CrashRecovery),
            capture_successful_diagnostics: false,
            action_runtime: None,
        })
    }
}

#[derive(Serialize)]
struct ExecutorBootstrapDocument<'a> {
    bootstrap_version: &'static str,
    websocket_url: &'a str,
    local_session_token: &'a str,
    session_token: &'a str,
    installation_id: String,
    executor_id: String,
    heartbeat_interval_seconds: u8,
    state_directory: &'a Path,
    local_emergency_stop: bool,
    crash_recovery: bool,
    capture_successful_diagnostics: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    action_runtime: Option<&'a ExecutorActionRuntimeInput>,
}

fn require_uuid_v4(source: &str) -> Result<Uuid, ExecutorBootstrapError> {
    let parsed =
        Uuid::parse_str(source).map_err(|_| ExecutorBootstrapError::bootstrap_rejected())?;
    if parsed.get_version_num() != 4 || parsed.get_variant() != Variant::RFC4122 {
        return Err(ExecutorBootstrapError::bootstrap_rejected());
    }
    Ok(parsed)
}

fn require_endpoint(source: &str) -> Result<(), ExecutorBootstrapError> {
    if source.is_empty() || source.len() > MAX_ENDPOINT_BYTES {
        return Err(ExecutorBootstrapError::bootstrap_rejected());
    }
    let parsed = Url::parse(source).map_err(|_| ExecutorBootstrapError::bootstrap_rejected())?;
    if !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.path() != EXECUTOR_WEBSOCKET_PATH
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return Err(ExecutorBootstrapError::bootstrap_rejected());
    }
    let endpoint_allowed = match parsed.scheme() {
        "ws" => {
            parsed.host_str() == Some("127.0.0.1")
                && parsed
                    .port()
                    .is_some_and(|port| (1..=65535).contains(&port))
        }
        "wss" => parsed.port().is_none() || parsed.port() == Some(443),
        _ => false,
    };
    if !endpoint_allowed {
        return Err(ExecutorBootstrapError::bootstrap_rejected());
    }
    Ok(())
}

fn require_state_directory(source: &Path) -> Result<(), ExecutorBootstrapError> {
    let encoded = source
        .to_str()
        .ok_or_else(ExecutorBootstrapError::bootstrap_rejected)?;
    if !source.is_absolute()
        || source.parent().is_none()
        || encoded.is_empty()
        || encoded.len() > MAX_STATE_DIRECTORY_BYTES
        || encoded.chars().any(|character| {
            let codepoint = character as u32;
            character.is_control()
                || (0x202a..=0x202e).contains(&codepoint)
                || (0x2066..=0x2069).contains(&codepoint)
        })
        || source
            .components()
            .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
    {
        return Err(ExecutorBootstrapError::bootstrap_rejected());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixed_cross_language_proof_vector_stays_stable() {
        let token = LocalSessionToken {
            bytes: std::array::from_fn(|index| index as u8),
        };
        token
            .verify_event_proof(
                LocalExecutorEvent::Healthy,
                "atlep1.NOuvIGSTV1bPoAZcqjJCd4V0TtBvVdvc4nPHufoUpRY",
            )
            .expect("Python and Rust HMAC vector must match");
    }

    #[test]
    fn fixed_platform_command_vectors_stay_cross_language_compatible() {
        let token = LocalSessionToken {
            bytes: std::array::from_fn(|index| index as u8),
        };
        let mut stdin = Vec::new();
        token
            .write_platform_command(
                &mut stdin,
                "123e4567-e89b-42d3-a456-426614174005",
                LocalPlatformCommand::OpenDouyinLogin,
                Path::new("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path::new("/private/tmp/automation-tool-profile"),
                true,
            )
            .expect("valid platform command");
        let command: serde_json::Value =
            serde_json::from_slice(&stdin).expect("platform command JSON");
        assert_eq!(
            command["authenticationProof"],
            "atlcp1.4RcHva0oy2FBj2BQ7G3_NwYDT9CS0u3mEsF938YBX5E"
        );
        let result = token
            .parse_platform_command_result(
                "123e4567-e89b-42d3-a456-426614174005",
                LocalPlatformCommand::OpenDouyinLogin,
                r#"{"authenticationProof":"atlcp1.RSBSCseDm8EcwHuenH2QWdQOMjD6L5X1J_mwPqoqd_s","commandId":"123e4567-e89b-42d3-a456-426614174005","event":"platform.command.completed","flowVersion":"douyin.qr-login.v2","platform":"douyin","protocolVersion":"1.0","state":"awaiting_scan"}"#,
            )
            .expect("valid platform command result");
        assert_eq!(result.state(), "awaiting_scan");

        let mut logout = Vec::new();
        token
            .write_session_command(
                &mut logout,
                "123e4567-e89b-42d3-a456-426614174005",
                LocalPlatformCommand::CompleteDouyinLogout,
            )
            .expect("valid logout completion command");
        let command: serde_json::Value =
            serde_json::from_slice(&logout).expect("logout command JSON");
        assert_eq!(
            command["authenticationProof"],
            "atlcp1._NWhd5jlSI3elRsNVLNm7d-CEDz4A08bB4gIL2USR64"
        );
    }

    #[test]
    fn successful_diagnostic_capture_is_an_explicit_bootstrap_boolean() {
        let token = LocalSessionToken {
            bytes: std::array::from_fn(|index| index as u8),
        };
        for (enabled, expected) in [(false, false), (true, true)] {
            let input = ExecutorBootstrapInput::new(
                "ws://127.0.0.1:8765/api/v1/executors/connect",
                "atds1.private-session",
                "123e4567-e89b-42d3-a456-426614174003",
                "123e4567-e89b-42d3-a456-426614174004",
                Path::new("/private/tmp/automation-tool-executor-test"),
                1,
            )
            .expect("valid bootstrap")
            .with_capture_successful_diagnostics(enabled);
            let mut serialized = Vec::new();
            token
                .write_bootstrap(&mut serialized, &input)
                .expect("serialize bootstrap");
            let document: serde_json::Value =
                serde_json::from_slice(&serialized).expect("bootstrap JSON");
            assert_eq!(document["capture_successful_diagnostics"], expected);
        }
    }

    #[test]
    fn malformed_proofs_and_endpoint_policy_fail_closed() {
        let token = LocalSessionToken {
            bytes: [7_u8; LOCAL_SESSION_TOKEN_BYTES],
        };
        for proof in ["", "atlep1.", "atlep1.not-base64!", "private"] {
            assert_eq!(
                token
                    .verify_event_proof(LocalExecutorEvent::Healthy, proof)
                    .expect_err("malformed proof")
                    .code(),
                ExecutorBootstrapErrorCode::AuthenticationRejected,
            );
        }
        for endpoint in [
            "http://127.0.0.1:8765/api/v1/executors/connect",
            "ws://localhost:8765/api/v1/executors/connect",
            "ws://127.0.0.1/api/v1/executors/connect",
            "ws://127.0.0.1:0/api/v1/executors/connect",
            "wss://demo.example.com:8443/api/v1/executors/connect",
            "wss://user@demo.example.com/api/v1/executors/connect",
            "wss://demo.example.com/wrong",
        ] {
            assert!(ExecutorBootstrapInput::new(
                endpoint,
                "atds1.private-session",
                "123e4567-e89b-42d3-a456-426614174003",
                "123e4567-e89b-42d3-a456-426614174004",
                Path::new("/private/tmp/automation-tool-executor-test"),
                1,
            )
            .is_err());
        }
    }
}

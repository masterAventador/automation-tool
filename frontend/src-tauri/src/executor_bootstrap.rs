//! One-shot authenticated stdin bootstrap for the packaged Local Executor.

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use hmac::{Hmac, KeyInit, Mac};
use reqwest::Url;
use serde::Serialize;
use sha2::Sha256;
use std::fmt;
use std::io::Write;
use uuid::{Uuid, Variant};
use zeroize::{Zeroize, Zeroizing};

const BOOTSTRAP_VERSION: &str = "1";
const EXECUTOR_PROTOCOL_VERSION: &str = "1.0";
const EXECUTOR_WEBSOCKET_PATH: &str = "/api/v1/executors/connect";
const LOCAL_SESSION_TOKEN_BYTES: usize = 32;
const MAX_BOOTSTRAP_BYTES: usize = 16 * 1024;
const MAX_ENDPOINT_BYTES: usize = 2048;
const MAX_CONTROL_PLANE_SESSION_BYTES: usize = 4096;
const PROOF_PREFIX: &str = "atlep1.";
const AUTHENTICATION_DOMAIN: &[u8] = b"automation-tool.local-executor-event.v1\0";

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
}

impl<'a> ExecutorBootstrapInput<'a> {
    pub fn new(
        websocket_url: &'a str,
        control_plane_session: &'a str,
        installation_id: &str,
        executor_id: &str,
        heartbeat_interval_seconds: u8,
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
        Ok(Self {
            websocket_url,
            control_plane_session,
            installation_id,
            executor_id,
            heartbeat_interval_seconds,
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
                1,
            )
            .is_err());
        }
    }
}

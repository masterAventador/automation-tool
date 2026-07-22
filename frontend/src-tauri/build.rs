use base64::engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD};
use base64::Engine as _;
use ed25519_dalek::VerifyingKey;
use minisign_verify::PublicKey;
use url::Url;

const EXECUTOR_VERIFYING_KEY_ENVIRONMENT: &str = "AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY";
const UPDATE_ENDPOINT_ENVIRONMENT: &str = "AUTOMATION_TOOL_UPDATE_ENDPOINT";
const UPDATE_PUBLIC_KEY_ENVIRONMENT: &str = "AUTOMATION_TOOL_UPDATE_PUBLIC_KEY";

fn main() {
    println!("cargo:rerun-if-env-changed=AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY");
    println!("cargo:rerun-if-env-changed=AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY");
    println!("cargo:rerun-if-env-changed=AUTOMATION_TOOL_LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS");
    println!("cargo:rerun-if-env-changed=AUTOMATION_TOOL_LOCAL_ACTION_TASK_LIMIT");
    println!("cargo:rerun-if-env-changed=AUTOMATION_TOOL_UPDATE_ENDPOINT");
    println!("cargo:rerun-if-env-changed=AUTOMATION_TOOL_UPDATE_PUBLIC_KEY");
    require_release_executor_verifying_key();
    require_release_update_configuration();
    tauri_build::build()
}

fn require_release_executor_verifying_key() {
    if std::env::var("PROFILE").as_deref() != Ok("release") {
        return;
    }

    let encoded = std::env::var(EXECUTOR_VERIFYING_KEY_ENVIRONMENT)
        .unwrap_or_else(|_| panic!("release Executor verification key is required"));
    let decoded = URL_SAFE_NO_PAD
        .decode(&encoded)
        .unwrap_or_else(|_| panic!("release Executor verification key is invalid"));
    if URL_SAFE_NO_PAD.encode(&decoded) != encoded {
        panic!("release Executor verification key is invalid");
    }
    let bytes: [u8; 32] = decoded
        .try_into()
        .unwrap_or_else(|_| panic!("release Executor verification key is invalid"));
    let verifying_key = VerifyingKey::from_bytes(&bytes)
        .unwrap_or_else(|_| panic!("release Executor verification key is invalid"));
    if verifying_key.is_weak() {
        panic!("release Executor verification key is invalid");
    }
}

fn require_release_update_configuration() {
    if std::env::var("PROFILE").as_deref() != Ok("release") {
        return;
    }
    let endpoint = std::env::var(UPDATE_ENDPOINT_ENVIRONMENT)
        .unwrap_or_else(|_| panic!("release update configuration is required"));
    let encoded_public_key = std::env::var(UPDATE_PUBLIC_KEY_ENVIRONMENT)
        .unwrap_or_else(|_| panic!("release update configuration is required"));
    if endpoint.len() > 2048
        || endpoint.matches("{{target}}").count() != 1
        || endpoint.matches("{{arch}}").count() != 1
        || endpoint.matches("{{current_version}}").count() != 1
    {
        panic!("release update configuration is invalid");
    }
    let parsed =
        Url::parse(&endpoint).unwrap_or_else(|_| panic!("release update configuration is invalid"));
    if parsed.scheme() != "https"
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.fragment().is_some()
    {
        panic!("release update configuration is invalid");
    }
    if encoded_public_key.is_empty() || encoded_public_key.len() > 8192 {
        panic!("release update configuration is invalid");
    }
    let public_key_bytes = STANDARD
        .decode(&encoded_public_key)
        .unwrap_or_else(|_| panic!("release update configuration is invalid"));
    if STANDARD.encode(&public_key_bytes) != encoded_public_key {
        panic!("release update configuration is invalid");
    }
    let public_key_text = std::str::from_utf8(&public_key_bytes)
        .unwrap_or_else(|_| panic!("release update configuration is invalid"));
    PublicKey::decode(public_key_text)
        .unwrap_or_else(|_| panic!("release update configuration is invalid"));
}

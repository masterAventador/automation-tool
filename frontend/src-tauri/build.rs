use base64::engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD};
use base64::Engine as _;
use ed25519_dalek::VerifyingKey;
use minisign_verify::PublicKey;
use url::Url;

#[allow(dead_code)]
#[path = "src/deployment_profile.rs"]
mod deployment_profile;

const EXECUTOR_VERIFYING_KEY_ENVIRONMENT: &str = "AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY";
const UPDATE_ENDPOINT_ENVIRONMENT: &str = "AUTOMATION_TOOL_UPDATE_ENDPOINT";
const UPDATE_PUBLIC_KEY_ENVIRONMENT: &str = "AUTOMATION_TOOL_UPDATE_PUBLIC_KEY";
const DEPLOYMENT_PROFILE_PAYLOAD_ENVIRONMENT: &str = "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_PAYLOAD";
const DEPLOYMENT_PROFILE_SIGNATURE_ENVIRONMENT: &str =
    "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_SIGNATURE";
const DEPLOYMENT_PROFILE_VERIFYING_KEY_ENVIRONMENT: &str =
    "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_VERIFYING_KEY";

fn main() {
    println!("cargo:rerun-if-env-changed=AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY");
    println!("cargo:rerun-if-env-changed=AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY");
    println!("cargo:rerun-if-env-changed=AUTOMATION_TOOL_LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS");
    println!("cargo:rerun-if-env-changed=AUTOMATION_TOOL_LOCAL_ACTION_TASK_LIMIT");
    println!("cargo:rerun-if-env-changed=AUTOMATION_TOOL_UPDATE_ENDPOINT");
    println!("cargo:rerun-if-env-changed=AUTOMATION_TOOL_UPDATE_PUBLIC_KEY");
    println!("cargo:rerun-if-env-changed={DEPLOYMENT_PROFILE_PAYLOAD_ENVIRONMENT}");
    println!("cargo:rerun-if-env-changed={DEPLOYMENT_PROFILE_SIGNATURE_ENVIRONMENT}");
    println!("cargo:rerun-if-env-changed={DEPLOYMENT_PROFILE_VERIFYING_KEY_ENVIRONMENT}");
    validate_optional_deployment_profile();
    require_release_executor_verifying_key();
    require_release_update_configuration();
    tauri_build::build()
}

fn validate_optional_deployment_profile() {
    let payload = std::env::var(DEPLOYMENT_PROFILE_PAYLOAD_ENVIRONMENT).ok();
    let signature = std::env::var(DEPLOYMENT_PROFILE_SIGNATURE_ENVIRONMENT).ok();
    let verifying_key = std::env::var(DEPLOYMENT_PROFILE_VERIFYING_KEY_ENVIRONMENT).ok();
    match (payload, signature, verifying_key) {
        (None, None, None) => {}
        (Some(payload), Some(signature), Some(verifying_key)) => {
            deployment_profile::DeploymentProfile::verify_signed(
                &payload,
                &signature,
                &verifying_key,
            )
            .unwrap_or_else(|_| panic!("deployment profile is invalid"));
            println!(
                "cargo:rustc-env=AUTOMATION_TOOL_COMPILED_DEPLOYMENT_PROFILE_PAYLOAD={payload}"
            );
            println!(
                "cargo:rustc-env=AUTOMATION_TOOL_COMPILED_DEPLOYMENT_PROFILE_SIGNATURE={signature}"
            );
            println!(
                "cargo:rustc-env=AUTOMATION_TOOL_COMPILED_DEPLOYMENT_PROFILE_VERIFYING_KEY={verifying_key}"
            );
        }
        _ => panic!("deployment profile is invalid"),
    }
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

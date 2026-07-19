use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine as _;
use ed25519_dalek::VerifyingKey;

const EXECUTOR_VERIFYING_KEY_ENVIRONMENT: &str = "AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY";

fn main() {
    println!("cargo:rerun-if-env-changed=AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY");
    require_release_executor_verifying_key();
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

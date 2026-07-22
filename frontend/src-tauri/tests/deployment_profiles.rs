use std::fs;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine as _;
use ed25519_dalek::{Signer, SigningKey};

use automation_tool_desktop_lib::deployment_profile::{DeploymentProfile, DeploymentProfileKind};

const MANIFEST: &str = r#"{"version":"customer-demo-profile.v1","profile":"demo","profileId":"demo-acceptance","baseUrl":"https://api.automation-tool.test","allowedHosts":["api.automation-tool.test"]}"#;
const TEST_SEED: [u8; 32] = [7; 32];
static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(0);

fn signed_profile(manifest: &str) -> DeploymentProfile {
    try_signed_profile(manifest).expect("valid signed profile")
}

fn try_signed_profile(manifest: &str) -> Result<DeploymentProfile, impl std::error::Error> {
    let signer = SigningKey::from_bytes(&TEST_SEED);
    let signature = signer.sign(manifest.as_bytes());
    DeploymentProfile::verify_signed(
        &URL_SAFE_NO_PAD.encode(manifest),
        &URL_SAFE_NO_PAD.encode(signature.to_bytes()),
        &URL_SAFE_NO_PAD.encode(signer.verifying_key().to_bytes()),
    )
}

fn temporary_root() -> std::path::PathBuf {
    std::env::temp_dir().join(format!(
        "automation-tool-c10-07-{}-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time")
            .as_nanos(),
        NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed),
    ))
}

#[test]
fn well_signed_unsafe_or_noncanonical_profiles_fail_closed() {
    let invalid = [
        MANIFEST.replace("https://", "http://"),
        MANIFEST.replace(
            "https://api.automation-tool.test",
            "https://operator@api.automation-tool.test",
        ),
        MANIFEST.replace(
            "https://api.automation-tool.test",
            "https://api.automation-tool.test:443",
        ),
        MANIFEST.replace(
            "https://api.automation-tool.test",
            "https://api.automation-tool.test/api",
        ),
        MANIFEST.replace(
            r#"["api.automation-tool.test"]"#,
            r#"["api.automation-tool.test.evil.test"]"#,
        ),
        MANIFEST.replace("demo-acceptance", "../local"),
        MANIFEST.replace("customer-demo-profile.v1", "customer-demo-profile.v2"),
        MANIFEST.replace(r#""profile":"demo""#, r#""profile":"local""#),
        MANIFEST.replace(
            r#""allowedHosts":["api.automation-tool.test"]"#,
            r#""allowedHosts":["api.automation-tool.test","api.automation-tool.test"]"#,
        ),
        format!("{MANIFEST} "),
    ];
    for manifest in invalid {
        assert!(try_signed_profile(&manifest).is_err(), "{manifest}");
    }
}

#[test]
fn signed_demo_profile_is_canonical_and_tamper_evident() {
    let profile = signed_profile(MANIFEST);
    assert_eq!(profile.kind(), DeploymentProfileKind::Demo);
    assert_eq!(profile.base_url(), "https://api.automation-tool.test");
    assert_eq!(profile.allowed_hosts(), &["api.automation-tool.test"]);
    assert_eq!(profile.profile_id(), "demo-acceptance");

    let signer = SigningKey::from_bytes(&TEST_SEED);
    let signature = signer.sign(MANIFEST.as_bytes());
    for payload in [
        MANIFEST.replace("https://", "http://"),
        MANIFEST.replace("api.automation-tool.test", "evil.test"),
        format!("{MANIFEST} "),
    ] {
        assert!(DeploymentProfile::verify_signed(
            &URL_SAFE_NO_PAD.encode(payload),
            &URL_SAFE_NO_PAD.encode(signature.to_bytes()),
            &URL_SAFE_NO_PAD.encode(signer.verifying_key().to_bytes()),
        )
        .is_err());
    }
}

#[test]
fn local_and_demo_private_data_roots_never_overlap() {
    let root = temporary_root();
    fs::create_dir(&root).expect("create app data root");
    let local = DeploymentProfile::local()
        .prepare_data_directory(&root)
        .expect("local data root");
    let demo = signed_profile(MANIFEST)
        .prepare_data_directory(&root)
        .expect("demo data root");

    assert_eq!(local, root);
    assert_eq!(demo, root.join("profiles").join("demo-acceptance"));
    assert_ne!(
        local.join("product-account-session-v1"),
        demo.join("product-account-session-v1")
    );
    assert_ne!(
        local.join("device-credential-v1"),
        demo.join("device-credential-v1")
    );
    assert_ne!(
        local.join("device-identity-ed25519-v1"),
        demo.join("device-identity-ed25519-v1")
    );
    fs::write(local.join("local-only"), b"local").expect("local marker");
    assert!(!demo.join("local-only").exists());
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        assert_eq!(
            fs::metadata(&demo)
                .expect("Demo metadata")
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
    }
    assert!(DeploymentProfile::local()
        .prepare_data_directory(std::path::Path::new("relative"))
        .is_err());

    fs::remove_dir_all(&root).expect("remove app data root");
}

#[cfg(unix)]
#[test]
fn demo_profile_rejects_a_symlinked_namespace_boundary() {
    use std::os::unix::fs::symlink;

    let root = temporary_root();
    let outside = temporary_root();
    fs::create_dir(&root).expect("create app data root");
    fs::create_dir(&outside).expect("create outside root");
    symlink(&outside, root.join("profiles")).expect("create namespace symlink");

    assert!(signed_profile(MANIFEST)
        .prepare_data_directory(&root)
        .is_err());

    fs::remove_dir_all(&root).expect("remove app data root");
    fs::remove_dir_all(&outside).expect("remove outside root");
}

#[test]
fn compiled_profile_matches_build_contract() {
    let profile = DeploymentProfile::load().expect("compiled deployment profile");
    if option_env!("AUTOMATION_TOOL_C10_07_PROFILE_ACCEPTANCE") == Some("1") {
        assert_eq!(profile.kind(), DeploymentProfileKind::Demo);
        assert_eq!(profile.base_url(), "https://api.automation-tool.test");
        assert_eq!(profile.profile_id(), "demo-acceptance");
    } else {
        assert_eq!(profile.kind(), DeploymentProfileKind::Local);
        assert_eq!(profile.base_url(), "http://127.0.0.1:8765");
        assert_eq!(profile.profile_id(), "local");
    }
}

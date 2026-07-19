use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::executor_package::{
    ExecutorPackageErrorCode, ExecutorPackageVerifier,
};
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use ed25519_dalek::{Signer, SigningKey};
use serde::Serialize;
use sha2::{Digest, Sha256};

const TEST_SEED: [u8; 32] = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25,
    26, 27, 28, 29, 30, 31,
];
static NEXT_TEMPORARY_PACKAGE: AtomicU64 = AtomicU64::new(0);

struct TemporaryPackage {
    path: PathBuf,
}

impl TemporaryPackage {
    fn new() -> Self {
        let unique = format!(
            "automation-tool-e4-05-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            NEXT_TEMPORARY_PACKAGE.fetch_add(1, Ordering::Relaxed),
        );
        let path = std::env::temp_dir().join(unique);
        fs::create_dir_all(path.join("_internal")).expect("create temporary package");
        Self { path }
    }
}

impl Drop for TemporaryPackage {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

#[derive(Serialize)]
struct TestPackageFile {
    path: String,
    sha256: String,
    size: u64,
}

#[derive(Serialize)]
struct TestManifest {
    architecture: String,
    build_id: String,
    entrypoint: String,
    executor_version: String,
    files: Vec<TestPackageFile>,
    manifest_version: String,
    package_sha256: String,
    package_size: u64,
    platform: String,
}

fn current_platform() -> &'static str {
    if cfg!(target_os = "windows") {
        "windows"
    } else {
        "macos"
    }
}

fn current_architecture() -> &'static str {
    if cfg!(target_arch = "x86_64") {
        "x86_64"
    } else {
        "aarch64"
    }
}

fn sha256_hex(contents: &[u8]) -> String {
    Sha256::digest(contents)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn inventory_digest(files: &[TestPackageFile]) -> String {
    let mut digest = Sha256::new();
    digest.update(b"automation-tool.executor-package.v1\0");
    for file in files {
        let path = file.path.as_bytes();
        digest.update(
            u32::try_from(path.len())
                .expect("fixture path length")
                .to_be_bytes(),
        );
        digest.update(path);
        digest.update(file.size.to_be_bytes());
        digest.update(hex_bytes(&file.sha256));
    }
    digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn hex_bytes(value: &str) -> Vec<u8> {
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let pair = std::str::from_utf8(pair).expect("ASCII hex");
            u8::from_str_radix(pair, 16).expect("hex byte")
        })
        .collect()
}

fn sign_manifest(root: &Path, signing_key: &SigningKey, manifest_bytes: &[u8]) {
    fs::write(root.join("executor-manifest.v1.json"), manifest_bytes).expect("write manifest");
    let signature = signing_key.sign(manifest_bytes);
    fs::write(
        root.join("executor-manifest.v1.sig"),
        format!("atems1.{}\n", URL_SAFE_NO_PAD.encode(signature.to_bytes())),
    )
    .expect("write signature");
}

fn create_signed_package(
    version: &str,
    platform: &str,
    architecture: &str,
) -> (TemporaryPackage, SigningKey) {
    let package = TemporaryPackage::new();
    let entrypoint = if platform == "windows" {
        "automation-tool-executor.exe"
    } else {
        "automation-tool-executor"
    };
    let runtime = b"runtime fixture";
    let executable = b"executor fixture";
    fs::write(package.path.join("_internal/runtime.dat"), runtime).expect("write runtime");
    fs::write(package.path.join(entrypoint), executable).expect("write entrypoint");
    let files = vec![
        TestPackageFile {
            path: "_internal/runtime.dat".to_owned(),
            sha256: sha256_hex(runtime),
            size: runtime.len() as u64,
        },
        TestPackageFile {
            path: entrypoint.to_owned(),
            sha256: sha256_hex(executable),
            size: executable.len() as u64,
        },
    ];
    let manifest = TestManifest {
        architecture: architecture.to_owned(),
        build_id: "rust-current-target".to_owned(),
        entrypoint: entrypoint.to_owned(),
        executor_version: version.to_owned(),
        package_sha256: inventory_digest(&files),
        package_size: files.iter().map(|file| file.size).sum(),
        files,
        manifest_version: "1".to_owned(),
        platform: platform.to_owned(),
    };
    let mut manifest_bytes = serde_json::to_vec(&manifest).expect("serialize fixture manifest");
    manifest_bytes.push(b'\n');
    let signing_key = SigningKey::from_bytes(&TEST_SEED);
    sign_manifest(&package.path, &signing_key, &manifest_bytes);
    (package, signing_key)
}

fn verifier(signing_key: &SigningKey) -> ExecutorPackageVerifier {
    ExecutorPackageVerifier::new(
        signing_key.verifying_key().to_bytes(),
        ">=1.0.0, <2.0.0",
        Some("1.2.0"),
    )
    .expect("valid verifier policy")
}

#[test]
fn current_target_package_is_verified_with_semver_policy_and_complete_inventory() {
    let (package, signing_key) =
        create_signed_package("1.3.0", current_platform(), current_architecture());

    let verified = verifier(&signing_key)
        .verify_current(&package.path)
        .expect("verify current package");

    assert_eq!(verified.version().to_string(), "1.3.0");
    assert_eq!(verified.build_id(), "rust-current-target");
    assert_eq!(
        verified.entrypoint_path(),
        package.path.join(if cfg!(target_os = "windows") {
            "automation-tool-executor.exe"
        } else {
            "automation-tool-executor"
        })
    );
    assert_eq!(verified.file_count(), 2);
    assert_eq!(verified.package_size(), 31);
}

#[test]
fn version_range_rollback_platform_and_architecture_are_all_fail_closed() {
    let (rollback, signing_key) =
        create_signed_package("1.1.9", current_platform(), current_architecture());
    assert_eq!(
        verifier(&signing_key)
            .verify_current(&rollback.path)
            .expect_err("rollback must fail")
            .code(),
        ExecutorPackageErrorCode::RollbackRejected
    );

    let (outside_range, signing_key) =
        create_signed_package("2.0.0", current_platform(), current_architecture());
    assert_eq!(
        verifier(&signing_key)
            .verify_current(&outside_range.path)
            .expect_err("outside range must fail")
            .code(),
        ExecutorPackageErrorCode::VersionRejected
    );

    let (prerelease, signing_key) =
        create_signed_package("1.3.0-rc.1", current_platform(), current_architecture());
    assert_eq!(
        verifier(&signing_key)
            .verify_current(&prerelease.path)
            .expect_err("prerelease must be explicitly allowed")
            .code(),
        ExecutorPackageErrorCode::VersionRejected
    );
    let prerelease_verifier = ExecutorPackageVerifier::new(
        signing_key.verifying_key().to_bytes(),
        ">=1.3.0-rc.1, <2.0.0",
        Some("1.2.0"),
    )
    .expect("explicit prerelease policy");
    assert_eq!(
        prerelease_verifier
            .verify_current(&prerelease.path)
            .expect("explicit prerelease must pass")
            .version()
            .to_string(),
        "1.3.0-rc.1"
    );

    let other_platform = if cfg!(target_os = "windows") {
        "macos"
    } else {
        "windows"
    };
    let (wrong_platform, signing_key) =
        create_signed_package("1.3.0", other_platform, current_architecture());
    assert_eq!(
        verifier(&signing_key)
            .verify_current(&wrong_platform.path)
            .expect_err("wrong platform must fail")
            .code(),
        ExecutorPackageErrorCode::PlatformMismatch
    );

    let other_architecture = if cfg!(target_arch = "x86_64") {
        "aarch64"
    } else {
        "x86_64"
    };
    let (wrong_architecture, signing_key) =
        create_signed_package("1.3.0", current_platform(), other_architecture);
    assert_eq!(
        verifier(&signing_key)
            .verify_current(&wrong_architecture.path)
            .expect_err("wrong architecture must fail")
            .code(),
        ExecutorPackageErrorCode::PlatformMismatch
    );
}

#[test]
fn wrong_signature_and_payload_tampering_are_rejected_without_reflection() {
    let (package, signing_key) =
        create_signed_package("1.3.0", current_platform(), current_architecture());
    let wrong_key = SigningKey::from_bytes(&[42; 32]);
    let error = verifier(&wrong_key)
        .verify_current(&package.path)
        .expect_err("wrong signer must fail");
    assert_eq!(error.code(), ExecutorPackageErrorCode::SignatureInvalid);
    assert_eq!(error.to_string(), "executor package is rejected");
    assert!(!error
        .to_string()
        .contains(package.path.to_string_lossy().as_ref()));

    fs::write(package.path.join("_internal/runtime.dat"), b"tampered").expect("tamper payload");
    assert_eq!(
        verifier(&signing_key)
            .verify_current(&package.path)
            .expect_err("tamper must fail")
            .code(),
        ExecutorPackageErrorCode::DigestMismatch
    );
}

#[test]
fn signed_but_noncanonical_unknown_or_duplicate_manifests_are_rejected() {
    let (package, signing_key) =
        create_signed_package("1.3.0", current_platform(), current_architecture());
    let path = package.path.join("executor-manifest.v1.json");
    let canonical = fs::read_to_string(&path).expect("read canonical manifest");

    let unknown = canonical.replacen('{', "{\"unknown\":true,", 1);
    sign_manifest(&package.path, &signing_key, unknown.as_bytes());
    assert_eq!(
        verifier(&signing_key)
            .verify_current(&package.path)
            .expect_err("unknown field must fail")
            .code(),
        ExecutorPackageErrorCode::ManifestInvalid
    );

    let duplicate = canonical.replacen('{', "{\"platform\":\"macos\",", 1);
    sign_manifest(&package.path, &signing_key, duplicate.as_bytes());
    assert_eq!(
        verifier(&signing_key)
            .verify_current(&package.path)
            .expect_err("duplicate field must fail")
            .code(),
        ExecutorPackageErrorCode::ManifestInvalid
    );

    let noncanonical = canonical.replace(',', ", ");
    sign_manifest(&package.path, &signing_key, noncanonical.as_bytes());
    assert_eq!(
        verifier(&signing_key)
            .verify_current(&package.path)
            .expect_err("noncanonical bytes must fail")
            .code(),
        ExecutorPackageErrorCode::ManifestInvalid
    );
}

#[test]
fn trusted_signer_cannot_bypass_manifest_identity_rules() {
    let (package, signing_key) =
        create_signed_package("1.3.0", current_platform(), current_architecture());
    let canonical = fs::read_to_string(package.path.join("executor-manifest.v1.json"))
        .expect("read canonical manifest");
    let mutations = [
        canonical.replace("\"manifest_version\":\"1\"", "\"manifest_version\":\"2\""),
        canonical.replace(
            "\"build_id\":\"rust-current-target\"",
            "\"build_id\":\"../escape\"",
        ),
        canonical.replace(
            "\"executor_version\":\"1.3.0\"",
            "\"executor_version\":\"01.3.0\"",
        ),
        canonical.replace(
            &format!(
                "\"entrypoint\":\"{}\"",
                if cfg!(target_os = "windows") {
                    "automation-tool-executor.exe"
                } else {
                    "automation-tool-executor"
                }
            ),
            "\"entrypoint\":\"wrong-executor\"",
        ),
    ];

    for mutation in mutations {
        sign_manifest(&package.path, &signing_key, mutation.as_bytes());
        assert_eq!(
            verifier(&signing_key)
                .verify_current(&package.path)
                .expect_err("invalid signed identity must fail")
                .code(),
            ExecutorPackageErrorCode::ManifestInvalid
        );
    }
}

#[test]
fn malformed_signature_envelopes_are_rejected_before_manifest_use() {
    let (package, signing_key) =
        create_signed_package("1.3.0", current_platform(), current_architecture());
    let signature_path = package.path.join("executor-manifest.v1.sig");
    let valid = fs::read_to_string(&signature_path).expect("read signature");
    let malformed = [
        String::new(),
        valid.trim_end().to_owned(),
        valid.replace("atems1.", "wrong."),
        format!("{}=\n", valid.trim_end()),
        "atems1.AA\n".to_owned(),
        "atems1.invalid+alphabet\n".to_owned(),
    ];

    for envelope in malformed {
        fs::write(&signature_path, envelope).expect("write malformed signature");
        assert_eq!(
            verifier(&signing_key)
                .verify_current(&package.path)
                .expect_err("malformed signature must fail")
                .code(),
            ExecutorPackageErrorCode::SignatureInvalid
        );
    }
}

#[test]
fn missing_or_extra_directory_members_are_rejected() {
    let (package, signing_key) =
        create_signed_package("1.3.0", current_platform(), current_architecture());
    fs::write(package.path.join("unexpected"), b"extra").expect("write extra file");
    assert_eq!(
        verifier(&signing_key)
            .verify_current(&package.path)
            .expect_err("extra file must fail")
            .code(),
        ExecutorPackageErrorCode::PackageInvalid
    );

    fs::remove_file(package.path.join("unexpected")).expect("remove extra file");
    fs::remove_file(package.path.join("_internal/runtime.dat")).expect("remove payload file");
    assert_eq!(
        verifier(&signing_key)
            .verify_current(&package.path)
            .expect_err("missing file must fail")
            .code(),
        ExecutorPackageErrorCode::PackageInvalid
    );
}

#[cfg(unix)]
#[test]
fn every_package_symlink_is_rejected() {
    use std::os::unix::fs::symlink;

    let (package, signing_key) =
        create_signed_package("1.3.0", current_platform(), current_architecture());
    symlink(
        package.path.join("_internal/runtime.dat"),
        package.path.join("linked-runtime"),
    )
    .expect("create symlink");

    assert_eq!(
        verifier(&signing_key)
            .verify_current(&package.path)
            .expect_err("symlink must fail")
            .code(),
        ExecutorPackageErrorCode::PackageInvalid
    );
}

#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
#[test]
fn python_generated_cross_language_fixture_is_verified_by_the_rust_boundary() {
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../contracts/fixtures/executor-package-v1/valid");
    let package = TemporaryPackage::new();
    fs::copy(
        fixture.join("package/automation-tool-executor"),
        package.path.join("automation-tool-executor"),
    )
    .expect("copy fixture entrypoint");
    fs::copy(
        fixture.join("package/_internal/runtime.dat"),
        package.path.join("_internal/runtime.dat"),
    )
    .expect("copy fixture runtime");
    fs::copy(
        fixture.join("executor-manifest.v1.json"),
        package.path.join("executor-manifest.v1.json"),
    )
    .expect("copy fixture manifest");
    fs::copy(
        fixture.join("executor-manifest.v1.sig"),
        package.path.join("executor-manifest.v1.sig"),
    )
    .expect("copy fixture signature");
    let signing_key = SigningKey::from_bytes(&TEST_SEED);
    let verifier =
        ExecutorPackageVerifier::new(signing_key.verifying_key().to_bytes(), "^0.1.0", None)
            .expect("fixture policy");

    let verified = verifier
        .verify_current(&package.path)
        .expect("verify Python fixture");

    assert_eq!(verified.version().to_string(), "0.1.0");
    assert_eq!(verified.build_id(), "fixture-build-1");
}

#[test]
fn malformed_verifying_keys_and_version_policies_are_rejected() {
    let mut weak_key = [0_u8; 32];
    weak_key[0] = 1;
    assert_eq!(
        ExecutorPackageVerifier::new(weak_key, ">=1.0.0", None)
            .expect_err("weak public key")
            .code(),
        ExecutorPackageErrorCode::ConfigurationInvalid
    );
    assert_eq!(
        ExecutorPackageVerifier::new(
            SigningKey::from_bytes(&TEST_SEED)
                .verifying_key()
                .to_bytes(),
            "*",
            None,
        )
        .expect_err("unbounded requirement")
        .code(),
        ExecutorPackageErrorCode::ConfigurationInvalid
    );
    assert_eq!(
        ExecutorPackageVerifier::new(
            SigningKey::from_bytes(&TEST_SEED)
                .verifying_key()
                .to_bytes(),
            "not a requirement",
            None,
        )
        .expect_err("invalid requirement")
        .code(),
        ExecutorPackageErrorCode::ConfigurationInvalid
    );
    assert_eq!(
        ExecutorPackageVerifier::new(
            SigningKey::from_bytes(&TEST_SEED)
                .verifying_key()
                .to_bytes(),
            ">=1.0.0",
            Some("not-semver"),
        )
        .expect_err("invalid installed version")
        .code(),
        ExecutorPackageErrorCode::ConfigurationInvalid
    );
}

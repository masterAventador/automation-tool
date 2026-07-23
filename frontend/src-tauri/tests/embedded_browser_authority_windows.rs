#![cfg(windows)]

use automation_tool_desktop_lib::embedded_browser_authority::{
    release_target_id, EmbeddedBrowserAuthority, EmbeddedBrowserAuthorityError,
};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::PathBuf;
use std::process::Command;

const TARGET: &str = "windows-x86_64";
const EXECUTABLE: &str = "chrome-win64/chrome.exe";

fn sha256_hex(payload: &[u8]) -> String {
    let mut digest = Sha256::new();
    digest.update(payload);
    digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn pe_binary() -> Vec<u8> {
    let mut binary = vec![0_u8; 0x86];
    binary[..2].copy_from_slice(b"MZ");
    binary[0x3c..0x40].copy_from_slice(&(0x80_u32).to_le_bytes());
    binary[0x80..0x84].copy_from_slice(b"PE\0\0");
    binary[0x84..0x86].copy_from_slice(&0x8664_u16.to_le_bytes());
    binary
}

struct FixtureTree {
    base: PathBuf,
    resource_dir: PathBuf,
}

impl Drop for FixtureTree {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.base);
    }
}

static FIXTURE_COUNTER: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);

fn write_fixture() -> FixtureTree {
    let index = FIXTURE_COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    let base = std::env::temp_dir().join(format!(
        "automation-tool-eb07-win-{}-{index}",
        std::process::id()
    ));
    let resource_dir = base.join("resources");
    let root = resource_dir.join("embedded-browser");
    let binary = pe_binary();
    let executable = root.join(EXECUTABLE);
    fs::create_dir_all(executable.parent().expect("parent")).expect("mkdir");
    fs::write(&executable, &binary).expect("write PE");
    let manifest = serde_json::json!({
        "schemaVersion": 1,
        "policy": "fail_closed",
        "verified_at": "2026-07-24",
        "target": TARGET,
        "runtime": {
            "playwright_python": "1.61.0",
            "chromium": {
                "title": "Chrome for Testing",
                "browser_version": "149.0.7827.55",
                "revision": "1228",
            },
            "browser_use": "0.13.6",
            "render_engine": "0.7.68",
        },
        "executable": EXECUTABLE,
        "source": {
            "download_url": "https://cdn.playwright.dev/chrome-win64.zip",
            "archive_sha256": "ab".repeat(32),
        },
        "fileCount": 1,
        "totalBytes": binary.len(),
        "entries": [{
            "path": EXECUTABLE,
            "type": "file",
            "size": binary.len(),
            "sha256": sha256_hex(&binary),
            "executable": true,
        }],
        "licenses": {},
        "sbom": [],
    });
    fs::write(
        root.join("distribution-manifest.v1.json"),
        serde_json::to_vec_pretty(&manifest).expect("manifest"),
    )
    .expect("write manifest");
    FixtureTree { base, resource_dir }
}

#[test]
fn native_release_target_is_windows_x86_64() {
    assert_eq!(release_target_id(), TARGET);
}

#[test]
fn missing_distribution_reports_component_missing() {
    let fixture = write_fixture();
    fs::remove_dir_all(fixture.resource_dir.join("embedded-browser")).expect("remove");
    let authority = EmbeddedBrowserAuthority::new(fixture.resource_dir.clone(), TARGET);
    assert!(matches!(
        authority.resolve(),
        Err(EmbeddedBrowserAuthorityError::ComponentMissing)
    ));
}

#[test]
fn valid_distribution_resolves_and_caches_the_verified_executable() {
    let fixture = write_fixture();
    let authority = EmbeddedBrowserAuthority::new(fixture.resource_dir.clone(), TARGET);
    let first = authority.resolve().expect("first resolve");
    assert!(first.ends_with("chrome.exe"));
    assert!(first.is_file());
    assert_eq!(first, authority.resolve().expect("cached resolve"));
}

#[test]
fn tampered_distribution_reports_component_invalid() {
    let fixture = write_fixture();
    fs::write(
        fixture
            .resource_dir
            .join("embedded-browser")
            .join(EXECUTABLE),
        b"tampered-binary",
    )
    .expect("tamper");
    let authority = EmbeddedBrowserAuthority::new(fixture.resource_dir.clone(), TARGET);
    assert!(matches!(
        authority.resolve(),
        Err(EmbeddedBrowserAuthorityError::ComponentInvalid)
    ));
}

#[test]
fn executable_removed_after_caching_is_detected_on_next_resolve() {
    let fixture = write_fixture();
    let authority = EmbeddedBrowserAuthority::new(fixture.resource_dir.clone(), TARGET);
    let executable = authority.resolve().expect("first resolve");
    fs::remove_file(executable).expect("remove executable");
    assert!(authority.resolve().is_err());
}

#[test]
fn distribution_root_junction_after_caching_forces_full_revalidation() {
    let fixture = write_fixture();
    let authority = EmbeddedBrowserAuthority::new(fixture.resource_dir.clone(), TARGET);
    authority.resolve().expect("first resolve");

    let root = fixture.resource_dir.join("embedded-browser");
    let moved = fixture.resource_dir.join("embedded-browser-real");
    fs::rename(&root, &moved).expect("move distribution root");
    let output = Command::new("cmd.exe")
        .args([
            "/d",
            "/c",
            "mklink",
            "/J",
            root.to_str().expect("root path"),
            moved.to_str().expect("target path"),
        ])
        .output()
        .expect("create junction");
    assert!(output.status.success(), "mklink /J failed");

    let second = authority.resolve();
    fs::remove_dir(&root).expect("remove junction without following it");
    fs::rename(&moved, &root).expect("restore distribution root");
    assert!(matches!(
        second,
        Err(EmbeddedBrowserAuthorityError::ComponentInvalid)
    ));
}

#[test]
fn debug_output_reveals_no_paths() {
    let fixture = write_fixture();
    let authority = EmbeddedBrowserAuthority::new(fixture.resource_dir.clone(), TARGET);
    authority.resolve().expect("resolve");
    let debug = format!("{authority:?}");
    assert!(!debug.contains(fixture.resource_dir.to_str().expect("utf8")));
    assert!(!debug.contains("chrome-win64"));
}

#[test]
fn version_drift_reports_version_incompatible_not_generic_damage() {
    let fixture = write_fixture();
    let manifest_path = fixture
        .resource_dir
        .join("embedded-browser")
        .join("distribution-manifest.v1.json");
    let mut document: serde_json::Value =
        serde_json::from_slice(&fs::read(&manifest_path).expect("read")).expect("parse");
    document["runtime"]["chromium"]["browser_version"] = serde_json::json!("150.0.0.0");
    fs::write(
        &manifest_path,
        serde_json::to_vec_pretty(&document).expect("json"),
    )
    .expect("write");
    let authority = EmbeddedBrowserAuthority::new(fixture.resource_dir.clone(), TARGET);
    assert!(matches!(
        authority.resolve(),
        Err(EmbeddedBrowserAuthorityError::VersionIncompatible)
    ));
}

#[test]
#[ignore = "requires a real staged distribution; run via scripts/run_eb_07_acceptance.py"]
fn real_distribution_resolves_through_the_authority() {
    let resource_dir = std::env::var("EB07_REAL_RESOURCE_DIR").expect("EB07_REAL_RESOURCE_DIR");
    let authority = EmbeddedBrowserAuthority::new(PathBuf::from(resource_dir), TARGET);
    let first = authority.resolve().expect("real distribution must resolve");
    assert!(first.is_file());
    assert_eq!(first, authority.resolve().expect("cached resolve"));
}

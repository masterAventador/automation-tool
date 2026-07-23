#![cfg(windows)]

use automation_tool_desktop_lib::embedded_browser_distribution::{
    EmbeddedBrowserDistribution, EmbeddedBrowserError,
};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::PathBuf;

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

fn pe_binary(machine: u16) -> Vec<u8> {
    let mut binary = vec![0_u8; 0x86];
    binary[..2].copy_from_slice(b"MZ");
    binary[0x3c..0x40].copy_from_slice(&(0x80_u32).to_le_bytes());
    binary[0x80..0x84].copy_from_slice(b"PE\0\0");
    binary[0x84..0x86].copy_from_slice(&machine.to_le_bytes());
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

fn write_fixture(machine: u16) -> FixtureTree {
    let index = FIXTURE_COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    let base = std::env::temp_dir().join(format!(
        "automation-tool-eb06-win-{}-{index}",
        std::process::id()
    ));
    let resource_dir = base.join("resources");
    let root = resource_dir.join("embedded-browser");
    let binary = pe_binary(machine);
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
fn windows_x86_64_distribution_accepts_only_an_amd64_pe() {
    let valid = write_fixture(0x8664);
    let distribution = EmbeddedBrowserDistribution::load_for_target(&valid.resource_dir, TARGET)
        .expect("AMD64 PE must load");
    assert!(distribution.executable_path().ends_with("chrome.exe"));

    let wrong_machine = write_fixture(0xaa64);
    assert!(matches!(
        EmbeddedBrowserDistribution::load_for_target(&wrong_machine.resource_dir, TARGET),
        Err(EmbeddedBrowserError::Invalid(_))
    ));
}

#[test]
fn windows_target_rejects_a_macos_executable_contract() {
    let fixture = write_fixture(0x8664);
    let manifest_path = fixture
        .resource_dir
        .join("embedded-browser/distribution-manifest.v1.json");
    let mut manifest: serde_json::Value =
        serde_json::from_slice(&fs::read(&manifest_path).expect("read")).expect("parse");
    manifest["executable"] = serde_json::json!(
        "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    );
    fs::write(
        manifest_path,
        serde_json::to_vec_pretty(&manifest).expect("serialize"),
    )
    .expect("rewrite");
    assert!(matches!(
        EmbeddedBrowserDistribution::load_for_target(&fixture.resource_dir, TARGET),
        Err(EmbeddedBrowserError::Invalid(_))
    ));
}

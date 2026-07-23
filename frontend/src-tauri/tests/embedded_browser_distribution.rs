//! EB-06: fail-closed resolution of the packaged embedded-browser distribution.
#![cfg(target_os = "macos")]

use automation_tool_desktop_lib::embedded_browser_distribution::{
    EmbeddedBrowserDistribution, EmbeddedBrowserError,
};
use sha2::{Digest, Sha256};
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

const TARGET: &str = "macos-arm64";
const ROOT_ENTRY: &str = "chrome-mac-arm64";
const EXECUTABLE: &str =
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing";
const FRAMEWORK_FILE: &str =
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/Frameworks/F.framework/Versions/A/F";
const FRAMEWORK_LINK: &str =
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/Frameworks/F.framework/Versions/Current";

fn sha256_hex(payload: &[u8]) -> String {
    let mut digest = Sha256::new();
    digest.update(payload);
    let bytes = digest.finalize();
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
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

fn unique_base() -> PathBuf {
    let index = FIXTURE_COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    std::env::temp_dir().join(format!(
        "automation-tool-eb06-{}-{index}",
        std::process::id()
    ))
}

fn entry_json(path: &str, payload: &[u8], executable: bool) -> serde_json::Value {
    serde_json::json!({
        "path": path,
        "type": "file",
        "size": payload.len(),
        "sha256": sha256_hex(payload),
        "executable": executable,
    })
}

fn write_fixture() -> FixtureTree {
    let base = unique_base();
    fs::create_dir_all(&base).expect("fixture base");
    let resource_dir = base.join("resources");
    let root = resource_dir.join("embedded-browser");
    let binary = b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01\x00\x00\x00\x00".to_vec();
    let plist = b"<plist/>".to_vec();
    let framework = b"framework".to_vec();

    for (relative, payload, executable) in [
        (EXECUTABLE, &binary, true),
        (
            "chrome-mac-arm64/Google Chrome for Testing.app/Contents/Info.plist",
            &plist,
            false,
        ),
        (FRAMEWORK_FILE, &framework, false),
    ] {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().expect("parent")).expect("mkdir");
        fs::write(&path, payload).expect("write");
        let mode = if executable { 0o755 } else { 0o644 };
        fs::set_permissions(&path, fs::Permissions::from_mode(mode)).expect("chmod");
    }
    std::os::unix::fs::symlink("A", root.join(FRAMEWORK_LINK)).expect("symlink");

    let manifest = serde_json::json!({
        "schemaVersion": 1,
        "policy": "fail_closed",
        "verified_at": "2026-07-23",
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
            "download_url": "https://cdn.playwright.dev/builds/cft/149.0.7827.55/mac-arm64/chrome-mac-arm64.zip",
            "archive_sha256": "311211b54c429245e2cec0314ee1e314085e9c00350215b95e1a879350786630",
        },
        "fileCount": 3,
        "totalBytes": binary.len() + plist.len() + framework.len(),
        "entries": [
            entry_json(EXECUTABLE, &binary, true),
            entry_json(
                "chrome-mac-arm64/Google Chrome for Testing.app/Contents/Info.plist",
                &plist,
                false,
            ),
            entry_json(FRAMEWORK_FILE, &framework, false),
            serde_json::json!({
                "path": FRAMEWORK_LINK,
                "type": "symlink",
                "targetPath": "A",
            }),
        ],
        "licenses": {"chromium_build": {"redistribution_review": "pending"}},
        "sbom": [],
    });
    fs::write(
        root.join("distribution-manifest.v1.json"),
        serde_json::to_vec_pretty(&manifest).expect("manifest json"),
    )
    .expect("write manifest");
    FixtureTree { base, resource_dir }
}

fn rewrite_manifest(resource_dir: &Path, mutate: impl FnOnce(&mut serde_json::Value)) {
    let path = resource_dir
        .join("embedded-browser")
        .join("distribution-manifest.v1.json");
    let mut document: serde_json::Value =
        serde_json::from_slice(&fs::read(&path).expect("read manifest")).expect("parse");
    mutate(&mut document);
    fs::write(&path, serde_json::to_vec_pretty(&document).expect("json")).expect("write");
}

#[test]
fn valid_distribution_loads_and_exposes_confined_executable() {
    let fixture = write_fixture();
    let distribution = EmbeddedBrowserDistribution::load_for_target(&fixture.resource_dir, TARGET)
        .expect("valid distribution must load");
    assert_eq!(distribution.browser_version(), "149.0.7827.55");
    let canonical_root =
        fs::canonicalize(fixture.resource_dir.join("embedded-browser")).expect("canonical root");
    assert!(distribution
        .executable_path()
        .starts_with(canonical_root.join(ROOT_ENTRY)));
    let debug = format!("{distribution:?}");
    assert!(!debug.contains(canonical_root.to_str().expect("utf8 path")));
}

#[test]
fn tampered_file_is_rejected() {
    let fixture = write_fixture();
    fs::write(
        fixture
            .resource_dir
            .join("embedded-browser")
            .join("chrome-mac-arm64/Google Chrome for Testing.app/Contents/Info.plist"),
        b"<tampered/>",
    )
    .expect("tamper");
    assert!(matches!(
        EmbeddedBrowserDistribution::load_for_target(&fixture.resource_dir, TARGET),
        Err(EmbeddedBrowserError::Invalid(_))
    ));
}

#[test]
fn missing_and_extra_files_are_rejected() {
    let fixture = write_fixture();
    let root = fixture.resource_dir.join("embedded-browser");
    fs::write(root.join(ROOT_ENTRY).join("extra.bin"), b"x").expect("extra");
    assert!(matches!(
        EmbeddedBrowserDistribution::load_for_target(&fixture.resource_dir, TARGET),
        Err(EmbeddedBrowserError::Invalid(_))
    ));
    fs::remove_file(root.join(ROOT_ENTRY).join("extra.bin")).expect("cleanup");
    fs::remove_file(root.join(FRAMEWORK_FILE)).expect("remove");
    assert!(matches!(
        EmbeddedBrowserDistribution::load_for_target(&fixture.resource_dir, TARGET),
        Err(EmbeddedBrowserError::Invalid(_))
    ));
}

#[test]
fn undeclared_wrong_target_and_escaping_symlinks_are_rejected() {
    let fixture = write_fixture();
    let root = fixture.resource_dir.join("embedded-browser");

    std::os::unix::fs::symlink("A", root.join(ROOT_ENTRY).join("undeclared-link"))
        .expect("undeclared symlink");
    assert!(matches!(
        EmbeddedBrowserDistribution::load_for_target(&fixture.resource_dir, TARGET),
        Err(EmbeddedBrowserError::Invalid(_))
    ));
    fs::remove_file(root.join(ROOT_ENTRY).join("undeclared-link")).expect("cleanup");

    fs::remove_file(root.join(FRAMEWORK_LINK)).expect("remove link");
    std::os::unix::fs::symlink("B", root.join(FRAMEWORK_LINK)).expect("wrong target");
    assert!(matches!(
        EmbeddedBrowserDistribution::load_for_target(&fixture.resource_dir, TARGET),
        Err(EmbeddedBrowserError::Invalid(_))
    ));

    fs::remove_file(root.join(FRAMEWORK_LINK)).expect("remove link");
    std::os::unix::fs::symlink("../../../../../../../etc", root.join(FRAMEWORK_LINK))
        .expect("escaping");
    rewrite_manifest(&fixture.resource_dir, |document| {
        let entries = document["entries"].as_array_mut().expect("entries");
        entries[3]["targetPath"] = serde_json::json!("../../../../../../../etc");
    });
    assert!(matches!(
        EmbeddedBrowserDistribution::load_for_target(&fixture.resource_dir, TARGET),
        Err(EmbeddedBrowserError::Invalid(_))
    ));
}

#[test]
fn platform_mismatch_and_unknown_target_are_rejected() {
    let fixture = write_fixture();
    assert!(matches!(
        EmbeddedBrowserDistribution::load_for_target(&fixture.resource_dir, "windows-x86_64"),
        Err(EmbeddedBrowserError::Invalid(_))
    ));
    assert!(matches!(
        EmbeddedBrowserDistribution::load_for_target(&fixture.resource_dir, "linux-x64"),
        Err(EmbeddedBrowserError::Invalid(_))
    ));
}

#[test]
fn runtime_version_drift_is_rejected() {
    let fixture = write_fixture();
    rewrite_manifest(&fixture.resource_dir, |document| {
        document["runtime"]["chromium"]["browser_version"] = serde_json::json!("150.0.0.0");
    });
    assert!(matches!(
        EmbeddedBrowserDistribution::load_for_target(&fixture.resource_dir, TARGET),
        Err(EmbeddedBrowserError::VersionIncompatible)
    ));
}

#[test]
fn second_browser_binary_names_are_rejected() {
    let fixture = write_fixture();
    let root = fixture.resource_dir.join("embedded-browser");
    let shell = root.join(ROOT_ENTRY).join("chrome-headless-shell");
    fs::write(&shell, b"x").expect("write shell");
    rewrite_manifest(&fixture.resource_dir, |document| {
        let entries = document["entries"].as_array_mut().expect("entries");
        entries.push(entry_json(
            "chrome-mac-arm64/chrome-headless-shell",
            b"x",
            false,
        ));
    });
    assert!(matches!(
        EmbeddedBrowserDistribution::load_for_target(&fixture.resource_dir, TARGET),
        Err(EmbeddedBrowserError::Invalid(_))
    ));
}

#[test]
fn distribution_root_replaced_by_symlink_is_rejected() {
    let fixture = write_fixture();
    let root = fixture.resource_dir.join("embedded-browser");
    let moved = fixture.resource_dir.join("embedded-browser-real");
    fs::rename(&root, &moved).expect("move root");
    std::os::unix::fs::symlink(&moved, &root).expect("link root");
    assert!(matches!(
        EmbeddedBrowserDistribution::load_for_target(&fixture.resource_dir, TARGET),
        Err(EmbeddedBrowserError::Invalid(_))
    ));
}

/// 真实验收：EB06_REAL_RESOURCE_DIR 指向由生产 Python 构建器产出的真实资源目录
/// （EB-03 暂存 + EB-05 Manifest）。常规门禁 ignored，验收脚本以 --ignored 运行。
#[test]
#[ignore = "requires a real staged distribution; run via scripts/run_eb_06_acceptance.py"]
fn real_staged_distribution_loads_end_to_end() {
    let resource_dir = std::env::var("EB06_REAL_RESOURCE_DIR").expect("EB06_REAL_RESOURCE_DIR");
    let distribution =
        EmbeddedBrowserDistribution::load_for_target(Path::new(&resource_dir), TARGET)
            .expect("real staged distribution must load");
    assert_eq!(distribution.browser_version(), "149.0.7827.55");
    assert!(distribution.executable_path().is_file());
}

use automation_tool_desktop_lib::video_media_toolchain::VideoMediaToolchain;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(1);

struct TempDirectory(PathBuf);

impl TempDirectory {
    fn new(label: &str) -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-vf04-{label}-{}-{}",
            std::process::id(),
            TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }
}

impl Drop for TempDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn write_package(resource_root: &Path, target_id: &str) -> PathBuf {
    let root = resource_root.join("media-toolchain");
    fs::create_dir_all(root.join("bin")).unwrap();
    fs::create_dir_all(root.join("source")).unwrap();
    let suffix = if target_id == "windows-x86_64" {
        ".exe"
    } else {
        ""
    };
    let files = [
        ("BUILD-INFO.txt".to_owned(), b"locked build\n".to_vec()),
        ("COPYING.GPLv3".to_owned(), b"GPLv3\n".to_vec()),
        ("NOTICE.txt".to_owned(), b"FFmpeg and x264\n".to_vec()),
        (format!("bin/ffmpeg{suffix}"), b"ffmpeg binary\n".to_vec()),
        (format!("bin/ffprobe{suffix}"), b"ffprobe binary\n".to_vec()),
        (
            "source/ffmpeg-8.1.2.tar.xz".to_owned(),
            b"ffmpeg source\n".to_vec(),
        ),
        (
            "source/x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55.tar.gz".to_owned(),
            b"x264 source\n".to_vec(),
        ),
    ];
    let mut entries = Vec::new();
    for (relative, bytes) in files {
        let path = root.join(&relative);
        fs::write(&path, &bytes).unwrap();
        #[cfg(unix)]
        if relative.starts_with("bin/") {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        }
        entries.push(json!({"path": relative, "size": bytes.len(), "sha256": sha256(&bytes)}));
    }
    fs::write(
        root.join("manifest.json"),
        serde_json::to_vec_pretty(&json!({
            "schema_version": 1,
            "target_id": target_id,
            "version": "8.1.2",
            "license": "GPL-3.0-or-later",
            "files": entries,
        }))
        .unwrap(),
    )
    .unwrap();
    root
}

#[test]
fn resolves_one_verified_pair_into_both_worker_environment_contracts() {
    let temp = TempDirectory::new("shared-pair");
    let root = write_package(&temp.0, "macos-arm64");
    let toolchain = VideoMediaToolchain::load_for_target(&temp.0, "macos-arm64").unwrap();

    assert_eq!(toolchain.package_root(), fs::canonicalize(root).unwrap());
    assert_eq!(
        toolchain.intelligent_material_environment()["IMAGEIO_FFMPEG_EXE"],
        toolchain.ffmpeg_path()
    );
    assert_eq!(
        toolchain.brand_motion_environment()["HYPERFRAMES_FFMPEG_PATH"],
        toolchain.ffmpeg_path()
    );
    assert_eq!(
        toolchain.brand_motion_environment()["HYPERFRAMES_FFPROBE_PATH"],
        toolchain.ffprobe_path()
    );
    let debug = format!("{toolchain:?}");
    assert!(debug.contains("macos-arm64"));
    assert!(!debug.contains(temp.0.to_string_lossy().as_ref()));
}

#[test]
fn accepts_the_locked_windows_layout_without_using_system_path() {
    let temp = TempDirectory::new("windows-layout");
    write_package(&temp.0, "windows-x86_64");
    let toolchain = VideoMediaToolchain::load_for_target(&temp.0, "windows-x86_64").unwrap();
    assert!(toolchain.ffmpeg_path().ends_with("bin/ffmpeg.exe"));
    assert!(toolchain.ffprobe_path().ends_with("bin/ffprobe.exe"));
}

#[test]
fn rejects_digest_tampering_missing_extra_and_target_drift() {
    for scenario in ["digest", "missing", "extra", "target"] {
        let temp = TempDirectory::new(scenario);
        let root = write_package(&temp.0, "macos-arm64");
        match scenario {
            "digest" => fs::write(root.join("bin/ffmpeg"), b"same-ish tamper").unwrap(),
            "missing" => fs::remove_file(root.join("NOTICE.txt")).unwrap(),
            "extra" => fs::write(root.join("untracked"), b"extra").unwrap(),
            "target" => {
                let manifest_path = root.join("manifest.json");
                let mut manifest: serde_json::Value =
                    serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
                manifest["target_id"] = json!("windows-x86_64");
                fs::write(manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
            }
            _ => unreachable!(),
        }
        assert!(VideoMediaToolchain::load_for_target(&temp.0, "macos-arm64").is_err());
    }
}

#[cfg(unix)]
#[test]
fn rejects_linked_roots_files_and_non_executable_programs() {
    use std::os::unix::fs::{symlink, PermissionsExt};

    let temp = TempDirectory::new("links");
    let root = write_package(&temp.0, "macos-arm64");
    let original = root.join("NOTICE.txt");
    let replacement = temp.0.join("replacement");
    fs::rename(&original, &replacement).unwrap();
    symlink(&replacement, &original).unwrap();
    assert!(VideoMediaToolchain::load_for_target(&temp.0, "macos-arm64").is_err());

    fs::remove_file(&original).unwrap();
    fs::rename(&replacement, &original).unwrap();
    fs::set_permissions(root.join("bin/ffmpeg"), fs::Permissions::from_mode(0o600)).unwrap();
    assert!(VideoMediaToolchain::load_for_target(&temp.0, "macos-arm64").is_err());
}

#[test]
fn rejects_traversal_unknown_fields_and_unsupported_target() {
    let temp = TempDirectory::new("schema");
    let root = write_package(&temp.0, "macos-arm64");
    let manifest_path = root.join("manifest.json");
    let mut manifest: serde_json::Value =
        serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
    manifest["unexpected"] = json!(true);
    fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
    assert!(VideoMediaToolchain::load_for_target(&temp.0, "macos-arm64").is_err());
    assert!(VideoMediaToolchain::load_for_target(&temp.0, "linux-x86_64").is_err());

    write_package(&temp.0, "macos-arm64");
    let mut manifest: serde_json::Value =
        serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
    manifest["files"][0]["path"] = json!("../escape");
    fs::write(manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
    assert!(VideoMediaToolchain::load_for_target(&temp.0, "macos-arm64").is_err());
}

#[test]
fn source_is_not_exposed_as_a_webview_command_or_system_path_fallback() {
    let source = include_str!("../src/video_media_toolchain.rs");
    assert!(!source.contains("#[tauri::command]"));
    assert!(!source.contains("std::env::var(\"PATH\")"));
    assert!(!source.contains("which::"));
    assert!(!source.contains("download"));
}

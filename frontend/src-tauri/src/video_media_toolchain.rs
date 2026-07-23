//! Fail-closed resolver for the single packaged FFmpeg/ffprobe pair.

use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Component, Path, PathBuf};

const TOOLCHAIN_DIRECTORY: &str = "media-toolchain";
const MANIFEST_FILE: &str = "manifest.json";
const TOOLCHAIN_VERSION: &str = "8.1.2";
const TOOLCHAIN_LICENSE: &str = "GPL-3.0-or-later";
const MAX_MANIFEST_BYTES: u64 = 512 * 1024;
const MAX_FILE_COUNT: usize = 16;
const MAX_FILE_BYTES: u64 = 256 * 1024 * 1024;

#[derive(Debug)]
pub enum VideoMediaToolchainError {
    Io(io::Error),
    Invalid(&'static str),
    InvalidDetail(String),
}

impl fmt::Display for VideoMediaToolchainError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "media toolchain I/O failure: {error}"),
            Self::Invalid(message) => write!(formatter, "invalid media toolchain: {message}"),
            Self::InvalidDetail(message) => write!(formatter, "invalid media toolchain: {message}"),
        }
    }
}

impl std::error::Error for VideoMediaToolchainError {}

impl From<io::Error> for VideoMediaToolchainError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RuntimeManifest {
    schema_version: u32,
    target_id: String,
    version: String,
    license: String,
    files: Vec<RuntimeFile>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RuntimeFile {
    path: String,
    size: u64,
    sha256: String,
}

/// Verified native tool paths. It deliberately has no serde implementation and
/// its Debug output never reveals installation paths to the WebView or logs.
pub struct VideoMediaToolchain {
    root: PathBuf,
    ffmpeg: PathBuf,
    ffprobe: PathBuf,
    target_id: String,
}

impl fmt::Debug for VideoMediaToolchain {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VideoMediaToolchain")
            .field("target_id", &self.target_id)
            .field("version", &TOOLCHAIN_VERSION)
            .finish_non_exhaustive()
    }
}

impl VideoMediaToolchain {
    pub fn load(resource_directory: &Path) -> Result<Self, VideoMediaToolchainError> {
        Self::load_for_target(resource_directory, release_target_id())
    }

    pub fn load_for_target(
        resource_directory: &Path,
        target_id: &str,
    ) -> Result<Self, VideoMediaToolchainError> {
        if !matches!(target_id, "macos-arm64" | "windows-x86_64") {
            return Err(VideoMediaToolchainError::Invalid(
                "unsupported release target",
            ));
        }
        reject_link(resource_directory)?;
        let resource_root = fs::canonicalize(resource_directory)?;
        let root = resource_root.join(TOOLCHAIN_DIRECTORY);
        reject_link(&root)?;
        let root = fs::canonicalize(&root)?;
        ensure_direct_child(&resource_root, &root)?;

        let manifest_path = root.join(MANIFEST_FILE);
        let manifest_bytes = read_bounded(&manifest_path, MAX_MANIFEST_BYTES)?;
        let manifest: RuntimeManifest = serde_json::from_slice(&manifest_bytes)
            .map_err(|_| VideoMediaToolchainError::Invalid("manifest schema is invalid"))?;
        if manifest.schema_version != 1
            || manifest.target_id != target_id
            || manifest.version != TOOLCHAIN_VERSION
            || manifest.license != TOOLCHAIN_LICENSE
        {
            return Err(VideoMediaToolchainError::Invalid(
                "manifest identity mismatch",
            ));
        }
        if manifest.files.is_empty() || manifest.files.len() > MAX_FILE_COUNT {
            return Err(VideoMediaToolchainError::Invalid(
                "manifest file count is invalid",
            ));
        }

        let required = required_paths(target_id);
        let mut entries = BTreeMap::new();
        for entry in manifest.files {
            validate_relative_path(&entry.path)?;
            if entry.size == 0 || entry.size > MAX_FILE_BYTES || !is_sha256(&entry.sha256) {
                return Err(VideoMediaToolchainError::Invalid(
                    "manifest file metadata is invalid",
                ));
            }
            if entries.insert(entry.path.clone(), entry).is_some() {
                return Err(VideoMediaToolchainError::Invalid(
                    "manifest path is duplicated",
                ));
            }
        }
        if entries.keys().cloned().collect::<BTreeSet<_>>() != required {
            return Err(VideoMediaToolchainError::Invalid(
                "manifest file set is incomplete",
            ));
        }

        let actual = collect_file_set(&root)?;
        let mut expected = required.clone();
        expected.insert(MANIFEST_FILE.to_owned());
        if actual != expected {
            return Err(VideoMediaToolchainError::Invalid(
                "package contains missing or extra files",
            ));
        }

        for (relative, entry) in &entries {
            let path = root.join(relative);
            reject_link(&path)?;
            let canonical = fs::canonicalize(&path)?;
            ensure_contained(&root, &canonical)?;
            let metadata = fs::metadata(&canonical)?;
            if !metadata.is_file() || metadata.len() != entry.size {
                return Err(VideoMediaToolchainError::Invalid(
                    "packaged file size mismatch",
                ));
            }
            if hash_file(&canonical)? != entry.sha256 {
                return Err(VideoMediaToolchainError::Invalid(
                    "packaged file digest mismatch",
                ));
            }
        }

        let suffix = if target_id == "windows-x86_64" {
            ".exe"
        } else {
            ""
        };
        let ffmpeg = root.join(format!("bin/ffmpeg{suffix}"));
        let ffprobe = root.join(format!("bin/ffprobe{suffix}"));
        assert_executable(&ffmpeg, target_id)?;
        assert_executable(&ffprobe, target_id)?;
        Ok(Self {
            root,
            ffmpeg,
            ffprobe,
            target_id: target_id.to_owned(),
        })
    }

    pub fn intelligent_material_environment(&self) -> BTreeMap<&'static str, &Path> {
        BTreeMap::from([("IMAGEIO_FFMPEG_EXE", self.ffmpeg.as_path())])
    }

    pub fn brand_motion_environment(&self) -> BTreeMap<&'static str, &Path> {
        BTreeMap::from([
            ("HYPERFRAMES_FFMPEG_PATH", self.ffmpeg.as_path()),
            ("HYPERFRAMES_FFPROBE_PATH", self.ffprobe.as_path()),
        ])
    }

    pub fn ffmpeg_path(&self) -> &Path {
        &self.ffmpeg
    }

    pub fn ffprobe_path(&self) -> &Path {
        &self.ffprobe
    }

    pub fn package_root(&self) -> &Path {
        &self.root
    }
}

fn release_target_id() -> &'static str {
    if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        "macos-arm64"
    } else if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        "windows-x86_64"
    } else {
        "unsupported"
    }
}

fn required_paths(target_id: &str) -> BTreeSet<String> {
    let suffix = if target_id == "windows-x86_64" {
        ".exe"
    } else {
        ""
    };
    BTreeSet::from([
        "BUILD-INFO.txt".to_owned(),
        "COPYING.GPLv3".to_owned(),
        "NOTICE.txt".to_owned(),
        format!("bin/ffmpeg{suffix}"),
        format!("bin/ffprobe{suffix}"),
        "source/ffmpeg-8.1.2.tar.xz".to_owned(),
        "source/x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55.tar.gz".to_owned(),
    ])
}

fn read_bounded(path: &Path, maximum: u64) -> Result<Vec<u8>, VideoMediaToolchainError> {
    reject_link(path)?;
    let metadata = fs::metadata(path)?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > maximum {
        return Err(VideoMediaToolchainError::Invalid(
            "manifest size is invalid",
        ));
    }
    let mut data = Vec::with_capacity(metadata.len() as usize);
    File::open(path)?.take(maximum + 1).read_to_end(&mut data)?;
    if data.len() as u64 > maximum {
        return Err(VideoMediaToolchainError::Invalid(
            "manifest exceeds size limit",
        ));
    }
    Ok(data)
}

fn validate_relative_path(value: &str) -> Result<(), VideoMediaToolchainError> {
    if value.is_empty() || value.contains('\\') {
        return Err(VideoMediaToolchainError::Invalid(
            "manifest path is invalid",
        ));
    }
    let path = Path::new(value);
    if path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(VideoMediaToolchainError::Invalid(
            "manifest path escapes package",
        ));
    }
    Ok(())
}

fn collect_file_set(root: &Path) -> Result<BTreeSet<String>, VideoMediaToolchainError> {
    let mut result = BTreeSet::new();
    for entry in walkdir::WalkDir::new(root).follow_links(false) {
        let entry =
            entry.map_err(|error| VideoMediaToolchainError::InvalidDetail(error.to_string()))?;
        let path = entry.path();
        if path == root {
            continue;
        }
        reject_link(path)?;
        if entry.file_type().is_file() {
            let relative = path
                .strip_prefix(root)
                .map_err(|_| VideoMediaToolchainError::Invalid("package path escaped"))?;
            result.insert(relative.to_string_lossy().replace('\\', "/"));
        } else if !entry.file_type().is_dir() {
            return Err(VideoMediaToolchainError::Invalid(
                "special package file is forbidden",
            ));
        }
    }
    Ok(result)
}

fn hash_file(path: &Path) -> Result<String, VideoMediaToolchainError> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(to_lower_hex(&digest.finalize()))
}

fn to_lower_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn ensure_direct_child(parent: &Path, child: &Path) -> Result<(), VideoMediaToolchainError> {
    ensure_contained(parent, child)?;
    if child.parent() != Some(parent) {
        return Err(VideoMediaToolchainError::Invalid(
            "package is not a direct resource child",
        ));
    }
    Ok(())
}

fn ensure_contained(root: &Path, path: &Path) -> Result<(), VideoMediaToolchainError> {
    if path == root || !path.starts_with(root) {
        return Err(VideoMediaToolchainError::Invalid(
            "package path is outside resource root",
        ));
    }
    Ok(())
}

fn reject_link(path: &Path) -> Result<(), VideoMediaToolchainError> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || has_windows_reparse_point(&metadata) {
        return Err(VideoMediaToolchainError::Invalid(
            "links and reparse points are forbidden",
        ));
    }
    Ok(())
}

#[cfg(windows)]
fn has_windows_reparse_point(metadata: &fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;
    metadata.file_attributes() & 0x400 != 0
}

#[cfg(not(windows))]
fn has_windows_reparse_point(_metadata: &fs::Metadata) -> bool {
    false
}

fn assert_executable(_path: &Path, target_id: &str) -> Result<(), VideoMediaToolchainError> {
    if target_id == "macos-arm64" {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if fs::metadata(_path)?.permissions().mode() & 0o111 == 0 {
                return Err(VideoMediaToolchainError::Invalid(
                    "media binary is not executable",
                ));
            }
        }
    }
    Ok(())
}

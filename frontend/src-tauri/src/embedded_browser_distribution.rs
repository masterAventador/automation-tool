//! Fail-closed resolver for the packaged embedded-browser distribution (EB-06).
//!
//! The production runtime only ever accepts the one embedded Chromium that
//! ships inside the Tauri resource directory and matches the EB-05
//! distribution manifest: exact locked runtime versions, per-file SHA-256,
//! declared-only symlinks confined to the distribution root, no extra or
//! missing files, no second browser binary and no reparse points. The
//! resolved absolute executable path never enters the WebView: the type has
//! no serde implementation and its Debug output is redacted.

use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fmt;
use std::fs::{self, File};
use std::io::{self, Read, Seek, SeekFrom};
use std::path::{Component, Path, PathBuf};

const DISTRIBUTION_DIRECTORY: &str = "embedded-browser";
const MANIFEST_FILE: &str = "distribution-manifest.v1.json";
const STAGING_MANIFEST_FILE: &str = "staging-manifest.json";
const MAX_MANIFEST_BYTES: u64 = 4 * 1024 * 1024;
const MAX_ENTRY_COUNT: usize = 20_000;
const MAX_FILE_BYTES: u64 = 4 * 1024 * 1024 * 1024;
const MAX_PE_HEADER_OFFSET: u64 = 16 * 1024 * 1024;

const EXPECTED_PLAYWRIGHT: &str = "1.61.0";
const EXPECTED_CHROMIUM_TITLE: &str = "Chrome for Testing";
const EXPECTED_CHROMIUM_VERSION: &str = "149.0.7827.55";
const EXPECTED_CHROMIUM_REVISION: &str = "1228";
const EXPECTED_BROWSER_USE: &str = "0.13.6";
const EXPECTED_RENDER_ENGINE: &str = "0.7.68";

const FORBIDDEN_NAME_SUBSTRINGS: [&str; 4] = [
    "chrome-headless-shell",
    "headless_shell",
    "firefox",
    "webkit",
];

#[derive(Debug)]
pub enum EmbeddedBrowserError {
    Io(io::Error),
    Invalid(&'static str),
    VersionIncompatible,
}

impl fmt::Display for EmbeddedBrowserError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "embedded browser I/O failure: {error}"),
            Self::Invalid(message) => {
                write!(
                    formatter,
                    "invalid embedded browser distribution: {message}"
                )
            }
            Self::VersionIncompatible => {
                formatter.write_str("embedded browser distribution version is incompatible")
            }
        }
    }
}

impl std::error::Error for EmbeddedBrowserError {}

impl From<io::Error> for EmbeddedBrowserError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DistributionManifest {
    #[serde(rename = "schemaVersion")]
    schema_version: u32,
    policy: String,
    verified_at: String,
    target: String,
    runtime: RuntimeVersions,
    executable: String,
    source: serde_json::Value,
    #[serde(rename = "fileCount")]
    file_count: u64,
    #[serde(rename = "totalBytes")]
    total_bytes: u64,
    entries: Vec<ManifestEntry>,
    licenses: serde_json::Value,
    sbom: serde_json::Value,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RuntimeVersions {
    playwright_python: String,
    chromium: ChromiumVersion,
    browser_use: String,
    render_engine: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ChromiumVersion {
    title: String,
    browser_version: String,
    revision: String,
}

#[derive(Deserialize)]
#[serde(tag = "type")]
enum ManifestEntry {
    #[serde(rename = "file")]
    File {
        path: String,
        size: u64,
        sha256: String,
        executable: bool,
    },
    #[serde(rename = "symlink")]
    Symlink {
        path: String,
        #[serde(rename = "targetPath")]
        target_path: String,
    },
}

/// Verified embedded-browser paths. Deliberately not serializable; Debug is
/// redacted so absolute installation paths never reach the WebView or logs.
pub struct EmbeddedBrowserDistribution {
    root: PathBuf,
    executable: PathBuf,
    target_id: String,
    browser_version: String,
}

impl fmt::Debug for EmbeddedBrowserDistribution {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EmbeddedBrowserDistribution")
            .field("target_id", &self.target_id)
            .field("browser_version", &self.browser_version)
            .finish_non_exhaustive()
    }
}

impl EmbeddedBrowserDistribution {
    pub fn load(resource_directory: &Path) -> Result<Self, EmbeddedBrowserError> {
        Self::load_for_target(resource_directory, release_target_id())
    }

    pub fn load_for_target(
        resource_directory: &Path,
        target_id: &str,
    ) -> Result<Self, EmbeddedBrowserError> {
        if !matches!(target_id, "macos-arm64" | "macos-x86_64" | "windows-x86_64") {
            return Err(EmbeddedBrowserError::Invalid("unsupported release target"));
        }
        reject_link(resource_directory)?;
        let resource_root = fs::canonicalize(resource_directory)?;
        let root = resource_root.join(DISTRIBUTION_DIRECTORY);
        reject_link(&root)?;
        let root = fs::canonicalize(&root)?;
        if root.parent() != Some(resource_root.as_path()) {
            return Err(EmbeddedBrowserError::Invalid(
                "distribution is not a direct resource child",
            ));
        }

        let manifest_bytes = read_bounded(&root.join(MANIFEST_FILE), MAX_MANIFEST_BYTES)?;
        let manifest: DistributionManifest = serde_json::from_slice(&manifest_bytes)
            .map_err(|_| EmbeddedBrowserError::Invalid("manifest schema is invalid"))?;
        let _ = (&manifest.source, &manifest.licenses, &manifest.sbom);
        if manifest.schema_version != 1
            || manifest.policy != "fail_closed"
            || manifest.verified_at.is_empty()
            || manifest.target != target_id
        {
            return Err(EmbeddedBrowserError::Invalid("manifest identity mismatch"));
        }
        if manifest.executable != expected_executable(target_id)? {
            return Err(EmbeddedBrowserError::Invalid(
                "manifest executable does not match release target",
            ));
        }
        let runtime = &manifest.runtime;
        if runtime.playwright_python != EXPECTED_PLAYWRIGHT
            || runtime.chromium.title != EXPECTED_CHROMIUM_TITLE
            || runtime.chromium.browser_version != EXPECTED_CHROMIUM_VERSION
            || runtime.chromium.revision != EXPECTED_CHROMIUM_REVISION
            || runtime.browser_use != EXPECTED_BROWSER_USE
            || runtime.render_engine != EXPECTED_RENDER_ENGINE
        {
            return Err(EmbeddedBrowserError::VersionIncompatible);
        }
        if manifest.entries.is_empty() || manifest.entries.len() > MAX_ENTRY_COUNT {
            return Err(EmbeddedBrowserError::Invalid(
                "manifest entry count invalid",
            ));
        }

        validate_relative_path(&manifest.executable)?;
        let root_entry = first_component(&manifest.executable)?;

        let mut files: BTreeMap<String, (u64, String, bool)> = BTreeMap::new();
        let mut links: BTreeMap<String, String> = BTreeMap::new();
        let mut file_count: u64 = 0;
        let mut total_bytes: u64 = 0;
        for entry in &manifest.entries {
            match entry {
                ManifestEntry::File {
                    path,
                    size,
                    sha256,
                    executable,
                } => {
                    validate_relative_path(path)?;
                    ensure_same_root(path, root_entry)?;
                    ensure_allowed_name(path)?;
                    if *size > MAX_FILE_BYTES || !is_sha256(sha256) {
                        return Err(EmbeddedBrowserError::Invalid(
                            "manifest file metadata invalid",
                        ));
                    }
                    if files
                        .insert(path.clone(), (*size, sha256.clone(), *executable))
                        .is_some()
                    {
                        return Err(EmbeddedBrowserError::Invalid("manifest path duplicated"));
                    }
                    file_count += 1;
                    total_bytes += *size;
                }
                ManifestEntry::Symlink { path, target_path } => {
                    validate_relative_path(path)?;
                    ensure_same_root(path, root_entry)?;
                    validate_symlink_target(path, target_path, root_entry)?;
                    if links.insert(path.clone(), target_path.clone()).is_some() {
                        return Err(EmbeddedBrowserError::Invalid("manifest path duplicated"));
                    }
                }
            }
        }
        if manifest.file_count != file_count || manifest.total_bytes != total_bytes {
            return Err(EmbeddedBrowserError::Invalid("manifest totals mismatch"));
        }
        if !files.contains_key(&manifest.executable) {
            return Err(EmbeddedBrowserError::Invalid(
                "executable missing from manifest entries",
            ));
        }

        walk_and_verify(&root, root_entry, &files, &links)?;

        let executable = root.join(from_relative(&manifest.executable));
        assert_executable(&executable, target_id)?;
        Ok(Self {
            root,
            executable,
            target_id: target_id.to_owned(),
            browser_version: runtime.chromium.browser_version.clone(),
        })
    }

    pub fn executable_path(&self) -> &Path {
        &self.executable
    }

    pub fn distribution_root(&self) -> &Path {
        &self.root
    }

    pub fn browser_version(&self) -> &str {
        &self.browser_version
    }

    pub fn target_id(&self) -> &str {
        &self.target_id
    }
}

fn release_target_id() -> &'static str {
    if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        "macos-arm64"
    } else if cfg!(all(target_os = "macos", target_arch = "x86_64")) {
        "macos-x86_64"
    } else if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        "windows-x86_64"
    } else {
        "unsupported"
    }
}

fn expected_executable(target_id: &str) -> Result<&'static str, EmbeddedBrowserError> {
    match target_id {
        "macos-arm64" => Ok(
            "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        ),
        "macos-x86_64" => Ok(
            "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        ),
        "windows-x86_64" => Ok("chrome-win64/chrome.exe"),
        _ => Err(EmbeddedBrowserError::Invalid("unsupported release target")),
    }
}

fn walk_and_verify(
    root: &Path,
    root_entry: &str,
    files: &BTreeMap<String, (u64, String, bool)>,
    links: &BTreeMap<String, String>,
) -> Result<(), EmbeddedBrowserError> {
    let browser_root = root.join(root_entry);
    reject_link(&browser_root)?;
    if !browser_root.is_dir() {
        return Err(EmbeddedBrowserError::Invalid("browser root missing"));
    }
    for entry in root.read_dir()? {
        let name = entry?.file_name();
        let name = name.to_string_lossy();
        if name != root_entry && name != MANIFEST_FILE && name != STAGING_MANIFEST_FILE {
            return Err(EmbeddedBrowserError::Invalid(
                "unexpected top-level distribution entry",
            ));
        }
    }

    let mut seen_files: u64 = 0;
    let mut seen_links: u64 = 0;
    for entry in walkdir::WalkDir::new(&browser_root).follow_links(false) {
        let entry = entry.map_err(|_| EmbeddedBrowserError::Invalid("walk failed"))?;
        let path = entry.path();
        if path == browser_root {
            continue;
        }
        let relative = path
            .strip_prefix(root)
            .map_err(|_| EmbeddedBrowserError::Invalid("path escaped root"))?;
        let relative = relative
            .to_str()
            .ok_or(EmbeddedBrowserError::Invalid("non-utf8 path"))?
            .replace('\\', "/");
        ensure_allowed_name(&relative)?;

        let file_type = entry.file_type();
        if file_type.is_symlink() {
            let expected = links
                .get(&relative)
                .ok_or(EmbeddedBrowserError::Invalid("undeclared symlink"))?;
            let actual = fs::read_link(path)?;
            if actual.as_os_str().to_string_lossy() != expected.as_str() {
                return Err(EmbeddedBrowserError::Invalid("symlink target drifted"));
            }
            seen_links += 1;
        } else if file_type.is_file() {
            let metadata = fs::symlink_metadata(path)?;
            if has_windows_reparse_point(&metadata) {
                return Err(EmbeddedBrowserError::Invalid("reparse point forbidden"));
            }
            let (size, sha256, _) = files
                .get(&relative)
                .ok_or(EmbeddedBrowserError::Invalid("unexpected extra file"))?;
            if metadata.len() != *size {
                return Err(EmbeddedBrowserError::Invalid("file size mismatch"));
            }
            if hash_file(path)? != *sha256 {
                return Err(EmbeddedBrowserError::Invalid("file digest mismatch"));
            }
            seen_files += 1;
        } else if !file_type.is_dir() {
            return Err(EmbeddedBrowserError::Invalid("special file forbidden"));
        }
    }
    if seen_files != files.len() as u64 || seen_links != links.len() as u64 {
        return Err(EmbeddedBrowserError::Invalid(
            "distribution has missing files or links",
        ));
    }
    Ok(())
}

fn read_bounded(path: &Path, maximum: u64) -> Result<Vec<u8>, EmbeddedBrowserError> {
    reject_link(path)?;
    let metadata = fs::metadata(path)?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > maximum {
        return Err(EmbeddedBrowserError::Invalid("manifest size invalid"));
    }
    let mut data = Vec::with_capacity(metadata.len() as usize);
    File::open(path)?.take(maximum + 1).read_to_end(&mut data)?;
    Ok(data)
}

fn validate_relative_path(value: &str) -> Result<(), EmbeddedBrowserError> {
    if value.is_empty() || value.contains('\\') {
        return Err(EmbeddedBrowserError::Invalid("manifest path invalid"));
    }
    let path = Path::new(value);
    if path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(EmbeddedBrowserError::Invalid("manifest path escapes root"));
    }
    Ok(())
}

fn first_component(value: &str) -> Result<&str, EmbeddedBrowserError> {
    value
        .split('/')
        .next()
        .filter(|part| !part.is_empty())
        .ok_or(EmbeddedBrowserError::Invalid("manifest path invalid"))
}

fn ensure_same_root(path: &str, root_entry: &str) -> Result<(), EmbeddedBrowserError> {
    if first_component(path)? != root_entry {
        return Err(EmbeddedBrowserError::Invalid(
            "entry outside the browser root",
        ));
    }
    Ok(())
}

fn ensure_allowed_name(relative: &str) -> Result<(), EmbeddedBrowserError> {
    let lowered = relative.to_ascii_lowercase();
    for forbidden in FORBIDDEN_NAME_SUBSTRINGS {
        if lowered.contains(forbidden) {
            return Err(EmbeddedBrowserError::Invalid(
                "forbidden browser entry present",
            ));
        }
    }
    Ok(())
}

fn validate_symlink_target(
    link_path: &str,
    target: &str,
    root_entry: &str,
) -> Result<(), EmbeddedBrowserError> {
    if target.is_empty() || target.contains('\\') || Path::new(target).is_absolute() {
        return Err(EmbeddedBrowserError::Invalid("symlink target invalid"));
    }
    let mut resolved: Vec<&str> = link_path.split('/').collect();
    resolved.pop();
    for part in target.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                if resolved.pop().is_none() {
                    return Err(EmbeddedBrowserError::Invalid("symlink escapes root"));
                }
            }
            other => resolved.push(other),
        }
    }
    if resolved.first() != Some(&root_entry) {
        return Err(EmbeddedBrowserError::Invalid("symlink escapes root"));
    }
    Ok(())
}

fn from_relative(value: &str) -> PathBuf {
    value.split('/').collect()
}

fn hash_file(path: &Path) -> Result<String, EmbeddedBrowserError> {
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
    let bytes = digest.finalize();
    let mut output = String::with_capacity(64);
    for byte in bytes {
        use std::fmt::Write;
        let _ = write!(output, "{byte:02x}");
    }
    Ok(output)
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn reject_link(path: &Path) -> Result<(), EmbeddedBrowserError> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || has_windows_reparse_point(&metadata) {
        return Err(EmbeddedBrowserError::Invalid(
            "links and reparse points are forbidden here",
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

fn assert_executable(path: &Path, target_id: &str) -> Result<(), EmbeddedBrowserError> {
    reject_link(path)?;
    if !fs::metadata(path)?.is_file() {
        return Err(EmbeddedBrowserError::Invalid("executable missing"));
    }
    if target_id.starts_with("macos-") {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if fs::metadata(path)?.permissions().mode() & 0o111 == 0 {
                return Err(EmbeddedBrowserError::Invalid("executable bit missing"));
            }
        }
        assert_macho_architecture(path, target_id)?;
    } else if target_id == "windows-x86_64" {
        assert_pe_x86_64(path)?;
    } else {
        return Err(EmbeddedBrowserError::Invalid("unsupported release target"));
    }
    Ok(())
}

fn assert_macho_architecture(path: &Path, target_id: &str) -> Result<(), EmbeddedBrowserError> {
    let mut header = [0_u8; 12];
    File::open(path)?.read_exact(&mut header)?;
    if header[..4] != [0xcf, 0xfa, 0xed, 0xfe] {
        return Err(EmbeddedBrowserError::Invalid(
            "browser executable is not a thin 64-bit Mach-O",
        ));
    }
    let actual = u32::from_le_bytes(
        header[4..8]
            .try_into()
            .map_err(|_| EmbeddedBrowserError::Invalid("Mach-O header invalid"))?,
    );
    let expected = match target_id {
        "macos-arm64" => 0x0100_000c,
        "macos-x86_64" => 0x0100_0007,
        _ => return Err(EmbeddedBrowserError::Invalid("unsupported macOS target")),
    };
    if actual != expected {
        return Err(EmbeddedBrowserError::Invalid(
            "browser Mach-O architecture mismatch",
        ));
    }
    Ok(())
}

fn assert_pe_x86_64(path: &Path) -> Result<(), EmbeddedBrowserError> {
    let mut file = File::open(path)?;
    let length = file.metadata()?.len();
    let mut dos_header = [0_u8; 64];
    file.read_exact(&mut dos_header)?;
    if dos_header[..2] != *b"MZ" {
        return Err(EmbeddedBrowserError::Invalid(
            "browser executable is not PE",
        ));
    }
    let pe_offset = u32::from_le_bytes(
        dos_header[0x3c..0x40]
            .try_into()
            .map_err(|_| EmbeddedBrowserError::Invalid("PE header invalid"))?,
    ) as u64;
    if pe_offset < 64 || pe_offset > MAX_PE_HEADER_OFFSET || pe_offset + 6 > length {
        return Err(EmbeddedBrowserError::Invalid("PE header offset invalid"));
    }
    file.seek(SeekFrom::Start(pe_offset))?;
    let mut coff_header = [0_u8; 6];
    file.read_exact(&mut coff_header)?;
    if coff_header[..4] != *b"PE\0\0"
        || u16::from_le_bytes([coff_header[4], coff_header[5]]) != 0x8664
    {
        return Err(EmbeddedBrowserError::Invalid(
            "browser PE architecture mismatch",
        ));
    }
    Ok(())
}

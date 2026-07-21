use std::error::Error;
use std::fmt::{Display, Formatter};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Take};
use std::path::{Component, Path, PathBuf};

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use ed25519_dalek::{Signature, VerifyingKey};
use semver::{Version, VersionReq};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use walkdir::WalkDir;

const MANIFEST_FILE_NAME: &str = "executor-manifest.v1.json";
const SIGNATURE_FILE_NAME: &str = "executor-manifest.v1.sig";
const SIGNATURE_PREFIX: &str = "atems1.";
const INVENTORY_DOMAIN: &[u8] = b"automation-tool.executor-package.v1\0";
const MAX_MANIFEST_BYTES: usize = 4 * 1024 * 1024;
const MAX_SIGNATURE_BYTES: usize = 96;
const MAX_PACKAGE_FILES: usize = 10_000;
const MAX_PACKAGE_BYTES: u64 = 8 * 1024 * 1024 * 1024;
const HASH_BUFFER_BYTES: usize = 1024 * 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExecutorPackageErrorCode {
    ConfigurationInvalid,
    SignatureInvalid,
    ManifestInvalid,
    PlatformMismatch,
    VersionRejected,
    RollbackRejected,
    PackageInvalid,
    DigestMismatch,
    IoUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ExecutorPackageError {
    code: ExecutorPackageErrorCode,
}

impl ExecutorPackageError {
    fn new(code: ExecutorPackageErrorCode) -> Self {
        Self { code }
    }

    pub fn code(&self) -> ExecutorPackageErrorCode {
        self.code
    }
}

impl Display for ExecutorPackageError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("executor package is rejected")
    }
}

impl Error for ExecutorPackageError {}

#[derive(Debug)]
pub struct ExecutorPackageVerifier {
    verifying_key: VerifyingKey,
    allowed_versions: VersionReq,
    installed_version: Option<Version>,
}

#[derive(Debug)]
pub struct VerifiedExecutorPackage {
    version: Version,
    build_id: String,
    entrypoint_path: PathBuf,
    file_count: usize,
    package_size: u64,
}

impl VerifiedExecutorPackage {
    pub fn version(&self) -> &Version {
        &self.version
    }

    pub fn build_id(&self) -> &str {
        &self.build_id
    }

    pub fn entrypoint_path(&self) -> &Path {
        &self.entrypoint_path
    }

    pub fn file_count(&self) -> usize {
        self.file_count
    }

    pub fn package_size(&self) -> u64 {
        self.package_size
    }
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ExecutorPackageFile {
    path: String,
    sha256: String,
    size: u64,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ExecutorPackageManifest {
    architecture: String,
    build_id: String,
    entrypoint: String,
    executor_version: String,
    files: Vec<ExecutorPackageFile>,
    manifest_version: String,
    package_sha256: String,
    package_size: u64,
    platform: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct FileIdentity {
    device: u64,
    file: u64,
    length: u64,
}

impl ExecutorPackageVerifier {
    pub fn new(
        verifying_key: [u8; 32],
        allowed_version_requirement: &str,
        installed_version: Option<&str>,
    ) -> Result<Self, ExecutorPackageError> {
        if allowed_version_requirement == "*" {
            return Err(ExecutorPackageError::new(
                ExecutorPackageErrorCode::ConfigurationInvalid,
            ));
        }
        let verifying_key = VerifyingKey::from_bytes(&verifying_key).map_err(|_| {
            ExecutorPackageError::new(ExecutorPackageErrorCode::ConfigurationInvalid)
        })?;
        if verifying_key.is_weak() {
            return Err(ExecutorPackageError::new(
                ExecutorPackageErrorCode::ConfigurationInvalid,
            ));
        }
        let allowed_versions = VersionReq::parse(allowed_version_requirement).map_err(|_| {
            ExecutorPackageError::new(ExecutorPackageErrorCode::ConfigurationInvalid)
        })?;
        let installed_version =
            installed_version
                .map(Version::parse)
                .transpose()
                .map_err(|_| {
                    ExecutorPackageError::new(ExecutorPackageErrorCode::ConfigurationInvalid)
                })?;
        Ok(Self {
            verifying_key,
            allowed_versions,
            installed_version,
        })
    }

    pub fn verify_current(
        &self,
        package_root: &Path,
    ) -> Result<VerifiedExecutorPackage, ExecutorPackageError> {
        let platform = match std::env::consts::OS {
            "macos" => "macos",
            "windows" => "windows",
            _ => {
                return Err(ExecutorPackageError::new(
                    ExecutorPackageErrorCode::PlatformMismatch,
                ))
            }
        };
        let architecture = match std::env::consts::ARCH {
            "aarch64" => "aarch64",
            "x86_64" => "x86_64",
            _ => {
                return Err(ExecutorPackageError::new(
                    ExecutorPackageErrorCode::PlatformMismatch,
                ))
            }
        };
        self.verify_for_target(package_root, platform, architecture)
    }

    fn verify_for_target(
        &self,
        package_root: &Path,
        platform: &str,
        architecture: &str,
    ) -> Result<VerifiedExecutorPackage, ExecutorPackageError> {
        ensure_safe_package_root(package_root)?;
        let manifest_bytes =
            read_bounded_regular_file(&package_root.join(MANIFEST_FILE_NAME), MAX_MANIFEST_BYTES)?;
        let signature_bytes = read_bounded_regular_file(
            &package_root.join(SIGNATURE_FILE_NAME),
            MAX_SIGNATURE_BYTES,
        )?;
        self.verify_signature(&manifest_bytes, &signature_bytes)?;
        let manifest = parse_canonical_manifest(&manifest_bytes)?;
        let version = validate_manifest_identity(&manifest, platform, architecture)?;
        if !self.allowed_versions.matches(&version) {
            return Err(ExecutorPackageError::new(
                ExecutorPackageErrorCode::VersionRejected,
            ));
        }
        if self
            .installed_version
            .as_ref()
            .is_some_and(|installed| &version < installed)
        {
            return Err(ExecutorPackageError::new(
                ExecutorPackageErrorCode::RollbackRejected,
            ));
        }
        verify_complete_inventory(package_root, &manifest)?;
        Ok(VerifiedExecutorPackage {
            version,
            build_id: manifest.build_id,
            entrypoint_path: package_root.join(manifest.entrypoint),
            file_count: manifest.files.len(),
            package_size: manifest.package_size,
        })
    }

    fn verify_signature(
        &self,
        manifest_bytes: &[u8],
        signature_envelope: &[u8],
    ) -> Result<(), ExecutorPackageError> {
        let envelope = std::str::from_utf8(signature_envelope)
            .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::SignatureInvalid))?;
        let encoded = envelope
            .strip_suffix('\n')
            .and_then(|value| value.strip_prefix(SIGNATURE_PREFIX))
            .filter(|value| !value.is_empty() && !value.contains(['\r', '\n', '=']))
            .ok_or_else(|| ExecutorPackageError::new(ExecutorPackageErrorCode::SignatureInvalid))?;
        let signature = URL_SAFE_NO_PAD
            .decode(encoded)
            .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::SignatureInvalid))?;
        let signature = Signature::from_slice(&signature)
            .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::SignatureInvalid))?;
        self.verifying_key
            .verify_strict(manifest_bytes, &signature)
            .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::SignatureInvalid))
    }
}

fn parse_canonical_manifest(
    manifest_bytes: &[u8],
) -> Result<ExecutorPackageManifest, ExecutorPackageError> {
    let manifest: ExecutorPackageManifest = serde_json::from_slice(manifest_bytes)
        .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::ManifestInvalid))?;
    let mut canonical = serde_json::to_vec(&manifest)
        .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::ManifestInvalid))?;
    canonical.push(b'\n');
    if canonical != manifest_bytes {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::ManifestInvalid,
        ));
    }
    Ok(manifest)
}

fn validate_manifest_identity(
    manifest: &ExecutorPackageManifest,
    platform: &str,
    architecture: &str,
) -> Result<Version, ExecutorPackageError> {
    if manifest.manifest_version != "1"
        || !valid_build_id(&manifest.build_id)
        || !matches!(manifest.platform.as_str(), "macos" | "windows")
        || !matches!(manifest.architecture.as_str(), "aarch64" | "x86_64")
    {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::ManifestInvalid,
        ));
    }
    if manifest.platform != platform || manifest.architecture != architecture {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::PlatformMismatch,
        ));
    }
    let expected_entrypoint = if platform == "windows" {
        "automation-tool-executor.exe"
    } else {
        "automation-tool-executor"
    };
    if manifest.entrypoint != expected_entrypoint {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::ManifestInvalid,
        ));
    }
    let version = Version::parse(&manifest.executor_version)
        .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::ManifestInvalid))?;
    if version.to_string() != manifest.executor_version {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::ManifestInvalid,
        ));
    }
    Ok(version)
}

fn valid_build_id(value: &str) -> bool {
    (1..=64).contains(&value.len())
        && value.bytes().enumerate().all(|(index, byte)| match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' => true,
            b'.' | b'_' | b'-' => index > 0,
            _ => false,
        })
}

fn verify_complete_inventory(
    package_root: &Path,
    manifest: &ExecutorPackageManifest,
) -> Result<(), ExecutorPackageError> {
    if manifest.files.is_empty()
        || manifest.files.len() > MAX_PACKAGE_FILES
        || manifest.package_size == 0
        || manifest.package_size > MAX_PACKAGE_BYTES
        || decode_sha256(&manifest.package_sha256).is_none()
    {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::ManifestInvalid,
        ));
    }
    let actual_paths = collect_payload_paths(package_root)?;
    let declared_paths = manifest
        .files
        .iter()
        .map(|file| file.path.as_str())
        .collect::<Vec<_>>();
    if declared_paths.windows(2).any(|pair| pair[0] >= pair[1])
        || declared_paths.iter().any(|path| !valid_relative_path(path))
        || declared_paths
            .iter()
            .any(|path| matches!(*path, MANIFEST_FILE_NAME | SIGNATURE_FILE_NAME))
        || actual_paths
            != declared_paths
                .iter()
                .map(|path| (*path).to_owned())
                .collect::<Vec<_>>()
    {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::PackageInvalid,
        ));
    }

    let mut total_size = 0_u64;
    let mut inventory = Sha256::new();
    inventory.update(INVENTORY_DOMAIN);
    for declared in &manifest.files {
        let expected_digest = decode_sha256(&declared.sha256)
            .ok_or_else(|| ExecutorPackageError::new(ExecutorPackageErrorCode::ManifestInvalid))?;
        let (actual_size, actual_digest) = hash_stable_regular_file(
            &package_root.join(relative_path(&declared.path)?),
            MAX_PACKAGE_BYTES,
        )?;
        if actual_size != declared.size || actual_digest != expected_digest {
            return Err(ExecutorPackageError::new(
                ExecutorPackageErrorCode::DigestMismatch,
            ));
        }
        total_size = total_size
            .checked_add(actual_size)
            .ok_or_else(|| ExecutorPackageError::new(ExecutorPackageErrorCode::ManifestInvalid))?;
        if total_size > MAX_PACKAGE_BYTES {
            return Err(ExecutorPackageError::new(
                ExecutorPackageErrorCode::ManifestInvalid,
            ));
        }
        let path = declared.path.as_bytes();
        let path_length = u32::try_from(path.len())
            .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::ManifestInvalid))?;
        inventory.update(path_length.to_be_bytes());
        inventory.update(path);
        inventory.update(actual_size.to_be_bytes());
        inventory.update(actual_digest);
    }
    let package_digest: [u8; 32] = inventory.finalize().into();
    if total_size != manifest.package_size
        || Some(package_digest) != decode_sha256(&manifest.package_sha256)
        || manifest
            .files
            .iter()
            .find(|file| file.path == manifest.entrypoint)
            .is_none_or(|entrypoint| entrypoint.size == 0)
    {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::DigestMismatch,
        ));
    }
    if collect_payload_paths(package_root)? != actual_paths {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::PackageInvalid,
        ));
    }
    Ok(())
}

fn collect_payload_paths(package_root: &Path) -> Result<Vec<String>, ExecutorPackageError> {
    let mut paths = Vec::new();
    for item in WalkDir::new(package_root).follow_links(false).into_iter() {
        let item =
            item.map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::IoUnavailable))?;
        if item.depth() == 0 {
            continue;
        }
        let relative = item
            .path()
            .strip_prefix(package_root)
            .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::PackageInvalid))?;
        let rendered = path_to_manifest(relative)?;
        let metadata = fs::symlink_metadata(item.path())
            .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::IoUnavailable))?;
        if metadata.file_type().is_symlink() {
            return Err(ExecutorPackageError::new(
                ExecutorPackageErrorCode::PackageInvalid,
            ));
        }
        if metadata.is_dir() {
            continue;
        }
        if !metadata.is_file() {
            return Err(ExecutorPackageError::new(
                ExecutorPackageErrorCode::PackageInvalid,
            ));
        }
        if item.depth() == 1
            && matches!(rendered.as_str(), MANIFEST_FILE_NAME | SIGNATURE_FILE_NAME)
        {
            continue;
        }
        paths.push(rendered);
        if paths.len() > MAX_PACKAGE_FILES {
            return Err(ExecutorPackageError::new(
                ExecutorPackageErrorCode::PackageInvalid,
            ));
        }
    }
    paths.sort_unstable();
    Ok(paths)
}

fn valid_relative_path(value: &str) -> bool {
    if value.is_empty() || value.len() > 4096 || !value.is_ascii() {
        return false;
    }
    value.split('/').all(|component| {
        !component.is_empty()
            && component != "."
            && component != ".."
            && component.len() <= 255
            && component.bytes().all(|byte| {
                byte >= 32
                    && !matches!(byte, b'\\' | b'"' | b'*' | b':' | b'<' | b'>' | b'?' | b'|')
            })
    })
}

fn relative_path(value: &str) -> Result<PathBuf, ExecutorPackageError> {
    if !valid_relative_path(value) {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::PackageInvalid,
        ));
    }
    Ok(value.split('/').collect())
}

fn path_to_manifest(path: &Path) -> Result<String, ExecutorPackageError> {
    let mut parts = Vec::new();
    for component in path.components() {
        match component {
            Component::Normal(value) => parts.push(value.to_str().ok_or_else(|| {
                ExecutorPackageError::new(ExecutorPackageErrorCode::PackageInvalid)
            })?),
            _ => {
                return Err(ExecutorPackageError::new(
                    ExecutorPackageErrorCode::PackageInvalid,
                ))
            }
        }
    }
    let rendered = parts.join("/");
    if valid_relative_path(&rendered) {
        Ok(rendered)
    } else {
        Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::PackageInvalid,
        ))
    }
}

fn decode_sha256(value: &str) -> Option<[u8; 32]> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
    {
        return None;
    }
    let mut decoded = [0_u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        let pair = std::str::from_utf8(pair).ok()?;
        decoded[index] = u8::from_str_radix(pair, 16).ok()?;
    }
    Some(decoded)
}

fn ensure_safe_package_root(package_root: &Path) -> Result<(), ExecutorPackageError> {
    ensure_no_symlink_ancestors(package_root)?;
    let metadata = fs::symlink_metadata(package_root)
        .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::IoUnavailable))?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::PackageInvalid,
        ));
    }
    Ok(())
}

pub(crate) fn ensure_no_symlink_ancestors(path: &Path) -> Result<(), ExecutorPackageError> {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::IoUnavailable))?
            .join(path)
    };
    #[cfg(target_os = "macos")]
    let absolute = {
        let mut normalized = absolute;
        for alias in ["var", "tmp", "etc"] {
            let prefix = Path::new("/").join(alias);
            if let Ok(suffix) = normalized.strip_prefix(&prefix) {
                normalized = Path::new("/private").join(alias).join(suffix);
                break;
            }
        }
        normalized
    };
    let mut current = PathBuf::new();
    for component in absolute.components() {
        current.push(component.as_os_str());
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                return Err(ExecutorPackageError::new(
                    ExecutorPackageErrorCode::PackageInvalid,
                ))
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => break,
            Err(_) => {
                return Err(ExecutorPackageError::new(
                    ExecutorPackageErrorCode::IoUnavailable,
                ))
            }
        }
    }
    Ok(())
}

pub(crate) fn read_bounded_regular_file(
    path: &Path,
    maximum: usize,
) -> Result<Vec<u8>, ExecutorPackageError> {
    let (file, identity) = open_stable_regular_file(path)?;
    let mut contents = Vec::with_capacity(
        usize::try_from(identity.length)
            .unwrap_or(maximum)
            .min(maximum),
    );
    let mut bounded: Take<File> = file.take(maximum as u64 + 1);
    bounded
        .read_to_end(&mut contents)
        .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::IoUnavailable))?;
    if contents.len() > maximum {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::PackageInvalid,
        ));
    }
    verify_stable_identity(path, bounded.get_ref(), identity)?;
    Ok(contents)
}

fn hash_stable_regular_file(
    path: &Path,
    maximum: u64,
) -> Result<(u64, [u8; 32]), ExecutorPackageError> {
    let (mut file, identity) = open_stable_regular_file(path)?;
    if identity.length > maximum {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::PackageInvalid,
        ));
    }
    let mut digest = Sha256::new();
    let mut buffer = vec![0_u8; HASH_BUFFER_BYTES];
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::IoUnavailable))?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    verify_stable_identity(path, &file, identity)?;
    Ok((identity.length, digest.finalize().into()))
}

fn open_stable_regular_file(path: &Path) -> Result<(File, FileIdentity), ExecutorPackageError> {
    ensure_no_symlink_ancestors(path)?;
    let metadata = fs::symlink_metadata(path)
        .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::IoUnavailable))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::PackageInvalid,
        ));
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::{
            FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_READ,
        };
        options
            .share_mode(FILE_SHARE_READ)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }
    let file = options
        .open(path)
        .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::IoUnavailable))?;
    let identity = file_identity(&file)?;
    if metadata.len() != identity.length {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::IoUnavailable,
        ));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.dev() != identity.device || metadata.ino() != identity.file {
            return Err(ExecutorPackageError::new(
                ExecutorPackageErrorCode::IoUnavailable,
            ));
        }
    }
    Ok((file, identity))
}

fn verify_stable_identity(
    path: &Path,
    file: &File,
    identity: FileIdentity,
) -> Result<(), ExecutorPackageError> {
    if file_identity(file)? != identity {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::IoUnavailable,
        ));
    }
    let (current, current_identity) = open_stable_regular_file(path)?;
    drop(current);
    if current_identity != identity {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::IoUnavailable,
        ));
    }
    Ok(())
}

#[cfg(unix)]
fn file_identity(file: &File) -> Result<FileIdentity, ExecutorPackageError> {
    use std::os::unix::fs::MetadataExt;

    let metadata = file
        .metadata()
        .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::IoUnavailable))?;
    Ok(FileIdentity {
        device: metadata.dev(),
        file: metadata.ino(),
        length: metadata.len(),
    })
}

#[cfg(windows)]
fn file_identity(file: &File) -> Result<FileIdentity, ExecutorPackageError> {
    use std::mem::zeroed;
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
    };

    let mut information: BY_HANDLE_FILE_INFORMATION = unsafe { zeroed() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle() as _, &mut information) } == 0 {
        return Err(ExecutorPackageError::new(
            ExecutorPackageErrorCode::IoUnavailable,
        ));
    }
    Ok(FileIdentity {
        device: u64::from(information.dwVolumeSerialNumber),
        file: (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow),
        length: (u64::from(information.nFileSizeHigh) << 32) | u64::from(information.nFileSizeLow),
    })
}

#[cfg(all(not(unix), not(windows)))]
fn file_identity(file: &File) -> Result<FileIdentity, ExecutorPackageError> {
    let metadata = file
        .metadata()
        .map_err(|_| ExecutorPackageError::new(ExecutorPackageErrorCode::IoUnavailable))?;
    Ok(FileIdentity {
        device: 0,
        file: 0,
        length: metadata.len(),
    })
}

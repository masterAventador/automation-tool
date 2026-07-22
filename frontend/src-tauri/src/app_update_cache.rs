use std::error::Error;
use std::fmt::{Debug, Display, Formatter};
use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read, Write};
use std::path::{Path, PathBuf};

use base64::engine::general_purpose::STANDARD;
use base64::Engine as _;
use minisign_verify::{PublicKey, Signature};
use reqwest::header::{CONTENT_LENGTH, CONTENT_RANGE, ETAG, IF_RANGE, RANGE};
use semver::Version;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::app_updates::{
    UpdateArchitecture, UpdatePolicy, UpdateRelease, UpdateTarget, MAX_UPDATE_ARTIFACT_BYTES,
};
use crate::secure_store::{AppDataSecretStore, SecretStore};

const CACHE_DIRECTORY: &str = "app-updates/cache-v1";
const PACKAGE_FILE: &str = "candidate.package";
const PARTIAL_FILE: &str = "candidate.partial";
const CACHE_MANIFEST: &str = "cache-manifest-v1";
const PARTIAL_MANIFEST: &str = "partial-manifest-v1";
const MAX_SIGNATURE_BYTES: usize = 4096;
const MAX_ETAG_BYTES: usize = 256;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum UpdateDownloadErrorCode {
    ManifestRejected,
    TransportUnavailable,
    SignatureRejected,
    StorageUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct UpdateDownloadError(UpdateDownloadErrorCode);

impl UpdateDownloadError {
    pub fn code(self) -> UpdateDownloadErrorCode {
        self.0
    }
}

impl Display for UpdateDownloadError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("update download unavailable")
    }
}

impl Error for UpdateDownloadError {}

#[derive(Clone, Debug)]
pub struct DownloadSource {
    url: reqwest::Url,
    signature: String,
}

impl DownloadSource {
    pub fn new(url: reqwest::Url, signature: String) -> Result<Self, UpdateDownloadError> {
        if signature.is_empty() || signature.len() > MAX_SIGNATURE_BYTES {
            return Err(manifest_error());
        }
        Ok(Self { url, signature })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct CachedUpdateRecord {
    version: String,
    channel: String,
    policy: UpdatePolicy,
    target: UpdateTarget,
    arch: UpdateArchitecture,
    sha256: String,
    size_bytes: u64,
}

impl CachedUpdateRecord {
    pub fn version(&self) -> &str {
        &self.version
    }

    pub fn size_bytes(&self) -> u64 {
        self.size_bytes
    }

    pub fn matches_release(&self, release: &UpdateRelease) -> bool {
        self.matches(release)
    }

    fn from_release(release: &UpdateRelease) -> Self {
        Self {
            version: release.version.as_str().to_owned(),
            channel: release.channel.as_str().to_owned(),
            policy: release.policy,
            target: release.artifact.target,
            arch: release.artifact.arch,
            sha256: release.artifact.sha256.clone(),
            size_bytes: release.artifact.size_bytes,
        }
    }

    fn matches(&self, release: &UpdateRelease) -> bool {
        self == &Self::from_release(release)
    }

    fn validate(&self) -> Result<(), UpdateDownloadError> {
        let version = Version::parse(&self.version).map_err(|_| manifest_error())?;
        if version.to_string() != self.version
            || !is_safe_channel(&self.channel)
            || !is_lower_hex_digest(&self.sha256)
            || !(1..=MAX_UPDATE_ARTIFACT_BYTES).contains(&self.size_bytes)
        {
            return Err(manifest_error());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct PartialUpdateRecord {
    #[serde(flatten)]
    candidate: CachedUpdateRecord,
    signature_sha256: String,
    etag: Option<String>,
}

impl PartialUpdateRecord {
    fn new(release: &UpdateRelease, source: &DownloadSource) -> Self {
        Self {
            candidate: CachedUpdateRecord::from_release(release),
            signature_sha256: digest_bytes(source.signature.as_bytes()),
            etag: None,
        }
    }

    fn matches(&self, release: &UpdateRelease, source: &DownloadSource) -> bool {
        self.candidate.matches(release)
            && self.signature_sha256 == digest_bytes(source.signature.as_bytes())
    }

    fn validate(&self) -> Result<(), UpdateDownloadError> {
        self.candidate.validate()?;
        if !is_lower_hex_digest(&self.signature_sha256)
            || self
                .etag
                .as_deref()
                .is_some_and(|etag| !is_strong_etag(etag))
        {
            return Err(manifest_error());
        }
        Ok(())
    }
}

pub struct AppUpdateCache {
    directory: PathBuf,
    package_path: PathBuf,
    partial_path: PathBuf,
    cache_manifest: AppDataSecretStore,
    partial_manifest: AppDataSecretStore,
    public_key: PublicKey,
}

impl Debug for AppUpdateCache {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("AppUpdateCache")
    }
}

impl AppUpdateCache {
    pub fn initialize(
        app_data_directory: &Path,
        encoded_public_key: &str,
    ) -> Result<Self, UpdateDownloadError> {
        let public_key = decode_public_key(encoded_public_key)?;
        let directory = app_data_directory.join(CACHE_DIRECTORY);
        let cache_manifest =
            AppDataSecretStore::new(&directory, CACHE_MANIFEST).map_err(|_| storage_error())?;
        let partial_manifest =
            AppDataSecretStore::new(&directory, PARTIAL_MANIFEST).map_err(|_| storage_error())?;
        let cache = Self {
            package_path: directory.join(PACKAGE_FILE),
            partial_path: directory.join(PARTIAL_FILE),
            directory,
            cache_manifest,
            partial_manifest,
            public_key,
        };
        cache.validate_disk_state()?;
        Ok(cache)
    }

    pub fn cached(&self) -> Result<Option<CachedUpdateRecord>, UpdateDownloadError> {
        let Some(record) = self.load_cache_manifest()? else {
            if safe_file_metadata(&self.package_path)?.is_some() {
                return Err(storage_error());
            }
            return Ok(None);
        };
        let metadata = safe_file_metadata(&self.package_path)?.ok_or_else(storage_error)?;
        ensure_private_file_permissions(&metadata)?;
        if metadata.len() != record.size_bytes || digest_file(&self.package_path)? != record.sha256
        {
            return Err(storage_error());
        }
        Ok(Some(record))
    }

    pub fn read_verified_package(
        &self,
        release: &UpdateRelease,
        source: &DownloadSource,
    ) -> Result<Vec<u8>, UpdateDownloadError> {
        let record = self.cached()?.ok_or_else(storage_error)?;
        if !record.matches(release) {
            return Err(manifest_error());
        }
        let metadata = safe_file_metadata(&self.package_path)?.ok_or_else(storage_error)?;
        ensure_private_file_permissions(&metadata)?;
        let expected_size = usize::try_from(record.size_bytes).map_err(|_| storage_error())?;
        let mut package = Vec::with_capacity(expected_size);
        File::open(&self.package_path)
            .and_then(|mut file| file.read_to_end(&mut package))
            .map_err(|_| storage_error())?;
        if package.len() != expected_size || digest_bytes(&package) != record.sha256 {
            return Err(storage_error());
        }
        let signature_text = STANDARD
            .decode(&source.signature)
            .map_err(|_| signature_error())?;
        if signature_text.is_empty() || signature_text.len() > MAX_SIGNATURE_BYTES {
            return Err(signature_error());
        }
        let signature_text = std::str::from_utf8(&signature_text).map_err(|_| signature_error())?;
        let signature = Signature::decode(signature_text).map_err(|_| signature_error())?;
        self.public_key
            .verify(&package, &signature, false)
            .map_err(|_| signature_error())?;
        Ok(package)
    }

    pub async fn download<F>(
        &self,
        client: &reqwest::Client,
        release: &UpdateRelease,
        source: &DownloadSource,
        mut progress: F,
    ) -> Result<CachedUpdateRecord, UpdateDownloadError>
    where
        F: FnMut(u64, u64),
    {
        let candidate = CachedUpdateRecord::from_release(release);
        candidate.validate()?;
        if self
            .cached()?
            .as_ref()
            .is_some_and(|cached| cached == &candidate)
        {
            progress(candidate.size_bytes, candidate.size_bytes);
            return Ok(candidate);
        }

        let mut partial = self.prepare_partial(release, source)?;
        let mut offset = partial_length(&self.partial_path)?;
        if offset > candidate.size_bytes {
            self.discard_partial()?;
            partial = PartialUpdateRecord::new(release, source);
            self.create_empty_partial()?;
            self.save_partial_manifest(&partial)?;
            offset = 0;
        }

        let mut request = client.get(source.url.clone());
        if offset > 0 {
            request = request.header(RANGE, format!("bytes={offset}-"));
            if let Some(etag) = partial.etag.as_deref() {
                request = request.header(IF_RANGE, etag);
            }
        }
        let mut response = request.send().await.map_err(|_| transport_error())?;
        let status = response.status();
        let mut write_offset = offset;
        if offset == 0 {
            if status != reqwest::StatusCode::OK {
                return Err(transport_error());
            }
        } else if status == reqwest::StatusCode::OK {
            write_offset = 0;
        } else if status == reqwest::StatusCode::PARTIAL_CONTENT {
            validate_partial_response(&response, offset, candidate.size_bytes)?;
        } else {
            return Err(transport_error());
        }
        validate_content_length(&response, candidate.size_bytes - write_offset)?;

        partial.etag = response
            .headers()
            .get(ETAG)
            .and_then(|value| value.to_str().ok())
            .filter(|etag| is_strong_etag(etag))
            .map(ToOwned::to_owned);
        self.save_partial_manifest(&partial)?;
        let mut file = self.open_partial_for_write(write_offset > 0)?;
        if write_offset == 0 {
            file.set_len(0).map_err(|_| storage_error())?;
        }

        let mut downloaded = write_offset;
        progress(downloaded, candidate.size_bytes);
        loop {
            let chunk = match response.chunk().await {
                Ok(Some(chunk)) => chunk,
                Ok(None) => break,
                Err(_) => {
                    file.sync_all().map_err(|_| storage_error())?;
                    return Err(transport_error());
                }
            };
            downloaded = downloaded
                .checked_add(chunk.len() as u64)
                .ok_or_else(transport_error)?;
            if downloaded > candidate.size_bytes {
                file.sync_all().map_err(|_| storage_error())?;
                return Err(manifest_error());
            }
            file.write_all(&chunk).map_err(|_| storage_error())?;
            progress(downloaded, candidate.size_bytes);
        }
        file.sync_all().map_err(|_| storage_error())?;
        drop(file);
        if downloaded != candidate.size_bytes {
            return Err(transport_error());
        }

        if digest_file(&self.partial_path)? != candidate.sha256 {
            self.discard_partial()?;
            return Err(manifest_error());
        }
        if self
            .verify_signature(&self.partial_path, &source.signature)
            .is_err()
        {
            self.discard_partial()?;
            return Err(signature_error());
        }

        atomic_replace(&self.partial_path, &self.package_path)?;
        sync_directory(&self.directory)?;
        self.save_cache_manifest(&candidate)?;
        self.partial_manifest
            .delete()
            .map_err(|_| storage_error())?;
        sync_directory(&self.directory)?;
        Ok(candidate)
    }

    fn validate_disk_state(&self) -> Result<(), UpdateDownloadError> {
        match self.load_cache_manifest()? {
            Some(record) => {
                let metadata = safe_file_metadata(&self.package_path)?.ok_or_else(storage_error)?;
                ensure_private_file_permissions(&metadata)?;
                if metadata.len() != record.size_bytes {
                    return Err(storage_error());
                }
            }
            None if safe_file_metadata(&self.package_path)?.is_some() => {
                return Err(storage_error());
            }
            None => {}
        }
        match self.load_partial_manifest()? {
            Some(_) => {
                let metadata = safe_file_metadata(&self.partial_path)?.ok_or_else(storage_error)?;
                ensure_private_file_permissions(&metadata)?;
            }
            None if safe_file_metadata(&self.partial_path)?.is_some() => {
                return Err(storage_error());
            }
            None => {}
        }
        Ok(())
    }

    fn prepare_partial(
        &self,
        release: &UpdateRelease,
        source: &DownloadSource,
    ) -> Result<PartialUpdateRecord, UpdateDownloadError> {
        if let Some(existing) = self.load_partial_manifest()? {
            if existing.matches(release, source) {
                let metadata = safe_file_metadata(&self.partial_path)?.ok_or_else(storage_error)?;
                ensure_private_file_permissions(&metadata)?;
                return Ok(existing);
            }
            self.discard_partial()?;
        } else if safe_file_metadata(&self.partial_path)?.is_some() {
            return Err(storage_error());
        }
        let record = PartialUpdateRecord::new(release, source);
        self.create_empty_partial()?;
        self.save_partial_manifest(&record)?;
        Ok(record)
    }

    fn create_empty_partial(&self) -> Result<(), UpdateDownloadError> {
        if safe_file_metadata(&self.partial_path)?.is_some() {
            return Err(storage_error());
        }
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        options
            .open(&self.partial_path)
            .and_then(|file| file.sync_all())
            .map_err(|_| storage_error())
    }

    fn open_partial_for_write(&self, append: bool) -> Result<File, UpdateDownloadError> {
        let metadata = safe_file_metadata(&self.partial_path)?.ok_or_else(storage_error)?;
        ensure_private_file_permissions(&metadata)?;
        let mut options = OpenOptions::new();
        options.write(true).append(append);
        options
            .open(&self.partial_path)
            .map_err(|_| storage_error())
    }

    fn discard_partial(&self) -> Result<(), UpdateDownloadError> {
        if let Some(metadata) = safe_file_metadata(&self.partial_path)? {
            ensure_private_file_permissions(&metadata)?;
            match fs::remove_file(&self.partial_path) {
                Ok(()) => {}
                Err(error) if error.kind() == ErrorKind::NotFound => {}
                Err(_) => return Err(storage_error()),
            }
        }
        self.partial_manifest
            .delete()
            .map_err(|_| storage_error())?;
        sync_directory(&self.directory)
    }

    fn load_cache_manifest(&self) -> Result<Option<CachedUpdateRecord>, UpdateDownloadError> {
        let Some(bytes) = self.cache_manifest.load().map_err(|_| storage_error())? else {
            return Ok(None);
        };
        let record: CachedUpdateRecord =
            serde_json::from_slice(bytes.as_slice()).map_err(|_| storage_error())?;
        record.validate().map_err(|_| storage_error())?;
        Ok(Some(record))
    }

    fn load_partial_manifest(&self) -> Result<Option<PartialUpdateRecord>, UpdateDownloadError> {
        let Some(bytes) = self.partial_manifest.load().map_err(|_| storage_error())? else {
            return Ok(None);
        };
        let record: PartialUpdateRecord =
            serde_json::from_slice(bytes.as_slice()).map_err(|_| storage_error())?;
        record.validate().map_err(|_| storage_error())?;
        Ok(Some(record))
    }

    fn save_cache_manifest(&self, record: &CachedUpdateRecord) -> Result<(), UpdateDownloadError> {
        let bytes = serde_json::to_vec(record).map_err(|_| storage_error())?;
        self.cache_manifest
            .save(&bytes)
            .map_err(|_| storage_error())
    }

    fn save_partial_manifest(
        &self,
        record: &PartialUpdateRecord,
    ) -> Result<(), UpdateDownloadError> {
        let bytes = serde_json::to_vec(record).map_err(|_| storage_error())?;
        self.partial_manifest
            .save(&bytes)
            .map_err(|_| storage_error())
    }

    fn verify_signature(
        &self,
        path: &Path,
        encoded_signature: &str,
    ) -> Result<(), UpdateDownloadError> {
        let signature_text = STANDARD
            .decode(encoded_signature)
            .map_err(|_| signature_error())?;
        if signature_text.is_empty() || signature_text.len() > MAX_SIGNATURE_BYTES {
            return Err(signature_error());
        }
        let signature_text = std::str::from_utf8(&signature_text).map_err(|_| signature_error())?;
        let signature = Signature::decode(signature_text).map_err(|_| signature_error())?;
        let mut verifier = self
            .public_key
            .verify_stream(&signature)
            .map_err(|_| signature_error())?;
        let mut file = File::open(path).map_err(|_| storage_error())?;
        let mut buffer = [0_u8; 64 * 1024];
        loop {
            let count = file.read(&mut buffer).map_err(|_| storage_error())?;
            if count == 0 {
                break;
            }
            verifier.update(&buffer[..count]);
        }
        verifier.finalize().map_err(|_| signature_error())
    }
}

fn decode_public_key(encoded: &str) -> Result<PublicKey, UpdateDownloadError> {
    if encoded.is_empty() || encoded.len() > MAX_SIGNATURE_BYTES * 2 {
        return Err(signature_error());
    }
    let bytes = STANDARD.decode(encoded).map_err(|_| signature_error())?;
    if bytes.is_empty() || bytes.len() > MAX_SIGNATURE_BYTES {
        return Err(signature_error());
    }
    let text = std::str::from_utf8(&bytes).map_err(|_| signature_error())?;
    PublicKey::decode(text).map_err(|_| signature_error())
}

fn validate_content_length(
    response: &reqwest::Response,
    expected: u64,
) -> Result<(), UpdateDownloadError> {
    let value = response
        .headers()
        .get(CONTENT_LENGTH)
        .ok_or_else(transport_error)?
        .to_str()
        .map_err(|_| transport_error())?
        .parse::<u64>()
        .map_err(|_| transport_error())?;
    if value == expected {
        Ok(())
    } else {
        Err(transport_error())
    }
}

fn validate_partial_response(
    response: &reqwest::Response,
    offset: u64,
    total: u64,
) -> Result<(), UpdateDownloadError> {
    let value = response
        .headers()
        .get(CONTENT_RANGE)
        .ok_or_else(transport_error)?
        .to_str()
        .map_err(|_| transport_error())?;
    let expected = format!("bytes {offset}-{}/{total}", total - 1);
    if value == expected {
        Ok(())
    } else {
        Err(transport_error())
    }
}

fn partial_length(path: &Path) -> Result<u64, UpdateDownloadError> {
    let metadata = safe_file_metadata(path)?.ok_or_else(storage_error)?;
    ensure_private_file_permissions(&metadata)?;
    Ok(metadata.len())
}

fn digest_file(path: &Path) -> Result<String, UpdateDownloadError> {
    let metadata = safe_file_metadata(path)?.ok_or_else(storage_error)?;
    ensure_private_file_permissions(&metadata)?;
    let mut file = File::open(path).map_err(|_| storage_error())?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = file.read(&mut buffer).map_err(|_| storage_error())?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(hex_digest(hasher.finalize().as_slice()))
}

fn digest_bytes(bytes: &[u8]) -> String {
    hex_digest(Sha256::digest(bytes).as_slice())
}

fn hex_digest(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn is_safe_channel(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 32
        && value.as_bytes()[0].is_ascii_lowercase()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
}

fn is_lower_hex_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn is_strong_etag(value: &str) -> bool {
    value.len() >= 2
        && value.len() <= MAX_ETAG_BYTES
        && value.starts_with('"')
        && value.ends_with('"')
        && !value.starts_with("W/")
        && value
            .as_bytes()
            .iter()
            .all(|byte| byte.is_ascii_graphic() && *byte != b'\\')
}

fn safe_file_metadata(path: &Path) -> Result<Option<fs::Metadata>, UpdateDownloadError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_file() => {
            Err(storage_error())
        }
        Ok(metadata) => Ok(Some(metadata)),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(_) => Err(storage_error()),
    }
}

#[cfg(unix)]
fn ensure_private_file_permissions(metadata: &fs::Metadata) -> Result<(), UpdateDownloadError> {
    use std::os::unix::fs::PermissionsExt;

    if metadata.permissions().mode() & 0o077 == 0 {
        Ok(())
    } else {
        Err(storage_error())
    }
}

#[cfg(not(unix))]
fn ensure_private_file_permissions(_metadata: &fs::Metadata) -> Result<(), UpdateDownloadError> {
    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn atomic_replace(source: &Path, destination: &Path) -> Result<(), UpdateDownloadError> {
    fs::rename(source, destination).map_err(|_| storage_error())
}

#[cfg(target_os = "windows")]
fn atomic_replace(source: &Path, destination: &Path) -> Result<(), UpdateDownloadError> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    fn wide_null(path: &Path) -> Result<Vec<u16>, UpdateDownloadError> {
        let units = path.as_os_str().encode_wide().collect::<Vec<_>>();
        if units.is_empty() || units.len() >= 32_768 || units.contains(&0) {
            return Err(storage_error());
        }
        Ok(units.into_iter().chain(std::iter::once(0)).collect())
    }

    let source = wide_null(source)?;
    let destination = wide_null(destination)?;
    if unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    } == 0
    {
        Err(storage_error())
    } else {
        Ok(())
    }
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<(), UpdateDownloadError> {
    File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(|_| storage_error())
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<(), UpdateDownloadError> {
    Ok(())
}

fn manifest_error() -> UpdateDownloadError {
    UpdateDownloadError(UpdateDownloadErrorCode::ManifestRejected)
}

fn transport_error() -> UpdateDownloadError {
    UpdateDownloadError(UpdateDownloadErrorCode::TransportUnavailable)
}

fn signature_error() -> UpdateDownloadError {
    UpdateDownloadError(UpdateDownloadErrorCode::SignatureRejected)
}

fn storage_error() -> UpdateDownloadError {
    UpdateDownloadError(UpdateDownloadErrorCode::StorageUnavailable)
}

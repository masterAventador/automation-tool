use std::error::Error;
use std::fmt::{Debug, Display, Formatter};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::Mutex;
use uuid::{Uuid, Variant};

use crate::secure_store::{AppDataSecretStore, SecretStore};

#[cfg(unix)]
#[path = "browser_profiles_unix.rs"]
mod browser_profiles_platform;
#[cfg(target_os = "windows")]
#[path = "browser_profiles_windows.rs"]
mod browser_profiles_platform;

use browser_profiles_platform::{PlatformProfile, PlatformProfileLock, PlatformProfileStore};

const PROFILE_ROOT_DIRECTORY: &str = "browser-profiles";
const DOUYIN_DIRECTORY: &str = "douyin";
const PROFILE_ID_GENERATION_ATTEMPTS: usize = 32;
const CURRENT_DOUYIN_PROFILE_FILE: &str = "current-douyin-profile-v1";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SocialPlatform {
    Douyin,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BrowserProfileErrorCode {
    InvalidProfileId,
    ProfileNotFound,
    UnsafeDirectory,
    IdentityChanged,
    ProfileInUse,
    RecoveryRequired,
    StorageUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BrowserProfileError {
    code: BrowserProfileErrorCode,
}

impl BrowserProfileError {
    pub fn code(self) -> BrowserProfileErrorCode {
        self.code
    }

    fn invalid_profile_id() -> Self {
        Self {
            code: BrowserProfileErrorCode::InvalidProfileId,
        }
    }

    pub(super) fn profile_not_found() -> Self {
        Self {
            code: BrowserProfileErrorCode::ProfileNotFound,
        }
    }

    pub(super) fn unsafe_directory() -> Self {
        Self {
            code: BrowserProfileErrorCode::UnsafeDirectory,
        }
    }

    pub(super) fn identity_changed() -> Self {
        Self {
            code: BrowserProfileErrorCode::IdentityChanged,
        }
    }

    pub(super) fn profile_in_use() -> Self {
        Self {
            code: BrowserProfileErrorCode::ProfileInUse,
        }
    }

    pub(super) fn recovery_required() -> Self {
        Self {
            code: BrowserProfileErrorCode::RecoveryRequired,
        }
    }

    pub(super) fn storage_unavailable() -> Self {
        Self {
            code: BrowserProfileErrorCode::StorageUnavailable,
        }
    }
}

impl Display for BrowserProfileError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("browser profile unavailable")
    }
}

impl Error for BrowserProfileError {}

pub(super) enum CreateProfileError {
    Collision,
    Failure(BrowserProfileError),
}

pub struct BrowserProfileStore {
    platform: Arc<PlatformProfileStore>,
    app_data_directory: PathBuf,
    current_profile_store: Mutex<AppDataSecretStore>,
}

impl BrowserProfileStore {
    pub fn initialize(app_data_directory: &Path) -> Result<Self, BrowserProfileError> {
        validate_app_data_path(app_data_directory)?;
        let platform = PlatformProfileStore::initialize(
            app_data_directory,
            PROFILE_ROOT_DIRECTORY,
            DOUYIN_DIRECTORY,
        )?;
        let current_profile_store = AppDataSecretStore::new(
            &app_data_directory.join(PROFILE_ROOT_DIRECTORY),
            CURRENT_DOUYIN_PROFILE_FILE,
        )
        .map_err(|_| BrowserProfileError::storage_unavailable())?;
        Ok(Self {
            platform: Arc::new(platform),
            app_data_directory: app_data_directory.to_path_buf(),
            current_profile_store: Mutex::new(current_profile_store),
        })
    }

    pub fn current_douyin_profile(&self) -> Result<BrowserProfile, BrowserProfileError> {
        let store = self
            .current_profile_store
            .lock()
            .map_err(|_| BrowserProfileError::storage_unavailable())?;
        let stored = store
            .load()
            .map_err(|_| BrowserProfileError::storage_unavailable())?;
        if let Some(stored) = stored {
            let profile_id = std::str::from_utf8(&stored)
                .map_err(|_| BrowserProfileError::storage_unavailable())?;
            require_canonical_profile_id(profile_id)
                .map_err(|_| BrowserProfileError::storage_unavailable())?;
            return self.open_douyin_profile(profile_id);
        }
        let profile = self.create_douyin_profile()?;
        store
            .save(profile.profile_id().as_bytes())
            .map_err(|_| BrowserProfileError::storage_unavailable())?;
        Ok(profile)
    }

    pub fn create_douyin_profile(&self) -> Result<BrowserProfile, BrowserProfileError> {
        for _ in 0..PROFILE_ID_GENERATION_ATTEMPTS {
            let profile_id = generate_profile_id()?;
            match self.platform.create_profile(&profile_id) {
                Ok(handle) => return Ok(self.profile(profile_id, handle)),
                Err(CreateProfileError::Collision) => continue,
                Err(CreateProfileError::Failure(error)) => return Err(error),
            }
        }
        Err(BrowserProfileError::storage_unavailable())
    }

    pub fn open_douyin_profile(
        &self,
        profile_id: &str,
    ) -> Result<BrowserProfile, BrowserProfileError> {
        require_canonical_profile_id(profile_id)?;
        let handle = self.platform.open_profile(profile_id)?;
        Ok(self.profile(profile_id.to_owned(), handle))
    }

    pub fn remove_current_douyin_profile(&self) -> Result<(), BrowserProfileError> {
        let store = self
            .current_profile_store
            .lock()
            .map_err(|_| BrowserProfileError::storage_unavailable())?;
        let Some(stored) = store
            .load()
            .map_err(|_| BrowserProfileError::storage_unavailable())?
        else {
            return Ok(());
        };
        let profile_id =
            std::str::from_utf8(&stored).map_err(|_| BrowserProfileError::storage_unavailable())?;
        require_canonical_profile_id(profile_id)
            .map_err(|_| BrowserProfileError::storage_unavailable())?;
        self.platform.remove_profile(profile_id)?;
        store
            .delete()
            .map_err(|_| BrowserProfileError::storage_unavailable())
    }

    fn profile(&self, profile_id: String, handle: PlatformProfile) -> BrowserProfile {
        BrowserProfile {
            platform: Arc::clone(&self.platform),
            directory: self
                .app_data_directory
                .join(PROFILE_ROOT_DIRECTORY)
                .join(DOUYIN_DIRECTORY)
                .join(&profile_id),
            profile_id,
            handle,
        }
    }
}

impl Debug for BrowserProfileStore {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("BrowserProfileStore")
            .field("platform", &SocialPlatform::Douyin)
            .finish_non_exhaustive()
    }
}

pub struct BrowserProfile {
    platform: Arc<PlatformProfileStore>,
    profile_id: String,
    directory: PathBuf,
    handle: PlatformProfile,
}

impl BrowserProfile {
    pub fn platform(&self) -> SocialPlatform {
        SocialPlatform::Douyin
    }

    pub fn profile_id(&self) -> &str {
        &self.profile_id
    }

    pub fn directory(&self) -> &Path {
        &self.directory
    }

    pub fn revalidate(&self) -> Result<(), BrowserProfileError> {
        self.platform
            .revalidate_profile(&self.profile_id, &self.handle)
    }

    pub fn try_acquire_lock(&self) -> Result<BrowserProfileLock<'_>, BrowserProfileError> {
        self.revalidate()?;
        let handle = self.handle.try_acquire_lock()?;
        self.revalidate()?;
        Ok(BrowserProfileLock {
            profile: self,
            handle: Some(handle),
        })
    }

    pub fn try_acquire_owned_lock(self) -> Result<BrowserProfileLease, BrowserProfileError> {
        self.revalidate()?;
        let handle = self.handle.try_acquire_lock()?;
        self.revalidate()?;
        Ok(BrowserProfileLease {
            profile: self,
            handle: Some(handle),
        })
    }
}

pub struct BrowserProfileLease {
    profile: BrowserProfile,
    handle: Option<PlatformProfileLock>,
}

impl BrowserProfileLease {
    pub fn profile_id(&self) -> &str {
        self.profile.profile_id()
    }

    pub fn directory(&self) -> &Path {
        self.profile.directory()
    }

    pub fn release(mut self) -> Result<(), BrowserProfileError> {
        self.profile.revalidate()?;
        self.handle
            .take()
            .ok_or_else(BrowserProfileError::storage_unavailable)?
            .release()
    }
}

impl Debug for BrowserProfileLease {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("BrowserProfileLease")
            .field("platform", &self.profile.platform())
            .field("profile_id", &self.profile.profile_id())
            .finish_non_exhaustive()
    }
}

impl Debug for BrowserProfile {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("BrowserProfile")
            .field("platform", &SocialPlatform::Douyin)
            .field("profile_id", &self.profile_id)
            .finish_non_exhaustive()
    }
}

pub struct BrowserProfileLock<'profile> {
    profile: &'profile BrowserProfile,
    handle: Option<PlatformProfileLock>,
}

impl BrowserProfileLock<'_> {
    pub fn platform(&self) -> SocialPlatform {
        self.profile.platform()
    }

    pub fn profile_id(&self) -> &str {
        self.profile.profile_id()
    }

    pub fn directory(&self) -> &Path {
        self.profile.directory()
    }

    pub fn release(mut self) -> Result<(), BrowserProfileError> {
        self.profile.revalidate()?;
        self.handle
            .take()
            .ok_or_else(BrowserProfileError::storage_unavailable)?
            .release()
    }
}

impl Debug for BrowserProfileLock<'_> {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("BrowserProfileLock")
            .field("platform", &self.platform())
            .field("profile_id", &self.profile_id())
            .finish_non_exhaustive()
    }
}

fn validate_app_data_path(path: &Path) -> Result<(), BrowserProfileError> {
    if !path.is_absolute()
        || path.parent().is_none()
        || path.components().any(|component| {
            matches!(
                component,
                std::path::Component::CurDir | std::path::Component::ParentDir
            )
        })
    {
        return Err(BrowserProfileError::unsafe_directory());
    }
    Ok(())
}

fn generate_profile_id() -> Result<String, BrowserProfileError> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes).map_err(|_| BrowserProfileError::storage_unavailable())?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(Uuid::from_bytes(bytes).hyphenated().to_string())
}

fn require_canonical_profile_id(profile_id: &str) -> Result<(), BrowserProfileError> {
    let parsed =
        Uuid::parse_str(profile_id).map_err(|_| BrowserProfileError::invalid_profile_id())?;
    if parsed.get_version_num() != 4
        || parsed.get_variant() != Variant::RFC4122
        || parsed.hyphenated().to_string() != profile_id
    {
        return Err(BrowserProfileError::invalid_profile_id());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_profile_ids_are_canonical_random_uuid_v4_values() {
        let first = generate_profile_id().expect("first profile ID");
        let second = generate_profile_id().expect("second profile ID");
        require_canonical_profile_id(&first).expect("canonical first ID");
        require_canonical_profile_id(&second).expect("canonical second ID");
        assert_ne!(first, second);
    }

    #[test]
    fn profile_id_validation_rejects_other_uuid_versions_and_encodings() {
        for value in [
            "550e8400-e29b-11d4-a716-446655440000",
            "550E8400-E29B-41D4-A716-446655440000",
            "550e8400e29b41d4a716446655440000",
            "../550e8400-e29b-41d4-a716-446655440000",
        ] {
            assert_eq!(
                require_canonical_profile_id(value)
                    .expect_err("profile ID must fail")
                    .code(),
                BrowserProfileErrorCode::InvalidProfileId
            );
        }
    }
}

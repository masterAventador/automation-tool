//! Native-only Bilibili open-platform credentials and archive defaults.
//!
//! The WebView may configure a fresh credential bundle, but it can only read
//! the non-sensitive account label and archive defaults afterwards.  Secrets
//! remain in the Rust-managed protected store and are zeroized when their
//! temporary values leave scope.

use crate::secure_store::{AppDataSecretStore, SecretStore, SecureStoreError};
use serde::{Deserialize, Serialize};
use std::error::Error;
use std::fmt::{self, Display, Formatter};
use std::path::Path;
use zeroize::{Zeroize, Zeroizing};

const CREDENTIAL_FILE: &str = "publishing-bilibili-v1";
const MAX_SECRET_LENGTH: usize = 4096;
const MAX_STORED_CREDENTIAL_BYTES: usize = 4096;
const MAX_ACCOUNT_LABEL_CHARACTERS: usize = 80;
const MAX_TAG_CHARACTERS: usize = 200;

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BilibiliServiceSnapshot {
    provider: &'static str,
    provider_label: &'static str,
    configured: bool,
    target_account: Option<String>,
    tid: Option<u32>,
    tag: Option<String>,
    no_reprint: Option<u8>,
}

impl BilibiliServiceSnapshot {
    pub const fn configured(&self) -> bool {
        self.configured
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct ConfigureBilibiliServiceRequest {
    client_id: String,
    app_secret: String,
    access_token: String,
    refresh_token: String,
    expires_at_epoch_seconds: u64,
    target_account: String,
    tid: u32,
    tag: String,
    no_reprint: u8,
}

impl Drop for ConfigureBilibiliServiceRequest {
    fn drop(&mut self) {
        self.client_id.zeroize();
        self.app_secret.zeroize();
        self.access_token.zeroize();
        self.refresh_token.zeroize();
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct StoredCredential {
    version: u32,
    client_id: String,
    app_secret: String,
    access_token: String,
    refresh_token: String,
    expires_at_epoch_seconds: u64,
    target_account: String,
    tid: u32,
    tag: String,
    no_reprint: u8,
}

impl Drop for StoredCredential {
    fn drop(&mut self) {
        self.client_id.zeroize();
        self.app_secret.zeroize();
        self.access_token.zeroize();
        self.refresh_token.zeroize();
    }
}

pub struct BilibiliServiceCredential {
    client_id: Zeroizing<String>,
    app_secret: Zeroizing<String>,
    access_token: Zeroizing<String>,
    refresh_token: Zeroizing<String>,
    expires_at_epoch_seconds: u64,
    target_account: String,
    tid: u32,
    tag: String,
    no_reprint: u8,
}

impl fmt::Debug for BilibiliServiceCredential {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("BilibiliServiceCredential")
            .field("target_account", &self.target_account)
            .field("tid", &self.tid)
            .finish_non_exhaustive()
    }
}

impl BilibiliServiceCredential {
    pub fn client_id(&self) -> &str {
        self.client_id.as_str()
    }

    pub fn app_secret(&self) -> &str {
        self.app_secret.as_str()
    }

    pub fn access_token(&self) -> &str {
        self.access_token.as_str()
    }

    pub fn refresh_token(&self) -> &str {
        self.refresh_token.as_str()
    }

    pub const fn expires_at_epoch_seconds(&self) -> u64 {
        self.expires_at_epoch_seconds
    }

    pub fn target_account(&self) -> &str {
        &self.target_account
    }

    pub const fn tid(&self) -> u32 {
        self.tid
    }

    pub fn tag(&self) -> &str {
        &self.tag
    }

    pub const fn no_reprint(&self) -> u8 {
        self.no_reprint
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct BilibiliCredentialRotation {
    access_token: String,
    refresh_token: String,
    expires_at_epoch_seconds: u64,
}

impl Drop for BilibiliCredentialRotation {
    fn drop(&mut self) {
        self.access_token.zeroize();
        self.refresh_token.zeroize();
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BilibiliServiceErrorCode {
    ConfigurationInvalid,
    ConfigurationRequired,
    StorageUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BilibiliServiceError {
    code: BilibiliServiceErrorCode,
    retryable: bool,
}

impl BilibiliServiceError {
    fn new(code: BilibiliServiceErrorCode, retryable: bool) -> Self {
        Self { code, retryable }
    }

    pub const fn code(self) -> BilibiliServiceErrorCode {
        self.code
    }

    pub const fn retryable(self) -> bool {
        self.retryable
    }
}

impl Display for BilibiliServiceError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str("Bilibili service settings are unavailable")
    }
}

impl Error for BilibiliServiceError {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BilibiliServiceCommandError {
    code: BilibiliServiceErrorCode,
    retryable: bool,
}

impl Serialize for BilibiliServiceCommandError {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        crate::command_error::serialize(&self.code, Some(self.retryable), serializer)
    }
}

impl From<BilibiliServiceError> for BilibiliServiceCommandError {
    fn from(value: BilibiliServiceError) -> Self {
        Self {
            code: value.code,
            retryable: value.retryable,
        }
    }
}

pub struct BilibiliServiceSettings<S> {
    store: S,
}

impl<S> fmt::Debug for BilibiliServiceSettings<S> {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("BilibiliServiceSettings")
            .field("provider", &"bilibili")
            .finish_non_exhaustive()
    }
}

impl<S> BilibiliServiceSettings<S>
where
    S: SecretStore,
{
    pub fn new(store: S) -> Result<Self, BilibiliServiceError> {
        let settings = Self { store };
        let _ = settings.snapshot()?;
        Ok(settings)
    }

    pub fn snapshot(&self) -> Result<BilibiliServiceSnapshot, BilibiliServiceError> {
        let credential = self.load()?;
        Ok(BilibiliServiceSnapshot {
            provider: "bilibili",
            provider_label: "B站开放平台",
            configured: credential.is_some(),
            target_account: credential
                .as_ref()
                .map(|value| value.target_account.clone()),
            tid: credential.as_ref().map(|value| value.tid),
            tag: credential.as_ref().map(|value| value.tag.clone()),
            no_reprint: credential.as_ref().map(|value| value.no_reprint),
        })
    }

    pub fn configure(
        &self,
        request: &ConfigureBilibiliServiceRequest,
    ) -> Result<BilibiliServiceSnapshot, BilibiliServiceError> {
        let credential = StoredCredential {
            version: 1,
            client_id: request.client_id.clone(),
            app_secret: request.app_secret.clone(),
            access_token: request.access_token.clone(),
            refresh_token: request.refresh_token.clone(),
            expires_at_epoch_seconds: request.expires_at_epoch_seconds,
            target_account: request.target_account.clone(),
            tid: request.tid,
            tag: request.tag.clone(),
            no_reprint: request.no_reprint,
        };
        validate_credential(&credential)?;
        self.save(&credential)?;
        self.snapshot()
    }

    pub fn clear(&self) -> Result<BilibiliServiceSnapshot, BilibiliServiceError> {
        self.store.delete().map_err(map_storage_error)?;
        self.snapshot()
    }

    pub fn credential_for_publish(
        &self,
    ) -> Result<BilibiliServiceCredential, BilibiliServiceError> {
        let credential = self.load()?.ok_or_else(|| {
            BilibiliServiceError::new(BilibiliServiceErrorCode::ConfigurationRequired, false)
        })?;
        Ok(BilibiliServiceCredential {
            client_id: Zeroizing::new(credential.client_id.clone()),
            app_secret: Zeroizing::new(credential.app_secret.clone()),
            access_token: Zeroizing::new(credential.access_token.clone()),
            refresh_token: Zeroizing::new(credential.refresh_token.clone()),
            expires_at_epoch_seconds: credential.expires_at_epoch_seconds,
            target_account: credential.target_account.clone(),
            tid: credential.tid,
            tag: credential.tag.clone(),
            no_reprint: credential.no_reprint,
        })
    }

    pub fn apply_rotation(
        &self,
        rotation: &BilibiliCredentialRotation,
    ) -> Result<(), BilibiliServiceError> {
        let mut credential = self.load()?.ok_or_else(|| {
            BilibiliServiceError::new(BilibiliServiceErrorCode::ConfigurationRequired, false)
        })?;
        credential.access_token = rotation.access_token.clone();
        credential.refresh_token = rotation.refresh_token.clone();
        credential.expires_at_epoch_seconds = rotation.expires_at_epoch_seconds;
        validate_credential(&credential)?;
        self.save(&credential)
    }

    fn save(&self, credential: &StoredCredential) -> Result<(), BilibiliServiceError> {
        let bytes = Zeroizing::new(serde_json::to_vec(credential).map_err(|_| {
            BilibiliServiceError::new(BilibiliServiceErrorCode::ConfigurationInvalid, false)
        })?);
        if bytes.len() > MAX_STORED_CREDENTIAL_BYTES {
            return Err(BilibiliServiceError::new(
                BilibiliServiceErrorCode::ConfigurationInvalid,
                false,
            ));
        }
        self.store.save(bytes.as_slice()).map_err(map_storage_error)
    }

    fn load(&self) -> Result<Option<StoredCredential>, BilibiliServiceError> {
        let Some(bytes) = self.store.load().map_err(map_storage_error)? else {
            return Ok(None);
        };
        let credential: StoredCredential =
            serde_json::from_slice(bytes.as_slice()).map_err(|_| {
                BilibiliServiceError::new(BilibiliServiceErrorCode::StorageUnavailable, false)
            })?;
        validate_credential(&credential)?;
        Ok(Some(credential))
    }
}

fn validate_credential(credential: &StoredCredential) -> Result<(), BilibiliServiceError> {
    if credential.version != 1
        || !compact_secret(&credential.client_id)
        || !compact_secret(&credential.app_secret)
        || !compact_secret(&credential.access_token)
        || !compact_secret(&credential.refresh_token)
        || credential.expires_at_epoch_seconds == 0
        || !readable(&credential.target_account, MAX_ACCOUNT_LABEL_CHARACTERS)
        || credential.tid == 0
        || !valid_tag(&credential.tag)
        || !matches!(credential.no_reprint, 0 | 1)
    {
        return Err(BilibiliServiceError::new(
            BilibiliServiceErrorCode::ConfigurationInvalid,
            false,
        ));
    }
    Ok(())
}

fn compact_secret(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_SECRET_LENGTH
        && value.trim() == value
        && !value.chars().any(char::is_whitespace)
}

fn readable(value: &str, maximum: usize) -> bool {
    let count = value.chars().count();
    count > 0
        && count <= maximum
        && value.trim() == value
        && !value.chars().any(|character| character.is_control())
}

fn valid_tag(value: &str) -> bool {
    readable(value, MAX_TAG_CHARACTERS)
        && value.split(',').all(|tag| {
            let trimmed = tag.trim();
            !trimmed.is_empty() && trimmed == tag
        })
}

fn map_storage_error(_error: SecureStoreError) -> BilibiliServiceError {
    BilibiliServiceError::new(BilibiliServiceErrorCode::StorageUnavailable, false)
}

pub(crate) type ProductionBilibiliServiceSettings = BilibiliServiceSettings<AppDataSecretStore>;

pub(crate) fn initialize_production_bilibili_service_settings(
    app_data_directory: &Path,
) -> Result<ProductionBilibiliServiceSettings, BilibiliServiceError> {
    let directory = app_data_directory.join("publishing-services");
    let store = AppDataSecretStore::new(&directory, CREDENTIAL_FILE).map_err(map_storage_error)?;
    BilibiliServiceSettings::new(store)
}

#[cfg(test)]
mod tests {
    use std::cell::RefCell;

    use zeroize::Zeroizing;

    use super::{
        BilibiliCredentialRotation, BilibiliServiceSettings, ConfigureBilibiliServiceRequest,
    };
    use crate::secure_store::{SecretStore, SecureStoreError};

    #[derive(Default)]
    struct MemorySecretStore {
        value: RefCell<Option<Vec<u8>>>,
    }

    impl SecretStore for MemorySecretStore {
        fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, SecureStoreError> {
            Ok(self.value.borrow().clone().map(Zeroizing::new))
        }

        fn save(&self, secret: &[u8]) -> Result<(), SecureStoreError> {
            self.value.replace(Some(secret.to_vec()));
            Ok(())
        }

        fn delete(&self) -> Result<(), SecureStoreError> {
            self.value.replace(None);
            Ok(())
        }
    }

    fn request() -> ConfigureBilibiliServiceRequest {
        ConfigureBilibiliServiceRequest {
            client_id: "client-1".to_owned(),
            app_secret: "app-secret".to_owned(),
            access_token: "access-token".to_owned(),
            refresh_token: "refresh-token".to_owned(),
            expires_at_epoch_seconds: 1_800_000_000,
            target_account: "运营账号".to_owned(),
            tid: 171,
            tag: "自动化,效率".to_owned(),
            no_reprint: 1,
        }
    }

    #[test]
    fn credentials_remain_native_and_rotations_replace_the_protected_tokens() {
        let settings =
            BilibiliServiceSettings::new(MemorySecretStore::default()).expect("settings");
        let snapshot = settings.configure(&request()).expect("configure");
        assert!(snapshot.configured());
        let public = serde_json::to_string(&snapshot).expect("snapshot JSON");
        assert!(public.contains("运营账号"));
        for secret in ["client-1", "app-secret", "access-token", "refresh-token"] {
            assert!(!public.contains(secret));
        }

        let credential = settings.credential_for_publish().expect("credential");
        assert_eq!(credential.target_account(), "运营账号");
        assert_eq!(credential.access_token(), "access-token");
        let debug = format!("{credential:?}");
        assert!(!debug.contains("access-token"));
        drop(credential);

        settings
            .apply_rotation(&BilibiliCredentialRotation {
                access_token: "rotated-access".to_owned(),
                refresh_token: "rotated-refresh".to_owned(),
                expires_at_epoch_seconds: 1_900_000_000,
            })
            .expect("rotate");
        let rotated = settings
            .credential_for_publish()
            .expect("rotated credential");
        assert_eq!(rotated.access_token(), "rotated-access");
        assert_eq!(rotated.refresh_token(), "rotated-refresh");
        assert_eq!(rotated.expires_at_epoch_seconds(), 1_900_000_000);

        assert!(!settings.clear().expect("clear").configured());
    }

    #[test]
    fn invalid_credentials_are_rejected_before_storage() {
        let settings =
            BilibiliServiceSettings::new(MemorySecretStore::default()).expect("settings");
        let mut invalid = request();
        invalid.access_token = "contains whitespace".to_owned();
        assert!(settings.configure(&invalid).is_err());
        assert!(!settings.snapshot().expect("snapshot").configured());
    }

    #[test]
    fn aggregate_credentials_larger_than_the_protected_store_are_rejected_as_configuration() {
        let settings =
            BilibiliServiceSettings::new(MemorySecretStore::default()).expect("settings");
        let mut oversized = request();
        oversized.access_token = "a".repeat(3_000);
        oversized.refresh_token = "r".repeat(3_000);

        let error = settings
            .configure(&oversized)
            .expect_err("oversized aggregate");

        assert_eq!(
            error.code(),
            super::BilibiliServiceErrorCode::ConfigurationInvalid
        );
        assert!(!settings.snapshot().expect("snapshot").configured());
    }
}

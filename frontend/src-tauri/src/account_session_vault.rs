use std::error::Error;
use std::fmt::{Debug, Display, Formatter};
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
use std::path::Path;

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use serde::{Deserialize, Serialize};
use time::format_description::well_known::Rfc3339;
use time::{OffsetDateTime, UtcOffset};
use uuid::{Uuid, Variant};
use zeroize::Zeroizing;

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
use crate::secure_store::AppDataSecretStore;
use crate::secure_store::{SecretStore, SecureStoreError};

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
const ACCOUNT_SESSION_FILE_NAME: &str = "product-account-session-v1";
const ACCOUNT_SESSION_SCHEMA_VERSION: u8 = 1;
const MAX_ACCOUNT_TOKEN_LENGTH: usize = 256;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AccountSessionVaultErrorCode {
    StorageUnavailable,
    InvalidSession,
    CorruptStoredSession,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct AccountSessionVaultError {
    code: AccountSessionVaultErrorCode,
}

impl AccountSessionVaultError {
    fn new(code: AccountSessionVaultErrorCode) -> Self {
        Self { code }
    }

    pub fn code(&self) -> AccountSessionVaultErrorCode {
        self.code
    }
}

impl Display for AccountSessionVaultError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("product account Session unavailable")
    }
}

impl Error for AccountSessionVaultError {}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AccountProjection {
    user_id: String,
    login_name: String,
    status: String,
}

impl AccountProjection {
    pub fn user_id(&self) -> &str {
        &self.user_id
    }

    pub fn login_name(&self) -> &str {
        &self.login_name
    }

    pub fn status(&self) -> &str {
        &self.status
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum AccountSessionSnapshot {
    /// This deployment issues no product accounts, so there is nothing to log
    /// in to. Distinct from `Unauthenticated`, which means the deployment does
    /// have accounts and this device is not holding a session for one.
    #[serde(rename_all = "camelCase")]
    NotRequired { account: Option<AccountProjection> },
    #[serde(rename_all = "camelCase")]
    Unauthenticated { account: Option<AccountProjection> },
    #[serde(rename_all = "camelCase")]
    Authenticated { account: AccountProjection },
}

impl AccountSessionSnapshot {
    pub fn not_required() -> Self {
        Self::NotRequired { account: None }
    }

    pub fn unauthenticated() -> Self {
        Self::Unauthenticated { account: None }
    }

    pub fn authenticated(session: &AccountSessionSecrets) -> Self {
        Self::Authenticated {
            account: session.account.clone(),
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct StoredAccountSession {
    schema_version: u8,
    access_token: String,
    refresh_token: String,
    access_expires_at: String,
    refresh_expires_at: String,
    account: AccountProjection,
}

pub struct AccountSessionSecrets {
    access_token: Zeroizing<String>,
    refresh_token: Zeroizing<String>,
    access_expires_at: String,
    refresh_expires_at: String,
    account: AccountProjection,
}

impl Debug for AccountSessionSecrets {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("AccountSessionSecrets")
            .field("access_token", &"[REDACTED]")
            .field("refresh_token", &"[REDACTED]")
            .field("access_expires_at", &self.access_expires_at)
            .field("refresh_expires_at", &self.refresh_expires_at)
            .field("account", &self.account)
            .finish()
    }
}

impl AccountSessionSecrets {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        access_token: String,
        refresh_token: String,
        access_expires_at: String,
        refresh_expires_at: String,
        user_id: String,
        login_name: String,
        status: String,
    ) -> Result<Self, AccountSessionVaultError> {
        let session = Self {
            access_token: Zeroizing::new(access_token),
            refresh_token: Zeroizing::new(refresh_token),
            access_expires_at,
            refresh_expires_at,
            account: AccountProjection {
                user_id,
                login_name,
                status,
            },
        };
        session.validate(AccountSessionVaultErrorCode::InvalidSession)?;
        Ok(session)
    }

    pub fn access_token(&self) -> &str {
        self.access_token.as_str()
    }

    pub fn refresh_token(&self) -> &str {
        self.refresh_token.as_str()
    }

    pub fn access_expires_at(&self) -> &str {
        &self.access_expires_at
    }

    pub fn refresh_expires_at(&self) -> &str {
        &self.refresh_expires_at
    }

    pub fn account(&self) -> &AccountProjection {
        &self.account
    }

    fn validate(&self, code: AccountSessionVaultErrorCode) -> Result<(), AccountSessionVaultError> {
        require_account_token(self.access_token(), "atas1", code)?;
        require_account_token(self.refresh_token(), "atrs1", code)?;
        let access_expires_at = require_utc_timestamp(&self.access_expires_at, code)?;
        let refresh_expires_at = require_utc_timestamp(&self.refresh_expires_at, code)?;
        if access_expires_at >= refresh_expires_at
            || !is_canonical_uuid_v4(&self.account.user_id)
            || !is_canonical_login_name(&self.account.login_name)
            || self.account.status != "active"
        {
            return Err(AccountSessionVaultError::new(code));
        }
        Ok(())
    }

    fn to_stored(&self) -> StoredAccountSession {
        StoredAccountSession {
            schema_version: ACCOUNT_SESSION_SCHEMA_VERSION,
            access_token: self.access_token().to_owned(),
            refresh_token: self.refresh_token().to_owned(),
            access_expires_at: self.access_expires_at.clone(),
            refresh_expires_at: self.refresh_expires_at.clone(),
            account: self.account.clone(),
        }
    }

    fn from_stored(stored: StoredAccountSession) -> Result<Self, AccountSessionVaultError> {
        if stored.schema_version != ACCOUNT_SESSION_SCHEMA_VERSION {
            return Err(AccountSessionVaultError::new(
                AccountSessionVaultErrorCode::CorruptStoredSession,
            ));
        }
        let session = Self {
            access_token: Zeroizing::new(stored.access_token),
            refresh_token: Zeroizing::new(stored.refresh_token),
            access_expires_at: stored.access_expires_at,
            refresh_expires_at: stored.refresh_expires_at,
            account: stored.account,
        };
        session.validate(AccountSessionVaultErrorCode::CorruptStoredSession)?;
        Ok(session)
    }
}

pub struct AccountSessionVault<S> {
    store: S,
}

impl<S> AccountSessionVault<S>
where
    S: SecretStore,
{
    pub fn new(store: S) -> Self {
        Self { store }
    }

    pub fn load(&self) -> Result<Option<AccountSessionSecrets>, AccountSessionVaultError> {
        let Some(bytes) = self.store.load().map_err(map_store_error)? else {
            return Ok(None);
        };
        let stored = serde_json::from_slice::<StoredAccountSession>(&bytes).map_err(|_| {
            AccountSessionVaultError::new(AccountSessionVaultErrorCode::CorruptStoredSession)
        })?;
        AccountSessionSecrets::from_stored(stored).map(Some)
    }

    pub fn replace(&self, session: &AccountSessionSecrets) -> Result<(), AccountSessionVaultError> {
        session.validate(AccountSessionVaultErrorCode::InvalidSession)?;
        let serialized =
            Zeroizing::new(serde_json::to_vec(&session.to_stored()).map_err(|_| {
                AccountSessionVaultError::new(AccountSessionVaultErrorCode::InvalidSession)
            })?);
        self.store.save(&serialized).map_err(map_store_error)
    }

    pub fn delete(&self) -> Result<(), AccountSessionVaultError> {
        self.store.delete().map_err(map_store_error)
    }
}

fn map_store_error(_error: SecureStoreError) -> AccountSessionVaultError {
    AccountSessionVaultError::new(AccountSessionVaultErrorCode::StorageUnavailable)
}

fn require_account_token(
    value: &str,
    prefix: &str,
    code: AccountSessionVaultErrorCode,
) -> Result<(), AccountSessionVaultError> {
    if value.is_empty() || value.len() > MAX_ACCOUNT_TOKEN_LENGTH {
        return Err(AccountSessionVaultError::new(code));
    }
    let mut segments = value.split('.');
    let (Some(actual_prefix), Some(identifier), Some(secret), None) = (
        segments.next(),
        segments.next(),
        segments.next(),
        segments.next(),
    ) else {
        return Err(AccountSessionVaultError::new(code));
    };
    let decoded = URL_SAFE_NO_PAD
        .decode(secret)
        .map_err(|_| AccountSessionVaultError::new(code))?;
    if actual_prefix != prefix
        || !is_canonical_uuid_v4(identifier)
        || decoded.len() != 32
        || URL_SAFE_NO_PAD.encode(decoded) != secret
    {
        return Err(AccountSessionVaultError::new(code));
    }
    Ok(())
}

fn is_canonical_uuid_v4(value: &str) -> bool {
    Uuid::parse_str(value).is_ok_and(|parsed| {
        parsed.get_version_num() == 4
            && parsed.get_variant() == Variant::RFC4122
            && parsed.hyphenated().to_string() == value
    })
}

fn is_canonical_login_name(value: &str) -> bool {
    (3..=64).contains(&value.len())
        && value.as_bytes()[0].is_ascii_lowercase()
        && value.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || b"._-".contains(&byte)
        })
}

fn require_utc_timestamp(
    value: &str,
    code: AccountSessionVaultErrorCode,
) -> Result<OffsetDateTime, AccountSessionVaultError> {
    let parsed =
        OffsetDateTime::parse(value, &Rfc3339).map_err(|_| AccountSessionVaultError::new(code))?;
    if parsed.offset() != UtcOffset::UTC {
        return Err(AccountSessionVaultError::new(code));
    }
    Ok(parsed)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
pub(crate) type ProductionAccountSessionVault = AccountSessionVault<AppDataSecretStore>;

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
pub(crate) fn initialize_production_account_session_vault(
    app_data_directory: &Path,
) -> Result<ProductionAccountSessionVault, AccountSessionVaultError> {
    let store = AppDataSecretStore::new(app_data_directory, ACCOUNT_SESSION_FILE_NAME)
        .map_err(map_store_error)?;
    Ok(AccountSessionVault::new(store))
}

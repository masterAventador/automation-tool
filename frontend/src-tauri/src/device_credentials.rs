use std::error::Error;
use std::fmt::{Display, Formatter};
use std::path::Path;

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use uuid::{Uuid, Variant};
use zeroize::Zeroizing;

use crate::secure_store::{SecretStore, SecureStoreError};

const MAX_DEVICE_CREDENTIAL_LENGTH: usize = 256;
const DEVICE_CREDENTIAL_FILE_NAME: &str = "device-credential-v1";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DeviceCredentialErrorCode {
    SecureStoreUnavailable,
    InvalidCredential,
    CorruptStoredCredential,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DeviceCredentialError {
    code: DeviceCredentialErrorCode,
}

impl DeviceCredentialError {
    fn new(code: DeviceCredentialErrorCode) -> Self {
        Self { code }
    }

    pub fn code(&self) -> DeviceCredentialErrorCode {
        self.code
    }
}

impl Display for DeviceCredentialError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("device credential unavailable")
    }
}

impl Error for DeviceCredentialError {}

pub struct StoredDeviceCredential {
    bearer: Zeroizing<String>,
}

impl StoredDeviceCredential {
    pub fn as_str(&self) -> &str {
        self.bearer.as_str()
    }
}

pub struct DeviceCredentialVault<S> {
    store: S,
}

impl<S> DeviceCredentialVault<S>
where
    S: SecretStore,
{
    pub fn new(store: S) -> Self {
        Self { store }
    }

    pub fn load(&self) -> Result<Option<StoredDeviceCredential>, DeviceCredentialError> {
        let Some(stored) = self.store.load().map_err(map_store_error)? else {
            return Ok(None);
        };
        let value = std::str::from_utf8(stored.as_slice()).map_err(|_| {
            DeviceCredentialError::new(DeviceCredentialErrorCode::CorruptStoredCredential)
        })?;
        if !is_canonical_device_credential(value) {
            return Err(DeviceCredentialError::new(
                DeviceCredentialErrorCode::CorruptStoredCredential,
            ));
        }
        Ok(Some(StoredDeviceCredential {
            bearer: Zeroizing::new(value.to_owned()),
        }))
    }

    pub fn replace(&self, value: &str) -> Result<(), DeviceCredentialError> {
        if !is_canonical_device_credential(value) {
            return Err(DeviceCredentialError::new(
                DeviceCredentialErrorCode::InvalidCredential,
            ));
        }
        self.store.save(value.as_bytes()).map_err(map_store_error)
    }

    pub fn delete(&self) -> Result<(), DeviceCredentialError> {
        self.store.delete().map_err(map_store_error)
    }

    #[cfg(test)]
    fn store_for_test(&self) -> &S {
        &self.store
    }
}

fn map_store_error(_error: SecureStoreError) -> DeviceCredentialError {
    DeviceCredentialError::new(DeviceCredentialErrorCode::SecureStoreUnavailable)
}

fn is_canonical_device_credential(value: &str) -> bool {
    if value.is_empty() || value.len() > MAX_DEVICE_CREDENTIAL_LENGTH {
        return false;
    }
    let mut segments = value.split('.');
    let (Some(prefix), Some(identifier), Some(encoded_secret), None) = (
        segments.next(),
        segments.next(),
        segments.next(),
        segments.next(),
    ) else {
        return false;
    };
    if prefix != "atdc1" {
        return false;
    }
    let Ok(parsed_id) = Uuid::parse_str(identifier) else {
        return false;
    };
    if parsed_id.get_version_num() != 4
        || parsed_id.get_variant() != Variant::RFC4122
        || parsed_id.hyphenated().to_string() != identifier
    {
        return false;
    }
    let Ok(secret) = URL_SAFE_NO_PAD.decode(encoded_secret) else {
        return false;
    };
    secret.len() == 32 && URL_SAFE_NO_PAD.encode(secret) == encoded_secret
}

pub(crate) type ProductionDeviceCredentialVault =
    DeviceCredentialVault<crate::secure_store::AppDataSecretStore>;

pub(crate) fn initialize_production_device_credential_vault(
    app_data_directory: &Path,
) -> Result<ProductionDeviceCredentialVault, DeviceCredentialError> {
    let store = crate::secure_store::AppDataSecretStore::new(
        app_data_directory,
        DEVICE_CREDENTIAL_FILE_NAME,
    )
    .map_err(map_store_error)?;
    let vault = DeviceCredentialVault::new(store);
    let _ = vault.load()?;
    Ok(vault)
}

#[cfg(test)]
mod tests {
    use std::cell::{Cell, RefCell};

    use zeroize::Zeroizing;

    use super::{
        DeviceCredentialError, DeviceCredentialErrorCode, DeviceCredentialVault,
        StoredDeviceCredential,
    };
    use crate::secure_store::{SecretStore, SecureStoreError};

    const FIRST: &str =
        "atdc1.9ef928d0-92af-45e5-96ac-cc97829b5812.AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8";
    const SECOND: &str =
        "atdc1.ed224c6f-21f5-4587-82fe-a5351e1182e6.eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg";

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    enum FailingOperation {
        Load,
        Save,
        Delete,
    }

    #[derive(Default)]
    struct MemorySecretStore {
        secret: RefCell<Option<Vec<u8>>>,
        failing_operation: Cell<Option<FailingOperation>>,
        save_count: Cell<usize>,
        delete_count: Cell<usize>,
    }

    impl MemorySecretStore {
        fn with_secret(secret: Vec<u8>) -> Self {
            Self {
                secret: RefCell::new(Some(secret)),
                ..Self::default()
            }
        }

        fn fail(operation: FailingOperation) -> Self {
            Self {
                failing_operation: Cell::new(Some(operation)),
                ..Self::default()
            }
        }
    }

    impl SecretStore for MemorySecretStore {
        fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, SecureStoreError> {
            if self.failing_operation.get() == Some(FailingOperation::Load) {
                return Err(SecureStoreError::Unavailable);
            }
            Ok(self.secret.borrow().clone().map(Zeroizing::new))
        }

        fn save(&self, secret: &[u8]) -> Result<(), SecureStoreError> {
            if self.failing_operation.get() == Some(FailingOperation::Save) {
                return Err(SecureStoreError::Unavailable);
            }
            self.save_count.set(self.save_count.get() + 1);
            self.secret.replace(Some(secret.to_vec()));
            Ok(())
        }

        fn delete(&self) -> Result<(), SecureStoreError> {
            if self.failing_operation.get() == Some(FailingOperation::Delete) {
                return Err(SecureStoreError::Unavailable);
            }
            self.delete_count.set(self.delete_count.get() + 1);
            self.secret.replace(None);
            Ok(())
        }
    }

    fn loaded_text(loaded: Option<StoredDeviceCredential>) -> String {
        loaded.expect("stored credential").as_str().to_owned()
    }

    fn load_error(
        result: Result<Option<StoredDeviceCredential>, DeviceCredentialError>,
    ) -> DeviceCredentialError {
        match result {
            Err(error) => error,
            Ok(_) => panic!("expected device credential load to fail"),
        }
    }

    #[test]
    fn missing_credential_is_distinct_from_store_failure() {
        let vault = DeviceCredentialVault::new(MemorySecretStore::default());

        assert!(vault.load().expect("empty store").is_none());
        assert_eq!(vault.store_for_test().save_count.get(), 0);
    }

    #[test]
    fn valid_credential_round_trips_and_rotation_replaces_the_old_value() {
        let vault = DeviceCredentialVault::new(MemorySecretStore::default());

        vault.replace(FIRST).expect("save first credential");
        assert_eq!(loaded_text(vault.load().expect("load first")), FIRST);
        vault.replace(SECOND).expect("replace credential");

        assert_eq!(loaded_text(vault.load().expect("load replacement")), SECOND);
        assert_eq!(vault.store_for_test().save_count.get(), 2);
        assert!(!loaded_text(vault.load().expect("load final")).contains(FIRST));
    }

    #[test]
    fn deletion_is_idempotent_and_removes_the_credential() {
        let vault = DeviceCredentialVault::new(MemorySecretStore::default());
        vault.replace(FIRST).expect("seed credential");

        vault.delete().expect("first delete");
        vault.delete().expect("idempotent delete");

        assert!(vault.load().expect("deleted store").is_none());
        assert_eq!(vault.store_for_test().delete_count.get(), 2);
    }

    #[test]
    fn invalid_replacements_are_rejected_before_touching_the_store() {
        let vault = DeviceCredentialVault::new(MemorySecretStore::default());
        let invalid = [
            "",
            "atdc2.9ef928d0-92af-45e5-96ac-cc97829b5812.AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
            "atdc1.not-a-uuid.AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
            "atdc1.123e4567-e89b-12d3-a456-426614174000.AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
            "atdc1.9ef928d0-92af-45e5-96ac-cc97829b5812.AA",
            "atdc1.9ef928d0-92af-45e5-96ac-cc97829b5812.AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
            "atdc1.9ef928d0-92af-45e5-96ac-cc97829b5812.私密值",
        ];

        for value in invalid {
            let error = vault.replace(value).expect_err("invalid credential");
            assert_eq!(error.code(), DeviceCredentialErrorCode::InvalidCredential);
            assert_eq!(error.to_string(), "device credential unavailable");
            if !value.is_empty() {
                assert!(!error.to_string().contains(value));
            }
        }
        assert_eq!(vault.store_for_test().save_count.get(), 0);
    }

    #[test]
    fn corrupt_stored_values_fail_closed_without_delete_or_rewrite() {
        let corrupt_values = [
            vec![0xff, 0xfe],
            b"private-invalid-credential".to_vec(),
            format!("{FIRST}private-trailing-data").into_bytes(),
        ];

        for corrupt in corrupt_values {
            let vault = DeviceCredentialVault::new(MemorySecretStore::with_secret(corrupt));
            let error = load_error(vault.load());

            assert_eq!(
                error.code(),
                DeviceCredentialErrorCode::CorruptStoredCredential
            );
            assert_eq!(error.to_string(), "device credential unavailable");
            assert_eq!(vault.store_for_test().save_count.get(), 0);
            assert_eq!(vault.store_for_test().delete_count.get(), 0);
        }
    }

    #[test]
    fn secure_store_failures_are_fixed_and_do_not_leak_credentials() {
        let load_error = load_error(
            DeviceCredentialVault::new(MemorySecretStore::fail(FailingOperation::Load)).load(),
        );
        let save_error =
            DeviceCredentialVault::new(MemorySecretStore::fail(FailingOperation::Save))
                .replace(FIRST)
                .expect_err("save denied");
        let delete_error =
            DeviceCredentialVault::new(MemorySecretStore::fail(FailingOperation::Delete))
                .delete()
                .expect_err("delete denied");

        for error in [load_error, save_error, delete_error] {
            assert_eq!(
                error.code(),
                DeviceCredentialErrorCode::SecureStoreUnavailable
            );
            assert_eq!(error.to_string(), "device credential unavailable");
            assert!(!format!("{error:?}").contains(FIRST));
        }
    }

    #[test]
    fn credential_and_error_types_do_not_expose_secret_debug_output() {
        fn assert_error(_: DeviceCredentialError) {}

        assert_error(DeviceCredentialError::new(
            DeviceCredentialErrorCode::SecureStoreUnavailable,
        ));
        let vault =
            DeviceCredentialVault::new(MemorySecretStore::with_secret(FIRST.as_bytes().to_vec()));
        let loaded = vault.load().expect("load").expect("stored");
        assert_eq!(loaded.as_str(), FIRST);
    }

    #[test]
    fn real_app_data_device_credential_round_trip() {
        use std::fs;
        use std::time::{SystemTime, UNIX_EPOCH};

        use crate::secure_store::AppDataSecretStore;

        let unique_directory = std::env::temp_dir().join(format!(
            "i2-08-test-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos()
        ));
        let store = AppDataSecretStore::new(&unique_directory, "device-credential-v1")
            .expect("create app data store");
        let vault = DeviceCredentialVault::new(store);

        let result = (|| {
            vault.replace(FIRST)?;
            let loaded = vault.load()?.expect("stored credential");
            assert_eq!(loaded.as_str(), FIRST);
            vault.delete()?;
            assert!(vault.load()?.is_none());
            Ok::<(), DeviceCredentialError>(())
        })();
        let cleanup = vault.delete();

        let directory_cleanup = fs::remove_dir_all(&unique_directory);

        result.expect("app data credential round trip");
        cleanup.expect("clean app data test credential");
        directory_cleanup.expect("clean app data test directory");
    }
}

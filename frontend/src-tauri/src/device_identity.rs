use std::error::Error;
use std::fmt::{Display, Formatter};
use std::path::Path;

use ed25519_dalek::{Signer, SigningKey};
use zeroize::Zeroizing;

use crate::secure_store::{SecretStore, SecureStoreError};

const DEVICE_SECRET_LENGTH: usize = 32;
const DEVICE_IDENTITY_FILE_NAME: &str = "device-identity-ed25519-v1";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum DeviceIdentityErrorCode {
    SecureStoreUnavailable,
    CorruptStoredKey,
    RandomnessUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct DeviceIdentityError {
    code: DeviceIdentityErrorCode,
}

impl DeviceIdentityError {
    fn new(code: DeviceIdentityErrorCode) -> Self {
        Self { code }
    }

    #[cfg(test)]
    fn code(&self) -> DeviceIdentityErrorCode {
        self.code
    }
}

impl Display for DeviceIdentityError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("device identity unavailable")
    }
}

impl Error for DeviceIdentityError {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct DevicePublicIdentity {
    public_key: [u8; DEVICE_SECRET_LENGTH],
}

pub struct ProductionDeviceIdentity {
    public_identity: DevicePublicIdentity,
    store: crate::secure_store::AppDataSecretStore,
}

impl ProductionDeviceIdentity {
    pub(crate) fn public_key(&self) -> &[u8; DEVICE_SECRET_LENGTH] {
        self.public_identity.as_bytes()
    }

    pub(crate) fn sign(&self, payload: &[u8]) -> Result<[u8; 64], DeviceIdentityError> {
        let stored_secret = self.store.load().map_err(map_store_error)?.ok_or_else(|| {
            DeviceIdentityError::new(DeviceIdentityErrorCode::SecureStoreUnavailable)
        })?;
        let secret: [u8; DEVICE_SECRET_LENGTH] = stored_secret
            .as_slice()
            .try_into()
            .map_err(|_| DeviceIdentityError::new(DeviceIdentityErrorCode::CorruptStoredKey))?;
        let secret = Zeroizing::new(secret);
        Ok(SigningKey::from_bytes(&secret).sign(payload).to_bytes())
    }
}

impl DevicePublicIdentity {
    pub(crate) fn as_bytes(&self) -> &[u8; DEVICE_SECRET_LENGTH] {
        &self.public_key
    }
}

trait SecretKeyGenerator {
    fn generate(&self) -> Result<Zeroizing<[u8; DEVICE_SECRET_LENGTH]>, DeviceIdentityError>;
}

struct SystemSecretKeyGenerator;

impl SecretKeyGenerator for SystemSecretKeyGenerator {
    fn generate(&self) -> Result<Zeroizing<[u8; DEVICE_SECRET_LENGTH]>, DeviceIdentityError> {
        let mut secret = Zeroizing::new([0_u8; DEVICE_SECRET_LENGTH]);
        getrandom::fill(&mut *secret).map_err(|_| {
            DeviceIdentityError::new(DeviceIdentityErrorCode::RandomnessUnavailable)
        })?;
        Ok(secret)
    }
}

struct DeviceIdentityManager<'a, S, G> {
    store: &'a S,
    generator: &'a G,
}

impl<'a, S, G> DeviceIdentityManager<'a, S, G>
where
    S: SecretStore,
    G: SecretKeyGenerator,
{
    fn new(store: &'a S, generator: &'a G) -> Self {
        Self { store, generator }
    }

    fn get_or_create(&self) -> Result<DevicePublicIdentity, DeviceIdentityError> {
        if let Some(stored_secret) = self.store.load().map_err(map_store_error)? {
            return identity_from_stored_secret(&stored_secret);
        }

        let secret = self.generator.generate()?;
        self.store.save(secret.as_ref()).map_err(map_store_error)?;
        Ok(identity_from_secret(&secret))
    }
}

fn map_store_error(_error: SecureStoreError) -> DeviceIdentityError {
    DeviceIdentityError::new(DeviceIdentityErrorCode::SecureStoreUnavailable)
}

fn identity_from_stored_secret(
    stored_secret: &[u8],
) -> Result<DevicePublicIdentity, DeviceIdentityError> {
    let secret: [u8; DEVICE_SECRET_LENGTH] = stored_secret
        .try_into()
        .map_err(|_| DeviceIdentityError::new(DeviceIdentityErrorCode::CorruptStoredKey))?;
    let secret = Zeroizing::new(secret);
    Ok(identity_from_secret(&secret))
}

fn identity_from_secret(secret: &[u8; DEVICE_SECRET_LENGTH]) -> DevicePublicIdentity {
    let signing_key = SigningKey::from_bytes(secret);
    DevicePublicIdentity {
        public_key: signing_key.verifying_key().to_bytes(),
    }
}

pub(crate) fn initialize_production_identity(
    app_data_directory: &Path,
) -> Result<ProductionDeviceIdentity, DeviceIdentityError> {
    let store =
        crate::secure_store::AppDataSecretStore::new(app_data_directory, DEVICE_IDENTITY_FILE_NAME)
            .map_err(map_store_error)?;
    let public_identity =
        DeviceIdentityManager::new(&store, &SystemSecretKeyGenerator).get_or_create()?;
    Ok(ProductionDeviceIdentity {
        public_identity,
        store,
    })
}

#[cfg(feature = "desktop-e2e")]
pub(crate) fn initialize_ephemeral_identity() -> Result<DevicePublicIdentity, DeviceIdentityError> {
    let secret = SystemSecretKeyGenerator.generate()?;
    Ok(identity_from_secret(&secret))
}

#[cfg(test)]
mod tests {
    use std::cell::{Cell, RefCell};
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    use ed25519_dalek::{Signature, SigningKey, Verifier, VerifyingKey};
    use zeroize::Zeroizing;

    use super::{
        initialize_production_identity, DeviceIdentityError, DeviceIdentityErrorCode,
        DeviceIdentityManager, SecretKeyGenerator, SystemSecretKeyGenerator,
    };
    use crate::secure_store::{SecretStore, SecureStoreError};

    #[derive(Default)]
    struct MemorySecretStore {
        secret: RefCell<Option<Vec<u8>>>,
        fail_load: Cell<bool>,
        fail_save: Cell<bool>,
        save_count: Cell<usize>,
    }

    impl MemorySecretStore {
        fn with_secret(secret: Vec<u8>) -> Self {
            Self {
                secret: RefCell::new(Some(secret)),
                ..Self::default()
            }
        }

        fn saved_secret(&self) -> Option<Vec<u8>> {
            self.secret.borrow().clone()
        }
    }

    impl SecretStore for MemorySecretStore {
        fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, SecureStoreError> {
            if self.fail_load.get() {
                return Err(SecureStoreError::Unavailable);
            }
            Ok(self.secret.borrow().clone().map(Zeroizing::new))
        }

        fn save(&self, secret: &[u8]) -> Result<(), SecureStoreError> {
            if self.fail_save.get() {
                return Err(SecureStoreError::Unavailable);
            }
            self.save_count.set(self.save_count.get() + 1);
            self.secret.replace(Some(secret.to_vec()));
            Ok(())
        }

        fn delete(&self) -> Result<(), SecureStoreError> {
            self.secret.replace(None);
            Ok(())
        }
    }

    struct FixedSecretKeyGenerator {
        secret: [u8; 32],
        fail: Cell<bool>,
        calls: Cell<usize>,
    }

    impl FixedSecretKeyGenerator {
        fn new(secret: [u8; 32]) -> Self {
            Self {
                secret,
                fail: Cell::new(false),
                calls: Cell::new(0),
            }
        }
    }

    impl SecretKeyGenerator for FixedSecretKeyGenerator {
        fn generate(&self) -> Result<Zeroizing<[u8; 32]>, DeviceIdentityError> {
            self.calls.set(self.calls.get() + 1);
            if self.fail.get() {
                return Err(DeviceIdentityError::new(
                    DeviceIdentityErrorCode::RandomnessUnavailable,
                ));
            }
            Ok(Zeroizing::new(self.secret))
        }
    }

    struct TemporaryAppData {
        path: std::path::PathBuf,
    }

    impl TemporaryAppData {
        fn new(label: &str) -> Self {
            Self {
                path: std::env::temp_dir().join(format!(
                    "{label}-{}-{}",
                    std::process::id(),
                    SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .expect("system time")
                        .as_nanos()
                )),
            }
        }
    }

    impl Drop for TemporaryAppData {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    #[test]
    fn first_use_generates_and_persists_only_a_32_byte_secret() {
        let store = MemorySecretStore::default();
        let generator = FixedSecretKeyGenerator::new([7; 32]);
        let manager = DeviceIdentityManager::new(&store, &generator);

        let first = manager.get_or_create().expect("first identity");
        let second = manager.get_or_create().expect("persisted identity");
        let expected_public_key = SigningKey::from_bytes(&[7; 32]).verifying_key().to_bytes();

        assert_eq!(first.as_bytes(), &expected_public_key);
        assert_eq!(second, first);
        assert_eq!(store.saved_secret().expect("saved secret").len(), 32);
        assert_eq!(store.save_count.get(), 1);
        assert_eq!(generator.calls.get(), 1);
    }

    #[test]
    fn an_existing_secret_is_reused_without_generation_or_rewrite() {
        let store = MemorySecretStore::with_secret(vec![9; 32]);
        let generator = FixedSecretKeyGenerator::new([1; 32]);
        generator.fail.set(true);
        let manager = DeviceIdentityManager::new(&store, &generator);

        let identity = manager.get_or_create().expect("existing identity");
        let expected_public_key = SigningKey::from_bytes(&[9; 32]).verifying_key().to_bytes();

        assert_eq!(identity.as_bytes(), &expected_public_key);
        assert_eq!(store.save_count.get(), 0);
        assert_eq!(generator.calls.get(), 0);
    }

    #[test]
    fn corrupt_stored_secrets_fail_closed_without_rotation() {
        for invalid_length in [0, 31, 33, 64] {
            let store = MemorySecretStore::with_secret(vec![3; invalid_length]);
            let generator = FixedSecretKeyGenerator::new([4; 32]);
            let manager = DeviceIdentityManager::new(&store, &generator);

            let error = manager.get_or_create().expect_err("corrupt secret");

            assert_eq!(error.code(), DeviceIdentityErrorCode::CorruptStoredKey);
            assert_eq!(store.save_count.get(), 0);
            assert_eq!(generator.calls.get(), 0);
        }
    }

    #[test]
    fn secure_store_and_randomness_failures_are_fixed_and_non_leaking() {
        let load_store = MemorySecretStore::default();
        load_store.fail_load.set(true);
        let generator = FixedSecretKeyGenerator::new([5; 32]);
        let load_error = DeviceIdentityManager::new(&load_store, &generator)
            .get_or_create()
            .expect_err("load failure");

        let save_store = MemorySecretStore::default();
        save_store.fail_save.set(true);
        let save_error = DeviceIdentityManager::new(&save_store, &generator)
            .get_or_create()
            .expect_err("save failure");

        let random_store = MemorySecretStore::default();
        let failing_generator = FixedSecretKeyGenerator::new([6; 32]);
        failing_generator.fail.set(true);
        let random_error = DeviceIdentityManager::new(&random_store, &failing_generator)
            .get_or_create()
            .expect_err("randomness failure");

        assert_eq!(
            load_error.code(),
            DeviceIdentityErrorCode::SecureStoreUnavailable
        );
        assert_eq!(
            save_error.code(),
            DeviceIdentityErrorCode::SecureStoreUnavailable
        );
        assert_eq!(
            random_error.code(),
            DeviceIdentityErrorCode::RandomnessUnavailable
        );
        assert_eq!(load_error.to_string(), "device identity unavailable");
        assert_eq!(save_error.to_string(), "device identity unavailable");
        assert_eq!(random_error.to_string(), "device identity unavailable");
        assert!(!format!("{load_error:?}").contains("secret"));
    }

    #[test]
    fn system_generation_produces_distinct_ed25519_identities() {
        let first_store = MemorySecretStore::default();
        let second_store = MemorySecretStore::default();
        let generator = SystemSecretKeyGenerator;

        let first = DeviceIdentityManager::new(&first_store, &generator)
            .get_or_create()
            .expect("first random identity");
        let second = DeviceIdentityManager::new(&second_store, &generator)
            .get_or_create()
            .expect("second random identity");

        assert_ne!(first, second);
        assert_ne!(first_store.saved_secret(), second_store.saved_secret());
    }

    #[test]
    fn real_app_data_secure_store_round_trip() {
        use crate::secure_store::AppDataSecretStore;

        let app_data = TemporaryAppData::new("i2-04-test");
        let store = AppDataSecretStore::new(&app_data.path, "device-identity-ed25519-v1")
            .expect("create app data store");
        let generator = SystemSecretKeyGenerator;
        let manager = DeviceIdentityManager::new(&store, &generator);

        let first = manager.get_or_create().expect("store app data identity");
        let second = manager.get_or_create().expect("reload app data identity");

        assert_eq!(second, first);
        assert_eq!(
            store.load().expect("load app data secret").unwrap().len(),
            32
        );
        store.delete().expect("delete app data test identity");
        assert!(store.load().expect("confirm deletion").is_none());
    }

    #[test]
    fn production_identity_reloads_private_app_data_for_signing_and_rejects_corruption() {
        let app_data = TemporaryAppData::new("i2-09-signing-test");
        let identity = initialize_production_identity(&app_data.path).expect("production identity");
        let payload = b"registration-proof-payload";

        let signature = Signature::from_bytes(&identity.sign(payload).expect("signature"));
        let verifying_key =
            VerifyingKey::from_bytes(identity.public_key()).expect("public verifying key");
        verifying_key
            .verify(payload, &signature)
            .expect("valid device proof");

        let reloaded =
            initialize_production_identity(&app_data.path).expect("reloaded production identity");
        assert_eq!(reloaded.public_key(), identity.public_key());

        fs::write(app_data.path.join("device-identity-ed25519-v1"), [9_u8; 31])
            .expect("corrupt stored identity");
        let error = reloaded
            .sign(payload)
            .expect_err("corrupt signing identity");
        assert_eq!(error.code(), DeviceIdentityErrorCode::CorruptStoredKey);
        assert_eq!(error.to_string(), "device identity unavailable");
    }
}

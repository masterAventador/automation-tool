use std::error::Error;
use std::fmt::{Display, Formatter};

use ed25519_dalek::SigningKey;
use zeroize::Zeroizing;

const DEVICE_SECRET_LENGTH: usize = 32;
#[cfg(any(target_os = "macos", target_os = "windows"))]
const DEVICE_KEYRING_SERVICE: &str = "com.aventador.automationtool.device-identity";
#[cfg(any(target_os = "macos", target_os = "windows"))]
const DEVICE_KEYRING_ACCOUNT: &str = "ed25519-signing-key-v1";

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

impl DevicePublicIdentity {
    pub(crate) fn as_bytes(&self) -> &[u8; DEVICE_SECRET_LENGTH] {
        &self.public_key
    }
}

trait DeviceSecretStore {
    fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, DeviceIdentityError>;
    fn save(&self, secret: &[u8]) -> Result<(), DeviceIdentityError>;
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
    S: DeviceSecretStore,
    G: SecretKeyGenerator,
{
    fn new(store: &'a S, generator: &'a G) -> Self {
        Self { store, generator }
    }

    fn get_or_create(&self) -> Result<DevicePublicIdentity, DeviceIdentityError> {
        if let Some(stored_secret) = self.store.load()? {
            return identity_from_stored_secret(&stored_secret);
        }

        let secret = self.generator.generate()?;
        self.store.save(secret.as_ref())?;
        Ok(identity_from_secret(&secret))
    }
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

#[cfg(any(target_os = "macos", target_os = "windows"))]
struct KeyringDeviceSecretStore {
    entry: keyring::v1::Entry,
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
impl KeyringDeviceSecretStore {
    fn new(service: &str, account: &str) -> Result<Self, DeviceIdentityError> {
        let entry = keyring::v1::Entry::new(service, account).map_err(|_| {
            DeviceIdentityError::new(DeviceIdentityErrorCode::SecureStoreUnavailable)
        })?;
        Ok(Self { entry })
    }

    #[cfg(test)]
    fn delete_for_test(&self) -> Result<(), DeviceIdentityError> {
        match self.entry.delete_credential() {
            Ok(()) | Err(keyring::v1::Error::NoEntry) => Ok(()),
            Err(_) => Err(DeviceIdentityError::new(
                DeviceIdentityErrorCode::SecureStoreUnavailable,
            )),
        }
    }
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
impl DeviceSecretStore for KeyringDeviceSecretStore {
    fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, DeviceIdentityError> {
        match self.entry.get_secret() {
            Ok(secret) => Ok(Some(Zeroizing::new(secret))),
            Err(keyring::v1::Error::NoEntry) => Ok(None),
            Err(_) => Err(DeviceIdentityError::new(
                DeviceIdentityErrorCode::SecureStoreUnavailable,
            )),
        }
    }

    fn save(&self, secret: &[u8]) -> Result<(), DeviceIdentityError> {
        self.entry
            .set_secret(secret)
            .map_err(|_| DeviceIdentityError::new(DeviceIdentityErrorCode::SecureStoreUnavailable))
    }
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
pub(crate) fn initialize_production_identity() -> Result<DevicePublicIdentity, DeviceIdentityError>
{
    let store = KeyringDeviceSecretStore::new(DEVICE_KEYRING_SERVICE, DEVICE_KEYRING_ACCOUNT)?;
    DeviceIdentityManager::new(&store, &SystemSecretKeyGenerator).get_or_create()
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
struct UnsupportedDeviceSecretStore;

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
impl DeviceSecretStore for UnsupportedDeviceSecretStore {
    fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, DeviceIdentityError> {
        Err(DeviceIdentityError::new(
            DeviceIdentityErrorCode::SecureStoreUnavailable,
        ))
    }

    fn save(&self, _secret: &[u8]) -> Result<(), DeviceIdentityError> {
        Err(DeviceIdentityError::new(
            DeviceIdentityErrorCode::SecureStoreUnavailable,
        ))
    }
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
pub(crate) fn initialize_production_identity() -> Result<DevicePublicIdentity, DeviceIdentityError>
{
    DeviceIdentityManager::new(&UnsupportedDeviceSecretStore, &SystemSecretKeyGenerator)
        .get_or_create()
}

#[cfg(feature = "desktop-e2e")]
pub(crate) fn initialize_ephemeral_identity() -> Result<DevicePublicIdentity, DeviceIdentityError> {
    let secret = SystemSecretKeyGenerator.generate()?;
    Ok(identity_from_secret(&secret))
}

#[cfg(test)]
mod tests {
    use std::cell::{Cell, RefCell};

    use ed25519_dalek::SigningKey;
    use zeroize::Zeroizing;

    use super::{
        DeviceIdentityError, DeviceIdentityErrorCode, DeviceIdentityManager, DeviceSecretStore,
        SecretKeyGenerator, SystemSecretKeyGenerator,
    };

    #[cfg(any(target_os = "macos", target_os = "windows"))]
    struct PlatformSecretCleanup<'a>(&'a super::KeyringDeviceSecretStore);

    #[cfg(any(target_os = "macos", target_os = "windows"))]
    impl Drop for PlatformSecretCleanup<'_> {
        fn drop(&mut self) {
            let _ = self.0.delete_for_test();
        }
    }

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

    impl DeviceSecretStore for MemorySecretStore {
        fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, DeviceIdentityError> {
            if self.fail_load.get() {
                return Err(DeviceIdentityError::new(
                    DeviceIdentityErrorCode::SecureStoreUnavailable,
                ));
            }
            Ok(self.secret.borrow().clone().map(Zeroizing::new))
        }

        fn save(&self, secret: &[u8]) -> Result<(), DeviceIdentityError> {
            if self.fail_save.get() {
                return Err(DeviceIdentityError::new(
                    DeviceIdentityErrorCode::SecureStoreUnavailable,
                ));
            }
            self.save_count.set(self.save_count.get() + 1);
            self.secret.replace(Some(secret.to_vec()));
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

    #[cfg(any(target_os = "macos", target_os = "windows"))]
    #[test]
    fn real_platform_secure_store_round_trip() {
        use std::time::{SystemTime, UNIX_EPOCH};

        use super::KeyringDeviceSecretStore;

        let unique_account = format!(
            "i2-04-test-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos()
        );
        let store = KeyringDeviceSecretStore::new(
            "com.aventador.automationtool.tests.device-identity",
            &unique_account,
        )
        .expect("create platform keyring entry");
        let _cleanup = PlatformSecretCleanup(&store);
        let generator = SystemSecretKeyGenerator;
        let manager = DeviceIdentityManager::new(&store, &generator);

        let first = manager.get_or_create().expect("store platform identity");
        let second = manager.get_or_create().expect("reload platform identity");

        assert_eq!(second, first);
        assert_eq!(
            store.load().expect("load platform secret").unwrap().len(),
            32
        );
        store
            .delete_for_test()
            .expect("delete platform test identity");
        assert!(store.load().expect("confirm deletion").is_none());
    }
}

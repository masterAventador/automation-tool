//! Reads the one short-lived registration grant a co-located Control Plane
//! leaves in the App private directory.
//!
//! The document is written by another process, so it is treated as untrusted
//! external input: it must arrive in exactly the frozen canonical form, name
//! the local environment, carry a well-formed `atb1` token and still be valid.
//! Nothing here performs the registration; the grant is handed to the same
//! `ControlPlaneClient::register_installation` every deployment uses, so no
//! second issuing or credential path exists.
//!
//! Contract: `contracts/protocol/local-registration-handoff-v1.json`.

use std::error::Error;
use std::fmt::{Display, Formatter};
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::control_plane::{ControlPlaneErrorCode, DemoBootstrap};
use crate::secure_store::{AppDataSecretStore, SecretStore, SecureStoreError};

pub const LOCAL_REGISTRATION_HANDOFF_FILE_NAME: &str = "local-registration-bootstrap-v1";
pub const LOCAL_ENVIRONMENT_ID: &str = "local";
pub const HANDOFF_DOCUMENT_VERSION: u32 = 1;
pub const MAX_LOCAL_REGISTRATION_HANDOFF_BYTES: usize = 4096;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LocalRegistrationHandoffErrorCode {
    /// The private file exists but cannot be read safely: a symlink, a
    /// directory, a mode other users can read, or an oversized body.
    StorageUnavailable,
    /// The document is not the exact canonical grant this App accepts.
    HandoffInvalid,
    /// The grant is well formed but no longer valid.
    HandoffExpired,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LocalRegistrationHandoffError {
    code: LocalRegistrationHandoffErrorCode,
}

impl LocalRegistrationHandoffError {
    fn new(code: LocalRegistrationHandoffErrorCode) -> Self {
        Self { code }
    }

    pub fn code(&self) -> LocalRegistrationHandoffErrorCode {
        self.code
    }
}

impl Display for LocalRegistrationHandoffError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("local registration handoff is unusable")
    }
}

impl Error for LocalRegistrationHandoffError {}

/// Field order is the frozen canonical order; re-serialising it is what proves
/// the file carried no duplicate key, no reordering and no extra whitespace.
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct HandoffDocument {
    environment_id: String,
    expires_at: i64,
    token: String,
    version: u32,
}

pub struct LocalRegistrationHandoff {
    bootstrap: DemoBootstrap,
}

impl LocalRegistrationHandoff {
    pub fn bootstrap(&self) -> &DemoBootstrap {
        &self.bootstrap
    }
}

/// Opaque on purpose: a derived implementation would print the grant, and
/// test failures, panics and assertions are the easiest way for a secret to
/// escape into a log.
impl std::fmt::Debug for LocalRegistrationHandoff {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("LocalRegistrationHandoff")
    }
}

fn invalid() -> LocalRegistrationHandoffError {
    LocalRegistrationHandoffError::new(LocalRegistrationHandoffErrorCode::HandoffInvalid)
}

pub fn parse_local_registration_handoff(
    raw: &[u8],
    now_unix: i64,
) -> Result<LocalRegistrationHandoff, LocalRegistrationHandoffError> {
    if raw.is_empty() || raw.len() > MAX_LOCAL_REGISTRATION_HANDOFF_BYTES {
        return Err(invalid());
    }
    let document: HandoffDocument = serde_json::from_slice(raw).map_err(|_| invalid())?;
    let canonical = serde_json::to_vec(&document).map_err(|_| invalid())?;
    if canonical != raw {
        return Err(invalid());
    }
    if document.version != HANDOFF_DOCUMENT_VERSION
        || document.environment_id != LOCAL_ENVIRONMENT_ID
    {
        return Err(invalid());
    }
    let bootstrap = DemoBootstrap::new(document.token, document.environment_id)
        .map_err(|_| invalid())?;
    if document.expires_at <= now_unix {
        return Err(LocalRegistrationHandoffError::new(
            LocalRegistrationHandoffErrorCode::HandoffExpired,
        ));
    }
    Ok(LocalRegistrationHandoff { bootstrap })
}

pub struct LocalRegistrationHandoffStore<S> {
    store: S,
}

impl<S> LocalRegistrationHandoffStore<S>
where
    S: SecretStore,
{
    pub fn new(store: S) -> Self {
        Self { store }
    }

    pub fn load(
        &self,
        now_unix: i64,
    ) -> Result<Option<LocalRegistrationHandoff>, LocalRegistrationHandoffError> {
        let Some(raw) = self.store.load().map_err(map_store_error)? else {
            return Ok(None);
        };
        parse_local_registration_handoff(raw.as_slice(), now_unix).map(Some)
    }

    pub fn consume(&self) -> Result<(), LocalRegistrationHandoffError> {
        self.store.delete().map_err(map_store_error)
    }
}

fn map_store_error(_error: SecureStoreError) -> LocalRegistrationHandoffError {
    LocalRegistrationHandoffError::new(LocalRegistrationHandoffErrorCode::StorageUnavailable)
}

pub(crate) type ProductionLocalRegistrationHandoffStore =
    LocalRegistrationHandoffStore<AppDataSecretStore>;

pub(crate) fn initialize_local_registration_handoff_store(
    app_data_directory: &Path,
) -> Result<ProductionLocalRegistrationHandoffStore, LocalRegistrationHandoffError> {
    let store =
        AppDataSecretStore::new(app_data_directory, LOCAL_REGISTRATION_HANDOFF_FILE_NAME)
            .map_err(map_store_error)?;
    Ok(LocalRegistrationHandoffStore::new(store))
}

/// What one startup attempt at first registration did.
///
/// Only [`InstallationRegistrationOutcome::Conflict`] is actionable by the
/// user, so it is the only one the startup probe turns into a blocking
/// diagnostic. Everything else leaves the App exactly as unregistered as it
/// was before, which is the behaviour a machine without a local Control Plane
/// already has.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum InstallationRegistrationOutcome {
    /// A device credential was already stored; no grant was read.
    AlreadyRegistered,
    /// This attempt stored a device credential.
    Registered,
    /// No usable grant existed, so nothing was sent.
    NotAttempted,
    /// The attempt did not produce a stored credential. The grant is kept.
    Failed,
    /// The service already holds an Installation for this device public key.
    ///
    /// This is the residue of an accepted registration whose credential never
    /// reached the vault. No retry can clear it, because the conflict is the
    /// device public key itself; recovery has to replace the device identity.
    Conflict,
}

/// The one production registration call, behind a seam small enough to test.
pub trait InstallationRegistrar {
    fn has_credential(&self) -> Result<bool, ControlPlaneErrorCode>;

    fn register(
        &self,
        bootstrap: &DemoBootstrap,
    ) -> impl std::future::Future<Output = Result<(), ControlPlaneErrorCode>>;
}

pub async fn ensure_installation_registered<R, S>(
    registrar: &R,
    handoff: &LocalRegistrationHandoffStore<S>,
    now_unix: i64,
) -> InstallationRegistrationOutcome
where
    R: InstallationRegistrar,
    S: SecretStore,
{
    match registrar.has_credential() {
        Ok(true) => return InstallationRegistrationOutcome::AlreadyRegistered,
        Ok(false) => {}
        Err(_) => return InstallationRegistrationOutcome::Failed,
    }
    let Ok(Some(grant)) = handoff.load(now_unix) else {
        return InstallationRegistrationOutcome::NotAttempted;
    };
    match registrar.register(grant.bootstrap()).await {
        Ok(()) => {
            // Best effort: a credential that is already stored must not be
            // thrown away because the spent grant could not be removed.
            let _ = handoff.consume();
            InstallationRegistrationOutcome::Registered
        }
        Err(ControlPlaneErrorCode::InstallationConflict) => {
            InstallationRegistrationOutcome::Conflict
        }
        Err(_) => InstallationRegistrationOutcome::Failed,
    }
}

/// Seconds since the Unix epoch, for grant validity only.
pub(crate) fn current_unix_seconds() -> Result<i64, LocalRegistrationHandoffError> {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .ok()
        .and_then(|elapsed| i64::try_from(elapsed.as_secs()).ok())
        .ok_or_else(|| {
            LocalRegistrationHandoffError::new(
                LocalRegistrationHandoffErrorCode::StorageUnavailable,
            )
        })
}

#[cfg(test)]
mod tests {
    use std::cell::{Cell, RefCell};
    use std::fs;
    use std::path::{Path, PathBuf};

    use zeroize::Zeroizing;

    use super::{
        ensure_installation_registered, initialize_local_registration_handoff_store,
        parse_local_registration_handoff, InstallationRegistrar,
        InstallationRegistrationOutcome, LocalRegistrationHandoffErrorCode,
        LocalRegistrationHandoffStore, HANDOFF_DOCUMENT_VERSION, LOCAL_ENVIRONMENT_ID,
        LOCAL_REGISTRATION_HANDOFF_FILE_NAME, MAX_LOCAL_REGISTRATION_HANDOFF_BYTES,
    };
    use crate::control_plane::{ControlPlaneErrorCode, DemoBootstrap};
    use crate::secure_store::{SecretStore, SecureStoreError};

    const NOW: i64 = 1_785_000_000;
    const TOKEN: &str = "atb1.eyJlbnZpcm9ubWVudElkIjoibG9jYWwifQ.c2lnbmF0dXJlLXBsYWNlaG9sZGVy";

    #[derive(Default)]
    struct MemorySecretStore {
        value: RefCell<Option<Vec<u8>>>,
        fail_load: Cell<bool>,
        delete_count: Cell<usize>,
    }

    impl MemorySecretStore {
        fn with_document(document: &str) -> Self {
            Self {
                value: RefCell::new(Some(document.as_bytes().to_vec())),
                ..Self::default()
            }
        }
    }

    impl SecretStore for MemorySecretStore {
        fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, SecureStoreError> {
            if self.fail_load.get() {
                return Err(SecureStoreError::Unavailable);
            }
            Ok(self.value.borrow().clone().map(Zeroizing::new))
        }

        fn save(&self, secret: &[u8]) -> Result<(), SecureStoreError> {
            self.value.replace(Some(secret.to_vec()));
            Ok(())
        }

        fn delete(&self) -> Result<(), SecureStoreError> {
            self.delete_count.set(self.delete_count.get() + 1);
            self.value.replace(None);
            Ok(())
        }
    }

    fn document(expires_at: i64) -> String {
        format!(
            "{{\"environmentId\":\"{LOCAL_ENVIRONMENT_ID}\",\"expiresAt\":{expires_at},\
             \"token\":\"{TOKEN}\",\"version\":{HANDOFF_DOCUMENT_VERSION}}}"
        )
    }

    fn contract() -> serde_json::Value {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../contracts/protocol/local-registration-handoff-v1.json");
        serde_json::from_slice(&fs::read(path).expect("the frozen handoff contract must exist"))
            .expect("the frozen handoff contract must be JSON")
    }

    struct TemporaryAppData {
        path: PathBuf,
    }

    impl TemporaryAppData {
        fn new(name: &str) -> Self {
            let path = std::env::temp_dir().join(format!(
                "automation-tool-local-registration-{}-{name}",
                std::process::id()
            ));
            let _ = fs::remove_dir_all(&path);
            Self { path }
        }
    }

    impl Drop for TemporaryAppData {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    #[test]
    fn the_frozen_contract_governs_every_handoff_constant() {
        let contract = contract();

        assert_eq!(
            contract["fileName"].as_str(),
            Some(LOCAL_REGISTRATION_HANDOFF_FILE_NAME)
        );
        assert_eq!(contract["environmentId"].as_str(), Some(LOCAL_ENVIRONMENT_ID));
        assert_eq!(
            contract["documentVersion"].as_u64(),
            Some(u64::from(HANDOFF_DOCUMENT_VERSION))
        );
        assert_eq!(
            contract["maxFileBytes"].as_u64(),
            Some(MAX_LOCAL_REGISTRATION_HANDOFF_BYTES as u64)
        );
        let fields = contract["documentFields"]
            .as_array()
            .expect("the contract fixes the document fields")
            .iter()
            .map(|value| value.as_str().expect("field names are strings"))
            .collect::<Vec<_>>();
        assert_eq!(fields, ["environmentId", "expiresAt", "token", "version"]);
    }

    #[test]
    fn a_canonical_unexpired_grant_becomes_the_shared_bootstrap_type() {
        let handoff = parse_local_registration_handoff(document(NOW + 1).as_bytes(), NOW)
            .expect("a canonical grant is usable");

        // The bootstrap is only ever handed to the one production registration
        // call, so the reader exposes no way to read the token back out.
        let _: &crate::control_plane::DemoBootstrap = handoff.bootstrap();
        assert_eq!(format!("{handoff:?}"), "LocalRegistrationHandoff");
        assert!(!format!("{handoff:?}").contains(TOKEN));
    }

    /// A real document produced by `provision_local_registration_bootstrap`,
    /// recorded byte for byte. The two sides agree on a frozen contract but are
    /// written in different languages, so this is the only place that proves the
    /// issuer's actual output survives the reader's canonical-form check. Its
    /// grant expired long ago and its signing key was discarded at issue time.
    const ISSUED_BY_THE_LOCAL_SERVICE: &str = concat!(
        r#"{"environmentId":"local","expiresAt":1785010656,"token":"atb1."#,
        "eyJlbnZpcm9ubWVudElkIjoibG9jYWwiLCJleHBpcmVzQXQiOjE3ODUwMTA2NTYsIm5vdEJlZm9yZSI6",
        "MTc4NTAxMDA1NiwicHVycG9zZSI6Imluc3RhbGxhdGlvbi5yZWdpc3RlciIsInZlcnNpb24iOjF9.",
        "IhCx0QAvjMxNENBuK2KKPafcEFZw1at5aan3nSn20HZUnI-Miy4XPS39S1XSLMY-IOfl8_Z5bbmlyj",
        r#"0XnWUZCg","version":1}"#,
    );

    #[test]
    fn a_document_the_local_service_really_wrote_is_accepted_unchanged() {
        parse_local_registration_handoff(
            ISSUED_BY_THE_LOCAL_SERVICE.as_bytes(),
            1_785_010_655,
        )
        .expect("the issuer's own output must satisfy the reader's canonical form");

        assert_eq!(
            parse_local_registration_handoff(
                ISSUED_BY_THE_LOCAL_SERVICE.as_bytes(),
                1_785_010_656,
            )
            .expect_err("the recorded grant expires at its own expiresAt")
            .code(),
            LocalRegistrationHandoffErrorCode::HandoffExpired
        );
    }

    #[test]
    fn an_expired_or_not_yet_current_grant_is_refused_at_the_exact_boundary() {
        assert_eq!(
            parse_local_registration_handoff(document(NOW).as_bytes(), NOW)
                .expect_err("expiry is exclusive")
                .code(),
            LocalRegistrationHandoffErrorCode::HandoffExpired
        );
        assert_eq!(
            parse_local_registration_handoff(document(NOW - 1).as_bytes(), NOW)
                .expect_err("an old grant")
                .code(),
            LocalRegistrationHandoffErrorCode::HandoffExpired
        );
        assert!(parse_local_registration_handoff(document(NOW + 1).as_bytes(), NOW).is_ok());
    }

    #[test]
    fn every_malformed_or_unsafe_document_is_refused_without_reflecting_it() {
        let oversized = format!(
            "{{\"environmentId\":\"{LOCAL_ENVIRONMENT_ID}\",\"expiresAt\":{},\"token\":\"atb1.{}.{}\",\"version\":1}}",
            NOW + 1,
            "a".repeat(MAX_LOCAL_REGISTRATION_HANDOFF_BYTES),
            "b".repeat(16),
        );
        for invalid in [
            String::new(),
            "   ".to_owned(),
            "null".to_owned(),
            "[]".to_owned(),
            "{".to_owned(),
            oversized,
            // Pretty printing, key reordering and padding all break the frozen
            // canonical form, which is what makes duplicate keys detectable.
            format!(
                "{{\n  \"environmentId\": \"{LOCAL_ENVIRONMENT_ID}\",\n  \"expiresAt\": {},\n  \"token\": \"{TOKEN}\",\n  \"version\": 1\n}}",
                NOW + 1
            ),
            format!(
                "{{\"version\":1,\"environmentId\":\"{LOCAL_ENVIRONMENT_ID}\",\"expiresAt\":{},\"token\":\"{TOKEN}\"}}",
                NOW + 1
            ),
            format!("{} ", document(NOW + 1)),
            // Duplicate key: a permissive parser would keep the last value and
            // register with a grant the issuer never signed.
            format!(
                "{{\"environmentId\":\"{LOCAL_ENVIRONMENT_ID}\",\"environmentId\":\"{LOCAL_ENVIRONMENT_ID}\",\"expiresAt\":{},\"token\":\"{TOKEN}\",\"version\":1}}",
                NOW + 1
            ),
            format!(
                "{{\"environmentId\":\"{LOCAL_ENVIRONMENT_ID}\",\"expiresAt\":{},\"extra\":1,\"token\":\"{TOKEN}\",\"version\":1}}",
                NOW + 1
            ),
            format!("{{\"expiresAt\":{},\"token\":\"{TOKEN}\",\"version\":1}}", NOW + 1),
            format!(
                "{{\"environmentId\":\"{LOCAL_ENVIRONMENT_ID}\",\"expiresAt\":{},\"token\":\"{TOKEN}\",\"version\":2}}",
                NOW + 1
            ),
            format!(
                "{{\"environmentId\":\"demo-cn-1\",\"expiresAt\":{},\"token\":\"{TOKEN}\",\"version\":1}}",
                NOW + 1
            ),
            format!(
                "{{\"environmentId\":\"{LOCAL_ENVIRONMENT_ID}\",\"expiresAt\":{},\"token\":\"atb2.aaaa.bbbb\",\"version\":1}}",
                NOW + 1
            ),
            format!(
                "{{\"environmentId\":\"{LOCAL_ENVIRONMENT_ID}\",\"expiresAt\":{},\"token\":\"atb1.aaaa\",\"version\":1}}",
                NOW + 1
            ),
            format!(
                "{{\"environmentId\":\"{LOCAL_ENVIRONMENT_ID}\",\"expiresAt\":{},\"token\":\"atb1..bbbb\",\"version\":1}}",
                NOW + 1
            ),
            format!(
                "{{\"environmentId\":\"{LOCAL_ENVIRONMENT_ID}\",\"expiresAt\":\"{}\",\"token\":\"{TOKEN}\",\"version\":1}}",
                NOW + 1
            ),
        ] {
            let failure = parse_local_registration_handoff(invalid.as_bytes(), NOW)
                .expect_err("an unsafe handoff document");
            assert_eq!(
                failure.code(),
                LocalRegistrationHandoffErrorCode::HandoffInvalid,
                "document was accepted: {invalid}"
            );
            assert_eq!(failure.to_string(), "local registration handoff is unusable");
        }

        let non_utf8 = [0x7b, 0xff, 0x7d];
        assert_eq!(
            parse_local_registration_handoff(&non_utf8, NOW)
                .expect_err("non UTF-8 bytes")
                .code(),
            LocalRegistrationHandoffErrorCode::HandoffInvalid
        );
    }

    #[test]
    fn an_absent_document_is_not_a_failure_and_an_unreadable_one_is() {
        let empty = LocalRegistrationHandoffStore::new(MemorySecretStore::default());
        assert!(empty.load(NOW).expect("no handoff is normal").is_none());

        let broken = MemorySecretStore::with_document(&document(NOW + 1));
        broken.fail_load.set(true);
        let store = LocalRegistrationHandoffStore::new(broken);
        assert_eq!(
            store
                .load(NOW)
                .expect_err("an unreadable private file")
                .code(),
            LocalRegistrationHandoffErrorCode::StorageUnavailable
        );
    }

    #[test]
    fn consuming_the_grant_removes_it_so_a_restart_cannot_replay_it() {
        let store = LocalRegistrationHandoffStore::new(MemorySecretStore::with_document(
            &document(NOW + 1),
        ));

        assert!(store.load(NOW).expect("a usable handoff").is_some());
        store.consume().expect("removal");

        assert!(store.load(NOW).expect("removed handoff").is_none());
    }

    #[test]
    fn the_production_store_reads_only_the_frozen_private_file_name() {
        let app_data = TemporaryAppData::new("frozen-name");
        let store = initialize_local_registration_handoff_store(&app_data.path)
            .expect("an initialised handoff store");
        assert!(store.load(NOW).expect("an empty app data directory").is_none());

        fs::write(
            app_data.path.join(LOCAL_REGISTRATION_HANDOFF_FILE_NAME),
            document(NOW + 1),
        )
        .expect("the local service writes the grant");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(
                app_data.path.join(LOCAL_REGISTRATION_HANDOFF_FILE_NAME),
                fs::Permissions::from_mode(0o600),
            )
            .expect("private mode");
        }

        assert!(store.load(NOW).expect("the written grant").is_some());
        store.consume().expect("removal");
        assert!(!app_data
            .path
            .join(LOCAL_REGISTRATION_HANDOFF_FILE_NAME)
            .exists());
    }

    struct FakeRegistrar {
        registered: Cell<bool>,
        credential_readable: Cell<bool>,
        failure: Cell<Option<ControlPlaneErrorCode>>,
        attempts: Cell<usize>,
    }

    impl FakeRegistrar {
        fn unregistered() -> Self {
            Self {
                registered: Cell::new(false),
                credential_readable: Cell::new(true),
                failure: Cell::new(None),
                attempts: Cell::new(0),
            }
        }

        fn failing(code: ControlPlaneErrorCode) -> Self {
            let registrar = Self::unregistered();
            registrar.failure.set(Some(code));
            registrar
        }
    }

    impl InstallationRegistrar for FakeRegistrar {
        fn has_credential(&self) -> Result<bool, ControlPlaneErrorCode> {
            if self.credential_readable.get() {
                Ok(self.registered.get())
            } else {
                Err(ControlPlaneErrorCode::StorageUnavailable)
            }
        }

        async fn register(&self, _bootstrap: &DemoBootstrap) -> Result<(), ControlPlaneErrorCode> {
            self.attempts.set(self.attempts.get() + 1);
            match self.failure.get() {
                None => {
                    self.registered.set(true);
                    Ok(())
                }
                Some(code) => Err(code),
            }
        }
    }

    fn attempt(
        registrar: &FakeRegistrar,
        store: &LocalRegistrationHandoffStore<MemorySecretStore>,
    ) -> InstallationRegistrationOutcome {
        tauri::async_runtime::block_on(ensure_installation_registered(registrar, store, NOW))
    }

    fn grant_store(expires_at: i64) -> LocalRegistrationHandoffStore<MemorySecretStore> {
        LocalRegistrationHandoffStore::new(MemorySecretStore::with_document(&document(expires_at)))
    }

    #[test]
    fn a_registered_app_never_reads_the_grant_file_at_all() {
        let registrar = FakeRegistrar::unregistered();
        registrar.registered.set(true);
        let store = grant_store(NOW + 1);
        store.store.fail_load.set(true);

        assert_eq!(
            attempt(&registrar, &store),
            InstallationRegistrationOutcome::AlreadyRegistered
        );
        assert_eq!(registrar.attempts.get(), 0);
    }

    #[test]
    fn a_successful_registration_consumes_the_grant_so_it_cannot_be_replayed() {
        let registrar = FakeRegistrar::unregistered();
        let store = grant_store(NOW + 1);

        assert_eq!(
            attempt(&registrar, &store),
            InstallationRegistrationOutcome::Registered
        );
        assert_eq!(registrar.attempts.get(), 1);
        assert_eq!(store.store.delete_count.get(), 1);
        assert!(store.load(NOW).expect("consumed grant").is_none());
    }

    #[test]
    fn an_absent_or_unusable_grant_is_never_attempted_and_never_deleted() {
        for store in [
            LocalRegistrationHandoffStore::new(MemorySecretStore::default()),
            grant_store(NOW),
            LocalRegistrationHandoffStore::new(MemorySecretStore::with_document("{}")),
        ] {
            let registrar = FakeRegistrar::unregistered();

            assert_eq!(
                attempt(&registrar, &store),
                InstallationRegistrationOutcome::NotAttempted
            );
            assert_eq!(registrar.attempts.get(), 0);
            assert_eq!(store.store.delete_count.get(), 0);
        }
    }

    #[test]
    fn an_unreadable_credential_vault_stops_before_touching_the_grant() {
        let registrar = FakeRegistrar::unregistered();
        registrar.credential_readable.set(false);
        let store = grant_store(NOW + 1);

        assert_eq!(
            attempt(&registrar, &store),
            InstallationRegistrationOutcome::Failed
        );
        assert_eq!(registrar.attempts.get(), 0);
        assert_eq!(store.store.delete_count.get(), 0);
    }

    #[test]
    fn a_refused_or_unreachable_service_keeps_the_grant_for_the_next_start() {
        for code in [
            ControlPlaneErrorCode::TransportUnavailable,
            ControlPlaneErrorCode::RequestRejected,
            // The credential reached this machine but never reached the vault.
            // Deleting the grant here is what would make the next attempt
            // unrecoverable, so it must stay.
            ControlPlaneErrorCode::OutcomeUncertain,
        ] {
            let registrar = FakeRegistrar::failing(code);
            let store = grant_store(NOW + 1);

            assert_eq!(
                attempt(&registrar, &store),
                InstallationRegistrationOutcome::Failed,
                "unexpected outcome for {code:?}"
            );
            assert_eq!(registrar.attempts.get(), 1);
            assert_eq!(store.store.delete_count.get(), 0);
            assert!(store.load(NOW).expect("retained grant").is_some());
        }
    }

    #[test]
    fn a_service_that_already_owns_this_device_key_is_reported_as_a_conflict() {
        let registrar = FakeRegistrar::failing(ControlPlaneErrorCode::InstallationConflict);
        let store = grant_store(NOW + 1);

        assert_eq!(
            attempt(&registrar, &store),
            InstallationRegistrationOutcome::Conflict
        );
        // Recovery replaces the device identity, not the grant, so the still
        // valid grant has to survive for the retry to have anything to use.
        assert_eq!(store.store.delete_count.get(), 0);
        assert!(store.load(NOW).expect("retained grant").is_some());
    }

    #[cfg(unix)]
    #[test]
    fn a_grant_readable_by_other_users_is_repaired_before_loading() {
        use std::os::unix::fs::PermissionsExt;

        let app_data = TemporaryAppData::new("loose-mode");
        let store = initialize_local_registration_handoff_store(&app_data.path)
            .expect("an initialised handoff store");
        let path = app_data.path.join(LOCAL_REGISTRATION_HANDOFF_FILE_NAME);
        fs::write(&path, document(NOW + 1)).expect("a grant");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).expect("loose mode");

        assert!(
            store
                .load(NOW)
                .expect("repair a migrated grant")
                .is_some()
        );
        assert_eq!(
            fs::metadata(&path)
                .expect("repaired grant metadata")
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
}

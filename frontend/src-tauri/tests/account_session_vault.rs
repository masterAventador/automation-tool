use std::cell::RefCell;

use automation_tool_desktop_lib::account_session_vault::{
    AccountSessionSecrets, AccountSessionSnapshot, AccountSessionVault,
};
use automation_tool_desktop_lib::secure_store::{SecretStore, SecureStoreError};
use zeroize::Zeroizing;

const ACCESS: &str =
    "atas1.123e4567-e89b-42d3-a456-426614174001.BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc";
const REFRESH: &str =
    "atrs1.123e4567-e89b-42d3-a456-426614174002.CAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAg";

#[derive(Default)]
struct MemoryStore {
    bytes: RefCell<Option<Vec<u8>>>,
}

impl SecretStore for MemoryStore {
    fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, SecureStoreError> {
        Ok(self.bytes.borrow().clone().map(Zeroizing::new))
    }

    fn save(&self, secret: &[u8]) -> Result<(), SecureStoreError> {
        self.bytes.replace(Some(secret.to_vec()));
        Ok(())
    }

    fn delete(&self) -> Result<(), SecureStoreError> {
        self.bytes.replace(None);
        Ok(())
    }
}

#[test]
fn private_account_session_round_trips_as_one_validated_secret_record() {
    let vault = AccountSessionVault::new(MemoryStore::default());
    let session = AccountSessionSecrets::new(
        ACCESS.to_owned(),
        REFRESH.to_owned(),
        "2026-07-23T10:10:00Z".to_owned(),
        "2026-08-22T10:00:00Z".to_owned(),
        "123e4567-e89b-42d3-a456-426614174000".to_owned(),
        "demo.operator".to_owned(),
        "active".to_owned(),
    )
    .expect("canonical account Session");

    vault.replace(&session).expect("persist account Session");
    let restored = vault
        .load()
        .expect("load account Session")
        .expect("stored Session");

    assert_eq!(restored.account().login_name(), "demo.operator");
    assert_eq!(restored.access_token(), ACCESS);
    assert_eq!(restored.refresh_token(), REFRESH);
    assert!(!format!("{restored:?}").contains("atas1"));
    assert!(!format!("{restored:?}").contains("atrs1"));
}

#[test]
fn corrupt_or_noncanonical_account_session_fails_closed() {
    let store = MemoryStore::default();
    store.bytes.replace(Some(
        br#"{"schemaVersion":1,"accessToken":"atas1.private","refreshToken":"atrs1.private"}"#
            .to_vec(),
    ));
    let vault = AccountSessionVault::new(store);

    assert!(vault.load().is_err());
}

#[test]
fn deleting_account_session_is_idempotent() {
    let vault = AccountSessionVault::new(MemoryStore::default());
    vault.delete().expect("first delete");
    vault.delete().expect("second delete");
    assert!(vault.load().expect("load after delete").is_none());
}

#[test]
fn webview_snapshot_contains_only_the_safe_account_projection() {
    let session = AccountSessionSecrets::new(
        ACCESS.to_owned(),
        REFRESH.to_owned(),
        "2026-07-23T10:10:00Z".to_owned(),
        "2026-08-22T10:00:00Z".to_owned(),
        "123e4567-e89b-42d3-a456-426614174000".to_owned(),
        "demo.operator".to_owned(),
        "active".to_owned(),
    )
    .expect("canonical account Session");

    let authenticated = serde_json::to_value(AccountSessionSnapshot::authenticated(&session))
        .expect("serialize safe account snapshot");
    assert_eq!(
        authenticated,
        serde_json::json!({
            "state": "authenticated",
            "account": {
                "userId": "123e4567-e89b-42d3-a456-426614174000",
                "loginName": "demo.operator",
                "status": "active"
            }
        })
    );
    let encoded = authenticated.to_string();
    assert!(!encoded.contains("atas1"));
    assert!(!encoded.contains("atrs1"));
    assert_eq!(
        serde_json::to_value(AccountSessionSnapshot::unauthenticated())
            .expect("serialize unauthenticated snapshot"),
        serde_json::json!({"state": "unauthenticated", "account": null})
    );
}

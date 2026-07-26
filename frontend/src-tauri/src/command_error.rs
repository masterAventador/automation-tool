//! The one shape every Tauri command error takes when it crosses into JavaScript.
//!
//! A command that returns `Err(E)` reaches the webview as the JSON serialization
//! of `E`, and `invoke()` rejects its promise with that value. JavaScript then
//! reads it the way it reads every rejection: `error.message` first, `String(error)`
//! second. A plain JSON object has neither — `String({code: "x"})` is
//! `"[object Object]"` — so a command error made only of structured fields arrives
//! with its code erased at exactly the moment someone needs it.
//!
//! That is not a test-only problem. It is what the browser console shows, what an
//! uncaught rejection reports, and what the desktop E2E runner prints: the runner's
//! in-page wrapper is literally `(e && e.message) || String(e)`, so four acceptance
//! drivers failed for weeks with `Error: [object Object]` and no way to tell which
//! of nine `ControlPlaneCommandError` codes they had hit.
//!
//! Making `String(error)` readable is impossible without changing what is rejected:
//! only a JSON *string* stringifies to anything useful, and turning the error into a
//! string would delete `code` and `retryable`, which every gateway in
//! `frontend/src/platform/tauri/` branches on. So the wire keeps its structured
//! fields and gains the property JavaScript actually looks for first — a `message`.
//!
//! `message` is derived from `code` and from nothing else. It carries no path, no
//! credential, no platform payload and no operating-system detail: it repeats a value
//! that is already on the wire, so it widens no disclosure surface. Every command
//! error code in this crate is a compile-time constant or a `#[serde]`-renamed enum
//! variant — a closed set — and `serialize` derives the text from the same
//! serialization that produces `code`, so the two can never disagree.

use serde::ser::SerializeMap;
use serde::{Serialize, Serializer};

/// Marks the text as this application's own command failure rather than a runtime
/// or transport error, and keeps it greppable in a runner log.
const MESSAGE_PREFIX: &str = "native command error: ";

/// The readable text a command error presents to JavaScript and to Rust logs.
///
/// Deliberately nothing but the prefix and the code: a caller that wants to append
/// a detail has to change this function, and reviewing that change is the point.
pub fn message(code: &str) -> String {
    format!("{MESSAGE_PREFIX}{code}")
}

/// Serialize a command error as `{code, message, retryable?}`.
///
/// `retryable` is `None` for the commands whose error never had one; the wire must
/// not grow a field the command does not answer for.
pub fn serialize<S, C>(code: &C, retryable: Option<bool>, serializer: S) -> Result<S::Ok, S::Error>
where
    S: Serializer,
    C: Serialize + ?Sized,
{
    let code = code_text(code).map_err(serde::ser::Error::custom)?;
    let mut wire = serializer.serialize_map(Some(if retryable.is_some() { 3 } else { 2 }))?;
    wire.serialize_entry("code", &code)?;
    wire.serialize_entry("message", &message(&code))?;
    if let Some(retryable) = retryable {
        wire.serialize_entry("retryable", &retryable)?;
    }
    wire.end()
}

/// The code exactly as it will appear on the wire.
///
/// Taking it from the code's own serialization — rather than a hand-written
/// `as_str()` per enum — is what stops `message` and `code` from drifting apart
/// when a `#[serde(rename)]` changes.
fn code_text<C>(code: &C) -> Result<String, &'static str>
where
    C: Serialize + ?Sized,
{
    match serde_json::to_value(code) {
        Ok(serde_json::Value::String(text)) => Ok(text),
        _ => Err("a command error code must serialize to a JSON string"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Serialize)]
    #[serde(rename_all = "snake_case")]
    enum RenamedCode {
        StorageUnavailable,
    }

    #[test]
    fn the_message_is_the_prefix_and_the_code_and_nothing_else() {
        assert_eq!(
            message("installation_access_denied"),
            "native command error: installation_access_denied"
        );
    }

    #[test]
    fn a_retryable_command_error_keeps_every_structured_field() {
        let wire = serde_json::to_value(Wire {
            code: "transport_unavailable",
            retryable: Some(true),
        })
        .expect("the shared serializer must produce JSON");

        assert_eq!(
            wire,
            serde_json::json!({
                "code": "transport_unavailable",
                "message": "native command error: transport_unavailable",
                "retryable": true,
            })
        );
    }

    #[test]
    fn a_command_error_without_a_retryable_answer_does_not_gain_one() {
        let wire = serde_json::to_value(Wire {
            code: "decision_unavailable",
            retryable: None,
        })
        .expect("the shared serializer must produce JSON");

        assert_eq!(
            wire,
            serde_json::json!({
                "code": "decision_unavailable",
                "message": "native command error: decision_unavailable",
            })
        );
    }

    /// The message must follow a `#[serde(rename)]`, never a Rust identifier;
    /// a hand-written `as_str()` per enum is exactly the drift this avoids.
    #[test]
    fn the_message_follows_the_serialized_code_not_the_rust_variant_name() {
        let wire = serde_json::to_value(RenamedWire {
            code: RenamedCode::StorageUnavailable,
        })
        .expect("the shared serializer must produce JSON");

        assert_eq!(
            wire,
            serde_json::json!({
                "code": "storage_unavailable",
                "message": "native command error: storage_unavailable",
            })
        );
    }

    /// A code that is not a JSON string is a programming error, and it must surface
    /// as a serialization failure rather than a panic inside the IPC boundary.
    #[test]
    fn a_code_that_is_not_a_string_is_refused_instead_of_panicking() {
        assert!(serde_json::to_value(NumericWire { code: 7 }).is_err());
    }

    struct Wire {
        code: &'static str,
        retryable: Option<bool>,
    }

    impl Serialize for Wire {
        fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
            super::serialize(&self.code, self.retryable, serializer)
        }
    }

    struct RenamedWire {
        code: RenamedCode,
    }

    impl Serialize for RenamedWire {
        fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
            super::serialize(&self.code, None, serializer)
        }
    }

    struct NumericWire {
        code: u8,
    }

    impl Serialize for NumericWire {
        fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
            super::serialize(&self.code, None, serializer)
        }
    }
}

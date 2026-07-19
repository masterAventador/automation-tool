use automation_tool_desktop_lib::executor_bootstrap::{
    ExecutorBootstrapInput, LocalExecutorEvent, LocalSessionToken,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use hmac::{Hmac, KeyInit, Mac};
use serde_json::Value;
use sha2::Sha256;
use std::path::Path;

const CONTROL_PLANE_SESSION: &str = "atds1.private-control-plane-session";
const AUTHENTICATION_DOMAIN: &[u8] = b"automation-tool.local-executor-event.v1\0";

fn input() -> ExecutorBootstrapInput<'static> {
    ExecutorBootstrapInput::new(
        "ws://127.0.0.1:8765/api/v1/executors/connect",
        CONTROL_PLANE_SESSION,
        "123e4567-e89b-42d3-a456-426614174003",
        "123e4567-e89b-42d3-a456-426614174004",
        Path::new("/private/tmp/automation-tool-executor-bootstrap-test"),
        1,
    )
    .expect("valid bootstrap input")
}

fn encoded_token(document: &Value) -> &str {
    document["local_session_token"]
        .as_str()
        .expect("local session token")
}

fn decode_hex(source: &str) -> Vec<u8> {
    source
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let value = std::str::from_utf8(pair).expect("ASCII hex");
            u8::from_str_radix(value, 16).expect("valid hex")
        })
        .collect()
}

fn proof_for(token: &str, event: &str) -> String {
    let mut authenticator =
        Hmac::<Sha256>::new_from_slice(&decode_hex(token)).expect("32-byte HMAC key");
    authenticator.update(AUTHENTICATION_DOMAIN);
    authenticator.update(event.as_bytes());
    authenticator.update(b"\0");
    authenticator.update(b"1.0");
    format!(
        "atlep1.{}",
        URL_SAFE_NO_PAD.encode(authenticator.finalize().into_bytes())
    )
}

#[test]
fn bootstrap_writes_a_fresh_256_bit_token_only_to_the_stdin_document() {
    let first = LocalSessionToken::generate().expect("random local session");
    let second = LocalSessionToken::generate().expect("second random local session");
    let mut first_stdin = Vec::new();
    let mut second_stdin = Vec::new();

    first
        .write_bootstrap(&mut first_stdin, &input())
        .expect("first stdin bootstrap");
    second
        .write_bootstrap(&mut second_stdin, &input())
        .expect("second stdin bootstrap");

    assert!(first_stdin.ends_with(b"\n"));
    assert_eq!(
        first_stdin.iter().filter(|value| **value == b'\n').count(),
        1
    );
    let first_document: Value = serde_json::from_slice(&first_stdin).expect("bootstrap JSON");
    let second_document: Value = serde_json::from_slice(&second_stdin).expect("bootstrap JSON");
    let first_token = encoded_token(&first_document);
    let second_token = encoded_token(&second_document);
    assert_eq!(first_token.len(), 64);
    assert!(first_token.bytes().all(|value| value.is_ascii_hexdigit()));
    assert!(first_token.bytes().all(|value| !value.is_ascii_uppercase()));
    assert_ne!(first_token, second_token);
    assert_eq!(first_document["session_token"], CONTROL_PLANE_SESSION);
    assert_ne!(first_token, CONTROL_PLANE_SESSION);
    assert_eq!(
        first_document["state_directory"],
        "/private/tmp/automation-tool-executor-bootstrap-test"
    );
    assert!(!format!("{first:?}").contains(first_token));
    assert!(!format!("{first:?}").contains(CONTROL_PLANE_SESSION));
}

#[test]
fn event_proof_is_verified_in_constant_time_without_reflecting_the_token() {
    let token = LocalSessionToken::generate().expect("random local session");
    let mut stdin = Vec::new();
    token
        .write_bootstrap(&mut stdin, &input())
        .expect("stdin bootstrap");
    let document: Value = serde_json::from_slice(&stdin).expect("bootstrap JSON");
    let local_session_token = encoded_token(&document);
    let healthy_proof = proof_for(local_session_token, "executor.healthy");

    token
        .verify_event_proof(LocalExecutorEvent::Healthy, &healthy_proof)
        .expect("matching proof");
    let rejected = token
        .verify_event_proof(LocalExecutorEvent::Stopped, &healthy_proof)
        .expect_err("event-bound proof must not replay");
    assert_eq!(
        rejected.to_string(),
        "Local Executor authentication is rejected"
    );
    assert!(!format!("{rejected:?}").contains(local_session_token));
    assert!(!healthy_proof.contains(local_session_token));
}

#[test]
fn bootstrap_rejects_invalid_inputs_and_failed_writes_without_secret_reflection() {
    assert!(ExecutorBootstrapInput::new(
        "ws://127.0.0.1:8765/api/v1/executors/connect",
        CONTROL_PLANE_SESSION,
        "not-an-installation",
        "123e4567-e89b-42d3-a456-426614174004",
        Path::new("/private/tmp/automation-tool-executor-bootstrap-test"),
        1,
    )
    .is_err());
    for state_directory in [
        Path::new("relative-state"),
        Path::new("/"),
        Path::new("/private/tmp/\u{202e}state"),
    ] {
        assert!(ExecutorBootstrapInput::new(
            "ws://127.0.0.1:8765/api/v1/executors/connect",
            CONTROL_PLANE_SESSION,
            "123e4567-e89b-42d3-a456-426614174003",
            "123e4567-e89b-42d3-a456-426614174004",
            state_directory,
            1,
        )
        .is_err());
    }

    struct FailingWriter;
    impl std::io::Write for FailingWriter {
        fn write(&mut self, _buffer: &[u8]) -> std::io::Result<usize> {
            Err(std::io::Error::other("private writer failure"))
        }

        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    let token = LocalSessionToken::generate().expect("random local session");
    let error = token
        .write_bootstrap(&mut FailingWriter, &input())
        .expect_err("failed stdin must fail closed");
    assert_eq!(error.to_string(), "Local Executor bootstrap is rejected");
    assert!(!format!("{error:?}").contains("private"));
    assert!(!format!("{error:?}").contains(CONTROL_PLANE_SESSION));
}

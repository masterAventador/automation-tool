//! PB-07: the publish command frames the App sends to the packaged executor.
//!
//! These frames cross a process boundary into Python, so agreeing with
//! ourselves is not enough. Every proof asserted here is recomputed
//! independently from the domain and field order that
//! `automation_tool.executor.authentication` uses; if either side changes its
//! binding the other stops being able to speak to it, and that must show up
//! here rather than as a rejected command on a user's machine.

use automation_tool_desktop_lib::executor_bootstrap::{
    ExecutorBootstrapInput, LocalPlatformCommand, LocalSessionToken,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use hmac::{Hmac, KeyInit, Mac};
use serde_json::Value;
use sha2::Sha256;
use std::path::Path;

const CONTROL_PLANE_SESSION: &str = "atds1.private-control-plane-session";
const PUBLISH_DOMAIN: &[u8] = b"automation-tool.local-executor-publish-command.v1\0";
const DISPATCH_DOMAIN: &[u8] = b"automation-tool.local-executor-publish-dispatch.v1\0";
const PROTOCOL_VERSION: &str = "1.0";
const COMMAND_ID: &str = "123e4567-e89b-42d3-a456-426614174005";
const PUBLISH_JOB_ID: &str = "123e4567-e89b-42d3-a456-426614174006";
const CONFIRMATION_ID: &str = "123e4567-e89b-42d3-a456-426614174007";
const TITLE: &str = "自动化运营工具发布验收标题";
const DESCRIPTION: &str = "自动化运营工具发布验收简介";
#[cfg(not(windows))]
const STATE_DIRECTORY: &str = "/private/tmp/automation-tool-publish-command-test";
#[cfg(windows)]
const STATE_DIRECTORY: &str = r"C:\private\tmp\automation-tool-publish-command-test";
#[cfg(not(windows))]
const EXECUTABLE: &str = "/opt/automation-tool/chromium";
#[cfg(not(windows))]
const PROFILE: &str = "/opt/automation-tool/profile";
#[cfg(not(windows))]
const ARTIFACT: &str = "/opt/automation-tool/clip.mp4";
#[cfg(windows)]
const EXECUTABLE: &str = r"C:\automation-tool\chromium.exe";
#[cfg(windows)]
const PROFILE: &str = r"C:\automation-tool\profile";
#[cfg(windows)]
const ARTIFACT: &str = r"C:\automation-tool\clip.mp4";

/// A token plus its plaintext, read back the only way it is ever exposed:
/// out of the bootstrap document the executor is handed on stdin.
fn token() -> (LocalSessionToken, String) {
    let input = ExecutorBootstrapInput::new(
        "ws://127.0.0.1:8765/api/v1/executors/connect",
        CONTROL_PLANE_SESSION,
        "123e4567-e89b-42d3-a456-426614174003",
        "123e4567-e89b-42d3-a456-426614174004",
        Path::new(STATE_DIRECTORY),
        1,
    )
    .expect("valid bootstrap input");
    let token = LocalSessionToken::generate().expect("random local session");
    let mut bootstrap = Vec::new();
    token
        .write_bootstrap(&mut bootstrap, &input)
        .expect("a bootstrap document");
    let document: Value = serde_json::from_slice(&bootstrap).expect("bootstrap JSON");
    let secret = document["local_session_token"]
        .as_str()
        .expect("local session token")
        .to_owned();
    (token, secret)
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

/// Recompute a proof the way the Python authenticator does: domain, then the
/// fields joined by a NUL, in exactly this order.
fn expected_proof(secret: &str, domain: &[u8], parts: &[&str]) -> String {
    let mut authenticator =
        Hmac::<Sha256>::new_from_slice(&decode_hex(secret)).expect("32-byte HMAC key");
    authenticator.update(domain);
    for (index, part) in parts.iter().enumerate() {
        if index > 0 {
            authenticator.update(b"\0");
        }
        authenticator.update(part.as_bytes());
    }
    format!(
        "atlcp1.{}",
        URL_SAFE_NO_PAD.encode(authenticator.finalize().into_bytes())
    )
}

fn written(write: impl FnOnce(&mut Vec<u8>, &LocalSessionToken)) -> (Value, String) {
    let (token, secret) = token();
    let mut frame = Vec::new();
    write(&mut frame, &token);
    let text = String::from_utf8(frame).expect("UTF-8 frame");
    assert!(text.ends_with('\n'), "a command frame is one line");
    (
        serde_json::from_str(text.trim_end()).expect("a JSON command frame"),
        secret,
    )
}

#[test]
fn the_preflight_frame_binds_the_browser_identity_the_artifact_and_the_copy() {
    let (document, secret) = written(|frame, token| {
        token
            .write_publish_command(
                frame,
                COMMAND_ID,
                PUBLISH_JOB_ID,
                Path::new(EXECUTABLE),
                Path::new(PROFILE),
                true,
                Path::new(ARTIFACT),
                TITLE,
                DESCRIPTION,
            )
            .expect("a writable preflight frame");
    });

    assert_eq!(document["commandType"], "douyin.publish.preflight");
    assert_eq!(document["publishJobId"], PUBLISH_JOB_ID);
    assert_eq!(document["artifactPath"], ARTIFACT);
    assert_eq!(document["title"], TITLE);
    assert_eq!(document["description"], DESCRIPTION);
    assert_eq!(document["protocolVersion"], PROTOCOL_VERSION);
    assert_eq!(
        document["authenticationProof"],
        expected_proof(
            &secret,
            PUBLISH_DOMAIN,
            &[
                COMMAND_ID,
                "douyin.publish.preflight",
                EXECUTABLE,
                PROFILE,
                "1",
                PUBLISH_JOB_ID,
                ARTIFACT,
                TITLE,
                DESCRIPTION,
                PROTOCOL_VERSION,
            ],
        )
    );
}

#[test]
fn the_dispatch_frame_binds_only_the_job_and_the_approval() {
    let (document, secret) = written(|frame, token| {
        token
            .write_publish_dispatch_command(frame, COMMAND_ID, PUBLISH_JOB_ID, CONFIRMATION_ID)
            .expect("a writable dispatch frame");
    });

    assert_eq!(document["commandType"], "douyin.publish.dispatch");
    assert_eq!(document["publishJobId"], PUBLISH_JOB_ID);
    assert_eq!(document["confirmationId"], CONFIRMATION_ID);
    // A dispatch acts on the pre-submit state the executor already holds; if it
    // restated the content it could contradict what was filled and approved.
    assert!(document.get("artifactPath").is_none());
    assert!(document.get("title").is_none());
    assert!(document.get("description").is_none());
    assert!(document.get("executablePath").is_none());
    assert_eq!(
        document["authenticationProof"],
        expected_proof(
            &secret,
            DISPATCH_DOMAIN,
            &[
                COMMAND_ID,
                "douyin.publish.dispatch",
                PUBLISH_JOB_ID,
                CONFIRMATION_ID,
                PROTOCOL_VERSION,
            ],
        )
    );
}

#[test]
fn a_publish_frame_with_an_unusable_identity_is_refused_before_it_is_written() {
    let (token, _secret) = token();
    let mut frame = Vec::new();

    for (command_id, job_id) in [
        ("not-a-uuid", PUBLISH_JOB_ID),
        (COMMAND_ID, "not-a-uuid"),
        ("", PUBLISH_JOB_ID),
        (COMMAND_ID, ""),
    ] {
        assert!(token
            .write_publish_command(
                &mut frame,
                command_id,
                job_id,
                Path::new(EXECUTABLE),
                Path::new(PROFILE),
                true,
                Path::new(ARTIFACT),
                TITLE,
                DESCRIPTION,
            )
            .is_err());
        assert!(token
            .write_publish_dispatch_command(&mut frame, command_id, job_id, CONFIRMATION_ID)
            .is_err());
    }
    assert!(frame.is_empty(), "a refused frame is never partially written");
}

#[test]
fn publish_copy_that_could_corrupt_the_frame_is_refused() {
    let (token, _secret) = token();
    let mut frame = Vec::new();

    for (title, description) in [
        ("", DESCRIPTION),
        (TITLE, ""),
        ("标题\u{0000}", DESCRIPTION),
        (TITLE, "简介\u{202e}"),
    ] {
        assert!(token
            .write_publish_command(
                &mut frame,
                COMMAND_ID,
                PUBLISH_JOB_ID,
                Path::new(EXECUTABLE),
                Path::new(PROFILE),
                true,
                Path::new(ARTIFACT),
                title,
                description,
            )
            .is_err());
    }
    assert!(frame.is_empty());
}

#[test]
fn a_preflight_result_is_accepted_only_under_its_own_flow_contract() {
    let (token, secret) = token();

    for state in [
        "publish_pre_submit_ready",
        "publish_handoff_required",
        "publish_blocked",
    ] {
        let line = result_line(&secret, COMMAND_ID, state, "douyin.publish-preflight.v1");
        let parsed = token
            .parse_platform_command_result(
                COMMAND_ID,
                LocalPlatformCommand::PreflightDouyinPublish,
                &line,
            )
            .expect("a preflight result");
        assert_eq!(parsed.state(), state);
    }

    // The dispatch flow contract must not be accepted for a preflight command.
    let line = result_line(
        &secret,
        COMMAND_ID,
        "publish_pre_submit_ready",
        "douyin.publish-release.v1",
    );
    assert!(token
        .parse_platform_command_result(
            COMMAND_ID,
            LocalPlatformCommand::PreflightDouyinPublish,
            &line
        )
        .is_err());
}

#[test]
fn a_dispatch_result_is_accepted_only_under_its_own_flow_contract() {
    let (token, secret) = token();

    for state in [
        "publish_verified",
        "publish_outcome_uncertain",
        "publish_not_dispatched",
    ] {
        let line = result_line(&secret, COMMAND_ID, state, "douyin.publish-release.v1");
        let parsed = token
            .parse_platform_command_result(
                COMMAND_ID,
                LocalPlatformCommand::DispatchDouyinPublish,
                &line,
            )
            .expect("a dispatch result");
        assert_eq!(parsed.state(), state);
    }

    for rejected in [
        result_line(&secret, COMMAND_ID, "healthy", "douyin.publish-release.v1"),
        result_line(
            &secret,
            COMMAND_ID,
            "publish_verified",
            "douyin.publish-preflight.v1",
        ),
    ] {
        assert!(token
            .parse_platform_command_result(
                COMMAND_ID,
                LocalPlatformCommand::DispatchDouyinPublish,
                &rejected
            )
            .is_err());
    }
}

fn result_line(secret: &str, command_id: &str, state: &str, flow_version: &str) -> String {
    let proof = expected_result_proof(secret, command_id, state);
    serde_json::to_string(&serde_json::json!({
        "authenticationProof": proof,
        "commandId": command_id,
        "event": "platform.command.completed",
        "flowVersion": flow_version,
        "platform": "douyin",
        "protocolVersion": PROTOCOL_VERSION,
        "state": state,
    }))
    .expect("a serializable result line")
}

fn expected_result_proof(secret: &str, command_id: &str, state: &str) -> String {
    let mut authenticator =
        Hmac::<Sha256>::new_from_slice(&decode_hex(secret)).expect("32-byte HMAC key");
    authenticator.update(b"automation-tool.local-executor-result.v1\0");
    authenticator.update(command_id.as_bytes());
    authenticator.update(b"\0");
    authenticator.update(state.as_bytes());
    authenticator.update(b"\0");
    authenticator.update(PROTOCOL_VERSION.as_bytes());
    format!(
        "atlcp1.{}",
        URL_SAFE_NO_PAD.encode(authenticator.finalize().into_bytes())
    )
}

/// Proofs computed by the Python authenticator itself, for a fixed key.
///
/// The tests above assert that what this crate writes equals what
/// `expected_proof` computes. This one asserts that `expected_proof` equals
/// what the other runtime actually produces, so the chain closes: a change to
/// either side's domain or field order breaks a test here rather than
/// surfacing as a rejected command on a user's machine.
///
/// Regenerate with `LocalSessionAuthenticator.proof_for_publish_command` /
/// `.proof_for_publish_dispatch_command` over the same fixed key.
#[test]
fn the_proof_binding_agrees_with_the_python_authenticator() {
    let secret: String = (0_u8..32).map(|value| format!("{value:02x}")).collect();

    assert_eq!(
        expected_proof(
            &secret,
            PUBLISH_DOMAIN,
            &[
                COMMAND_ID,
                "douyin.publish.preflight",
                "/opt/automation-tool/chromium",
                "/opt/automation-tool/profile",
                "1",
                PUBLISH_JOB_ID,
                "/opt/automation-tool/clip.mp4",
                TITLE,
                DESCRIPTION,
                PROTOCOL_VERSION,
            ],
        ),
        "atlcp1.jt-knrToB-LlNM4ljCdltOL5CJT4ZvMeomLnPI0mCwc"
    );
    assert_eq!(
        expected_proof(
            &secret,
            DISPATCH_DOMAIN,
            &[
                COMMAND_ID,
                "douyin.publish.dispatch",
                PUBLISH_JOB_ID,
                CONFIRMATION_ID,
                PROTOCOL_VERSION,
            ],
        ),
        "atlcp1.z8O9rIV67M3BQpYYu_PXWin1B6Y2XrJ8iMVAqpa07xE"
    );
}

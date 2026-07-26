#![cfg(unix)]
//! What the one-sentence path reports when the authoring child does not deliver.
//!
//! Every failure on this path used to arrive as one code, `render_unavailable`,
//! which is also what a missing packaged runtime, an unverifiable Executor
//! package and a worker that will not start return. A run that ends in that
//! code after fourteen minutes tells nobody which of those happened, and the
//! child's standard error is deliberately discarded so a model echo — which may
//! carry the user's own sentence or a fragment of their key — cannot reach a
//! log. Nothing else was left to read.
//!
//! So the things this side already knows for certain get their own codes:
//! whether we killed the child for outliving its budget, whether it completed
//! our protocol and said no, whether it died without answering, and whether it
//! answered with something we refused. None of them requires reading a single
//! byte the model produced.
//!
//! Splitting those four was not enough. Three failures kept arriving inside the
//! refusal document having read no sentence at all — a model service that was
//! never reached, one that answered and then went silent, and an installation
//! whose pinned files were simply absent — and each was reported to the user as
//! their description being impossible. They are told apart now by the closed
//! reason token the child writes, whose classes live in the shared contract
//! rather than in either side's head; the cases below drive a real child for
//! every token that contract takes out of the refusal channel.
//!
//! The refusal is told from the rest by the document the child wrote on
//! stdout, not by its exit number. Both would work when everything behaves,
//! but only the document is *evidence*: a child that fell over cannot produce
//! it, whereas an exit code is a single integer that a half-finished process
//! can still return. It also keeps the number itself in one language instead
//! of two that must be kept in step.
//!
//! The fixtures are shell scripts, so this file is Unix-only. What it covers —
//! how an exit status and a deadline become an error code — is
//! platform-independent code in `run_motion_authoring`; only the way a fake
//! child is written down is not.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::motion_video_studio::MotionVideoStudioErrorCode;
use automation_tool_desktop_lib::run_motion_authoring;

static TEMPORARY_SEQUENCE: AtomicU64 = AtomicU64::new(1);

struct TempDirectory(PathBuf);

impl TempDirectory {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-t36-child-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            TEMPORARY_SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        fs::create_dir(&path).expect("temporary root");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).expect("private root");
        Self(path)
    }
}

impl Drop for TempDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// A stand-in for the packaged Executor entrypoint.
///
/// Every fixture drains standard input first: the real entrypoint reads one
/// JSON request from there, and a child that exits without reading would break
/// the write instead of exercising the exit status this test is about.
fn child(root: &TempDirectory, body: &str) -> PathBuf {
    let entrypoint = root.0.join("automation-tool-executor");
    fs::write(&entrypoint, format!("#!/bin/sh\ncat >/dev/null\n{body}")).expect("fixture");
    fs::set_permissions(&entrypoint, fs::Permissions::from_mode(0o700)).expect("fixture mode");
    entrypoint
}

fn request() -> serde_json::Value {
    serde_json::json!({ "schemaVersion": 1, "brief": "用蓝色商务风做一段说明" })
}

/// The code as the card will actually receive it.
///
/// Asserted through the serialization rather than the enum so the thing under
/// test is the wire the React side branches on: a variant renamed without its
/// `serde` rename following would leave every enum comparison passing while the
/// card fell through to its catch-all sentence.
fn wire_code(
    error: &automation_tool_desktop_lib::motion_video_studio::MotionVideoStudioError,
) -> String {
    serde_json::to_value(error)
        .expect("a command error serializes")
        .get("code")
        .and_then(serde_json::Value::as_str)
        .expect("a command error carries its code")
        .to_owned()
}

/// Run one child that answers `document` and exits non-zero, and report the code.
fn code_for_answer(root: &TempDirectory, document: &str) -> String {
    let entrypoint = child(root, &format!("printf '%s' '{document}'\nexit 70\n"));
    let error = run_motion_authoring(&entrypoint, &request(), Duration::from_secs(30))
        .expect_err("a child that exits non-zero has not authored anything");
    wire_code(&error)
}

fn answer(status: &str, reason: &str) -> String {
    format!("{{\"schemaVersion\":1,\"status\":\"{status}\",\"rejectionReason\":\"{reason}\"}}")
}

const REFUSAL_CONTRACT: &str =
    include_str!("../../../contracts/video/motion-authoring-refusal.v1.json");

/// The code this side owes each class the shared contract declares.
///
/// A new class with no entry here fails loudly instead of quietly reaching the
/// user as the catch-all: that silence is precisely how three unrelated
/// failures came to share one sentence in the first place.
fn expected_code_for_class(class: &str) -> &'static str {
    match class {
        "app_request_invalid" => "authoring_crashed",
        // Our defect too, reached from the other end: the child's own
        // construction, not the request this side built.
        "executor_defect" => "authoring_crashed",
        "installation_damaged" => "authoring_installation_damaged",
        "model_configuration_required" => "configuration_required",
        "model_timed_out" => "authoring_model_timed_out",
        "model_transport_failed" => "authoring_model_transport_failed",
        other => panic!("the shared contract declares a class this side cannot report: {other}"),
    }
}

fn non_refusal_outcomes() -> serde_json::Map<String, serde_json::Value> {
    serde_json::from_str::<serde_json::Value>(REFUSAL_CONTRACT)
        .expect("the refusal contract parses")
        .get("nonRefusalOutcomes")
        .and_then(|value| value.as_object().cloned())
        .expect("the refusal contract declares its non-refusal classes")
}

/// A model service that was never reached is not the agent declining this brief.
///
/// Measured on 2026-07-26: an unreachable model told the user their description
/// could not be made, after two seconds. Nothing had read the description.
#[test]
fn a_model_that_was_never_reached_is_reported_as_its_own_failure() {
    let root = TempDirectory::new();

    let code = code_for_answer(
        &root,
        &answer(
            "model_transport_failed",
            "video_creation_model_transport_failed",
        ),
    );

    assert_eq!(code, "authoring_model_transport_failed");
}

/// A model that took the connection and then stopped sending is a third thing
/// again: it was reached, so the network and the address are not where the user
/// should be sent, and the wait has a known length worth telling them.
#[test]
fn a_model_that_went_quiet_is_told_apart_from_one_that_was_never_reached() {
    let root = TempDirectory::new();

    let silent = code_for_answer(
        &root,
        &answer("model_timed_out", "video_creation_model_timed_out"),
    );
    let absent = code_for_answer(
        &root,
        &answer(
            "model_transport_failed",
            "video_creation_model_transport_failed",
        ),
    );

    assert_eq!(silent, "authoring_model_timed_out");
    assert_ne!(
        silent, absent,
        "these two arrived as one code and therefore as one sentence"
    );
}

/// Our own packaged files failing verification is the widest mouth of this
/// funnel, and the least excusable one to word as the user's fault: no rewrite
/// of any sentence puts a missing pinned file back.
#[test]
fn a_damaged_installation_is_never_reported_as_the_description_being_wrong() {
    let root = TempDirectory::new();

    let code = code_for_answer(
        &root,
        &answer(
            "installation_damaged",
            "agent_pinned_workflow_file_is_missing_or_a_symlink",
        ),
    );

    assert_eq!(code, "authoring_installation_damaged");
}

/// The request the child judged malformed is one this side built. The user
/// typed a sentence that was never looked at.
#[test]
fn a_request_this_side_built_wrong_is_not_reported_as_a_refusal() {
    let root = TempDirectory::new();

    let code = code_for_answer(
        &root,
        &answer("app_request_invalid", "request_shape_invalid"),
    );

    assert_eq!(code, "authoring_crashed");
}

/// Five guards inside the child that can only fire because we built it wrong.
///
/// The workspace was not handed over, the pinned workflow reference was not
/// handed over, the tools argument was the wrong type, or the tool surface no
/// longer matches the closed allowlist. Nothing the user types reaches any of
/// them, and nothing had read their sentence — yet all four arrived here as a
/// refusal and told them to describe the film differently.
///
/// Both statuses are checked: the one a packaged child writes today, and the
/// one it writes once the class exists. The reason token is the evidence in
/// either case, so neither may resolve towards the user.
#[test]
fn our_own_wiring_defect_is_never_reported_as_the_description_being_wrong() {
    let root = TempDirectory::new();

    for reason in [
        "agent_not_a_motionauthoringtools_instance",
        "agent_tool_surface_does_not_match_the_closed_allowlist",
        "agent_tools_require_an_authoringworkspace",
        "agent_workflow_reference_required",
        "agent_workspace_required",
    ] {
        for status in ["rejected", "executor_defect"] {
            let code = code_for_answer(&root, &answer(status, reason));

            assert_ne!(
                code, "authoring_refused",
                "{reason} on status {status} asks for a sentence nothing read"
            );
            assert_eq!(code, "authoring_crashed", "{reason} on status {status}");
        }
    }
}

/// A packaged Executor from before the statuses split still answers these on
/// the refusal status. The reason token is the evidence, so it is what decides.
#[test]
fn an_older_child_that_still_says_rejected_is_classified_by_its_reason() {
    let root = TempDirectory::new();

    let code = code_for_answer(
        &root,
        &answer("rejected", "video_creation_model_transport_failed"),
    );

    assert_eq!(code, "authoring_model_transport_failed");
}

/// Contradictory evidence resolves away from the user, never towards them.
///
/// A status naming one class and a reason belonging to another is a child this
/// side does not understand. Reporting the more specific field would be a guess;
/// guessing "the user wrote a bad sentence" is the guess that must never be made.
#[test]
fn an_answer_that_contradicts_itself_is_reported_as_our_failure() {
    let root = TempDirectory::new();

    let code = code_for_answer(
        &root,
        &answer("installation_damaged", "brief_duration_out_of_range"),
    );

    assert_eq!(code, "authoring_crashed");
}

/// Every reason the shared contract takes out of the refusal channel must reach
/// the user as the code its class calls for — and never as a refusal.
///
/// This is the gate that keeps the two sides in step: the vocabulary lives in
/// the contract, and a token added there without a code here fails now rather
/// than silently reaching a user as "请换一句更具体的描述".
#[test]
fn every_non_refusal_reason_in_the_contract_reaches_its_own_code() {
    let root = TempDirectory::new();
    let outcomes = non_refusal_outcomes();
    assert!(
        !outcomes.is_empty(),
        "the contract must declare the classes"
    );

    let mut checked = 0;
    for (class, reasons) in &outcomes {
        let expected = expected_code_for_class(class);
        for reason in reasons.as_array().expect("a class lists its reasons") {
            let reason = reason.as_str().expect("a reason is a token");
            let code = code_for_answer(&root, &answer(class, reason));
            assert_eq!(code, expected, "class {class} reason {reason}");
            assert_ne!(
                code, "authoring_refused",
                "{reason} would tell the user to rewrite a sentence nothing read"
            );
            checked += 1;
        }
    }
    assert!(checked >= 20, "only {checked} reasons were covered");
}

/// The happy path is here to prove the fixtures actually run a child; without
/// it a broken harness would make all three failure cases pass for the wrong
/// reason.
#[test]
fn an_answer_from_a_child_that_finishes_is_read_back_whole() {
    let root = TempDirectory::new();
    let entrypoint = child(&root, "printf '%s' '{\"status\":\"authored\"}'\n");

    let answer = run_motion_authoring(&entrypoint, &request(), Duration::from_secs(30))
        .expect("a child that exits zero hands back its answer");

    assert_eq!(answer, "{\"status\":\"authored\"}");
}

/// The budget ran out and we killed the child. Nothing is known about why the
/// model was slow, and nothing needs to be: "it took longer than we allow" is
/// the whole finding, and it is ours, not the child's.
#[test]
fn a_child_that_outlives_its_deadline_is_reported_as_a_timeout() {
    let root = TempDirectory::new();
    let entrypoint = child(&root, "sleep 60\n");
    let started = Instant::now();

    let error = run_motion_authoring(&entrypoint, &request(), Duration::from_millis(500))
        .expect_err("a child past its deadline must not be waited on forever");

    assert_eq!(error.code(), MotionVideoStudioErrorCode::AuthoringTimedOut);
    assert!(
        started.elapsed() < Duration::from_secs(30),
        "the deadline must end the wait, not the child's own exit"
    );
}

/// The child completed our protocol and said no: it wrote the refusal document
/// on stdout and exited non-zero.
///
/// This is the agent deciding the request cannot be authored — a product
/// behaviour, and the user's move is to describe the film differently. Calling
/// it a crash would report working software as broken, and would bury the real
/// crashes in the same bucket.
#[test]
fn a_child_that_refuses_through_the_protocol_is_not_reported_as_a_crash() {
    let root = TempDirectory::new();
    let entrypoint = child(
        &root,
        "printf '%s' '{\"schemaVersion\":1,\"status\":\"rejected\",\"rejectionReason\":\"brief_duration_out_of_range\"}'\nexit 70\n",
    );

    let error = run_motion_authoring(&entrypoint, &request(), Duration::from_secs(30))
        .expect_err("a refusal is not an answer to render");

    assert_eq!(error.code(), MotionVideoStudioErrorCode::AuthoringRefused);
}

/// The App and Executor ship together, but a stale verified package has
/// existed in the field before. Keep the old two-field refusal classifiable
/// while requiring every newly present reason to pass the closed contract.
#[test]
fn an_older_child_without_a_reason_still_reports_a_refusal() {
    let root = TempDirectory::new();
    let entrypoint = child(
        &root,
        "printf '%s' '{\"schemaVersion\":1,\"status\":\"rejected\"}'\nexit 70\n",
    );

    let error = run_motion_authoring(&entrypoint, &request(), Duration::from_secs(30))
        .expect_err("a legacy refusal is still a refusal");

    assert_eq!(error.code(), MotionVideoStudioErrorCode::AuthoringRefused);
}

/// A rejection reason is a dedicated closed field, not a way to reopen the
/// general native error wire to arbitrary strings.
#[test]
fn a_child_cannot_put_an_arbitrary_string_in_the_refusal_reason() {
    let root = TempDirectory::new();
    let entrypoint = child(
        &root,
        "printf '%s' '{\"schemaVersion\":1,\"status\":\"rejected\",\"rejectionReason\":\"caller path: /Users/private/input.txt\"}'\nexit 70\n",
    );

    let error = run_motion_authoring(&entrypoint, &request(), Duration::from_secs(30))
        .expect_err("an unrecognised reason is not a valid refusal document");

    assert_eq!(error.code(), MotionVideoStudioErrorCode::AuthoringCrashed);
}

/// Static analysis can report several failures at once. The wire keeps those
/// codes because each is actionable, but only as a sorted subset of the
/// contract's closed gate vocabulary.
#[test]
fn a_closed_static_gate_reason_is_a_refusal_but_an_unknown_gate_is_not() {
    let root = TempDirectory::new();
    let valid = child(
        &root,
        "printf '%s' '{\"schemaVersion\":1,\"status\":\"rejected\",\"rejectionReason\":\"agent_composition_failed_static_gates:remote_reference+undeclared_asset\"}'\nexit 70\n",
    );

    let error = run_motion_authoring(&valid, &request(), Duration::from_secs(30))
        .expect_err("a closed static-gate reason is still a refusal");
    assert_eq!(error.code(), MotionVideoStudioErrorCode::AuthoringRefused);

    let invalid = child(
        &root,
        "printf '%s' '{\"schemaVersion\":1,\"status\":\"rejected\",\"rejectionReason\":\"agent_composition_failed_static_gates:remote_reference+user_supplied\"}'\nexit 70\n",
    );
    let error = run_motion_authoring(&invalid, &request(), Duration::from_secs(30))
        .expect_err("an unknown gate cannot widen the refusal wire");
    assert_eq!(error.code(), MotionVideoStudioErrorCode::AuthoringCrashed);
}

/// The child died without completing the protocol.
///
/// A crashing child cannot write the refusal document: its traceback goes to
/// standard error, which is discarded so no model echo can reach a log, and
/// stdout stays empty. That absence is the evidence — this side never has to
/// trust an exit number to tell the two apart.
#[test]
fn a_child_that_dies_without_answering_is_reported_as_a_crash() {
    let root = TempDirectory::new();
    let entrypoint = child(
        &root,
        "echo 'Traceback (most recent call last):' >&2\nexit 1\n",
    );

    let error = run_motion_authoring(&entrypoint, &request(), Duration::from_secs(30))
        .expect_err("a child that never answered has not refused anything");

    assert_eq!(error.code(), MotionVideoStudioErrorCode::AuthoringCrashed);
}

/// The classification is the protocol document, not the exit status.
///
/// A child that exits with the refusal code but never produced the refusal
/// document did not refuse anything — it fell over on the way. Reading the
/// number alone would let a half-written crash present itself as a clean
/// product decision, which is exactly the confusion these codes exist to end.
#[test]
fn the_refusal_is_judged_by_the_answer_and_not_by_the_exit_number() {
    let root = TempDirectory::new();
    let entrypoint = child(&root, "printf '%s' 'Traceback (most rec'\nexit 70\n");

    let error = run_motion_authoring(&entrypoint, &request(), Duration::from_secs(30))
        .expect_err("a truncated answer is not a refusal");

    assert_eq!(error.code(), MotionVideoStudioErrorCode::AuthoringCrashed);
}

/// An entrypoint that cannot be started at all is not an authoring failure: no
/// authoring run ever happened. It stays on the packaged-runtime code so the
/// three new ones keep meaning exactly what they say.
#[test]
fn an_entrypoint_that_cannot_be_started_is_not_reported_as_an_authoring_failure() {
    let root = TempDirectory::new();

    let error = run_motion_authoring(
        &root.0.join("absent-executor"),
        &request(),
        Duration::from_secs(30),
    )
    .expect_err("a missing entrypoint cannot produce an answer");

    assert_eq!(error.code(), MotionVideoStudioErrorCode::RenderUnavailable);
}

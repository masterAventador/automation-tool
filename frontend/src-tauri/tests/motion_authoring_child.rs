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
//! The refusal is told from the crash by the document the child wrote on
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
        "printf '%s' '{\"schemaVersion\":1,\"status\":\"rejected\"}'\nexit 70\n",
    );

    let error = run_motion_authoring(&entrypoint, &request(), Duration::from_secs(30))
        .expect_err("a refusal is not an answer to render");

    assert_eq!(error.code(), MotionVideoStudioErrorCode::AuthoringRefused);
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
    let entrypoint = child(&root, "echo 'Traceback (most recent call last):' >&2\nexit 1\n");

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

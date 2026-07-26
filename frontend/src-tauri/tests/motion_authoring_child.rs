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
//! So the three things this side already knows for certain get their own codes:
//! whether we killed the child for outliving its budget, whether the child
//! itself exited non-zero, and whether it answered with something we refused.
//! None of them requires reading a single byte the model produced.
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

/// The child decided it could not do the job — it refuses on stdout and exits
/// 70 — or it crashed. Either way it is the authoring run that failed, not the
/// packaged renderer, and the user must not be sent to check a component that
/// was never involved.
#[test]
fn a_child_that_exits_non_zero_is_reported_as_the_authoring_run_failing() {
    let root = TempDirectory::new();
    let entrypoint = child(
        &root,
        "printf '%s' '{\"schemaVersion\":1,\"status\":\"rejected\"}'\nexit 70\n",
    );

    let error = run_motion_authoring(&entrypoint, &request(), Duration::from_secs(30))
        .expect_err("a non-zero exit is not an answer");

    assert_eq!(error.code(), MotionVideoStudioErrorCode::AuthoringFailed);
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

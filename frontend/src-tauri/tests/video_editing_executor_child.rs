#![cfg(unix)]

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::video_editing_executor::{
    run_video_editing_child, VideoEditingChildErrorKind, VideoEditingChildStatus,
};
use uuid::Uuid;

static TEMPORARY_SEQUENCE: AtomicU64 = AtomicU64::new(1);
const JOB_ID: &str = "00000000-0000-4000-8000-000000000203";

struct TemporaryRoot(PathBuf);

impl TemporaryRoot {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-t4-editing-child-{}-{}-{}",
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

    fn child(&self, body: &str) -> PathBuf {
        let path = self.0.join("automation-tool-executor");
        fs::write(&path, format!("#!/bin/sh\n{body}")).expect("child fixture");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).expect("child mode");
        path
    }

    fn output(&self) -> PathBuf {
        let path = self.0.join("output");
        fs::create_dir(&path).expect("output");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).expect("output mode");
        path
    }
}

impl Drop for TemporaryRoot {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn run(
    entrypoint: &Path,
    output: &Path,
    request: &[u8],
) -> Result<
    automation_tool_desktop_lib::video_editing_executor::VideoEditingChildResult,
    automation_tool_desktop_lib::video_editing_executor::VideoEditingChildError,
> {
    run_video_editing_child(
        entrypoint,
        request,
        Uuid::parse_str(JOB_ID).unwrap(),
        output,
        Duration::from_secs(5),
    )
}

#[test]
fn child_receives_one_stdin_document_and_returns_one_closed_success() {
    let root = TemporaryRoot::new();
    let output = root.output();
    let result_path = output.join("00000000-0000-4000-8000-000000000204.mp4");
    fs::write(&result_path, b"cloud-output").unwrap();
    let child = root.child(&format!(
        r#"test "$1" = "--execute-video-editing" || exit 91
request="$(cat)"
case "$request" in *private-secret*) ;; *) exit 92 ;; esac
printf '%s' '{{"schemaVersion":1,"status":"succeeded","editingJobId":"{JOB_ID}","outputPath":"{}","outputSha256":"{}","outputSizeBytes":12,"failureCode":null}}'
"#,
        result_path.display(),
        "0".repeat(64),
    ));

    let result = run(&child, &output, br#"{"accessKeySecret":"private-secret"}"#).unwrap();

    assert_eq!(result.status(), VideoEditingChildStatus::Succeeded);
    assert_eq!(result.output_path(), Some(result_path.as_path()));
    assert_eq!(result.output_sha256(), Some("0".repeat(64).as_str()));
    assert_eq!(result.output_size_bytes(), Some(12));
}

#[test]
fn malformed_timeout_and_rejected_children_never_reflect_request_material() {
    let root = TemporaryRoot::new();
    let output = root.output();
    let secret = b"{\"accessKeySecret\":\"private-secret\"}";
    for (body, expected, budget) in [
        (
            "cat >/dev/null\nprintf '%s' 'private-secret'\nexit 0\n",
            VideoEditingChildErrorKind::InvalidResponse,
            Duration::from_secs(2),
        ),
        (
            "cat >/dev/null\nexit 2\n",
            VideoEditingChildErrorKind::RequestRejected,
            Duration::from_secs(2),
        ),
        (
            "cat >/dev/null\nsleep 5\n",
            VideoEditingChildErrorKind::OutcomeUncertain,
            Duration::from_millis(100),
        ),
    ] {
        let child = root.child(body);
        let error = run_video_editing_child(
            &child,
            secret,
            Uuid::parse_str(JOB_ID).unwrap(),
            &output,
            budget,
        )
        .unwrap_err();
        assert_eq!(error.kind(), expected);
        let rendered = format!("{error} {error:?}");
        assert!(!rendered.contains("private-secret"));
        assert!(!rendered.contains(&root.0.to_string_lossy().to_string()));
    }
}

#[test]
fn timeout_terminates_the_whole_child_process_tree() {
    let root = TemporaryRoot::new();
    let output = root.output();
    let descendant_pid_path = fs::canonicalize(&root.0)
        .expect("canonical temporary root")
        .join("descendant.pid");
    let child = root.child(&format!(
        r#"sleep 30 &
printf '%s' "$!" > '{}'
cat >/dev/null
sleep 30
"#,
        descendant_pid_path.display(),
    ));

    let error = run_video_editing_child(
        &child,
        br#"{"schemaVersion":1}"#,
        Uuid::parse_str(JOB_ID).unwrap(),
        &output,
        Duration::from_secs(2),
    )
    .unwrap_err();
    assert_eq!(error.kind(), VideoEditingChildErrorKind::OutcomeUncertain);

    let descendant_pid = fs::read_to_string(&descendant_pid_path)
        .expect("descendant pid")
        .parse::<i32>()
        .expect("numeric descendant pid");
    let deadline = Instant::now() + Duration::from_secs(2);
    while process_exists(descendant_pid) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(20));
    }
    let process_tree_was_terminated = !process_exists(descendant_pid);
    if !process_tree_was_terminated {
        unsafe {
            libc::kill(descendant_pid, libc::SIGKILL);
        }
    }
    assert!(
        process_tree_was_terminated,
        "the editing child left a descendant process running"
    );
}

fn process_exists(process_id: i32) -> bool {
    unsafe { libc::kill(process_id, 0) == 0 }
}

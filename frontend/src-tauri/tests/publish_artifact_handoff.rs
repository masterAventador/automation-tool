//! The seam: what the App hands over, and what the executor will actually take.
//!
//! Every other layer of the publish chain proves itself against a stand-in.
//! Rust unit tests never start the executor; the UI Harness drives a controlled
//! Adapter; the Python tests build their own input file. So a disagreement
//! about *the file itself* is invisible everywhere and fatal in production —
//! which is exactly what happened: video Artifacts are stored as
//! `<artifacts>/<id>/payload`, with no extension at all, and
//! `douyin/publish_artifact.py` requires exactly one `.mp4`/`.mov` suffix.
//! Wiring the page to the command would have produced a publish button that
//! fails on every click, with all three layers green.
//!
//! So these tests refuse to re-implement either side. Rust really produces the
//! file, and the real Python boundary really judges it.

#![cfg(unix)]

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::video_job_workspace::{
    VideoJobWorkspacePolicy, VideoJobWorkspaceStore,
};
use uuid::Uuid;

static TEMPORARY_ROOT_SEQUENCE: AtomicU64 = AtomicU64::new(0);

/// Ask the *real* executor boundary what it makes of one path.
///
/// Not a re-statement of its rules — the rules are what is in dispute. The
/// answer is whatever `open_publish_artifact` does today.
fn executor_verdict(path: &Path) -> String {
    let backend = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../backend");
    let output = Command::new("uv")
        .current_dir(&backend)
        .args([
            "run",
            "--locked",
            "python",
            "-c",
            concat!(
                "import sys\n",
                "from pathlib import Path\n",
                "from automation_tool.executor.rpa.douyin.publish_artifact import (\n",
                "    DouyinPublishArtifactRejected,\n",
                "    open_publish_artifact,\n",
                ")\n",
                "try:\n",
                "    artifact = open_publish_artifact(Path(sys.argv[1]))\n",
                "except DouyinPublishArtifactRejected:\n",
                "    print('rejected')\n",
                "else:\n",
                "    print(f'accepted {artifact.media_type} {artifact.sha256}')\n",
            ),
        ])
        .arg(path)
        .output()
        .expect("the executor boundary must be runnable: `uv` and backend/.venv are required");
    assert!(
        output.status.success(),
        "executor boundary failed to answer: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout)
        .expect("executor verdict")
        .trim()
        .to_owned()
}

struct TemporaryRoot(PathBuf);

impl TemporaryRoot {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-pb07-handoff-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            TEMPORARY_ROOT_SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        fs::create_dir(&path).expect("temporary AppData");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).expect("private AppData");
        // The executor refuses any symlinked ancestor, and on macOS the system
        // temporary directory is reached through one.
        Self(fs::canonicalize(path).expect("canonical AppData"))
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TemporaryRoot {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn policy() -> VideoJobWorkspacePolicy {
    VideoJobWorkspacePolicy::new(16 * 1024 * 1024, 8 * 1024 * 1024, 8, 30 * 24 * 60 * 60, 0)
        .expect("workspace policy")
}

/// Import one finished video exactly the way both creation lines do.
fn store_with_one_rendered_video(
    root: &TemporaryRoot,
    payload: &[u8],
) -> (VideoJobWorkspaceStore, Uuid) {
    let store = VideoJobWorkspaceStore::initialize(root.path(), policy()).expect("workspace store");
    let workspace = store
        .create(Uuid::parse_str("123e4567-e89b-42d3-a456-4266141742a1").expect("job UUID"))
        .expect("workspace");
    fs::write(
        store
            .worker_output_directory(&workspace)
            .expect("worker output")
            .join("result.mp4"),
        payload,
    )
    .expect("rendered video");
    let record = store
        .import_output(&workspace, "result.mp4", "video/mp4", "rendered_video")
        .expect("artifact import");
    let artifact_id = record.artifact_id();
    (store, artifact_id)
}

fn stored_payload_path(root: &TemporaryRoot, artifact_id: Uuid) -> PathBuf {
    root.path()
        .join("video-workspaces-v1")
        .join("artifacts")
        .join(artifact_id.hyphenated().to_string())
        .join("payload")
}

#[test]
fn what_the_bridge_stages_is_what_the_executor_accepts() {
    let root = TemporaryRoot::new();
    let payload = b"deterministic-video-bytes-for-the-publish-handoff";
    let (store, artifact_id) = store_with_one_rendered_video(&root, payload);

    let staged = store
        .stage_publishable_artifact(artifact_id)
        .expect("stage the finished video for publishing");

    assert_eq!(
        staged.path().extension().and_then(|value| value.to_str()),
        Some("mp4"),
    );
    assert!(staged.path().starts_with(root.path()));
    assert_eq!(
        executor_verdict(staged.path()),
        format!("accepted video/mp4 {}", staged.sha256()),
    );
    assert_eq!(staged.size_bytes(), payload.len() as u64);
}

#[test]
fn the_stored_artifact_payload_is_refused_by_the_executor() {
    // The reason staging exists at all. Handing `<artifacts>/<id>/payload`
    // straight to the executor — which is what an `artifactPath` contract
    // invites — is refused for want of an extension, on every single click.
    let root = TemporaryRoot::new();
    let (_store, artifact_id) = store_with_one_rendered_video(&root, b"stored-video-bytes");

    let stored = stored_payload_path(&root, artifact_id);

    assert!(stored.is_file());
    assert_eq!(executor_verdict(&stored), "rejected");
}

#[test]
fn a_hard_link_is_refused_by_the_executor_so_the_staged_copy_must_be_a_copy() {
    // Staging the video by hard-linking it would be the obvious optimisation,
    // and it does not work: the executor requires a single-link regular file,
    // and a hard link makes *both* names multiply-linked. This test is here so
    // that optimisation gets refused in CI instead of in front of an operator.
    let root = TemporaryRoot::new();
    let (store, artifact_id) = store_with_one_rendered_video(&root, b"linked-video-bytes");
    let staged = store
        .stage_publishable_artifact(artifact_id)
        .expect("stage the finished video for publishing");
    let linked = staged
        .path()
        .parent()
        .expect("staging directory")
        .join("hard-linked.mp4");
    fs::hard_link(staged.path(), &linked).expect("hard link");

    assert_eq!(executor_verdict(&linked), "rejected");
    // And the staged copy stops being acceptable while the extra link exists.
    assert_eq!(executor_verdict(staged.path()), "rejected");

    fs::remove_file(&linked).expect("drop the extra link");
    assert!(executor_verdict(staged.path()).starts_with("accepted "));
}

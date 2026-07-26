#![cfg(unix)]

use std::fs;
use std::io::Read;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::video_job_workspace::{
    VideoJobWorkspacePolicy, VideoJobWorkspaceStore, VideoWorkspaceDisposition,
    VideoWorkspaceErrorCode,
};
use sha2::{Digest, Sha256};
use uuid::Uuid;

static TEMPORARY_ROOT_SEQUENCE: AtomicU64 = AtomicU64::new(0);

struct TemporaryRoot(PathBuf);

impl TemporaryRoot {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-vf03-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            TEMPORARY_ROOT_SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        fs::create_dir(&path).expect("temporary AppData");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).expect("private AppData");
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

fn job(value: &str) -> Uuid {
    Uuid::parse_str(value).expect("job UUID")
}

#[test]
fn isolates_jobs_and_atomically_imports_a_content_addressed_artifact() {
    let root = TemporaryRoot::new();
    let store = VideoJobWorkspaceStore::initialize(root.path(), policy()).expect("workspace store");
    let first = store
        .create(job("123e4567-e89b-42d3-a456-426614174201"))
        .expect("first workspace");
    let second = store
        .create(job("123e4567-e89b-42d3-a456-426614174202"))
        .expect("second workspace");

    assert_ne!(
        store.worker_output_directory(&first).expect("first output"),
        store
            .worker_output_directory(&second)
            .expect("second output"),
    );
    let payload = b"deterministic-video-bytes";
    fs::write(
        store
            .worker_output_directory(&first)
            .expect("first output")
            .join("result.mp4"),
        payload,
    )
    .expect("worker output");
    let artifact = store
        .import_output(&first, "result.mp4", "video/mp4", "rendered_video")
        .expect("artifact import");

    assert_eq!(artifact.job_id(), first.job_id());
    assert_eq!(artifact.media_type(), "video/mp4");
    assert_eq!(artifact.role(), "rendered_video");
    assert_eq!(artifact.size_bytes(), payload.len() as u64);
    let expected_digest = Sha256::digest(payload)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    assert_eq!(artifact.sha256(), expected_digest);
    let mut reader = store.open_artifact(&artifact).expect("artifact reader");
    let mut imported = Vec::new();
    reader.read_to_end(&mut imported).expect("stream artifact");
    assert_eq!(imported, payload);
    assert_eq!(
        store.list_artifacts().expect("artifact inventory"),
        vec![artifact]
    );
    assert!(store
        .worker_output_directory(&second)
        .expect("second output")
        .read_dir()
        .expect("isolated second output")
        .next()
        .is_none());
}

#[test]
fn checkpoint_survives_reopen_and_workspace_disposition_does_not_delete_artifacts() {
    let root = TemporaryRoot::new();
    let job_id = job("123e4567-e89b-42d3-a456-426614174203");
    let store = VideoJobWorkspaceStore::initialize(root.path(), policy()).expect("workspace store");
    let workspace = store.create(job_id).expect("workspace");
    store
        .save_checkpoint(&workspace, "render-progress", br#"{"frame":42}"#)
        .expect("checkpoint");
    fs::write(
        store
            .worker_output_directory(&workspace)
            .expect("workspace output")
            .join("result.mp4"),
        b"video",
    )
    .expect("worker output");
    let artifact = store
        .import_output(&workspace, "result.mp4", "video/mp4", "rendered_video")
        .expect("artifact");
    drop(store);

    let reopened_store =
        VideoJobWorkspaceStore::initialize(root.path(), policy()).expect("reopened store");
    let reopened = reopened_store.open(job_id).expect("reopened workspace");
    assert_eq!(
        reopened_store
            .load_checkpoint(&reopened, "render-progress")
            .expect("reopened checkpoint"),
        br#"{"frame":42}"#,
    );
    reopened_store
        .finish(&reopened, VideoWorkspaceDisposition::Keep)
        .expect("keep workspace");
    assert!(reopened_store.open(job_id).is_ok());
    reopened_store
        .finish(&reopened, VideoWorkspaceDisposition::Delete)
        .expect("delete workspace");
    assert_eq!(
        reopened_store
            .open(job_id)
            .expect_err("deleted workspace")
            .code(),
        VideoWorkspaceErrorCode::NotFound,
    );
    let mut reader = reopened_store
        .open_artifact(&artifact)
        .expect("retained artifact");
    let mut retained_payload = Vec::new();
    reader
        .read_to_end(&mut retained_payload)
        .expect("stream retained artifact");
    assert_eq!(retained_payload, b"video");
    reopened_store
        .delete_artifact(artifact.artifact_id())
        .expect("explicit artifact delete");
    assert!(reopened_store
        .list_artifacts()
        .expect("empty inventory")
        .is_empty());
}

#[test]
fn rejects_traversal_links_replaced_workspaces_and_duplicate_names() {
    let root = TemporaryRoot::new();
    let store = VideoJobWorkspaceStore::initialize(root.path(), policy()).expect("workspace store");
    let workspace = store
        .create(job("123e4567-e89b-42d3-a456-426614174204"))
        .expect("workspace");
    let outside = root.path().join("outside.mp4");
    fs::write(&outside, b"outside").expect("outside file");

    for name in [
        "../outside.mp4",
        "/tmp/outside.mp4",
        "nested/result.mp4",
        "..",
    ] {
        let error = store
            .import_output(&workspace, name, "video/mp4", "rendered_video")
            .expect_err("unsafe output name");
        assert_eq!(error.code(), VideoWorkspaceErrorCode::PathRejected);
    }
    let linked = store
        .worker_output_directory(&workspace)
        .expect("workspace output")
        .join("linked.mp4");
    std::os::unix::fs::symlink(&outside, &linked).expect("linked output");
    assert_eq!(
        store
            .import_output(&workspace, "linked.mp4", "video/mp4", "rendered_video")
            .expect_err("linked output rejected")
            .code(),
        VideoWorkspaceErrorCode::PathRejected,
    );

    let original = store
        .worker_output_directory(&workspace)
        .expect("workspace output");
    let workspace_root = original.parent().expect("workspace root").to_path_buf();
    let moved = workspace_root.with_extension("moved");
    fs::rename(&workspace_root, &moved).expect("move original workspace");
    fs::create_dir(&workspace_root).expect("replace workspace");
    fs::set_permissions(&workspace_root, fs::Permissions::from_mode(0o700))
        .expect("replacement permissions");
    assert_eq!(
        store
            .save_checkpoint(&workspace, "render-progress", b"unsafe")
            .expect_err("workspace identity replacement rejected")
            .code(),
        VideoWorkspaceErrorCode::PathRejected,
    );
}

#[test]
fn quota_and_free_space_failures_leave_no_partial_artifact_or_checkpoint() {
    let root = TemporaryRoot::new();
    let small = VideoJobWorkspacePolicy::new(32, 8, 1, 60, 0).expect("small policy");
    let store = VideoJobWorkspaceStore::initialize(root.path(), small).expect("small store");
    let workspace = store
        .create(job("123e4567-e89b-42d3-a456-426614174205"))
        .expect("workspace");
    fs::write(
        store
            .worker_output_directory(&workspace)
            .expect("workspace output")
            .join("large.mp4"),
        b"123456789",
    )
    .expect("oversized output");
    assert_eq!(
        store
            .import_output(&workspace, "large.mp4", "video/mp4", "rendered_video")
            .expect_err("artifact quota")
            .code(),
        VideoWorkspaceErrorCode::QuotaExceeded,
    );
    assert!(store
        .list_artifacts()
        .expect("no partial artifact")
        .is_empty());
    store
        .save_checkpoint(&workspace, "one", b"12345678")
        .expect("first checkpoint");
    store
        .save_checkpoint(&workspace, "one", b"updated")
        .expect("replace checkpoint atomically");
    assert_eq!(
        store
            .load_checkpoint(&workspace, "one")
            .expect("updated checkpoint"),
        b"updated",
    );
    assert_eq!(
        store
            .save_checkpoint(&workspace, "two", b"x")
            .expect_err("checkpoint count quota")
            .code(),
        VideoWorkspaceErrorCode::QuotaExceeded,
    );

    let unavailable_root = TemporaryRoot::new();
    let unavailable_policy =
        VideoJobWorkspacePolicy::new(1024, 512, 1, 60, u64::MAX).expect("space policy");
    let unavailable =
        VideoJobWorkspaceStore::initialize(unavailable_root.path(), unavailable_policy)
            .expect("space constrained store");
    let constrained = unavailable
        .create(job("123e4567-e89b-42d3-a456-426614174206"))
        .expect("constrained workspace");
    assert_eq!(
        unavailable
            .save_checkpoint(&constrained, "progress", b"x")
            .expect_err("insufficient free space")
            .code(),
        VideoWorkspaceErrorCode::StorageUnavailable,
    );
    assert!(unavailable
        .worker_output_directory(&constrained)
        .expect("constrained output")
        .parent()
        .expect("workspace root")
        .join("checkpoints")
        .read_dir()
        .expect("checkpoint directory")
        .next()
        .is_none());
}

#[test]
fn tampered_artifact_and_non_v4_job_fail_closed_without_path_reflection() {
    let root = TemporaryRoot::new();
    let store = VideoJobWorkspaceStore::initialize(root.path(), policy()).expect("workspace store");
    let invalid_job = Uuid::parse_str("123e4567-e89b-12d3-a456-426614174210").expect("non-v4 UUID");
    let invalid = store.create(invalid_job).expect_err("non-v4 job rejected");
    assert_eq!(
        invalid.code(),
        VideoWorkspaceErrorCode::ConfigurationInvalid
    );
    assert!(!format!("{invalid:?}").contains(&root.path().to_string_lossy().to_string()));

    let workspace = store
        .create(job("123e4567-e89b-42d3-a456-426614174211"))
        .expect("workspace");
    fs::write(
        store
            .worker_output_directory(&workspace)
            .expect("output")
            .join("result.mp4"),
        b"original",
    )
    .expect("worker output");
    let artifact = store
        .import_output(&workspace, "result.mp4", "video/mp4", "rendered_video")
        .expect("artifact");
    let payload = root
        .path()
        .join("video-workspaces-v1")
        .join("artifacts")
        .join(artifact.artifact_id().hyphenated().to_string())
        .join("payload");
    fs::write(payload, b"tampered").expect("same-size artifact tampering");
    assert_eq!(
        store
            .open_artifact(&artifact)
            .expect_err("digest mismatch rejected")
            .code(),
        VideoWorkspaceErrorCode::StorageUnavailable,
    );
}

#[test]
fn reopening_with_a_stricter_policy_discards_existing_oversized_artifacts() {
    let root = TemporaryRoot::new();
    let initial_policy = VideoJobWorkspacePolicy::new(32, 16, 1, 60, 0).expect("initial policy");
    let store =
        VideoJobWorkspaceStore::initialize(root.path(), initial_policy).expect("workspace store");
    let workspace = store
        .create(job("123e4567-e89b-42d3-a456-426614174212"))
        .expect("workspace");
    fs::write(
        store
            .worker_output_directory(&workspace)
            .expect("output")
            .join("result.mp4"),
        b"123456789",
    )
    .expect("worker output");
    store
        .import_output(&workspace, "result.mp4", "video/mp4", "rendered_video")
        .expect("artifact");
    drop(store);

    let stricter = VideoJobWorkspacePolicy::new(32, 8, 1, 60, 0).expect("stricter policy");
    let reopened = VideoJobWorkspaceStore::initialize(root.path(), stricter)
        .expect("reopened workspace store");
    assert!(reopened
        .list_artifacts()
        .expect("oversized artifact discarded")
        .is_empty());
}

#[test]
fn startup_discards_corrupt_artifacts_while_runtime_listing_stays_strict() {
    let root = TemporaryRoot::new();
    let store = VideoJobWorkspaceStore::initialize(root.path(), policy()).expect("workspace store");
    let workspace = store
        .create(job("123e4567-e89b-42d3-a456-426614174213"))
        .expect("workspace");
    let outputs = store
        .worker_output_directory(&workspace)
        .expect("worker output");
    fs::write(outputs.join("missing-manifest.mp4"), b"first-video").expect("first output");
    fs::write(outputs.join("size-mismatch.mp4"), b"second-video").expect("second output");
    let missing_manifest = store
        .import_output(
            &workspace,
            "missing-manifest.mp4",
            "video/mp4",
            "rendered_video",
        )
        .expect("first artifact");
    let size_mismatch = store
        .import_output(
            &workspace,
            "size-mismatch.mp4",
            "video/mp4",
            "rendered_video",
        )
        .expect("second artifact");
    let artifacts = root.path().join("video-workspaces-v1").join("artifacts");
    let missing_manifest_directory =
        artifacts.join(missing_manifest.artifact_id().hyphenated().to_string());
    let size_mismatch_directory =
        artifacts.join(size_mismatch.artifact_id().hyphenated().to_string());
    fs::remove_file(missing_manifest_directory.join("manifest.json"))
        .expect("simulate interrupted deletion");
    fs::write(size_mismatch_directory.join("payload"), b"short")
        .expect("simulate interrupted payload persistence");
    assert_eq!(
        store
            .list_artifacts()
            .expect_err("runtime inventory remains strict")
            .code(),
        VideoWorkspaceErrorCode::StorageUnavailable,
    );
    let outside = root.path().join("outside-artifact-data");
    fs::create_dir(&outside).expect("outside directory");
    fs::write(outside.join("sentinel"), b"must-survive").expect("outside sentinel");
    std::os::unix::fs::symlink(&outside, missing_manifest_directory.join("linked-outside"))
        .expect("linked outside directory");
    drop(store);

    let restarted =
        VideoJobWorkspaceStore::initialize(root.path(), policy()).expect("restarted store");
    assert!(!missing_manifest_directory.exists());
    assert!(!size_mismatch_directory.exists());
    assert_eq!(
        fs::read(outside.join("sentinel")).expect("outside data survives cleanup"),
        b"must-survive",
    );
    assert!(restarted
        .list_artifacts()
        .expect("clean inventory")
        .is_empty());
}

#[test]
fn retention_cleanup_preserves_active_jobs_and_initialization_recovers_partial_imports() {
    let root = TemporaryRoot::new();
    let retained_policy =
        VideoJobWorkspacePolicy::new(1024, 512, 2, 1, 0).expect("retention policy");
    let store =
        VideoJobWorkspaceStore::initialize(root.path(), retained_policy).expect("workspace store");
    let retained_id = job("123e4567-e89b-42d3-a456-426614174207");
    let active_id = job("123e4567-e89b-42d3-a456-426614174208");
    let retained = store.create(retained_id).expect("retained workspace");
    let _active = store.create(active_id).expect("active workspace");
    store
        .finish(&retained, VideoWorkspaceDisposition::Keep)
        .expect("retention marker");

    let cleanup = store
        .cleanup_expired(u64::MAX)
        .expect("expired workspace cleanup");
    assert_eq!(cleanup.removed_workspaces(), 1);
    assert_eq!(
        store
            .open(retained_id)
            .expect_err("retained workspace expired")
            .code(),
        VideoWorkspaceErrorCode::NotFound,
    );
    assert!(store.open(active_id).is_ok());

    let partial = root
        .path()
        .join("video-workspaces-v1")
        .join("artifacts")
        .join(".import-123e4567-e89b-42d3-a456-426614174209-1-1");
    fs::create_dir(&partial).expect("interrupted import directory");
    fs::set_permissions(&partial, fs::Permissions::from_mode(0o700)).expect("partial permissions");
    fs::write(partial.join("payload"), b"partial").expect("partial payload");
    drop(store);

    let recovered = VideoJobWorkspaceStore::initialize(root.path(), retained_policy)
        .expect("recover interrupted import");
    assert!(!partial.exists());
    assert!(recovered
        .list_artifacts()
        .expect("clean inventory")
        .is_empty());
}

#[test]
fn tauri_composition_root_owns_the_workspace_store_without_webview_path_commands() {
    let source = fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("src")
            .join("lib.rs"),
    )
    .expect("Tauri composition root");
    assert!(source.contains("app.manage(video_job_workspace::VideoJobWorkspaceStore::initialize("));
    assert!(!source.contains("get_video_workspace_path"));
    assert!(!source.contains("get_video_artifact_path"));
}

#[test]
fn only_a_finished_video_can_be_staged_for_publishing() {
    let root = TemporaryRoot::new();
    let store = VideoJobWorkspaceStore::initialize(root.path(), policy()).expect("workspace store");
    let workspace = store
        .create(job("123e4567-e89b-42d3-a456-426614174220"))
        .expect("workspace");
    let outputs = store
        .worker_output_directory(&workspace)
        .expect("worker output");
    fs::write(outputs.join("result.mp4"), b"rendered-video-bytes").expect("rendered output");
    fs::write(outputs.join("cover.mp4"), b"intermediate-bytes").expect("intermediate output");
    let rendered = store
        .import_output(&workspace, "result.mp4", "video/mp4", "rendered_video")
        .expect("rendered artifact");
    // Same media type, different role: a working file is not a deliverable.
    let intermediate = store
        .import_output(&workspace, "cover.mp4", "video/mp4", "intermediate_render")
        .expect("intermediate artifact");

    let staged = store
        .stage_publishable_artifact(rendered.artifact_id())
        .expect("stage the finished video");
    assert_eq!(staged.sha256(), rendered.sha256());
    assert_eq!(staged.size_bytes(), rendered.size_bytes());
    assert_eq!(staged.artifact_id(), rendered.artifact_id());
    assert_eq!(
        fs::read(staged.path()).expect("staged bytes"),
        b"rendered-video-bytes",
    );

    assert_eq!(
        store
            .stage_publishable_artifact(intermediate.artifact_id())
            .expect_err("a working file is not publishable")
            .code(),
        VideoWorkspaceErrorCode::ConfigurationInvalid,
    );
    assert_eq!(
        store
            .stage_publishable_artifact(job("123e4567-e89b-42d3-a456-426614174221"))
            .expect_err("an artifact that does not exist is not publishable")
            .code(),
        VideoWorkspaceErrorCode::NotFound,
    );
    // A video deleted while it sat selected on the publish page is the same
    // answer, and it must not take the staged copy of a different one with it.
    store
        .delete_artifact(rendered.artifact_id())
        .expect("delete the chosen video");
    assert_eq!(
        store
            .stage_publishable_artifact(rendered.artifact_id())
            .expect_err("a deleted video is not publishable")
            .code(),
        VideoWorkspaceErrorCode::NotFound,
    );
}

#[test]
fn staging_keeps_one_copy_at_a_time_and_can_be_discarded() {
    let root = TemporaryRoot::new();
    let store = VideoJobWorkspaceStore::initialize(root.path(), policy()).expect("workspace store");
    let workspace = store
        .create(job("123e4567-e89b-42d3-a456-426614174222"))
        .expect("workspace");
    let outputs = store
        .worker_output_directory(&workspace)
        .expect("worker output");
    fs::write(outputs.join("first.mp4"), b"first-video").expect("first output");
    fs::write(outputs.join("second.mp4"), b"second-video").expect("second output");
    let first = store
        .import_output(&workspace, "first.mp4", "video/mp4", "rendered_video")
        .expect("first artifact");
    let second = store
        .import_output(&workspace, "second.mp4", "video/mp4", "rendered_video")
        .expect("second artifact");

    let first_staged = store
        .stage_publishable_artifact(first.artifact_id())
        .expect("stage first");
    let second_staged = store
        .stage_publishable_artifact(second.artifact_id())
        .expect("stage second");

    // A publish the operator walked away from must not leave a copy of the
    // video lying around for the next one to pick up.
    assert!(!first_staged.path().exists());
    assert!(second_staged.path().exists());

    store
        .discard_staged_publish_artifacts()
        .expect("discard staged copies");
    assert!(!second_staged.path().exists());
    // Discarding what was handed over never touches the Artifact itself.
    assert_eq!(store.list_artifacts().expect("inventory").len(), 2);
    // And discarding twice is not an error: cleanup runs on every exit path.
    store
        .discard_staged_publish_artifacts()
        .expect("idempotent discard");
}

#[test]
fn a_staged_copy_left_by_a_crash_does_not_survive_the_next_start() {
    let root = TemporaryRoot::new();
    let store = VideoJobWorkspaceStore::initialize(root.path(), policy()).expect("workspace store");
    let workspace = store
        .create(job("123e4567-e89b-42d3-a456-426614174223"))
        .expect("workspace");
    fs::write(
        store
            .worker_output_directory(&workspace)
            .expect("worker output")
            .join("result.mp4"),
        b"crash-video",
    )
    .expect("rendered output");
    let artifact = store
        .import_output(&workspace, "result.mp4", "video/mp4", "rendered_video")
        .expect("artifact");
    let staged = store
        .stage_publishable_artifact(artifact.artifact_id())
        .expect("stage");
    assert!(staged.path().exists());
    drop(store);

    let restarted =
        VideoJobWorkspaceStore::initialize(root.path(), policy()).expect("restarted store");
    assert!(!staged.path().exists());
    assert_eq!(restarted.list_artifacts().expect("inventory").len(), 1);
}

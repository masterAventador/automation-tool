//! Seeding the authored composition's runtime, and judging a typed brief.
//!
//! The authoring agent writes a composition that loads GSAP from the workspace.
//! If those bytes are not the ones the prompt contract was written against, the
//! composition either fails to animate — the exact shape of the still-image
//! failure this line already shipped once — or animates against an API the
//! prompt never described. So the runtime is verified against the locked digest
//! before it is placed, on the same terms as the packaged fonts and Chromium:
//! check before use, refuse on mismatch, never fall back.

use automation_tool_desktop_lib::motion_video_studio::{
    seed_authoring_runtime, MotionVideoBriefRequest, MotionVideoStudioErrorCode,
    AUTHORING_RUNTIME_ASSET,
};
use automation_tool_desktop_lib::video_job_workspace::{
    VideoJobWorkspacePolicy, VideoJobWorkspaceStore,
};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

static TEMPORARY_SEQUENCE: AtomicU64 = AtomicU64::new(1);

struct TempDirectory(PathBuf);

impl TempDirectory {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-t36-runtime-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            TEMPORARY_SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        fs::create_dir(&path).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        }
        Self(fs::canonicalize(path).expect("canonical temporary root"))
    }
}

impl Drop for TempDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn store(root: &Path) -> VideoJobWorkspaceStore {
    VideoJobWorkspaceStore::initialize(
        root,
        VideoJobWorkspacePolicy::new(
            64 * 1024 * 1024,
            32 * 1024 * 1024,
            32,
            Duration::from_secs(3600).as_secs(),
            0,
        )
        .unwrap(),
    )
    .unwrap()
}

/// The bytes the locked catalog declares, read from the machine's catalog when
/// it is present. The digest — not this path — is what the product trusts.
fn locked_runtime() -> Option<Vec<u8>> {
    let candidate = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../.local/offline-motion-deps/catalog/offline-deps/js/gsap-3.14.2/gsap.min.js");
    fs::read(candidate).ok()
}

#[test]
fn the_declared_runtime_is_placed_where_the_composition_loads_it() {
    let Some(runtime) = locked_runtime() else {
        // The catalog is a reproducible download, not a committed asset. Where
        // it has not been built the placement cannot be exercised, but the
        // refusal below still can and is the half that protects the user.
        return;
    };
    let root = TempDirectory::new();
    let store = store(&root.0);
    let source = root.0.join("source-gsap.min.js");
    fs::write(&source, &runtime).unwrap();
    let workspace = store.create_new().unwrap();

    seed_authoring_runtime(&store, &workspace, &source).unwrap();

    let placed = store
        .worker_asset_directory(&workspace)
        .unwrap()
        .join(AUTHORING_RUNTIME_ASSET);
    assert_eq!(fs::read(placed).unwrap(), runtime);
}

#[test]
fn a_runtime_that_is_not_the_locked_one_is_refused_rather_than_used() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    let source = root.0.join("source-gsap.min.js");
    fs::write(&source, b"/* not the locked runtime */").unwrap();
    let workspace = store.create_new().unwrap();

    let error = seed_authoring_runtime(&store, &workspace, &source).unwrap_err();

    assert_eq!(error.code(), MotionVideoStudioErrorCode::RenderUnavailable);
    assert!(
        !store
            .worker_asset_directory(&workspace)
            .unwrap()
            .join(AUTHORING_RUNTIME_ASSET)
            .exists(),
        "a refused runtime must leave nothing behind for the composition to load"
    );
}

#[test]
fn a_missing_runtime_is_refused_rather_than_silently_skipped() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    let workspace = store.create_new().unwrap();

    let error =
        seed_authoring_runtime(&store, &workspace, &root.0.join("absent.js")).unwrap_err();

    assert_eq!(error.code(), MotionVideoStudioErrorCode::RenderUnavailable);
}

#[test]
fn a_brief_is_judged_against_the_same_contracts_the_agent_reads() {
    let longest = automation_tool_desktop_lib::motion_video_studio::duration_limits()
        .unwrap()
        .total_seconds_maximum();

    MotionVideoBriefRequest::one_sentence(
        "用蓝色商务风做一段本周销售增长说明".to_owned(),
        "16:9".to_owned(),
        longest,
        "zh".to_owned(),
    )
    .expect("the longest admissible film is accepted");

    for (brief, ratio, seconds, language) in [
        ("   ", "16:9", 6, "zh"),
        ("用蓝色商务风做一段说明", "1:1", 6, "zh"),
        ("用蓝色商务风做一段说明", "16:9", longest + 1, "zh"),
        ("用蓝色商务风做一段说明", "16:9", 0, "zh"),
        ("用蓝色商务风做一段说明", "16:9", 6, "fr"),
    ] {
        let error = MotionVideoBriefRequest::one_sentence(
            brief.to_owned(),
            ratio.to_owned(),
            seconds,
            language.to_owned(),
        )
        .unwrap_err();
        assert_eq!(error.code(), MotionVideoStudioErrorCode::DraftInvalid);
    }
}

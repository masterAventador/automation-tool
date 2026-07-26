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

/// The agent's answer is untrusted input from another process.
///
/// It names the file the renderer will load and the assets the sandbox will
/// allow, so accepting it unchecked would let a compromised or simply buggy
/// agent widen the sandbox or point the render at something that was never
/// authored. Every field is therefore re-derived or re-checked against the
/// brief the user actually submitted.
#[test]
fn an_authored_answer_is_rechecked_against_the_brief_before_it_becomes_a_render_job() {
    use automation_tool_desktop_lib::motion_video_studio::{
        accept_authored_render_job, MOTION_COMPOSITION_FILE,
    };

    let root = TempDirectory::new();
    let store = store(&root.0);
    let workspace = store.create_new().unwrap();
    let work = store.worker_asset_directory(&workspace).unwrap();
    fs::create_dir_all(work.join("runtime")).unwrap();
    fs::write(work.join(AUTHORING_RUNTIME_ASSET), b"/* runtime */").unwrap();
    fs::write(work.join(MOTION_COMPOSITION_FILE), b"<html></html>").unwrap();
    let request = MotionVideoBriefRequest::one_sentence(
        "用蓝色商务风做一段本周销售增长说明".to_owned(),
        "16:9".to_owned(),
        6,
        "zh".to_owned(),
    )
    .unwrap();

    let good = serde_json::json!({
        "schemaVersion": 1,
        "status": "authored",
        "entryHtml": MOTION_COMPOSITION_FILE,
        "allowedAssets": [AUTHORING_RUNTIME_ASSET],
        "frameCount": 6 * 30,
        "framesPerSecond": 30,
        "durationSeconds": 6,
        "aspectRatio": "16:9",
    });
    let prepared = accept_authored_render_job(&store, &workspace, &request, &good.to_string())
        .expect("a consistent answer is accepted");
    assert_eq!(prepared.frame_count(), 6 * 30);
    assert_eq!(prepared.allowed_assets(), [AUTHORING_RUNTIME_ASSET]);

    for mutation in [
        serde_json::json!({"status": "rejected"}),
        // 指向一个不是编排产物的入口
        serde_json::json!({"entryHtml": "runtime/gsap.min.js"}),
        // 把沙箱白名单扩大到工作区之外
        serde_json::json!({"allowedAssets": ["../../../etc/passwd"]}),
        // 声明一个工作区里根本不存在的资源
        serde_json::json!({"allowedAssets": ["runtime/absent.js"]}),
        // 帧数与用户提交的时长对不上
        serde_json::json!({"frameCount": 6 * 30 + 1}),
        // 时长与用户提交的不一致
        serde_json::json!({"durationSeconds": 7}),
        // 画幅与用户提交的不一致
        serde_json::json!({"aspectRatio": "9:16"}),
    ] {
        let mut document = good.clone();
        for (key, value) in mutation.as_object().unwrap() {
            document[key] = value.clone();
        }
        assert!(
            accept_authored_render_job(&store, &workspace, &request, &document.to_string())
                .is_err(),
            "an answer disagreeing with the brief must not become a RenderJob: {document}"
        );
    }
}

/// The path the package puts the animation runtime at, and the path the render
/// workspace loads it from, must be the same string.
///
/// They are two roles for one relative path: the worker package declares where
/// the release installs it, and the authoring prompt names where the composition
/// loads it. If they ever drift, the seed reads a file that is not there and the
/// one-sentence path fails closed for a reason nobody can see from either side.
#[test]
fn the_packaged_runtime_path_matches_the_path_the_composition_loads() {
    let contract: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../contracts/quality/motion-video-worker-package.v1.json"),
        )
        .unwrap(),
    )
    .unwrap();

    assert_eq!(
        contract["packageLayout"]["authoringRuntimeAsset"]
            .as_str()
            .expect("the worker package must declare where the animation runtime lives"),
        AUTHORING_RUNTIME_ASSET
    );
}

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

/// The resource directory this build resolves its packaged parts from.
///
/// An integration test binary is written to `<target>/<profile>/deps`, and a
/// debug App resolves its resources from `<target>/<profile>` — the directory
/// one level up. Deriving it keeps the test on whatever profile is running
/// instead of naming `debug` a second time.
fn resource_directory() -> PathBuf {
    std::env::current_exe()
        .expect("test executable")
        .parent()
        .and_then(Path::parent)
        .expect("a test binary always sits under <target>/<profile>/deps")
        .to_path_buf()
}

/// The animation runtime exactly where the App will read it from.
///
/// The directory the release installs the worker package into is declared once,
/// in the release resource contract; spelling it out here as well would be a
/// second place to update when it moves, and the failure of the pair drifting
/// is a runtime that cannot be found at all.
fn packaged_runtime() -> PathBuf {
    let contract: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../contracts/quality/release-package-resources.v1.json"),
        )
        .expect("the release resource contract must be readable"),
    )
    .expect("the release resource contract must be valid JSON");
    let parts = contract["resources"]
        .as_array()
        .expect("the contract must list the installed resources")
        .iter()
        .find(|resource| resource["name"] == "motion-video-worker")
        .and_then(|resource| resource["installedParts"].as_array())
        .expect("the contract must say where the motion worker package is installed");
    let mut path = resource_directory();
    for part in parts {
        path.push(part.as_str().expect("an installed part is a path segment"));
    }
    path.join(AUTHORING_RUNTIME_ASSET)
}

/// Placement is exercised with the bytes the App itself would load.
///
/// This used to read the developer-local offline catalog and return early when
/// it was absent, which reported as a pass on any machine that had never built
/// the catalog — the one outcome a gate must never produce. It now reads the
/// assembled worker package, the same file and the same path the production
/// command hands to `seed_authoring_runtime`, and a missing package fails
/// loudly: without it the one-sentence path cannot render at all, so a green
/// result here would be describing a feature that does not work.
#[test]
fn the_declared_runtime_is_placed_where_the_composition_loads_it() {
    let source = packaged_runtime();
    let runtime = fs::read(&source).unwrap_or_else(|error| {
        panic!(
            "the assembled motion worker package must provide {}: {error}. \
             Build it with scripts/prepare_video_runtime.py before running this suite.",
            source.display()
        )
    });
    let root = TempDirectory::new();
    let store = store(&root.0);
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

    let error = seed_authoring_runtime(&store, &workspace, &root.0.join("absent.js")).unwrap_err();

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
        "segments": [{
            "entryHtml": MOTION_COMPOSITION_FILE,
            "allowedAssets": [AUTHORING_RUNTIME_ASSET],
            "canvas": {"width": 640, "height": 360, "deviceScaleFactor": 2},
            "frameCount": 6 * 30,
        }],
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
        let error = accept_authored_render_job(&store, &workspace, &request, &document.to_string())
            .expect_err(&format!(
                "an answer disagreeing with the brief must not become a RenderJob: {document}"
            ));
        // The refusal has to name itself. Reporting it as an unavailable
        // renderer sends the user to check a component that was never
        // involved, and leaves the next person unable to tell an answer we
        // rejected from a packaged part we could not find.
        assert_eq!(
            error.code(),
            MotionVideoStudioErrorCode::AuthoringAnswerInvalid,
            "refusing the agent's answer is not the renderer being unavailable: {document}"
        );
    }
}

/// A film is a list of renders, and this side has to be able to read one.
///
/// Route A draws each shot on the stage its part declares — 1920x1080 for most
/// of the catalog, 1080x1920 for three, and the built-in template on 640x360 at
/// factor 2. One render per shot is what lets each be itself, so the answer
/// carries a segment per shot and this is where they stop being the child's
/// word for it: each names an entry the sandbox will load and a list the sandbox
/// will allow, on exactly the terms the single composition was already checked
/// on.
#[test]
fn an_authored_answer_carries_the_segments_the_film_is_made_of() {
    use automation_tool_desktop_lib::motion_video_studio::{
        accept_authored_render_job, MOTION_COMPOSITION_FILE, TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR,
        TEMPLATE_CANVAS_HEIGHT, TEMPLATE_CANVAS_WIDTH,
    };

    let root = TempDirectory::new();
    let store = store(&root.0);
    let workspace = store.create_new().unwrap();
    let work = store.worker_asset_directory(&workspace).unwrap();
    fs::create_dir_all(work.join("runtime")).unwrap();
    fs::write(work.join(AUTHORING_RUNTIME_ASSET), b"/* runtime */").unwrap();
    fs::write(work.join(MOTION_COMPOSITION_FILE), b"<html></html>").unwrap();
    // The working copy of one catalog part, laid out the way the child writes
    // it: the part beside the shared dependency tree it reaches through `../../`.
    let part_entry = "catalog/items/lt-bold-block/lt-bold-block.html";
    let part_asset = "catalog/offline-deps/gsap.min.js";
    fs::create_dir_all(work.join("catalog/items/lt-bold-block")).unwrap();
    fs::create_dir_all(work.join("catalog/offline-deps")).unwrap();
    fs::write(work.join(part_entry), b"<html></html>").unwrap();
    fs::write(work.join(part_asset), b"/* gsap */").unwrap();

    let request = MotionVideoBriefRequest::one_sentence(
        "用蓝色商务风做一段本周销售增长说明".to_owned(),
        "16:9".to_owned(),
        6,
        "zh".to_owned(),
    )
    .unwrap();

    let template_segment = serde_json::json!({
        "entryHtml": MOTION_COMPOSITION_FILE,
        "allowedAssets": [AUTHORING_RUNTIME_ASSET],
        "canvas": {
            "width": TEMPLATE_CANVAS_WIDTH,
            "height": TEMPLATE_CANVAS_HEIGHT,
            "deviceScaleFactor": TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR,
        },
        "frameCount": 90,
    });
    let part_segment = serde_json::json!({
        "entryHtml": part_entry,
        "allowedAssets": [part_asset],
        // The part's own stage, at factor 1: it already is the output
        // resolution, and sharpening it further costs every frame.
        "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
        "frameCount": 144,
    });
    let good = serde_json::json!({
        "schemaVersion": 1,
        "status": "authored",
        "entryHtml": MOTION_COMPOSITION_FILE,
        "allowedAssets": [AUTHORING_RUNTIME_ASSET],
        "frameCount": 6 * 30,
        "framesPerSecond": 30,
        "durationSeconds": 6,
        "aspectRatio": "16:9",
        "segments": [template_segment, part_segment],
    });

    let prepared = accept_authored_render_job(&store, &workspace, &request, &good.to_string())
        .expect("the answer the packaged Executor produces is accepted");
    let segments = prepared.segments();
    assert_eq!(segments.len(), 2);
    assert_eq!(segments[0].entry_html(), MOTION_COMPOSITION_FILE);
    assert_eq!(segments[0].allowed_assets(), [AUTHORING_RUNTIME_ASSET]);
    assert_eq!(segments[0].frame_count(), 90);
    assert_eq!(segments[1].entry_html(), part_entry);
    assert_eq!(segments[1].width(), 1920);
    assert_eq!(segments[1].height(), 1080);
    assert_eq!(segments[1].device_scale_factor(), 1);
    assert_eq!(segments[1].frame_count(), 144);
    // The film is as long as its shots come to. It is deliberately not the
    // brief's `durationSeconds` x fps: a shot runs for whichever is longer, the
    // line or the part's own motion, so the requested length steers the
    // storyboard rather than truncating the film.
    assert_eq!(prepared.film_frame_count(), 234);

    for mutation in [
        // 一部影片至少要有一段
        serde_json::json!({"segments": []}),
        // 指向工作区外
        serde_json::json!({"segments": [{
            "entryHtml": "../../../etc/passwd",
            "allowedAssets": [part_asset],
            "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
            "frameCount": 144,
        }]}),
        // 入口在工作区里根本不存在
        serde_json::json!({"segments": [{
            "entryHtml": "catalog/items/absent/absent.html",
            "allowedAssets": [part_asset],
            "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
            "frameCount": 144,
        }]}),
        // 把沙箱白名单扩大到工作区之外
        serde_json::json!({"segments": [{
            "entryHtml": part_entry,
            "allowedAssets": ["../../../etc/passwd"],
            "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
            "frameCount": 144,
        }]}),
        // 画布超出渲染沙箱一帧能承受的像素
        serde_json::json!({"segments": [{
            "entryHtml": part_entry,
            "allowedAssets": [part_asset],
            "canvas": {"width": 2560, "height": 2560, "deviceScaleFactor": 3},
            "frameCount": 144,
        }]}),
        // 一段的帧数超过单次渲染的上限
        serde_json::json!({"segments": [{
            "entryHtml": part_entry,
            "allowedAssets": [part_asset],
            "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
            "frameCount": 601,
        }]}),
        // 一段没有帧
        serde_json::json!({"segments": [{
            "entryHtml": part_entry,
            "allowedAssets": [part_asset],
            "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
            "frameCount": 0,
        }]}),
        // 模板段没带它必须加载的动画运行时
        serde_json::json!({"segments": [{
            "entryHtml": MOTION_COMPOSITION_FILE,
            "allowedAssets": [part_asset],
            "canvas": {
                "width": TEMPLATE_CANVAS_WIDTH,
                "height": TEMPLATE_CANVAS_HEIGHT,
                "deviceScaleFactor": TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR,
            },
            "frameCount": 90,
        }]}),
    ] {
        let mut document = good.clone();
        for (key, value) in mutation.as_object().unwrap() {
            document[key] = value.clone();
        }
        let error = accept_authored_render_job(&store, &workspace, &request, &document.to_string())
            .expect_err(&format!("a segment this side cannot render must not become a RenderJob: {document}"));
        assert_eq!(
            error.code(),
            MotionVideoStudioErrorCode::AuthoringAnswerInvalid,
            "refusing a segment is not the renderer being unavailable: {document}"
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

/// The child cannot use the packaged parts it is never told about.
///
/// PC-04 gave every beat a `catalogParts` field and PC-16 put the 134 parts in
/// the installer; between them sat this request, which named the workspace, the
/// brief and the model and stopped there. So the agent built its catalog from
/// nothing, every beat that chose a part was refused, and the film fell back to
/// the one built-in composition — the same silence, one layer further along.
///
/// The directory name is read from the release contract rather than repeated
/// here: the installer puts the tree there, the audit requires it there, and a
/// third hand-typed copy is how the request comes to point at an empty path
/// that nothing checks.
#[test]
fn the_authoring_request_tells_the_child_where_the_packaged_parts_are() {
    use automation_tool_desktop_lib::{motion_authoring_request, MOTION_CATALOG_DIRECTORY};

    let declared: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../contracts/quality/release-package-resources.v1.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let installed = declared["resources"]
        .as_array()
        .unwrap()
        .iter()
        .find(|resource| resource["category"] == "catalog")
        .expect("the release must declare where the parts catalog is installed");
    assert_eq!(
        installed["installedParts"][0].as_str().unwrap(),
        MOTION_CATALOG_DIRECTORY,
        "the request must point at the directory the installer actually fills"
    );

    let request = MotionVideoBriefRequest::one_sentence(
        "用蓝色商务风做一段本周销售增长说明".to_owned(),
        "16:9".to_owned(),
        6,
        "zh".to_owned(),
    )
    .unwrap();
    let work = Path::new("/tmp/automation-tool-example/work");
    let catalog = Path::new("/tmp/automation-tool-example/resources").join(MOTION_CATALOG_DIRECTORY);
    let document = motion_authoring_request(work, &catalog, &request, "qwen-example", "sk-example");

    assert_eq!(document["catalogRoot"].as_str().unwrap(), catalog.to_str().unwrap());
    assert_eq!(document["workspace"].as_str().unwrap(), work.to_str().unwrap());
    assert_eq!(document["brief"].as_str().unwrap(), request.brief());
    assert_eq!(document["aspectRatio"].as_str().unwrap(), request.aspect_ratio());
    assert_eq!(document["durationSeconds"].as_u64().unwrap(), 6);
    assert_eq!(document["language"].as_str().unwrap(), request.language());
}

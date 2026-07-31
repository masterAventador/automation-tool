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

/// 一句话入口开放的每一个片长，后面每一层都得真的收得下。
///
/// 上一轮把上限从 20 抬到 180 时只改了请求校验那一处，`brief_plan` 还在拿
/// `total_seconds_maximum`（20，固定模板那条路的沙箱单次上限）去卡。于是 60 秒
/// 的请求先通过校验，再在编排跑完之后死在这里——**用户等三分钟才拿到一句
/// 「做不出来」**，而他选的正是界面允许他选的长度。
#[test]
fn every_length_the_entry_offers_is_one_the_render_plan_accepts() {
    use automation_tool_desktop_lib::motion_video_studio::duration_limits;

    let limits = duration_limits().unwrap();
    for seconds in [
        1,
        limits.total_seconds_maximum(),
        limits.brief_seconds_maximum(),
    ] {
        MotionVideoBriefRequest::one_sentence(
            "用蓝色商务风做一段本周销售增长说明".to_owned(),
            "16:9".to_owned(),
            seconds,
            "zh".to_owned(),
        )
        .unwrap_or_else(|_| panic!("{seconds} 秒是界面允许选的，请求校验必须放行"));
        limits
            .brief_plan(seconds)
            .unwrap_or_else(|_| panic!("{seconds} 秒过了请求校验，渲染计划就不能再拒它"));
    }
    assert!(limits
        .brief_plan(limits.brief_seconds_maximum() + 1)
        .is_err());
    assert!(limits.brief_plan(0).is_err());
}

/// 前端发过来的那份 JSON 少了开关，必须响亮地失败。
///
/// 这一跳（JS 对象 → serde）是整条链上唯一没有被测试盖住的。原本这个字段带
/// `#[serde(default)]`，于是漏传会静默回落到「开着」——用户明明关了，片子照样
/// 按开着跑，没有任何一处报错。App 自带前端、同一次构建产出，不存在旧调用方，
/// 所以「漏传」只可能是漏写，而不是兼容性。
#[test]
fn a_request_that_forgot_the_thinking_choice_is_refused_rather_than_defaulted() {
    let complete = serde_json::json!({
        "creationMode": "one_sentence_v1",
        "brief": "用蓝色商务风做一段本周销售增长说明",
        "aspectRatio": "16:9",
        "durationSeconds": 12,
        "language": "zh",
        "catalogPartOverrides": [],
        "modelThinking": false,
    });
    let parsed: MotionVideoBriefRequest = serde_json::from_value(complete.clone()).unwrap();
    assert!(!parsed.model_thinking(), "关掉的选择必须被解析出来");
    assert!(parsed.catalog_part_overrides().is_empty());

    let mut missing_parts = complete.clone();
    missing_parts
        .as_object_mut()
        .unwrap()
        .remove("catalogPartOverrides");
    assert!(
        serde_json::from_value::<MotionVideoBriefRequest>(missing_parts).is_err(),
        "少了逐镜头覆盖字段必须拒绝，而不是悄悄当作没有选择"
    );

    let mut missing = complete;
    missing.as_object_mut().unwrap().remove("modelThinking");
    assert!(
        serde_json::from_value::<MotionVideoBriefRequest>(missing).is_err(),
        "少了这个字段必须拒绝，而不是悄悄按默认值跑"
    );
}

#[test]
fn user_part_overrides_are_validated_and_reach_the_authoring_request() {
    use automation_tool_desktop_lib::motion_authoring_request;

    let request = MotionVideoBriefRequest::one_sentence_with_thinking_and_part_overrides(
        "用蓝色商务风做一段本周销售增长说明".to_owned(),
        "16:9".to_owned(),
        20,
        "zh".to_owned(),
        true,
        vec![Some("data-chart".to_owned()), None, None],
    )
    .unwrap();
    assert_eq!(
        request.catalog_part_overrides(),
        &[Some("data-chart".to_owned()), None, None],
    );

    let root = TempDirectory::new();
    let document = motion_authoring_request(
        &root.0,
        &root.0.join("catalog"),
        &root.0.join("chromium/chrome"),
        &root.0.join("media-toolchain/bin/ffprobe"),
        &request,
        "qwen3.7-max-2026-06-08",
        "sk-not-a-real-key",
    );
    assert_eq!(
        document["catalogPartOverrides"],
        serde_json::json!(["data-chart", null, null]),
    );

    for invalid in [vec![Some("not-a-real-part".to_owned())], vec![None, None]] {
        assert!(
            MotionVideoBriefRequest::one_sentence_with_thinking_and_part_overrides(
                "用蓝色商务风做一段本周销售增长说明".to_owned(),
                "16:9".to_owned(),
                20,
                "zh".to_owned(),
                true,
                invalid,
            )
            .is_err(),
            "未知零件或只有空槽的覆盖不得进入编排",
        );
    }
    assert!(
        MotionVideoBriefRequest::one_sentence_with_thinking_and_part_overrides(
            "用蓝色商务风做一段本周销售增长说明".to_owned(),
            "16:9".to_owned(),
            12,
            "zh".to_owned(),
            true,
            vec![Some("caption-kinetic-slam".to_owned()), None, None],
        )
        .is_err(),
        "a catalogued part without a real film slot must not reach the Executor",
    );
}

/// 深度思考的开关要一路走到编排请求里，不能在某一层被吃掉。
///
/// 这条线本周已经栽过七次「一侧改了另一侧没跟上」，其中 `catalogRoot` 那次正是
/// **协议接受了这个字段、然后把它丢掉**——两侧测试各自全绿，产品安静地少做一件事。
/// 所以这里断言的是「发出去的请求文档里有它」，不是「结构体收得下它」。
#[test]
fn the_thinking_choice_reaches_the_authoring_request() {
    use automation_tool_desktop_lib::motion_authoring_request;

    let root = TempDirectory::new();
    for thinking in [true, false] {
        let request = MotionVideoBriefRequest::one_sentence_with_thinking(
            "用蓝色商务风做一段本周销售增长说明".to_owned(),
            "16:9".to_owned(),
            12,
            "zh".to_owned(),
            thinking,
        )
        .unwrap();
        let document = motion_authoring_request(
            &root.0,
            &root.0.join("catalog"),
            &root.0.join("chromium/chrome"),
            &root.0.join("media-toolchain/bin/ffprobe"),
            &request,
            "qwen3.7-max-2026-06-08",
            "sk-not-a-real-key",
        );
        assert_eq!(
            document["modelThinking"],
            serde_json::Value::Bool(thinking),
            "编排请求必须带上这次选的深度思考开关"
        );
    }
}

#[test]
fn a_brief_is_judged_against_the_same_contracts_the_agent_reads() {
    // The one-sentence entry's own ceiling, not the template path's: this path
    // renders one shot at a time, so the sandbox's single-capture limit is not
    // what bounds it.
    let longest = automation_tool_desktop_lib::motion_video_studio::duration_limits()
        .unwrap()
        .brief_seconds_maximum();

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
            "sourceStartMillis": 0,
            "sourceEndMillis": 6000,
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
        accept_authored_render_job, advance, record_rendered_shot_frames, snapshot,
        MotionRenderJobStatus, MOTION_COMPOSITION_FILE, TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR,
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
        "part": null,
        "entryHtml": MOTION_COMPOSITION_FILE,
        "allowedAssets": [AUTHORING_RUNTIME_ASSET],
        "canvas": {
            "width": TEMPLATE_CANVAS_WIDTH,
            "height": TEMPLATE_CANVAS_HEIGHT,
            "deviceScaleFactor": TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR,
        },
        "frameCount": 90,
        // 这一段覆盖 composition 时间轴上的哪一截。模板段全都加载同一个文件，
        // 除了这个窗口以外没有任何东西能把两次渲染区分开。
        "sourceStartMillis": 0,
        "sourceEndMillis": 3000,
    });
    let part_segment = serde_json::json!({
        "part": "lt-bold-block",
        "entryHtml": part_entry,
        "allowedAssets": [part_asset],
        // The part's own stage, at factor 1: it already is the output
        // resolution, and sharpening it further costs every frame.
        "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
        "frameCount": 144,
        // 零件是自带时间轴的独立文件，窗口就是它自己那条。
        "sourceStartMillis": 0,
        "sourceEndMillis": 4800,
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
    // 每一段覆盖源文件时间轴上的哪一截，必须一路传到渲染请求里。
    // 没有它的时候，Worker 只能把整条时间轴摊到这一段要的帧数上——
    // 于是每个模板镜头都把整部片子重渲一遍。2026-07-28 留档那条 12 秒成片
    // 就是这么来的：两段一模一样的 6 秒，各自倍速。
    assert_eq!(segments[0].source_start_millis(), 0);
    assert_eq!(segments[0].source_end_millis(), 3000);
    assert_eq!(segments[1].source_end_millis(), 4800);
    // The film is as long as its shots come to. It is deliberately not the
    // brief's `durationSeconds` x fps: a shot runs for whichever is longer, the
    // line or the part's own motion, so the requested length steers the
    // storyboard rather than truncating the film.
    assert_eq!(prepared.film_frame_count(), 234);
    let persisted = serde_json::to_value(snapshot(&store, workspace.job_id()).unwrap()).unwrap();
    assert_eq!(
        persisted["shotStructure"],
        serde_json::json!([
            {
                "index": 1,
                "startFrame": 0,
                "frameCount": 90,
                "renderedStartFrame": null,
                "renderedFrameCount": null,
                "part": null,
                "narrationSeconds": null,
            },
            {
                "index": 2,
                "startFrame": 90,
                "frameCount": 144,
                "renderedStartFrame": null,
                "renderedFrameCount": null,
                "part": "lt-bold-block",
                "narrationSeconds": null,
            },
        ]),
        "T2.2: the accepted answer's shot table must survive in the RenderJob checkpoint",
    );
    advance(
        &store,
        workspace.job_id(),
        MotionRenderJobStatus::Encoding,
        85,
        None,
        None,
    )
    .unwrap();
    record_rendered_shot_frames(&store, workspace.job_id(), &[91, 145])
        .expect_err("two one-frame length drifts move the second boundary by two frames");
    let measured = record_rendered_shot_frames(&store, workspace.job_id(), &[90, 145])
        .expect("every decoded start/end boundary stays within one frame");
    let measured = serde_json::to_value(measured).unwrap();
    assert_eq!(measured["shotStructure"][1]["renderedStartFrame"], 90);
    assert_eq!(measured["shotStructure"][1]["renderedFrameCount"], 145);

    for mutation in [
        // 一部影片至少要有一段
        serde_json::json!({"segments": []}),
        // 指向工作区外
        serde_json::json!({"segments": [{
            "entryHtml": "../../../etc/passwd",
            "allowedAssets": [part_asset],
            "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
            "frameCount": 144,
            "sourceStartMillis": 0,
            "sourceEndMillis": 3000,
        }]}),
        // 入口在工作区里根本不存在
        serde_json::json!({"segments": [{
            "entryHtml": "catalog/items/absent/absent.html",
            "allowedAssets": [part_asset],
            "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
            "frameCount": 144,
            "sourceStartMillis": 0,
            "sourceEndMillis": 3000,
        }]}),
        // 把沙箱白名单扩大到工作区之外
        serde_json::json!({"segments": [{
            "entryHtml": part_entry,
            "allowedAssets": ["../../../etc/passwd"],
            "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
            "frameCount": 144,
            "sourceStartMillis": 0,
            "sourceEndMillis": 3000,
        }]}),
        // 画布超出渲染沙箱一帧能承受的像素
        serde_json::json!({"segments": [{
            "entryHtml": part_entry,
            "allowedAssets": [part_asset],
            "canvas": {"width": 2560, "height": 2560, "deviceScaleFactor": 3},
            "frameCount": 144,
            "sourceStartMillis": 0,
            "sourceEndMillis": 3000,
        }]}),
        // 一段的帧数超过单次渲染的上限
        serde_json::json!({"segments": [{
            "entryHtml": part_entry,
            "allowedAssets": [part_asset],
            "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
            "frameCount": 601,
            "sourceStartMillis": 0,
            "sourceEndMillis": 3000,
        }]}),
        // 一段没有帧
        serde_json::json!({"segments": [{
            "entryHtml": part_entry,
            "allowedAssets": [part_asset],
            "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
            "frameCount": 0,
            "sourceStartMillis": 0,
            "sourceEndMillis": 3000,
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
            "sourceStartMillis": 0,
            "sourceEndMillis": 3000,
        }]}),
        // 时间窗倒着来：结束不晚于开始，这一段没有任何时间可采
        serde_json::json!({"segments": [{
            "entryHtml": part_entry,
            "allowedAssets": [part_asset],
            "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
            "frameCount": 144,
            "sourceStartMillis": 4800,
            "sourceEndMillis": 4800,
        }]}),
        // 起点在结束之后：这一条要真的走到那句判断，所以字段名必须是对的——
        // 名字写错会被 deny_unknown_fields 提前拒掉，那条判断删了测试也照样绿。
        serde_json::json!({"segments": [{
            "entryHtml": part_entry,
            "allowedAssets": [part_asset],
            "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
            "frameCount": 144,
            "sourceStartMillis": 5000,
            "sourceEndMillis": 4800,
        }]}),
        // 小数：整份 spec 全是整数，签名绑定的规范化 JSON 容不下浮点
        serde_json::json!({"segments": [{
            "entryHtml": part_entry,
            "allowedAssets": [part_asset],
            "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
            "frameCount": 144,
            "sourceStartMillis": 0,
            "sourceEndMillis": 4800.5,
        }]}),
    ] {
        let mut document = good.clone();
        for (key, value) in mutation.as_object().unwrap() {
            document[key] = value.clone();
        }
        let error = accept_authored_render_job(&store, &workspace, &request, &document.to_string())
            .expect_err(&format!(
                "a segment this side cannot render must not become a RenderJob: {document}"
            ));
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
    let catalog =
        Path::new("/tmp/automation-tool-example/resources").join(MOTION_CATALOG_DIRECTORY);
    let browser = Path::new("/tmp/automation-tool-example/resources/chromium/chrome");
    let ffprobe = Path::new("/tmp/automation-tool-example/resources/media-toolchain/bin/ffprobe");
    let document = motion_authoring_request(
        work,
        &catalog,
        browser,
        ffprobe,
        &request,
        "qwen-example",
        "sk-example",
    );

    assert_eq!(
        document["catalogRoot"].as_str().unwrap(),
        catalog.to_str().unwrap()
    );
    assert_eq!(
        document["workspace"].as_str().unwrap(),
        work.to_str().unwrap()
    );
    assert_eq!(document["brief"].as_str().unwrap(), request.brief());
    assert_eq!(
        document["aspectRatio"].as_str().unwrap(),
        request.aspect_ratio()
    );
    assert_eq!(document["durationSeconds"].as_u64().unwrap(), 6);
    assert_eq!(document["language"].as_str().unwrap(), request.language());
    // PC-14：溢出探针启动的就是这个字段指的浏览器。catalogRoot 的教训逐字适用——
    // 协议收下字段再丢掉，两侧各自全绿，产品安静地跳过实测。
    assert_eq!(
        document["browserExecutable"].as_str().unwrap(),
        browser.to_str().unwrap()
    );
    // PC-26：旁白时长用工具链的 ffprobe 量。字段缺了不会红任何测试——
    // 子进程只会安静地出一条无声片，所以断言落在发出的请求文档上。
    assert_eq!(
        document["ffprobeExecutable"].as_str().unwrap(),
        ffprobe.to_str().unwrap()
    );
}

/// PC-26：旁白随段到达。音频必须真在工作区里，秒数必须装得进这一拍——
/// 镜头长 = max(语音, 动效) 是子进程排的，这里是它的话不再被直接采信的边界。
#[test]
fn a_narrated_segment_is_accepted_and_its_narration_reaches_the_mix() {
    use automation_tool_desktop_lib::motion_video_studio::{
        accept_authored_render_job, narration_mix_arguments, MOTION_COMPOSITION_FILE,
    };

    let root = TempDirectory::new();
    let store = store(&root.0);
    let workspace = store.create_new().unwrap();
    let work = store.worker_asset_directory(&workspace).unwrap();
    fs::create_dir_all(work.join("runtime")).unwrap();
    fs::write(work.join(AUTHORING_RUNTIME_ASSET), b"/* runtime */").unwrap();
    fs::write(work.join(MOTION_COMPOSITION_FILE), b"<html></html>").unwrap();
    let part_entry = "catalog/items/lt-bold-block/lt-bold-block.html";
    fs::create_dir_all(work.join("catalog/items/lt-bold-block")).unwrap();
    fs::write(work.join(part_entry), b"<html></html>").unwrap();
    fs::create_dir_all(work.join("narration")).unwrap();
    fs::write(work.join("narration/hook.wav"), b"RIFFfake").unwrap();

    let request = MotionVideoBriefRequest::one_sentence(
        "用蓝色商务风做一段本周销售增长说明".to_owned(),
        "16:9".to_owned(),
        6,
        "zh".to_owned(),
    )
    .unwrap();
    let segment = |narration: serde_json::Value| {
        let mut base = serde_json::json!({
            "part": "lt-bold-block",
            "entryHtml": part_entry,
            "allowedAssets": [],
            "canvas": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
            "frameCount": 144,
            "sourceStartMillis": 0,
            "sourceEndMillis": 4800,
        });
        base.as_object_mut()
            .unwrap()
            .extend(narration.as_object().unwrap().clone());
        base
    };
    let answer = |narration: serde_json::Value| {
        serde_json::json!({
            "schemaVersion": 1,
            "status": "authored",
            "entryHtml": MOTION_COMPOSITION_FILE,
            "allowedAssets": [AUTHORING_RUNTIME_ASSET],
            "frameCount": 6 * 30,
            "framesPerSecond": 30,
            "durationSeconds": 6,
            "aspectRatio": "16:9",
            "segments": [segment(narration)],
        })
        .to_string()
    };

    let narrated = serde_json::json!({
        "narrationAudio": "narration/hook.wav",
        "narrationSeconds": 4.0,
    });
    let prepared = accept_authored_render_job(&store, &workspace, &request, &answer(narrated))
        .expect("a narrated segment whose audio exists and fits its shot is accepted");
    let segments = prepared.segments();
    assert_eq!(segments[0].narration_audio(), Some("narration/hook.wav"));

    // 混音参数：每条旁白铺在它自己镜头的起点；无声片不混（None）。
    let film = Path::new("/tmp/automation-tool-example/film.mp4");
    let output = Path::new("/tmp/automation-tool-example/film-voiced.mp4");
    let arguments =
        narration_mix_arguments(film, &work, segments, 30, output).expect("narrated film mixes");
    let rendered = arguments
        .iter()
        .map(|argument| argument.to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join(" ");
    assert!(
        rendered.contains("adelay=0|0"),
        "第一镜的旁白从 0ms 开始: {rendered}"
    );
    assert!(
        rendered.contains("amix"),
        "多条旁白要混在同一条音轨上: {rendered}"
    );
    assert!(
        rendered.contains("narration/hook.wav"),
        "音频输入必须是工作区里那个文件"
    );
    assert!(
        rendered.contains("-c:v copy"),
        "视频流原样穿透，混音不许重编码画面"
    );

    for mutation in [
        // 只带音频不带秒数——两者是一对
        serde_json::json!({"narrationAudio": "narration/hook.wav"}),
        // 秒数装不进这一拍：144 帧 @30fps 只有 4.8 秒
        serde_json::json!({"narrationAudio": "narration/hook.wav", "narrationSeconds": 9.5}),
        // 音频在工作区里不存在
        serde_json::json!({"narrationAudio": "narration/absent.wav", "narrationSeconds": 4.0}),
        // 音频路径爬出工作区
        serde_json::json!({"narrationAudio": "../../../etc/passwd", "narrationSeconds": 4.0}),
    ] {
        accept_authored_render_job(&store, &workspace, &request, &answer(mutation))
            .expect_err("a narration the workspace cannot honour must be refused");
    }
}

/// 两镜两条旁白：第二条要铺在第一镜结束的位置，不是零点；无声片不混。
#[test]
fn the_second_narration_starts_where_the_first_shot_ends() {
    use automation_tool_desktop_lib::motion_video_studio::{
        accept_authored_render_job, narration_mix_arguments, MOTION_COMPOSITION_FILE,
        TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR, TEMPLATE_CANVAS_HEIGHT, TEMPLATE_CANVAS_WIDTH,
    };

    let root = TempDirectory::new();
    let store = store(&root.0);
    let workspace = store.create_new().unwrap();
    let work = store.worker_asset_directory(&workspace).unwrap();
    fs::create_dir_all(work.join("runtime")).unwrap();
    fs::write(work.join(AUTHORING_RUNTIME_ASSET), b"/* runtime */").unwrap();
    fs::write(work.join(MOTION_COMPOSITION_FILE), b"<html></html>").unwrap();
    fs::create_dir_all(work.join("narration")).unwrap();
    fs::write(work.join("narration/a.wav"), b"RIFFfake").unwrap();
    fs::write(work.join("narration/b.wav"), b"RIFFfake").unwrap();

    let request = MotionVideoBriefRequest::one_sentence(
        "用蓝色商务风做一段本周销售增长说明".to_owned(),
        "16:9".to_owned(),
        6,
        "zh".to_owned(),
    )
    .unwrap();
    let canvas = serde_json::json!({
        "width": TEMPLATE_CANVAS_WIDTH,
        "height": TEMPLATE_CANVAS_HEIGHT,
        "deviceScaleFactor": TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR,
    });
    let answer = serde_json::json!({
        "schemaVersion": 1,
        "status": "authored",
        "entryHtml": MOTION_COMPOSITION_FILE,
        "allowedAssets": [AUTHORING_RUNTIME_ASSET],
        "frameCount": 6 * 30,
        "framesPerSecond": 30,
        "durationSeconds": 6,
        "aspectRatio": "16:9",
        "segments": [
            {
                "entryHtml": MOTION_COMPOSITION_FILE,
                "allowedAssets": [AUTHORING_RUNTIME_ASSET],
                "canvas": canvas.clone(),
                "frameCount": 90,
                "sourceStartMillis": 0,
                "sourceEndMillis": 3000,
                "narrationAudio": "narration/a.wav",
                "narrationSeconds": 2.5,
            },
            {
                "entryHtml": MOTION_COMPOSITION_FILE,
                "allowedAssets": [AUTHORING_RUNTIME_ASSET],
                "canvas": canvas,
                "frameCount": 90,
                "sourceStartMillis": 3000,
                "sourceEndMillis": 6000,
                "narrationAudio": "narration/b.wav",
                "narrationSeconds": 2.5,
            },
        ],
    });
    let prepared = accept_authored_render_job(&store, &workspace, &request, &answer.to_string())
        .expect("two narrated template shots are accepted");

    let arguments = narration_mix_arguments(
        Path::new("/tmp/film.mp4"),
        &work,
        prepared.segments(),
        30,
        Path::new("/tmp/film-voiced.mp4"),
    )
    .expect("both shots narrated");
    let rendered = arguments
        .iter()
        .map(|argument| argument.to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join(" ");
    // 第一镜 90 帧 @30fps = 3000ms，第二条旁白从这里开始。
    assert!(rendered.contains("adelay=0|0"), "{rendered}");
    assert!(rendered.contains("adelay=3000|3000"), "{rendered}");
}

/// The progress the render loop reports must be progress the job will accept.
///
/// It was not. `run_motion_render_job` divides the rendering band among the
/// shots so a nine-shot film does not sit on one number for ten minutes, and
/// the state machine still required exactly 55 — so `advance` refused the very
/// first shot's progress (index 0 gives 5, for any number of shots) and every
/// render failed as `RenderFailed` before a browser was ever launched. 100% of
/// runs, single shot or nine.
///
/// It survived a 450-test suite because `run_motion_render_job` is a private
/// function on a spawned thread behind an `AppHandle` and nothing covered it.
/// This test covers the boundary that actually broke: the loop's number and the
/// job's judgement of it, which is one rule and used to be written down three
/// times.
#[test]
fn every_progress_the_render_loop_reports_is_one_the_job_accepts() {
    use automation_tool_desktop_lib::motion_video_studio::{
        accept_authored_render_job, advance, rendering_progress_percent, MotionRenderJobStatus,
        MOTION_COMPOSITION_FILE,
    };

    let root = TempDirectory::new();
    let store = store(&root.0);
    let request = MotionVideoBriefRequest::one_sentence(
        "用蓝色商务风做一段本周销售增长说明".to_owned(),
        "16:9".to_owned(),
        6,
        "zh".to_owned(),
    )
    .unwrap();

    for total in [1_usize, 2, 3, 5, 9, 20] {
        // A real job, produced the way production produces one: the Queued
        // snapshot `advance` reads is written by accepting an answer, and a
        // bare workspace has none.
        let workspace = store.create_new().unwrap();
        let work = store.worker_asset_directory(&workspace).unwrap();
        fs::create_dir_all(work.join("runtime")).unwrap();
        fs::write(work.join(AUTHORING_RUNTIME_ASSET), b"/* runtime */").unwrap();
        fs::write(work.join(MOTION_COMPOSITION_FILE), b"<html></html>").unwrap();
        let answer = serde_json::json!({
            "schemaVersion": 1,
            "status": "authored",
            "entryHtml": MOTION_COMPOSITION_FILE,
            "allowedAssets": [AUTHORING_RUNTIME_ASSET],
            "frameCount": 6 * 30,
            "framesPerSecond": 30,
            "durationSeconds": 6,
            "aspectRatio": "16:9",
            "segments": (0..total).map(|_| serde_json::json!({
                "entryHtml": MOTION_COMPOSITION_FILE,
                "allowedAssets": [AUTHORING_RUNTIME_ASSET],
                "canvas": {"width": 640, "height": 360, "deviceScaleFactor": 2},
                "frameCount": 30,
                "sourceStartMillis": 0,
                "sourceEndMillis": 1000,
            })).collect::<Vec<_>>(),
        });
        let prepared =
            accept_authored_render_job(&store, &workspace, &request, &answer.to_string())
                .expect("the answer is accepted");
        assert_eq!(prepared.segments().len(), total);
        let job = workspace.job_id();
        let mut previous = 0_u8;
        for index in 0..total {
            let percent = rendering_progress_percent(index, total);
            assert!(
                percent >= previous,
                "a shot may not move the bar backwards: {total} shots, shot {index}"
            );
            previous = percent;
            advance(
                &store,
                job,
                MotionRenderJobStatus::Rendering,
                percent,
                None,
                None,
            )
            .unwrap_or_else(|error| {
                panic!(
                    "the job refused the progress its own render loop reports: \
                     {total} shots, shot {index}, {percent}%, {error:?}"
                )
            });
        }
        // The encode stage still owns the top of the band, so no shot may reach
        // it — a bar that hits 85 while shots are still being captured tells the
        // person watching that the render finished.
        assert!(
            previous < 85,
            "{total} shots ran the bar into the encode stage"
        );
    }
}

/// The two paths stopped sharing a ceiling when one of them stopped being one render.
///
/// `totalSecondsMaximum` is 20 because a film used to be a single capture and
/// the sandbox stops at 600 frames. The fixed-template path still is one
/// capture, so 20 is still its answer. The one-sentence path is route A — one
/// render per shot, joined — and its ceiling is a product decision rather than
/// a sandbox limit; the product owner set it at 180 seconds on 2026-07-28 so
/// the operator can choose, having measured that the 12 second default made the
/// model decline every catalog part as too expensive for the budget.
///
/// Keeping one number for both would have raised the template path's films to
/// 5400 frames in a single capture, which the sandbox refuses.
#[test]
fn the_one_sentence_ceiling_is_the_products_and_the_template_ceiling_is_the_sandboxs() {
    use automation_tool_desktop_lib::motion_video_studio::duration_limits;

    let limits = duration_limits().unwrap();

    assert_eq!(
        limits.total_seconds_maximum() * limits.frames_per_second(),
        600,
        "the template path is still one capture and must fit the sandbox"
    );
    assert_eq!(
        limits.brief_seconds_maximum(),
        180,
        "the one-sentence ceiling the operator may choose up to"
    );

    MotionVideoBriefRequest::one_sentence(
        "用蓝色商务风做一段本周销售增长说明".to_owned(),
        "16:9".to_owned(),
        limits.brief_seconds_maximum(),
        "zh".to_owned(),
    )
    .expect("the longest film the product offers is accepted");

    let error = MotionVideoBriefRequest::one_sentence(
        "用蓝色商务风做一段本周销售增长说明".to_owned(),
        "16:9".to_owned(),
        limits.brief_seconds_maximum() + 1,
        "zh".to_owned(),
    )
    .expect_err("one second past the ceiling is refused");
    assert_eq!(error.code(), MotionVideoStudioErrorCode::DraftInvalid);
}

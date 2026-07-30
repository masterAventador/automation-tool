use automation_tool_desktop_lib::local_video_orchestrator::{
    VideoWorkerRenderCanvas, VideoWorkerRenderSandboxRequest, VideoWorkerSourceWindow,
};
use automation_tool_desktop_lib::motion_video_studio::{
    advance, cancel, cancel_marker_file_name, cancellation_requested, delete_artifact,
    duration_limits, import_rendered_output, prepare_manual_render_job, render_sandbox_budget,
    rendered_film_is_static, snapshot, MotionRenderFailureCode, MotionRenderJobStatus,
    MotionVideoBeatDraft, MotionVideoDraftRequest, MotionVideoStudioErrorCode,
    TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR, TEMPLATE_CANVAS_HEIGHT, TEMPLATE_CANVAS_WIDTH,
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
            "automation-tool-bm08-{}-{}-{}",
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

/// The seconds-per-beat every fixture below uses; the storyboard length each
/// test asserts is derived from it rather than restated.
const FIXTURE_SECONDS_PER_BEAT: u32 = 4;

fn draft() -> MotionVideoDraftRequest {
    MotionVideoDraftRequest::manual_template(
        "新品发布".to_owned(),
        "blue-professional".to_owned(),
        "#1234ab".to_owned(),
        "#f2eadb".to_owned(),
        FIXTURE_SECONDS_PER_BEAT,
        vec![
            MotionVideoBeatDraft::new("增长看得见".to_owned(), "字幕：本周销售增长 38%".to_owned()),
            MotionVideoBeatDraft::new("来自续费".to_owned(), "字幕：客户持续选择我们".to_owned()),
            MotionVideoBeatDraft::new("下一步行动".to_owned(), "字幕：立即查看新版能力".to_owned()),
        ],
        None,
    )
    .unwrap()
}

fn beats(count: usize) -> Vec<MotionVideoBeatDraft> {
    (1..=count)
        .map(|index| {
            MotionVideoBeatDraft::new(format!("第{index}个亮点"), format!("字幕：第{index}段说明"))
        })
        .collect()
}

fn sized_draft(
    seconds_per_beat: u32,
    beat_count: usize,
) -> Result<
    MotionVideoDraftRequest,
    automation_tool_desktop_lib::motion_video_studio::MotionVideoStudioError,
> {
    MotionVideoDraftRequest::manual_template(
        "新品发布".to_owned(),
        "blue-professional".to_owned(),
        "#1234ab".to_owned(),
        "#f2eadb".to_owned(),
        seconds_per_beat,
        beats(beat_count),
        None,
    )
}

#[test]
fn user_configured_beat_count_and_seconds_per_beat_drive_the_whole_storyboard() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    let request = sized_draft(4, 5).expect("five four-second beats are inside the declared budget");
    let prepared = prepare_manual_render_job(&store, &request).unwrap();

    assert_eq!(prepared.total_seconds(), 20);
    assert_eq!(prepared.frames_per_second(), 30);
    assert_eq!(prepared.frame_count(), 600);

    let workspace = store.open(prepared.render_job_id()).unwrap();
    let assets = store.worker_asset_directory(&workspace).unwrap();

    let storyboard: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(assets.join("STORYBOARD.json")).unwrap()).unwrap();
    assert_eq!(storyboard["durationSeconds"], 20);
    let storyboard_beats = storyboard["beats"].as_array().unwrap();
    assert_eq!(storyboard_beats.len(), 5);
    for (index, beat) in storyboard_beats.iter().enumerate() {
        assert_eq!(beat["startSeconds"], index as u64 * 4, "beat {index} start");
        assert_eq!(beat["durationSeconds"], 4, "beat {index} duration");
    }

    let render_job: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(assets.join("renderjob.json")).unwrap()).unwrap();
    assert_eq!(render_job["durationSeconds"], 20);
    assert_eq!(render_job["frameCount"], 600);

    let html = fs::read_to_string(assets.join("composition.html")).unwrap();
    assert!(
        html.contains("data-duration=\"20\""),
        "the captured timeline length must follow the configured storyboard"
    );
    assert!(
        !html.contains("2.999"),
        "the seek clamp must not stay pinned to the retired three second timeline"
    );
    assert_eq!(
        html.matches("data-track-index=").count(),
        5,
        "every configured beat needs its own scene"
    );
}

#[test]
fn the_declared_duration_limits_reject_out_of_range_beats_seconds_and_their_combination() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    let limits = duration_limits().expect("the storyboard duration contract must load");

    assert!(
        sized_draft(4, 5).is_ok(),
        "five four-second beats must stay admissible"
    );
    for (seconds_per_beat, beat_count, why) in [
        (
            4_u32,
            limits.beat_count_minimum() as usize - 1,
            "no beats at all",
        ),
        (
            4,
            limits.beat_count_maximum() as usize + 1,
            "one beat past the maximum",
        ),
        (
            limits.seconds_per_beat_minimum() - 1,
            3,
            "zero seconds per beat",
        ),
        (
            limits.seconds_per_beat_maximum() + 1,
            3,
            "one second past the maximum",
        ),
        (
            6,
            6,
            "two in-range factors whose product exceeds the total budget",
        ),
    ] {
        let error = sized_draft(seconds_per_beat, beat_count).expect_err(why);
        assert_eq!(
            error.code(),
            MotionVideoStudioErrorCode::DraftInvalid,
            "{why}"
        );
    }
    assert!(
        store.list_workspaces().unwrap().is_empty(),
        "a rejected storyboard must never reach the workspace store"
    );
}

#[test]
fn the_render_sandbox_budget_follows_the_frame_count_instead_of_a_fixed_number() {
    let root = TempDirectory::new();
    let limits = duration_limits().expect("the storyboard duration contract must load");
    let shortest_frames = limits.frames_per_second() * limits.seconds_per_beat_minimum();
    let longest_frames = limits.frame_count_maximum();
    let shortest = render_sandbox_budget(shortest_frames).expect("shortest budget");
    let longest = render_sandbox_budget(longest_frames).expect("longest budget");

    assert!(
        longest.wall_seconds() > shortest.wall_seconds(),
        "a twenty second render cannot share the wall budget of a one second render"
    );
    assert!(
        longest.cpu_seconds() > longest.wall_seconds(),
        "CPU seconds accrue once per busy core and must not be pinned to the wall budget"
    );

    // Both ends must be admissible to the real sandbox constructor, otherwise a
    // legal user configuration would fail as an opaque configuration error.
    for (frames, budget) in [(shortest_frames, shortest), (longest_frames, longest)] {
        VideoWorkerRenderSandboxRequest::new(
            root.0.clone(),
            "composition.html".to_owned(),
            cancel_marker_file_name().unwrap().to_owned(),
            VideoWorkerRenderCanvas::new(
                TEMPLATE_CANVAS_WIDTH,
                TEMPLATE_CANVAS_HEIGHT,
                TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR,
            )
            .expect("the template canvas is inside the declared bounds"),
            VideoWorkerSourceWindow::new(0, 6_000).expect("a window inside the declared bounds"),
            Vec::new(),
            frames,
            budget.wall_seconds(),
            budget.cpu_seconds(),
            2048,
            256 * 1024 * 1024,
        )
        .unwrap_or_else(|error| {
            panic!("the sandbox refused the derived budget for {frames} frames: {error:?}")
        });
    }
}

#[test]
fn manual_template_freezes_editable_copy_and_seekable_composition_in_private_render_job() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    let prepared = prepare_manual_render_job(&store, &draft()).unwrap();

    let workspace = store.open(prepared.render_job_id()).unwrap();
    let assets = store.worker_asset_directory(&workspace).unwrap();
    let html = fs::read_to_string(assets.join("composition.html")).unwrap();
    assert!(html.contains("增长看得见"));
    assert!(html.contains("字幕：本周销售增长 38%"));
    assert!(html.contains("window.__timelines"));
    assert!(html.contains(&format!(
        "data-duration=\"{}\"",
        3 * FIXTURE_SECONDS_PER_BEAT
    )));
    assert!(!html.contains("http://"));
    assert!(!html.contains("https://"));

    let freeze = fs::read_to_string(assets.join("style-freeze.json")).unwrap();
    assert!(freeze.contains("\"stylePresetId\":\"blue-professional\""));
    assert!(freeze.contains("\"sourceFrameSha256\""));
    assert_eq!(prepared.total_seconds(), 3 * FIXTURE_SECONDS_PER_BEAT);
    assert_eq!(prepared.frames_per_second(), 30);
    assert_eq!(prepared.frame_count(), 3 * FIXTURE_SECONDS_PER_BEAT * 30);
}

#[test]
fn manual_template_rejects_active_content_and_incomplete_storyboards_before_workspace_creation() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    let invalid = MotionVideoDraftRequest::manual_template(
        "新品发布".to_owned(),
        "blue-professional".to_owned(),
        "#1234ab".to_owned(),
        "#f2eadb".to_owned(),
        FIXTURE_SECONDS_PER_BEAT,
        vec![MotionVideoBeatDraft::new(
            "<script>fetch('https://evil.invalid')</script>".to_owned(),
            "字幕".to_owned(),
        )],
        None,
    );
    assert!(invalid.is_err());
    assert!(store.list_workspaces().unwrap().is_empty());
}

#[test]
fn artifact_import_removes_the_working_copy_and_user_delete_removes_the_only_video() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    let prepared = prepare_manual_render_job(&store, &draft()).unwrap();
    let workspace = store.open(prepared.render_job_id()).unwrap();
    let output = store.worker_output_directory(&workspace).unwrap();
    let working_video = output.join("brand-motion-result.mp4");
    fs::write(&working_video, b"verified-mp4-payload").unwrap();

    let artifact = import_rendered_output(&store, prepared.render_job_id()).unwrap();
    assert!(
        !working_video.exists(),
        "the private working copy must not survive atomic Artifact import"
    );
    advance(
        &store,
        prepared.render_job_id(),
        MotionRenderJobStatus::Succeeded,
        100,
        Some(&artifact),
        None,
    )
    .unwrap();
    delete_artifact(&store, artifact.artifact_id()).unwrap();

    assert!(store.list_artifacts().unwrap().is_empty());
    assert!(!working_video.exists());
}

#[test]
fn bm16_all_twelve_locked_styles_freeze_seekable_compositions() {
    let contract: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../contracts/video/motion-style-freeze.v1.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let presets: Vec<String> = contract["presets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|preset| preset["id"].as_str().unwrap().to_owned())
        .collect();
    assert_eq!(
        presets.len(),
        12,
        "locked style contract must expose 12 presets"
    );

    let export = std::env::var_os("AUTOMATION_TOOL_BM16_STYLE_SWEEP_DIR").map(PathBuf::from);
    if let Some(directory) = &export {
        fs::create_dir_all(directory).unwrap();
    }

    let root = TempDirectory::new();
    let store = store(&root.0);
    for preset in &presets {
        let request = MotionVideoDraftRequest::manual_template(
            format!("风格验收 {preset}"),
            preset.clone(),
            "#1234ab".to_owned(),
            "#f2eadb".to_owned(),
            FIXTURE_SECONDS_PER_BEAT,
            vec![
                MotionVideoBeatDraft::new(
                    "增长看得见".to_owned(),
                    "字幕：本周销售增长 38%".to_owned(),
                ),
                MotionVideoBeatDraft::new(
                    "来自续费".to_owned(),
                    "字幕：客户持续选择我们".to_owned(),
                ),
                MotionVideoBeatDraft::new(
                    "下一步行动".to_owned(),
                    "字幕：立即查看新版能力".to_owned(),
                ),
            ],
            None,
        )
        .unwrap_or_else(|error| panic!("style {preset} rejected a valid draft: {error:?}"));
        let prepared = prepare_manual_render_job(&store, &request)
            .unwrap_or_else(|error| panic!("style {preset} failed to freeze: {error:?}"));
        let workspace = store.open(prepared.render_job_id()).unwrap();
        let assets = store.worker_asset_directory(&workspace).unwrap();
        let html = fs::read_to_string(assets.join("composition.html")).unwrap();
        assert!(
            html.contains("window.__timelines"),
            "style {preset} lost the timeline"
        );
        assert!(
            html.contains(&format!(
                "data-duration=\"{}\"",
                3 * FIXTURE_SECONDS_PER_BEAT
            )),
            "style {preset} lost the configured duration"
        );
        assert!(
            !html.contains("http://") && !html.contains("https://"),
            "style {preset} leaked a remote reference"
        );
        let freeze = fs::read_to_string(assets.join("style-freeze.json")).unwrap();
        assert!(
            freeze.contains(&format!("\"stylePresetId\":\"{preset}\"")),
            "style {preset} freeze metadata drifted"
        );
        if let Some(directory) = &export {
            fs::write(directory.join(format!("{preset}.html")), &html).unwrap();
        }
    }
}

/// Frames that never change mean the film is a still image. The render worker
/// reports success either way — it captured exactly the frames it was asked
/// for — and FFmpeg happily encodes them into a well-formed MP4. This gate is
/// the only place between "render succeeded" and "here is your video" that can
/// tell the difference, so it is what keeps a canvas or timeline mismatch loud.
fn write_frames(directory: &Path, frame_count: u32, distinct: bool) {
    fs::create_dir_all(directory).unwrap();
    for index in 1..=frame_count {
        let body = if distinct {
            format!("frame payload {index}")
        } else {
            "frame payload constant".to_owned()
        };
        fs::write(
            directory.join(format!("frame-{index:05}.png")),
            body.as_bytes(),
        )
        .unwrap();
    }
}

#[test]
fn a_film_whose_sampled_frames_all_match_is_reported_as_static() {
    let root = TempDirectory::new();
    let frames = root.0.join("frames");
    write_frames(&frames, 180, false);

    assert!(rendered_film_is_static(&frames, 180).unwrap());
}

#[test]
fn a_film_with_moving_frames_is_not_reported_as_static() {
    let root = TempDirectory::new();
    let frames = root.0.join("frames");
    write_frames(&frames, 180, true);

    assert!(!rendered_film_is_static(&frames, 180).unwrap());
}

#[test]
fn a_single_frame_film_is_never_called_static() {
    let root = TempDirectory::new();
    let frames = root.0.join("frames");
    write_frames(&frames, 1, false);

    assert!(!rendered_film_is_static(&frames, 1).unwrap());
}

#[test]
fn a_film_that_only_moves_in_the_middle_is_not_reported_as_static() {
    let root = TempDirectory::new();
    let frames = root.0.join("frames");
    write_frames(&frames, 180, false);
    fs::write(frames.join("frame-00090.png"), b"a different middle frame").unwrap();

    assert!(!rendered_film_is_static(&frames, 180).unwrap());
}

#[test]
fn a_missing_frame_is_a_storage_failure_rather_than_a_verdict() {
    // The gap has to fall inside a run of identical frames: that is the only
    // shape where staying silent would let an incomplete capture be reported
    // as a settled "static" verdict instead of the storage fault it is.
    let root = TempDirectory::new();
    let frames = root.0.join("frames");
    write_frames(&frames, 180, false);
    fs::remove_file(frames.join("frame-00090.png")).unwrap();

    let error = rendered_film_is_static(&frames, 180).unwrap_err();
    assert_eq!(error.code(), MotionVideoStudioErrorCode::StorageUnavailable);
}

#[test]
fn a_static_render_reaches_the_user_as_its_own_failure_code() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    let prepared = prepare_manual_render_job(&store, &draft()).unwrap();
    let job = prepared.render_job_id();

    advance(
        &store,
        job,
        MotionRenderJobStatus::Rendering,
        55,
        None,
        None,
    )
    .unwrap();
    let snapshot = advance(
        &store,
        job,
        MotionRenderJobStatus::Failed,
        55,
        None,
        Some(MotionRenderFailureCode::StaticRender),
    )
    .unwrap();

    assert_eq!(snapshot.status(), MotionRenderJobStatus::Failed);
    assert_eq!(
        serde_json::to_value(MotionRenderFailureCode::StaticRender).unwrap(),
        serde_json::json!("static_render"),
    );
}

/// The App creates exactly the file the contract declares, and the render
/// request hands that same name to the Worker.
///
/// The two used to be separate literals — `MOTION_CANCEL_FILE` here and
/// `SANDBOX_CANCEL_FILE` in `worker.mjs`. Editing one was invisible: the button
/// still answers and the job still settles, and only the render keeps going.
/// See `contracts/video/motion-render-cancel-marker.v1.json`.
#[test]
fn the_cancellation_marker_written_on_disk_is_the_one_the_contract_declares() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    let prepared = prepare_manual_render_job(&store, &draft()).unwrap();
    let job = prepared.render_job_id();

    let declared: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../contracts/video/motion-render-cancel-marker.v1.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let declared = declared["markerFileName"].as_str().unwrap();
    assert_eq!(cancel_marker_file_name().unwrap(), declared);

    assert!(!cancellation_requested(&store, job).unwrap());
    cancel(&store, job).unwrap();

    let workspace = store.open(job).unwrap();
    let assets = store.worker_asset_directory(&workspace).unwrap();
    assert!(
        assets.join(declared).is_file(),
        "the cancellation marker must be the declared file name",
    );
    assert!(cancellation_requested(&store, job).unwrap());
}

/// A marker name that could leave the RenderJob is refused before a render
/// starts, so the Worker is never handed a path it would stat outside its
/// workspace.
///
/// Containment is the transport's rule and it holds for any workspace-relative
/// path, nested ones included. Staying at the workspace root is this product's
/// narrower rule, and it is enforced where the name is resolved rather than
/// where it is transported — so both are asserted, in the layer that owns each.
#[test]
fn a_cancellation_marker_that_escapes_the_workspace_is_not_a_render_request() {
    let root = TempDirectory::new();
    for escaping in ["../outside", "/absolute", "", ".", "..", "a\\b"] {
        assert!(
            VideoWorkerRenderSandboxRequest::new(
                root.0.clone(),
                "composition.html".to_owned(),
                escaping.to_owned(),
                VideoWorkerRenderCanvas::new(
                    TEMPLATE_CANVAS_WIDTH,
                    TEMPLATE_CANVAS_HEIGHT,
                    TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR,
                )
                .expect("the template canvas is inside the declared bounds"),
                VideoWorkerSourceWindow::new(0, 6_000)
                    .expect("a window inside the declared bounds"),
                Vec::new(),
                90,
                20,
                20,
                2048,
                256 * 1024 * 1024,
            )
            .is_err(),
            "a marker of {escaping:?} must not produce a render request",
        );
    }

    let declared = cancel_marker_file_name().unwrap();
    assert!(
        !declared.is_empty() && !declared.contains(['/', '\\']),
        "the declared marker is one file at the workspace root, never a path: {declared:?}",
    );
}

/// Pressing cancel records that a stop was *asked for*. It does not settle the
/// job — only the executor that owns the browser and the encoder can say the
/// work has actually stopped.
///
/// This is `CLAUDE.md` §4.4 applied where it bites: while the App claimed
/// 已取消 the render Worker was still capturing frames and FFmpeg was still
/// writing an MP4. Both notice within a frame, so nobody watched the clock —
/// but the claim was untrue for as long as it took, and the state machine
/// treated it as final, which is what let the next test's film go missing.
#[test]
fn cancelling_records_the_request_and_waits_for_the_executor_to_confirm() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    let prepared = prepare_manual_render_job(&store, &draft()).unwrap();
    let job = prepared.render_job_id();
    advance(
        &store,
        job,
        MotionRenderJobStatus::Rendering,
        55,
        None,
        None,
    )
    .unwrap();

    cancel(&store, job).unwrap();

    let requested = snapshot(&store, job).unwrap();
    assert_eq!(
        requested.status(),
        MotionRenderJobStatus::Cancelling,
        "the command may say a stop was requested, never that it has happened",
    );
    assert!(
        cancellation_requested(&store, job).unwrap(),
        "the executor has to be able to see the request",
    );

    // A second press is the same request, not an error and not a second state.
    cancel(&store, job).unwrap();
    assert_eq!(
        snapshot(&store, job).unwrap().status(),
        MotionRenderJobStatus::Cancelling,
    );

    let settled = advance(
        &store,
        job,
        MotionRenderJobStatus::Cancelled,
        55,
        None,
        None,
    )
    .unwrap();
    assert_eq!(settled.status(), MotionRenderJobStatus::Cancelled);
}

/// Once a stop has been asked for, the run may only settle. Letting a stage
/// advance out of it would put the cancel button back on a job that is already
/// stopping and reset the card to 正在合成视频.
#[test]
fn a_requested_cancellation_cannot_be_walked_back_into_a_running_stage() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    let prepared = prepare_manual_render_job(&store, &draft()).unwrap();
    let job = prepared.render_job_id();
    advance(
        &store,
        job,
        MotionRenderJobStatus::Rendering,
        55,
        None,
        None,
    )
    .unwrap();
    cancel(&store, job).unwrap();

    assert!(
        advance(&store, job, MotionRenderJobStatus::Encoding, 85, None, None).is_err(),
        "a stage may not start after the operator asked for a stop",
    );
    assert_eq!(
        snapshot(&store, job).unwrap().status(),
        MotionRenderJobStatus::Cancelling,
    );
}

/// A film whose encode had already finished when the cancel arrived is still
/// the user's film.
///
/// This is the concrete cost of settling the job in the command. FFmpeg exits
/// successfully, the render thread imports the MP4, and the import lands after
/// the snapshot has gone terminal — so the `Succeeded` write is dropped, the
/// job reads 已取消 and the artifact it produced is referenced by nothing. It
/// is not listed on the artifacts page, cannot be played and cannot be deleted,
/// while still counting against the workspace quota.
#[test]
fn a_film_that_finished_before_the_executor_saw_the_cancel_is_not_lost() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    let prepared = prepare_manual_render_job(&store, &draft()).unwrap();
    let job = prepared.render_job_id();
    advance(
        &store,
        job,
        MotionRenderJobStatus::Rendering,
        55,
        None,
        None,
    )
    .unwrap();
    advance(&store, job, MotionRenderJobStatus::Encoding, 85, None, None).unwrap();
    let workspace = store.open(job).unwrap();
    let output = store.worker_output_directory(&workspace).unwrap();
    fs::write(
        output.join("brand-motion-result.mp4"),
        b"verified-mp4-payload",
    )
    .unwrap();

    // The operator presses cancel in the window between FFmpeg exiting and the
    // render thread importing what it produced.
    cancel(&store, job).unwrap();
    let artifact = import_rendered_output(&store, job).unwrap();
    let settled = advance(
        &store,
        job,
        MotionRenderJobStatus::Succeeded,
        100,
        Some(&artifact),
        None,
    )
    .unwrap();

    assert_eq!(
        settled.status(),
        MotionRenderJobStatus::Succeeded,
        "the work was already done when the request arrived; saying otherwise loses the film",
    );
    let projected = serde_json::to_value(&settled).unwrap();
    assert_eq!(
        projected["artifactId"],
        serde_json::json!(artifact.artifact_id().to_string()),
        "the film has to be reachable from the job the user is looking at",
    );
    assert_eq!(
        store.list_artifacts().unwrap().len(),
        1,
        "and there must be no second, unreferenced copy",
    );
}

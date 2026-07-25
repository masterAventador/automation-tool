use automation_tool_desktop_lib::motion_video_studio::{
    advance, delete_artifact, import_rendered_output, prepare_manual_render_job,
    MotionRenderJobStatus, MotionVideoBeatDraft, MotionVideoDraftRequest,
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

fn draft() -> MotionVideoDraftRequest {
    MotionVideoDraftRequest::manual_template(
        "新品发布".to_owned(),
        "blue-professional".to_owned(),
        "#1234ab".to_owned(),
        "#f2eadb".to_owned(),
        vec![
            MotionVideoBeatDraft::new("增长看得见".to_owned(), "字幕：本周销售增长 38%".to_owned()),
            MotionVideoBeatDraft::new("来自续费".to_owned(), "字幕：客户持续选择我们".to_owned()),
            MotionVideoBeatDraft::new("下一步行动".to_owned(), "字幕：立即查看新版能力".to_owned()),
        ],
        None,
    )
    .unwrap()
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
    assert!(html.contains("data-duration=\"3\""));
    assert!(!html.contains("http://"));
    assert!(!html.contains("https://"));

    let freeze = fs::read_to_string(assets.join("style-freeze.json")).unwrap();
    assert!(freeze.contains("\"stylePresetId\":\"blue-professional\""));
    assert!(freeze.contains("\"sourceFrameSha256\""));
    assert_eq!(prepared.frame_count(), 90);
    assert_eq!(prepared.frames_per_second(), 30);
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
    assert_eq!(presets.len(), 12, "locked style contract must expose 12 presets");

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
        assert!(html.contains("window.__timelines"), "style {preset} lost the timeline");
        assert!(html.contains("data-duration=\"3\""), "style {preset} lost the duration");
        assert!(!html.contains("http://") && !html.contains("https://"),
            "style {preset} leaked a remote reference");
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

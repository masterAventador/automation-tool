use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::video_editing_workspace::{
    CreateEditingProjectRequest, EditingTimelineDraft, TimelineClip, TimelineTrack,
    TimelineTrackKind, VideoEditingWorkspace, VideoEditingWorkspaceErrorCode,
};
use uuid::Uuid;

const ARTIFACT_ID: &str = "00000000-0000-4000-8000-000000000103";
static TEMPORARY_SEQUENCE: AtomicU64 = AtomicU64::new(1);

struct TemporaryRoot(PathBuf);

impl TemporaryRoot {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-t4-editing-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            TEMPORARY_SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        std::fs::create_dir(&path).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        }
        Self(std::fs::canonicalize(path).unwrap())
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TemporaryRoot {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn draft() -> EditingTimelineDraft {
    EditingTimelineDraft {
        duration_ms: 3_000,
        tracks: vec![TimelineTrack {
            track_id: "track-1".to_owned(),
            kind: TimelineTrackKind::Visual,
            clips: vec![TimelineClip {
                clip_id: "clip-1".to_owned(),
                start_ms: 0,
                duration_ms: 3_000,
                source_artifact_id: Some(Uuid::parse_str(ARTIFACT_ID).unwrap()),
                text: None,
                transition_in: None,
            }],
        }],
    }
}

#[test]
fn project_and_timeline_survive_reopening_the_app_store() {
    let root = TemporaryRoot::new();
    let workspace = VideoEditingWorkspace::initialize(root.path()).unwrap();
    let project = workspace
        .create_project(CreateEditingProjectRequest {
            title: "新品精剪".to_owned(),
            source_artifact_ids: vec![Uuid::parse_str(ARTIFACT_ID).unwrap()],
        })
        .unwrap();
    let first = workspace
        .save_timeline(project.project_id, draft())
        .unwrap();
    assert_eq!(first.revision, 1);

    drop(workspace);
    let reopened = VideoEditingWorkspace::initialize(root.path()).unwrap();
    let reopened_projects = reopened.list_projects().unwrap();
    assert_eq!(reopened_projects.len(), 1);
    assert_eq!(reopened_projects[0].project_id, project.project_id);
    assert_eq!(reopened_projects[0].title, project.title);
    assert_eq!(
        reopened_projects[0].source_artifact_ids,
        project.source_artifact_ids
    );
    assert!(reopened_projects[0].updated_at >= project.updated_at);
    assert_eq!(
        reopened.get_timeline(project.project_id).unwrap(),
        Some(first.clone())
    );
    let second = reopened.save_timeline(project.project_id, draft()).unwrap();
    assert_eq!(second.timeline_id, first.timeline_id);
    assert_eq!(second.revision, 2);
}

#[test]
fn invalid_projects_and_timelines_fail_closed_without_mutating_state() {
    let root = TemporaryRoot::new();
    let workspace = VideoEditingWorkspace::initialize(root.path()).unwrap();

    let invalid = workspace
        .create_project(CreateEditingProjectRequest {
            title: " ".to_owned(),
            source_artifact_ids: vec![],
        })
        .unwrap_err();
    assert_eq!(
        invalid.code(),
        VideoEditingWorkspaceErrorCode::InvalidProject
    );
    assert!(workspace.list_projects().unwrap().is_empty());

    let missing = workspace
        .save_timeline(
            Uuid::parse_str("00000000-0000-4000-8000-000000000199").unwrap(),
            draft(),
        )
        .unwrap_err();
    assert_eq!(
        missing.code(),
        VideoEditingWorkspaceErrorCode::InvalidProject
    );
}

#[test]
fn editing_submission_does_not_invent_a_cloud_job_before_the_adapter_is_connected() {
    let root = TemporaryRoot::new();
    let workspace = VideoEditingWorkspace::initialize(root.path()).unwrap();
    let project = workspace
        .create_project(CreateEditingProjectRequest {
            title: "新品精剪".to_owned(),
            source_artifact_ids: vec![Uuid::parse_str(ARTIFACT_ID).unwrap()],
        })
        .unwrap();
    workspace
        .save_timeline(project.project_id, draft())
        .unwrap();

    let failure = workspace
        .submit_editing_job(project.project_id)
        .unwrap_err();
    assert_eq!(
        failure.code(),
        VideoEditingWorkspaceErrorCode::EditingServiceUnavailable
    );
    assert!(workspace
        .list_editing_jobs(project.project_id)
        .unwrap()
        .is_empty());
}

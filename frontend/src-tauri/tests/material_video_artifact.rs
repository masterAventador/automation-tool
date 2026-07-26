//! Reading a finished smart-material film back out of the App-owned store.
//!
//! The brand-motion line has had this since BM-08, which is why its films can
//! be played inside the App. Smart-material films are imported into the very
//! same artifact store, with the same media type and the same `rendered_video`
//! role, and yet nothing could read one back: the studio offered only "delete"
//! and "publish". A user who had just made a video could not look at it.

use automation_tool_desktop_lib::material_video_studio::read_artifact;
use automation_tool_desktop_lib::video_job_workspace::{
    VideoJobWorkspacePolicy, VideoJobWorkspaceStore,
};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use automation_tool_desktop_lib::video_job_workspace::generate_uuid_v4;
use uuid::Uuid;

static TEMPORARY_SEQUENCE: AtomicU64 = AtomicU64::new(1);

/// The file name the WebUI observation bridge copies a finished film to.
const MATERIAL_OUTPUT_FILE: &str = "material-result.mp4";

struct TempDirectory(PathBuf);

impl TempDirectory {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-t36-{}-{}-{}",
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

/// Import one finished film exactly the way `reconcile_active_observation`
/// does, and answer with the artifact id the studio would project.
fn imported_film(store: &VideoJobWorkspaceStore, payload: &[u8]) -> Uuid {
    let workspace = store.create_new().unwrap();
    let output = store.worker_output_directory(&workspace).unwrap();
    fs::write(output.join(MATERIAL_OUTPUT_FILE), payload).unwrap();
    store
        .import_output(&workspace, MATERIAL_OUTPUT_FILE, "video/mp4", "rendered_video")
        .unwrap()
        .artifact_id()
}

#[test]
fn a_finished_smart_material_film_can_be_read_back_for_in_app_playback() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    let payload = b"verified-material-mp4-payload";
    let artifact_id = imported_film(&store, payload);

    let read = read_artifact(&store, artifact_id).unwrap();

    assert_eq!(read.artifact_id(), artifact_id);
    assert_eq!(read.media_type(), "video/mp4");
    assert_eq!(
        read.base64(),
        "dmVyaWZpZWQtbWF0ZXJpYWwtbXA0LXBheWxvYWQ=",
        "the player is fed a data URL, so the payload has to survive verbatim"
    );
}

#[test]
fn an_unknown_smart_material_artifact_is_refused_instead_of_guessed() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    imported_film(&store, b"verified-material-mp4-payload");

    let error = read_artifact(&store, generate_uuid_v4().unwrap()).unwrap_err();

    assert_eq!(
        error.code(),
        automation_tool_desktop_lib::material_video_studio::MaterialVideoStudioErrorCode::JobUnavailable
    );
}

/// The store also holds staged publish copies and, in future, other roles. A
/// reader that answered with any file it can find would hand the player
/// whatever else the workspace happens to carry.
#[test]
fn only_a_rendered_video_artifact_is_readable() {
    let root = TempDirectory::new();
    let store = store(&root.0);
    let workspace = store.create_new().unwrap();
    let output = store.worker_output_directory(&workspace).unwrap();
    fs::write(output.join("diagnostic.mp4"), b"not-a-finished-film").unwrap();
    let other = store
        .import_output(&workspace, "diagnostic.mp4", "video/mp4", "diagnostic_capture")
        .unwrap()
        .artifact_id();

    let error = read_artifact(&store, other).unwrap_err();

    assert_eq!(
        error.code(),
        automation_tool_desktop_lib::material_video_studio::MaterialVideoStudioErrorCode::JobUnavailable
    );
}

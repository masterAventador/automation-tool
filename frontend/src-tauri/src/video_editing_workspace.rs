//! Durable, provider-neutral editing projects and timeline drafts.
//!
//! T4 moves the workbench out of WebView `sessionStorage`. This store owns one
//! bounded JSON document below Tauri `app_data_dir`, validates every value on
//! both read and write, and replaces it atomically. Cloud submission remains
//! fail-closed until the provider execution adapter is connected; this module
//! never invents an `EditingJob` merely to make the UI look operational.

use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::fmt::{self, Display, Formatter};
use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;
use uuid::{Uuid, Variant};

use crate::video_job_workspace::generate_uuid_v4;

const STORE_DIRECTORY: &str = "video-editing-workspace-v1";
const STATE_FILE: &str = "state.json";
const STATE_VERSION: u8 = 1;
const MAX_STATE_BYTES: u64 = 16 * 1024 * 1024;
const MAX_PROJECTS: usize = 1_024;
const MAX_PROJECT_TITLE_CHARACTERS: usize = 200;
const MAX_SOURCE_ARTIFACTS: usize = 256;
const MAX_VIDEO_DURATION_MS: u64 = 600_000;
const MIN_TIMELINE_DURATION_MS: u64 = 100;
const MAX_TRACKS: usize = 32;
const MAX_CLIPS_PER_TRACK: usize = 512;
const MAX_TRANSITION_DURATION_MS: u64 = 10_000;
const MAX_JOB_ARTIFACT_REFERENCES: usize = 64;
static TEMPORARY_SEQUENCE: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VideoEditingWorkspaceErrorCode {
    InvalidProject,
    InvalidTimeline,
    DraftStorageUnavailable,
    EditingServiceUnavailable,
}

impl VideoEditingWorkspaceErrorCode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::InvalidProject => "invalid_project",
            Self::InvalidTimeline => "invalid_timeline",
            Self::DraftStorageUnavailable => "draft_storage_unavailable",
            Self::EditingServiceUnavailable => "editing_service_unavailable",
        }
    }

    pub const fn retryable(self) -> bool {
        matches!(self, Self::DraftStorageUnavailable)
    }
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct VideoEditingWorkspaceError {
    code: VideoEditingWorkspaceErrorCode,
}

impl VideoEditingWorkspaceError {
    const fn new(code: VideoEditingWorkspaceErrorCode) -> Self {
        Self { code }
    }

    pub const fn code(self) -> VideoEditingWorkspaceErrorCode {
        self.code
    }
}

impl fmt::Debug for VideoEditingWorkspaceError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VideoEditingWorkspaceError")
            .field("code", &self.code)
            .finish()
    }
}

impl Display for VideoEditingWorkspaceError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str("video editing operation unavailable")
    }
}

impl std::error::Error for VideoEditingWorkspaceError {}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CreateEditingProjectRequest {
    pub title: String,
    pub source_artifact_ids: Vec<Uuid>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EditingProjectSnapshot {
    pub project_id: Uuid,
    pub title: String,
    pub source_artifact_ids: Vec<Uuid>,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum TimelineTrackKind {
    Visual,
    Audio,
    Caption,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum TimelineTransitionKind {
    Cut,
    Fade,
    Dissolve,
    Wipe,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TimelineTransition {
    pub kind: TimelineTransitionKind,
    pub duration_ms: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TimelineClip {
    pub clip_id: String,
    pub start_ms: u64,
    pub duration_ms: u64,
    pub source_artifact_id: Option<Uuid>,
    pub text: Option<String>,
    pub transition_in: Option<TimelineTransition>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TimelineTrack {
    pub track_id: String,
    pub kind: TimelineTrackKind,
    pub clips: Vec<TimelineClip>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EditingTimelineDraft {
    pub duration_ms: u64,
    pub tracks: Vec<TimelineTrack>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EditingTimelineSnapshot {
    pub timeline_id: Uuid,
    pub project_id: Uuid,
    pub revision: u32,
    pub duration_ms: u64,
    pub tracks: Vec<TimelineTrack>,
    pub created_at: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EditingJobStatus {
    Queued,
    Running,
    Paused,
    Cancelling,
    Succeeded,
    Failed,
    Cancelled,
    OutcomeUncertain,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EditingFailureCode {
    InvalidInput,
    DependencyUnavailable,
    ResourceExhausted,
    EditingFailed,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EditingJobSnapshot {
    pub editing_job_id: Uuid,
    pub project_id: Uuid,
    pub timeline_id: Uuid,
    pub timeline_revision: u32,
    pub status: EditingJobStatus,
    pub input_artifact_ids: Vec<Uuid>,
    pub output_artifact_ids: Vec<Uuid>,
    pub failure_code: Option<EditingFailureCode>,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct StoredState {
    version: u8,
    projects: Vec<EditingProjectSnapshot>,
    timelines: HashMap<Uuid, EditingTimelineSnapshot>,
    jobs: HashMap<Uuid, Vec<EditingJobSnapshot>>,
}

impl Default for StoredState {
    fn default() -> Self {
        Self {
            version: STATE_VERSION,
            projects: Vec::new(),
            timelines: HashMap::new(),
            jobs: HashMap::new(),
        }
    }
}

pub struct VideoEditingWorkspace {
    directory: PathBuf,
    state_path: PathBuf,
    state: Mutex<StoredState>,
}

impl fmt::Debug for VideoEditingWorkspace {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str("VideoEditingWorkspace")
    }
}

impl VideoEditingWorkspace {
    pub fn initialize(app_data_directory: &Path) -> Result<Self, VideoEditingWorkspaceError> {
        if !app_data_directory.is_absolute() {
            return Err(storage_unavailable());
        }
        require_private_directory(app_data_directory)?;
        let directory = app_data_directory.join(STORE_DIRECTORY);
        ensure_private_directory(&directory)?;
        let state_path = directory.join(STATE_FILE);
        let state = load_state(&state_path)?;
        validate_state(&state)?;
        Ok(Self {
            directory,
            state_path,
            state: Mutex::new(state),
        })
    }

    pub fn list_projects(&self) -> Result<Vec<EditingProjectSnapshot>, VideoEditingWorkspaceError> {
        Ok(self.lock_state()?.projects.clone())
    }

    pub fn create_project(
        &self,
        request: CreateEditingProjectRequest,
    ) -> Result<EditingProjectSnapshot, VideoEditingWorkspaceError> {
        validate_title(&request.title)?;
        validate_artifact_ids(&request.source_artifact_ids, MAX_SOURCE_ARTIFACTS)
            .map_err(|_| invalid_project())?;
        let mut guard = self.lock_state()?;
        if guard.projects.len() >= MAX_PROJECTS {
            return Err(invalid_project());
        }
        let now = utc_timestamp();
        let project = EditingProjectSnapshot {
            project_id: generate_uuid_v4().map_err(|_| storage_unavailable())?,
            title: request.title,
            source_artifact_ids: request.source_artifact_ids,
            created_at: now.clone(),
            updated_at: now,
        };
        validate_project(&project)?;
        let mut candidate = guard.clone();
        candidate.projects.push(project.clone());
        self.commit(&candidate)?;
        *guard = candidate;
        Ok(project)
    }

    pub fn get_timeline(
        &self,
        project_id: Uuid,
    ) -> Result<Option<EditingTimelineSnapshot>, VideoEditingWorkspaceError> {
        let guard = self.lock_state()?;
        require_project(&guard, project_id)?;
        Ok(guard.timelines.get(&project_id).cloned())
    }

    pub fn save_timeline(
        &self,
        project_id: Uuid,
        draft: EditingTimelineDraft,
    ) -> Result<EditingTimelineSnapshot, VideoEditingWorkspaceError> {
        validate_timeline_draft(&draft)?;
        let mut guard = self.lock_state()?;
        require_project(&guard, project_id)?;
        let previous = guard.timelines.get(&project_id);
        let snapshot = EditingTimelineSnapshot {
            timeline_id: match previous {
                Some(value) => value.timeline_id,
                None => generate_uuid_v4().map_err(|_| storage_unavailable())?,
            },
            project_id,
            revision: previous.map_or(Ok(1), |value| {
                value.revision.checked_add(1).ok_or_else(invalid_timeline)
            })?,
            duration_ms: draft.duration_ms,
            tracks: draft.tracks,
            created_at: utc_timestamp(),
        };
        validate_timeline(&snapshot)?;
        let mut candidate = guard.clone();
        candidate.timelines.insert(project_id, snapshot.clone());
        if let Some(project) = candidate
            .projects
            .iter_mut()
            .find(|project| project.project_id == project_id)
        {
            project.updated_at = snapshot.created_at.clone();
        }
        self.commit(&candidate)?;
        *guard = candidate;
        Ok(snapshot)
    }

    pub fn list_editing_jobs(
        &self,
        project_id: Uuid,
    ) -> Result<Vec<EditingJobSnapshot>, VideoEditingWorkspaceError> {
        let guard = self.lock_state()?;
        require_project(&guard, project_id)?;
        Ok(guard.jobs.get(&project_id).cloned().unwrap_or_default())
    }

    pub fn submit_editing_job(
        &self,
        project_id: Uuid,
    ) -> Result<EditingJobSnapshot, VideoEditingWorkspaceError> {
        let guard = self.lock_state()?;
        require_project(&guard, project_id)?;
        if !guard.timelines.contains_key(&project_id) {
            return Err(invalid_timeline());
        }
        Err(VideoEditingWorkspaceError::new(
            VideoEditingWorkspaceErrorCode::EditingServiceUnavailable,
        ))
    }

    fn lock_state(
        &self,
    ) -> Result<std::sync::MutexGuard<'_, StoredState>, VideoEditingWorkspaceError> {
        self.state.lock().map_err(|_| storage_unavailable())
    }

    fn commit(&self, state: &StoredState) -> Result<(), VideoEditingWorkspaceError> {
        validate_state(state)?;
        let payload = serde_json::to_vec(state).map_err(|_| storage_unavailable())?;
        if payload.len() as u64 > MAX_STATE_BYTES {
            return Err(storage_unavailable());
        }
        atomic_write(&self.directory, &self.state_path, &payload)
    }
}

fn validate_state(state: &StoredState) -> Result<(), VideoEditingWorkspaceError> {
    if state.version != STATE_VERSION || state.projects.len() > MAX_PROJECTS {
        return Err(storage_unavailable());
    }
    let mut project_ids = HashSet::new();
    for project in &state.projects {
        validate_project(project).map_err(|_| storage_unavailable())?;
        if !project_ids.insert(project.project_id) {
            return Err(storage_unavailable());
        }
    }
    if state.timelines.len() > state.projects.len() || state.jobs.len() > state.projects.len() {
        return Err(storage_unavailable());
    }
    for (project_id, timeline) in &state.timelines {
        if *project_id != timeline.project_id || !project_ids.contains(project_id) {
            return Err(storage_unavailable());
        }
        validate_timeline(timeline).map_err(|_| storage_unavailable())?;
    }
    for (project_id, jobs) in &state.jobs {
        if !project_ids.contains(project_id)
            || jobs.len() > MAX_PROJECTS
            || jobs.iter().any(|job| {
                job.project_id != *project_id
                    || !valid_uuid_v4(job.editing_job_id)
                    || !valid_uuid_v4(job.timeline_id)
                    || job.timeline_revision == 0
                    || validate_artifact_ids(&job.input_artifact_ids, MAX_JOB_ARTIFACT_REFERENCES)
                        .is_err()
                    || job.input_artifact_ids.is_empty()
                    || validate_artifact_ids(&job.output_artifact_ids, MAX_JOB_ARTIFACT_REFERENCES)
                        .is_err()
                    || job
                        .input_artifact_ids
                        .iter()
                        .any(|input| job.output_artifact_ids.contains(input))
                    || !valid_timestamp(&job.created_at)
                    || !valid_timestamp(&job.updated_at)
                    || !valid_job_outcome(job)
            })
        {
            return Err(storage_unavailable());
        }
    }
    Ok(())
}

fn valid_job_outcome(job: &EditingJobSnapshot) -> bool {
    match job.status {
        EditingJobStatus::Succeeded => {
            !job.output_artifact_ids.is_empty() && job.failure_code.is_none()
        }
        EditingJobStatus::Failed => {
            job.output_artifact_ids.is_empty() && job.failure_code.is_some()
        }
        _ => job.output_artifact_ids.is_empty() && job.failure_code.is_none(),
    }
}

fn validate_project(project: &EditingProjectSnapshot) -> Result<(), VideoEditingWorkspaceError> {
    if !valid_uuid_v4(project.project_id)
        || !valid_timestamp(&project.created_at)
        || !valid_timestamp(&project.updated_at)
    {
        return Err(invalid_project());
    }
    validate_title(&project.title)?;
    validate_artifact_ids(&project.source_artifact_ids, MAX_SOURCE_ARTIFACTS)
        .map_err(|_| invalid_project())
}

fn validate_title(value: &str) -> Result<(), VideoEditingWorkspaceError> {
    let characters = value.chars().count();
    if value.trim() != value
        || characters == 0
        || characters > MAX_PROJECT_TITLE_CHARACTERS
        || value.chars().any(|character| {
            character.is_control()
                || matches!(
                    character,
                    '\u{00ad}'
                        | '\u{061c}'
                        | '\u{200b}'..='\u{200f}'
                        | '\u{202a}'..='\u{202e}'
                        | '\u{2060}'..='\u{206f}'
                        | '\u{feff}'
                )
        })
    {
        return Err(invalid_project());
    }
    Ok(())
}

fn validate_artifact_ids(values: &[Uuid], maximum: usize) -> Result<(), ()> {
    if values.len() > maximum
        || values.iter().any(|value| !valid_uuid_v4(*value))
        || values.iter().collect::<HashSet<_>>().len() != values.len()
    {
        return Err(());
    }
    Ok(())
}

fn validate_timeline(timeline: &EditingTimelineSnapshot) -> Result<(), VideoEditingWorkspaceError> {
    if !valid_uuid_v4(timeline.timeline_id)
        || !valid_uuid_v4(timeline.project_id)
        || timeline.revision == 0
        || !valid_timestamp(&timeline.created_at)
    {
        return Err(invalid_timeline());
    }
    validate_timeline_draft(&EditingTimelineDraft {
        duration_ms: timeline.duration_ms,
        tracks: timeline.tracks.clone(),
    })
}

fn validate_timeline_draft(draft: &EditingTimelineDraft) -> Result<(), VideoEditingWorkspaceError> {
    if !(MIN_TIMELINE_DURATION_MS..=MAX_VIDEO_DURATION_MS).contains(&draft.duration_ms)
        || draft.tracks.is_empty()
        || draft.tracks.len() > MAX_TRACKS
        || !draft
            .tracks
            .iter()
            .any(|track| track.kind == TimelineTrackKind::Visual)
    {
        return Err(invalid_timeline());
    }
    let mut track_ids = HashSet::new();
    for track in &draft.tracks {
        if !valid_local_id(&track.track_id)
            || !track_ids.insert(track.track_id.as_str())
            || track.clips.is_empty()
            || track.clips.len() > MAX_CLIPS_PER_TRACK
        {
            return Err(invalid_timeline());
        }
        let mut clip_ids = HashSet::new();
        let mut previous_end = 0_u64;
        for clip in &track.clips {
            let end = clip
                .start_ms
                .checked_add(clip.duration_ms)
                .ok_or_else(invalid_timeline)?;
            let source_valid = clip.source_artifact_id.is_some_and(valid_uuid_v4);
            let text_valid = clip.text.as_deref().is_some_and(|text| {
                !text.is_empty() && text.chars().count() <= 2_000 && text.trim() == text
            });
            let shape_valid = match track.kind {
                TimelineTrackKind::Caption => !source_valid && text_valid,
                TimelineTrackKind::Visual | TimelineTrackKind::Audio => {
                    source_valid && clip.text.is_none()
                }
            };
            if !valid_local_id(&clip.clip_id)
                || !clip_ids.insert(clip.clip_id.as_str())
                || clip.duration_ms == 0
                || clip.duration_ms > MAX_VIDEO_DURATION_MS
                || clip.start_ms < previous_end
                || end > draft.duration_ms
                || !shape_valid
                || clip.transition_in.as_ref().is_some_and(|transition| {
                    transition.duration_ms == 0
                        || transition.duration_ms > MAX_TRANSITION_DURATION_MS
                })
            {
                return Err(invalid_timeline());
            }
            previous_end = end;
        }
    }
    Ok(())
}

fn valid_local_id(value: &str) -> bool {
    let bytes = value.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 64
        && bytes[0].is_ascii_lowercase()
        && bytes
            .iter()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'-')
}

fn require_project(
    state: &StoredState,
    project_id: Uuid,
) -> Result<(), VideoEditingWorkspaceError> {
    if !valid_uuid_v4(project_id)
        || !state
            .projects
            .iter()
            .any(|project| project.project_id == project_id)
    {
        return Err(invalid_project());
    }
    Ok(())
}

fn load_state(path: &Path) -> Result<StoredState, VideoEditingWorkspaceError> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(StoredState::default()),
        Err(_) => return Err(storage_unavailable()),
    };
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err(storage_unavailable());
    }
    #[cfg(unix)]
    require_private_file(&metadata)?;
    if metadata.len() == 0 || metadata.len() > MAX_STATE_BYTES {
        return Err(storage_unavailable());
    }
    let payload = fs::read(path).map_err(|_| storage_unavailable())?;
    serde_json::from_slice(&payload).map_err(|_| storage_unavailable())
}

fn atomic_write(
    directory: &Path,
    destination: &Path,
    payload: &[u8],
) -> Result<(), VideoEditingWorkspaceError> {
    require_private_directory(directory)?;
    let sequence = TEMPORARY_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let temporary = directory.join(format!(".state-{}-{sequence}.tmp", std::process::id()));
    let result = (|| {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options
            .open(&temporary)
            .map_err(|_| storage_unavailable())?;
        file.write_all(payload)
            .and_then(|()| file.sync_all())
            .map_err(|_| storage_unavailable())?;
        drop(file);
        atomic_replace(&temporary, destination)?;
        sync_directory(directory)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

#[cfg(not(target_os = "windows"))]
fn atomic_replace(source: &Path, destination: &Path) -> Result<(), VideoEditingWorkspaceError> {
    fs::rename(source, destination).map_err(|_| storage_unavailable())
}

#[cfg(target_os = "windows")]
fn atomic_replace(source: &Path, destination: &Path) -> Result<(), VideoEditingWorkspaceError> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };
    let wide = |path: &Path| {
        path.as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>()
    };
    let source = wide(source);
    let destination = wide(destination);
    let moved = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if moved == 0 {
        return Err(storage_unavailable());
    }
    Ok(())
}

fn ensure_private_directory(path: &Path) -> Result<(), VideoEditingWorkspaceError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
                return Err(storage_unavailable());
            }
        }
        Err(error) if error.kind() == ErrorKind::NotFound => {
            fs::create_dir(path).map_err(|_| storage_unavailable())?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(path, fs::Permissions::from_mode(0o700))
                    .map_err(|_| storage_unavailable())?;
            }
        }
        Err(_) => return Err(storage_unavailable()),
    }
    require_private_directory(path)
}

fn require_private_directory(path: &Path) -> Result<(), VideoEditingWorkspaceError> {
    let metadata = fs::symlink_metadata(path).map_err(|_| storage_unavailable())?;
    if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
        return Err(storage_unavailable());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Err(storage_unavailable());
        }
    }
    Ok(())
}

#[cfg(unix)]
fn require_private_file(metadata: &fs::Metadata) -> Result<(), VideoEditingWorkspaceError> {
    use std::os::unix::fs::PermissionsExt;
    if metadata.permissions().mode() & 0o177 != 0 {
        return Err(storage_unavailable());
    }
    Ok(())
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<(), VideoEditingWorkspaceError> {
    File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(|_| storage_unavailable())
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<(), VideoEditingWorkspaceError> {
    Ok(())
}

fn valid_uuid_v4(value: Uuid) -> bool {
    value.get_version_num() == 4 && value.get_variant() == Variant::RFC4122
}

fn valid_timestamp(value: &str) -> bool {
    value.ends_with('Z') && OffsetDateTime::parse(value, &Rfc3339).is_ok()
}

fn utc_timestamp() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let days = now / 86_400;
    let seconds_of_day = now % 86_400;
    let mut year = 1970_i64;
    let mut remaining = days as i64;
    loop {
        let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
        let length = if leap { 366 } else { 365 };
        if remaining < length {
            break;
        }
        remaining -= length;
        year += 1;
    }
    let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
    let month_lengths = [
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    let mut month = 1;
    for length in month_lengths {
        if remaining < length {
            break;
        }
        remaining -= length;
        month += 1;
    }
    format!(
        "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z",
        day = remaining + 1,
        hour = seconds_of_day / 3600,
        minute = (seconds_of_day % 3600) / 60,
        second = seconds_of_day % 60,
    )
}

const fn invalid_project() -> VideoEditingWorkspaceError {
    VideoEditingWorkspaceError::new(VideoEditingWorkspaceErrorCode::InvalidProject)
}

const fn invalid_timeline() -> VideoEditingWorkspaceError {
    VideoEditingWorkspaceError::new(VideoEditingWorkspaceErrorCode::InvalidTimeline)
}

const fn storage_unavailable() -> VideoEditingWorkspaceError {
    VideoEditingWorkspaceError::new(VideoEditingWorkspaceErrorCode::DraftStorageUnavailable)
}

//! Native brand-motion draft, RenderJob and Artifact boundary.
//!
//! The no-model path is deliberately a fixed, declared-variable template. It
//! never calls an authoring model and never claims that variable replacement
//! is one-sentence generation. The generated HTML remains untrusted and goes
//! through the BM-04 renderer sandbox.

use crate::video_job_workspace::{
    VideoArtifactRecord, VideoJobWorkspace, VideoJobWorkspaceStore, VideoWorkspaceDisposition,
    VideoWorkspaceError,
};
use base64::Engine;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fmt;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use uuid::Uuid;

pub const MOTION_RENDER_JOB_CHECKPOINT: &str = "motion-render-job";
pub const MOTION_CANCEL_FILE: &str = ".automation-tool-cancel";
pub const MOTION_OUTPUT_FILE: &str = "brand-motion-result.mp4";
pub const MOTION_COMPOSITION_FILE: &str = "composition.html";
pub const MOTION_FRAMES_PER_SECOND: u32 = 30;
const MAX_TEXT_CHARS: usize = 160;
const MAX_SUBJECT_CHARS: usize = 80;
const MAX_LOGO_BYTES: usize = 4 * 1024 * 1024;
const MAX_ARTIFACT_READ_BYTES: u64 = 32 * 1024 * 1024;
const MILLIS_PER_SECOND: u32 = 1000;
const STYLE_CONTRACT: &str = include_str!("../../../contracts/video/motion-style-freeze.v1.json");
const DURATION_CONTRACT: &str =
    include_str!("../../../contracts/video/motion-storyboard-duration.v1.json");

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MotionVideoStudioErrorCode {
    DraftInvalid,
    JobUnavailable,
    RenderUnavailable,
    StorageUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct MotionVideoStudioError {
    code: MotionVideoStudioErrorCode,
    retryable: bool,
}

impl MotionVideoStudioError {
    pub const fn code(self) -> MotionVideoStudioErrorCode {
        self.code
    }
}

impl fmt::Display for MotionVideoStudioError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Brand motion video operation is unavailable")
    }
}

impl std::error::Error for MotionVideoStudioError {}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MotionVideoBeatDraft {
    title: String,
    caption: String,
}

impl MotionVideoBeatDraft {
    pub fn new(title: String, caption: String) -> Self {
        Self { title, caption }
    }

    fn validate(&self) -> Result<(), MotionVideoStudioError> {
        validate_copy(&self.title, MAX_TEXT_CHARS)?;
        validate_copy(&self.caption, MAX_TEXT_CHARS)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MotionVideoLogoDraft {
    file_name: String,
    media_type: String,
    bytes: Vec<u8>,
}

impl MotionVideoLogoDraft {
    fn validated_file_name(&self) -> Result<&'static str, MotionVideoStudioError> {
        if self.file_name.is_empty()
            || self.file_name.chars().count() > 128
            || self.file_name.contains(['/', '\\', '\0'])
            || self.bytes.is_empty()
            || self.bytes.len() > MAX_LOGO_BYTES
        {
            return Err(draft_invalid());
        }
        let lower = self.file_name.to_ascii_lowercase();
        match self.media_type.as_str() {
            "image/png"
                if lower.ends_with(".png") && self.bytes.starts_with(b"\x89PNG\r\n\x1a\n") =>
            {
                Ok("logo.png")
            }
            "image/jpeg"
                if (lower.ends_with(".jpg") || lower.ends_with(".jpeg"))
                    && self.bytes.starts_with(b"\xff\xd8\xff") =>
            {
                Ok("logo.jpg")
            }
            "image/webp"
                if lower.ends_with(".webp")
                    && self.bytes.starts_with(b"RIFF")
                    && self.bytes.get(8..12) == Some(b"WEBP") =>
            {
                Ok("logo.webp")
            }
            _ => Err(draft_invalid()),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MotionVideoDraftRequest {
    creation_mode: String,
    subject: String,
    style_preset_id: String,
    primary_color: String,
    secondary_color: String,
    seconds_per_beat: u32,
    beats: Vec<MotionVideoBeatDraft>,
    logo: Option<MotionVideoLogoDraft>,
}

impl MotionVideoDraftRequest {
    pub fn manual_template(
        subject: String,
        style_preset_id: String,
        primary_color: String,
        secondary_color: String,
        seconds_per_beat: u32,
        beats: Vec<MotionVideoBeatDraft>,
        logo: Option<MotionVideoLogoDraft>,
    ) -> Result<Self, MotionVideoStudioError> {
        let value = Self {
            creation_mode: "manual_template_v1".to_owned(),
            subject,
            style_preset_id,
            primary_color,
            secondary_color,
            seconds_per_beat,
            beats,
            logo,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(LockedStyle, MotionStoryboardPlan), MotionVideoStudioError> {
        let plan = duration_limits()?.plan(self.beats.len(), self.seconds_per_beat)?;
        if self.creation_mode != "manual_template_v1"
            || !valid_color(&self.primary_color)
            || !valid_color(&self.secondary_color)
        {
            return Err(draft_invalid());
        }
        validate_copy(&self.subject, MAX_SUBJECT_CHARS)?;
        for beat in &self.beats {
            beat.validate()?;
        }
        if let Some(logo) = &self.logo {
            logo.validated_file_name()?;
        }
        Ok((locked_style(&self.style_preset_id)?, plan))
    }
}

/// The user-configured shape of one brand-motion film. Every frame count,
/// timeline length and render budget in this module is derived from a plan; no
/// caller may restate a duration of its own.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MotionStoryboardPlan {
    beat_count: u32,
    seconds_per_beat: u32,
    frames_per_second: u32,
}

impl MotionStoryboardPlan {
    pub const fn beat_count(&self) -> u32 {
        self.beat_count
    }

    pub const fn seconds_per_beat(&self) -> u32 {
        self.seconds_per_beat
    }

    pub const fn frames_per_second(&self) -> u32 {
        self.frames_per_second
    }

    pub const fn total_seconds(&self) -> u32 {
        self.beat_count * self.seconds_per_beat
    }

    pub const fn frame_count(&self) -> u32 {
        self.total_seconds() * self.frames_per_second
    }
}

/// The declared, contract-backed range a user may configure a storyboard in.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MotionDurationLimits {
    frames_per_second: u32,
    beat_count_minimum: u32,
    beat_count_maximum: u32,
    seconds_per_beat_minimum: u32,
    seconds_per_beat_maximum: u32,
    total_seconds_maximum: u32,
    render_wall_seconds_base: u32,
    render_wall_millis_per_frame: u32,
    render_cpu_parallelism: u32,
}

impl MotionDurationLimits {
    pub const fn frames_per_second(&self) -> u32 {
        self.frames_per_second
    }

    pub const fn beat_count_minimum(&self) -> u32 {
        self.beat_count_minimum
    }

    pub const fn beat_count_maximum(&self) -> u32 {
        self.beat_count_maximum
    }

    pub const fn seconds_per_beat_minimum(&self) -> u32 {
        self.seconds_per_beat_minimum
    }

    pub const fn seconds_per_beat_maximum(&self) -> u32 {
        self.seconds_per_beat_maximum
    }

    pub const fn total_seconds_maximum(&self) -> u32 {
        self.total_seconds_maximum
    }

    pub const fn frame_count_maximum(&self) -> u32 {
        self.total_seconds_maximum * self.frames_per_second
    }

    /// Both factors must be in range and so must their product: a beat count
    /// and a beat length that are each legal can still ask for a film the
    /// render sandbox cannot capture.
    pub fn plan(
        &self,
        beat_count: usize,
        seconds_per_beat: u32,
    ) -> Result<MotionStoryboardPlan, MotionVideoStudioError> {
        let beat_count = u32::try_from(beat_count).map_err(|_| draft_invalid())?;
        if !(self.beat_count_minimum..=self.beat_count_maximum).contains(&beat_count)
            || !(self.seconds_per_beat_minimum..=self.seconds_per_beat_maximum)
                .contains(&seconds_per_beat)
            || beat_count
                .checked_mul(seconds_per_beat)
                .is_none_or(|total| total > self.total_seconds_maximum)
        {
            return Err(draft_invalid());
        }
        Ok(MotionStoryboardPlan {
            beat_count,
            seconds_per_beat,
            frames_per_second: self.frames_per_second,
        })
    }
}

/// The wall-clock and CPU seconds one render sandbox run may occupy. Both are
/// derived from how many frames the film actually has, so a longer film is not
/// killed as a stall and a shorter one does not reserve a budget it cannot use.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MotionRenderSandboxBudget {
    wall_seconds: u32,
    cpu_seconds: u32,
}

impl MotionRenderSandboxBudget {
    pub const fn wall_seconds(&self) -> u32 {
        self.wall_seconds
    }

    pub const fn cpu_seconds(&self) -> u32 {
        self.cpu_seconds
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DurationContract {
    schema_version: u8,
    id: String,
    version: String,
    policy: String,
    frames_per_second: u32,
    beat_count_minimum: u32,
    beat_count_maximum: u32,
    beat_count_default: u32,
    seconds_per_beat_minimum: u32,
    seconds_per_beat_maximum: u32,
    seconds_per_beat_default: u32,
    total_seconds_maximum: u32,
    render_wall_seconds_base: u32,
    render_wall_millis_per_frame: u32,
    render_cpu_parallelism: u32,
    defined_in: Vec<String>,
    enforced_by: String,
    rationale: serde_json::Value,
}

/// Reads the single declared source of every storyboard bound, failing closed
/// if the contract drifts from what this module and the render sandbox can
/// actually honour.
pub fn duration_limits() -> Result<MotionDurationLimits, MotionVideoStudioError> {
    let contract: DurationContract =
        serde_json::from_str(DURATION_CONTRACT).map_err(|_| draft_invalid())?;
    let default_total = contract
        .beat_count_default
        .checked_mul(contract.seconds_per_beat_default)
        .ok_or_else(draft_invalid)?;
    let frame_count_maximum = contract
        .total_seconds_maximum
        .checked_mul(contract.frames_per_second)
        .ok_or_else(draft_invalid)?;
    if contract.schema_version != 1
        || contract.id != "motion-storyboard-duration"
        || contract.version != "motion-storyboard-duration.v1"
        || contract.policy != "fail_closed"
        || contract.defined_in.is_empty()
        || contract.enforced_by.is_empty()
        || !contract.rationale.is_object()
        || contract.frames_per_second != MOTION_FRAMES_PER_SECOND
        || contract.beat_count_minimum == 0
        || contract.beat_count_minimum > contract.beat_count_default
        || contract.beat_count_default > contract.beat_count_maximum
        || contract.seconds_per_beat_minimum == 0
        || contract.seconds_per_beat_minimum > contract.seconds_per_beat_default
        || contract.seconds_per_beat_default > contract.seconds_per_beat_maximum
        || contract.render_wall_seconds_base == 0
        || contract.render_wall_millis_per_frame == 0
        || contract.render_cpu_parallelism == 0
        || default_total > contract.total_seconds_maximum
        || contract.total_seconds_maximum
            < contract.beat_count_minimum * contract.seconds_per_beat_minimum
        // A total the render sandbox cannot capture would turn a legal user
        // configuration into an opaque configuration error at submit time.
        || frame_count_maximum > crate::local_video_orchestrator::SANDBOX_FRAMES_MAXIMUM
    {
        return Err(draft_invalid());
    }
    Ok(MotionDurationLimits {
        frames_per_second: contract.frames_per_second,
        beat_count_minimum: contract.beat_count_minimum,
        beat_count_maximum: contract.beat_count_maximum,
        seconds_per_beat_minimum: contract.seconds_per_beat_minimum,
        seconds_per_beat_maximum: contract.seconds_per_beat_maximum,
        total_seconds_maximum: contract.total_seconds_maximum,
        render_wall_seconds_base: contract.render_wall_seconds_base,
        render_wall_millis_per_frame: contract.render_wall_millis_per_frame,
        render_cpu_parallelism: contract.render_cpu_parallelism,
    })
}

/// Startup cost plus a per-frame cost, never a fixed number: a film with six
/// times the frames needs six times the capture time.
pub fn render_sandbox_budget(
    frame_count: u32,
) -> Result<MotionRenderSandboxBudget, MotionVideoStudioError> {
    let limits = duration_limits()?;
    if frame_count == 0 || frame_count > limits.frame_count_maximum() {
        return Err(draft_invalid());
    }
    let capture_seconds = frame_count
        .checked_mul(limits.render_wall_millis_per_frame)
        .ok_or_else(draft_invalid)?
        .div_ceil(MILLIS_PER_SECOND);
    let wall_seconds = limits
        .render_wall_seconds_base
        .checked_add(capture_seconds)
        .ok_or_else(draft_invalid)?;
    let cpu_seconds = wall_seconds
        .checked_mul(limits.render_cpu_parallelism)
        .ok_or_else(draft_invalid)?;
    Ok(MotionRenderSandboxBudget {
        wall_seconds,
        cpu_seconds,
    })
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MotionRenderJobStatus {
    Queued,
    Rendering,
    Encoding,
    Succeeded,
    Failed,
    Cancelled,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MotionRenderFailureCode {
    RenderFailed,
    EncodingFailed,
    Interrupted,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MotionRenderJobSnapshot {
    render_job_id: Uuid,
    revision: u64,
    status: MotionRenderJobStatus,
    progress_percent: u8,
    subject: String,
    style_display_name: String,
    artifact_id: Option<Uuid>,
    artifact_size_bytes: Option<u64>,
    failure_code: Option<MotionRenderFailureCode>,
}

impl MotionRenderJobSnapshot {
    pub const fn status(&self) -> MotionRenderJobStatus {
        self.status
    }

    pub const fn progress_percent(&self) -> u8 {
        self.progress_percent
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MotionVideoArtifactPayload {
    artifact_id: Uuid,
    media_type: &'static str,
    base64: String,
}

#[derive(Clone, Debug)]
pub struct PreparedMotionRenderJob {
    render_job_id: Uuid,
    allowed_assets: Vec<String>,
    plan: MotionStoryboardPlan,
}

impl PreparedMotionRenderJob {
    pub const fn render_job_id(&self) -> Uuid {
        self.render_job_id
    }

    pub const fn frame_count(&self) -> u32 {
        self.plan.frame_count()
    }

    pub const fn frames_per_second(&self) -> u32 {
        self.plan.frames_per_second()
    }

    pub const fn total_seconds(&self) -> u32 {
        self.plan.total_seconds()
    }

    pub fn allowed_assets(&self) -> &[String] {
        &self.allowed_assets
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StyleContract {
    schema_version: u8,
    policy: String,
    upstream_version: String,
    upstream_commit: String,
    source_root: String,
    presets: Vec<StyleContractPreset>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StyleContractPreset {
    id: String,
    path: String,
    sha256: String,
}

#[derive(Clone)]
struct LockedStyle {
    id: String,
    display_name: &'static str,
    source_sha256: String,
    upstream_version: String,
    upstream_commit: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct StyleFreeze<'a> {
    schema_version: u8,
    style_preset_id: &'a str,
    style_display_name: &'a str,
    upstream_version: &'a str,
    upstream_commit: &'a str,
    source_frame_sha256: &'a str,
    brand_tokens_sha256: String,
    frozen_frame_sha256: String,
    frame_artifact_path: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RenderJobDocument<'a> {
    schema_version: u8,
    render_job_id: Uuid,
    creation_mode: &'static str,
    entry_html: &'static str,
    allowed_assets: &'a [String],
    frame_count: u32,
    frames_per_second: u32,
    duration_seconds: u32,
    style_preset_id: &'a str,
}

pub fn prepare_manual_render_job(
    store: &VideoJobWorkspaceStore,
    draft: &MotionVideoDraftRequest,
) -> Result<PreparedMotionRenderJob, MotionVideoStudioError> {
    let (style, plan) = draft.validate()?;
    let workspace = store.create_new().map_err(map_workspace_error)?;
    let result = prepare_inside_workspace(store, &workspace, draft, &style, plan);
    if result.is_err() {
        let _ = store.finish(&workspace, VideoWorkspaceDisposition::Delete);
    }
    result
}

fn prepare_inside_workspace(
    store: &VideoJobWorkspaceStore,
    workspace: &VideoJobWorkspace,
    draft: &MotionVideoDraftRequest,
    style: &LockedStyle,
    plan: MotionStoryboardPlan,
) -> Result<PreparedMotionRenderJob, MotionVideoStudioError> {
    let work = store
        .worker_asset_directory(workspace)
        .map_err(map_workspace_error)?;
    let logo_file = draft
        .logo
        .as_ref()
        .map(MotionVideoLogoDraft::validated_file_name)
        .transpose()?;
    let mut allowed_assets = Vec::new();
    if let (Some(logo), Some(file_name)) = (&draft.logo, logo_file) {
        write_private_file(&work.join(file_name), &logo.bytes)?;
        allowed_assets.push(file_name.to_owned());
    }

    let script = serde_json::to_vec(&serde_json::json!({
        "schemaVersion": 1,
        "creationMode": "manual_template_v1",
        "subject": draft.subject,
        "secondsPerBeat": plan.seconds_per_beat(),
        "beats": draft.beats,
    }))
    .map_err(|_| storage_unavailable())?;
    write_private_file(&work.join("SCRIPT.json"), &script)?;
    let storyboard = serde_json::to_vec(&serde_json::json!({
        "schemaVersion": 1,
        "durationSeconds": plan.total_seconds(),
        "secondsPerBeat": plan.seconds_per_beat(),
        "beats": draft.beats.iter().enumerate().map(|(index, beat)| serde_json::json!({
            "index": index,
            "startSeconds": index as u32 * plan.seconds_per_beat(),
            "durationSeconds": plan.seconds_per_beat(),
            "title": beat.title,
            "caption": beat.caption,
        })).collect::<Vec<_>>(),
    }))
    .map_err(|_| storage_unavailable())?;
    write_private_file(&work.join("STORYBOARD.json"), &storyboard)?;

    let frame_markdown = manual_frame_markdown(draft, style, logo_file, plan);
    write_private_file(&work.join("frame.md"), frame_markdown.as_bytes())?;
    let brand_tokens = serde_json::json!({
        "primaryColor": draft.primary_color,
        "secondaryColor": draft.secondary_color,
        "logoAsset": logo_file,
    });
    let brand_raw = serde_json::to_vec(&brand_tokens).map_err(|_| storage_unavailable())?;
    let freeze = StyleFreeze {
        schema_version: 1,
        style_preset_id: &style.id,
        style_display_name: style.display_name,
        upstream_version: &style.upstream_version,
        upstream_commit: &style.upstream_commit,
        source_frame_sha256: &style.source_sha256,
        brand_tokens_sha256: sha256_hex(&brand_raw),
        frozen_frame_sha256: sha256_hex(frame_markdown.as_bytes()),
        frame_artifact_path: "frame.md",
    };
    write_private_file(
        &work.join("style-freeze.json"),
        &serde_json::to_vec(&freeze).map_err(|_| storage_unavailable())?,
    )?;

    let composition = manual_composition(draft, style, logo_file, plan);
    write_private_file(&work.join(MOTION_COMPOSITION_FILE), composition.as_bytes())?;
    let render_job = RenderJobDocument {
        schema_version: 1,
        render_job_id: workspace.job_id(),
        creation_mode: "manual_template_v1",
        entry_html: MOTION_COMPOSITION_FILE,
        allowed_assets: &allowed_assets,
        frame_count: plan.frame_count(),
        frames_per_second: plan.frames_per_second(),
        duration_seconds: plan.total_seconds(),
        style_preset_id: &style.id,
    };
    write_private_file(
        &work.join("renderjob.json"),
        &serde_json::to_vec(&render_job).map_err(|_| storage_unavailable())?,
    )?;

    let snapshot = MotionRenderJobSnapshot {
        render_job_id: workspace.job_id(),
        revision: 1,
        status: MotionRenderJobStatus::Queued,
        progress_percent: 5,
        subject: draft.subject.trim().to_owned(),
        style_display_name: style.display_name.to_owned(),
        artifact_id: None,
        artifact_size_bytes: None,
        failure_code: None,
    };
    save_snapshot(store, workspace, &snapshot)?;
    Ok(PreparedMotionRenderJob {
        render_job_id: workspace.job_id(),
        allowed_assets,
        plan,
    })
}

pub fn jobs(
    store: &VideoJobWorkspaceStore,
) -> Result<Vec<MotionRenderJobSnapshot>, MotionVideoStudioError> {
    let mut result = Vec::new();
    for workspace in store.list_workspaces().map_err(map_workspace_error)? {
        if let Some(snapshot) = load_snapshot(store, &workspace)? {
            result.push(snapshot);
        }
    }
    result.sort_by_key(|snapshot| snapshot.render_job_id);
    Ok(result)
}

pub fn snapshot(
    store: &VideoJobWorkspaceStore,
    render_job_id: Uuid,
) -> Result<MotionRenderJobSnapshot, MotionVideoStudioError> {
    let workspace = store.open(render_job_id).map_err(map_workspace_error)?;
    load_snapshot(store, &workspace)?.ok_or_else(job_unavailable)
}

pub fn advance(
    store: &VideoJobWorkspaceStore,
    render_job_id: Uuid,
    status: MotionRenderJobStatus,
    progress_percent: u8,
    artifact: Option<&VideoArtifactRecord>,
    failure_code: Option<MotionRenderFailureCode>,
) -> Result<MotionRenderJobSnapshot, MotionVideoStudioError> {
    let workspace = store.open(render_job_id).map_err(map_workspace_error)?;
    let mut current = load_snapshot(store, &workspace)?.ok_or_else(job_unavailable)?;
    if matches!(
        current.status,
        MotionRenderJobStatus::Succeeded
            | MotionRenderJobStatus::Failed
            | MotionRenderJobStatus::Cancelled
    ) {
        return Ok(current);
    }
    let valid = match status {
        MotionRenderJobStatus::Rendering => progress_percent == 55 && artifact.is_none(),
        MotionRenderJobStatus::Encoding => progress_percent == 85 && artifact.is_none(),
        MotionRenderJobStatus::Succeeded => {
            progress_percent == 100 && artifact.is_some() && failure_code.is_none()
        }
        MotionRenderJobStatus::Failed => {
            progress_percent < 100 && artifact.is_none() && failure_code.is_some()
        }
        MotionRenderJobStatus::Cancelled => {
            progress_percent < 100 && artifact.is_none() && failure_code.is_none()
        }
        MotionRenderJobStatus::Queued => false,
    };
    if !valid {
        return Err(job_unavailable());
    }
    current.revision = current
        .revision
        .checked_add(1)
        .ok_or_else(job_unavailable)?;
    current.status = status;
    current.progress_percent = progress_percent;
    current.artifact_id = artifact.map(VideoArtifactRecord::artifact_id);
    current.artifact_size_bytes = artifact.map(VideoArtifactRecord::size_bytes);
    current.failure_code = failure_code;
    save_snapshot(store, &workspace, &current)?;
    Ok(current)
}

pub fn cancel(
    store: &VideoJobWorkspaceStore,
    render_job_id: Uuid,
) -> Result<(), MotionVideoStudioError> {
    let workspace = store.open(render_job_id).map_err(map_workspace_error)?;
    let current = load_snapshot(store, &workspace)?.ok_or_else(job_unavailable)?;
    if !matches!(
        current.status,
        MotionRenderJobStatus::Queued
            | MotionRenderJobStatus::Rendering
            | MotionRenderJobStatus::Encoding
    ) {
        return Err(job_unavailable());
    }
    let marker = store
        .worker_asset_directory(&workspace)
        .map_err(map_workspace_error)?
        .join(MOTION_CANCEL_FILE);
    match OpenOptions::new().create_new(true).write(true).open(marker) {
        Ok(mut file) => file
            .write_all(b"cancel\n")
            .and_then(|()| file.sync_all())
            .map_err(|_| storage_unavailable())?,
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(_) => return Err(storage_unavailable()),
    }
    advance(
        store,
        render_job_id,
        MotionRenderJobStatus::Cancelled,
        current.progress_percent.min(99),
        None,
        None,
    )?;
    Ok(())
}

pub fn cancellation_requested(
    store: &VideoJobWorkspaceStore,
    render_job_id: Uuid,
) -> Result<bool, MotionVideoStudioError> {
    let workspace = store.open(render_job_id).map_err(map_workspace_error)?;
    let marker = store
        .worker_asset_directory(&workspace)
        .map_err(map_workspace_error)?
        .join(MOTION_CANCEL_FILE);
    match fs::symlink_metadata(marker) {
        Ok(metadata) => Ok(metadata.file_type().is_file() && !metadata.file_type().is_symlink()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(_) => Err(storage_unavailable()),
    }
}

pub fn read_artifact(
    store: &VideoJobWorkspaceStore,
    artifact_id: Uuid,
) -> Result<MotionVideoArtifactPayload, MotionVideoStudioError> {
    let record = store
        .list_artifacts()
        .map_err(map_workspace_error)?
        .into_iter()
        .find(|record| {
            record.artifact_id() == artifact_id
                && record.media_type() == "video/mp4"
                && record.role() == "rendered_video"
        })
        .ok_or_else(job_unavailable)?;
    if record.size_bytes() > MAX_ARTIFACT_READ_BYTES {
        return Err(job_unavailable());
    }
    let mut reader = store.open_artifact(&record).map_err(map_workspace_error)?;
    let mut bytes = Vec::with_capacity(record.size_bytes() as usize);
    reader
        .read_to_end(&mut bytes)
        .map_err(|_| storage_unavailable())?;
    Ok(MotionVideoArtifactPayload {
        artifact_id,
        media_type: "video/mp4",
        base64: base64::engine::general_purpose::STANDARD.encode(bytes),
    })
}

pub fn delete_artifact(
    store: &VideoJobWorkspaceStore,
    artifact_id: Uuid,
) -> Result<(), MotionVideoStudioError> {
    let mut matched = None;
    for workspace in store.list_workspaces().map_err(map_workspace_error)? {
        if let Some(snapshot) = load_snapshot(store, &workspace)? {
            if snapshot.artifact_id == Some(artifact_id) {
                if matched.is_some() {
                    return Err(job_unavailable());
                }
                matched = Some((workspace, snapshot));
            }
        }
    }
    let (workspace, mut snapshot) = matched.ok_or_else(job_unavailable)?;
    store
        .delete_artifact(artifact_id)
        .map_err(map_workspace_error)?;
    snapshot.revision = snapshot
        .revision
        .checked_add(1)
        .ok_or_else(job_unavailable)?;
    snapshot.artifact_id = None;
    snapshot.artifact_size_bytes = None;
    save_snapshot(store, &workspace, &snapshot)
}

pub fn workspace_render_paths(
    store: &VideoJobWorkspaceStore,
    render_job_id: Uuid,
) -> Result<(PathBuf, PathBuf, PathBuf), MotionVideoStudioError> {
    let workspace = store.open(render_job_id).map_err(map_workspace_error)?;
    let work = store
        .worker_asset_directory(&workspace)
        .map_err(map_workspace_error)?;
    let output = store
        .worker_output_directory(&workspace)
        .map_err(map_workspace_error)?;
    let video = output.join(MOTION_OUTPUT_FILE);
    Ok((work, output, video))
}

pub fn import_rendered_output(
    store: &VideoJobWorkspaceStore,
    render_job_id: Uuid,
) -> Result<VideoArtifactRecord, MotionVideoStudioError> {
    let workspace = store.open(render_job_id).map_err(map_workspace_error)?;
    let artifact = store
        .import_output(
            &workspace,
            MOTION_OUTPUT_FILE,
            "video/mp4",
            "rendered_video",
        )
        .map_err(map_workspace_error)?;
    if store.remove_output(&workspace, MOTION_OUTPUT_FILE).is_err() {
        let _ = store.delete_artifact(artifact.artifact_id());
        return Err(storage_unavailable());
    }
    Ok(artifact)
}

fn save_snapshot(
    store: &VideoJobWorkspaceStore,
    workspace: &VideoJobWorkspace,
    snapshot: &MotionRenderJobSnapshot,
) -> Result<(), MotionVideoStudioError> {
    validate_snapshot(snapshot, workspace.job_id())?;
    store
        .save_checkpoint(
            workspace,
            MOTION_RENDER_JOB_CHECKPOINT,
            &serde_json::to_vec(snapshot).map_err(|_| storage_unavailable())?,
        )
        .map_err(map_workspace_error)
}

fn load_snapshot(
    store: &VideoJobWorkspaceStore,
    workspace: &VideoJobWorkspace,
) -> Result<Option<MotionRenderJobSnapshot>, MotionVideoStudioError> {
    let raw = match store.load_checkpoint(workspace, MOTION_RENDER_JOB_CHECKPOINT) {
        Ok(value) => value,
        Err(error)
            if error.code() == crate::video_job_workspace::VideoWorkspaceErrorCode::NotFound =>
        {
            return Ok(None);
        }
        Err(error) => return Err(map_workspace_error(error)),
    };
    let snapshot: MotionRenderJobSnapshot =
        serde_json::from_slice(&raw).map_err(|_| job_unavailable())?;
    validate_snapshot(&snapshot, workspace.job_id())?;
    Ok(Some(snapshot))
}

fn validate_snapshot(
    snapshot: &MotionRenderJobSnapshot,
    workspace_id: Uuid,
) -> Result<(), MotionVideoStudioError> {
    let valid_failure = match snapshot.status {
        MotionRenderJobStatus::Failed => snapshot.failure_code.is_some(),
        _ => snapshot.failure_code.is_none(),
    };
    let valid_artifact = snapshot.artifact_id.is_some() == snapshot.artifact_size_bytes.is_some()
        && (snapshot.artifact_id.is_none() || snapshot.status == MotionRenderJobStatus::Succeeded);
    let valid_progress = match snapshot.status {
        MotionRenderJobStatus::Queued => snapshot.progress_percent == 5,
        MotionRenderJobStatus::Rendering => snapshot.progress_percent == 55,
        MotionRenderJobStatus::Encoding => snapshot.progress_percent == 85,
        MotionRenderJobStatus::Succeeded => snapshot.progress_percent == 100,
        MotionRenderJobStatus::Failed | MotionRenderJobStatus::Cancelled => {
            snapshot.progress_percent < 100
        }
    };
    if snapshot.render_job_id != workspace_id
        || snapshot.render_job_id.get_version_num() != 4
        || snapshot.revision == 0
        || validate_copy(&snapshot.subject, MAX_SUBJECT_CHARS).is_err()
        || snapshot.style_display_name.is_empty()
        || snapshot.style_display_name.chars().count() > 40
        || !valid_failure
        || !valid_artifact
        || !valid_progress
    {
        return Err(job_unavailable());
    }
    Ok(())
}

fn locked_style(id: &str) -> Result<LockedStyle, MotionVideoStudioError> {
    let contract: StyleContract =
        serde_json::from_str(STYLE_CONTRACT).map_err(|_| draft_invalid())?;
    if contract.schema_version != 1
        || contract.policy != "fail_closed"
        || contract.upstream_version != "v0.7.68"
        || contract.upstream_commit != "71d84ff27f1c2b2828f4fdf9015c3da4157140ee"
        || contract.source_root != "skills/hyperframes-creative/frame-presets"
        || contract.presets.len() != 12
    {
        return Err(draft_invalid());
    }
    let preset = contract
        .presets
        .into_iter()
        .find(|preset| preset.id == id)
        .ok_or_else(draft_invalid)?;
    if preset.path != format!("{id}/FRAME.md") || !valid_digest(&preset.sha256) {
        return Err(draft_invalid());
    }
    let display_name = match id {
        "biennale-yellow" => "艺展暖黄",
        "blockframe" => "撞色方框",
        "blue-professional" => "专业蓝",
        "bold-poster" => "醒目海报",
        "broadside" => "宣言橙黑",
        "capsule" => "糖果胶囊",
        "cartesian" => "留白坐标",
        "cobalt-grid" => "钴蓝网格",
        "coral" => "珊瑚标题",
        "creative-mode" => "创意硬朗",
        "daisy-days" => "雏菊晴日",
        "editorial-forest" => "森林刊物",
        _ => return Err(draft_invalid()),
    };
    Ok(LockedStyle {
        id: id.to_owned(),
        display_name,
        source_sha256: preset.sha256,
        upstream_version: contract.upstream_version,
        upstream_commit: contract.upstream_commit,
    })
}

fn manual_frame_markdown(
    draft: &MotionVideoDraftRequest,
    style: &LockedStyle,
    logo_file: Option<&str>,
    plan: MotionStoryboardPlan,
) -> String {
    format!(
        "---\nversion: 1\nname: {}\ncolors:\n  primary: {}\n  secondary: {}\n  ink: #17213a\ntypography:\n  fontFamily: system-ui\n---\n\n固定模板手工制作；{} 段分镜，每段 {} 秒；Logo: {}\n",
        style.display_name,
        draft.primary_color,
        draft.secondary_color,
        plan.beat_count(),
        plan.seconds_per_beat(),
        logo_file.unwrap_or("none"),
    )
}

fn manual_composition(
    draft: &MotionVideoDraftRequest,
    style: &LockedStyle,
    logo_file: Option<&str>,
    plan: MotionStoryboardPlan,
) -> String {
    let logo = logo_file.map_or_else(String::new, |file| {
        format!(
            "<img class=\"logo\" src=\"{}\" alt=\"品牌 Logo\">",
            html_escape(file)
        )
    });
    let scenes = draft
        .beats
        .iter()
        .enumerate()
        .map(|(index, beat)| {
            format!(
                "<section class=\"scene\" data-track-index=\"{index}\">\
                 <span class=\"eyebrow\">第 {} 段 · {}</span>\
                 <h1>{}</h1><p>{}</p><div class=\"meter\"><i></i></div></section>",
                index + 1,
                html_escape(style.display_name),
                html_escape(beat.title.trim()),
                html_escape(beat.caption.trim()),
            )
        })
        .collect::<String>();
    format!(
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>\
         *{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;overflow:hidden}}\
         body{{font-family:system-ui,-apple-system,sans-serif;background:{secondary};color:#17213a}}\
         main{{position:relative;width:640px;height:360px;overflow:hidden;\
         background:linear-gradient(135deg,{secondary} 0%,#fff 58%,{primary}22 100%)}}\
         main:before{{content:'';position:absolute;width:280px;height:280px;border-radius:50%;\
         right:-80px;top:-100px;background:{primary};opacity:.16}}\
         .brand{{position:absolute;z-index:3;left:34px;top:28px;font-size:14px;font-weight:800;\
         letter-spacing:.12em;color:{primary}}}.logo{{position:absolute;z-index:4;right:34px;top:24px;\
         width:58px;height:58px;object-fit:contain;border-radius:14px;background:#fff;padding:7px;\
         box-shadow:0 8px 24px #0002}}.scene{{position:absolute;inset:0;padding:92px 58px 46px;\
         opacity:0;transform:translateY(22px) scale(.98);transition:none}}\
         .scene.active{{opacity:1;transform:none}}.eyebrow{{display:inline-block;color:{primary};\
         font-size:15px;font-weight:800;letter-spacing:.08em}}h1{{margin:16px 0 13px;\
         max-width:520px;font-size:46px;line-height:1.05;letter-spacing:-.04em}}p{{margin:0;\
         display:inline-block;max-width:510px;padding:10px 16px;border-radius:999px;\
         background:#17213a;color:#fff;font-size:19px;font-weight:650}}.meter{{position:absolute;\
         left:58px;right:58px;bottom:34px;height:6px;border-radius:99px;background:#17213a18;\
         overflow:hidden}}.meter i{{display:block;height:100%;width:0;background:{primary}}}\
         </style></head><body>\
         <main data-composition-id=\"manual-template\" data-duration=\"{total}\">\
         <div class=\"brand\">{subject}</div>{logo}{scenes}</main><script>\
         (function(){{const scenes=Array.from(document.querySelectorAll('.scene'));\
         const per={per},last={last},total={total};\
         function seek(time){{const safe=Math.max(0,Math.min(total-0.001,Number(time)||0));\
         const active=Math.min(last,Math.floor(safe/per));scenes.forEach((scene,index)=>{{\
         scene.classList.toggle('active',index===active);const meter=scene.querySelector('i');\
         meter.style.width=(index<active?'100%':index>active?'0%':\
         (((safe-active*per)/per)*100)+'%');}});}}\
         window.__timelines={{'manual-template':{{seek:seek}}}};seek(0);}})();\
         </script></body></html>",
        primary = draft.primary_color,
        secondary = draft.secondary_color,
        subject = html_escape(draft.subject.trim()),
        per = plan.seconds_per_beat(),
        last = plan.beat_count() - 1,
        total = plan.total_seconds(),
    )
}

fn write_private_file(path: &Path, bytes: &[u8]) -> Result<(), MotionVideoStudioError> {
    if bytes.is_empty() {
        return Err(storage_unavailable());
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|_| storage_unavailable())?;
    file.write_all(bytes)
        .and_then(|()| file.sync_all())
        .map_err(|_| storage_unavailable())
}

fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut value = String::with_capacity(64);
    for byte in digest {
        use std::fmt::Write as _;
        write!(&mut value, "{byte:02x}").expect("writing to String cannot fail");
    }
    value
}

fn html_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

fn validate_copy(value: &str, maximum: usize) -> Result<(), MotionVideoStudioError> {
    let trimmed = value.trim();
    let lowered = trimmed.to_ascii_lowercase();
    if trimmed.is_empty()
        || trimmed.chars().count() > maximum
        || trimmed
            .chars()
            .any(|character| character == '\0' || character.is_control())
        || trimmed.contains(['<', '>'])
        || lowered.contains("://")
        || lowered.contains("www.")
    {
        return Err(draft_invalid());
    }
    Ok(())
}

fn valid_color(value: &str) -> bool {
    value.len() == 7
        && value.starts_with('#')
        && value[1..].bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn valid_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn map_workspace_error(_error: VideoWorkspaceError) -> MotionVideoStudioError {
    storage_unavailable()
}

const fn draft_invalid() -> MotionVideoStudioError {
    MotionVideoStudioError {
        code: MotionVideoStudioErrorCode::DraftInvalid,
        retryable: false,
    }
}

const fn job_unavailable() -> MotionVideoStudioError {
    MotionVideoStudioError {
        code: MotionVideoStudioErrorCode::JobUnavailable,
        retryable: false,
    }
}

pub const fn render_unavailable() -> MotionVideoStudioError {
    MotionVideoStudioError {
        code: MotionVideoStudioErrorCode::RenderUnavailable,
        retryable: true,
    }
}

const fn storage_unavailable() -> MotionVideoStudioError {
    MotionVideoStudioError {
        code: MotionVideoStudioErrorCode::StorageUnavailable,
        retryable: false,
    }
}

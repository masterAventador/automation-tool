//! Authenticated, process-owned lifecycle for local video workers.

use crate::control_plane::{
    SmartEditMaterialAnalysisRequest, SmartEditMaterialWritebackRequest,
    SmartEditNarrationMaterialRequest,
};
use crate::managed_process_tree::{configure_managed_process, ManagedProcessTree};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use hmac::{Hmac, KeyInit, Mac};
use semver::Version;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::ffi::OsString;
use std::fmt;
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream};
use std::path::{Component, Path, PathBuf};
use std::process::{Child, ChildStderr, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::mpsc::{self, Receiver, TryRecvError};
use std::sync::{Mutex, MutexGuard};
use std::thread::JoinHandle;
use std::time::Duration;
use uuid::{Uuid, Variant};
use zeroize::{Zeroize, Zeroizing};

const BOOTSTRAP_VERSION: &str = "1";
const WORKER_PROTOCOL_VERSION: &str = "1.0";
const SESSION_TOKEN_BYTES: usize = 32;
const MAX_LINE_BYTES: usize = 16 * 1024;
const MAX_HTTP_RESPONSE_BYTES: u64 = 16 * 1024;
const MAX_VERSION_BYTES: usize = 128;
const MAX_PATH_BYTES: usize = 4096;
const MAX_RESTARTS: u8 = 8;
const MAX_TIMEOUT: Duration = Duration::from_secs(60);
const MAX_ENVIRONMENT_ENTRIES: usize = 8;
const MAX_ENVIRONMENT_NAME_BYTES: usize = 64;
/// Names that decide which program or library a process loads. Handing a
/// Worker one of them would relocate its runtime instead of naming a packaged
/// dependency, so they are refused however the caller obtained the path.
const FORBIDDEN_ENVIRONMENT_NAMES: &[&str] = &[
    "DYLD_FALLBACK_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "NODE_OPTIONS",
    "PATH",
    "PATHEXT",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
];
const EVENT_PROOF_PREFIX: &str = "atvwp1.";
const COMMAND_PROOF_PREFIX: &str = "atvwc1.";
const EVENT_AUTHENTICATION_DOMAIN: &[u8] = b"automation-tool.video-worker-event.v1\0";
const COMMAND_AUTHENTICATION_DOMAIN: &[u8] = b"automation-tool.video-worker-command.v1\0";
const LOOPBACK_HOST: &str = "127.0.0.1";
const WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
const BAILIAN_BASE_URL: &str = "https://dashscope.aliyuncs.com/compatible-mode/v1";
const CHROMIUM_MAJOR_MINIMUM: u32 = 100;
const CHROMIUM_MAJOR_MAXIMUM: u32 = 999;
const RENDER_LAUNCH_TIMEOUT_MINIMUM: Duration = Duration::from_secs(1);
const RENDER_LAUNCH_TIMEOUT_MAXIMUM: Duration = Duration::from_secs(60);
/// The most frames one render sandbox run may capture. It is public because it
/// is what bounds how long a brand-motion film may be: see
/// `motion_video_studio::duration_limits`, which fails closed if the declared
/// storyboard budget would ask for more frames than this.
pub const SANDBOX_FRAMES_MAXIMUM: u32 = 600;
/// Wall clock is the stall guard: a hung render is killed at this many seconds.
const SANDBOX_SECONDS_MAXIMUM: u32 = 300;
/// CPU seconds are a different quantity: the Worker sums them over the whole
/// browser process tree, so a render occupying N cores accrues them N times
/// faster than wall clock. The admissible CPU budget is the wall-clock budget
/// times the highest average core occupancy one render may declare. See
/// `contracts/video/motion-render-sandbox-budget.v1.json`.
const SANDBOX_CPU_PARALLELISM_MAXIMUM: u32 = 8;
const SANDBOX_MEMORY_MEGABYTES_MINIMUM: u32 = 128;
const SANDBOX_MEMORY_MEGABYTES_MAXIMUM: u32 = 8192;
const SANDBOX_OUTPUT_BYTES_MAXIMUM: u64 = 2_147_483_647;
const SANDBOX_ASSETS_MAXIMUM: usize = 128;
const SANDBOX_RELATIVE_PATH_MAXIMUM: usize = 512;
pub const MOTION_VIDEO_WORKER_VERSION: &str = "0.7.68";

pub const DEFAULT_VIDEO_WORKER_START_TIMEOUT: Duration = Duration::from_secs(30);
pub const DEFAULT_VIDEO_WORKER_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

type HmacSha256 = Hmac<Sha256>;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VideoWorkerKind {
    Python,
    Node,
}

impl VideoWorkerKind {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Python => "python",
            Self::Node => "node",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VideoWorkerState {
    Running,
    Stopped,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VideoWorkerErrorCode {
    AlreadyRunning,
    AuthenticationRejected,
    ConfigurationInvalid,
    NotRunning,
    ProcessUnavailable,
    RenderRejected,
    TimedOut,
    VersionMismatch,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct VideoWorkerError {
    code: VideoWorkerErrorCode,
}

impl VideoWorkerError {
    const fn new(code: VideoWorkerErrorCode) -> Self {
        Self { code }
    }

    pub const fn code(self) -> VideoWorkerErrorCode {
        self.code
    }
}

impl fmt::Debug for VideoWorkerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VideoWorkerError")
            .field("code", &self.code)
            .finish()
    }
}

impl fmt::Display for VideoWorkerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Local video worker lifecycle is unavailable")
    }
}

impl std::error::Error for VideoWorkerError {}

#[derive(Clone, Copy)]
pub struct VideoWorkerRestartPolicy {
    maximum_restarts: u8,
    restart_delay: Duration,
}

impl VideoWorkerRestartPolicy {
    pub fn new(maximum_restarts: u8, restart_delay: Duration) -> Result<Self, VideoWorkerError> {
        if maximum_restarts > MAX_RESTARTS || restart_delay > MAX_TIMEOUT {
            return Err(configuration_invalid());
        }
        Ok(Self {
            maximum_restarts,
            restart_delay,
        })
    }
}

#[derive(Clone)]
pub struct VideoWorkerLaunch {
    kind: VideoWorkerKind,
    executable_path: PathBuf,
    arguments: Vec<OsString>,
    isolated_environment: bool,
    environment: BTreeMap<&'static str, PathBuf>,
    media_tools: Option<VideoWorkerMediaToolsConfiguration>,
    asset_root: PathBuf,
    expected_version: String,
    restart_policy: VideoWorkerRestartPolicy,
    script_model: Option<VideoWorkerScriptModelConfiguration>,
    render_browser: Option<VideoWorkerRenderBrowserConfiguration>,
    web_ui: bool,
}

/// The exact packaged FFmpeg pair a local-editing Worker may use.
///
/// Paths travel in the authenticated stdin bootstrap, never argv or the
/// environment.  The Python receiver can therefore construct
/// `PackagedMediaTools` without discovery, PATH lookup, or environment reads.
#[derive(Clone)]
pub struct VideoWorkerMediaToolsConfiguration {
    ffmpeg_path: PathBuf,
    ffprobe_path: PathBuf,
}

impl fmt::Debug for VideoWorkerMediaToolsConfiguration {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("VideoWorkerMediaToolsConfiguration(<redacted>)")
    }
}

impl VideoWorkerMediaToolsConfiguration {
    pub fn new(ffmpeg_path: PathBuf, ffprobe_path: PathBuf) -> Result<Self, VideoWorkerError> {
        validate_executable_path(&ffmpeg_path)?;
        validate_executable_path(&ffprobe_path)?;
        if ffmpeg_path == ffprobe_path {
            return Err(configuration_invalid());
        }
        Ok(Self {
            ffmpeg_path,
            ffprobe_path,
        })
    }
}

/// The path-free identity of one local-editing render request.
#[derive(Clone)]
pub struct VideoWorkerLocalEditingJobRequest {
    project_id: Uuid,
    timeline_id: Uuid,
    timeline_revision: u32,
}

impl fmt::Debug for VideoWorkerLocalEditingJobRequest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("VideoWorkerLocalEditingJobRequest(<redacted>)")
    }
}

impl VideoWorkerLocalEditingJobRequest {
    pub fn new(
        project_id: Uuid,
        timeline_id: Uuid,
        timeline_revision: u32,
    ) -> Result<Self, VideoWorkerError> {
        if !valid_uuid_v4(project_id)
            || !valid_uuid_v4(timeline_id)
            || project_id == timeline_id
            || timeline_revision == 0
            || timeline_revision > i32::MAX as u32
        {
            return Err(configuration_invalid());
        }
        Ok(Self {
            project_id,
            timeline_id,
            timeline_revision,
        })
    }

    fn document(&self) -> serde_json::Value {
        serde_json::json!({
            "projectId": self.project_id.hyphenated().to_string(),
            "timelineId": self.timeline_id.hyphenated().to_string(),
            "timelineRevision": self.timeline_revision,
        })
    }

    fn canonical_json(&self) -> Result<String, VideoWorkerError> {
        serde_json::to_string(&self.document()).map_err(|_| process_unavailable())
    }

    pub(crate) const fn project_id(&self) -> Uuid {
        self.project_id
    }

    pub(crate) const fn timeline_id(&self) -> Uuid {
        self.timeline_id
    }

    pub(crate) const fn timeline_revision(&self) -> u32 {
        self.timeline_revision
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VideoWorkerLocalEditingPhase {
    Preparing,
    Rendering,
    Publishing,
}

impl VideoWorkerLocalEditingPhase {
    fn parse(value: &str) -> Option<Self> {
        match value {
            "preparing" => Some(Self::Preparing),
            "rendering" => Some(Self::Rendering),
            "publishing" => Some(Self::Publishing),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VideoWorkerLocalEditingFailureCode {
    InvalidTimeline,
    MaterialUnavailable,
    MaterialUnsupported,
    FontUnavailable,
    RenderFailed,
    ResourceExhausted,
    PermissionDenied,
    WorkspaceUnusable,
}

impl VideoWorkerLocalEditingFailureCode {
    fn parse(value: &str) -> Option<Self> {
        match value {
            "invalid_timeline" => Some(Self::InvalidTimeline),
            "material_unavailable" => Some(Self::MaterialUnavailable),
            "material_unsupported" => Some(Self::MaterialUnsupported),
            "font_unavailable" => Some(Self::FontUnavailable),
            "render_failed" => Some(Self::RenderFailed),
            "resource_exhausted" => Some(Self::ResourceExhausted),
            "permission_denied" => Some(Self::PermissionDenied),
            "workspace_unusable" => Some(Self::WorkspaceUnusable),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum VideoWorkerLocalEditingEvent {
    Progress {
        phase: VideoWorkerLocalEditingPhase,
        progress_per_mille: u16,
    },
    Succeeded {
        output_artifact_id: Uuid,
    },
    Failed {
        failure_code: VideoWorkerLocalEditingFailureCode,
    },
    Cancelled,
}

const SMART_EDIT_REQUEST_SCHEMA: &str = "smart-edit-generation-request.v1";
const SMART_EDIT_RESULT_SCHEMA: &str = "smart-edit-generation-result.v1";
const SMART_EDIT_RESULT_MAX_BYTES: u64 = 4 * 1024 * 1024;

#[derive(Clone)]
pub struct VideoWorkerSmartEditRequest {
    prompt: String,
    materials: Vec<serde_json::Value>,
    enable_thinking: bool,
}

impl fmt::Debug for VideoWorkerSmartEditRequest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("VideoWorkerSmartEditRequest(<redacted>)")
    }
}

impl VideoWorkerSmartEditRequest {
    pub fn new(
        prompt: impl Into<String>,
        materials: Vec<serde_json::Value>,
        enable_thinking: bool,
    ) -> Result<Self, VideoWorkerError> {
        let prompt = prompt.into();
        const MATERIAL_KEYS: &[&str] = &[
            "aiDescription",
            "aiTags",
            "audioLoudnessLufs",
            "contentDigest",
            "describedAt",
            "descriptionSource",
            "durationMs",
            "hasAudio",
            "hasSpeech",
            "height",
            "kind",
            "materialId",
            "shotBoundariesMs",
            "speechSegmentsMs",
            "speechTranscript",
            "width",
        ];
        if prompt.is_empty()
            || prompt.trim() != prompt
            || prompt.chars().count() > 4_000
            || prompt
                .chars()
                .any(|character| character.is_control() && !matches!(character, '\n' | '\t'))
            || !(1..=32).contains(&materials.len())
            || materials.iter().any(|material| {
                material.as_object().is_none_or(|object| {
                    object.len() != MATERIAL_KEYS.len()
                        || MATERIAL_KEYS.iter().any(|key| !object.contains_key(*key))
                        || object
                            .keys()
                            .any(|key| key.to_ascii_lowercase().contains("path"))
                })
            })
        {
            return Err(configuration_invalid());
        }
        Ok(Self {
            prompt,
            materials,
            enable_thinking,
        })
    }

    fn document(&self, job_id: &str) -> serde_json::Value {
        serde_json::json!({
            "enableThinking": self.enable_thinking,
            "jobId": job_id,
            "materials": self.materials,
            "prompt": self.prompt,
            "schemaVersion": SMART_EDIT_REQUEST_SCHEMA,
        })
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VideoWorkerSmartEditStage {
    Preparing,
    Analyzing,
    Scripting,
    Synthesizing,
    Matching,
    Selecting,
    Publishing,
    Completed,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum VideoWorkerSmartEditFailureCode {
    InsufficientMaterials,
    SourceTooShort,
    NoRelevantMaterial,
    ConfigurationMissing,
    MaterialUnavailable,
    UpstreamRejected,
    WorkspaceUnusable,
    CommitFailed,
    LocalFailed,
}

impl VideoWorkerSmartEditFailureCode {
    fn as_str(self) -> &'static str {
        match self {
            Self::InsufficientMaterials => "insufficient_materials",
            Self::SourceTooShort => "source_too_short",
            Self::NoRelevantMaterial => "no_relevant_material",
            Self::ConfigurationMissing => "configuration_missing",
            Self::MaterialUnavailable => "material_unavailable",
            Self::UpstreamRejected => "upstream_rejected",
            Self::WorkspaceUnusable => "workspace_unusable",
            Self::CommitFailed => "commit_failed",
            Self::LocalFailed => "local_failed",
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
enum VideoWorkerSmartEditParagraphKind {
    OriginalSpeech,
    Narrated,
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerSmartEditParagraph {
    audio_material_id: String,
    caption_text: String,
    duration_ms: u64,
    kind: VideoWorkerSmartEditParagraphKind,
    sequence: u32,
    visual_material_id: String,
    visual_source_in_ms: Option<u64>,
    visual_source_out_ms: Option<u64>,
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerSmartEditDraft {
    duration_ms: u64,
    paragraphs: Vec<VideoWorkerSmartEditParagraph>,
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerSmartEditNarrationRegistration {
    bytes_written: u64,
    content_digest: String,
    duration_ms: u64,
    material_id: String,
    relative_path: String,
    sequence: u32,
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct VideoWorkerSmartEditResult {
    analysis_updates: Vec<SmartEditMaterialAnalysisRequest>,
    draft: VideoWorkerSmartEditDraft,
    job_id: String,
    narration_registrations: Vec<VideoWorkerSmartEditNarrationRegistration>,
    schema_version: String,
}

impl fmt::Debug for VideoWorkerSmartEditResult {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("VideoWorkerSmartEditResult(<redacted>)")
    }
}

impl VideoWorkerSmartEditResult {
    fn validate(&self, expected_job_id: &str) -> Result<(), VideoWorkerError> {
        if self.schema_version != SMART_EDIT_RESULT_SCHEMA
            || self.job_id != expected_job_id
            || self.analysis_updates.len() > 32
            || self.narration_registrations.len() > 32
            || self.draft.paragraphs.is_empty()
            || self.draft.paragraphs.len() > 256
            || !(100..=4 * 60 * 60 * 1000).contains(&self.draft.duration_ms)
        {
            return Err(authentication_rejected());
        }
        let mut duration_ms = 0_u64;
        let mut paragraph_ids = std::collections::HashSet::new();
        for (index, paragraph) in self.draft.paragraphs.iter().enumerate() {
            let expected_sequence =
                u32::try_from(index + 1).map_err(|_| authentication_rejected())?;
            let visual_id = Uuid::parse_str(&paragraph.visual_material_id)
                .map_err(|_| authentication_rejected())?;
            let audio_id = Uuid::parse_str(&paragraph.audio_material_id)
                .map_err(|_| authentication_rejected())?;
            let valid_source_window = match (
                paragraph.visual_source_in_ms,
                paragraph.visual_source_out_ms,
            ) {
                (Some(source_in_ms), Some(source_out_ms)) => {
                    source_out_ms.checked_sub(source_in_ms) == Some(paragraph.duration_ms)
                }
                (None, None) => true,
                _ => false,
            };
            if paragraph.sequence != expected_sequence
                || !valid_uuid_v4(visual_id)
                || !valid_uuid_v4(audio_id)
                || visual_id.hyphenated().to_string() != paragraph.visual_material_id
                || audio_id.hyphenated().to_string() != paragraph.audio_material_id
                || paragraph.duration_ms == 0
                || !valid_source_window
                || paragraph.caption_text.is_empty()
                || paragraph.caption_text.trim() != paragraph.caption_text
                || paragraph.caption_text.chars().count() > 2_000
                || paragraph
                    .caption_text
                    .chars()
                    .any(|character| character.is_control() && !matches!(character, '\n' | '\t'))
                || paragraph.kind == VideoWorkerSmartEditParagraphKind::OriginalSpeech
                    && (paragraph.audio_material_id != paragraph.visual_material_id
                        || paragraph.visual_source_in_ms.is_none())
                || !paragraph_ids.insert(paragraph.sequence)
            {
                return Err(authentication_rejected());
            }
            duration_ms = duration_ms
                .checked_add(paragraph.duration_ms)
                .ok_or_else(authentication_rejected)?;
        }
        if duration_ms != self.draft.duration_ms {
            return Err(authentication_rejected());
        }
        let narrated = self
            .draft
            .paragraphs
            .iter()
            .filter(|paragraph| paragraph.kind == VideoWorkerSmartEditParagraphKind::Narrated)
            .collect::<Vec<_>>();
        if narrated.len() != self.narration_registrations.len() {
            return Err(authentication_rejected());
        }
        let mut material_ids = std::collections::HashSet::new();
        let mut digests = std::collections::HashSet::new();
        for (registration, paragraph) in self.narration_registrations.iter().zip(narrated) {
            if registration.sequence == 0
                || registration.sequence != paragraph.sequence
                || registration.material_id != paragraph.audio_material_id
                || registration.duration_ms != paragraph.duration_ms
                || registration.bytes_written == 0
                || registration.bytes_written > 512 * 1024 * 1024
                || !valid_sha256(&registration.content_digest)
                || !valid_smart_edit_relative_path(&registration.relative_path)
                || !material_ids.insert(registration.material_id.as_str())
                || !digests.insert(registration.content_digest.as_str())
            {
                return Err(authentication_rejected());
            }
        }
        let writeback = self.writeback_request()?;
        if let Some(request) = writeback {
            request.validate().map_err(|_| authentication_rejected())?;
        }
        Ok(())
    }

    pub(crate) fn writeback_request(
        &self,
    ) -> Result<Option<SmartEditMaterialWritebackRequest>, VideoWorkerError> {
        let paragraphs = self
            .draft
            .paragraphs
            .iter()
            .map(|paragraph| (paragraph.sequence, paragraph))
            .collect::<BTreeMap<_, _>>();
        let narrations = self
            .narration_registrations
            .iter()
            .map(|value| {
                let paragraph = paragraphs
                    .get(&value.sequence)
                    .ok_or_else(authentication_rejected)?;
                Ok(SmartEditNarrationMaterialRequest {
                    material_id: value.material_id.clone(),
                    content_digest: value.content_digest.clone(),
                    duration_ms: value.duration_ms,
                    speech_transcript: paragraph.caption_text.clone(),
                })
            })
            .collect::<Result<Vec<_>, VideoWorkerError>>()?;
        if self.analysis_updates.is_empty() && narrations.is_empty() {
            return Ok(None);
        }
        Ok(Some(SmartEditMaterialWritebackRequest {
            analyses: self.analysis_updates.clone(),
            narrations,
        }))
    }

    pub(crate) fn timeline_document(&self) -> serde_json::Value {
        let mut start_ms = 0_u64;
        let mut visual = Vec::new();
        let mut narration = Vec::new();
        let mut ambient = Vec::new();
        let mut caption = Vec::new();
        for paragraph in &self.draft.paragraphs {
            let suffix = format!("{:04}", paragraph.sequence);
            visual.push(serde_json::json!({
                "clipId": format!("visual-{suffix}"),
                "startMs": start_ms,
                "durationMs": paragraph.duration_ms,
                "sourceMaterialId": paragraph.visual_material_id,
                "sourceInMs": paragraph.visual_source_in_ms,
                "sourceOutMs": paragraph.visual_source_out_ms,
                "text": null,
                "gainDb": null,
                "transitionIn": null,
                "originalAudioMode": null,
            }));
            let audio = serde_json::json!({
                "clipId": if paragraph.kind == VideoWorkerSmartEditParagraphKind::Narrated {
                    format!("narration-{suffix}")
                } else {
                    format!("ambient-{suffix}")
                },
                "startMs": start_ms,
                "durationMs": paragraph.duration_ms,
                "sourceMaterialId": paragraph.audio_material_id,
                "sourceInMs": if paragraph.kind == VideoWorkerSmartEditParagraphKind::Narrated {
                    Some(0)
                } else {
                    paragraph.visual_source_in_ms
                },
                "sourceOutMs": if paragraph.kind == VideoWorkerSmartEditParagraphKind::Narrated {
                    Some(paragraph.duration_ms)
                } else {
                    paragraph.visual_source_out_ms
                },
                "text": null,
                "gainDb": 0.0,
                "transitionIn": null,
                "originalAudioMode": if paragraph.kind == VideoWorkerSmartEditParagraphKind::OriginalSpeech {
                    serde_json::Value::String("auto_duck".to_owned())
                } else {
                    serde_json::Value::Null
                },
            });
            if paragraph.kind == VideoWorkerSmartEditParagraphKind::Narrated {
                narration.push(audio);
            } else {
                ambient.push(audio);
            }
            caption.push(serde_json::json!({
                "clipId": format!("caption-{suffix}"),
                "startMs": start_ms,
                "durationMs": paragraph.duration_ms,
                "sourceMaterialId": null,
                "sourceInMs": null,
                "sourceOutMs": null,
                "text": paragraph.caption_text,
                "gainDb": null,
                "transitionIn": null,
                "originalAudioMode": null,
            }));
            start_ms += paragraph.duration_ms;
        }
        let mut tracks = vec![serde_json::json!({
            "trackId": "visual",
            "kind": "visual",
            "clips": visual,
        })];
        if !narration.is_empty() {
            tracks.push(serde_json::json!({
                "trackId": "narration",
                "kind": "narration",
                "clips": narration,
            }));
        }
        if !ambient.is_empty() {
            tracks.push(serde_json::json!({
                "trackId": "ambient",
                "kind": "ambient",
                "clips": ambient,
            }));
        }
        tracks.push(serde_json::json!({
            "trackId": "caption",
            "kind": "caption",
            "clips": caption,
        }));
        serde_json::json!({"durationMs": self.draft.duration_ms, "tracks": tracks})
    }
}

#[derive(Clone, Debug)]
pub enum VideoWorkerSmartEditEvent {
    Progress {
        stage: VideoWorkerSmartEditStage,
        progress_per_mille: u16,
    },
    Prepared {
        result_digest: String,
        result: VideoWorkerSmartEditResult,
    },
    Failed {
        failure_code: VideoWorkerSmartEditFailureCode,
    },
    Cancelled,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VideoWorkerLocalMaterialKind {
    Video,
    Image,
    Audio,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct VideoWorkerLocalMaterialFacts {
    audio_loudness_lufs: Option<f64>,
    content_digest: String,
    duration_ms: Option<u32>,
    has_audio: bool,
    height: Option<u32>,
    kind: VideoWorkerLocalMaterialKind,
    width: Option<u32>,
}

impl VideoWorkerLocalMaterialFacts {
    pub const fn kind(&self) -> VideoWorkerLocalMaterialKind {
        self.kind
    }

    pub const fn duration_ms(&self) -> Option<u32> {
        self.duration_ms
    }

    pub const fn width(&self) -> Option<u32> {
        self.width
    }

    pub const fn height(&self) -> Option<u32> {
        self.height
    }

    pub fn content_digest(&self) -> &str {
        &self.content_digest
    }

    pub const fn has_audio(&self) -> bool {
        self.has_audio
    }

    pub const fn audio_loudness_lufs(&self) -> Option<f64> {
        self.audio_loudness_lufs
    }

    fn is_valid(&self) -> bool {
        let duration_valid = match self.kind {
            VideoWorkerLocalMaterialKind::Image => self.duration_ms.is_none(),
            VideoWorkerLocalMaterialKind::Video | VideoWorkerLocalMaterialKind::Audio => self
                .duration_ms
                .is_some_and(|duration| (1..=4 * 60 * 60 * 1000).contains(&duration)),
        };
        let dimensions_valid = match self.kind {
            VideoWorkerLocalMaterialKind::Audio => {
                self.width.is_none() && self.height.is_none() && self.has_audio
            }
            VideoWorkerLocalMaterialKind::Video | VideoWorkerLocalMaterialKind::Image => {
                self.width.zip(self.height).is_some_and(|(width, height)| {
                    (1..=8192).contains(&width) && (1..=8192).contains(&height)
                })
            }
        };
        let loudness_valid = self.audio_loudness_lufs.is_none_or(|loudness| {
            self.has_audio && loudness.is_finite() && (-70.0..=0.0).contains(&loudness)
        });
        duration_valid
            && dimensions_valid
            && loudness_valid
            && !(self.kind == VideoWorkerLocalMaterialKind::Image && self.has_audio)
            && self.content_digest.len() == 64
            && self
                .content_digest
                .bytes()
                .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
    }

    fn canonical_json(&self) -> Result<String, VideoWorkerError> {
        let document = serde_json::to_value(self).map_err(|_| process_unavailable())?;
        serde_json::to_string(&document).map_err(|_| process_unavailable())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VideoWorkerLocalMaterialFailureCode {
    Unreadable,
    SourceNotAtRest,
    UnsafePath,
    Undecodable,
    NoUsableStream,
    UnusableDuration,
    TooLong,
    UnusableFrameSize,
    FrameTooLarge,
    FileTooLarge,
    SilentAudio,
    ProbeCrashed,
    ProbeFailed,
    WorkspaceUnusable,
    UnusableIdentifier,
    NotRegistered,
    FileMissing,
    FileUnreadable,
    FileChanged,
    RegistryUnreadable,
    RegistryUnwritable,
    RegistryFull,
}

impl VideoWorkerLocalMaterialFailureCode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Unreadable => "unreadable",
            Self::SourceNotAtRest => "source_not_at_rest",
            Self::UnsafePath => "unsafe_path",
            Self::Undecodable => "undecodable",
            Self::NoUsableStream => "no_usable_stream",
            Self::UnusableDuration => "unusable_duration",
            Self::TooLong => "too_long",
            Self::UnusableFrameSize => "unusable_frame_size",
            Self::FrameTooLarge => "frame_too_large",
            Self::FileTooLarge => "file_too_large",
            Self::SilentAudio => "silent_audio",
            Self::ProbeCrashed => "probe_crashed",
            Self::ProbeFailed => "probe_failed",
            Self::WorkspaceUnusable => "workspace_unusable",
            Self::UnusableIdentifier => "unusable_identifier",
            Self::NotRegistered => "not_registered",
            Self::FileMissing => "file_missing",
            Self::FileUnreadable => "file_unreadable",
            Self::FileChanged => "file_changed",
            Self::RegistryUnreadable => "registry_unreadable",
            Self::RegistryUnwritable => "registry_unwritable",
            Self::RegistryFull => "registry_full",
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VideoWorkerLocalMaterialStatus {
    Available,
    UnusableIdentifier,
    NotRegistered,
    FileMissing,
    FileUnreadable,
    FileChanged,
    RegistryUnreadable,
    RegistryUnwritable,
    RegistryFull,
}

impl VideoWorkerLocalMaterialStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Available => "available",
            Self::UnusableIdentifier => "unusable_identifier",
            Self::NotRegistered => "not_registered",
            Self::FileMissing => "file_missing",
            Self::FileUnreadable => "file_unreadable",
            Self::FileChanged => "file_changed",
            Self::RegistryUnreadable => "registry_unreadable",
            Self::RegistryUnwritable => "registry_unwritable",
            Self::RegistryFull => "registry_full",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VideoWorkerLocalMaterialError {
    Lifecycle(VideoWorkerErrorCode),
    Rejected(VideoWorkerLocalMaterialFailureCode),
}

impl fmt::Display for VideoWorkerLocalMaterialError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Local material Worker operation is unavailable")
    }
}

impl std::error::Error for VideoWorkerLocalMaterialError {}

impl From<VideoWorkerError> for VideoWorkerLocalMaterialError {
    fn from(error: VideoWorkerError) -> Self {
        Self::Lifecycle(error.code())
    }
}

/// The single, already-verified embedded Chromium the render Worker may
/// launch. No other browser source exists: the Worker never downloads,
/// discovers a system browser or consults a cache fallback.
#[derive(Clone)]
pub struct VideoWorkerRenderBrowserConfiguration {
    executable_path: PathBuf,
    chromium_major: u32,
    launch_timeout: Duration,
}

impl fmt::Debug for VideoWorkerRenderBrowserConfiguration {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VideoWorkerRenderBrowserConfiguration")
            .field("chromium_major", &self.chromium_major)
            .field("launch_timeout", &self.launch_timeout)
            .finish_non_exhaustive()
    }
}

impl VideoWorkerRenderBrowserConfiguration {
    pub fn new(
        executable_path: PathBuf,
        chromium_major: u32,
        launch_timeout: Duration,
    ) -> Result<Self, VideoWorkerError> {
        validate_executable_path(&executable_path)?;
        if !(CHROMIUM_MAJOR_MINIMUM..=CHROMIUM_MAJOR_MAXIMUM).contains(&chromium_major)
            || launch_timeout < RENDER_LAUNCH_TIMEOUT_MINIMUM
            || launch_timeout > RENDER_LAUNCH_TIMEOUT_MAXIMUM
            || launch_timeout.subsec_nanos() != 0
        {
            return Err(configuration_invalid());
        }
        Ok(Self {
            executable_path: child_process_path(&executable_path),
            chromium_major,
            launch_timeout,
        })
    }

    pub const fn chromium_major(&self) -> u32 {
        self.chromium_major
    }
}

/// The stage one render draws on.
///
/// Not a constant, because more than one kind of composition is rendered:
/// `composition_template` writes its whole type scale for 640x360, while a
/// catalog part declares its own stage — 105 of the frozen catalog's parts are
/// 1920x1080, three are 1080x1920 portrait and one is 1440x2560. A part drawn
/// on the template's stage is the top-left corner of itself, which is the
/// incident `contracts/video/motion-render-canvas.v1.json` records: a valid MP4
/// that was a still image, with neither side able to see the disagreement.
///
/// The bounds mirror `requestedCanvas` in that contract;
/// `frontend/tests/motion-render-canvas-per-render.test.mjs` keeps them aligned.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct VideoWorkerRenderCanvas {
    width: u32,
    height: u32,
    device_scale_factor: u32,
}

const CANVAS_WIDTH_MINIMUM: u32 = 320;
const CANVAS_WIDTH_MAXIMUM: u32 = 2560;
const CANVAS_HEIGHT_MINIMUM: u32 = 320;
const CANVAS_HEIGHT_MAXIMUM: u32 = 2560;
const CANVAS_DEVICE_SCALE_FACTOR_MINIMUM: u32 = 1;
const CANVAS_DEVICE_SCALE_FACTOR_MAXIMUM: u32 = 3;
/// Output pixels, not CSS pixels: that is what a captured PNG costs.
const CANVAS_OUTPUT_PIXELS_MAXIMUM: u64 = 3_686_400;

impl VideoWorkerRenderCanvas {
    pub fn new(
        width: u32,
        height: u32,
        device_scale_factor: u32,
    ) -> Result<Self, VideoWorkerError> {
        if !(CANVAS_WIDTH_MINIMUM..=CANVAS_WIDTH_MAXIMUM).contains(&width)
            || !(CANVAS_HEIGHT_MINIMUM..=CANVAS_HEIGHT_MAXIMUM).contains(&height)
            || !(CANVAS_DEVICE_SCALE_FACTOR_MINIMUM..=CANVAS_DEVICE_SCALE_FACTOR_MAXIMUM)
                .contains(&device_scale_factor)
        {
            return Err(configuration_invalid());
        }
        // The product is what a frame costs; checking the sides alone would
        // admit 2560x2560 at factor 3, which is 59 megapixels a frame.
        let output = u64::from(width)
            * u64::from(height)
            * u64::from(device_scale_factor)
            * u64::from(device_scale_factor);
        if output > CANVAS_OUTPUT_PIXELS_MAXIMUM {
            return Err(configuration_invalid());
        }
        Ok(Self {
            width,
            height,
            device_scale_factor,
        })
    }

    fn document(&self) -> serde_json::Value {
        serde_json::json!({
            "deviceScaleFactor": self.device_scale_factor,
            "height": self.height,
            "width": self.width,
        })
    }
}

/// Which stretch of the loaded document's own timeline one render covers.
///
/// A render used to be "this page, this many frames", and the Worker's only
/// possible rule was to spread the page's whole timeline across those frames.
/// That is right for a film captured in one pass and wrong the moment several
/// shots load one document: every template shot of a one-sentence film
/// re-rendered the entire composition. The kept artifact of 2026-07-28 is
/// twelve seconds made of two identical six second halves at double speed, and
/// the codec, canvas, frame count, duration and still-image gate were all green
/// over it — nothing downstream of here can see the difference.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct VideoWorkerSourceWindow {
    start_millis: u32,
    end_millis: u32,
}

impl VideoWorkerSourceWindow {
    /// Refuses a window no render could sample: ending no later than it begins,
    /// or reaching past what one render may run for.
    ///
    /// Whole milliseconds rather than seconds. The render command's HMAC binds
    /// to a canonical JSON that this side, the Worker and the Executor must all
    /// produce byte for byte, and a float does not survive that trip — Python
    /// writes `0.0` where `JSON.stringify` writes `0`, so the proof stops
    /// matching and the command is dropped in silence. Every other number in
    /// the spec is an integer for the same reason.
    pub fn new(start_millis: u32, end_millis: u32) -> Result<Self, VideoWorkerError> {
        if end_millis <= start_millis || end_millis > SANDBOX_SECONDS_MAXIMUM.saturating_mul(1000) {
            return Err(configuration_invalid());
        }
        Ok(Self {
            start_millis,
            end_millis,
        })
    }

    pub const fn start_millis(&self) -> u32 {
        self.start_millis
    }

    pub const fn end_millis(&self) -> u32 {
        self.end_millis
    }
}

/// One RenderJob HTML render sandbox request. The workspace is the VF-03
/// private RenderJob directory; the entry document and every declared asset
/// are workspace-relative paths that the Worker re-validates for containment.
#[derive(Clone)]
pub struct VideoWorkerRenderSandboxRequest {
    canvas: VideoWorkerRenderCanvas,
    source_window: VideoWorkerSourceWindow,
    workspace: PathBuf,
    entry_html: String,
    /// The workspace file whose appearance tells the render to stop, named by
    /// the caller rather than known by the Worker. Carried here for the same
    /// reason `entry_html` is: it is the App's convention, and a Worker holding
    /// its own copy of it is a second source that can drift silently.
    cancel_marker: String,
    allowed_assets: Vec<String>,
    frame_count: u32,
    max_duration_seconds: u32,
    max_cpu_seconds: u32,
    max_memory_megabytes: u32,
    max_output_bytes: u64,
}

impl fmt::Debug for VideoWorkerRenderSandboxRequest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VideoWorkerRenderSandboxRequest")
            .field("canvas", &self.canvas)
            .field("source_window", &self.source_window)
            .field("frame_count", &self.frame_count)
            .field("max_duration_seconds", &self.max_duration_seconds)
            .field("max_cpu_seconds", &self.max_cpu_seconds)
            .field("max_memory_megabytes", &self.max_memory_megabytes)
            .field("max_output_bytes", &self.max_output_bytes)
            .finish_non_exhaustive()
    }
}

impl VideoWorkerRenderSandboxRequest {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        workspace: PathBuf,
        entry_html: String,
        cancel_marker: String,
        canvas: VideoWorkerRenderCanvas,
        source_window: VideoWorkerSourceWindow,
        allowed_assets: Vec<String>,
        frame_count: u32,
        max_duration_seconds: u32,
        max_cpu_seconds: u32,
        max_memory_megabytes: u32,
        max_output_bytes: u64,
    ) -> Result<Self, VideoWorkerError> {
        if !workspace.is_absolute()
            || workspace.as_os_str().len() > MAX_PATH_BYTES
            || !valid_sandbox_relative_path(&entry_html)
            || !valid_sandbox_relative_path(&cancel_marker)
            || allowed_assets.len() > SANDBOX_ASSETS_MAXIMUM
            || !allowed_assets
                .iter()
                .all(|asset| valid_sandbox_relative_path(asset))
            || !(1..=SANDBOX_FRAMES_MAXIMUM).contains(&frame_count)
            || !(1..=SANDBOX_SECONDS_MAXIMUM).contains(&max_duration_seconds)
            || !(1..=max_duration_seconds.saturating_mul(SANDBOX_CPU_PARALLELISM_MAXIMUM))
                .contains(&max_cpu_seconds)
            || !(SANDBOX_MEMORY_MEGABYTES_MINIMUM..=SANDBOX_MEMORY_MEGABYTES_MAXIMUM)
                .contains(&max_memory_megabytes)
            || !(1..=SANDBOX_OUTPUT_BYTES_MAXIMUM).contains(&max_output_bytes)
        {
            return Err(configuration_invalid());
        }
        Ok(Self {
            canvas,
            source_window,
            workspace,
            entry_html,
            cancel_marker,
            allowed_assets,
            frame_count,
            max_duration_seconds,
            max_cpu_seconds,
            max_memory_megabytes,
            max_output_bytes,
        })
    }

    fn document(&self) -> Result<serde_json::Value, VideoWorkerError> {
        let workspace = self.workspace.to_str().ok_or_else(configuration_invalid)?;
        Ok(serde_json::json!({
            "allowedAssets": self.allowed_assets,
            "canvas": self.canvas.document(),
            "cancelMarker": self.cancel_marker,
            "entryHtml": self.entry_html,
            "frameCount": self.frame_count,
            "sourceEndMillis": self.source_window.end_millis(),
            "sourceStartMillis": self.source_window.start_millis(),
            "maxCpuSeconds": self.max_cpu_seconds,
            "maxDurationSeconds": self.max_duration_seconds,
            "maxMemoryMegabytes": self.max_memory_megabytes,
            "maxOutputBytes": self.max_output_bytes,
            "workspace": workspace,
        }))
    }

    fn canonical_json(&self) -> Result<String, VideoWorkerError> {
        // serde_json::Value keeps object keys in sorted (BTreeMap) order and
        // serializes compactly, matching the Worker's canonical HMAC form.
        serde_json::to_string(&self.document()?).map_err(|_| process_unavailable())
    }
}

/// The authenticated outcome of a completed render sandbox: how many frames
/// were captured and how many hostile actions the sandbox refused.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct VideoWorkerRenderSandboxSummary {
    pub chromium_major: u32,
    pub frames_captured: u32,
    pub output_bytes: u64,
    pub blocked_requests: u32,
    pub blocked_navigations: u32,
    pub blocked_downloads: u32,
    pub blocked_popups: u32,
    pub blocked_dialogs: u32,
}

#[derive(Clone)]
pub struct VideoWorkerScriptModelConfiguration {
    model_id: String,
    api_key: Zeroizing<String>,
}

impl fmt::Debug for VideoWorkerScriptModelConfiguration {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VideoWorkerScriptModelConfiguration")
            .field("source_provider", &"bailian")
            .field("upstream_provider", &"openai")
            .field("model_id", &self.model_id)
            .finish_non_exhaustive()
    }
}

impl VideoWorkerScriptModelConfiguration {
    pub fn bailian(
        model_id: impl Into<String>,
        api_key: impl Into<String>,
    ) -> Result<Self, VideoWorkerError> {
        let model_id = model_id.into();
        let api_key = Zeroizing::new(api_key.into());
        if !matches!(
            model_id.as_str(),
            "deepseek-v4-pro" | "glm-5.2" | "qwen3.7-max-2026-06-08"
        ) || !valid_model_api_key(&api_key)
        {
            return Err(configuration_invalid());
        }
        Ok(Self { model_id, api_key })
    }

    pub fn model_id(&self) -> &str {
        &self.model_id
    }

    fn same_secret_configuration(&self, other: &Self) -> bool {
        self.model_id == other.model_id && self.api_key.as_bytes() == other.api_key.as_bytes()
    }
}

impl VideoWorkerLaunch {
    pub fn new(
        kind: VideoWorkerKind,
        executable_path: PathBuf,
        asset_root: PathBuf,
        expected_version: String,
        restart_policy: VideoWorkerRestartPolicy,
    ) -> Result<Self, VideoWorkerError> {
        validate_executable_path(&executable_path)?;
        validate_directory_path(&asset_root)?;
        if expected_version.is_empty()
            || expected_version.len() > MAX_VERSION_BYTES
            || Version::parse(&expected_version).is_err()
        {
            return Err(configuration_invalid());
        }
        Ok(Self {
            kind,
            executable_path: child_process_path(&executable_path),
            arguments: Vec::new(),
            isolated_environment: false,
            environment: BTreeMap::new(),
            media_tools: None,
            asset_root: child_process_path(&asset_root),
            expected_version,
            restart_policy,
            script_model: None,
            render_browser: None,
            web_ui: false,
        })
    }

    pub fn bundled_node(
        package_root: &Path,
        asset_root: PathBuf,
        restart_policy: VideoWorkerRestartPolicy,
    ) -> Result<Self, VideoWorkerError> {
        validate_directory_path(package_root)?;
        #[cfg(windows)]
        let executable_path = package_root.join("runtime/node.exe");
        #[cfg(not(windows))]
        let executable_path = package_root.join("runtime/node");
        let entrypoint = package_root.join("app/worker.mjs");
        validate_executable_path(&executable_path)?;
        validate_regular_file_path(&entrypoint)?;
        let mut launch = Self::new(
            VideoWorkerKind::Node,
            executable_path,
            asset_root,
            MOTION_VIDEO_WORKER_VERSION.to_owned(),
            restart_policy,
        )?;
        launch
            .arguments
            .push(child_process_path(&entrypoint).into_os_string());
        launch.isolated_environment = true;
        Ok(launch)
    }

    /// Bind one validated script to a Python Worker runtime.
    ///
    /// This is single-assignment and switches the child to the same cleared
    /// environment used by the bundled Node runtime. Native media-tool paths
    /// remain on the authenticated bootstrap channel rather than argv.
    pub fn with_python_entrypoint(mut self, entrypoint: PathBuf) -> Result<Self, VideoWorkerError> {
        if self.kind != VideoWorkerKind::Python
            || !self.arguments.is_empty()
            || entrypoint == self.executable_path
        {
            return Err(configuration_invalid());
        }
        validate_regular_file_path(&entrypoint)?;
        self.arguments.push(entrypoint.into_os_string());
        self.isolated_environment = true;
        Ok(self)
    }

    /// Tell the Worker where a packaged native dependency lives.
    ///
    /// Both video engines resolve FFmpeg through an environment variable
    /// first and search the user's machine when it is absent, so a Worker
    /// started without this runs whatever build that machine happens to carry.
    /// Every value is re-validated as an executable file belonging to the
    /// caller's verified package: a launch is refused rather than started with
    /// a variable that points at nothing.
    pub fn with_environment(
        mut self,
        environment: BTreeMap<&'static str, &Path>,
    ) -> Result<Self, VideoWorkerError> {
        if environment.is_empty()
            || self.environment.len().saturating_add(environment.len()) > MAX_ENVIRONMENT_ENTRIES
        {
            return Err(configuration_invalid());
        }
        for (name, value) in environment {
            if !valid_environment_name(name) {
                return Err(configuration_invalid());
            }
            validate_executable_path(value)?;
            if self
                .environment
                .insert(name, child_process_path(value))
                .is_some()
            {
                return Err(configuration_invalid());
            }
        }
        Ok(self)
    }

    pub fn with_script_model(mut self, configuration: VideoWorkerScriptModelConfiguration) -> Self {
        self.script_model = Some(configuration);
        self
    }

    pub fn with_media_tools(
        mut self,
        configuration: VideoWorkerMediaToolsConfiguration,
    ) -> Result<Self, VideoWorkerError> {
        if self.kind != VideoWorkerKind::Python || self.media_tools.is_some() {
            return Err(configuration_invalid());
        }
        self.media_tools = Some(configuration);
        Ok(self)
    }

    pub fn with_render_browser(
        mut self,
        configuration: VideoWorkerRenderBrowserConfiguration,
    ) -> Self {
        self.render_browser = Some(configuration);
        self
    }

    pub fn with_web_ui(mut self) -> Self {
        self.web_ui = true;
        self
    }
}

#[derive(Clone)]
pub struct VideoWorkerWebUiEndpoint {
    port: u16,
    path: String,
}

impl fmt::Debug for VideoWorkerWebUiEndpoint {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("VideoWorkerWebUiEndpoint(<redacted>)")
    }
}

impl VideoWorkerWebUiEndpoint {
    pub(crate) fn url(&self) -> Result<url::Url, VideoWorkerError> {
        url::Url::parse(&format!(
            "http://{LOOPBACK_HOST}:{}/{}/",
            self.port, self.path
        ))
        .map_err(|_| process_unavailable())
    }

    pub(crate) const fn port(&self) -> u16 {
        self.port
    }

    pub(crate) fn path(&self) -> &str {
        &self.path
    }
}

#[derive(Clone)]
struct VideoWorkerMaterialPreviewEndpoint {
    port: u16,
    path: String,
}

impl fmt::Debug for VideoWorkerMaterialPreviewEndpoint {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("VideoWorkerMaterialPreviewEndpoint(<redacted>)")
    }
}

impl VideoWorkerMaterialPreviewEndpoint {
    fn url(&self, material_id: &str) -> Result<String, VideoWorkerLocalMaterialError> {
        let url = url::Url::parse(&format!(
            "http://{LOOPBACK_HOST}:{}/api/v1/material-previews/{}/{material_id}",
            self.port, self.path,
        ))
        .map_err(|_| {
            VideoWorkerLocalMaterialError::Lifecycle(VideoWorkerErrorCode::ProcessUnavailable)
        })?;
        Ok(url.into())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct VideoWorkerStatus {
    kind: VideoWorkerKind,
    state: VideoWorkerState,
    worker_version: Option<String>,
    host: Option<&'static str>,
    port: Option<u16>,
    process_id: Option<u32>,
    restart_count: u8,
    script_model_id: Option<String>,
    web_ui_available: bool,
}

impl VideoWorkerStatus {
    fn stopped(kind: VideoWorkerKind, restart_count: u8) -> Self {
        Self {
            kind,
            state: VideoWorkerState::Stopped,
            worker_version: None,
            host: None,
            port: None,
            process_id: None,
            restart_count,
            script_model_id: None,
            web_ui_available: false,
        }
    }

    fn running(
        kind: VideoWorkerKind,
        worker_version: String,
        port: u16,
        process_id: u32,
        restart_count: u8,
        script_model_id: Option<String>,
        web_ui_available: bool,
    ) -> Self {
        Self {
            kind,
            state: VideoWorkerState::Running,
            worker_version: Some(worker_version),
            host: Some(LOOPBACK_HOST),
            port: Some(port),
            process_id: Some(process_id),
            restart_count,
            script_model_id,
            web_ui_available,
        }
    }

    pub const fn state(&self) -> VideoWorkerState {
        self.state
    }

    pub fn worker_version(&self) -> Option<&str> {
        self.worker_version.as_deref()
    }

    pub const fn host(&self) -> Option<&str> {
        self.host
    }

    pub const fn port(&self) -> Option<u16> {
        self.port
    }

    pub const fn process_id(&self) -> Option<u32> {
        self.process_id
    }

    pub const fn restart_count(&self) -> u8 {
        self.restart_count
    }

    pub fn script_model_id(&self) -> Option<&str> {
        self.script_model_id.as_deref()
    }

    pub const fn web_ui_available(&self) -> bool {
        self.web_ui_available
    }
}

pub struct LocalVideoOrchestrator {
    start_timeout: Duration,
    request_timeout: Duration,
    workers: Mutex<BTreeMap<VideoWorkerKind, RunningVideoWorker>>,
}

impl LocalVideoOrchestrator {
    pub fn new(
        start_timeout: Duration,
        request_timeout: Duration,
    ) -> Result<Self, VideoWorkerError> {
        if start_timeout.is_zero()
            || request_timeout.is_zero()
            || start_timeout > MAX_TIMEOUT
            || request_timeout > MAX_TIMEOUT
        {
            return Err(configuration_invalid());
        }
        Ok(Self {
            start_timeout,
            request_timeout,
            workers: Mutex::new(BTreeMap::new()),
        })
    }

    pub fn start(&self, launch: VideoWorkerLaunch) -> Result<VideoWorkerStatus, VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        if workers.contains_key(&launch.kind) {
            return Err(VideoWorkerError::new(VideoWorkerErrorCode::AlreadyRunning));
        }
        let running = spawn_worker(launch.clone(), self.start_timeout, 0)?;
        let status = running.status.clone();
        workers.insert(launch.kind, running);
        Ok(status)
    }

    pub fn status(&self, kind: VideoWorkerKind) -> Result<VideoWorkerStatus, VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        let Some(running) = workers.get_mut(&kind) else {
            return Ok(VideoWorkerStatus::stopped(kind, 0));
        };
        let Some(exit_status) = running
            .child
            .try_wait()
            .map_err(|_| process_unavailable())?
        else {
            return Ok(running.status.clone());
        };
        let restart_count = running.status.restart_count;
        if exit_status.success() || restart_count >= running.launch.restart_policy.maximum_restarts
        {
            let mut stopped = workers.remove(&kind).expect("worker entry exists");
            finish_exited_worker(&mut stopped);
            return Ok(VideoWorkerStatus::stopped(kind, restart_count));
        }

        let launch = running.launch.clone();
        let next_restart_count = restart_count.saturating_add(1);
        let delay = launch.restart_policy.restart_delay;
        let mut crashed = workers.remove(&kind).expect("worker entry exists");
        finish_exited_worker(&mut crashed);
        if !delay.is_zero() {
            std::thread::sleep(delay);
        }
        let replacement = spawn_worker(launch, self.start_timeout, next_restart_count)?;
        let status = replacement.status.clone();
        workers.insert(kind, replacement);
        Ok(status)
    }

    pub fn health(&self, kind: VideoWorkerKind) -> Result<(), VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        let running = workers.get_mut(&kind).ok_or_else(not_running)?;
        if running
            .child
            .try_wait()
            .map_err(|_| process_unavailable())?
            .is_some()
        {
            return Err(process_unavailable());
        }
        verify_health(running, self.request_timeout)
    }

    pub fn import_local_material(
        &self,
        material_id: Uuid,
        source_path: &Path,
    ) -> Result<VideoWorkerLocalMaterialFacts, VideoWorkerLocalMaterialError> {
        let source_path = valid_local_material_source_path(source_path).ok_or(
            VideoWorkerLocalMaterialError::Lifecycle(VideoWorkerErrorCode::ConfigurationInvalid),
        )?;
        let material_id = valid_local_material_id(material_id)?;
        let mut workers = self
            .lock_workers()
            .map_err(VideoWorkerLocalMaterialError::from)?;
        let running = local_material_worker(&mut workers)?;
        let authentication_proof = running
            .token
            .command_proof_with_detail(
                VideoWorkerKind::Python,
                "worker.material.import",
                &material_id,
                Some(source_path),
            )
            .map_err(VideoWorkerLocalMaterialError::from)?;
        let command = VideoWorkerLocalMaterialImportCommandDocument {
            authentication_proof: &authentication_proof,
            command: "worker.material.import",
            material_id: &material_id,
            protocol_version: WORKER_PROTOCOL_VERSION,
            source_path,
            worker_kind: VideoWorkerKind::Python.as_str(),
        };
        write_command(&mut running.stdin, &command).map_err(VideoWorkerLocalMaterialError::from)?;
        let line = receive_line(&running.events, self.request_timeout)
            .map_err(VideoWorkerLocalMaterialError::from)?;
        if let Ok(event) = serde_json::from_str::<VideoWorkerLocalMaterialImportedEvent>(&line) {
            if !event.facts.is_valid() {
                return Err(VideoWorkerLocalMaterialError::Lifecycle(
                    VideoWorkerErrorCode::AuthenticationRejected,
                ));
            }
            let canonical = event.facts.canonical_json().map_err(|_| {
                VideoWorkerLocalMaterialError::Lifecycle(
                    VideoWorkerErrorCode::AuthenticationRejected,
                )
            })?;
            let detail = format!("{material_id}\0{canonical}");
            if !valid_local_material_event(
                running,
                "worker.material.imported",
                &material_id,
                &detail,
                &event.authentication(),
            ) {
                return Err(VideoWorkerLocalMaterialError::Lifecycle(
                    VideoWorkerErrorCode::AuthenticationRejected,
                ));
            }
            return Ok(event.facts);
        }
        let event: VideoWorkerLocalMaterialFailedEvent =
            serde_json::from_str(&line).map_err(|_| {
                VideoWorkerLocalMaterialError::Lifecycle(
                    VideoWorkerErrorCode::AuthenticationRejected,
                )
            })?;
        let detail = format!("{material_id}\0{}", event.failure_code.as_str());
        if !valid_local_material_event(
            running,
            "worker.material.import_failed",
            &material_id,
            &detail,
            &event.authentication(),
        ) {
            return Err(VideoWorkerLocalMaterialError::Lifecycle(
                VideoWorkerErrorCode::AuthenticationRejected,
            ));
        }
        Err(VideoWorkerLocalMaterialError::Rejected(event.failure_code))
    }

    pub fn forget_local_material(
        &self,
        material_id: Uuid,
    ) -> Result<(), VideoWorkerLocalMaterialError> {
        let material_id = valid_local_material_id(material_id)?;
        let mut workers = self
            .lock_workers()
            .map_err(VideoWorkerLocalMaterialError::from)?;
        let running = local_material_worker(&mut workers)?;
        let authentication_proof = running
            .token
            .command_proof(
                VideoWorkerKind::Python,
                "worker.material.forget",
                &material_id,
            )
            .map_err(VideoWorkerLocalMaterialError::from)?;
        let command = VideoWorkerLocalMaterialCommandDocument {
            authentication_proof: &authentication_proof,
            command: "worker.material.forget",
            material_id: &material_id,
            protocol_version: WORKER_PROTOCOL_VERSION,
            worker_kind: VideoWorkerKind::Python.as_str(),
        };
        write_command(&mut running.stdin, &command).map_err(VideoWorkerLocalMaterialError::from)?;
        let line = receive_line(&running.events, self.request_timeout)
            .map_err(VideoWorkerLocalMaterialError::from)?;
        if let Ok(event) = serde_json::from_str::<VideoWorkerLocalMaterialForgottenEvent>(&line) {
            if valid_local_material_event(
                running,
                "worker.material.forgotten",
                &material_id,
                &material_id,
                &event.authentication(),
            ) {
                return Ok(());
            }
            return Err(VideoWorkerLocalMaterialError::Lifecycle(
                VideoWorkerErrorCode::AuthenticationRejected,
            ));
        }
        let event: VideoWorkerLocalMaterialFailedEvent =
            serde_json::from_str(&line).map_err(|_| {
                VideoWorkerLocalMaterialError::Lifecycle(
                    VideoWorkerErrorCode::AuthenticationRejected,
                )
            })?;
        let detail = format!("{material_id}\0{}", event.failure_code.as_str());
        if !valid_local_material_event(
            running,
            "worker.material.forget_failed",
            &material_id,
            &detail,
            &event.authentication(),
        ) {
            return Err(VideoWorkerLocalMaterialError::Lifecycle(
                VideoWorkerErrorCode::AuthenticationRejected,
            ));
        }
        Err(VideoWorkerLocalMaterialError::Rejected(event.failure_code))
    }

    pub fn local_material_status(
        &self,
        material_id: Uuid,
    ) -> Result<VideoWorkerLocalMaterialStatus, VideoWorkerLocalMaterialError> {
        let material_id = valid_local_material_id(material_id)?;
        let mut workers = self
            .lock_workers()
            .map_err(VideoWorkerLocalMaterialError::from)?;
        let running = local_material_worker(&mut workers)?;
        let authentication_proof = running
            .token
            .command_proof(
                VideoWorkerKind::Python,
                "worker.material.status",
                &material_id,
            )
            .map_err(VideoWorkerLocalMaterialError::from)?;
        let command = VideoWorkerLocalMaterialCommandDocument {
            authentication_proof: &authentication_proof,
            command: "worker.material.status",
            material_id: &material_id,
            protocol_version: WORKER_PROTOCOL_VERSION,
            worker_kind: VideoWorkerKind::Python.as_str(),
        };
        write_command(&mut running.stdin, &command).map_err(VideoWorkerLocalMaterialError::from)?;
        let line = receive_line(&running.events, self.request_timeout)
            .map_err(VideoWorkerLocalMaterialError::from)?;
        let event: VideoWorkerLocalMaterialStatusEvent =
            serde_json::from_str(&line).map_err(|_| {
                VideoWorkerLocalMaterialError::Lifecycle(
                    VideoWorkerErrorCode::AuthenticationRejected,
                )
            })?;
        let detail = format!("{material_id}\0{}", event.status.as_str());
        if !valid_local_material_event(
            running,
            "worker.material.status",
            &material_id,
            &detail,
            &event.authentication(),
        ) {
            return Err(VideoWorkerLocalMaterialError::Lifecycle(
                VideoWorkerErrorCode::AuthenticationRejected,
            ));
        }
        Ok(event.status)
    }

    pub fn local_material_preview_url(
        &self,
        material_id: Uuid,
    ) -> Result<String, VideoWorkerLocalMaterialError> {
        let material_id = valid_local_material_id(material_id)?;
        let mut workers = self
            .lock_workers()
            .map_err(VideoWorkerLocalMaterialError::from)?;
        let running = local_material_worker(&mut workers)?;
        let endpoint =
            running
                .material_preview
                .as_ref()
                .ok_or(VideoWorkerLocalMaterialError::Lifecycle(
                    VideoWorkerErrorCode::AuthenticationRejected,
                ))?;
        endpoint.url(&material_id)
    }

    pub fn cancel(&self, kind: VideoWorkerKind, job_id: Uuid) -> Result<(), VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        let running = workers.get_mut(&kind).ok_or_else(not_running)?;
        if running.editing_job.is_some() || running.smart_edit_job.is_some() {
            return Err(configuration_invalid());
        }
        let job_id = job_id.hyphenated().to_string();
        let authentication_proof = running
            .token
            .command_proof(kind, "worker.cancel", &job_id)?;
        let command = VideoWorkerCommandDocument {
            command: "worker.cancel",
            job_id: &job_id,
            protocol_version: WORKER_PROTOCOL_VERSION,
            worker_kind: kind.as_str(),
            authentication_proof: &authentication_proof,
        };
        let mut bytes =
            Zeroizing::new(serde_json::to_vec(&command).map_err(|_| process_unavailable())?);
        bytes.push(b'\n');
        running
            .stdin
            .write_all(&bytes)
            .and_then(|()| running.stdin.flush())
            .map_err(|_| process_unavailable())?;
        let line = receive_line(&running.events, self.request_timeout)?;
        let event: VideoWorkerCancelledEvent =
            serde_json::from_str(&line).map_err(|_| authentication_rejected())?;
        if event.event != "worker.cancelled"
            || event.job_id != job_id
            || event.protocol_version != WORKER_PROTOCOL_VERSION
            || event.worker_kind != kind.as_str()
            || event.worker_version != running.launch.expected_version
            || !running.token.verify_event_proof(
                "worker.cancelled",
                kind,
                &event.worker_version,
                &event.job_id,
                &event.authentication_proof,
            )
        {
            return Err(authentication_rejected());
        }
        Ok(())
    }

    /// Dispatch one path-free local-editing job to the authenticated Python
    /// Worker. Progress and terminal events are consumed with
    /// [`Self::try_local_editing_event`], keeping cancellation non-blocking.
    pub fn start_local_editing_job(
        &self,
        job_id: Uuid,
        request: &VideoWorkerLocalEditingJobRequest,
    ) -> Result<(), VideoWorkerError> {
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let mut workers = self.lock_workers()?;
        let running = workers
            .get_mut(&VideoWorkerKind::Python)
            .ok_or_else(not_running)?;
        if running.launch.media_tools.is_none()
            || running.editing_job.is_some()
            || running.smart_edit_job.is_some()
        {
            return Err(configuration_invalid());
        }
        if running
            .child
            .try_wait()
            .map_err(|_| process_unavailable())?
            .is_some()
        {
            return Err(process_unavailable());
        }
        let job_id = job_id.hyphenated().to_string();
        let canonical_editing = request.canonical_json()?;
        let authentication_proof = running.token.command_proof_with_detail(
            VideoWorkerKind::Python,
            "worker.editing.start",
            &job_id,
            Some(&canonical_editing),
        )?;
        let command = VideoWorkerLocalEditingStartCommandDocument {
            authentication_proof: &authentication_proof,
            command: "worker.editing.start",
            editing: request.document(),
            job_id: &job_id,
            protocol_version: WORKER_PROTOCOL_VERSION,
            worker_kind: VideoWorkerKind::Python.as_str(),
        };
        write_command(&mut running.stdin, &command)?;
        running.editing_job = Some(RunningLocalEditingJob {
            job_id,
            phase: None,
            progress_per_mille: 0,
            cancelling: false,
            terminal: false,
        });
        Ok(())
    }

    /// Consume at most one already-emitted editing event without holding the
    /// process lock while waiting. Empty means the caller should poll again.
    pub fn try_local_editing_event(
        &self,
        job_id: Uuid,
    ) -> Result<Option<VideoWorkerLocalEditingEvent>, VideoWorkerError> {
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let mut workers = self.lock_workers()?;
        let running = workers
            .get_mut(&VideoWorkerKind::Python)
            .ok_or_else(not_running)?;
        let expected_job_id = job_id.hyphenated().to_string();
        let state = running
            .editing_job
            .as_mut()
            .filter(|state| state.job_id == expected_job_id)
            .ok_or_else(configuration_invalid)?;
        let line = match running.events.try_recv() {
            Ok(Ok(line)) => line,
            Ok(Err(())) => return Err(authentication_rejected()),
            Err(TryRecvError::Empty) => return Ok(None),
            Err(TryRecvError::Disconnected) => return Err(process_unavailable()),
        };
        if state.terminal {
            return Err(authentication_rejected());
        }
        let event = parse_local_editing_event(&running.token, &running.launch, state, &line)?;
        Ok(Some(event))
    }

    pub(crate) fn local_editing_job_owner(&self) -> Result<Option<Uuid>, VideoWorkerError> {
        let workers = self.lock_workers()?;
        let Some(running) = workers.get(&VideoWorkerKind::Python) else {
            return Ok(None);
        };
        running
            .editing_job
            .as_ref()
            .map(|state| Uuid::parse_str(&state.job_id).map_err(|_| authentication_rejected()))
            .transpose()
    }

    /// Request cooperative cancellation. The authoritative result remains an
    /// authenticated terminal event because completion can win the race.
    pub fn request_local_editing_cancel(&self, job_id: Uuid) -> Result<(), VideoWorkerError> {
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let mut workers = self.lock_workers()?;
        let running = workers
            .get_mut(&VideoWorkerKind::Python)
            .ok_or_else(not_running)?;
        let job_id = job_id.hyphenated().to_string();
        let state = running
            .editing_job
            .as_mut()
            .filter(|state| state.job_id == job_id && !state.terminal)
            .ok_or_else(configuration_invalid)?;
        if state.cancelling {
            return Err(configuration_invalid());
        }
        let authentication_proof =
            running
                .token
                .command_proof(VideoWorkerKind::Python, "worker.cancel", &job_id)?;
        let command = VideoWorkerCommandDocument {
            command: "worker.cancel",
            job_id: &job_id,
            protocol_version: WORKER_PROTOCOL_VERSION,
            worker_kind: VideoWorkerKind::Python.as_str(),
            authentication_proof: &authentication_proof,
        };
        write_command(&mut running.stdin, &command)?;
        state.cancelling = true;
        Ok(())
    }

    /// Kill the Python Worker process tree immediately. Unlike cooperative
    /// cancel this deliberately has no Worker-authored terminal event.
    pub fn emergency_stop_local_editing_job(&self, job_id: Uuid) -> Result<(), VideoWorkerError> {
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let mut workers = self.lock_workers()?;
        let expected = job_id.hyphenated().to_string();
        let running = workers
            .get(&VideoWorkerKind::Python)
            .ok_or_else(not_running)?;
        if !running
            .editing_job
            .as_ref()
            .is_some_and(|state| state.job_id == expected && !state.terminal)
        {
            return Err(configuration_invalid());
        }
        let mut running = workers
            .remove(&VideoWorkerKind::Python)
            .expect("checked Python Worker exists");
        force_stop(&mut running);
        Ok(())
    }

    /// Acknowledge that the scheduler durably consumed a terminal event before
    /// the Worker may accept another local-editing job.
    pub fn finish_local_editing_job(&self, job_id: Uuid) -> Result<(), VideoWorkerError> {
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let mut workers = self.lock_workers()?;
        let running = workers
            .get_mut(&VideoWorkerKind::Python)
            .ok_or_else(not_running)?;
        let expected = job_id.hyphenated().to_string();
        if !running
            .editing_job
            .as_ref()
            .is_some_and(|state| state.job_id == expected && state.terminal)
        {
            return Err(configuration_invalid());
        }
        running.editing_job = None;
        Ok(())
    }

    /// Stage one private smart-edit request and start its authenticated
    /// two-phase Worker transaction. The prompt and material facts are written
    /// only below the Worker's private asset root and never enter argv, logs or
    /// the command line protocol.
    pub fn start_smart_edit_job(
        &self,
        job_id: Uuid,
        request: &VideoWorkerSmartEditRequest,
    ) -> Result<(), VideoWorkerError> {
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let mut workers = self.lock_workers()?;
        let running = workers
            .get_mut(&VideoWorkerKind::Python)
            .ok_or_else(not_running)?;
        if running.launch.media_tools.is_none()
            || running.launch.script_model.is_none()
            || running.editing_job.is_some()
            || running.smart_edit_job.is_some()
            || running
                .child
                .try_wait()
                .map_err(|_| process_unavailable())?
                .is_some()
        {
            return Err(configuration_invalid());
        }
        let job_id = job_id.hyphenated().to_string();
        let job_root = write_smart_edit_request(&running.launch.asset_root, &job_id, request)?;
        let outcome = (|| {
            let authentication_proof = running.token.command_proof(
                VideoWorkerKind::Python,
                "worker.smart_edit.start",
                &job_id,
            )?;
            let command = VideoWorkerCommandDocument {
                command: "worker.smart_edit.start",
                job_id: &job_id,
                protocol_version: WORKER_PROTOCOL_VERSION,
                worker_kind: VideoWorkerKind::Python.as_str(),
                authentication_proof: &authentication_proof,
            };
            write_command(&mut running.stdin, &command)
        })();
        if let Err(error) = outcome {
            let _ = fs::remove_dir_all(&job_root);
            return Err(error);
        }
        running.smart_edit_job = Some(RunningSmartEditJob {
            job_id,
            job_root,
            stage: None,
            progress_per_mille: 0,
            cancelling: false,
            prepared_digest: None,
            terminal: false,
        });
        Ok(())
    }

    pub fn try_smart_edit_event(
        &self,
        job_id: Uuid,
    ) -> Result<Option<VideoWorkerSmartEditEvent>, VideoWorkerError> {
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let mut workers = self.lock_workers()?;
        let running = workers
            .get_mut(&VideoWorkerKind::Python)
            .ok_or_else(not_running)?;
        let expected_job_id = job_id.hyphenated().to_string();
        let state = running
            .smart_edit_job
            .as_mut()
            .filter(|state| state.job_id == expected_job_id && !state.terminal)
            .ok_or_else(configuration_invalid)?;
        let line = match running.events.try_recv() {
            Ok(Ok(line)) => line,
            Ok(Err(())) => return Err(authentication_rejected()),
            Err(TryRecvError::Empty) => return Ok(None),
            Err(TryRecvError::Disconnected) => return Err(process_unavailable()),
        };
        let event = parse_smart_edit_event(&running.token, &running.launch, state, &line)?;
        Ok(Some(event))
    }

    pub fn request_smart_edit_cancel(&self, job_id: Uuid) -> Result<(), VideoWorkerError> {
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let mut workers = self.lock_workers()?;
        let running = workers
            .get_mut(&VideoWorkerKind::Python)
            .ok_or_else(not_running)?;
        let job_id = job_id.hyphenated().to_string();
        let state = running
            .smart_edit_job
            .as_mut()
            .filter(|state| {
                state.job_id == job_id
                    && !state.terminal
                    && state.prepared_digest.is_none()
                    && !state.cancelling
            })
            .ok_or_else(configuration_invalid)?;
        let authentication_proof =
            running
                .token
                .command_proof(VideoWorkerKind::Python, "worker.cancel", &job_id)?;
        write_command(
            &mut running.stdin,
            &VideoWorkerCommandDocument {
                command: "worker.cancel",
                job_id: &job_id,
                protocol_version: WORKER_PROTOCOL_VERSION,
                worker_kind: VideoWorkerKind::Python.as_str(),
                authentication_proof: &authentication_proof,
            },
        )?;
        state.cancelling = true;
        Ok(())
    }

    pub fn commit_smart_edit_job(&self, job_id: Uuid) -> Result<(), VideoWorkerError> {
        self.finalize_smart_edit_job(job_id, true)
    }

    pub fn abort_smart_edit_job(&self, job_id: Uuid) -> Result<(), VideoWorkerError> {
        self.finalize_smart_edit_job(job_id, false)
    }

    /// Kill a smart-edit Worker transaction whose authenticated event stream
    /// can no longer be trusted, then remove its host-owned private staging.
    pub fn emergency_stop_smart_edit_job(&self, job_id: Uuid) -> Result<(), VideoWorkerError> {
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let mut workers = self.lock_workers()?;
        let expected_job_id = job_id.hyphenated().to_string();
        let running = workers
            .get(&VideoWorkerKind::Python)
            .ok_or_else(not_running)?;
        let job_root = running
            .smart_edit_job
            .as_ref()
            .filter(|state| state.job_id == expected_job_id)
            .map(|state| state.job_root.clone())
            .ok_or_else(configuration_invalid)?;
        let expected_job_root = running
            .launch
            .asset_root
            .join("local-executor/smart-edit/jobs")
            .join(&expected_job_id);
        if job_root != expected_job_root {
            return Err(authentication_rejected());
        }
        let mut running = workers
            .remove(&VideoWorkerKind::Python)
            .expect("checked Python Worker exists");
        force_stop(&mut running);
        drop(workers);
        remove_private_smart_edit_job_root(&job_root)
    }

    fn finalize_smart_edit_job(&self, job_id: Uuid, commit: bool) -> Result<(), VideoWorkerError> {
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let mut workers = self.lock_workers()?;
        let running = workers
            .get_mut(&VideoWorkerKind::Python)
            .ok_or_else(not_running)?;
        let job_id = job_id.hyphenated().to_string();
        let state = running
            .smart_edit_job
            .as_ref()
            .filter(|state| {
                state.job_id == job_id
                    && !state.terminal
                    && state.prepared_digest.is_some()
                    && (!commit || !state.cancelling)
            })
            .ok_or_else(configuration_invalid)?;
        let digest = state
            .prepared_digest
            .as_deref()
            .ok_or_else(configuration_invalid)?
            .to_owned();
        let command_name = if commit {
            "worker.smart_edit.commit"
        } else {
            "worker.smart_edit.abort"
        };
        let authentication_proof =
            running
                .token
                .command_proof(VideoWorkerKind::Python, command_name, &job_id)?;
        write_command(
            &mut running.stdin,
            &VideoWorkerCommandDocument {
                command: command_name,
                job_id: &job_id,
                protocol_version: WORKER_PROTOCOL_VERSION,
                worker_kind: VideoWorkerKind::Python.as_str(),
                authentication_proof: &authentication_proof,
            },
        )?;
        let line = receive_line(&running.events, self.request_timeout)?;
        let accepted = if commit {
            valid_smart_edit_succeeded(&running.token, &running.launch, &job_id, &digest, &line)?
        } else {
            valid_smart_edit_aborted(&running.token, &running.launch, &job_id, &line)?
        };
        if !accepted {
            if let Some(state) = running.smart_edit_job.as_mut() {
                state.terminal = true;
            }
            return Err(VideoWorkerError::new(VideoWorkerErrorCode::RenderRejected));
        }
        remove_private_smart_edit_job_root(&state.job_root)?;
        running.smart_edit_job = None;
        Ok(())
    }

    pub fn finish_smart_edit_job(&self, job_id: Uuid) -> Result<(), VideoWorkerError> {
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let mut workers = self.lock_workers()?;
        let running = workers
            .get_mut(&VideoWorkerKind::Python)
            .ok_or_else(not_running)?;
        let expected = job_id.hyphenated().to_string();
        let state = running
            .smart_edit_job
            .as_ref()
            .filter(|state| state.job_id == expected && state.terminal)
            .ok_or_else(configuration_invalid)?;
        remove_private_smart_edit_job_root(&state.job_root)?;
        running.smart_edit_job = None;
        Ok(())
    }

    pub(crate) fn smart_edit_job_owner(&self) -> Result<Option<Uuid>, VideoWorkerError> {
        let workers = self.lock_workers()?;
        let Some(running) = workers.get(&VideoWorkerKind::Python) else {
            return Ok(None);
        };
        running
            .smart_edit_job
            .as_ref()
            .map(|state| Uuid::parse_str(&state.job_id).map_err(|_| authentication_rejected()))
            .transpose()
    }

    pub(crate) fn worker_uses_script_model(
        &self,
        expected: &VideoWorkerScriptModelConfiguration,
    ) -> Result<bool, VideoWorkerError> {
        let workers = self.lock_workers()?;
        Ok(workers
            .get(&VideoWorkerKind::Python)
            .and_then(|running| running.launch.script_model.as_ref())
            .is_some_and(|actual| actual.same_secret_configuration(expected)))
    }

    pub(crate) fn rollback_committed_smart_edit(
        &self,
        job_id: Uuid,
        material_ids: &[Uuid],
    ) -> Result<(), VideoWorkerError> {
        if !valid_uuid_v4(job_id)
            || material_ids.len() > 32
            || material_ids.iter().any(|value| !valid_uuid_v4(*value))
        {
            return Err(configuration_invalid());
        }
        let mut failed = false;
        for material_id in material_ids {
            if self.forget_local_material(*material_id).is_err() {
                failed = true;
            }
        }
        let workers = self.lock_workers()?;
        let running = workers
            .get(&VideoWorkerKind::Python)
            .ok_or_else(not_running)?;
        let durable = running
            .launch
            .asset_root
            .join("local-executor/generated-materials")
            .join(job_id.hyphenated().to_string());
        drop(workers);
        if durable.exists()
            && (validate_directory_path(&durable).is_err() || fs::remove_dir_all(durable).is_err())
        {
            failed = true;
        }
        if failed {
            return Err(process_unavailable());
        }
        Ok(())
    }

    /// Ask the Node Worker to launch the configured embedded Chromium as an
    /// independent headless process inside a fresh RenderJob directory and
    /// prove that the actual Chromium major matches the verified expectation.
    pub fn render_verify(
        &self,
        kind: VideoWorkerKind,
        job_id: Uuid,
    ) -> Result<u32, VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        let running = workers.get_mut(&kind).ok_or_else(not_running)?;
        let render_browser = running
            .launch
            .render_browser
            .as_ref()
            .ok_or_else(configuration_invalid)?;
        // The Worker runs the browser twice (version probe and capture), each
        // bounded by the launch timeout it received in its bootstrap.
        let wait = self.request_timeout + 2 * render_browser.launch_timeout;
        let expected_major = render_browser.chromium_major;
        let job_id = job_id.hyphenated().to_string();
        let authentication_proof =
            running
                .token
                .command_proof(kind, "worker.render.verify", &job_id)?;
        let command = VideoWorkerCommandDocument {
            command: "worker.render.verify",
            job_id: &job_id,
            protocol_version: WORKER_PROTOCOL_VERSION,
            worker_kind: kind.as_str(),
            authentication_proof: &authentication_proof,
        };
        let mut bytes =
            Zeroizing::new(serde_json::to_vec(&command).map_err(|_| process_unavailable())?);
        bytes.push(b'\n');
        running
            .stdin
            .write_all(&bytes)
            .and_then(|()| running.stdin.flush())
            .map_err(|_| process_unavailable())?;
        let line = receive_line(&running.events, wait)?;
        if let Ok(event) = serde_json::from_str::<VideoWorkerRenderVerifiedEvent>(&line) {
            let detail = format!("{job_id}\0{}", event.chromium_major);
            if event.event != "worker.render.verified"
                || event.job_id != job_id
                || event.protocol_version != WORKER_PROTOCOL_VERSION
                || event.worker_kind != kind.as_str()
                || event.worker_version != running.launch.expected_version
                || event.chromium_major != expected_major
                || !running.token.verify_event_proof(
                    "worker.render.verified",
                    kind,
                    &event.worker_version,
                    &detail,
                    &event.authentication_proof,
                )
            {
                return Err(authentication_rejected());
            }
            return Ok(event.chromium_major);
        }
        let event: VideoWorkerRenderFailedEvent =
            serde_json::from_str(&line).map_err(|_| authentication_rejected())?;
        let detail = format!("{job_id}\0{}", event.reason_code);
        if event.event != "worker.render.failed"
            || event.job_id != job_id
            || event.protocol_version != WORKER_PROTOCOL_VERSION
            || event.worker_kind != kind.as_str()
            || event.worker_version != running.launch.expected_version
            || !valid_render_reason_code(&event.reason_code)
            || !running.token.verify_event_proof(
                "worker.render.failed",
                kind,
                &event.worker_version,
                &detail,
                &event.authentication_proof,
            )
        {
            return Err(authentication_rejected());
        }
        Err(VideoWorkerError::new(VideoWorkerErrorCode::RenderRejected))
    }

    /// Ask the Node Worker to render one RenderJob's HTML inside the sandbox:
    /// private workspace containment, default offline, request allowlist,
    /// navigation/download/popup/dialog interception, and CPU/memory/frame/
    /// output/wall budgets. Returns the authenticated block-and-frame summary
    /// or a fail-closed [`VideoWorkerErrorCode::RenderRejected`].
    pub fn render_sandbox(
        &self,
        kind: VideoWorkerKind,
        job_id: Uuid,
        request: &VideoWorkerRenderSandboxRequest,
    ) -> Result<VideoWorkerRenderSandboxSummary, VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        let running = workers.get_mut(&kind).ok_or_else(not_running)?;
        let render_browser = running
            .launch
            .render_browser
            .as_ref()
            .ok_or_else(configuration_invalid)?;
        let expected_major = render_browser.chromium_major;
        // The Worker runs a version probe (launch timeout) and then the render
        // pass (its own wall budget) before answering.
        let wait = self.request_timeout
            + render_browser.launch_timeout
            + Duration::from_secs(u64::from(request.max_duration_seconds));
        let job_id = job_id.hyphenated().to_string();
        let canonical_sandbox = request.canonical_json()?;
        let authentication_proof =
            running
                .token
                .sandbox_command_proof(kind, &job_id, &canonical_sandbox)?;
        let command = VideoWorkerSandboxCommandDocument {
            authentication_proof: &authentication_proof,
            command: "worker.render.sandbox",
            job_id: &job_id,
            protocol_version: WORKER_PROTOCOL_VERSION,
            sandbox: request.document()?,
            worker_kind: kind.as_str(),
        };
        let mut bytes =
            Zeroizing::new(serde_json::to_vec(&command).map_err(|_| process_unavailable())?);
        bytes.push(b'\n');
        running
            .stdin
            .write_all(&bytes)
            .and_then(|()| running.stdin.flush())
            .map_err(|_| process_unavailable())?;
        let line = receive_line(&running.events, wait)?;
        if let Ok(event) = serde_json::from_str::<VideoWorkerRenderSandboxedEvent>(&line) {
            let detail = format!(
                "{job_id}\0{}\0{}\0{}\0{}\0{}\0{}\0{}\0{}",
                event.chromium_major,
                event.frames_captured,
                event.output_bytes,
                event.blocked_requests,
                event.blocked_navigations,
                event.blocked_downloads,
                event.blocked_popups,
                event.blocked_dialogs,
            );
            if event.event != "worker.render.sandboxed"
                || event.job_id != job_id
                || event.protocol_version != WORKER_PROTOCOL_VERSION
                || event.worker_kind != kind.as_str()
                || event.worker_version != running.launch.expected_version
                || event.chromium_major != expected_major
                || event.frames_captured > SANDBOX_FRAMES_MAXIMUM
                || event.output_bytes > SANDBOX_OUTPUT_BYTES_MAXIMUM
                || !running.token.verify_event_proof(
                    "worker.render.sandboxed",
                    kind,
                    &event.worker_version,
                    &detail,
                    &event.authentication_proof,
                )
            {
                return Err(authentication_rejected());
            }
            return Ok(VideoWorkerRenderSandboxSummary {
                chromium_major: event.chromium_major,
                frames_captured: event.frames_captured,
                output_bytes: event.output_bytes,
                blocked_requests: event.blocked_requests,
                blocked_navigations: event.blocked_navigations,
                blocked_downloads: event.blocked_downloads,
                blocked_popups: event.blocked_popups,
                blocked_dialogs: event.blocked_dialogs,
            });
        }
        let event: VideoWorkerRenderFailedEvent =
            serde_json::from_str(&line).map_err(|_| authentication_rejected())?;
        let detail = format!("{job_id}\0{}", event.reason_code);
        if event.event != "worker.render.failed"
            || event.job_id != job_id
            || event.protocol_version != WORKER_PROTOCOL_VERSION
            || event.worker_kind != kind.as_str()
            || event.worker_version != running.launch.expected_version
            || !valid_render_reason_code(&event.reason_code)
            || !running.token.verify_event_proof(
                "worker.render.failed",
                kind,
                &event.worker_version,
                &detail,
                &event.authentication_proof,
            )
        {
            return Err(authentication_rejected());
        }
        Err(VideoWorkerError::new(VideoWorkerErrorCode::RenderRejected))
    }

    pub fn web_ui_endpoint(
        &self,
        kind: VideoWorkerKind,
    ) -> Result<VideoWorkerWebUiEndpoint, VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        let running = workers.get_mut(&kind).ok_or_else(not_running)?;
        if running
            .child
            .try_wait()
            .map_err(|_| process_unavailable())?
            .is_some()
        {
            return Err(process_unavailable());
        }
        running.web_ui.clone().ok_or_else(not_running)
    }

    pub fn verify_web_ui(&self, kind: VideoWorkerKind) -> Result<(), VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        let running = workers.get_mut(&kind).ok_or_else(not_running)?;
        if running
            .child
            .try_wait()
            .map_err(|_| process_unavailable())?
            .is_some()
        {
            return Err(process_unavailable());
        }
        let endpoint = running.web_ui.as_ref().ok_or_else(not_running)?;
        verify_web_ui_document(endpoint, self.request_timeout)
    }

    pub fn stop(&self, kind: VideoWorkerKind) -> Result<(), VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        let mut running = workers.remove(&kind).ok_or_else(not_running)?;
        force_stop(&mut running);
        Ok(())
    }

    pub fn stop_all(&self) -> Result<(), VideoWorkerError> {
        let mut workers = self.lock_workers()?;
        for (_, mut running) in std::mem::take(&mut *workers) {
            force_stop(&mut running);
        }
        Ok(())
    }

    fn lock_workers(
        &self,
    ) -> Result<MutexGuard<'_, BTreeMap<VideoWorkerKind, RunningVideoWorker>>, VideoWorkerError>
    {
        self.workers.lock().map_err(|_| process_unavailable())
    }
}

impl Drop for LocalVideoOrchestrator {
    fn drop(&mut self) {
        if let Ok(mut workers) = self.workers.lock() {
            for (_, mut running) in std::mem::take(&mut *workers) {
                force_stop(&mut running);
            }
        }
    }
}

struct RunningVideoWorker {
    child: Child,
    stdin: ChildStdin,
    process_tree: ManagedProcessTree,
    token: VideoWorkerSessionToken,
    events: Receiver<Result<String, ()>>,
    stdout_thread: Option<JoinHandle<()>>,
    stderr_thread: Option<JoinHandle<()>>,
    launch: VideoWorkerLaunch,
    status: VideoWorkerStatus,
    web_ui: Option<VideoWorkerWebUiEndpoint>,
    material_preview: Option<VideoWorkerMaterialPreviewEndpoint>,
    editing_job: Option<RunningLocalEditingJob>,
    smart_edit_job: Option<RunningSmartEditJob>,
}

struct RunningLocalEditingJob {
    job_id: String,
    phase: Option<VideoWorkerLocalEditingPhase>,
    progress_per_mille: u16,
    cancelling: bool,
    terminal: bool,
}

struct RunningSmartEditJob {
    job_id: String,
    job_root: PathBuf,
    stage: Option<VideoWorkerSmartEditStage>,
    progress_per_mille: u16,
    cancelling: bool,
    prepared_digest: Option<String>,
    terminal: bool,
}

struct VideoWorkerSessionToken {
    bytes: [u8; SESSION_TOKEN_BYTES],
}

impl VideoWorkerSessionToken {
    fn generate() -> Result<Self, VideoWorkerError> {
        let mut bytes = [0_u8; SESSION_TOKEN_BYTES];
        getrandom::fill(&mut bytes).map_err(|_| process_unavailable())?;
        Ok(Self { bytes })
    }

    fn encoded(&self) -> Zeroizing<String> {
        use fmt::Write as _;

        let mut encoded = Zeroizing::new(String::with_capacity(SESSION_TOKEN_BYTES * 2));
        for byte in self.bytes {
            write!(&mut *encoded, "{byte:02x}").expect("writing to a String cannot fail");
        }
        encoded
    }

    fn verify_event_proof(
        &self,
        event: &str,
        kind: VideoWorkerKind,
        worker_version: &str,
        detail: &str,
        proof: &str,
    ) -> bool {
        let Some(encoded) = proof.strip_prefix(EVENT_PROOF_PREFIX) else {
            return false;
        };
        let Ok(presented) = URL_SAFE_NO_PAD.decode(encoded) else {
            return false;
        };
        let Ok(mut authenticator) = HmacSha256::new_from_slice(&self.bytes) else {
            return false;
        };
        update_authenticator(
            &mut authenticator,
            EVENT_AUTHENTICATION_DOMAIN,
            &[
                event,
                kind.as_str(),
                WORKER_PROTOCOL_VERSION,
                worker_version,
                detail,
            ],
        );
        authenticator.verify_slice(&presented).is_ok()
    }

    fn command_proof(
        &self,
        kind: VideoWorkerKind,
        command: &'static str,
        job_id: &str,
    ) -> Result<Zeroizing<String>, VideoWorkerError> {
        self.command_proof_with_detail(kind, command, job_id, None)
    }

    fn command_proof_with_detail(
        &self,
        kind: VideoWorkerKind,
        command: &'static str,
        job_id: &str,
        detail: Option<&str>,
    ) -> Result<Zeroizing<String>, VideoWorkerError> {
        let mut authenticator =
            HmacSha256::new_from_slice(&self.bytes).map_err(|_| process_unavailable())?;
        let common = [command, kind.as_str(), WORKER_PROTOCOL_VERSION, job_id];
        match detail {
            Some(detail) => update_authenticator(
                &mut authenticator,
                COMMAND_AUTHENTICATION_DOMAIN,
                &[common[0], common[1], common[2], common[3], detail],
            ),
            None => {
                update_authenticator(&mut authenticator, COMMAND_AUTHENTICATION_DOMAIN, &common)
            }
        }
        Ok(Zeroizing::new(format!(
            "{COMMAND_PROOF_PREFIX}{}",
            URL_SAFE_NO_PAD.encode(authenticator.finalize().into_bytes())
        )))
    }

    fn sandbox_command_proof(
        &self,
        kind: VideoWorkerKind,
        job_id: &str,
        canonical_sandbox: &str,
    ) -> Result<Zeroizing<String>, VideoWorkerError> {
        let mut authenticator =
            HmacSha256::new_from_slice(&self.bytes).map_err(|_| process_unavailable())?;
        update_authenticator(
            &mut authenticator,
            COMMAND_AUTHENTICATION_DOMAIN,
            &[
                "worker.render.sandbox",
                kind.as_str(),
                WORKER_PROTOCOL_VERSION,
                job_id,
                canonical_sandbox,
            ],
        );
        Ok(Zeroizing::new(format!(
            "{COMMAND_PROOF_PREFIX}{}",
            URL_SAFE_NO_PAD.encode(authenticator.finalize().into_bytes())
        )))
    }
}

impl Drop for VideoWorkerSessionToken {
    fn drop(&mut self) {
        self.bytes.zeroize();
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct VideoWorkerBootstrapDocument<'a> {
    asset_root: &'a str,
    bootstrap_version: &'static str,
    enable_web_ui: bool,
    local_session_token: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    media_tools: Option<VideoWorkerMediaToolsBootstrap<'a>>,
    protocol_version: &'static str,
    render_browser: Option<VideoWorkerRenderBrowserBootstrap<'a>>,
    script_model: Option<VideoWorkerScriptModelBootstrap<'a>>,
    worker_kind: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct VideoWorkerMediaToolsBootstrap<'a> {
    ffmpeg_path: &'a str,
    ffprobe_path: &'a str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct VideoWorkerRenderBrowserBootstrap<'a> {
    chromium_major: u32,
    executable_path: &'a str,
    launch_timeout_seconds: u64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct VideoWorkerScriptModelBootstrap<'a> {
    api_key: &'a str,
    base_url: &'static str,
    model_id: &'a str,
    source_provider: &'static str,
    upstream_provider: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct VideoWorkerCommandDocument<'a> {
    command: &'static str,
    job_id: &'a str,
    protocol_version: &'static str,
    worker_kind: &'static str,
    authentication_proof: &'a str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct VideoWorkerLocalEditingStartCommandDocument<'a> {
    authentication_proof: &'a str,
    command: &'static str,
    editing: serde_json::Value,
    job_id: &'a str,
    protocol_version: &'static str,
    worker_kind: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct VideoWorkerLocalMaterialImportCommandDocument<'a> {
    authentication_proof: &'a str,
    command: &'static str,
    material_id: &'a str,
    protocol_version: &'static str,
    source_path: &'a str,
    worker_kind: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct VideoWorkerLocalMaterialCommandDocument<'a> {
    authentication_proof: &'a str,
    command: &'static str,
    material_id: &'a str,
    protocol_version: &'static str,
    worker_kind: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct VideoWorkerSandboxCommandDocument<'a> {
    authentication_proof: &'a str,
    command: &'static str,
    job_id: &'a str,
    protocol_version: &'static str,
    sandbox: serde_json::Value,
    worker_kind: &'static str,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerReadyEvent {
    authentication_proof: String,
    event: String,
    material_preview_authentication_proof: Option<String>,
    material_preview_path: Option<String>,
    protocol_version: String,
    script_model_id: Option<String>,
    web_ui_authentication_proof: Option<String>,
    web_ui_path: Option<String>,
    web_ui_port: Option<u16>,
    worker_kind: String,
    worker_version: String,
    port: u16,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerHealthEvent {
    authentication_proof: String,
    event: String,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
    port: u16,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerRenderVerifiedEvent {
    authentication_proof: String,
    chromium_major: u32,
    event: String,
    job_id: String,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerRenderSandboxedEvent {
    authentication_proof: String,
    blocked_dialogs: u32,
    blocked_downloads: u32,
    blocked_navigations: u32,
    blocked_popups: u32,
    blocked_requests: u32,
    chromium_major: u32,
    event: String,
    frames_captured: u32,
    job_id: String,
    output_bytes: u64,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerRenderFailedEvent {
    authentication_proof: String,
    event: String,
    job_id: String,
    protocol_version: String,
    reason_code: String,
    worker_kind: String,
    worker_version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerCancelledEvent {
    authentication_proof: String,
    event: String,
    job_id: String,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerLocalEditingProgressEvent {
    authentication_proof: String,
    event: String,
    job_id: String,
    phase: String,
    #[serde(rename = "progressPermille")]
    progress_per_mille: u16,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerLocalEditingSucceededEvent {
    authentication_proof: String,
    event: String,
    job_id: String,
    output_artifact_id: String,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerLocalEditingFailedEvent {
    authentication_proof: String,
    event: String,
    failure_code: String,
    job_id: String,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerLocalEditingCancelledEvent {
    authentication_proof: String,
    event: String,
    job_id: String,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerSmartEditProgressEvent {
    authentication_proof: String,
    event: String,
    job_id: String,
    #[serde(rename = "progressPermille")]
    progress_per_mille: u16,
    protocol_version: String,
    stage: VideoWorkerSmartEditStage,
    worker_kind: String,
    worker_version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerSmartEditPreparedEvent {
    authentication_proof: String,
    event: String,
    job_id: String,
    protocol_version: String,
    result_digest: String,
    worker_kind: String,
    worker_version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerSmartEditFailedEvent {
    authentication_proof: String,
    event: String,
    failure_code: VideoWorkerSmartEditFailureCode,
    job_id: String,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerSmartEditSimpleEvent {
    authentication_proof: String,
    event: String,
    job_id: String,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerSmartEditSucceededEvent {
    authentication_proof: String,
    event: String,
    job_id: String,
    protocol_version: String,
    result_digest: String,
    worker_kind: String,
    worker_version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerLocalMaterialImportedEvent {
    authentication_proof: String,
    event: String,
    facts: VideoWorkerLocalMaterialFacts,
    material_id: String,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
}

impl VideoWorkerLocalMaterialImportedEvent {
    fn authentication(&self) -> VideoWorkerLocalMaterialEventAuthentication<'_> {
        VideoWorkerLocalMaterialEventAuthentication {
            authentication_proof: &self.authentication_proof,
            event: &self.event,
            material_id: &self.material_id,
            protocol_version: &self.protocol_version,
            worker_kind: &self.worker_kind,
            worker_version: &self.worker_version,
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerLocalMaterialFailedEvent {
    authentication_proof: String,
    event: String,
    failure_code: VideoWorkerLocalMaterialFailureCode,
    material_id: String,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
}

impl VideoWorkerLocalMaterialFailedEvent {
    fn authentication(&self) -> VideoWorkerLocalMaterialEventAuthentication<'_> {
        VideoWorkerLocalMaterialEventAuthentication {
            authentication_proof: &self.authentication_proof,
            event: &self.event,
            material_id: &self.material_id,
            protocol_version: &self.protocol_version,
            worker_kind: &self.worker_kind,
            worker_version: &self.worker_version,
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerLocalMaterialForgottenEvent {
    authentication_proof: String,
    event: String,
    material_id: String,
    protocol_version: String,
    worker_kind: String,
    worker_version: String,
}

impl VideoWorkerLocalMaterialForgottenEvent {
    fn authentication(&self) -> VideoWorkerLocalMaterialEventAuthentication<'_> {
        VideoWorkerLocalMaterialEventAuthentication {
            authentication_proof: &self.authentication_proof,
            event: &self.event,
            material_id: &self.material_id,
            protocol_version: &self.protocol_version,
            worker_kind: &self.worker_kind,
            worker_version: &self.worker_version,
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoWorkerLocalMaterialStatusEvent {
    authentication_proof: String,
    event: String,
    material_id: String,
    protocol_version: String,
    status: VideoWorkerLocalMaterialStatus,
    worker_kind: String,
    worker_version: String,
}

impl VideoWorkerLocalMaterialStatusEvent {
    fn authentication(&self) -> VideoWorkerLocalMaterialEventAuthentication<'_> {
        VideoWorkerLocalMaterialEventAuthentication {
            authentication_proof: &self.authentication_proof,
            event: &self.event,
            material_id: &self.material_id,
            protocol_version: &self.protocol_version,
            worker_kind: &self.worker_kind,
            worker_version: &self.worker_version,
        }
    }
}

struct VideoWorkerLocalMaterialEventAuthentication<'a> {
    authentication_proof: &'a str,
    event: &'a str,
    material_id: &'a str,
    protocol_version: &'a str,
    worker_kind: &'a str,
    worker_version: &'a str,
}

fn valid_local_material_id(material_id: Uuid) -> Result<String, VideoWorkerLocalMaterialError> {
    if !valid_uuid_v4(material_id) {
        return Err(VideoWorkerLocalMaterialError::Lifecycle(
            VideoWorkerErrorCode::ConfigurationInvalid,
        ));
    }
    Ok(material_id.hyphenated().to_string())
}

fn valid_local_material_source_path(source_path: &Path) -> Option<&str> {
    let value = source_path.to_str()?;
    if !source_path.is_absolute()
        || value.is_empty()
        || value.len() > MAX_PATH_BYTES
        || value.chars().any(|character| {
            character.is_control() || matches!(character as u32, 0x202a..=0x202e | 0x2066..=0x2069)
        })
    {
        return None;
    }
    Some(value)
}

fn local_material_worker(
    workers: &mut BTreeMap<VideoWorkerKind, RunningVideoWorker>,
) -> Result<&mut RunningVideoWorker, VideoWorkerLocalMaterialError> {
    let running = workers.get_mut(&VideoWorkerKind::Python).ok_or(
        VideoWorkerLocalMaterialError::Lifecycle(VideoWorkerErrorCode::NotRunning),
    )?;
    if running.launch.media_tools.is_none()
        || running.editing_job.is_some()
        || running.smart_edit_job.is_some()
    {
        return Err(VideoWorkerLocalMaterialError::Lifecycle(
            VideoWorkerErrorCode::ConfigurationInvalid,
        ));
    }
    if running
        .child
        .try_wait()
        .map_err(|_| {
            VideoWorkerLocalMaterialError::Lifecycle(VideoWorkerErrorCode::ProcessUnavailable)
        })?
        .is_some()
    {
        return Err(VideoWorkerLocalMaterialError::Lifecycle(
            VideoWorkerErrorCode::ProcessUnavailable,
        ));
    }
    Ok(running)
}

fn valid_local_material_event(
    running: &RunningVideoWorker,
    expected_event: &str,
    expected_material_id: &str,
    detail: &str,
    authentication: &VideoWorkerLocalMaterialEventAuthentication<'_>,
) -> bool {
    authentication.event == expected_event
        && authentication.material_id == expected_material_id
        && authentication.protocol_version == WORKER_PROTOCOL_VERSION
        && authentication.worker_kind == VideoWorkerKind::Python.as_str()
        && authentication.worker_version == running.launch.expected_version
        && running.token.verify_event_proof(
            expected_event,
            VideoWorkerKind::Python,
            authentication.worker_version,
            detail,
            authentication.authentication_proof,
        )
}

fn write_command(stdin: &mut ChildStdin, command: &impl Serialize) -> Result<(), VideoWorkerError> {
    let mut bytes = Zeroizing::new(serde_json::to_vec(command).map_err(|_| process_unavailable())?);
    bytes.push(b'\n');
    if bytes.len() > MAX_LINE_BYTES {
        return Err(configuration_invalid());
    }
    stdin
        .write_all(&bytes)
        .and_then(|()| stdin.flush())
        .map_err(|_| process_unavailable())
}

struct LocalEditingEventAuthentication<'a> {
    event: &'a str,
    job_id: &'a str,
    worker_kind: &'a str,
    protocol_version: &'a str,
    worker_version: &'a str,
    detail: &'a str,
    proof: &'a str,
}

fn valid_local_editing_event_common(
    token: &VideoWorkerSessionToken,
    launch: &VideoWorkerLaunch,
    authentication: &LocalEditingEventAuthentication<'_>,
) -> bool {
    authentication.event.starts_with("worker.editing.")
        && authentication.job_id.len() == 36
        && authentication.worker_kind == VideoWorkerKind::Python.as_str()
        && authentication.protocol_version == WORKER_PROTOCOL_VERSION
        && authentication.worker_version == launch.expected_version
        && token.verify_event_proof(
            authentication.event,
            VideoWorkerKind::Python,
            authentication.worker_version,
            authentication.detail,
            authentication.proof,
        )
}

fn parse_local_editing_event(
    token: &VideoWorkerSessionToken,
    launch: &VideoWorkerLaunch,
    state: &mut RunningLocalEditingJob,
    line: &str,
) -> Result<VideoWorkerLocalEditingEvent, VideoWorkerError> {
    if let Ok(event) = serde_json::from_str::<VideoWorkerLocalEditingProgressEvent>(line) {
        let phase = VideoWorkerLocalEditingPhase::parse(&event.phase)
            .ok_or_else(authentication_rejected)?;
        let detail = format!(
            "{}\0{}\0{}",
            event.job_id, event.phase, event.progress_per_mille
        );
        if event.event != "worker.editing.progress"
            || event.job_id != state.job_id
            || event.progress_per_mille > 1000
            || !valid_local_editing_event_common(
                token,
                launch,
                &LocalEditingEventAuthentication {
                    event: &event.event,
                    job_id: &event.job_id,
                    worker_kind: &event.worker_kind,
                    protocol_version: &event.protocol_version,
                    worker_version: &event.worker_version,
                    detail: &detail,
                    proof: &event.authentication_proof,
                },
            )
        {
            return Err(authentication_rejected());
        }
        match state.phase {
            None if phase != VideoWorkerLocalEditingPhase::Preparing
                || event.progress_per_mille != 0 =>
            {
                return Err(authentication_rejected());
            }
            Some(previous)
                if phase < previous || event.progress_per_mille < state.progress_per_mille =>
            {
                return Err(authentication_rejected());
            }
            _ => {}
        }
        state.phase = Some(phase);
        state.progress_per_mille = event.progress_per_mille;
        return Ok(VideoWorkerLocalEditingEvent::Progress {
            phase,
            progress_per_mille: event.progress_per_mille,
        });
    }
    if let Ok(event) = serde_json::from_str::<VideoWorkerLocalEditingSucceededEvent>(line) {
        let Ok(output_artifact_id) = Uuid::parse_str(&event.output_artifact_id) else {
            return Err(authentication_rejected());
        };
        let detail = format!("{}\0{}", event.job_id, event.output_artifact_id);
        if event.event != "worker.editing.succeeded"
            || event.job_id != state.job_id
            || !valid_uuid_v4(output_artifact_id)
            || output_artifact_id.hyphenated().to_string() != event.output_artifact_id
            || state.phase != Some(VideoWorkerLocalEditingPhase::Publishing)
            || state.progress_per_mille != 1000
            || !valid_local_editing_event_common(
                token,
                launch,
                &LocalEditingEventAuthentication {
                    event: &event.event,
                    job_id: &event.job_id,
                    worker_kind: &event.worker_kind,
                    protocol_version: &event.protocol_version,
                    worker_version: &event.worker_version,
                    detail: &detail,
                    proof: &event.authentication_proof,
                },
            )
        {
            return Err(authentication_rejected());
        }
        state.terminal = true;
        return Ok(VideoWorkerLocalEditingEvent::Succeeded { output_artifact_id });
    }
    if let Ok(event) = serde_json::from_str::<VideoWorkerLocalEditingFailedEvent>(line) {
        let failure_code = VideoWorkerLocalEditingFailureCode::parse(&event.failure_code)
            .ok_or_else(authentication_rejected)?;
        let detail = format!("{}\0{}", event.job_id, event.failure_code);
        if event.event != "worker.editing.failed"
            || event.job_id != state.job_id
            || !valid_local_editing_event_common(
                token,
                launch,
                &LocalEditingEventAuthentication {
                    event: &event.event,
                    job_id: &event.job_id,
                    worker_kind: &event.worker_kind,
                    protocol_version: &event.protocol_version,
                    worker_version: &event.worker_version,
                    detail: &detail,
                    proof: &event.authentication_proof,
                },
            )
        {
            return Err(authentication_rejected());
        }
        state.terminal = true;
        return Ok(VideoWorkerLocalEditingEvent::Failed { failure_code });
    }
    if let Ok(event) = serde_json::from_str::<VideoWorkerLocalEditingCancelledEvent>(line) {
        if event.event != "worker.editing.cancelled"
            || event.job_id != state.job_id
            || !state.cancelling
            || !valid_local_editing_event_common(
                token,
                launch,
                &LocalEditingEventAuthentication {
                    event: &event.event,
                    job_id: &event.job_id,
                    worker_kind: &event.worker_kind,
                    protocol_version: &event.protocol_version,
                    worker_version: &event.worker_version,
                    detail: &event.job_id,
                    proof: &event.authentication_proof,
                },
            )
        {
            return Err(authentication_rejected());
        }
        state.terminal = true;
        return Ok(VideoWorkerLocalEditingEvent::Cancelled);
    }
    Err(authentication_rejected())
}

fn valid_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn sha256_hex(payload: &[u8]) -> String {
    use fmt::Write as _;

    let digest = Sha256::digest(payload);
    let mut encoded = String::with_capacity(64);
    for byte in digest {
        write!(&mut encoded, "{byte:02x}").expect("writing to a String cannot fail");
    }
    encoded
}

fn valid_smart_edit_relative_path(value: &str) -> bool {
    if value.is_empty()
        || value.len() > 512
        || value.contains('\\')
        || value.chars().any(char::is_control)
    {
        return false;
    }
    let path = Path::new(value);
    !path.is_absolute()
        && path
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
}

struct SmartEditEventAuthentication<'a> {
    expected_event: &'a str,
    event: &'a str,
    expected_job_id: &'a str,
    job_id: &'a str,
    protocol_version: &'a str,
    worker_kind: &'a str,
    worker_version: &'a str,
    detail: &'a str,
    proof: &'a str,
}

fn smart_edit_event_common(
    token: &VideoWorkerSessionToken,
    launch: &VideoWorkerLaunch,
    authentication: &SmartEditEventAuthentication<'_>,
) -> bool {
    authentication.event == authentication.expected_event
        && authentication.job_id == authentication.expected_job_id
        && authentication.protocol_version == WORKER_PROTOCOL_VERSION
        && authentication.worker_kind == VideoWorkerKind::Python.as_str()
        && authentication.worker_version == launch.expected_version
        && token.verify_event_proof(
            authentication.expected_event,
            VideoWorkerKind::Python,
            authentication.worker_version,
            authentication.detail,
            authentication.proof,
        )
}

fn load_smart_edit_result(
    state: &RunningSmartEditJob,
    digest: &str,
) -> Result<VideoWorkerSmartEditResult, VideoWorkerError> {
    let path = state.job_root.join("result.json");
    let metadata = path
        .symlink_metadata()
        .map_err(|_| authentication_rejected())?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || !(1..=SMART_EDIT_RESULT_MAX_BYTES).contains(&metadata.len())
    {
        return Err(authentication_rejected());
    }
    let mut source = fs::File::open(&path).map_err(|_| authentication_rejected())?;
    let mut payload = Vec::with_capacity(metadata.len() as usize);
    Read::by_ref(&mut source)
        .take(SMART_EDIT_RESULT_MAX_BYTES + 1)
        .read_to_end(&mut payload)
        .map_err(|_| authentication_rejected())?;
    if payload.len() as u64 != metadata.len()
        || payload.len() as u64 > SMART_EDIT_RESULT_MAX_BYTES
        || sha256_hex(&payload) != digest
    {
        return Err(authentication_rejected());
    }
    let result: VideoWorkerSmartEditResult =
        serde_json::from_slice(&payload).map_err(|_| authentication_rejected())?;
    result.validate(&state.job_id)?;
    Ok(result)
}

fn parse_smart_edit_event(
    token: &VideoWorkerSessionToken,
    launch: &VideoWorkerLaunch,
    state: &mut RunningSmartEditJob,
    line: &str,
) -> Result<VideoWorkerSmartEditEvent, VideoWorkerError> {
    if let Ok(event) = serde_json::from_str::<VideoWorkerSmartEditProgressEvent>(line) {
        let stage = event.stage;
        let stage_name = match stage {
            VideoWorkerSmartEditStage::Preparing => "preparing",
            VideoWorkerSmartEditStage::Analyzing => "analyzing",
            VideoWorkerSmartEditStage::Scripting => "scripting",
            VideoWorkerSmartEditStage::Synthesizing => "synthesizing",
            VideoWorkerSmartEditStage::Matching => "matching",
            VideoWorkerSmartEditStage::Selecting => "selecting",
            VideoWorkerSmartEditStage::Publishing => "publishing",
            VideoWorkerSmartEditStage::Completed => "completed",
        };
        let detail = format!(
            "{}\0{}\0{}",
            event.job_id, stage_name, event.progress_per_mille
        );
        if event.progress_per_mille > 1_000
            || state.prepared_digest.is_some()
            || !smart_edit_event_common(
                token,
                launch,
                &SmartEditEventAuthentication {
                    expected_event: "worker.smart_edit.progress",
                    event: &event.event,
                    expected_job_id: &state.job_id,
                    job_id: &event.job_id,
                    protocol_version: &event.protocol_version,
                    worker_kind: &event.worker_kind,
                    worker_version: &event.worker_version,
                    detail: &detail,
                    proof: &event.authentication_proof,
                },
            )
        {
            return Err(authentication_rejected());
        }
        match state.stage {
            None if stage != VideoWorkerSmartEditStage::Preparing
                || event.progress_per_mille != 0 =>
            {
                return Err(authentication_rejected());
            }
            Some(previous)
                if stage < previous || event.progress_per_mille < state.progress_per_mille =>
            {
                return Err(authentication_rejected());
            }
            _ => {}
        }
        state.stage = Some(stage);
        state.progress_per_mille = event.progress_per_mille;
        return Ok(VideoWorkerSmartEditEvent::Progress {
            stage,
            progress_per_mille: event.progress_per_mille,
        });
    }
    if let Ok(event) = serde_json::from_str::<VideoWorkerSmartEditPreparedEvent>(line) {
        let detail = format!("{}\0{}", event.job_id, event.result_digest);
        if state.stage != Some(VideoWorkerSmartEditStage::Completed)
            || state.progress_per_mille != 1_000
            || state.prepared_digest.is_some()
            || !valid_sha256(&event.result_digest)
            || !smart_edit_event_common(
                token,
                launch,
                &SmartEditEventAuthentication {
                    expected_event: "worker.smart_edit.prepared",
                    event: &event.event,
                    expected_job_id: &state.job_id,
                    job_id: &event.job_id,
                    protocol_version: &event.protocol_version,
                    worker_kind: &event.worker_kind,
                    worker_version: &event.worker_version,
                    detail: &detail,
                    proof: &event.authentication_proof,
                },
            )
        {
            return Err(authentication_rejected());
        }
        let result = load_smart_edit_result(state, &event.result_digest)?;
        state.prepared_digest = Some(event.result_digest.clone());
        return Ok(VideoWorkerSmartEditEvent::Prepared {
            result_digest: event.result_digest,
            result,
        });
    }
    if let Ok(event) = serde_json::from_str::<VideoWorkerSmartEditFailedEvent>(line) {
        let detail = format!("{}\0{}", event.job_id, event.failure_code.as_str());
        if !smart_edit_event_common(
            token,
            launch,
            &SmartEditEventAuthentication {
                expected_event: "worker.smart_edit.failed",
                event: &event.event,
                expected_job_id: &state.job_id,
                job_id: &event.job_id,
                protocol_version: &event.protocol_version,
                worker_kind: &event.worker_kind,
                worker_version: &event.worker_version,
                detail: &detail,
                proof: &event.authentication_proof,
            },
        ) {
            return Err(authentication_rejected());
        }
        state.terminal = true;
        return Ok(VideoWorkerSmartEditEvent::Failed {
            failure_code: event.failure_code,
        });
    }
    if let Ok(event) = serde_json::from_str::<VideoWorkerSmartEditSimpleEvent>(line) {
        if !state.cancelling
            || state.prepared_digest.is_some()
            || !smart_edit_event_common(
                token,
                launch,
                &SmartEditEventAuthentication {
                    expected_event: "worker.smart_edit.cancelled",
                    event: &event.event,
                    expected_job_id: &state.job_id,
                    job_id: &event.job_id,
                    protocol_version: &event.protocol_version,
                    worker_kind: &event.worker_kind,
                    worker_version: &event.worker_version,
                    detail: &event.job_id,
                    proof: &event.authentication_proof,
                },
            )
        {
            return Err(authentication_rejected());
        }
        state.terminal = true;
        return Ok(VideoWorkerSmartEditEvent::Cancelled);
    }
    Err(authentication_rejected())
}

fn valid_smart_edit_succeeded(
    token: &VideoWorkerSessionToken,
    launch: &VideoWorkerLaunch,
    job_id: &str,
    digest: &str,
    line: &str,
) -> Result<bool, VideoWorkerError> {
    if let Ok(event) = serde_json::from_str::<VideoWorkerSmartEditSucceededEvent>(line) {
        let detail = format!("{}\0{}", event.job_id, event.result_digest);
        if event.result_digest == digest
            && smart_edit_event_common(
                token,
                launch,
                &SmartEditEventAuthentication {
                    expected_event: "worker.smart_edit.succeeded",
                    event: &event.event,
                    expected_job_id: job_id,
                    job_id: &event.job_id,
                    protocol_version: &event.protocol_version,
                    worker_kind: &event.worker_kind,
                    worker_version: &event.worker_version,
                    detail: &detail,
                    proof: &event.authentication_proof,
                },
            )
        {
            return Ok(true);
        }
        return Err(authentication_rejected());
    }
    let event: VideoWorkerSmartEditFailedEvent =
        serde_json::from_str(line).map_err(|_| authentication_rejected())?;
    let detail = format!("{}\0{}", event.job_id, event.failure_code.as_str());
    if event.failure_code != VideoWorkerSmartEditFailureCode::CommitFailed
        || !smart_edit_event_common(
            token,
            launch,
            &SmartEditEventAuthentication {
                expected_event: "worker.smart_edit.failed",
                event: &event.event,
                expected_job_id: job_id,
                job_id: &event.job_id,
                protocol_version: &event.protocol_version,
                worker_kind: &event.worker_kind,
                worker_version: &event.worker_version,
                detail: &detail,
                proof: &event.authentication_proof,
            },
        )
    {
        return Err(authentication_rejected());
    }
    Ok(false)
}

fn valid_smart_edit_aborted(
    token: &VideoWorkerSessionToken,
    launch: &VideoWorkerLaunch,
    job_id: &str,
    line: &str,
) -> Result<bool, VideoWorkerError> {
    if let Ok(event) = serde_json::from_str::<VideoWorkerSmartEditSimpleEvent>(line) {
        if smart_edit_event_common(
            token,
            launch,
            &SmartEditEventAuthentication {
                expected_event: "worker.smart_edit.aborted",
                event: &event.event,
                expected_job_id: job_id,
                job_id: &event.job_id,
                protocol_version: &event.protocol_version,
                worker_kind: &event.worker_kind,
                worker_version: &event.worker_version,
                detail: &event.job_id,
                proof: &event.authentication_proof,
            },
        ) {
            return Ok(true);
        }
        return Err(authentication_rejected());
    }
    let event: VideoWorkerSmartEditFailedEvent =
        serde_json::from_str(line).map_err(|_| authentication_rejected())?;
    let detail = format!("{}\0{}", event.job_id, event.failure_code.as_str());
    if event.failure_code != VideoWorkerSmartEditFailureCode::LocalFailed
        || !smart_edit_event_common(
            token,
            launch,
            &SmartEditEventAuthentication {
                expected_event: "worker.smart_edit.failed",
                event: &event.event,
                expected_job_id: job_id,
                job_id: &event.job_id,
                protocol_version: &event.protocol_version,
                worker_kind: &event.worker_kind,
                worker_version: &event.worker_version,
                detail: &detail,
                proof: &event.authentication_proof,
            },
        )
    {
        return Err(authentication_rejected());
    }
    Ok(false)
}

fn write_smart_edit_request(
    asset_root: &Path,
    job_id: &str,
    request: &VideoWorkerSmartEditRequest,
) -> Result<PathBuf, VideoWorkerError> {
    validate_directory_path(asset_root)?;
    let mut jobs_root = asset_root.to_path_buf();
    for component in ["local-executor", "smart-edit", "jobs"] {
        jobs_root.push(component);
        match fs::create_dir(&jobs_root) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(_) => return Err(process_unavailable()),
        }
        validate_directory_path(&jobs_root)?;
    }
    let job_root = jobs_root.join(job_id);
    fs::create_dir(&job_root).map_err(|_| configuration_invalid())?;
    let outcome = (|| {
        #[cfg(unix)]
        use std::os::unix::fs::{OpenOptionsExt as _, PermissionsExt as _};
        #[cfg(unix)]
        fs::set_permissions(&job_root, fs::Permissions::from_mode(0o700))
            .map_err(|_| process_unavailable())?;
        let path = job_root.join("request.json");
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        options.mode(0o600);
        let mut file = options.open(path).map_err(|_| process_unavailable())?;
        let payload =
            serde_json::to_vec(&request.document(job_id)).map_err(|_| configuration_invalid())?;
        if payload.is_empty() || payload.len() as u64 > SMART_EDIT_RESULT_MAX_BYTES {
            return Err(configuration_invalid());
        }
        file.write_all(&payload)
            .and_then(|()| file.flush())
            .and_then(|()| file.sync_all())
            .map_err(|_| process_unavailable())
    })();
    if let Err(error) = outcome {
        let _ = fs::remove_dir_all(&job_root);
        return Err(error);
    }
    Ok(job_root)
}

fn remove_private_smart_edit_job_root(job_root: &Path) -> Result<(), VideoWorkerError> {
    match fs::symlink_metadata(job_root) {
        Ok(_) => {
            validate_directory_path(job_root)?;
            fs::remove_dir_all(job_root).map_err(|_| process_unavailable())
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(process_unavailable()),
    }
}

fn spawn_worker(
    launch: VideoWorkerLaunch,
    start_timeout: Duration,
    restart_count: u8,
) -> Result<RunningVideoWorker, VideoWorkerError> {
    let token = VideoWorkerSessionToken::generate()?;
    let mut command = Command::new(&launch.executable_path);
    if launch.isolated_environment {
        command.env_clear();
        #[cfg(windows)]
        for name in ["SystemRoot", "WINDIR"] {
            if let Some(value) = std::env::var_os(name) {
                command.env(name, value);
            }
        }
        #[cfg(windows)]
        for name in [
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "TEMP",
            "TMP",
        ] {
            command.env(name, &launch.asset_root);
        }
    }
    // After any clearing, so an isolated Worker keeps exactly the packaged
    // dependencies the App hands it and nothing the user's machine supplied.
    for (name, value) in &launch.environment {
        command.env(name, value);
    }
    command
        .args(&launch.arguments)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    configure_managed_process(&mut command);
    let mut child = command.spawn().map_err(|_| process_unavailable())?;
    let mut process_tree = match ManagedProcessTree::attach(&child) {
        Ok(process_tree) => process_tree,
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(process_unavailable());
        }
    };
    let setup = (|| {
        let mut stdin = child.stdin.take().ok_or_else(process_unavailable)?;
        let stdout = child.stdout.take().ok_or_else(process_unavailable)?;
        let stderr = child.stderr.take().ok_or_else(process_unavailable)?;
        let (events, stdout_thread) = spawn_stdout_reader(stdout);
        let stderr_thread = spawn_stderr_drain(stderr);
        write_bootstrap(&token, &launch, &mut stdin)?;
        let line = receive_line(&events, start_timeout)?;
        let event: VideoWorkerReadyEvent =
            serde_json::from_str(&line).map_err(|_| authentication_rejected())?;
        let (web_ui, material_preview) = validate_ready_event(&token, &launch, &event)?;
        let status = VideoWorkerStatus::running(
            launch.kind,
            event.worker_version,
            event.port,
            child.id(),
            restart_count,
            event.script_model_id,
            web_ui.is_some(),
        );
        Ok((
            stdin,
            events,
            stdout_thread,
            stderr_thread,
            status,
            web_ui,
            material_preview,
        ))
    })();
    let (stdin, events, stdout_thread, stderr_thread, status, web_ui, material_preview) =
        match setup {
            Ok(value) => value,
            Err(error) => {
                let _ = process_tree.terminate();
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
        };
    let mut running = RunningVideoWorker {
        child,
        stdin,
        process_tree,
        token,
        events,
        stdout_thread: Some(stdout_thread),
        stderr_thread: Some(stderr_thread),
        launch,
        status,
        web_ui,
        material_preview,
        editing_job: None,
        smart_edit_job: None,
    };
    if let Err(error) = verify_health(&running, start_timeout) {
        force_stop(&mut running);
        return Err(error);
    }
    Ok(running)
}

fn write_bootstrap(
    token: &VideoWorkerSessionToken,
    launch: &VideoWorkerLaunch,
    stdin: &mut ChildStdin,
) -> Result<(), VideoWorkerError> {
    let encoded = token.encoded();
    let asset_root = launch
        .asset_root
        .to_str()
        .ok_or_else(configuration_invalid)?;
    let render_browser = match launch.render_browser.as_ref() {
        None => None,
        Some(configuration) => Some(VideoWorkerRenderBrowserBootstrap {
            chromium_major: configuration.chromium_major,
            executable_path: configuration
                .executable_path
                .to_str()
                .ok_or_else(configuration_invalid)?,
            launch_timeout_seconds: configuration.launch_timeout.as_secs(),
        }),
    };
    let media_tools = match launch.media_tools.as_ref() {
        None => None,
        Some(configuration) => Some(VideoWorkerMediaToolsBootstrap {
            ffmpeg_path: configuration
                .ffmpeg_path
                .to_str()
                .ok_or_else(configuration_invalid)?,
            ffprobe_path: configuration
                .ffprobe_path
                .to_str()
                .ok_or_else(configuration_invalid)?,
        }),
    };
    let document = VideoWorkerBootstrapDocument {
        asset_root,
        bootstrap_version: BOOTSTRAP_VERSION,
        enable_web_ui: launch.web_ui,
        local_session_token: &encoded,
        media_tools,
        protocol_version: WORKER_PROTOCOL_VERSION,
        render_browser,
        script_model: launch.script_model.as_ref().map(|configuration| {
            VideoWorkerScriptModelBootstrap {
                api_key: &configuration.api_key,
                base_url: BAILIAN_BASE_URL,
                model_id: &configuration.model_id,
                source_provider: "bailian",
                upstream_provider: "openai",
            }
        }),
        worker_kind: launch.kind.as_str(),
    };
    let mut bytes =
        Zeroizing::new(serde_json::to_vec(&document).map_err(|_| process_unavailable())?);
    bytes.push(b'\n');
    if bytes.len() > MAX_LINE_BYTES {
        return Err(process_unavailable());
    }
    stdin
        .write_all(&bytes)
        .and_then(|()| stdin.flush())
        .map_err(|_| process_unavailable())
}

fn validate_ready_event(
    token: &VideoWorkerSessionToken,
    launch: &VideoWorkerLaunch,
    event: &VideoWorkerReadyEvent,
) -> Result<
    (
        Option<VideoWorkerWebUiEndpoint>,
        Option<VideoWorkerMaterialPreviewEndpoint>,
    ),
    VideoWorkerError,
> {
    let port = event.port.to_string();
    if event.event != "worker.ready"
        || event.protocol_version != WORKER_PROTOCOL_VERSION
        || event.worker_kind != launch.kind.as_str()
        || event.script_model_id.as_deref()
            != launch
                .script_model
                .as_ref()
                .map(VideoWorkerScriptModelConfiguration::model_id)
        || !token.verify_event_proof(
            "worker.ready",
            launch.kind,
            &event.worker_version,
            &port,
            &event.authentication_proof,
        )
    {
        return Err(authentication_rejected());
    }
    if event.worker_version != launch.expected_version {
        return Err(VideoWorkerError::new(VideoWorkerErrorCode::VersionMismatch));
    }
    let web_ui = match (
        launch.web_ui,
        event.web_ui_port,
        event.web_ui_path.as_deref(),
        event.web_ui_authentication_proof.as_deref(),
    ) {
        (false, None, None, None) => None,
        (true, Some(web_ui_port), Some(web_ui_path), Some(proof))
            if valid_web_ui_path(web_ui_path) =>
        {
            let detail = format!("{web_ui_port}:{web_ui_path}");
            if !token.verify_event_proof(
                "worker.web_ui_ready",
                launch.kind,
                &event.worker_version,
                &detail,
                proof,
            ) {
                return Err(authentication_rejected());
            }
            Some(VideoWorkerWebUiEndpoint {
                port: web_ui_port,
                path: web_ui_path.to_owned(),
            })
        }
        _ => return Err(authentication_rejected()),
    };
    let material_preview = match (
        launch.media_tools.is_some(),
        event.material_preview_path.as_deref(),
        event.material_preview_authentication_proof.as_deref(),
    ) {
        (false, None, None) => None,
        (true, Some(path), Some(proof)) if valid_material_preview_path(path) => {
            let detail = format!("{}:{path}", event.port);
            if !token.verify_event_proof(
                "worker.material_preview_ready",
                launch.kind,
                &event.worker_version,
                &detail,
                proof,
            ) {
                return Err(authentication_rejected());
            }
            Some(VideoWorkerMaterialPreviewEndpoint {
                port: event.port,
                path: path.to_owned(),
            })
        }
        _ => return Err(authentication_rejected()),
    };
    Ok((web_ui, material_preview))
}

fn verify_health(running: &RunningVideoWorker, timeout: Duration) -> Result<(), VideoWorkerError> {
    let port = running.status.port.ok_or_else(process_unavailable)?;
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port);
    let mut stream = TcpStream::connect_timeout(&address, timeout).map_err(|error| {
        if error.kind() == std::io::ErrorKind::TimedOut {
            timed_out()
        } else {
            process_unavailable()
        }
    })?;
    stream
        .set_read_timeout(Some(timeout))
        .and_then(|()| stream.set_write_timeout(Some(timeout)))
        .map_err(|_| process_unavailable())?;
    let token = running.token.encoded();
    let request = Zeroizing::new(format!(
        "GET /health HTTP/1.1\r\nHost: {LOOPBACK_HOST}:{port}\r\nAuthorization: Bearer {}\r\nConnection: close\r\n\r\n",
        *token,
    ));
    stream
        .write_all(request.as_bytes())
        .and_then(|()| stream.flush())
        .map_err(|_| process_unavailable())?;
    let mut response = Vec::new();
    stream
        .take(MAX_HTTP_RESPONSE_BYTES)
        .read_to_end(&mut response)
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::TimedOut {
                timed_out()
            } else {
                process_unavailable()
            }
        })?;
    let response = std::str::from_utf8(&response).map_err(|_| authentication_rejected())?;
    let (headers, body) = response
        .split_once("\r\n\r\n")
        .ok_or_else(authentication_rejected)?;
    if !headers.starts_with("HTTP/1.1 200 ") {
        return Err(authentication_rejected());
    }
    let event: VideoWorkerHealthEvent =
        serde_json::from_str(body).map_err(|_| authentication_rejected())?;
    let detail = port.to_string();
    if event.event != "worker.health"
        || event.protocol_version != WORKER_PROTOCOL_VERSION
        || event.worker_kind != running.launch.kind.as_str()
        || event.worker_version != running.launch.expected_version
        || event.port != port
        || !running.token.verify_event_proof(
            "worker.health",
            running.launch.kind,
            &event.worker_version,
            &detail,
            &event.authentication_proof,
        )
    {
        return Err(authentication_rejected());
    }
    Ok(())
}

fn verify_web_ui_document(
    endpoint: &VideoWorkerWebUiEndpoint,
    timeout: Duration,
) -> Result<(), VideoWorkerError> {
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), endpoint.port);
    let mut stream = TcpStream::connect_timeout(&address, timeout).map_err(|error| {
        if error.kind() == std::io::ErrorKind::TimedOut {
            timed_out()
        } else {
            process_unavailable()
        }
    })?;
    stream
        .set_read_timeout(Some(timeout))
        .and_then(|()| stream.set_write_timeout(Some(timeout)))
        .map_err(|_| process_unavailable())?;
    let request = format!(
        "GET /{}/ HTTP/1.1\r\nHost: {LOOPBACK_HOST}:{}\r\nAccept: text/html\r\nConnection: close\r\n\r\n",
        endpoint.path, endpoint.port
    );
    stream
        .write_all(request.as_bytes())
        .and_then(|()| stream.flush())
        .map_err(|_| process_unavailable())?;
    let mut response = Vec::new();
    stream
        .take(MAX_HTTP_RESPONSE_BYTES)
        .read_to_end(&mut response)
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::TimedOut {
                timed_out()
            } else {
                process_unavailable()
            }
        })?;
    let response = std::str::from_utf8(&response).map_err(|_| authentication_rejected())?;
    let (headers, body) = response
        .split_once("\r\n\r\n")
        .ok_or_else(authentication_rejected)?;
    if !headers.starts_with("HTTP/1.1 200 ")
        || !headers
            .to_ascii_lowercase()
            .contains("content-type: text/html")
        || !body.contains("<title>Streamlit</title>")
    {
        return Err(authentication_rejected());
    }
    Ok(())
}

fn update_authenticator(authenticator: &mut HmacSha256, domain: &[u8], parts: &[&str]) {
    authenticator.update(domain);
    for (index, part) in parts.iter().enumerate() {
        if index > 0 {
            authenticator.update(b"\0");
        }
        authenticator.update(part.as_bytes());
    }
}

fn spawn_stdout_reader(stdout: ChildStdout) -> (Receiver<Result<String, ()>>, JoinHandle<()>) {
    let (sender, receiver) = mpsc::channel();
    let thread = std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        loop {
            let mut bytes = Vec::new();
            let read = reader
                .by_ref()
                .take((MAX_LINE_BYTES + 1) as u64)
                .read_until(b'\n', &mut bytes);
            let Ok(count) = read else {
                let _ = sender.send(Err(()));
                return;
            };
            if count == 0 {
                return;
            }
            if bytes.len() > MAX_LINE_BYTES || !bytes.ends_with(b"\n") {
                let _ = sender.send(Err(()));
                return;
            }
            bytes.pop();
            if bytes.ends_with(b"\r") {
                bytes.pop();
            }
            let line = String::from_utf8(bytes).map_err(|_| ());
            if sender.send(line).is_err() {
                return;
            }
        }
    });
    (receiver, thread)
}

fn spawn_stderr_drain(stderr: ChildStderr) -> JoinHandle<()> {
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stderr);
        let mut buffer = [0_u8; 4096];
        while reader.read(&mut buffer).is_ok_and(|count| count > 0) {}
    })
}

fn receive_line(
    events: &Receiver<Result<String, ()>>,
    timeout: Duration,
) -> Result<String, VideoWorkerError> {
    events
        .recv_timeout(timeout)
        .map_err(|error| match error {
            mpsc::RecvTimeoutError::Timeout => timed_out(),
            mpsc::RecvTimeoutError::Disconnected => process_unavailable(),
        })?
        .map_err(|()| authentication_rejected())
}

fn force_stop(running: &mut RunningVideoWorker) {
    let _ = running.process_tree.terminate();
    let _ = running.child.kill();
    let _ = running.child.wait();
    join_readers(running);
}

fn finish_exited_worker(running: &mut RunningVideoWorker) {
    let _ = running.child.wait();
    let _ = running.process_tree.terminate();
    join_readers(running);
}

fn join_readers(running: &mut RunningVideoWorker) {
    if let Some(thread) = running.stdout_thread.take() {
        let _ = thread.join();
    }
    if let Some(thread) = running.stderr_thread.take() {
        let _ = thread.join();
    }
}

fn validate_executable_path(path: &Path) -> Result<(), VideoWorkerError> {
    if !path.is_absolute() || path.as_os_str().len() > MAX_PATH_BYTES {
        return Err(configuration_invalid());
    }
    validate_safe_path_components(path)?;
    let metadata = fs::metadata(path).map_err(|_| configuration_invalid())?;
    if !metadata.is_file() {
        return Err(configuration_invalid());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o111 == 0 {
            return Err(configuration_invalid());
        }
    }
    Ok(())
}

fn validate_regular_file_path(path: &Path) -> Result<(), VideoWorkerError> {
    if !path.is_absolute() || path.as_os_str().len() > MAX_PATH_BYTES {
        return Err(configuration_invalid());
    }
    validate_safe_path_components(path)?;
    if !fs::metadata(path)
        .map_err(|_| configuration_invalid())?
        .is_file()
    {
        return Err(configuration_invalid());
    }
    Ok(())
}

fn validate_directory_path(path: &Path) -> Result<(), VideoWorkerError> {
    if !path.is_absolute() || path.as_os_str().len() > MAX_PATH_BYTES {
        return Err(configuration_invalid());
    }
    validate_safe_path_components(path)?;
    if !fs::metadata(path)
        .map_err(|_| configuration_invalid())?
        .is_dir()
    {
        return Err(configuration_invalid());
    }
    Ok(())
}

/// Reject links/reparse points without asking Windows to inspect a bare drive
/// prefix such as `\\?\C:`. That prefix is a syntactic path component, not a
/// filesystem object; querying it returns `ERROR_INVALID_NAME` even though the
/// rooted verbatim path Tauri supplied is valid.
fn validate_safe_path_components(path: &Path) -> Result<(), VideoWorkerError> {
    let mut current = PathBuf::new();
    for component in path.components() {
        current.push(component.as_os_str());
        #[cfg(windows)]
        if matches!(component, Component::Prefix(_)) {
            continue;
        }
        let metadata = fs::symlink_metadata(&current).map_err(|_| configuration_invalid())?;
        #[cfg(windows)]
        let windows_file_attributes = {
            use std::os::windows::fs::MetadataExt;
            Some(metadata.file_attributes())
        };
        #[cfg(not(windows))]
        let windows_file_attributes = None;
        if unsafe_path_component(metadata.file_type().is_symlink(), windows_file_attributes) {
            return Err(configuration_invalid());
        }
    }
    Ok(())
}

/// Windows filesystem APIs accept the extended-length device spelling, but
/// Node treats it as a module-specifier shape and CreateProcessW cannot launch
/// the packaged executable from it. Validate the original path first, then
/// hand the child boundary the equivalent native drive/UNC spelling. Modern
/// Node and Chromium retain long-path support themselves.
#[cfg(windows)]
fn child_process_path(path: &Path) -> PathBuf {
    use std::ffi::OsString;
    use std::os::windows::ffi::{OsStrExt, OsStringExt};

    let encoded = path.as_os_str().encode_wide().collect::<Vec<_>>();
    let verbatim_unc = "\\\\?\\UNC\\".encode_utf16().collect::<Vec<_>>();
    let verbatim = "\\\\?\\".encode_utf16().collect::<Vec<_>>();
    if encoded.starts_with(&verbatim_unc) {
        let mut native = "\\\\".encode_utf16().collect::<Vec<_>>();
        native.extend_from_slice(&encoded[verbatim_unc.len()..]);
        return PathBuf::from(OsString::from_wide(&native));
    }
    if encoded.starts_with(&verbatim) {
        return PathBuf::from(OsString::from_wide(&encoded[verbatim.len()..]));
    }
    path.to_path_buf()
}

#[cfg(not(windows))]
fn child_process_path(path: &Path) -> PathBuf {
    path.to_path_buf()
}

fn valid_environment_name(value: &str) -> bool {
    (1..=MAX_ENVIRONMENT_NAME_BYTES).contains(&value.len())
        && value.starts_with(|character: char| character.is_ascii_uppercase())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
        && !FORBIDDEN_ENVIRONMENT_NAMES.contains(&value)
}

fn valid_model_api_key(value: &str) -> bool {
    // Real Bailian workspace keys carry dot-separated segments (sk-ws-X.....).
    (20..=256).contains(&value.len())
        && value.starts_with("sk-")
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
}

fn valid_uuid_v4(value: Uuid) -> bool {
    value.get_version_num() == 4 && value.get_variant() == Variant::RFC4122
}

fn valid_sandbox_relative_path(value: &str) -> bool {
    if value.is_empty()
        || value.len() > SANDBOX_RELATIVE_PATH_MAXIMUM
        || value.contains('\0')
        || value.contains('\\')
        || value.starts_with('/')
    {
        return false;
    }
    value
        .split('/')
        .all(|segment| !segment.is_empty() && segment != "." && segment != "..")
}

fn valid_render_reason_code(value: &str) -> bool {
    (1..=64).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte == b'_')
}

fn valid_web_ui_path(value: &str) -> bool {
    let Some(capability) = value.strip_prefix("studio-") else {
        return false;
    };
    capability.len() == 43
        && capability
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

fn valid_material_preview_path(value: &str) -> bool {
    let Some(capability) = value.strip_prefix("material-preview-v1-") else {
        return false;
    };
    capability.len() == 43
        && capability
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

const fn unsafe_path_component(is_symlink: bool, windows_file_attributes: Option<u32>) -> bool {
    is_symlink
        || match windows_file_attributes {
            Some(attributes) => attributes & WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT != 0,
            None => false,
        }
}

const fn configuration_invalid() -> VideoWorkerError {
    VideoWorkerError::new(VideoWorkerErrorCode::ConfigurationInvalid)
}

const fn authentication_rejected() -> VideoWorkerError {
    VideoWorkerError::new(VideoWorkerErrorCode::AuthenticationRejected)
}

const fn not_running() -> VideoWorkerError {
    VideoWorkerError::new(VideoWorkerErrorCode::NotRunning)
}

const fn process_unavailable() -> VideoWorkerError {
    VideoWorkerError::new(VideoWorkerErrorCode::ProcessUnavailable)
}

const fn timed_out() -> VideoWorkerError {
    VideoWorkerError::new(VideoWorkerErrorCode::TimedOut)
}

#[cfg(test)]
mod tests {
    use super::{unsafe_path_component, VideoWorkerSmartEditResult};

    #[test]
    fn smart_edit_result_accepts_one_static_image_in_multiple_paragraphs() {
        let job_id = "123e4567-e89b-42d3-a456-426614174100";
        let visual_id = "223e4567-e89b-42d3-a456-426614174101";
        let result: VideoWorkerSmartEditResult = serde_json::from_value(serde_json::json!({
            "analysisUpdates": [],
            "draft": {
                "durationMs": 2_000,
                "paragraphs": [
                    {
                        "audioMaterialId": "323e4567-e89b-42d3-a456-426614174102",
                        "captionText": "第一段旁白",
                        "durationMs": 1_000,
                        "kind": "narrated",
                        "sequence": 1,
                        "visualMaterialId": visual_id,
                        "visualSourceInMs": null,
                        "visualSourceOutMs": null
                    },
                    {
                        "audioMaterialId": "423e4567-e89b-42d3-a456-426614174103",
                        "captionText": "第二段旁白",
                        "durationMs": 1_000,
                        "kind": "narrated",
                        "sequence": 2,
                        "visualMaterialId": visual_id,
                        "visualSourceInMs": null,
                        "visualSourceOutMs": null
                    }
                ]
            },
            "jobId": job_id,
            "narrationRegistrations": [
                {
                    "bytesWritten": 1_024,
                    "contentDigest": "a".repeat(64),
                    "durationMs": 1_000,
                    "materialId": "323e4567-e89b-42d3-a456-426614174102",
                    "relativePath": "voiceover/sentence-0001.wav",
                    "sequence": 1
                },
                {
                    "bytesWritten": 1_024,
                    "contentDigest": "b".repeat(64),
                    "durationMs": 1_000,
                    "materialId": "423e4567-e89b-42d3-a456-426614174103",
                    "relativePath": "voiceover/sentence-0002.wav",
                    "sequence": 2
                }
            ],
            "schemaVersion": "smart-edit-generation-result.v1"
        }))
        .expect("static smart-edit result");

        result.validate(job_id).expect("valid static result");
        let timeline = result.timeline_document();
        assert!(timeline["tracks"][0]["clips"][0]["sourceInMs"].is_null());
        assert_eq!(timeline["tracks"][1]["clips"][0]["sourceInMs"], 0);
    }

    #[test]
    fn windows_reparse_points_are_rejected_even_when_not_reported_as_symlinks() {
        assert!(unsafe_path_component(false, Some(0x400)));
        assert!(!unsafe_path_component(false, Some(0x20)));
        assert!(unsafe_path_component(true, None));
    }
}

//! Native brand-motion draft, RenderJob and Artifact boundary.
//!
//! The no-model path is deliberately a fixed, declared-variable template. It
//! never calls an authoring model and never claims that variable replacement
//! is one-sentence generation. The generated HTML remains untrusted and goes
//! through the BM-04 renderer sandbox.

use crate::video_job_workspace::{
    RenderedVideoArtifactPayload, VideoArtifactRecord, VideoJobWorkspace, VideoJobWorkspaceStore,
    VideoWorkspaceDisposition, VideoWorkspaceError, VideoWorkspaceErrorCode,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use uuid::Uuid;

pub const MOTION_RENDER_JOB_CHECKPOINT: &str = "motion-render-job";
pub const MOTION_OUTPUT_FILE: &str = "brand-motion-result.mp4";
pub const MOTION_COMPOSITION_FILE: &str = "composition.html";
pub const MOTION_FRAMES_PER_SECOND: u32 = 30;
const MAX_TEXT_CHARS: usize = 160;
const MAX_SUBJECT_CHARS: usize = 80;
const MAX_LOGO_BYTES: usize = 4 * 1024 * 1024;
const MILLIS_PER_SECOND: u32 = 1000;
const STYLE_CONTRACT: &str = include_str!("../../../contracts/video/motion-style-freeze.v1.json");
const MODEL_CALL_CONTRACT: &str =
    include_str!("../../../contracts/video/motion-authoring-model-call.v1.json");
const DURATION_CONTRACT: &str =
    include_str!("../../../contracts/video/motion-storyboard-duration.v1.json");
const BRIEF_CONTRACT: &str =
    include_str!("../../../contracts/video/motion-one-sentence-brief.v1.json");
const AUTHORING_REFUSAL_CONTRACT: &str =
    include_str!("../../../contracts/video/motion-authoring-refusal.v1.json");
const OFFLINE_MOTION_DEPENDENCIES: &str =
    include_str!("../../../contracts/video/offline-motion-dependencies.v1.json");
const CANCEL_MARKER_CONTRACT: &str =
    include_str!("../../../contracts/video/motion-render-cancel-marker.v1.json");
const RENDER_CANVAS_CONTRACT: &str =
    include_str!("../../../contracts/video/motion-render-canvas.v1.json");

/// The file whose appearance in a RenderJob workspace means "stop".
///
/// It is read from the declared contract rather than written here because the
/// Worker is the other half of this convention, and the two used to hold one
/// literal each with nothing connecting them. Editing one of a matched pair is
/// the quietest defect this feature can have: the button answers, the job reads
/// 已取消 and only the render carries on. So the name exists once, and the
/// Worker is handed it in the render request instead of knowing it.
///
/// Every caller that could otherwise start an uncancellable render goes through
/// here first, so an unreadable contract stops the render rather than producing
/// one that ignores the button.
pub fn cancel_marker_file_name() -> Result<&'static str, MotionVideoStudioError> {
    static NAME: std::sync::OnceLock<Option<String>> = std::sync::OnceLock::new();
    NAME.get_or_init(|| {
        let document: serde_json::Value = serde_json::from_str(CANCEL_MARKER_CONTRACT).ok()?;
        let name = document.get("markerFileName")?.as_str()?;
        // A marker that is a path, or empty, would be a workspace escape rather
        // than a cancellation; the Worker refuses those too.
        if name.is_empty() || name.contains(['/', '\\', '\0']) || name == "." || name == ".." {
            return None;
        }
        Some(name.to_owned())
    })
    .as_deref()
    .ok_or_else(authoring_installation_damaged)
}

/// Where the authored composition loads its animation runtime from, relative to
/// the worker asset root. The authoring prompt names this exact path, so it is
/// declared once here and never spelled out again.
pub const AUTHORING_RUNTIME_ASSET: &str = "runtime/gsap.min.js";
/// The stage `composition_template` draws on, and the factor its output is
/// rasterised at.
///
/// Declared here rather than passed in: every type and spacing rule in that
/// template is written for 640x360, so this pair is a property of the template
/// and not of a request. A catalog part carries its own stage instead — see
/// `VideoWorkerRenderCanvas`. Mirrors `width`/`height`/`deviceScaleFactor` in
/// `contracts/video/motion-render-canvas.v1.json`.
pub const TEMPLATE_CANVAS_WIDTH: u32 = 640;
pub const TEMPLATE_CANVAS_HEIGHT: u32 = 360;
pub const TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR: u32 = 2;
/// The framing the fixed template draws, which it offers no choice about.
pub const TEMPLATE_ASPECT_RATIO: &str = "16:9";

/// The package the locked catalog calls this runtime.
const AUTHORING_RUNTIME_PACKAGE: &str = "gsap";
/// The largest runtime this seed will read. The locked build is ~72 KB; a file
/// far past that is not the declared artifact and is refused before it is read
/// into memory.
const MAX_AUTHORING_RUNTIME_BYTES: u64 = 4 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MotionVideoStudioErrorCode {
    /// The one-sentence path needs a video-creation model the user has not set
    /// up yet. It is its own code because "go configure a model" and "the
    /// renderer is broken" send the user to two different places.
    ConfigurationRequired,
    /// The authoring run outlived its budget and was killed.
    ///
    /// Splitting the three authoring outcomes out of `RenderUnavailable` is
    /// what makes this path diagnosable at all: the child's standard error is
    /// deliberately discarded so a model echo cannot reach a log, so the exit
    /// status and the deadline are the only things this side knows — and both
    /// of them are ours, containing nothing the model produced.
    AuthoringTimedOut,
    /// The authoring child completed the protocol and said no: it decided the
    /// request could not be authored.
    ///
    /// This is working software making a decision, so it is kept apart from
    /// `AuthoringCrashed`. Reporting the two as one code says "the feature
    /// refused you" and "the feature is broken" in the same words — it points
    /// the user at the wrong move and hides every real defect among the
    /// ordinary refusals.
    AuthoringRefused,
    /// The authoring child died without completing the protocol.
    ///
    /// Always a defect on our side: the user did nothing wrong and has no move
    /// beyond retrying. A child that crashes cannot write the refusal document,
    /// so its absence is what distinguishes this from `AuthoringRefused`.
    ///
    /// It is also where every answer this side cannot make sense of lands — an
    /// unknown reason, a status contradicting its reason, a class a newer
    /// Executor declares that this build has no code for. Resolving the unknown
    /// towards "our failure" is deliberate: the alternative is guessing, and
    /// the guess that must never be made is "the user wrote a bad sentence".
    AuthoringCrashed,
    /// Nothing ever came back from the video-creation model service.
    ///
    /// Its own code because the user's move is specific and nothing else on
    /// this path shares it: look at the network, then at the model service
    /// address in settings. Kept apart from `AuthoringModelTimedOut` because a
    /// service that answered and then went quiet sends them somewhere else
    /// entirely.
    ///
    /// Named after the transport rather than after "unreachable" because the
    /// Executor cannot narrow it any further and neither can this side: a
    /// refused connection, an address that does not resolve, a TLS handshake
    /// that fails and a connection dropped mid-stream all arrive as one
    /// `OSError` there and one reason token here. Claiming "could not connect"
    /// would be a fifth of those four wrong. What is true of all of them is
    /// that no reply arrived, and that is what the wording says.
    AuthoringModelTransportFailed,
    /// The model service took the connection and then stopped sending.
    ///
    /// Distinct from `AuthoringModelTransportFailed` in the only way that
    /// matters to the person waiting: the service is there, the address and the
    /// network are not the thing to go and check, and the wait had a known
    /// length worth telling them.
    AuthoringModelTimedOut,
    /// Our own packaged files no longer verify.
    ///
    /// Pinned workflow files, the locked catalog and the declared contracts are
    /// read at run time and checked against their digests. When that fails the
    /// installation is damaged, which is neither a refusal nor something a
    /// retry can repair — this is the one authoring code whose move is to
    /// reinstall. It reached users as "describe the film differently" until
    /// 2026-07-27, which no rewrite of any sentence could ever have fixed.
    AuthoringInstallationDamaged,
    /// The authoring child answered, and this side refused the answer.
    ///
    /// The answer names the file the renderer loads and the assets the sandbox
    /// allows, so it is re-checked field by field. A refusal here is a defect
    /// on our side of the boundary, never something the user typed wrong.
    AuthoringAnswerInvalid,
    DraftInvalid,
    JobUnavailable,
    RenderUnavailable,
    StorageUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MotionVideoStudioError {
    code: MotionVideoStudioErrorCode,
    retryable: bool,
}

impl Serialize for MotionVideoStudioError {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        crate::command_error::serialize(&self.code, Some(self.retryable), serializer)
    }
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

/// The declared bounds a typed one-sentence brief is judged against.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MotionBriefLimits {
    max_brief_characters: usize,
    max_brand_assets: usize,
    aspect_ratios: Vec<String>,
    languages: Vec<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct BriefContract {
    schema_version: u8,
    policy: String,
    max_brief_characters: usize,
    max_brand_assets: usize,
    aspect_ratios: Vec<String>,
    languages: Vec<String>,
}

/// Reads the one declaration of what a brief may contain.
///
/// The authoring agent reads the same file. When these bounds lived in two
/// places the form could offer a framing the agent refuses, and neither side
/// could see the disagreement.
pub fn brief_limits() -> Result<MotionBriefLimits, MotionVideoStudioError> {
    let contract: BriefContract =
        serde_json::from_str(BRIEF_CONTRACT).map_err(|_| draft_invalid())?;
    if contract.schema_version != 1
        || contract.policy != "fail_closed"
        || contract.max_brief_characters == 0
        || contract.aspect_ratios.is_empty()
        || contract.languages.is_empty()
    {
        return Err(draft_invalid());
    }
    Ok(MotionBriefLimits {
        max_brief_characters: contract.max_brief_characters,
        max_brand_assets: contract.max_brand_assets,
        aspect_ratios: contract.aspect_ratios,
        languages: contract.languages,
    })
}

impl MotionBriefLimits {
    pub const fn max_brand_assets(&self) -> usize {
        self.max_brand_assets
    }
}

/// One typed sentence, on its way to the authoring agent.
///
/// This is not a variant of the fixed template: the template carries finished
/// copy and a chosen style, this carries intent and lets the agent produce the
/// copy, the storyboard and the composition.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MotionVideoBriefRequest {
    creation_mode: String,
    brief: String,
    aspect_ratio: String,
    duration_seconds: u32,
    language: String,
    /// Whether the video-creation model reasons before it answers.
    ///
    /// Per request rather than per installation: turning it off saves about 31
    /// seconds of model time, and whether that is worth paying depends on
    /// whether this particular film is a rehearsal or a delivery.
    ///
    /// Required, with no serde default. This App ships its own front end, so a
    /// request missing this field is not an older caller — it is a caller that
    /// forgot, and a default here would turn "the operator switched it off"
    /// into "the film quietly ran with it on". That is the shape this line has
    /// been bitten by seven times: a value crosses a boundary, something
    /// downstream has a reasonable fallback, and the product silently does less
    /// than it was told to.
    model_thinking: bool,
}

/// Reasoning stays on unless the operator turns it off — read from the contract
/// both sides share rather than written here, because the sentence under the
/// switch is composed from the same file.
pub fn thinking_default() -> Result<bool, MotionVideoStudioError> {
    let contract: serde_json::Value =
        serde_json::from_str(MODEL_CALL_CONTRACT).map_err(|_| draft_invalid())?;
    contract["thinking"]["defaultEnabled"]
        .as_bool()
        .ok_or_else(draft_invalid)
}

impl MotionVideoBriefRequest {
    pub fn one_sentence(
        brief: String,
        aspect_ratio: String,
        duration_seconds: u32,
        language: String,
    ) -> Result<Self, MotionVideoStudioError> {
        Self::one_sentence_with_thinking(
            brief,
            aspect_ratio,
            duration_seconds,
            language,
            thinking_default()?,
        )
    }

    pub fn one_sentence_with_thinking(
        brief: String,
        aspect_ratio: String,
        duration_seconds: u32,
        language: String,
        model_thinking: bool,
    ) -> Result<Self, MotionVideoStudioError> {
        let value = Self {
            creation_mode: MOTION_BRIEF_CREATION_MODE.to_owned(),
            brief,
            aspect_ratio,
            duration_seconds,
            language,
            model_thinking,
        };
        value.validate()?;
        Ok(value)
    }

    pub fn brief(&self) -> &str {
        self.brief.trim()
    }

    pub fn aspect_ratio(&self) -> &str {
        &self.aspect_ratio
    }

    pub const fn duration_seconds(&self) -> u32 {
        self.duration_seconds
    }

    pub fn language(&self) -> &str {
        &self.language
    }

    pub const fn model_thinking(&self) -> bool {
        self.model_thinking
    }

    /// Judged against the two contracts the agent reads, so a brief this side
    /// accepts is one the agent will also accept — the round trip through a
    /// subprocess is not where a user should discover a bound.
    pub fn validate(&self) -> Result<(), MotionVideoStudioError> {
        let limits = brief_limits()?;
        let duration = duration_limits()?;
        let trimmed = self.brief.trim();
        if self.creation_mode != MOTION_BRIEF_CREATION_MODE
            || trimmed.is_empty()
            || trimmed.chars().count() > limits.max_brief_characters
            || !limits
                .aspect_ratios
                .iter()
                .any(|value| value == &self.aspect_ratio)
            || !limits.languages.iter().any(|value| value == &self.language)
            || self.duration_seconds == 0
            // The one-sentence entry's own ceiling: this path is one render per
            // shot, so the sandbox's single-capture limit is not what bounds it.
            || self.duration_seconds > duration.brief_seconds_maximum()
        {
            return Err(draft_invalid());
        }
        validate_copy(trimmed, limits.max_brief_characters)
    }
}

/// The single creation mode this request carries, declared once so the request
/// and the mode the agent is told to work in can never disagree.
pub const MOTION_BRIEF_CREATION_MODE: &str = "one_sentence_v1";

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
    brief_seconds_maximum: u32,
    brief_beat_count_maximum: u32,
    brief_seconds_per_beat_minimum: u32,
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

    /// The longest film the one-sentence entry lets the operator ask for.
    ///
    /// Larger than `total_seconds_maximum` because the two answer different
    /// questions: that one is the sandbox's single-capture limit and still
    /// binds the fixed-template path, while a one-sentence film is one render
    /// per shot and joined, so its ceiling is a product decision.
    pub const fn brief_seconds_maximum(&self) -> u32 {
        self.brief_seconds_maximum
    }

    /// The most shots a one-sentence storyboard may be cut into.
    pub const fn brief_beat_count_maximum(&self) -> u32 {
        self.brief_beat_count_maximum
    }

    /// The shortest shot the model is ever told to aim for.
    pub const fn brief_seconds_per_beat_minimum(&self) -> u32 {
        self.brief_seconds_per_beat_minimum
    }

    pub const fn frame_count_maximum(&self) -> u32 {
        self.total_seconds_maximum * self.frames_per_second
    }

    /// The plan for an automatically authored film.
    ///
    /// A brief has no beat grid — the agent decides the shots — so the film is
    /// one beat of the requested length. Frame count and budgets are still
    /// derived here, so the authored path can never ask the sandbox for
    /// something the fixed-template path would be refused.
    pub fn brief_plan(
        &self,
        duration_seconds: u32,
    ) -> Result<MotionStoryboardPlan, MotionVideoStudioError> {
        // The brief ceiling, not the template one. Asking `total_seconds_maximum`
        // here was the half of the raise that was missed: the request validator
        // accepted 60 seconds and this refused it, so the operator waited out
        // the whole authoring pass — minutes — to be told his film could not be
        // made, at a length the form had offered him.
        if duration_seconds == 0 || duration_seconds > self.brief_seconds_maximum {
            return Err(draft_invalid());
        }
        Ok(MotionStoryboardPlan {
            beat_count: 1,
            seconds_per_beat: duration_seconds,
            frames_per_second: self.frames_per_second,
        })
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
    brief_seconds_maximum: u32,
    brief_beat_count_maximum: u32,
    brief_seconds_per_beat_minimum: u32,
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
        // Still asked of `total_seconds_maximum` and not of the brief ceiling:
        // the fixed-template path captures its whole film in one pass, so this
        // is its real limit. A one-sentence film is one render per shot and is
        // bounded per shot instead, which is what let its ceiling be raised to
        // something an operator would choose rather than something the sandbox
        // imposes.
        || frame_count_maximum > crate::local_video_orchestrator::SANDBOX_FRAMES_MAXIMUM
        // The choice the operator is offered may not be shorter than the one
        // the template path already allows, and a shot still has to fit one
        // capture — that bound lives on the segment and is enforced where
        // segments are accepted.
        || contract.brief_seconds_maximum < contract.total_seconds_maximum
        // A film at the brief ceiling has to be cuttable into shots this side
        // can actually render: at most `briefBeatCountMaximum` of them, each
        // inside one capture. Without this the form could offer a length no
        // storyboard could legally express, and the refusal would arrive
        // minutes later with the authoring pass already paid for.
        || contract.brief_beat_count_maximum == 0
        || contract.brief_seconds_per_beat_minimum == 0
        || contract.brief_seconds_maximum
            > contract
                .brief_beat_count_maximum
                .saturating_mul(frame_count_maximum / contract.frames_per_second)
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
        brief_seconds_maximum: contract.brief_seconds_maximum,
        brief_beat_count_maximum: contract.brief_beat_count_maximum,
        brief_seconds_per_beat_minimum: contract.brief_seconds_per_beat_minimum,
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
    Cancelling,
    Cancelled,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MotionRenderFailureCode {
    RenderFailed,
    EncodingFailed,
    Interrupted,
    StaticRender,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MotionRenderShotSnapshot {
    index: u32,
    start_frame: u32,
    frame_count: u32,
    rendered_start_frame: Option<u32>,
    rendered_frame_count: Option<u32>,
    part: Option<String>,
    narration_seconds: Option<f64>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
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
    #[serde(default)]
    shot_structure: Vec<MotionRenderShotSnapshot>,
}

impl MotionRenderJobSnapshot {
    pub const fn status(&self) -> MotionRenderJobStatus {
        self.status
    }

    pub const fn progress_percent(&self) -> u8 {
        self.progress_percent
    }
}

/// One render inside a film, checked and ready for the sandbox.
///
/// A film is a list of these because the catalog's parts do not share a stage:
/// most declare 1920x1080, three declare 1080x1920, one 1440x2560, and the
/// built-in template draws on 640x360 at factor 2. One render per shot is what
/// lets each be itself; bringing them onto one canvas is the join's job, after
/// each has been captured on its own.
#[derive(Clone, Debug)]
pub struct MotionRenderSegment {
    entry_html: String,
    allowed_assets: Vec<String>,
    width: u32,
    height: u32,
    device_scale_factor: u32,
    frame_count: u32,
    source_start_millis: u32,
    source_end_millis: u32,
    part: Option<String>,
    // PC-26: the workspace-relative narration for this shot, verified real at
    // acceptance. None on a silent shot; the mix skips films where every shot
    // is None, which keeps the pre-narration pipeline byte-identical.
    narration_audio: Option<String>,
    narration_seconds: Option<f64>,
}

impl MotionRenderSegment {
    pub fn entry_html(&self) -> &str {
        &self.entry_html
    }

    pub fn allowed_assets(&self) -> &[String] {
        &self.allowed_assets
    }

    pub const fn width(&self) -> u32 {
        self.width
    }

    pub const fn height(&self) -> u32 {
        self.height
    }

    pub const fn device_scale_factor(&self) -> u32 {
        self.device_scale_factor
    }

    /// Where on the loaded document's own timeline this render starts.
    ///
    /// Two template shots load the same composition and differ by nothing else.
    /// Without this the Worker's only rule was "spread the page's whole
    /// timeline over the frames asked for", so each of them re-rendered the
    /// entire film — the kept artifact of 2026-07-28 is twelve seconds made of
    /// two identical six second halves, each at double speed, with the codec,
    /// the canvas, the frame count, the duration and the still-image gate all
    /// green over it.
    pub const fn source_start_millis(&self) -> u32 {
        self.source_start_millis
    }

    /// Where that stretch ends. Always greater than the start.
    pub const fn source_end_millis(&self) -> u32 {
        self.source_end_millis
    }

    pub const fn frame_count(&self) -> u32 {
        self.frame_count
    }

    pub fn part(&self) -> Option<&str> {
        self.part.as_deref()
    }

    pub fn narration_audio(&self) -> Option<&str> {
        self.narration_audio.as_deref()
    }

    pub const fn narration_seconds(&self) -> Option<f64> {
        self.narration_seconds
    }
}

fn declared_shot_structure(
    segments: &[MotionRenderSegment],
) -> Result<Vec<MotionRenderShotSnapshot>, MotionVideoStudioError> {
    if segments.is_empty() {
        return Err(job_unavailable());
    }
    let mut start_frame = 0_u32;
    let mut structure = Vec::with_capacity(segments.len());
    for (offset, segment) in segments.iter().enumerate() {
        let index = u32::try_from(offset + 1).map_err(|_| job_unavailable())?;
        structure.push(MotionRenderShotSnapshot {
            index,
            start_frame,
            frame_count: segment.frame_count,
            rendered_start_frame: None,
            rendered_frame_count: None,
            part: segment.part.clone(),
            narration_seconds: segment.narration_seconds,
        });
        start_frame = start_frame
            .checked_add(segment.frame_count)
            .ok_or_else(job_unavailable)?;
    }
    Ok(structure)
}

#[derive(Clone, Debug)]
pub struct PreparedMotionRenderJob {
    render_job_id: Uuid,
    allowed_assets: Vec<String>,
    segments: Vec<MotionRenderSegment>,
    film_canvas: MotionFilmCanvas,
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

    pub fn segments(&self) -> &[MotionRenderSegment] {
        &self.segments
    }

    /// The canvas this film is delivered on, settled where the framing is known.
    ///
    /// Resolved at prepare time rather than at render time: the render thread
    /// sees a list of shots and a workspace, and has no way back to the framing
    /// the user picked.
    pub const fn film_canvas(&self) -> MotionFilmCanvas {
        self.film_canvas
    }

    /// How many frames the finished film carries: the sum of its shots.
    ///
    /// Deliberately not the brief's `durationSeconds` x fps, which is what
    /// `frame_count` reports. A shot runs for whichever is longer, the narrated
    /// line or the part's own motion, so the requested length steers how much
    /// the storyboard tries to say rather than where the film is cut off — the
    /// product owner's correction of 2026-07-27, and the reason the timeline
    /// plans a film as the sum of its shots.
    pub fn film_frame_count(&self) -> u32 {
        self.segments
            .iter()
            .map(MotionRenderSegment::frame_count)
            .sum()
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

    // One shot, on the template's own stage. Stated rather than left empty so
    // the render loop and the retained product shot table describe one shape.
    let segments = vec![MotionRenderSegment {
        entry_html: MOTION_COMPOSITION_FILE.to_owned(),
        allowed_assets: allowed_assets.clone(),
        width: TEMPLATE_CANVAS_WIDTH,
        height: TEMPLATE_CANVAS_HEIGHT,
        device_scale_factor: TEMPLATE_CANVAS_DEVICE_SCALE_FACTOR,
        frame_count: plan.frame_count(),
        // The whole composition, because this path captures the whole film in
        // one pass. The window only has work to do where several shots load
        // one document.
        source_start_millis: 0,
        source_end_millis: plan.beat_count() * plan.seconds_per_beat() * 1000,
        part: None,
        // The fixed-template path predates narration and stays silent.
        narration_audio: None,
        narration_seconds: None,
    }];
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
        shot_structure: declared_shot_structure(&segments)?,
    };
    save_snapshot(store, workspace, &snapshot)?;
    Ok(PreparedMotionRenderJob {
        render_job_id: workspace.job_id(),
        segments,
        // The fixed template draws a 16:9 stage and offers no choice of
        // framing, so its film is delivered on the 16:9 canvas.
        film_canvas: film_canvas(TEMPLATE_ASPECT_RATIO)?,
        allowed_assets,
        plan,
    })
}

/// The digest the locked dependency catalog declares for the animation runtime.
///
/// Read from the same contract `build_offline_motion_catalog.py` locks, so the
/// bytes the release assembles and the bytes this seed accepts can never be
/// two different decisions.
fn locked_authoring_runtime_digest() -> Result<String, MotionVideoStudioError> {
    let contract: serde_json::Value =
        serde_json::from_str(OFFLINE_MOTION_DEPENDENCIES).map_err(|_| render_unavailable())?;
    if contract
        .get("schemaVersion")
        .and_then(serde_json::Value::as_u64)
        != Some(1)
    {
        return Err(render_unavailable());
    }
    let digest = contract
        .get("artifacts")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(render_unavailable)?
        .iter()
        .find(|artifact| {
            artifact.get("package").and_then(serde_json::Value::as_str)
                == Some(AUTHORING_RUNTIME_PACKAGE)
        })
        .and_then(|artifact| artifact.get("sha256"))
        .and_then(serde_json::Value::as_str)
        .ok_or_else(render_unavailable)?;
    if digest.len() != 64 || !digest.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(render_unavailable());
    }
    Ok(digest.to_owned())
}

/// Place the animation runtime the authored composition will load, or refuse.
///
/// The digest is checked against the locked catalog before anything is written,
/// on the same terms as the packaged fonts and Chromium: a runtime that is not
/// the declared one is refused outright. Falling back to it would produce
/// exactly the failure this line already shipped once — a composition that
/// loads nothing, animates nothing and encodes into a still picture that every
/// other check reads as a finished video.
pub fn seed_authoring_runtime(
    store: &VideoJobWorkspaceStore,
    workspace: &VideoJobWorkspace,
    source: &Path,
) -> Result<PathBuf, MotionVideoStudioError> {
    let expected = locked_authoring_runtime_digest()?;
    let metadata = fs::symlink_metadata(source).map_err(|_| render_unavailable())?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_AUTHORING_RUNTIME_BYTES {
        return Err(render_unavailable());
    }
    let bytes = fs::read(source).map_err(|_| render_unavailable())?;
    if sha256_hex(&bytes) != expected {
        return Err(render_unavailable());
    }
    let work = store
        .worker_asset_directory(workspace)
        .map_err(map_workspace_error)?;
    let destination = work.join(AUTHORING_RUNTIME_ASSET);
    let parent = destination.parent().ok_or_else(render_unavailable)?;
    fs::create_dir_all(parent).map_err(|_| storage_unavailable())?;
    write_private_file(&destination, &bytes)?;
    Ok(destination)
}

/// What the authoring agent reports back after it has written a composition.
///
/// This crosses a process boundary from a component whose whole job is to turn
/// untrusted model output into files, so it is parsed strictly and then
/// re-checked field by field against the brief the user actually submitted.
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct AuthoredCompositionAnswer {
    schema_version: u8,
    status: String,
    entry_html: String,
    allowed_assets: Vec<String>,
    frame_count: u32,
    frames_per_second: u32,
    duration_seconds: u32,
    aspect_ratio: String,
    /// The renders this film is made of, one per shot.
    ///
    /// Required rather than optional. Accepting an answer without it and
    /// falling back to the single composition is exactly the silence PC-04 was
    /// about: the model chose parts for a year and the choice reached nothing,
    /// because every layer downstream had a reasonable default. The Executor
    /// ships inside this App, so there is no older child to be lenient towards.
    segments: Vec<RenderSegmentAnswer>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RenderSegmentAnswer {
    entry_html: String,
    allowed_assets: Vec<String>,
    canvas: RenderSegmentCanvasAnswer,
    frame_count: u32,
    source_start_millis: u32,
    source_end_millis: u32,
    // T2.2: retained as product metadata only after it agrees with the
    // catalog working-copy path. A template shot carries null.
    #[serde(default)]
    part: Option<String>,
    // PC-26: which narration belongs to this shot and how long it really is.
    // Optional as a pair — a silent film's answer carries neither, and the
    // child only writes them together.
    #[serde(default)]
    narration_audio: Option<String>,
    #[serde(default)]
    narration_seconds: Option<f64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RenderSegmentCanvasAnswer {
    width: u32,
    height: u32,
    device_scale_factor: u32,
}

/// The name the agent gives a run it completed.
const AUTHORED_STATUS: &str = "authored";

/// The name the agent gives a run it declined to do.
const REFUSED_STATUS: &str = "rejected";

/// The whole of the document a refusing agent writes.
///
/// A current child includes one dedicated reason token. An older packaged
/// Executor may omit it, so the field remains optional for classification; if
/// present, it must belong to the separate closed refusal contract. Arbitrary
/// text therefore cannot turn this narrow field into a general error-detail
/// channel.
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RefusedAuthoringAnswer {
    schema_version: u8,
    status: String,
    rejection_reason: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct AuthoringRefusalContract {
    schema_version: u8,
    id: String,
    version: String,
    policy: String,
    fixed_reasons: Vec<String>,
    static_gate_reason_prefix: String,
    static_gate_codes: Vec<String>,
    /// Which findings are not the agent declining this brief, grouped by what
    /// the user can do about them. The child writes the class name as its
    /// answer status, so the two sides never keep separate copies of this.
    non_refusal_outcomes: BTreeMap<String, Vec<String>>,
    rationale: serde_json::Value,
}

fn is_wire_token(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_')
}

fn strictly_sorted_unique(values: &[String]) -> bool {
    values
        .windows(2)
        .all(|pair| pair[0].as_str() < pair[1].as_str())
}

fn refusal_contract() -> Option<AuthoringRefusalContract> {
    let contract =
        serde_json::from_str::<AuthoringRefusalContract>(AUTHORING_REFUSAL_CONTRACT).ok()?;
    let prefix = contract.static_gate_reason_prefix.strip_suffix(':')?;
    if contract.schema_version != 1
        || contract.id != "motion-authoring-refusal"
        || contract.version != "motion-authoring-refusal.v1"
        || contract.policy != "fail_closed"
        || contract.fixed_reasons.is_empty()
        || !strictly_sorted_unique(&contract.fixed_reasons)
        || !contract
            .fixed_reasons
            .iter()
            .all(|reason| is_wire_token(reason))
        || !is_wire_token(prefix)
        || contract.static_gate_codes.is_empty()
        || !strictly_sorted_unique(&contract.static_gate_codes)
        || !contract
            .static_gate_codes
            .iter()
            .all(|code| is_wire_token(code))
        || !contract.rationale.is_object()
        || !outcomes_are_declared(&contract)
    {
        return None;
    }
    Some(contract)
}

/// Is the non-refusal table a partition of known tokens into named classes?
///
/// Overlap is the shape worth rejecting outright: a token in two classes would
/// be reported as whichever was iterated first, and both sides would go on
/// looking consistent while the card said different things on different days.
fn outcomes_are_declared(contract: &AuthoringRefusalContract) -> bool {
    if contract.non_refusal_outcomes.is_empty() {
        return false;
    }
    let mut seen: Vec<&str> = Vec::new();
    for (name, reasons) in &contract.non_refusal_outcomes {
        if !is_wire_token(name)
            || name == REFUSED_STATUS
            || reasons.is_empty()
            || !strictly_sorted_unique(reasons)
            || !reasons.iter().all(|reason| {
                contract
                    .fixed_reasons
                    .iter()
                    .any(|candidate| candidate == reason)
            })
            || reasons.iter().any(|reason| seen.contains(&reason.as_str()))
        {
            return false;
        }
        seen.extend(reasons.iter().map(String::as_str));
    }
    true
}

fn rejection_reason_is_closed(contract: &AuthoringRefusalContract, reason: &str) -> bool {
    if contract
        .fixed_reasons
        .iter()
        .any(|candidate| candidate == reason)
    {
        return true;
    }
    let Some(suffix) = reason.strip_prefix(&contract.static_gate_reason_prefix) else {
        return false;
    };
    let codes = suffix.split('+').collect::<Vec<_>>();
    !codes.is_empty()
        && codes.iter().all(|code| !code.is_empty())
        && codes.windows(2).all(|pair| pair[0] < pair[1])
        && codes.iter().all(|code| {
            contract
                .static_gate_codes
                .iter()
                .any(|candidate| candidate == code)
        })
}

/// The code this build reports for a class the contract declares.
///
/// `app_request_invalid` is a real class that genuinely belongs on
/// `AuthoringCrashed`: the child judged the request this side built, so it is
/// our defect with no user move beyond retrying. `executor_defect` is the same
/// answer arrived at from the other end — the child's own construction was
/// wrong — and the two are named apart on the wire even though they report the
/// same code, because the class is a claim about what happened and only one of
/// them is a claim about the request. A class this build has never heard of
/// lands there too, for the same reason it has to: an unknown outcome is not
/// evidence of anything the user did.
fn code_for_non_refusal_class(class: &str) -> MotionVideoStudioError {
    match class {
        "installation_damaged" => authoring_installation_damaged(),
        "model_configuration_required" => configuration_required(),
        "model_timed_out" => authoring_model_timed_out(),
        "model_transport_failed" => authoring_model_transport_failed(),
        _ => authoring_crashed(),
    }
}

/// What actually happened to a child that exited non-zero.
///
/// It is answered from the document rather than the exit status because only
/// the document is evidence: a process that crashed cannot have written it,
/// while any half-finished process can still return a number.
///
/// The distinction that used to be the whole of this function — refused against
/// crashed — turned out to be too coarse by half. A refusal means the agent read
/// this brief and declined it, and it is the only outcome entitled to ask the
/// user for a different sentence. Failure injection on 2026-07-26 found three
/// things arriving in that document having read no sentence at all: a model
/// service that was never reached, one that answered and then went silent for
/// 363 seconds, and a tree whose pinned files were simply not there. All three
/// told the user to describe the film differently.
///
/// So the reason token decides, and the class it belongs to comes from the same
/// contract the child writes against. The status is corroboration only: an
/// older Executor answers these on `rejected`, and a status that names a class
/// other than the reason's is a child this side does not understand, which
/// resolves to our failure rather than to the user's.
///
/// Nothing here is surfaced — the bytes are classified and dropped — and the
/// child only ever writes its own protocol documents to stdout, so no model
/// output is involved on any branch.
pub fn classify_failed_authoring_answer(answer: &str) -> MotionVideoStudioError {
    let (Some(contract), Ok(document)) = (
        refusal_contract(),
        serde_json::from_str::<RefusedAuthoringAnswer>(answer),
    ) else {
        return authoring_crashed();
    };
    if document.schema_version != 1 {
        return authoring_crashed();
    }
    let refused_or_crashed = if document.status == REFUSED_STATUS {
        authoring_refused()
    } else {
        authoring_crashed()
    };
    // No reason at all is an older packaged Executor, which only ever wrote the
    // refusal document; there is nothing to classify it by beyond its status.
    let Some(reason) = document.rejection_reason.as_deref() else {
        return refused_or_crashed;
    };
    if !rejection_reason_is_closed(&contract, reason) {
        return authoring_crashed();
    }
    let class = contract
        .non_refusal_outcomes
        .iter()
        .find_map(|(name, reasons)| {
            reasons
                .iter()
                .any(|candidate| candidate == reason)
                .then_some(name.as_str())
        });
    match class {
        // The agent read the brief and declined it: the one refusal there is.
        None => refused_or_crashed,
        Some(class) if document.status == class || document.status == REFUSED_STATUS => {
            code_for_non_refusal_class(class)
        }
        Some(_) => authoring_crashed(),
    }
}

/// Turn an agent answer into a RenderJob, or refuse it.
///
/// The answer names the file the renderer will load and the assets the render
/// sandbox will allow. Accepting it as given would let a buggy — or tampered —
/// agent widen the sandbox or point the render at a file nobody authored, so
/// every field is re-derived from the brief or re-checked against the
/// workspace. Nothing here trusts the agent's arithmetic.
pub fn accept_authored_render_job(
    store: &VideoJobWorkspaceStore,
    workspace: &VideoJobWorkspace,
    request: &MotionVideoBriefRequest,
    answer: &str,
) -> Result<PreparedMotionRenderJob, MotionVideoStudioError> {
    let answer: AuthoredCompositionAnswer =
        serde_json::from_str(answer).map_err(|_| authoring_answer_invalid())?;
    let plan = duration_limits()?.brief_plan(request.duration_seconds())?;
    if answer.schema_version != 1
        || answer.status != AUTHORED_STATUS
        || answer.entry_html != MOTION_COMPOSITION_FILE
        || answer.duration_seconds != request.duration_seconds()
        || answer.aspect_ratio != request.aspect_ratio()
        || answer.frames_per_second != plan.frames_per_second()
        || answer.frame_count != plan.frame_count()
    {
        return Err(authoring_answer_invalid());
    }
    let work = store
        .worker_asset_directory(workspace)
        .map_err(map_workspace_error)?;
    if !work.join(MOTION_COMPOSITION_FILE).is_file() {
        return Err(authoring_answer_invalid());
    }
    let mut allowed_assets = Vec::new();
    for asset in &answer.allowed_assets {
        allowed_assets.push(workspace_relative_file(&work, asset)?);
    }
    if !allowed_assets
        .iter()
        .any(|asset| asset == AUTHORING_RUNTIME_ASSET)
    {
        return Err(authoring_answer_invalid());
    }
    let segments = accepted_segments(&work, &answer.segments, plan.frames_per_second())?;
    let snapshot = MotionRenderJobSnapshot {
        render_job_id: workspace.job_id(),
        revision: 1,
        status: MotionRenderJobStatus::Queued,
        progress_percent: 5,
        subject: request.brief().to_owned(),
        style_display_name: MOTION_BRIEF_STYLE_DISPLAY_NAME.to_owned(),
        artifact_id: None,
        artifact_size_bytes: None,
        failure_code: None,
        shot_structure: declared_shot_structure(&segments)?,
    };
    save_snapshot(store, workspace, &snapshot)?;
    Ok(PreparedMotionRenderJob {
        render_job_id: workspace.job_id(),
        allowed_assets,
        segments,
        film_canvas: film_canvas(request.aspect_ratio())?,
        plan,
    })
}

/// One path the child named, as a file that exists inside the workspace.
///
/// The sandbox resolves these against the worker asset root, so an absolute
/// path or a parent traversal would point outside the workspace the App bounds
/// and eventually deletes. Shared by the composition's own asset list and by
/// every segment's, so a segment cannot widen the sandbox on terms the
/// composition was never allowed.
fn workspace_relative_file(work: &Path, relative: &str) -> Result<String, MotionVideoStudioError> {
    if relative.is_empty()
        || relative.len() > 256
        || relative.starts_with('/')
        || relative.contains('\\')
        || Path::new(relative)
            .components()
            .any(|component| !matches!(component, std::path::Component::Normal(_)))
        || !work.join(relative).is_file()
    {
        return Err(authoring_answer_invalid());
    }
    Ok(relative.to_owned())
}

fn accepted_segment_part(
    entry_html: &str,
    answered: Option<&str>,
) -> Result<Option<String>, MotionVideoStudioError> {
    if entry_html == MOTION_COMPOSITION_FILE {
        return if answered.is_none() {
            Ok(None)
        } else {
            Err(authoring_answer_invalid())
        };
    }
    let Some(part) = answered else {
        return Err(authoring_answer_invalid());
    };
    if part.is_empty()
        || part.len() > 80
        || !part
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
    {
        return Err(authoring_answer_invalid());
    }
    let Some(relative) = entry_html.strip_prefix("catalog/items/") else {
        return Err(authoring_answer_invalid());
    };
    let Some((directory, document)) = relative.split_once('/') else {
        return Err(authoring_answer_invalid());
    };
    if directory != part
        || document.is_empty()
        || document.contains('/')
        || !document.ends_with(".html")
    {
        return Err(authoring_answer_invalid());
    }
    Ok(Some(part.to_owned()))
}

/// Every render this film is made of, or a refusal.
///
/// A segment is checked on exactly the terms the single composition already
/// was, plus the two the sandbox would apply anyway: the stage has to be one
/// the worker can open, and a render has to fit inside one capture. Both are
/// asked of the types that own those bounds rather than restated here, so there
/// stays one place that decides what a frame may cost.
fn accepted_segments(
    work: &Path,
    answered: &[RenderSegmentAnswer],
    frames_per_second: u32,
) -> Result<Vec<MotionRenderSegment>, MotionVideoStudioError> {
    if answered.is_empty() || frames_per_second == 0 {
        return Err(authoring_answer_invalid());
    }
    let mut segments = Vec::with_capacity(answered.len());
    for segment in answered {
        let entry_html = workspace_relative_file(work, &segment.entry_html)?;
        let part = accepted_segment_part(&entry_html, segment.part.as_deref())?;
        let mut allowed_assets = Vec::with_capacity(segment.allowed_assets.len());
        for asset in &segment.allowed_assets {
            allowed_assets.push(workspace_relative_file(work, asset)?);
        }
        // The authored composition loads the animation runtime by name. A
        // segment drawing it without that asset allowed renders a page whose
        // script never arrives, and a browser reports that by drawing the first
        // frame and holding it — a still image with every other signal green.
        if entry_html == MOTION_COMPOSITION_FILE
            && !allowed_assets
                .iter()
                .any(|asset| asset == AUTHORING_RUNTIME_ASSET)
        {
            return Err(authoring_answer_invalid());
        }
        crate::local_video_orchestrator::VideoWorkerRenderCanvas::new(
            segment.canvas.width,
            segment.canvas.height,
            segment.canvas.device_scale_factor,
        )
        .map_err(|_| authoring_answer_invalid())?;
        if !(1..=crate::local_video_orchestrator::SANDBOX_FRAMES_MAXIMUM)
            .contains(&segment.frame_count)
        {
            return Err(authoring_answer_invalid());
        }
        // A window that ends no later than it starts leaves every frame of this
        // shot seeking nowhere. Checked here rather than in the Worker because
        // this is where the child's word stops being taken.
        if segment.source_end_millis <= segment.source_start_millis {
            return Err(authoring_answer_invalid());
        }
        // PC-26: narration arrives as a pair, its audio must really be in the
        // workspace, and its measured seconds must fit inside this shot — the
        // child laid the timeline as max(voice, motion), so a line longer than
        // its own shot means the answer is not the one that plan produced.
        // Half a frame of float grace: frames = ceil(seconds x fps).
        let narration_audio = match (&segment.narration_audio, segment.narration_seconds) {
            (None, None) => None,
            (Some(audio), Some(seconds)) => {
                let shot_seconds = f64::from(segment.frame_count) / f64::from(frames_per_second);
                if !seconds.is_finite() || seconds <= 0.0 || seconds > shot_seconds + 0.5 {
                    return Err(authoring_answer_invalid());
                }
                Some(workspace_relative_file(work, audio)?)
            }
            _ => return Err(authoring_answer_invalid()),
        };
        segments.push(MotionRenderSegment {
            entry_html,
            allowed_assets,
            width: segment.canvas.width,
            height: segment.canvas.height,
            device_scale_factor: segment.canvas.device_scale_factor,
            frame_count: segment.frame_count,
            source_start_millis: segment.source_start_millis,
            source_end_millis: segment.source_end_millis,
            part,
            narration_audio,
            narration_seconds: segment.narration_seconds,
        });
    }
    Ok(segments)
}

/// How an automatically authored film is labelled in the jobs and artifacts
/// pages. The fixed-template path shows the locked style there; this path has
/// no style to show, so it says how the film was made instead.
pub const MOTION_BRIEF_STYLE_DISPLAY_NAME: &str = "一句话自动制作";

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

/// Retain what ffprobe decoded from each encoded shot before the intermediates
/// are removed.
///
/// This is deliberately separate from the answer's declared table. Copying
/// `frame_count` into both columns would make T2.2 agree with itself while
/// measuring nothing. The render loop supplies counts decoded from the actual
/// segment MP4s, and every cumulative start/end boundary must stay within one
/// frame of what the accepted answer declared.
pub fn record_rendered_shot_frames(
    store: &VideoJobWorkspaceStore,
    render_job_id: Uuid,
    rendered_frames: &[u32],
) -> Result<MotionRenderJobSnapshot, MotionVideoStudioError> {
    let workspace = store.open(render_job_id).map_err(map_workspace_error)?;
    let mut current = load_snapshot(store, &workspace)?.ok_or_else(job_unavailable)?;
    if current.status != MotionRenderJobStatus::Encoding
        || current.shot_structure.is_empty()
        || current.shot_structure.len() != rendered_frames.len()
        || current
            .shot_structure
            .iter()
            .any(|shot| shot.rendered_start_frame.is_some() || shot.rendered_frame_count.is_some())
    {
        return Err(job_unavailable());
    }
    let mut declared_start = 0_u32;
    let mut rendered_start = 0_u32;
    for (shot, rendered) in current.shot_structure.iter_mut().zip(rendered_frames) {
        if *rendered == 0 || declared_start.abs_diff(rendered_start) > 1 {
            return Err(job_unavailable());
        }
        shot.rendered_start_frame = Some(rendered_start);
        shot.rendered_frame_count = Some(*rendered);
        declared_start = declared_start
            .checked_add(shot.frame_count)
            .ok_or_else(job_unavailable)?;
        rendered_start = rendered_start
            .checked_add(*rendered)
            .ok_or_else(job_unavailable)?;
        if declared_start.abs_diff(rendered_start) > 1 {
            return Err(job_unavailable());
        }
    }
    current.revision = current
        .revision
        .checked_add(1)
        .ok_or_else(job_unavailable)?;
    save_snapshot(store, &workspace, &current)?;
    Ok(current)
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
    // Once a stop has been asked for the run may only settle. Without this a
    // stage that was already in flight when the request landed would overwrite
    // 正在取消 with 正在合成视频 and put the cancel button back on a job that is
    // already stopping.
    if current.status == MotionRenderJobStatus::Cancelling
        && !matches!(
            status,
            MotionRenderJobStatus::Cancelled
                | MotionRenderJobStatus::Succeeded
                | MotionRenderJobStatus::Failed
        )
    {
        return Err(job_unavailable());
    }
    let valid = match status {
        MotionRenderJobStatus::Rendering => {
            RENDERING_PROGRESS.contains(&progress_percent) && artifact.is_none()
        }
        MotionRenderJobStatus::Encoding => progress_percent == 85 && artifact.is_none(),
        MotionRenderJobStatus::Succeeded => {
            progress_percent == 100 && artifact.is_some() && failure_code.is_none()
        }
        MotionRenderJobStatus::Failed => {
            progress_percent < 100 && artifact.is_none() && failure_code.is_some()
        }
        MotionRenderJobStatus::Cancelling | MotionRenderJobStatus::Cancelled => {
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

/// Ask a running render to stop.
///
/// This records a *request*, and that is the whole of what this side knows.
/// The render lives in a Worker process driving a browser and, later, in an
/// FFmpeg child; only the thread that owns them can say the work has actually
/// stopped, so only that thread writes the terminal `Cancelled`
/// (`CLAUDE.md` §4.4). Settling the job here instead was not merely early — it
/// dropped the executor's own settlement on the floor, because `advance`
/// refuses to move a job that has already reached a terminal state. A film
/// whose encode finished in the same instant was imported and then referenced
/// by nothing.
///
/// `Cancelling` is a snapshot state rather than something the page remembers
/// because the studio page unmounts whenever the operator clicks another
/// sidebar entry, and a render outlives that by minutes. An "I already pressed
/// cancel" kept in React would be gone when he came back — and gone again after
/// a restart — leaving him looking at 正在合成视频 with the button offering
/// itself a second time.
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
            | MotionRenderJobStatus::Cancelling
    ) {
        return Err(job_unavailable());
    }
    let marker = store
        .worker_asset_directory(&workspace)
        .map_err(map_workspace_error)?
        .join(cancel_marker_file_name()?);
    match OpenOptions::new().create_new(true).write(true).open(marker) {
        Ok(mut file) => file
            .write_all(b"cancel\n")
            .and_then(|()| file.sync_all())
            .map_err(|_| storage_unavailable())?,
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(_) => return Err(storage_unavailable()),
    }
    // Pressing cancel twice is the same request, not a second state and not an
    // error: the marker is already there and the executor is already stopping.
    if current.status == MotionRenderJobStatus::Cancelling {
        return Ok(());
    }
    advance(
        store,
        render_job_id,
        MotionRenderJobStatus::Cancelling,
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
        .join(cancel_marker_file_name()?);
    match fs::symlink_metadata(marker) {
        Ok(metadata) => Ok(metadata.file_type().is_file() && !metadata.file_type().is_symlink()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(_) => Err(storage_unavailable()),
    }
}

pub fn read_artifact(
    store: &VideoJobWorkspaceStore,
    artifact_id: Uuid,
) -> Result<RenderedVideoArtifactPayload, MotionVideoStudioError> {
    store
        .read_rendered_video_artifact(artifact_id)
        .map_err(map_rendered_video_error)
}

/// A film that is gone, is not a film, or is too large to hold is the user's
/// answer — "pick another one" — not a storage fault they could act on.
fn map_rendered_video_error(error: VideoWorkspaceError) -> MotionVideoStudioError {
    match error.code() {
        VideoWorkspaceErrorCode::NotFound | VideoWorkspaceErrorCode::QuotaExceeded => {
            job_unavailable()
        }
        _ => storage_unavailable(),
    }
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

/// Where the progress bar may sit while shots are being captured.
///
/// One band rather than one number, because a film is a list of renders now and
/// the person watching needs to see it advance. It was a single 55 for as long
/// as a film was one render, and when the loop started dividing the band among
/// the shots the state machine still required exactly 55 — so `advance` refused
/// the first shot's progress and every render failed before a browser started.
/// The rule lives here once and is read by `advance`, by `validate_snapshot`
/// and by the loop that produces the numbers, so the three cannot disagree
/// again.
///
/// It stops below the encode stage's 85: a bar that reaches the encode number
/// while shots are still being captured says the render finished.
const RENDERING_PROGRESS: std::ops::RangeInclusive<u8> = 5..=84;

/// Where the bar sits while shot `index` of `total` is being captured.
///
/// Spread across the band rather than parked on one number: a nine-shot film
/// that sat still until the last shot finished is indistinguishable from a
/// stuck one, and these renders are minutes long.
pub fn rendering_progress_percent(index: usize, total: usize) -> u8 {
    let start = *RENDERING_PROGRESS.start();
    let span = u32::from(*RENDERING_PROGRESS.end() - start);
    if total == 0 {
        return start;
    }
    // Saturating rather than wrapping: `index` comes from a list whose length
    // the answer decided, and a bar outside its own band is refused by
    // `advance` — which is exactly the failure this constant was introduced to
    // stop being possible.
    let offset = (index as u64 * u64::from(span) / total as u64).min(u64::from(span));
    start.saturating_add(offset as u8)
}

/// What a finished film is, as opposed to what any one shot was captured on.
///
/// Route A captures each shot on the stage its part declares — 1920x1080 for
/// 105 of the catalog, 1080x1920 for three, 1440x2560 for one, 640x360 at
/// factor 2 for the built-in template. A delivered file is one size throughout,
/// so this is the size every segment is brought onto as it is encoded, and the
/// size the joined result is measured against afterwards.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MotionFilmCanvas {
    width: u32,
    height: u32,
    frames_per_second: u32,
}

impl MotionFilmCanvas {
    pub const fn width(&self) -> u32 {
        self.width
    }

    pub const fn height(&self) -> u32 {
        self.height
    }

    pub const fn frames_per_second(&self) -> u32 {
        self.frames_per_second
    }

    /// The filter chain that puts any captured shot on this canvas.
    ///
    /// Scale to fit and pad the remainder, never stretch: the part was laid out
    /// for the stage it declares, and stretching it is a design change nobody
    /// asked for. `setsar=1` is required rather than tidy — without it the
    /// padded stream carries the source's sample aspect ratio and a player
    /// stretches the result back.
    ///
    /// Applied to every segment including the ones already the right size. A
    /// scale to the size a stream already is costs nothing worth branching on,
    /// and one unconditional path is what makes the join a stream copy that
    /// never has to reconcile anything.
    fn video_filter(&self) -> String {
        format!(
            "scale={width}:{height}:force_original_aspect_ratio=decrease,\
             pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1",
            width = self.width,
            height = self.height,
        )
    }
}

/// What a film is encoded as. One value, because the concat demuxer reconciles
/// nothing: two segments in different pixel formats produce a file whose second
/// half decodes wrong.
const FILM_PIXEL_FORMAT: &str = "yuv420p";

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct FilmCanvasContract {
    frames_per_second: u32,
    pixel_format: String,
    by_aspect_ratio: BTreeMap<String, FilmCanvasSize>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FilmCanvasSize {
    width: u32,
    height: u32,
}

/// The canvas the film the user asked for is delivered on.
///
/// Read from `motion-render-canvas.v1.json` rather than derived from the ratio
/// string: "16:9" says nothing about how many pixels, and a second place that
/// decided would be a second answer to give.
pub fn film_canvas(aspect_ratio: &str) -> Result<MotionFilmCanvas, MotionVideoStudioError> {
    let contract: serde_json::Value =
        serde_json::from_str(RENDER_CANVAS_CONTRACT).map_err(|_| draft_invalid())?;
    let film: FilmCanvasContract =
        serde_json::from_value(contract["film"].clone()).map_err(|_| draft_invalid())?;
    if film.frames_per_second != MOTION_FRAMES_PER_SECOND || film.pixel_format != FILM_PIXEL_FORMAT
    {
        return Err(draft_invalid());
    }
    let size = film
        .by_aspect_ratio
        .get(aspect_ratio)
        .ok_or_else(draft_invalid)?;
    // A film is delivered on an even-sided canvas because yuv420p subsamples
    // chroma by two; an odd side makes libx264 refuse the encode outright.
    if size.width == 0 || size.height == 0 || size.width % 2 == 1 || size.height % 2 == 1 {
        return Err(draft_invalid());
    }
    Ok(MotionFilmCanvas {
        width: size.width,
        height: size.height,
        frames_per_second: film.frames_per_second,
    })
}

/// Encode one captured shot onto the film's canvas.
///
/// Returns the command rather than running it: the render job owns the wait,
/// because the wait is where a cancelled job is noticed and a partial file
/// removed. What belongs here is which arguments produce a segment the join can
/// stream-copy, and that is the same list wherever it is spawned from.
pub fn motion_segment_encode_command(
    ffmpeg: &Path,
    frames_directory: &Path,
    output: &Path,
    canvas: &MotionFilmCanvas,
    frame_count: u32,
) -> std::process::Command {
    let mut command = std::process::Command::new(ffmpeg);
    command
        .args([
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-framerate",
            &canvas.frames_per_second.to_string(),
            "-start_number",
            "1",
            "-i",
        ])
        .arg(frames_directory.join("frame-%05d.png"))
        .args([
            "-frames:v",
            &frame_count.to_string(),
            "-vf",
            &canvas.video_filter(),
            "-r",
            &canvas.frames_per_second.to_string(),
            "-c:v",
            "libx264",
            "-pix_fmt",
            FILM_PIXEL_FORMAT,
            "-movflags",
            "+faststart",
        ])
        .arg(output)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    command
}

/// Join the encoded shots into the film: check each one first, measure after.
///
/// Both halves are load-bearing and they catch different things.
///
/// **Before.** A segment that is not already on the canvas is refused rather
/// than joined. Measured 2026-07-28 with the packaged ffmpeg, concatenating a
/// 1920x1080 segment with a 1080x1920 one through the concat demuxer and
/// `-c copy`: exit 0, and the product decodes as 1920x1080, yuv420p, 30fps,
/// with exactly the frame count its inputs account for. The file is broken
/// anyway — its second half is portrait content inside a container claiming
/// landscape, and only the pixels say so. Nothing measurable about the result
/// distinguishes it from a correct film, which is why the check cannot live
/// there. This was verified the expensive way: an earlier version of this
/// function checked only the product, and
/// `a_join_that_exits_zero_is_still_refused_when_the_film_is_not_what_was_asked_for`
/// went green against a file whose second half was sideways.
///
/// **After.** The product is still measured, for the failures the inputs cannot
/// show: a join that dropped a segment, or produced fewer frames than its parts
/// carried. A film that is not the one its shots account for is deleted rather
/// than handed to the artifact store.
pub fn join_motion_film(
    segments: &[PathBuf],
    output: &Path,
    canvas: &MotionFilmCanvas,
    ffmpeg: &Path,
    ffprobe: &Path,
    expected_segment_frames: &[u32],
) -> Result<Vec<u32>, MotionVideoStudioError> {
    if segments.is_empty()
        || segments.len() != expected_segment_frames.len()
        || expected_segment_frames.contains(&0)
    {
        return Err(render_unavailable());
    }
    let mut rendered_frames = Vec::with_capacity(segments.len());
    for (segment, expected_frames) in segments.iter().zip(expected_segment_frames) {
        // Decode each intermediate before it is deleted. Reading only the
        // stream shape cannot tell a short shot from the answer it was meant
        // to realize, and copying the declared count into metadata is not a
        // measurement.
        let shot = probe_film(ffprobe, segment)?;
        let Some(frames) = shot.frames else {
            return Err(render_unavailable());
        };
        if shot.width != canvas.width
            || shot.height != canvas.height
            || shot.frames_per_second != canvas.frames_per_second
            || shot.pixel_format != FILM_PIXEL_FORMAT
            || frames.abs_diff(*expected_frames) > 1
        {
            return Err(render_unavailable());
        }
        rendered_frames.push(frames);
    }
    let expected_frames = rendered_frames
        .iter()
        .try_fold(0_u32, |total, frames| total.checked_add(*frames))
        .ok_or_else(render_unavailable)?;
    let listing = output.with_extension("concat.txt");
    write_private_file(&listing, concat_listing(segments).as_bytes())?;
    let joined = std::process::Command::new(ffmpeg)
        .args([
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
        ])
        .arg(&listing)
        .args(["-c", "copy", "-movflags", "+faststart"])
        .arg(output)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
    let _ = std::fs::remove_file(&listing);
    let refuse = |output: &Path| {
        let _ = std::fs::remove_file(output);
        render_unavailable()
    };
    if !joined.map(|status| status.success()).unwrap_or(false) {
        return Err(refuse(output));
    }
    let film = probe_film(ffprobe, output).map_err(|_| refuse(output))?;
    if film.frames != Some(expected_frames)
        || film.width != canvas.width
        || film.height != canvas.height
        || film.frames_per_second != canvas.frames_per_second
        || film.pixel_format != FILM_PIXEL_FORMAT
    {
        return Err(refuse(output));
    }
    Ok(rendered_frames)
}

/// The ffmpeg argument list that lays each shot's narration onto the joined
/// film, or None for a silent film.
///
/// Pure on purpose: the offsets arithmetic — each line starts where its own
/// shot starts, which is the sum of the frames before it — is the part a test
/// can hold still, and an encoder run is not. The video stream passes through
/// untouched (`-c:v copy`): mixing narration must never re-encode the frames
/// the still-image gate and the join already verified.
pub fn narration_mix_arguments(
    film: &Path,
    work: &Path,
    segments: &[MotionRenderSegment],
    frames_per_second: u32,
    output: &Path,
) -> Option<Vec<std::ffi::OsString>> {
    if frames_per_second == 0 {
        return None;
    }
    let mut narrated: Vec<(PathBuf, u64)> = Vec::new();
    let mut start_frames: u64 = 0;
    for segment in segments {
        if let Some(audio) = segment.narration_audio() {
            let start_millis = start_frames * 1000 / u64::from(frames_per_second);
            narrated.push((work.join(audio), start_millis));
        }
        start_frames += u64::from(segment.frame_count());
    }
    if narrated.is_empty() {
        return None;
    }
    let mut arguments: Vec<std::ffi::OsString> =
        ["-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i"]
            .iter()
            .map(std::ffi::OsString::from)
            .collect();
    arguments.push(film.into());
    for (audio, _) in &narrated {
        arguments.push("-i".into());
        arguments.push(audio.clone().into());
    }
    let mut filter = String::new();
    for (index, (_, start_millis)) in narrated.iter().enumerate() {
        // adelay wants one delay per channel; naming it twice covers stereo
        // sources while a mono one reads the first value only.
        filter.push_str(&format!(
            "[{}:a]adelay={start_millis}|{start_millis}[n{index}];",
            index + 1
        ));
    }
    for index in 0..narrated.len() {
        filter.push_str(&format!("[n{index}]"));
    }
    // normalize=0: amix's default divides every input by the input count, so a
    // film with three lines would be three times quieter than one with one.
    // The lines never overlap — each shot holds its own — so no headroom is
    // needed.
    filter.push_str(&format!(
        "amix=inputs={}:normalize=0[voice]",
        narrated.len()
    ));
    for argument in [
        "-filter_complex",
        &filter,
        "-map",
        "0:v:0",
        "-map",
        "[voice]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
    ] {
        arguments.push(argument.into());
    }
    arguments.push(output.into());
    Some(arguments)
}

/// Mix the narration into the joined film, in place, or do nothing for a
/// silent film.
///
/// The mixed file replaces the original only after the re-probe agrees the
/// video stream is intact — same frame count, same canvas — so a failed mix
/// can never swap a verified film for a damaged one.
pub fn mix_narration_into_film(
    film: &Path,
    work: &Path,
    segments: &[MotionRenderSegment],
    canvas: &MotionFilmCanvas,
    ffmpeg: &Path,
    ffprobe: &Path,
    expected_frames: u32,
) -> Result<(), MotionVideoStudioError> {
    let voiced = film.with_extension("voiced.mp4");
    let Some(arguments) =
        narration_mix_arguments(film, work, segments, canvas.frames_per_second, &voiced)
    else {
        return Ok(());
    };
    let refuse = |partial: &Path| {
        let _ = std::fs::remove_file(partial);
        render_unavailable()
    };
    let mixed = std::process::Command::new(ffmpeg)
        .args(&arguments)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
    if !mixed.map(|status| status.success()).unwrap_or(false) {
        return Err(refuse(&voiced));
    }
    let probed = probe_film(ffprobe, &voiced).map_err(|_| refuse(&voiced))?;
    if probed.frames != Some(expected_frames)
        || probed.width != canvas.width
        || probed.height != canvas.height
        || probed.pixel_format != FILM_PIXEL_FORMAT
    {
        return Err(refuse(&voiced));
    }
    std::fs::rename(&voiced, film).map_err(|_| refuse(&voiced))
}

/// The demuxer's list file.
///
/// `-safe 0` lets the list carry absolute paths, which makes a path data the
/// demuxer parses: a single quote inside one would close the entry early. The
/// demuxer's escape for that is the same one a POSIX shell uses.
fn concat_listing(segments: &[PathBuf]) -> String {
    let mut listing = String::new();
    for segment in segments {
        let escaped = segment.to_string_lossy().replace('\'', "'\\''");
        listing.push_str(&format!("file '{escaped}'\n"));
    }
    listing
}

struct ProbedFilm {
    width: u32,
    height: u32,
    frames_per_second: u32,
    pixel_format: String,
    frames: Option<u32>,
}

/// What this file actually is, asked of the same toolchain that made it.
///
/// `-count_frames` rather than the container's frame count: the container is a
/// claim and the decoded stream is the fact, and the incident this join is
/// guarded against is the two disagreeing without saying so.
fn probe_film(ffprobe: &Path, path: &Path) -> Result<ProbedFilm, MotionVideoStudioError> {
    let mut command = std::process::Command::new(ffprobe);
    command.args(["-v", "error", "-count_frames"]);
    let output = command
        .args([
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate,pix_fmt,nb_read_frames",
            "-of",
            "json",
        ])
        .arg(path)
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .output()
        .map_err(|_| render_unavailable())?;
    if !output.status.success() {
        return Err(render_unavailable());
    }
    let document: serde_json::Value =
        serde_json::from_slice(&output.stdout).map_err(|_| render_unavailable())?;
    let stream = &document["streams"][0];
    let rate = stream["avg_frame_rate"]
        .as_str()
        .and_then(|value| value.split_once('/'))
        .and_then(|(numerator, denominator)| {
            let numerator: u32 = numerator.parse().ok()?;
            let denominator: u32 = denominator.parse().ok()?;
            numerator.checked_div(denominator)
        })
        .ok_or_else(render_unavailable)?;
    Ok(ProbedFilm {
        width: u32::try_from(stream["width"].as_u64().ok_or_else(render_unavailable)?)
            .map_err(|_| render_unavailable())?,
        height: u32::try_from(stream["height"].as_u64().ok_or_else(render_unavailable)?)
            .map_err(|_| render_unavailable())?,
        frames_per_second: rate,
        pixel_format: stream["pix_fmt"]
            .as_str()
            .ok_or_else(render_unavailable)?
            .to_owned(),
        frames: Some(
            stream["nb_read_frames"]
                .as_str()
                .and_then(|value| value.parse().ok())
                .ok_or_else(render_unavailable)?,
        ),
    })
}

/// The largest single captured frame this gate will read. A 1920x1080 PNG is
/// a few megabytes; anything past this is not a frame the encoder would accept
/// either, so refusing to buffer it is not a lost verdict.
const MAX_FRAME_READ_BYTES: u64 = 32 * 1024 * 1024;

/// Report whether every captured frame is byte-identical.
///
/// A render that captures the frames it was asked for is reported as a success
/// by the worker even when the composition never moved — and FFmpeg encodes
/// those frames into a well-formed MP4 of exactly the right length. The result
/// is a video file that is a still image, which every other check in this
/// module calls a completed render.
///
/// That is not hypothetical: a composition sized to a stage larger than the
/// capture viewport renders as the empty corner of itself, and several clips
/// stacked at `inset: 0` never take turns. Both produce this shape. Comparing
/// the frames is the only signal available here that separates them from a
/// film that genuinely holds one image, so a one-frame film is never called
/// static and the comparison stops at the first frame that differs.
pub fn rendered_film_is_static(
    frames_directory: &Path,
    frame_count: u32,
) -> Result<bool, MotionVideoStudioError> {
    if frame_count == 0 || frame_count > crate::local_video_orchestrator::SANDBOX_FRAMES_MAXIMUM {
        return Err(draft_invalid());
    }
    if frame_count == 1 {
        return Ok(false);
    }
    let first = frame_digest(frames_directory, 1)?;
    for index in 2..=frame_count {
        if frame_digest(frames_directory, index)? != first {
            return Ok(false);
        }
    }
    Ok(true)
}

fn frame_digest(frames_directory: &Path, index: u32) -> Result<String, MotionVideoStudioError> {
    let path = frames_directory.join(format!("frame-{index:05}.png"));
    let metadata = fs::symlink_metadata(&path).map_err(|_| storage_unavailable())?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_FRAME_READ_BYTES {
        return Err(storage_unavailable());
    }
    let mut file = File::open(&path).map_err(|_| storage_unavailable())?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; 128 * 1024];
    loop {
        let read = file.read(&mut buffer).map_err(|_| storage_unavailable())?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    let digest = hasher.finalize();
    let mut value = String::with_capacity(64);
    for byte in digest {
        use std::fmt::Write as _;
        write!(&mut value, "{byte:02x}").expect("writing to String cannot fail");
    }
    Ok(value)
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
        MotionRenderJobStatus::Rendering => RENDERING_PROGRESS.contains(&snapshot.progress_percent),
        MotionRenderJobStatus::Encoding => snapshot.progress_percent == 85,
        MotionRenderJobStatus::Succeeded => snapshot.progress_percent == 100,
        MotionRenderJobStatus::Cancelling
        | MotionRenderJobStatus::Failed
        | MotionRenderJobStatus::Cancelled => snapshot.progress_percent < 100,
    };
    let valid_shots = valid_shot_structure(&snapshot.shot_structure)
        && (snapshot.status != MotionRenderJobStatus::Succeeded
            || snapshot.shot_structure.is_empty()
            || snapshot
                .shot_structure
                .iter()
                .all(|shot| shot.rendered_frame_count.is_some()));
    if snapshot.render_job_id != workspace_id
        || snapshot.render_job_id.get_version_num() != 4
        || snapshot.revision == 0
        || validate_copy(&snapshot.subject, MAX_SUBJECT_CHARS).is_err()
        || snapshot.style_display_name.is_empty()
        || snapshot.style_display_name.chars().count() > 40
        || !valid_failure
        || !valid_artifact
        || !valid_progress
        || !valid_shots
    {
        return Err(job_unavailable());
    }
    Ok(())
}

fn valid_shot_structure(shots: &[MotionRenderShotSnapshot]) -> bool {
    // Checkpoints written before T2.2 deserialize to an empty table. They stay
    // readable; every newly prepared job writes a non-empty table.
    if shots.is_empty() {
        return true;
    }
    let rendered = shots[0].rendered_frame_count.is_some();
    let mut declared_start = 0_u32;
    let mut rendered_start = 0_u32;
    for (offset, shot) in shots.iter().enumerate() {
        let Ok(index) = u32::try_from(offset + 1) else {
            return false;
        };
        if shot.index != index
            || shot.start_frame != declared_start
            || shot.frame_count == 0
            || shot.part.as_ref().is_some_and(|part| {
                part.is_empty()
                    || part.len() > 80
                    || !part.bytes().all(|byte| {
                        byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-'
                    })
            })
            || shot.narration_seconds.is_some_and(|seconds| {
                !seconds.is_finite()
                    || seconds <= 0.0
                    || seconds
                        > f64::from(shot.frame_count) / f64::from(MOTION_FRAMES_PER_SECOND) + 0.5
            })
        {
            return false;
        }
        declared_start = match declared_start.checked_add(shot.frame_count) {
            Some(value) => value,
            None => return false,
        };
        match (shot.rendered_start_frame, shot.rendered_frame_count) {
            (Some(start), Some(frames)) if rendered && frames > 0 => {
                if start != rendered_start || start.abs_diff(shot.start_frame) > 1 {
                    return false;
                }
                rendered_start = match rendered_start.checked_add(frames) {
                    Some(value) => value,
                    None => return false,
                };
                if rendered_start.abs_diff(declared_start) > 1 {
                    return false;
                }
            }
            (None, None) if !rendered => {}
            _ => return false,
        }
    }
    true
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

pub const fn authoring_timed_out() -> MotionVideoStudioError {
    MotionVideoStudioError {
        code: MotionVideoStudioErrorCode::AuthoringTimedOut,
        retryable: true,
    }
}

pub const fn authoring_refused() -> MotionVideoStudioError {
    MotionVideoStudioError {
        code: MotionVideoStudioErrorCode::AuthoringRefused,
        retryable: true,
    }
}

pub const fn authoring_crashed() -> MotionVideoStudioError {
    MotionVideoStudioError {
        code: MotionVideoStudioErrorCode::AuthoringCrashed,
        retryable: true,
    }
}

/// Retryable: an outage or a wrong address can be over or corrected by the time
/// the user presses the button again, and this side cannot tell which it was.
pub const fn authoring_model_transport_failed() -> MotionVideoStudioError {
    MotionVideoStudioError {
        code: MotionVideoStudioErrorCode::AuthoringModelTransportFailed,
        retryable: true,
    }
}

pub const fn authoring_model_timed_out() -> MotionVideoStudioError {
    MotionVideoStudioError {
        code: MotionVideoStudioErrorCode::AuthoringModelTimedOut,
        retryable: true,
    }
}

/// Not retryable, and the only authoring failure that is not: the files this
/// run needs failed their digest check, and pressing the button again reads the
/// same files and fails the same way.
///
/// Nothing in the video studio branches on `retryable` today — the card carries
/// the same message either way and says in words that retrying will not help.
/// The flag is set correctly here so that whatever does start reading it finds
/// the truth rather than a value chosen to match the others.
pub const fn authoring_installation_damaged() -> MotionVideoStudioError {
    MotionVideoStudioError {
        code: MotionVideoStudioErrorCode::AuthoringInstallationDamaged,
        retryable: false,
    }
}

/// Retryable because the same brief may well be authored acceptably on a second
/// attempt — the answer we refused came out of a model, not out of the user.
pub const fn authoring_answer_invalid() -> MotionVideoStudioError {
    MotionVideoStudioError {
        code: MotionVideoStudioErrorCode::AuthoringAnswerInvalid,
        retryable: true,
    }
}

pub const fn configuration_required() -> MotionVideoStudioError {
    MotionVideoStudioError {
        code: MotionVideoStudioErrorCode::ConfigurationRequired,
        retryable: false,
    }
}

pub const fn storage_unavailable() -> MotionVideoStudioError {
    MotionVideoStudioError {
        code: MotionVideoStudioErrorCode::StorageUnavailable,
        retryable: false,
    }
}

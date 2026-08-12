use std::collections::HashSet;
use std::error::Error;
use std::fmt::{Display, Formatter};
use std::path::Path;
use std::sync::OnceLock;
use std::time::Duration;

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use regex::Regex;
use reqwest::header::{ACCEPT, AUTHORIZATION, CACHE_CONTROL, CONTENT_LENGTH, CONTENT_TYPE};
use reqwest::redirect::Policy;
use serde::{Deserialize, Serialize};
use time::format_description::well_known::Rfc3339;
use time::{Duration as TimeDuration, OffsetDateTime, UtcOffset};
use uuid::Variant;
use zeroize::Zeroizing;

use crate::deployment_profile::{DeploymentProfile, DeploymentProfileKind};
use crate::device_credentials::{
    DeviceCredentialErrorCode, DeviceCredentialVault, StoredDeviceCredential,
};
use crate::device_identity::ProductionDeviceIdentity;
use crate::runtime_compatibility::{
    CONTROL_PLANE_API_VERSION, CONTROL_PLANE_VERSION, DESKTOP_APP_VERSION,
    EXECUTOR_PROTOCOL_VERSION, EXECUTOR_RUNTIME_VERSION,
};
use crate::secure_store::SecretStore;

const DEFAULT_LOCAL_CONTROL_PLANE_ORIGIN: &str = "http://127.0.0.1:8765";
const REQUEST_ID_HEADER: &str = "x-request-id";
const IDEMPOTENCY_KEY_HEADER: &str = "idempotency-key";
const LAST_EVENT_ID_HEADER: &str = "last-event-id";
const BILIBILI_PUBLISH_SESSION_HEADER: &str = "x-bilibili-publish-session";
const MAX_RESPONSE_LENGTH: usize = 64 * 1024;
const MAX_SSE_RESPONSE_LENGTH: usize = 512 * 1024;
const MAX_SSE_FRAME_LENGTH: usize = 64 * 1024;
const MAX_CROSS_RUNTIME_SEQUENCE: u64 = (1_u64 << 53) - 1;
const DOUYIN_SEARCH_EXPOSURE_TEMPLATE: &str = "douyin.search_exposure.v1";
const MAX_SEARCH_KEYWORD_CHARACTERS: usize = 80;
const MAX_MESSAGE_TEMPLATE_CHARACTERS: usize = 500;
const TARGET_DISPLAY_NAME_VARIABLE: &str = "{{target_display_name}}";
const MAX_TASK_TARGET_LIMIT: u16 = 100;
const MAX_TASK_INTERVAL_SECONDS: u16 = 3600;
const MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS: usize = 80;
const MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS: usize = 64;

#[derive(Clone, Copy)]
enum ControlPlaneOperation {
    GetSystemHealth,
    GetSystemVersion,
    GetCurrentInstallationAccess,
    GetWorkbenchStatus,
    GetWorkbenchMetrics,
    GetDouyinPlatformSession,
    PrepareDouyinPlatformSessionLogout,
    IssueInstallationRegistrationChallenge,
    CompleteInstallationRegistration,
    RotateDeviceCredential,
    RevokeDeviceCredential,
    ExchangeDeviceSession,
    #[allow(dead_code)]
    FindEditingMaterialByDigest,
    #[cfg_attr(not(feature = "control-plane-e2e"), allow(dead_code))]
    RegisterEditingMaterial,
    #[allow(dead_code)]
    ListEditingMaterials,
    GetEditingMaterial,
    #[allow(dead_code)]
    DeleteEditingMaterial,
    #[allow(dead_code)]
    UpdateEditingMaterialDescription,
    ApplySmartEditMaterialWriteback,
    ListEditingProjects,
    CreateEditingProject,
    #[allow(dead_code)]
    GetEditingProject,
    GetEditingProjectTimeline,
    SaveEditingProjectTimeline,
    ListEditingJobs,
    SubmitEditingJob,
    #[allow(dead_code)]
    GetEditingJob,
    ReconcileEditingJob,
    CreateTask,
    StartTaskDiscovery,
    GetTaskTargetPreview,
    ReplaceTaskTargetExclusions,
    ConfirmTaskTargetPreview,
    ListTasks,
    GetTask,
    GetTaskTargetResults,
    StreamTaskEvents,
    PauseTask,
    ResumeTask,
    CancelTask,
    EmergencyStopTask,
    PrepareBilibiliPublish,
    UploadBilibiliPublishVideo,
    SubmitBilibiliPublish,
    CancelBilibiliPublishSession,
}

impl ControlPlaneOperation {
    fn max_response_length(self) -> usize {
        if matches!(self, Self::ApplySmartEditMaterialWriteback) {
            8 * 1024 * 1024
        } else {
            MAX_RESPONSE_LENGTH
        }
    }

    fn method(self) -> &'static str {
        match self {
            Self::GetSystemHealth
            | Self::GetSystemVersion
            | Self::GetCurrentInstallationAccess
            | Self::GetWorkbenchStatus
            | Self::GetWorkbenchMetrics
            | Self::GetDouyinPlatformSession
            | Self::FindEditingMaterialByDigest
            | Self::ListEditingMaterials
            | Self::GetEditingMaterial
            | Self::ListEditingProjects
            | Self::GetEditingProject
            | Self::GetEditingProjectTimeline
            | Self::ListEditingJobs
            | Self::GetEditingJob
            | Self::GetTaskTargetPreview
            | Self::ListTasks
            | Self::GetTask
            | Self::GetTaskTargetResults
            | Self::StreamTaskEvents => "GET",
            Self::IssueInstallationRegistrationChallenge
            | Self::CompleteInstallationRegistration
            | Self::RotateDeviceCredential
            | Self::RevokeDeviceCredential
            | Self::ExchangeDeviceSession
            | Self::PrepareDouyinPlatformSessionLogout
            | Self::RegisterEditingMaterial
            | Self::ApplySmartEditMaterialWriteback
            | Self::CreateEditingProject
            | Self::SubmitEditingJob
            | Self::CreateTask
            | Self::StartTaskDiscovery
            | Self::ConfirmTaskTargetPreview
            | Self::PauseTask
            | Self::ResumeTask
            | Self::CancelTask
            | Self::EmergencyStopTask
            | Self::PrepareBilibiliPublish
            | Self::SubmitBilibiliPublish => "POST",
            Self::ReconcileEditingJob => "PATCH",
            Self::UpdateEditingMaterialDescription
            | Self::SaveEditingProjectTimeline
            | Self::ReplaceTaskTargetExclusions
            | Self::UploadBilibiliPublishVideo => "PUT",
            Self::DeleteEditingMaterial
            | Self::CancelBilibiliPublishSession => "DELETE",
        }
    }

    fn path(self) -> &'static str {
        match self {
            Self::GetSystemHealth => "/api/v1/health",
            Self::GetSystemVersion => "/api/v1/version",
            Self::GetCurrentInstallationAccess => "/api/v1/installations/current",
            Self::GetWorkbenchStatus => "/api/v1/workbench/status",
            Self::GetWorkbenchMetrics => "/api/v1/workbench/metrics",
            Self::GetDouyinPlatformSession => "/api/v1/platform-sessions/douyin",
            Self::PrepareDouyinPlatformSessionLogout => {
                "/api/v1/platform-sessions/douyin/logout/prepare"
            }
            Self::IssueInstallationRegistrationChallenge => {
                "/api/v1/installations/registration-challenges"
            }
            Self::CompleteInstallationRegistration => "/api/v1/installations",
            Self::RotateDeviceCredential => "/api/v1/device-credentials/rotations",
            Self::RevokeDeviceCredential => "/api/v1/device-credentials/revocations",
            Self::ExchangeDeviceSession => "/api/v1/device-sessions",
            Self::FindEditingMaterialByDigest | Self::RegisterEditingMaterial => {
                "/api/v1/editing-materials"
            }
            Self::ListEditingMaterials => "/api/v1/editing-materials/library",
            Self::GetEditingMaterial | Self::DeleteEditingMaterial => {
                "/api/v1/editing-materials/{material_id}"
            }
            Self::UpdateEditingMaterialDescription => {
                "/api/v1/editing-materials/{material_id}/description"
            }
            Self::ApplySmartEditMaterialWriteback => {
                "/api/v1/editing-materials/smart-edit-writebacks"
            }
            Self::ListEditingProjects | Self::CreateEditingProject => "/api/v1/editing-projects",
            Self::GetEditingProject => "/api/v1/editing-projects/{project_id}",
            Self::GetEditingProjectTimeline | Self::SaveEditingProjectTimeline => {
                "/api/v1/editing-projects/{project_id}/timeline"
            }
            Self::ListEditingJobs | Self::SubmitEditingJob => {
                "/api/v1/editing-projects/{project_id}/jobs"
            }
            Self::GetEditingJob | Self::ReconcileEditingJob => "/api/v1/editing-jobs/{job_id}",
            Self::CreateTask => "/api/v1/tasks",
            Self::StartTaskDiscovery => "/api/v1/tasks/{task_id}/discoveries",
            Self::GetTaskTargetPreview => "/api/v1/tasks/{task_id}/target-preview",
            Self::ReplaceTaskTargetExclusions => {
                "/api/v1/tasks/{task_id}/target-preview/exclusions"
            }
            Self::ConfirmTaskTargetPreview => {
                "/api/v1/tasks/{task_id}/target-preview/confirmations"
            }
            Self::ListTasks => "/api/v1/tasks",
            Self::GetTask => "/api/v1/tasks/{task_id}",
            Self::GetTaskTargetResults => "/api/v1/tasks/{task_id}/target-results",
            Self::StreamTaskEvents => "/api/v1/tasks/{task_id}/events",
            Self::PauseTask => "/api/v1/tasks/{task_id}/pause",
            Self::ResumeTask => "/api/v1/tasks/{task_id}/resume",
            Self::CancelTask => "/api/v1/tasks/{task_id}/cancel",
            Self::EmergencyStopTask => "/api/v1/tasks/{task_id}/emergency-stop",
            Self::PrepareBilibiliPublish => "/api/v1/publishing/bilibili/jobs/{publish_job_id}",
            Self::UploadBilibiliPublishVideo => {
                "/api/v1/publishing/bilibili/jobs/{publish_job_id}/video"
            }
            Self::SubmitBilibiliPublish => {
                "/api/v1/publishing/bilibili/jobs/{publish_job_id}/submission"
            }
            Self::CancelBilibiliPublishSession => {
                "/api/v1/publishing/bilibili/jobs/{publish_job_id}/session"
            }
        }
    }

    fn success_status(self) -> u16 {
        match self {
            Self::GetSystemHealth
            | Self::GetSystemVersion
            | Self::GetCurrentInstallationAccess
            | Self::GetWorkbenchStatus
            | Self::GetWorkbenchMetrics
            | Self::GetDouyinPlatformSession
            | Self::FindEditingMaterialByDigest
            | Self::ListEditingMaterials
            | Self::GetEditingMaterial
            | Self::UpdateEditingMaterialDescription
            | Self::ApplySmartEditMaterialWriteback
            | Self::ListEditingProjects
            | Self::GetEditingProject
            | Self::GetEditingProjectTimeline
            | Self::ListEditingJobs
            | Self::GetEditingJob
            | Self::ReconcileEditingJob
            | Self::GetTaskTargetPreview
            | Self::ReplaceTaskTargetExclusions
            | Self::PrepareDouyinPlatformSessionLogout
            | Self::RevokeDeviceCredential
            | Self::ListTasks
            | Self::GetTask
            | Self::GetTaskTargetResults
            | Self::StreamTaskEvents => 200,
            Self::IssueInstallationRegistrationChallenge
            | Self::CompleteInstallationRegistration
            | Self::RotateDeviceCredential
            | Self::ExchangeDeviceSession
            | Self::RegisterEditingMaterial
            | Self::CreateEditingProject
            | Self::SaveEditingProjectTimeline
            | Self::SubmitEditingJob
            | Self::CreateTask => 201,
            Self::StartTaskDiscovery
            | Self::ConfirmTaskTargetPreview
            | Self::PauseTask
            | Self::ResumeTask
            | Self::CancelTask
            | Self::EmergencyStopTask => 202,
            Self::DeleteEditingMaterial
            | Self::CancelBilibiliPublishSession => 204,
            Self::PrepareBilibiliPublish => 201,
            Self::UploadBilibiliPublishVideo => 200,
            Self::SubmitBilibiliPublish => 202,
        }
    }

    fn accepts_status(self, status: u16) -> bool {
        status == self.success_status()
            || matches!(
                self,
                Self::CreateTask
                    | Self::StartTaskDiscovery
                    | Self::ConfirmTaskTargetPreview
                    | Self::PauseTask
                    | Self::ResumeTask
                    | Self::CancelTask
                    | Self::EmergencyStopTask
            ) && status == 200
    }

    fn outcome_is_uncertain_on_transport_failure(self) -> bool {
        matches!(
            self,
            Self::CompleteInstallationRegistration
                | Self::RotateDeviceCredential
                | Self::RevokeDeviceCredential
                | Self::ExchangeDeviceSession
                | Self::CreateEditingProject
                | Self::SaveEditingProjectTimeline
                | Self::SubmitEditingJob
                | Self::RegisterEditingMaterial
                | Self::ApplySmartEditMaterialWriteback
                | Self::DeleteEditingMaterial
                | Self::ReconcileEditingJob
                | Self::SubmitBilibiliPublish
        )
    }
}

#[derive(Clone, Copy)]
enum ControlPlaneRequestTarget<'a> {
    List {
        cursor: Option<&'a str>,
        limit: u16,
    },
    Detail(&'a str),
    EventStream {
        task_id: &'a str,
        last_event_id: Option<u64>,
    },
    Control(&'a str),
    PreviewList {
        task_id: &'a str,
        cursor: Option<&'a str>,
        limit: u16,
    },
    PreviewCommand(&'a str),
    AccountDevice {
        installation_id: &'a str,
        expected_revision: u32,
    },
    EditingProjectList {
        cursor: Option<&'a str>,
        limit: u16,
    },
    EditingTimeline(&'a str),
    EditingJobList {
        project_id: &'a str,
        cursor: Option<&'a str>,
        limit: u16,
    },
    EditingProjectJobs(&'a str),
    EditingMaterial(&'a str),
    EditingMaterialList {
        cursor: Option<&'a str>,
        limit: u16,
    },
    #[cfg_attr(
        all(feature = "desktop-e2e", not(feature = "control-plane-e2e")),
        allow(dead_code)
    )]
    EditingMaterialDigest(&'a str),
    EditingJob(&'a str),
    BilibiliPublishJob(&'a str),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ControlPlaneErrorCode {
    TransportUnavailable,
    ProtocolInvalid,
    RequestRejected,
    InstallationAccessDenied,
    InstallationBusy,
    InstallationConflict,
    CredentialMissing,
    IdentityUnavailable,
    StorageUnavailable,
    OutcomeUncertain,
    AuthenticationInvalid,
    RecoveryInvalid,
    AccountSessionInvalid,
    ResourceNotFound,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ControlPlaneError {
    code: ControlPlaneErrorCode,
    retryable: bool,
}

impl ControlPlaneError {
    fn new(code: ControlPlaneErrorCode, retryable: bool) -> Self {
        Self { code, retryable }
    }

    pub fn retryable(&self) -> bool {
        self.retryable
    }

    pub fn code(&self) -> ControlPlaneErrorCode {
        self.code
    }
}

impl Display for ControlPlaneError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("Control Plane request failed")
    }
}

impl Error for ControlPlaneError {}

#[derive(Clone)]
struct ResponseMetadata {
    status: u16,
    request_id: Option<String>,
    content_type: Option<String>,
    cache_control: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HealthResponse {
    service: String,
    status: String,
    version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VersionCompatibilityResponse {
    current: String,
    minimum_compatible: String,
    maximum_compatible: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SystemVersionResponse {
    service: String,
    version: String,
    api_version: String,
    desktop_app: VersionCompatibilityResponse,
    executor_runtime: VersionCompatibilityResponse,
    executor_protocol: VersionCompatibilityResponse,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct InstallationAccessResponse {
    installation_id: String,
    status: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AccountDevice {
    installation_id: String,
    status: String,
    revision: u32,
    created_at: String,
    updated_at: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum BilibiliPublishPhase {
    Prepared,
    VideoUploaded,
    Dispatched,
    Submitted,
    Failed,
    OutcomeUncertain,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct BilibiliPublishResponse {
    publish_job_id: String,
    phase: BilibiliPublishPhase,
    request_digest: String,
    resource_id: Option<String>,
    replayed: bool,
    session_token: Option<String>,
    credential_rotation: Option<crate::bilibili_service_settings::BilibiliCredentialRotation>,
}

pub struct BilibiliPublishControlResult {
    phase: BilibiliPublishPhase,
    resource_id: Option<String>,
    replayed: bool,
    session_token: Option<Zeroizing<String>>,
    credential_rotation: Option<crate::bilibili_service_settings::BilibiliCredentialRotation>,
}

impl std::fmt::Debug for BilibiliPublishControlResult {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("BilibiliPublishControlResult")
            .field("phase", &self.phase)
            .field("resource_id", &self.resource_id)
            .field("replayed", &self.replayed)
            .finish_non_exhaustive()
    }
}

impl BilibiliPublishControlResult {
    pub const fn phase(&self) -> BilibiliPublishPhase {
        self.phase
    }

    pub fn resource_id(&self) -> Option<&str> {
        self.resource_id.as_deref()
    }

    pub const fn replayed(&self) -> bool {
        self.replayed
    }

    pub fn session_token(&self) -> Option<&str> {
        self.session_token.as_deref().map(|value| value.as_str())
    }

    pub fn into_session_token(mut self) -> Option<Zeroizing<String>> {
        self.session_token.take()
    }

    pub fn credential_rotation(
        &self,
    ) -> Option<&crate::bilibili_service_settings::BilibiliCredentialRotation> {
        self.credential_rotation.as_ref()
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct BilibiliCredentialRequest<'a> {
    client_id: &'a str,
    app_secret: &'a str,
    access_token: &'a str,
    refresh_token: &'a str,
    expires_at_epoch_seconds: u64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct BilibiliMaterialRequest<'a> {
    size_bytes: u64,
    duration_seconds: u32,
    sha256: &'a str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct BilibiliArchiveRequest<'a> {
    title: &'a str,
    tid: u32,
    tag: &'a str,
    description: &'a str,
    no_reprint: u8,
}

#[derive(Serialize)]
struct PrepareBilibiliPublishRequest<'a> {
    credential: BilibiliCredentialRequest<'a>,
    material: BilibiliMaterialRequest<'a>,
    archive: BilibiliArchiveRequest<'a>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ControlPlaneHealth {
    status: &'static str,
    service_version: String,
}

const MAX_EDITING_TIMELINE_DURATION_MS: u64 = 600_000;
const MAX_EDITING_MATERIAL_DURATION_MS: u64 = 14_400_000;
const MAX_EDITING_CLIPS_PER_TRACK: usize = 512;

fn deserialize_required_nullable<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EditingOutputSpec {
    width: u16,
    height: u16,
    fps: u8,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EditingCaptionStyle {
    font_key: String,
    font_px: u16,
    stroke_px: u8,
    line_spacing: f64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EditingProjectCreateRequest {
    title: String,
    output: EditingOutputSpec,
    caption_style: EditingCaptionStyle,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EditingProjectSnapshot {
    project_id: String,
    title: String,
    output: EditingOutputSpec,
    caption_style: EditingCaptionStyle,
    created_at: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct EditingProjectListResponse {
    items: Vec<EditingProjectSnapshot>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    next_cursor: Option<String>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EditingProjectListPage {
    items: Vec<EditingProjectSnapshot>,
    next_cursor: Option<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum EditingMaterialKind {
    Image,
    Video,
    Audio,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EditingMaterialSnapshot {
    material_id: String,
    kind: EditingMaterialKind,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    duration_ms: Option<u64>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    width: Option<u16>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    height: Option<u16>,
    content_digest: String,
    has_audio: bool,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    audio_loudness_lufs: Option<f64>,
    has_speech: bool,
    speech_segments_ms: Vec<(u64, u64)>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    speech_transcript: Option<String>,
    shot_boundaries_ms: Vec<u64>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    ai_description: Option<String>,
    ai_tags: Vec<String>,
    description_source: String,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    described_at: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct EditingMaterialListResponse {
    items: Vec<EditingMaterialSnapshot>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    next_cursor: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EditingMaterialListPage {
    items: Vec<EditingMaterialSnapshot>,
    next_cursor: Option<String>,
}

#[derive(Serialize)]
struct EditingMaterialUserDescriptionRequest<'a> {
    source: &'static str,
    description: &'a str,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct SmartEditMaterialAnalysisRequest {
    pub(crate) material_id: String,
    pub(crate) content_digest: String,
    pub(crate) has_speech: bool,
    pub(crate) speech_segments_ms: Vec<(u64, u64)>,
    pub(crate) speech_transcript: Option<String>,
    pub(crate) shot_boundaries_ms: Vec<u64>,
    pub(crate) ai_description: Option<String>,
    pub(crate) ai_tags: Vec<String>,
    pub(crate) description_source: String,
    pub(crate) described_at: Option<String>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct SmartEditNarrationMaterialRequest {
    pub(crate) material_id: String,
    pub(crate) content_digest: String,
    pub(crate) duration_ms: u64,
    pub(crate) speech_transcript: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct SmartEditMaterialWritebackRequest {
    pub(crate) analyses: Vec<SmartEditMaterialAnalysisRequest>,
    pub(crate) narrations: Vec<SmartEditNarrationMaterialRequest>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SmartEditMaterialWritebackResponse {
    materials: Vec<EditingMaterialSnapshot>,
}

#[derive(Clone, Debug, Serialize)]
#[cfg_attr(
    all(feature = "desktop-e2e", not(feature = "control-plane-e2e")),
    allow(dead_code)
)]
#[serde(rename_all = "camelCase")]
pub(crate) struct EditingMaterialRegistrationRequest {
    material_id: String,
    kind: EditingMaterialKind,
    duration_ms: Option<u64>,
    width: Option<u16>,
    height: Option<u16>,
    content_digest: String,
    has_audio: bool,
    audio_loudness_lufs: Option<f64>,
    has_speech: bool,
    speech_segments_ms: Vec<(u64, u64)>,
    speech_transcript: Option<String>,
    shot_boundaries_ms: Vec<u64>,
    ai_description: Option<String>,
    ai_tags: Vec<String>,
    description_source: &'static str,
    described_at: Option<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
enum EditingTrackKind {
    Visual,
    Narration,
    Ambient,
    Music,
    Caption,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
enum EditingOriginalAudioMode {
    AutoDuck,
    FixedVolume,
    Muted,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
enum EditingTransitionKind {
    Fade,
    Dissolve,
    Wipe,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct EditingTimelineTransition {
    kind: EditingTransitionKind,
    duration_ms: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct EditingTimelineClip {
    clip_id: String,
    start_ms: u64,
    duration_ms: u64,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    source_material_id: Option<String>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    source_in_ms: Option<u64>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    source_out_ms: Option<u64>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    text: Option<String>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    gain_db: Option<f64>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    transition_in: Option<EditingTimelineTransition>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    original_audio_mode: Option<EditingOriginalAudioMode>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct EditingTimelineTrack {
    track_id: String,
    kind: EditingTrackKind,
    clips: Vec<EditingTimelineClip>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EditingTimelineDraft {
    duration_ms: u64,
    tracks: Vec<EditingTimelineTrack>,
    #[serde(skip_serializing_if = "Option::is_none")]
    expected_revision: Option<u64>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EditingTimelineSnapshot {
    timeline_id: String,
    project_id: String,
    revision: u64,
    duration_ms: u64,
    tracks: Vec<EditingTimelineTrack>,
    created_at: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum EditingJobStatus {
    Queued,
    Running,
    Cancelling,
    Succeeded,
    Failed,
    Cancelled,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum EditingJobFailureCode {
    InvalidTimeline,
    MaterialUnavailable,
    MaterialUnsupported,
    FontUnavailable,
    RenderFailed,
    ResourceExhausted,
    PermissionDenied,
    WorkerLost,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EditingJobSnapshot {
    job_id: String,
    project_id: String,
    timeline_id: String,
    timeline_revision: u64,
    status: EditingJobStatus,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    failure_code: Option<EditingJobFailureCode>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    output_artifact_id: Option<String>,
    created_at: String,
    updated_at: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct EditingJobListResponse {
    items: Vec<EditingJobSnapshot>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    next_cursor: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EditingJobListPage {
    items: Vec<EditingJobSnapshot>,
    next_cursor: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct EditingJobReconcileRequest<'a> {
    expected_updated_at: &'a str,
    status: EditingJobStatus,
    failure_code: Option<EditingJobFailureCode>,
    output_artifact_id: Option<&'a str>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DouyinSearchExposureAction {
    Browse,
    Comment,
    DirectMessage,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct DouyinSearchExposureTaskDefinition {
    template: String,
    search_keyword: String,
    action: DouyinSearchExposureAction,
    message_template: Option<String>,
    target_limit: u16,
    minimum_interval_seconds: u16,
    maximum_interval_seconds: u16,
    preview_required: bool,
    final_confirmation_required: bool,
}

impl DouyinSearchExposureTaskDefinition {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        search_keyword: String,
        action: DouyinSearchExposureAction,
        message_template: Option<String>,
        target_limit: u16,
        minimum_interval_seconds: u16,
        maximum_interval_seconds: u16,
    ) -> Result<Self, ControlPlaneError> {
        let definition = Self {
            template: DOUYIN_SEARCH_EXPOSURE_TEMPLATE.to_owned(),
            search_keyword,
            action,
            message_template,
            target_limit,
            minimum_interval_seconds,
            maximum_interval_seconds,
            preview_required: true,
            final_confirmation_required: true,
        };
        definition.validate()?;
        Ok(definition)
    }

    fn validate(&self) -> Result<(), ControlPlaneError> {
        require_safe_exact_text(&self.search_keyword, MAX_SEARCH_KEYWORD_CHARACTERS)?;
        match (self.action, self.message_template.as_deref()) {
            (DouyinSearchExposureAction::Browse, None) => {}
            (DouyinSearchExposureAction::Comment, Some(message))
            | (DouyinSearchExposureAction::DirectMessage, Some(message)) => {
                require_action_message_template(message)?;
            }
            _ => return Err(protocol_invalid()),
        }
        if self.template != DOUYIN_SEARCH_EXPOSURE_TEMPLATE
            || self.target_limit == 0
            || self.target_limit > MAX_TASK_TARGET_LIMIT
            || self.minimum_interval_seconds == 0
            || self.minimum_interval_seconds > self.maximum_interval_seconds
            || self.maximum_interval_seconds > MAX_TASK_INTERVAL_SECONDS
            || !self.preview_required
            || !self.final_confirmation_required
        {
            return Err(protocol_invalid());
        }
        Ok(())
    }
}

impl ControlPlaneHealth {
    #[cfg(test)]
    fn service_version(&self) -> &str {
        &self.service_version
    }
}

pub struct ControlPlaneClient {
    client: reqwest::Client,
    origin: String,
    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    executor_websocket_url: String,
}

#[cfg(feature = "control-plane-e2e")]
fn configured_local_control_plane_origin() -> &'static str {
    option_env!("AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN")
        .unwrap_or(DEFAULT_LOCAL_CONTROL_PLANE_ORIGIN)
}

#[cfg(not(feature = "control-plane-e2e"))]
const fn configured_local_control_plane_origin() -> &'static str {
    DEFAULT_LOCAL_CONTROL_PLANE_ORIGIN
}

fn validated_loopback_origin(source: &str) -> Result<(String, String), ControlPlaneError> {
    let parsed = reqwest::Url::parse(source).map_err(|_| protocol_invalid())?;
    let port = parsed.port().ok_or_else(protocol_invalid)?;
    let canonical = format!("http://127.0.0.1:{port}");
    if parsed.scheme() != "http"
        || parsed.host_str() != Some("127.0.0.1")
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.path() != "/"
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || source != canonical
    {
        return Err(protocol_invalid());
    }
    Ok((
        canonical,
        format!("ws://127.0.0.1:{port}/api/v1/executors/connect"),
    ))
}

fn validated_demo_origin(
    source: &str,
    allowed_hosts: &[String],
) -> Result<(String, String), ControlPlaneError> {
    let parsed = reqwest::Url::parse(source).map_err(|_| protocol_invalid())?;
    let host = parsed.host_str().ok_or_else(protocol_invalid)?;
    let canonical = format!("https://{host}");
    if parsed.scheme() != "https"
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.port().is_some()
        || parsed.path() != "/"
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || source != canonical
        || !allowed_hosts.iter().any(|allowed| allowed == host)
    {
        return Err(protocol_invalid());
    }
    Ok((canonical, format!("wss://{host}/api/v1/executors/connect")))
}

impl ControlPlaneClient {
    pub fn local() -> Result<Self, ControlPlaneError> {
        let configured = validated_loopback_origin(configured_local_control_plane_origin())?;
        Self::from_validated_origins(configured)
    }

    pub fn for_deployment_profile(profile: &DeploymentProfile) -> Result<Self, ControlPlaneError> {
        let configured = match profile.kind() {
            DeploymentProfileKind::Local => {
                validated_loopback_origin(configured_local_control_plane_origin())?
            }
            DeploymentProfileKind::Demo => {
                validated_demo_origin(profile.base_url(), profile.allowed_hosts())?
            }
        };
        Self::from_validated_origins(configured)
    }

    fn from_validated_origins(configured: (String, String)) -> Result<Self, ControlPlaneError> {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(3))
            .timeout(Duration::from_secs(10))
            .redirect(Policy::none())
            .no_proxy()
            .user_agent("automation-tool-desktop/0.1")
            .build()
            .map_err(|_| {
                ControlPlaneError::new(ControlPlaneErrorCode::TransportUnavailable, true)
            })?;
        Ok(Self {
            client,
            origin: configured.0,
            #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
            executor_websocket_url: configured.1,
        })
    }

    pub async fn check_health(&self) -> Result<ControlPlaneHealth, ControlPlaneError> {
        let health_body = self
            .execute(
                ControlPlaneOperation::GetSystemHealth,
                None,
                None,
                None,
                None,
            )
            .await?;
        let health = parse_health_response(&health_body)?;
        let version_body = self
            .execute(
                ControlPlaneOperation::GetSystemVersion,
                None,
                None,
                None,
                None,
            )
            .await?;
        parse_system_version_response(&version_body, &health.service_version)?;
        Ok(health)
    }

    pub async fn check_installation_access_if_registered<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
    ) -> Result<(), ControlPlaneError>
    where
        S: SecretStore,
    {
        if vault.load().map_err(map_vault_error)?.is_none() {
            return Ok(());
        }
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let body = self
            .execute(
                ControlPlaneOperation::GetCurrentInstallationAccess,
                Some(session.token()),
                None,
                None,
                None,
            )
            .await?;
        parse_installation_access(&body).map(|_| ())
    }

    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    pub(crate) async fn issue_executor_connection<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
    ) -> Result<ExecutorConnectionMaterial, ControlPlaneError>
    where
        S: SecretStore,
    {
        let app_session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let access_body = self
            .execute(
                ControlPlaneOperation::GetCurrentInstallationAccess,
                Some(app_session.token()),
                None,
                None,
                None,
            )
            .await?;
        let installation_id = parse_installation_access(&access_body)?;
        let executor_session = self
            .exchange_device_session(vault, DeviceSessionCapability::ExecutorConnect)
            .await?;
        Ok(ExecutorConnectionMaterial {
            websocket_url: self.executor_websocket_url.clone(),
            session_token: executor_session.into_token(),
            installation_id,
        })
    }

    pub async fn register_installation<S>(
        &self,
        bootstrap: &DemoBootstrap,
        identity: &ProductionDeviceIdentity,
        vault: &DeviceCredentialVault<S>,
    ) -> Result<InstallationRegistration, ControlPlaneError>
    where
        S: SecretStore,
    {
        if vault.load().map_err(map_vault_error)?.is_some() {
            return Err(ControlPlaneError::new(
                ControlPlaneErrorCode::RequestRejected,
                false,
            ));
        }
        let challenge_request = serde_json::to_value(RegistrationChallengeRequest {
            environment_id: bootstrap.environment_id(),
            device_public_key: URL_SAFE_NO_PAD.encode(identity.public_key()),
        })
        .map_err(|_| ControlPlaneError::new(ControlPlaneErrorCode::ProtocolInvalid, false))?;
        let challenge_body = self
            .execute(
                ControlPlaneOperation::IssueInstallationRegistrationChallenge,
                Some(bootstrap.token()),
                Some(&challenge_request),
                None,
                None,
            )
            .await?;
        let challenge = parse_registration_challenge(&challenge_body)?;
        let signing_payload = decode_canonical_base64url(&challenge.signing_payload, 1, 2048)?;
        let signature = identity.sign(&signing_payload).map_err(|_| {
            ControlPlaneError::new(ControlPlaneErrorCode::IdentityUnavailable, false)
        })?;
        let completion_request = serde_json::to_value(InstallationRegistrationRequest {
            challenge_id: challenge.challenge_id,
            environment_id: bootstrap.environment_id(),
            signing_payload: challenge.signing_payload,
            signature: URL_SAFE_NO_PAD.encode(signature),
        })
        .map_err(|_| ControlPlaneError::new(ControlPlaneErrorCode::ProtocolInvalid, false))?;
        let registration_body = self
            .execute(
                ControlPlaneOperation::CompleteInstallationRegistration,
                Some(bootstrap.token()),
                Some(&completion_request),
                None,
                None,
            )
            .await?;
        let registered = parse_installation_registration(&registration_body)?;
        let credential = Zeroizing::new(registered.device_credential.credential);
        vault
            .replace(&credential)
            .map_err(|error| match error.code() {
                DeviceCredentialErrorCode::InvalidCredential => {
                    ControlPlaneError::new(ControlPlaneErrorCode::ProtocolInvalid, false)
                }
                DeviceCredentialErrorCode::SecureStoreUnavailable
                | DeviceCredentialErrorCode::CorruptStoredCredential => {
                    ControlPlaneError::new(ControlPlaneErrorCode::OutcomeUncertain, false)
                }
            })?;
        Ok(InstallationRegistration {
            installation_id: registered.installation_id,
            credential_version: registered.device_credential.version,
        })
    }

    pub async fn rotate_device_credential<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
    ) -> Result<u32, ControlPlaneError>
    where
        S: SecretStore,
    {
        let credential = required_credential(vault)?;
        let response_body = self
            .execute(
                ControlPlaneOperation::RotateDeviceCredential,
                Some(credential.as_str()),
                None,
                None,
                None,
            )
            .await?;
        let rotated = parse_rotated_credential(&response_body)?;
        let replacement = Zeroizing::new(rotated.credential);
        vault
            .replace(&replacement)
            .map_err(|error| match error.code() {
                DeviceCredentialErrorCode::InvalidCredential => {
                    ControlPlaneError::new(ControlPlaneErrorCode::ProtocolInvalid, false)
                }
                DeviceCredentialErrorCode::SecureStoreUnavailable
                | DeviceCredentialErrorCode::CorruptStoredCredential => {
                    ControlPlaneError::new(ControlPlaneErrorCode::OutcomeUncertain, false)
                }
            })?;
        Ok(rotated.version)
    }

    pub async fn exchange_device_session<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        capability: DeviceSessionCapability,
    ) -> Result<DeviceSession, ControlPlaneError>
    where
        S: SecretStore,
    {
        // 设备身份机制已删除：本地部署不再向服务端交换设备会话，
        // 这里只需要一个形状合法的占位 bearer，服务端不校验其内容。
        let _ = vault;
        Ok(DeviceSession {
            token: Zeroizing::new(format!("atds1.{}", "A".repeat(43))),
            capability,
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn prepare_bilibili_publish<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        publish_job_id: &str,
        size_bytes: u64,
        duration_seconds: u32,
        sha256: &str,
        title: &str,
        description: &str,
        credential: &crate::bilibili_service_settings::BilibiliServiceCredential,
    ) -> Result<BilibiliPublishControlResult, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(publish_job_id)?;
        if size_bytes == 0
            || duration_seconds == 0
            || !is_lower_hex_digest(sha256)
            || require_safe_exact_text(title, 80).is_err()
            || require_safe_exact_text(description, 250).is_err()
        {
            return Err(protocol_invalid());
        }
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let request = PrepareBilibiliPublishRequest {
            credential: BilibiliCredentialRequest {
                client_id: credential.client_id(),
                app_secret: credential.app_secret(),
                access_token: credential.access_token(),
                refresh_token: credential.refresh_token(),
                expires_at_epoch_seconds: credential.expires_at_epoch_seconds(),
            },
            material: BilibiliMaterialRequest {
                size_bytes,
                duration_seconds,
                sha256,
            },
            archive: BilibiliArchiveRequest {
                title,
                tid: credential.tid(),
                tag: credential.tag(),
                description,
                no_reprint: credential.no_reprint(),
            },
        };
        let payload = Zeroizing::new(serde_json::to_vec(&request).map_err(|_| protocol_invalid())?);
        let response = self
            .execute_bilibili_json(
                ControlPlaneOperation::PrepareBilibiliPublish,
                session.token(),
                publish_job_id,
                None,
                Some(payload.as_slice()),
            )
            .await?;
        let result = parse_bilibili_publish_response(&response, publish_job_id)?;
        if result.phase() != BilibiliPublishPhase::Prepared || result.session_token().is_none() {
            return Err(protocol_invalid());
        }
        Ok(result)
    }

    pub async fn upload_bilibili_publish_video<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        publish_job_id: &str,
        publish_session: &str,
        path: &Path,
        expected_size_bytes: u64,
    ) -> Result<BilibiliPublishControlResult, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(publish_job_id)?;
        require_bilibili_publish_session(publish_session)?;
        if expected_size_bytes == 0 {
            return Err(protocol_invalid());
        }
        let metadata = tokio::fs::metadata(path).await.map_err(|_| {
            ControlPlaneError::new(ControlPlaneErrorCode::StorageUnavailable, false)
        })?;
        if !metadata.is_file() || metadata.len() != expected_size_bytes {
            return Err(ControlPlaneError::new(
                ControlPlaneErrorCode::StorageUnavailable,
                false,
            ));
        }
        let file = tokio::fs::File::open(path).await.map_err(|_| {
            ControlPlaneError::new(ControlPlaneErrorCode::StorageUnavailable, false)
        })?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let request_id = new_request_id()?;
        let request_path = request_path(
            ControlPlaneOperation::UploadBilibiliPublishVideo,
            Some(ControlPlaneRequestTarget::BilibiliPublishJob(
                publish_job_id,
            )),
        )?;
        let request = self
            .client
            .put(format!("{}{request_path}", self.origin))
            .timeout(Duration::from_secs(2 * 60 * 60))
            .header(ACCEPT, "application/json")
            .header(CONTENT_TYPE, "video/mp4")
            .header(CONTENT_LENGTH, expected_size_bytes.to_string())
            .header(REQUEST_ID_HEADER, &request_id)
            .header(AUTHORIZATION, format!("Bearer {}", session.token()))
            .header(BILIBILI_PUBLISH_SESSION_HEADER, publish_session)
            .body(reqwest::Body::wrap_stream(
                tokio_util::io::ReaderStream::new(file),
            ));
        let response = self
            .send_bilibili_request(
                ControlPlaneOperation::UploadBilibiliPublishVideo,
                &request_id,
                request,
            )
            .await?;
        let result = parse_bilibili_publish_response(&response, publish_job_id)?;
        if result.phase() != BilibiliPublishPhase::VideoUploaded || result.session_token().is_some()
        {
            return Err(protocol_invalid());
        }
        Ok(result)
    }

    pub async fn submit_bilibili_publish<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        publish_job_id: &str,
        publish_session: &str,
    ) -> Result<BilibiliPublishControlResult, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(publish_job_id)?;
        require_bilibili_publish_session(publish_session)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response = self
            .execute_bilibili_json(
                ControlPlaneOperation::SubmitBilibiliPublish,
                session.token(),
                publish_job_id,
                Some(publish_session),
                None,
            )
            .await?;
        let result = parse_bilibili_publish_response(&response, publish_job_id)?;
        if result.session_token().is_some()
            || !matches!(
                result.phase(),
                BilibiliPublishPhase::Dispatched
                    | BilibiliPublishPhase::Submitted
                    | BilibiliPublishPhase::Failed
                    | BilibiliPublishPhase::OutcomeUncertain
            )
        {
            return Err(protocol_invalid());
        }
        Ok(result)
    }

    pub async fn cancel_bilibili_publish_session<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        publish_job_id: &str,
        publish_session: &str,
    ) -> Result<(), ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(publish_job_id)?;
        require_bilibili_publish_session(publish_session)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response = self
            .execute_bilibili_json(
                ControlPlaneOperation::CancelBilibiliPublishSession,
                session.token(),
                publish_job_id,
                Some(publish_session),
                None,
            )
            .await?;
        require_empty_response(response.as_slice())
    }

    pub async fn revoke_device_credential<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
    ) -> Result<u32, ControlPlaneError>
    where
        S: SecretStore,
    {
        let credential = required_credential(vault)?;
        let response_body = self
            .execute(
                ControlPlaneOperation::RevokeDeviceCredential,
                Some(credential.as_str()),
                None,
                None,
                None,
            )
            .await?;
        let revoked = parse_revoked_credential(&response_body)?;
        vault
            .delete()
            .map_err(|_| ControlPlaneError::new(ControlPlaneErrorCode::OutcomeUncertain, false))?;
        Ok(revoked.version)
    }

    pub async fn list_editing_projects<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        cursor: Option<&str>,
        limit: u16,
    ) -> Result<EditingProjectListPage, ControlPlaneError>
    where
        S: SecretStore,
    {
        validate_editing_page(cursor, limit)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let body = self
            .execute(
                ControlPlaneOperation::ListEditingProjects,
                Some(session.token()),
                None,
                None,
                Some(ControlPlaneRequestTarget::EditingProjectList { cursor, limit }),
            )
            .await?;
        parse_editing_project_list(&body)
    }

    #[cfg_attr(
        all(feature = "desktop-e2e", not(feature = "control-plane-e2e")),
        allow(dead_code)
    )]
    pub(crate) async fn find_editing_material_by_digest<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        content_digest: &str,
    ) -> Result<Option<EditingMaterialSnapshot>, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_content_digest(content_digest)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response = self
            .execute(
                ControlPlaneOperation::FindEditingMaterialByDigest,
                Some(session.token()),
                None,
                None,
                Some(ControlPlaneRequestTarget::EditingMaterialDigest(
                    content_digest,
                )),
            )
            .await;
        match response {
            Ok(body) => {
                let material: EditingMaterialSnapshot = parse_exact_json(&body)?;
                material.validate()?;
                if material.content_digest != content_digest {
                    return Err(protocol_invalid());
                }
                Ok(Some(material))
            }
            Err(error) if error.code() == ControlPlaneErrorCode::ResourceNotFound => Ok(None),
            Err(error) => Err(error),
        }
    }

    pub async fn list_editing_materials<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        cursor: Option<&str>,
        limit: u16,
    ) -> Result<EditingMaterialListPage, ControlPlaneError>
    where
        S: SecretStore,
    {
        validate_editing_page(cursor, limit)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let body = self
            .execute(
                ControlPlaneOperation::ListEditingMaterials,
                Some(session.token()),
                None,
                None,
                Some(ControlPlaneRequestTarget::EditingMaterialList { cursor, limit }),
            )
            .await?;
        parse_editing_material_list(&body)
    }

    #[cfg_attr(
        all(feature = "desktop-e2e", not(feature = "control-plane-e2e")),
        allow(dead_code)
    )]
    pub(crate) async fn register_editing_material<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        request: &EditingMaterialRegistrationRequest,
    ) -> Result<EditingMaterialSnapshot, ControlPlaneError>
    where
        S: SecretStore,
    {
        request.validate()?;
        let body = serde_json::to_value(request).map_err(|_| protocol_invalid())?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response = self
            .execute(
                ControlPlaneOperation::RegisterEditingMaterial,
                Some(session.token()),
                Some(&body),
                None,
                None,
            )
            .await?;
        let material: EditingMaterialSnapshot = parse_exact_json(&response)?;
        material.validate()?;
        if material.material_id != request.material_id
            || material.kind != request.kind
            || material.duration_ms != request.duration_ms
            || material.width != request.width
            || material.height != request.height
            || material.content_digest != request.content_digest
            || material.has_audio != request.has_audio
            || material.audio_loudness_lufs != request.audio_loudness_lufs
            || material.has_speech
            || !material.speech_segments_ms.is_empty()
            || material.speech_transcript.is_some()
            || !material.shot_boundaries_ms.is_empty()
            || material.ai_description.is_some()
            || !material.ai_tags.is_empty()
            || material.description_source != "ai"
            || material.described_at.is_some()
        {
            return Err(protocol_invalid());
        }
        Ok(material)
    }

    pub(crate) async fn delete_editing_material<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        material_id: &str,
    ) -> Result<(), ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(material_id)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response = self
            .execute(
                ControlPlaneOperation::DeleteEditingMaterial,
                Some(session.token()),
                None,
                None,
                Some(ControlPlaneRequestTarget::EditingMaterial(material_id)),
            )
            .await?;
        require_empty_response(&response)
    }

    pub(crate) async fn get_editing_material<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        material_id: &str,
    ) -> Result<EditingMaterialSnapshot, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(material_id)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let body = self
            .execute(
                ControlPlaneOperation::GetEditingMaterial,
                Some(session.token()),
                None,
                None,
                Some(ControlPlaneRequestTarget::EditingMaterial(material_id)),
            )
            .await?;
        let material: EditingMaterialSnapshot = parse_exact_json(&body)?;
        material.validate()?;
        if material.material_id != material_id {
            return Err(protocol_invalid());
        }
        Ok(material)
    }

    pub async fn update_editing_material_description<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        material_id: &str,
        description: &str,
    ) -> Result<EditingMaterialSnapshot, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(material_id)?;
        validate_editing_material_description(description)?;
        let request = EditingMaterialUserDescriptionRequest {
            source: "user",
            description,
        };
        let body = serde_json::to_value(request).map_err(|_| protocol_invalid())?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response = self
            .execute(
                ControlPlaneOperation::UpdateEditingMaterialDescription,
                Some(session.token()),
                Some(&body),
                None,
                Some(ControlPlaneRequestTarget::EditingMaterial(material_id)),
            )
            .await?;
        let material: EditingMaterialSnapshot = parse_exact_json(&response)?;
        material.validate()?;
        if material.material_id != material_id
            || material.description_source != "user"
            || material.ai_description.as_deref() != Some(description)
            || !material.ai_tags.is_empty()
            || material.described_at.is_some()
        {
            return Err(protocol_invalid());
        }
        Ok(material)
    }

    pub(crate) async fn apply_smart_edit_material_writeback<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        request: &SmartEditMaterialWritebackRequest,
    ) -> Result<Vec<EditingMaterialSnapshot>, ControlPlaneError>
    where
        S: SecretStore,
    {
        request.validate()?;
        let expected = request.expected_materials();
        let body = serde_json::to_value(request).map_err(|_| protocol_invalid())?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response = self
            .execute(
                ControlPlaneOperation::ApplySmartEditMaterialWriteback,
                Some(session.token()),
                Some(&body),
                None,
                None,
            )
            .await?;
        let parsed: SmartEditMaterialWritebackResponse = parse_exact_json(&response)?;
        if parsed.materials.len() != expected.len() {
            return Err(protocol_invalid());
        }
        for (material, (expected_id, expected_digest)) in parsed.materials.iter().zip(expected) {
            material.validate()?;
            if material.material_id != expected_id || material.content_digest != expected_digest {
                return Err(protocol_invalid());
            }
        }
        Ok(parsed.materials)
    }

    #[cfg(feature = "control-plane-e2e")]
    pub(crate) async fn register_editing_video_material_for_acceptance<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        material_id: &str,
        content_digest: &str,
    ) -> Result<EditingMaterialSnapshot, ControlPlaneError>
    where
        S: SecretStore,
    {
        let request = EditingMaterialRegistrationRequest::new(
            material_id,
            EditingMaterialKind::Video,
            Some(1_000),
            Some(1_280),
            Some(720),
            content_digest,
            false,
            None,
        )?;
        self.register_editing_material(vault, &request).await
    }

    pub async fn create_editing_project<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        request: &EditingProjectCreateRequest,
    ) -> Result<EditingProjectSnapshot, ControlPlaneError>
    where
        S: SecretStore,
    {
        request.validate()?;
        let body = serde_json::to_value(request).map_err(|_| protocol_invalid())?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response = self
            .execute(
                ControlPlaneOperation::CreateEditingProject,
                Some(session.token()),
                Some(&body),
                None,
                None,
            )
            .await?;
        parse_editing_project(&response)
    }

    pub async fn get_editing_project_timeline<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        project_id: &str,
    ) -> Result<Option<EditingTimelineSnapshot>, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(project_id)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response = self
            .execute(
                ControlPlaneOperation::GetEditingProjectTimeline,
                Some(session.token()),
                None,
                None,
                Some(ControlPlaneRequestTarget::EditingTimeline(project_id)),
            )
            .await;
        match response {
            Ok(body) => {
                let timeline = parse_editing_timeline(&body)?;
                if timeline.project_id != project_id {
                    return Err(protocol_invalid());
                }
                Ok(Some(timeline))
            }
            Err(error) if error.code() == ControlPlaneErrorCode::ResourceNotFound => Ok(None),
            Err(error) => Err(error),
        }
    }

    pub async fn save_editing_project_timeline<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        project_id: &str,
        draft: &EditingTimelineDraft,
    ) -> Result<EditingTimelineSnapshot, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(project_id)?;
        draft.validate()?;
        let body = serde_json::to_value(draft).map_err(|_| protocol_invalid())?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response = self
            .execute(
                ControlPlaneOperation::SaveEditingProjectTimeline,
                Some(session.token()),
                Some(&body),
                None,
                Some(ControlPlaneRequestTarget::EditingTimeline(project_id)),
            )
            .await?;
        let timeline = parse_editing_timeline(&response)?;
        if timeline.project_id != project_id {
            return Err(protocol_invalid());
        }
        Ok(timeline)
    }

    pub async fn list_editing_jobs<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        project_id: &str,
        cursor: Option<&str>,
        limit: u16,
    ) -> Result<EditingJobListPage, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(project_id)?;
        validate_editing_page(cursor, limit)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let body = self
            .execute(
                ControlPlaneOperation::ListEditingJobs,
                Some(session.token()),
                None,
                None,
                Some(ControlPlaneRequestTarget::EditingJobList {
                    project_id,
                    cursor,
                    limit,
                }),
            )
            .await?;
        let page = parse_editing_job_list(&body)?;
        if page.items.iter().any(|job| job.project_id != project_id) {
            return Err(protocol_invalid());
        }
        Ok(page)
    }

    pub async fn submit_editing_job<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        project_id: &str,
    ) -> Result<EditingJobSnapshot, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(project_id)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let body = serde_json::json!({});
        let response = self
            .execute(
                ControlPlaneOperation::SubmitEditingJob,
                Some(session.token()),
                Some(&body),
                None,
                Some(ControlPlaneRequestTarget::EditingProjectJobs(project_id)),
            )
            .await?;
        let job = parse_editing_job(&response)?;
        if job.project_id != project_id {
            return Err(protocol_invalid());
        }
        Ok(job)
    }

    pub(crate) async fn get_editing_job<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        job_id: &str,
    ) -> Result<EditingJobSnapshot, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(job_id)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response = self
            .execute(
                ControlPlaneOperation::GetEditingJob,
                Some(session.token()),
                None,
                None,
                Some(ControlPlaneRequestTarget::EditingJob(job_id)),
            )
            .await?;
        let job = parse_editing_job(&response)?;
        if job.job_id != job_id {
            return Err(protocol_invalid());
        }
        Ok(job)
    }

    pub(crate) async fn reconcile_editing_job<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        previous: &EditingJobSnapshot,
        status: EditingJobStatus,
        failure_code: Option<EditingJobFailureCode>,
        output_artifact_id: Option<&str>,
    ) -> Result<EditingJobSnapshot, ControlPlaneError>
    where
        S: SecretStore,
    {
        previous.validate()?;
        if (status == EditingJobStatus::Running
            && (failure_code.is_some() || output_artifact_id.is_some()))
            || (status == EditingJobStatus::Succeeded
                && (failure_code.is_some() || output_artifact_id.is_none()))
            || (status == EditingJobStatus::Failed
                && (failure_code.is_none() || output_artifact_id.is_some()))
            || !matches!(
                status,
                EditingJobStatus::Running | EditingJobStatus::Succeeded | EditingJobStatus::Failed
            )
        {
            return Err(protocol_invalid());
        }
        if let Some(identifier) = output_artifact_id {
            require_canonical_uuid_v4(identifier)?;
        }
        let request = EditingJobReconcileRequest {
            expected_updated_at: &previous.updated_at,
            status,
            failure_code,
            output_artifact_id,
        };
        let body = serde_json::to_value(request).map_err(|_| protocol_invalid())?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response = self
            .execute(
                ControlPlaneOperation::ReconcileEditingJob,
                Some(session.token()),
                Some(&body),
                None,
                Some(ControlPlaneRequestTarget::EditingJob(&previous.job_id)),
            )
            .await?;
        let changed = parse_editing_job(&response)?;
        if changed.job_id != previous.job_id
            || changed.project_id != previous.project_id
            || changed.timeline_id != previous.timeline_id
            || changed.timeline_revision != previous.timeline_revision
            || changed.created_at != previous.created_at
            || changed.status != status
            || changed.failure_code != failure_code
            || changed.output_artifact_id.as_deref() != output_artifact_id
        {
            return Err(protocol_invalid());
        }
        Ok(changed)
    }

    pub async fn create_task<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        idempotency_key: &str,
        definition: &DouyinSearchExposureTaskDefinition,
    ) -> Result<TaskSnapshot, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_idempotency_key(idempotency_key)?;
        definition.validate()?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let request_body = serde_json::to_value(definition).map_err(|_| protocol_invalid())?;
        let response_body = self
            .execute(
                ControlPlaneOperation::CreateTask,
                Some(session.token()),
                Some(&request_body),
                Some(idempotency_key),
                None,
            )
            .await?;
        parse_created_task(&response_body)
    }

    pub async fn get_workbench_status<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
    ) -> Result<WorkbenchRuntimeStatus, ControlPlaneError>
    where
        S: SecretStore,
    {
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response_body = self
            .execute(
                ControlPlaneOperation::GetWorkbenchStatus,
                Some(session.token()),
                None,
                None,
                None,
            )
            .await?;
        parse_workbench_status(&response_body)
    }

    pub async fn get_workbench_metrics<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
    ) -> Result<WorkbenchMetrics, ControlPlaneError>
    where
        S: SecretStore,
    {
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response_body = self
            .execute(
                ControlPlaneOperation::GetWorkbenchMetrics,
                Some(session.token()),
                None,
                None,
                None,
            )
            .await?;
        parse_workbench_metrics(&response_body)
    }

    pub async fn get_douyin_platform_session<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
    ) -> Result<PlatformSessionStatus, ControlPlaneError>
    where
        S: SecretStore,
    {
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response_body = self
            .execute(
                ControlPlaneOperation::GetDouyinPlatformSession,
                Some(session.token()),
                None,
                None,
                None,
            )
            .await?;
        parse_douyin_platform_session(&response_body)
    }

    pub async fn prepare_douyin_platform_session_logout<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
    ) -> Result<u64, ControlPlaneError>
    where
        S: SecretStore,
    {
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response_body = self
            .execute(
                ControlPlaneOperation::PrepareDouyinPlatformSessionLogout,
                Some(session.token()),
                None,
                None,
                None,
            )
            .await?;
        parse_douyin_platform_session_logout_prepare(&response_body)
    }

    pub async fn list_tasks<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        cursor: Option<&str>,
        limit: u16,
    ) -> Result<TaskListPage, ControlPlaneError>
    where
        S: SecretStore,
    {
        if !(1..=100).contains(&limit) {
            return Err(protocol_invalid());
        }
        if let Some(value) = cursor {
            require_list_cursor(value)?;
        }
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response_body = self
            .execute(
                ControlPlaneOperation::ListTasks,
                Some(session.token()),
                None,
                None,
                Some(ControlPlaneRequestTarget::List { cursor, limit }),
            )
            .await?;
        parse_task_list(&response_body)
    }

    pub async fn get_task<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        task_id: &str,
    ) -> Result<TaskSnapshot, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(task_id)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response_body = self
            .execute(
                ControlPlaneOperation::GetTask,
                Some(session.token()),
                None,
                None,
                Some(ControlPlaneRequestTarget::Detail(task_id)),
            )
            .await?;
        parse_task_snapshot_body(&response_body)
    }

    pub async fn get_task_target_results<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        task_id: &str,
    ) -> Result<TaskTargetResults, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(task_id)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response_body = self
            .execute(
                ControlPlaneOperation::GetTaskTargetResults,
                Some(session.token()),
                None,
                None,
                Some(ControlPlaneRequestTarget::Detail(task_id)),
            )
            .await?;
        parse_task_target_results(&response_body, task_id)
    }

    pub async fn start_task_discovery<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        task_id: &str,
        idempotency_key: &str,
    ) -> Result<TaskDiscoveryCommand, ControlPlaneError>
    where
        S: SecretStore,
    {
        validate_task_control_input(task_id, idempotency_key)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response_body = self
            .execute(
                ControlPlaneOperation::StartTaskDiscovery,
                Some(session.token()),
                None,
                Some(idempotency_key),
                Some(ControlPlaneRequestTarget::Control(task_id)),
            )
            .await?;
        parse_task_discovery(&response_body, task_id)
    }

    pub async fn get_task_target_preview<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        task_id: &str,
        cursor: Option<&str>,
        limit: u16,
    ) -> Result<TaskTargetPreview, ControlPlaneError>
    where
        S: SecretStore,
    {
        require_canonical_uuid_v4(task_id)?;
        if !(1..=MAX_TASK_TARGET_LIMIT).contains(&limit) {
            return Err(protocol_invalid());
        }
        if let Some(value) = cursor {
            require_preview_cursor(value)?;
        }
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response_body = self
            .execute(
                ControlPlaneOperation::GetTaskTargetPreview,
                Some(session.token()),
                None,
                None,
                Some(ControlPlaneRequestTarget::PreviewList {
                    task_id,
                    cursor,
                    limit,
                }),
            )
            .await?;
        parse_task_target_preview(&response_body, task_id, false)
    }

    pub async fn replace_task_target_exclusions<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        task_id: &str,
        page_revision: u64,
        expected_task_revision: u64,
        excluded_target_ids: &[String],
        idempotency_key: &str,
    ) -> Result<TaskTargetPreview, ControlPlaneError>
    where
        S: SecretStore,
    {
        validate_preview_command(
            task_id,
            page_revision,
            expected_task_revision,
            excluded_target_ids,
            idempotency_key,
        )?;
        let body = serde_json::json!({
            "pageRevision": page_revision,
            "expectedTaskRevision": expected_task_revision,
            "excludedTargetIds": excluded_target_ids,
        });
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response_body = self
            .execute(
                ControlPlaneOperation::ReplaceTaskTargetExclusions,
                Some(session.token()),
                Some(&body),
                Some(idempotency_key),
                Some(ControlPlaneRequestTarget::PreviewCommand(task_id)),
            )
            .await?;
        parse_task_target_preview(&response_body, task_id, true)
    }

    pub async fn confirm_task_target_preview<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        task_id: &str,
        page_revision: u64,
        confirmation_revision: u64,
        idempotency_key: &str,
    ) -> Result<TaskTargetPreview, ControlPlaneError>
    where
        S: SecretStore,
    {
        validate_preview_command(
            task_id,
            page_revision,
            confirmation_revision,
            &[],
            idempotency_key,
        )?;
        let body = serde_json::json!({
            "pageRevision": page_revision,
            "confirmationRevision": confirmation_revision,
        });
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let response_body = self
            .execute(
                ControlPlaneOperation::ConfirmTaskTargetPreview,
                Some(session.token()),
                Some(&body),
                Some(idempotency_key),
                Some(ControlPlaneRequestTarget::PreviewCommand(task_id)),
            )
            .await?;
        parse_task_target_preview(&response_body, task_id, true)
    }

    pub async fn pause_task<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        task_id: &str,
        idempotency_key: &str,
    ) -> Result<TaskControlCommand, ControlPlaneError>
    where
        S: SecretStore,
    {
        self.control_task(
            vault,
            ControlPlaneOperation::PauseTask,
            task_id,
            idempotency_key,
        )
        .await
    }

    pub async fn resume_task<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        task_id: &str,
        idempotency_key: &str,
    ) -> Result<TaskControlCommand, ControlPlaneError>
    where
        S: SecretStore,
    {
        self.control_task(
            vault,
            ControlPlaneOperation::ResumeTask,
            task_id,
            idempotency_key,
        )
        .await
    }

    pub async fn cancel_task<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        task_id: &str,
        idempotency_key: &str,
    ) -> Result<TaskControlCommand, ControlPlaneError>
    where
        S: SecretStore,
    {
        self.control_task(
            vault,
            ControlPlaneOperation::CancelTask,
            task_id,
            idempotency_key,
        )
        .await
    }

    pub async fn emergency_stop_task<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        task_id: &str,
        idempotency_key: &str,
    ) -> Result<TaskControlCommand, ControlPlaneError>
    where
        S: SecretStore,
    {
        self.control_task(
            vault,
            ControlPlaneOperation::EmergencyStopTask,
            task_id,
            idempotency_key,
        )
        .await
    }

    async fn control_task<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        operation: ControlPlaneOperation,
        task_id: &str,
        idempotency_key: &str,
    ) -> Result<TaskControlCommand, ControlPlaneError>
    where
        S: SecretStore,
    {
        validate_task_control_input(task_id, idempotency_key)?;
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let request_body = serde_json::json!({});
        let response_body = self
            .execute(
                operation,
                Some(session.token()),
                Some(&request_body),
                Some(idempotency_key),
                Some(ControlPlaneRequestTarget::Control(task_id)),
            )
            .await?;
        parse_task_control(&response_body, operation, task_id)
    }

    pub async fn stream_task_events<S>(
        &self,
        vault: &DeviceCredentialVault<S>,
        task_id: &str,
        last_event_id: Option<u64>,
        stop_after: Option<u16>,
    ) -> Result<TaskEventStreamResult, ControlPlaneError>
    where
        S: SecretStore,
    {
        self.stream_task_events_with(vault, task_id, last_event_id, stop_after, |_| true)
            .await
    }

    pub async fn stream_task_events_with<S, F>(
        &self,
        vault: &DeviceCredentialVault<S>,
        task_id: &str,
        last_event_id: Option<u64>,
        stop_after: Option<u16>,
        on_event: F,
    ) -> Result<TaskEventStreamResult, ControlPlaneError>
    where
        S: SecretStore,
        F: FnMut(&TaskEvent) -> bool,
    {
        let result = self
            .stream_task_events_with_without_logging(
                vault,
                task_id,
                last_event_id,
                stop_after,
                on_event,
            )
            .await;
        if let Err(error) = &result {
            crate::app_logging::record_failure(
                crate::app_logging::DesktopLogEvent::ControlPlaneEventStreamFailed,
                error,
            );
        }
        result
    }

    async fn stream_task_events_with_without_logging<S, F>(
        &self,
        vault: &DeviceCredentialVault<S>,
        task_id: &str,
        last_event_id: Option<u64>,
        stop_after: Option<u16>,
        mut on_event: F,
    ) -> Result<TaskEventStreamResult, ControlPlaneError>
    where
        S: SecretStore,
        F: FnMut(&TaskEvent) -> bool,
    {
        require_canonical_uuid_v4(task_id)?;
        if last_event_id.is_some_and(|sequence| sequence > MAX_CROSS_RUNTIME_SEQUENCE)
            || stop_after.is_some_and(|limit| !(1..=100).contains(&limit))
        {
            return Err(protocol_invalid());
        }
        let session = self
            .exchange_device_session(vault, DeviceSessionCapability::AppControlPlane)
            .await?;
        let request_id = new_request_id()?;
        let path = request_path(
            ControlPlaneOperation::StreamTaskEvents,
            Some(ControlPlaneRequestTarget::EventStream {
                task_id,
                last_event_id,
            }),
        )?;
        let mut request = self
            .client
            .get(format!("{}{path}", self.origin))
            .timeout(Duration::from_secs(60))
            .header(ACCEPT, "text/event-stream")
            .header(REQUEST_ID_HEADER, &request_id)
            .header(AUTHORIZATION, format!("Bearer {}", session.token()));
        if let Some(sequence) = last_event_id {
            request = request.header(LAST_EVENT_ID_HEADER, sequence.to_string());
        }
        let mut response = request
            .send()
            .await
            .map_err(|_| transport_error(ControlPlaneOperation::StreamTaskEvents))?;
        validate_sse_response_metadata(&request_id, &response)?;

        let expected_start = last_event_id.unwrap_or(0) + 1;
        let target_count = stop_after.map(usize::from);
        let mut total_bytes = 0_usize;
        let mut pending = Vec::new();
        let mut events = Vec::new();
        loop {
            while let Some(frame_end) = sse_frame_end(&pending) {
                if frame_end > MAX_SSE_FRAME_LENGTH {
                    return Err(protocol_invalid());
                }
                let frame = pending.drain(..frame_end).collect::<Vec<_>>();
                let event = parse_sse_frame(&frame, task_id, expected_start + events.len() as u64)?;
                if let Some(event) = event {
                    crate::app_logging::record_task_status(event.task_status());
                    if !on_event(&event) {
                        return Err(protocol_invalid());
                    }
                    events.push(event);
                    if target_count == Some(events.len()) {
                        return Ok(TaskEventStreamResult {
                            terminal: events
                                .last()
                                .is_some_and(|item| terminal_task_status(item.task_status())),
                            events,
                        });
                    }
                }
            }
            let Some(chunk) = response
                .chunk()
                .await
                .map_err(|_| transport_error(ControlPlaneOperation::StreamTaskEvents))?
            else {
                break;
            };
            total_bytes = total_bytes
                .checked_add(chunk.len())
                .ok_or_else(protocol_invalid)?;
            if total_bytes > MAX_SSE_RESPONSE_LENGTH {
                return Err(protocol_invalid());
            }
            pending.extend_from_slice(&chunk);
            if sse_frame_end(&pending).is_none() && pending.len() > MAX_SSE_FRAME_LENGTH {
                return Err(protocol_invalid());
            }
        }
        while let Some(frame_end) = sse_frame_end(&pending) {
            if frame_end > MAX_SSE_FRAME_LENGTH {
                return Err(protocol_invalid());
            }
            let frame = pending.drain(..frame_end).collect::<Vec<_>>();
            if let Some(event) =
                parse_sse_frame(&frame, task_id, expected_start + events.len() as u64)?
            {
                crate::app_logging::record_task_status(event.task_status());
                if !on_event(&event) {
                    return Err(protocol_invalid());
                }
                events.push(event);
            }
        }
        if !pending.is_empty() {
            return Err(protocol_invalid());
        }
        let terminal = events
            .last()
            .is_some_and(|event| terminal_task_status(event.task_status()));
        Ok(TaskEventStreamResult { events, terminal })
    }

    async fn execute(
        &self,
        operation: ControlPlaneOperation,
        bearer: Option<&str>,
        body: Option<&serde_json::Value>,
        idempotency_key: Option<&str>,
        target: Option<ControlPlaneRequestTarget<'_>>,
    ) -> Result<Zeroizing<Vec<u8>>, ControlPlaneError> {
        let result = self
            .execute_without_logging(operation, bearer, body, idempotency_key, target)
            .await;
        if let Err(error) = &result {
            crate::app_logging::record_failure(
                crate::app_logging::DesktopLogEvent::ControlPlaneRequestFailed,
                error,
            );
        }
        result
    }

    async fn execute_bilibili_json(
        &self,
        operation: ControlPlaneOperation,
        bearer: &str,
        publish_job_id: &str,
        publish_session: Option<&str>,
        body: Option<&[u8]>,
    ) -> Result<Zeroizing<Vec<u8>>, ControlPlaneError> {
        let request_id = new_request_id()?;
        let path = request_path(
            operation,
            Some(ControlPlaneRequestTarget::BilibiliPublishJob(
                publish_job_id,
            )),
        )?;
        let url = format!("{}{path}", self.origin);
        let mut request = match operation.method() {
            "POST" => self.client.post(url),
            "PUT" => self.client.put(url),
            "DELETE" => self.client.delete(url),
            _ => return Err(protocol_invalid()),
        }
        .header(ACCEPT, "application/json")
        .header(REQUEST_ID_HEADER, &request_id)
        .header(AUTHORIZATION, format!("Bearer {bearer}"));
        if let Some(token) = publish_session {
            request = request.header(BILIBILI_PUBLISH_SESSION_HEADER, token);
        }
        if let Some(payload) = body {
            request = request
                .header(CONTENT_TYPE, "application/json")
                .body(payload.to_vec());
        }
        self.send_bilibili_request(operation, &request_id, request)
            .await
    }

    async fn send_bilibili_request(
        &self,
        operation: ControlPlaneOperation,
        request_id: &str,
        request: reqwest::RequestBuilder,
    ) -> Result<Zeroizing<Vec<u8>>, ControlPlaneError> {
        let result = async {
            let mut response = request
                .send()
                .await
                .map_err(|_| transport_error(operation))?;
            let metadata = ResponseMetadata {
                status: response.status().as_u16(),
                request_id: header_text(response.headers(), REQUEST_ID_HEADER),
                content_type: header_text(response.headers(), CONTENT_TYPE.as_str()),
                cache_control: header_text(response.headers(), CACHE_CONTROL.as_str()),
            };
            validate_response_metadata(operation, request_id, &metadata)?;
            if response
                .content_length()
                .is_some_and(|length| length > MAX_RESPONSE_LENGTH as u64)
            {
                return Err(protocol_invalid());
            }
            let mut body = Zeroizing::new(Vec::new());
            while let Some(chunk) = response
                .chunk()
                .await
                .map_err(|_| transport_error(operation))?
            {
                if body.len() + chunk.len() > MAX_RESPONSE_LENGTH {
                    return Err(protocol_invalid());
                }
                body.extend_from_slice(&chunk);
            }
            Ok(body)
        }
        .await;
        if let Err(error) = &result {
            crate::app_logging::record_failure(
                crate::app_logging::DesktopLogEvent::ControlPlaneRequestFailed,
                error,
            );
        }
        result
    }

    async fn execute_without_logging(
        &self,
        operation: ControlPlaneOperation,
        bearer: Option<&str>,
        body: Option<&serde_json::Value>,
        idempotency_key: Option<&str>,
        target: Option<ControlPlaneRequestTarget<'_>>,
    ) -> Result<Zeroizing<Vec<u8>>, ControlPlaneError> {
        let request_id = new_request_id()?;
        let path = request_path(operation, target)?;
        let url = format!("{}{path}", self.origin);
        let mut request = match operation.method() {
            "GET" => self.client.get(url),
            "POST" => self.client.post(url),
            "PUT" => self.client.put(url),
            "PATCH" => self.client.patch(url),
            "DELETE" => self.client.delete(url),
            _ => {
                return Err(ControlPlaneError::new(
                    ControlPlaneErrorCode::ProtocolInvalid,
                    false,
                ));
            }
        }
        .header(ACCEPT, "application/json")
        .header(REQUEST_ID_HEADER, &request_id);
        if let Some(credential) = bearer {
            request = request.header(AUTHORIZATION, format!("Bearer {credential}"));
        }
        if let Some(key) = idempotency_key {
            request = request.header(IDEMPOTENCY_KEY_HEADER, key);
        }
        if let Some(payload) = body {
            request = request.json(payload);
        }

        let mut response = request
            .send()
            .await
            .map_err(|_| transport_error(operation))?;
        let metadata = ResponseMetadata {
            status: response.status().as_u16(),
            request_id: header_text(response.headers(), REQUEST_ID_HEADER),
            content_type: header_text(response.headers(), CONTENT_TYPE.as_str()),
            cache_control: header_text(response.headers(), CACHE_CONTROL.as_str()),
        };
        validate_response_metadata(operation, &request_id, &metadata)?;
        let max_response_length = operation.max_response_length();

        if response
            .content_length()
            .is_some_and(|length| length > max_response_length as u64)
        {
            return Err(ControlPlaneError::new(
                ControlPlaneErrorCode::ProtocolInvalid,
                false,
            ));
        }
        let mut response_body = Zeroizing::new(Vec::new());
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| transport_error(operation))?
        {
            if response_body.len() + chunk.len() > max_response_length {
                return Err(ControlPlaneError::new(
                    ControlPlaneErrorCode::ProtocolInvalid,
                    false,
                ));
            }
            response_body.extend_from_slice(&chunk);
        }
        Ok(response_body)
    }
}

fn request_path(
    operation: ControlPlaneOperation,
    target: Option<ControlPlaneRequestTarget<'_>>,
) -> Result<String, ControlPlaneError> {
    match (operation, target) {
        (
            ControlPlaneOperation::ListEditingMaterials,
            Some(ControlPlaneRequestTarget::EditingMaterialList { cursor, limit }),
        ) if (1..=100).contains(&limit) => {
            let mut path = format!("/api/v1/editing-materials/library?limit={limit}");
            if let Some(value) = cursor {
                require_list_cursor(value)?;
                path.push_str("&cursor=");
                path.push_str(value);
            }
            Ok(path)
        }
        (
            ControlPlaneOperation::ListEditingProjects,
            Some(ControlPlaneRequestTarget::EditingProjectList { cursor, limit }),
        ) if (1..=100).contains(&limit) => {
            let mut path = format!("/api/v1/editing-projects?limit={limit}");
            if let Some(value) = cursor {
                require_list_cursor(value)?;
                path.push_str("&cursor=");
                path.push_str(value);
            }
            Ok(path)
        }
        (
            operation @ (ControlPlaneOperation::GetEditingProjectTimeline
            | ControlPlaneOperation::SaveEditingProjectTimeline),
            Some(ControlPlaneRequestTarget::EditingTimeline(project_id)),
        ) => {
            require_canonical_uuid_v4(project_id)?;
            let _ = operation;
            Ok(format!("/api/v1/editing-projects/{project_id}/timeline"))
        }
        (
            ControlPlaneOperation::ListEditingJobs,
            Some(ControlPlaneRequestTarget::EditingJobList {
                project_id,
                cursor,
                limit,
            }),
        ) if (1..=100).contains(&limit) => {
            require_canonical_uuid_v4(project_id)?;
            let mut path = format!("/api/v1/editing-projects/{project_id}/jobs?limit={limit}");
            if let Some(value) = cursor {
                require_list_cursor(value)?;
                path.push_str("&cursor=");
                path.push_str(value);
            }
            Ok(path)
        }
        (
            ControlPlaneOperation::SubmitEditingJob,
            Some(ControlPlaneRequestTarget::EditingProjectJobs(project_id)),
        ) => {
            require_canonical_uuid_v4(project_id)?;
            Ok(format!("/api/v1/editing-projects/{project_id}/jobs"))
        }
        (
            ControlPlaneOperation::FindEditingMaterialByDigest,
            Some(ControlPlaneRequestTarget::EditingMaterialDigest(content_digest)),
        ) => {
            require_content_digest(content_digest)?;
            Ok(format!(
                "/api/v1/editing-materials?contentDigest={content_digest}"
            ))
        }
        (
            operation @ (ControlPlaneOperation::GetEditingMaterial
            | ControlPlaneOperation::DeleteEditingMaterial
            | ControlPlaneOperation::UpdateEditingMaterialDescription),
            Some(ControlPlaneRequestTarget::EditingMaterial(material_id)),
        ) => {
            require_canonical_uuid_v4(material_id)?;
            let suffix = match operation {
                ControlPlaneOperation::UpdateEditingMaterialDescription => "/description",
                ControlPlaneOperation::GetEditingMaterial
                | ControlPlaneOperation::DeleteEditingMaterial => "",
                _ => return Err(protocol_invalid()),
            };
            Ok(format!("/api/v1/editing-materials/{material_id}{suffix}"))
        }
        (
            operation @ (ControlPlaneOperation::GetEditingJob
            | ControlPlaneOperation::ReconcileEditingJob),
            Some(ControlPlaneRequestTarget::EditingJob(job_id)),
        ) => {
            require_canonical_uuid_v4(job_id)?;
            let _ = operation;
            Ok(format!("/api/v1/editing-jobs/{job_id}"))
        }
        (
            ControlPlaneOperation::ListTasks,
            Some(ControlPlaneRequestTarget::List { cursor, limit }),
        ) if (1..=100).contains(&limit) => {
            let mut path = format!("{}?limit={limit}", operation.path());
            if let Some(value) = cursor {
                require_list_cursor(value)?;
                path.push_str("&cursor=");
                path.push_str(value);
            }
            Ok(path)
        }
        (
            operation @ (ControlPlaneOperation::GetTask
            | ControlPlaneOperation::GetTaskTargetResults),
            Some(ControlPlaneRequestTarget::Detail(task_id)),
        ) => {
            require_canonical_uuid_v4(task_id)?;
            let suffix = match operation {
                ControlPlaneOperation::GetTask => "",
                ControlPlaneOperation::GetTaskTargetResults => "/target-results",
                _ => return Err(protocol_invalid()),
            };
            Ok(format!("/api/v1/tasks/{task_id}{suffix}"))
        }
        (
            ControlPlaneOperation::GetTaskTargetPreview,
            Some(ControlPlaneRequestTarget::PreviewList {
                task_id,
                cursor,
                limit,
            }),
        ) if (1..=MAX_TASK_TARGET_LIMIT).contains(&limit) => {
            require_canonical_uuid_v4(task_id)?;
            let mut path = format!("/api/v1/tasks/{task_id}/target-preview?limit={limit}");
            if let Some(value) = cursor {
                require_preview_cursor(value)?;
                path.push_str("&cursor=");
                path.push_str(value);
            }
            Ok(path)
        }
        (
            operation @ (ControlPlaneOperation::ReplaceTaskTargetExclusions
            | ControlPlaneOperation::ConfirmTaskTargetPreview),
            Some(ControlPlaneRequestTarget::PreviewCommand(task_id)),
        ) => {
            require_canonical_uuid_v4(task_id)?;
            let suffix = match operation {
                ControlPlaneOperation::ReplaceTaskTargetExclusions => "exclusions",
                ControlPlaneOperation::ConfirmTaskTargetPreview => "confirmations",
                _ => return Err(protocol_invalid()),
            };
            Ok(format!("/api/v1/tasks/{task_id}/target-preview/{suffix}"))
        }
        (
            operation @ (ControlPlaneOperation::PauseTask
            | ControlPlaneOperation::StartTaskDiscovery
            | ControlPlaneOperation::ResumeTask
            | ControlPlaneOperation::CancelTask
            | ControlPlaneOperation::EmergencyStopTask),
            Some(ControlPlaneRequestTarget::Control(task_id)),
        ) => {
            require_canonical_uuid_v4(task_id)?;
            let suffix = match operation {
                ControlPlaneOperation::StartTaskDiscovery => "discoveries",
                ControlPlaneOperation::PauseTask => "pause",
                ControlPlaneOperation::ResumeTask => "resume",
                ControlPlaneOperation::CancelTask => "cancel",
                ControlPlaneOperation::EmergencyStopTask => "emergency-stop",
                _ => return Err(protocol_invalid()),
            };
            Ok(format!("/api/v1/tasks/{task_id}/{suffix}"))
        }
        (
            ControlPlaneOperation::StreamTaskEvents,
            Some(ControlPlaneRequestTarget::EventStream {
                task_id,
                last_event_id,
            }),
        ) => {
            require_canonical_uuid_v4(task_id)?;
            if last_event_id.is_some_and(|sequence| sequence > MAX_CROSS_RUNTIME_SEQUENCE) {
                return Err(protocol_invalid());
            }
            Ok(format!("/api/v1/tasks/{task_id}/events"))
        }
        (
            operation @ (ControlPlaneOperation::PrepareBilibiliPublish
            | ControlPlaneOperation::UploadBilibiliPublishVideo
            | ControlPlaneOperation::SubmitBilibiliPublish
            | ControlPlaneOperation::CancelBilibiliPublishSession),
            Some(ControlPlaneRequestTarget::BilibiliPublishJob(publish_job_id)),
        ) => {
            require_canonical_uuid_v4(publish_job_id)?;
            Ok(operation.path().replace("{publish_job_id}", publish_job_id))
        }
        (
            ControlPlaneOperation::ListTasks
            | ControlPlaneOperation::ListEditingProjects
            | ControlPlaneOperation::GetEditingProject
            | ControlPlaneOperation::GetEditingProjectTimeline
            | ControlPlaneOperation::SaveEditingProjectTimeline
            | ControlPlaneOperation::ListEditingJobs
            | ControlPlaneOperation::SubmitEditingJob
            | ControlPlaneOperation::GetEditingJob
            | ControlPlaneOperation::FindEditingMaterialByDigest
            | ControlPlaneOperation::ListEditingMaterials
            | ControlPlaneOperation::GetEditingMaterial
            | ControlPlaneOperation::DeleteEditingMaterial
            | ControlPlaneOperation::UpdateEditingMaterialDescription
            | ControlPlaneOperation::ReconcileEditingJob
            | ControlPlaneOperation::GetTask
            | ControlPlaneOperation::GetTaskTargetResults
            | ControlPlaneOperation::GetTaskTargetPreview
            | ControlPlaneOperation::ReplaceTaskTargetExclusions
            | ControlPlaneOperation::ConfirmTaskTargetPreview
            | ControlPlaneOperation::StreamTaskEvents
            | ControlPlaneOperation::PauseTask
            | ControlPlaneOperation::StartTaskDiscovery
            | ControlPlaneOperation::ResumeTask
            | ControlPlaneOperation::CancelTask
            | ControlPlaneOperation::EmergencyStopTask
            | ControlPlaneOperation::PrepareBilibiliPublish
            | ControlPlaneOperation::UploadBilibiliPublishVideo
            | ControlPlaneOperation::SubmitBilibiliPublish
            | ControlPlaneOperation::CancelBilibiliPublishSession,
            _,
        )
        | (_, Some(_)) => Err(protocol_invalid()),
        (_, None) => Ok(operation.path().to_owned()),
    }
}

fn transport_error(operation: ControlPlaneOperation) -> ControlPlaneError {
    if operation.outcome_is_uncertain_on_transport_failure() {
        ControlPlaneError::new(ControlPlaneErrorCode::OutcomeUncertain, false)
    } else {
        ControlPlaneError::new(ControlPlaneErrorCode::TransportUnavailable, true)
    }
}

fn required_credential<S>(
    vault: &DeviceCredentialVault<S>,
) -> Result<StoredDeviceCredential, ControlPlaneError>
where
    S: SecretStore,
{
    vault
        .load()
        .map_err(map_vault_error)?
        .ok_or_else(|| ControlPlaneError::new(ControlPlaneErrorCode::CredentialMissing, false))
}

fn map_vault_error(error: crate::device_credentials::DeviceCredentialError) -> ControlPlaneError {
    let code = match error.code() {
        DeviceCredentialErrorCode::SecureStoreUnavailable => {
            ControlPlaneErrorCode::StorageUnavailable
        }
        DeviceCredentialErrorCode::InvalidCredential
        | DeviceCredentialErrorCode::CorruptStoredCredential => {
            ControlPlaneErrorCode::ProtocolInvalid
        }
    };
    ControlPlaneError::new(code, false)
}

fn header_text(headers: &reqwest::header::HeaderMap, name: &str) -> Option<String> {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned)
}

fn new_request_id() -> Result<String, ControlPlaneError> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes)
        .map_err(|_| ControlPlaneError::new(ControlPlaneErrorCode::TransportUnavailable, true))?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(uuid::Uuid::from_bytes(bytes).hyphenated().to_string())
}

fn validate_response_metadata(
    operation: ControlPlaneOperation,
    expected_request_id: &str,
    metadata: &ResponseMetadata,
) -> Result<(), ControlPlaneError> {
    let content_type_is_json = metadata
        .content_type
        .as_deref()
        .and_then(|value| value.split(';').next())
        .is_some_and(|value| value.trim().eq_ignore_ascii_case("application/json"));
    let cache_control_is_private = metadata
        .cache_control
        .as_deref()
        .is_some_and(|value| value.split(',').any(|part| part.trim() == "no-store"));
    let content_type_matches = if metadata.status == 204 {
        metadata.content_type.is_none()
    } else {
        content_type_is_json
    };
    if !operation.accepts_status(metadata.status)
        || metadata.request_id.as_deref() != Some(expected_request_id)
        || !content_type_matches
        || !cache_control_is_private
    {
        let code = if operation.accepts_status(metadata.status) {
            ControlPlaneErrorCode::ProtocolInvalid
        } else if metadata.status == 423
            && matches!(operation, ControlPlaneOperation::StartTaskDiscovery)
            && metadata.request_id.as_deref() == Some(expected_request_id)
            && content_type_is_json
            && cache_control_is_private
        {
            ControlPlaneErrorCode::InstallationBusy
        } else if metadata.status == 409
            && matches!(
                operation,
                ControlPlaneOperation::CompleteInstallationRegistration
            )
        {
            // The App always completes registration with a challenge it just
            // issued, so the only conflict it can provoke is a service that
            // already holds an Installation for this device public key. That
            // happens when a previous registration was accepted but the
            // credential never reached the vault, and it needs its own
            // diagnostic: no retry can resolve it, only a new device identity.
            ControlPlaneErrorCode::InstallationConflict
        } else if metadata.status == 401
            && matches!(
                operation,
                ControlPlaneOperation::ExchangeDeviceSession
                    | ControlPlaneOperation::GetCurrentInstallationAccess
                    | ControlPlaneOperation::FindEditingMaterialByDigest
                    | ControlPlaneOperation::RegisterEditingMaterial
                    | ControlPlaneOperation::ListEditingMaterials
                    | ControlPlaneOperation::GetEditingMaterial
                    | ControlPlaneOperation::DeleteEditingMaterial
                    | ControlPlaneOperation::UpdateEditingMaterialDescription
                    | ControlPlaneOperation::ApplySmartEditMaterialWriteback
                    | ControlPlaneOperation::ListEditingProjects
                    | ControlPlaneOperation::CreateEditingProject
                    | ControlPlaneOperation::GetEditingProject
                    | ControlPlaneOperation::GetEditingProjectTimeline
                    | ControlPlaneOperation::SaveEditingProjectTimeline
                    | ControlPlaneOperation::ListEditingJobs
                    | ControlPlaneOperation::SubmitEditingJob
                    | ControlPlaneOperation::GetEditingJob
                    | ControlPlaneOperation::ReconcileEditingJob
                    | ControlPlaneOperation::CreateTask
                    | ControlPlaneOperation::StartTaskDiscovery
                    | ControlPlaneOperation::ListTasks
                    | ControlPlaneOperation::GetTask
                    | ControlPlaneOperation::GetTaskTargetResults
                    | ControlPlaneOperation::PauseTask
                    | ControlPlaneOperation::ResumeTask
                    | ControlPlaneOperation::CancelTask
                    | ControlPlaneOperation::EmergencyStopTask
            )
        {
            ControlPlaneErrorCode::InstallationAccessDenied
        } else if metadata.status == 404
            && matches!(
                operation,
                ControlPlaneOperation::FindEditingMaterialByDigest
                    | ControlPlaneOperation::GetEditingMaterial
                    | ControlPlaneOperation::DeleteEditingMaterial
                    | ControlPlaneOperation::GetEditingProject
                    | ControlPlaneOperation::GetEditingProjectTimeline
                    | ControlPlaneOperation::GetEditingJob
            )
        {
            ControlPlaneErrorCode::ResourceNotFound
        } else {
            ControlPlaneErrorCode::RequestRejected
        };
        return Err(ControlPlaneError::new(code, metadata.status >= 500));
    }
    Ok(())
}

fn validate_sse_response_metadata(
    expected_request_id: &str,
    response: &reqwest::Response,
) -> Result<(), ControlPlaneError> {
    let content_type_is_sse = header_text(response.headers(), CONTENT_TYPE.as_str())
        .as_deref()
        .and_then(|value| value.split(';').next())
        .is_some_and(|value| value.trim().eq_ignore_ascii_case("text/event-stream"));
    let cache_control = header_text(response.headers(), CACHE_CONTROL.as_str());
    let has_cache_directives = ["no-store", "no-transform"].into_iter().all(|expected| {
        cache_control
            .as_deref()
            .is_some_and(|value| value.split(',').any(|part| part.trim() == expected))
    });
    let buffering_disabled = header_text(response.headers(), "x-accel-buffering")
        .is_some_and(|value| value.eq_ignore_ascii_case("no"));
    if response.status().as_u16() != 200
        || header_text(response.headers(), REQUEST_ID_HEADER).as_deref()
            != Some(expected_request_id)
        || !content_type_is_sse
        || !has_cache_directives
        || !buffering_disabled
    {
        let status = response.status().as_u16();
        let code = if status == 401 {
            ControlPlaneErrorCode::InstallationAccessDenied
        } else if status == 200 {
            ControlPlaneErrorCode::ProtocolInvalid
        } else {
            ControlPlaneErrorCode::RequestRejected
        };
        return Err(ControlPlaneError::new(code, status >= 500));
    }
    Ok(())
}

fn sse_frame_end(pending: &[u8]) -> Option<usize> {
    let lf = pending
        .windows(2)
        .position(|window| window == b"\n\n")
        .map(|index| index + 2);
    let crlf = pending
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .map(|index| index + 4);
    match (lf, crlf) {
        (Some(left), Some(right)) => Some(left.min(right)),
        (Some(end), None) | (None, Some(end)) => Some(end),
        (None, None) => None,
    }
}

fn valid_task_event_type(value: &str) -> bool {
    matches!(
        value,
        "task.created"
            | "task.validation_started"
            | "task.validation_failed"
            | "task.awaiting_platform_login"
            | "task.awaiting_confirmation"
            | "task.started"
            | "step.started"
            | "step.progress"
            | "step.completed"
            | "step.failed"
            | "task.awaiting_human"
            | "task.paused"
            | "task.resumed"
            | "task.cancelling"
            | "task.cancelled"
            | "task.completed"
            | "task.partially_completed"
            | "task.failed"
            | "task.outcome_uncertain"
    )
}

fn terminal_task_status(value: &str) -> bool {
    matches!(
        value,
        "succeeded" | "partially_succeeded" | "failed" | "cancelled" | "outcome_uncertain"
    )
}

fn parse_task_event(
    response: TaskEventResponse,
    expected_task_id: &str,
    expected_sequence: u64,
) -> Result<TaskEvent, ControlPlaneError> {
    require_canonical_uuid_v4(&response.task_id)?;
    if response.task_id != expected_task_id
        || response.sequence != expected_sequence
        || response.sequence > MAX_CROSS_RUNTIME_SEQUENCE
        || response.event_version != "1.0"
        || !valid_task_event_type(&response.event_type)
        || response.task_revision == 0
        || response.task_revision > MAX_CROSS_RUNTIME_SEQUENCE
        || !valid_task_status(&response.task_status)
    {
        return Err(protocol_invalid());
    }
    if let Some(attempt_id) = response.execution_attempt_id.as_deref() {
        require_canonical_uuid_v4(attempt_id)?;
    }
    if let Some(action_id) = response.action_id.as_deref() {
        require_canonical_uuid_v4(action_id)?;
        if response.execution_attempt_id.is_none() {
            return Err(protocol_invalid());
        }
    }
    if response
        .progress_percent
        .is_some_and(|progress| progress > 100)
        || response.event_type != "step.progress" && response.progress_percent.is_some()
    {
        return Err(protocol_invalid());
    }
    let occurred_at = require_bounded_timestamp(&response.occurred_at)?;
    let recorded_at = require_bounded_timestamp(&response.recorded_at)?;
    if recorded_at < occurred_at
        || response.message.as_deref().is_some_and(|message| {
            message.is_empty()
                || message.chars().count() > 1024
                || message.chars().any(char::is_control)
        })
    {
        return Err(protocol_invalid());
    }
    Ok(TaskEvent {
        task_id: response.task_id,
        sequence: response.sequence,
        event_version: response.event_version,
        event_type: response.event_type,
        task_revision: response.task_revision,
        task_status: response.task_status,
        execution_attempt_id: response.execution_attempt_id,
        action_id: response.action_id,
        progress_percent: response.progress_percent,
        occurred_at: response.occurred_at,
        recorded_at: response.recorded_at,
        message: response.message,
    })
}

fn parse_sse_frame(
    frame: &[u8],
    expected_task_id: &str,
    expected_sequence: u64,
) -> Result<Option<TaskEvent>, ControlPlaneError> {
    let text = std::str::from_utf8(frame).map_err(|_| protocol_invalid())?;
    let mut identifier = None;
    let mut event_name = None;
    let mut data = None;
    for line in text.lines() {
        if line.is_empty() || line.starts_with(':') {
            continue;
        }
        let (name, value) = line.split_once(':').ok_or_else(protocol_invalid)?;
        let value = value.strip_prefix(' ').unwrap_or(value);
        let target = match name {
            "id" => &mut identifier,
            "event" => &mut event_name,
            "data" => &mut data,
            _ => return Err(protocol_invalid()),
        };
        if target.replace(value).is_some() {
            return Err(protocol_invalid());
        }
    }
    if identifier.is_none() && event_name.is_none() && data.is_none() {
        return Ok(None);
    }
    let identifier = identifier.ok_or_else(protocol_invalid)?;
    let event_name = event_name.ok_or_else(protocol_invalid)?;
    let data = data.ok_or_else(protocol_invalid)?;
    if identifier != expected_sequence.to_string() {
        return Err(protocol_invalid());
    }
    let response: TaskEventResponse = serde_json::from_str(data).map_err(|_| protocol_invalid())?;
    if response.event_type != event_name {
        return Err(protocol_invalid());
    }
    parse_task_event(response, expected_task_id, expected_sequence).map(Some)
}

fn parse_health_response(body: &[u8]) -> Result<ControlPlaneHealth, ControlPlaneError> {
    let response: HealthResponse = serde_json::from_slice(body)
        .map_err(|_| ControlPlaneError::new(ControlPlaneErrorCode::ProtocolInvalid, false))?;
    if response.service != "control-plane"
        || response.status != "ok"
        || response.version.is_empty()
        || response.version.len() > 64
    {
        return Err(ControlPlaneError::new(
            ControlPlaneErrorCode::ProtocolInvalid,
            false,
        ));
    }
    Ok(ControlPlaneHealth {
        status: "available",
        service_version: response.version,
    })
}

fn validate_account_device(device: &AccountDevice) -> Result<(), ControlPlaneError> {
    require_canonical_uuid_v4(&device.installation_id)?;
    if !matches!(device.status.as_str(), "active" | "revoked") || device.revision == 0 {
        return Err(protocol_invalid());
    }
    let created_at = require_bounded_timestamp(&device.created_at)?;
    let updated_at = require_bounded_timestamp(&device.updated_at)?;
    if updated_at < created_at {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn parse_bilibili_publish_response(
    body: &[u8],
    expected_publish_job_id: &str,
) -> Result<BilibiliPublishControlResult, ControlPlaneError> {
    let response: BilibiliPublishResponse = parse_exact_json(body)?;
    require_canonical_uuid_v4(expected_publish_job_id)?;
    if response.publish_job_id != expected_publish_job_id
        || !is_lower_hex_digest(&response.request_digest)
        || response
            .resource_id
            .as_deref()
            .is_some_and(|value| !is_bilibili_resource_id(value))
        || (response.resource_id.is_some()
            != matches!(response.phase, BilibiliPublishPhase::Submitted))
    {
        return Err(protocol_invalid());
    }
    if let Some(token) = response.session_token.as_deref() {
        require_bilibili_publish_session(token)?;
    }
    Ok(BilibiliPublishControlResult {
        phase: response.phase,
        resource_id: response.resource_id,
        replayed: response.replayed,
        session_token: response.session_token.map(Zeroizing::new),
        credential_rotation: response.credential_rotation,
    })
}

fn require_login_input(login_name: &str, password: &str) -> Result<(), ControlPlaneError> {
    let valid_login_name = (3..=64).contains(&login_name.len())
        && login_name.as_bytes()[0].is_ascii_alphabetic()
        && login_name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'));
    if !valid_login_name {
        return Err(protocol_invalid());
    }
    require_password(password)
}

fn require_password(password: &str) -> Result<(), ControlPlaneError> {
    let length = password.chars().count();
    if !(12..=128).contains(&length) || password.chars().any(|value| value == '\0') {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn require_empty_response(body: &[u8]) -> Result<(), ControlPlaneError> {
    if body.is_empty() {
        Ok(())
    } else {
        Err(protocol_invalid())
    }
}

fn parse_system_version_response(
    body: &[u8],
    health_version: &str,
) -> Result<(), ControlPlaneError> {
    let response: SystemVersionResponse =
        serde_json::from_slice(body).map_err(|_| protocol_invalid())?;
    if response.service != "control-plane"
        || response.version != health_version
        || response.version != CONTROL_PLANE_VERSION
        || response.api_version != CONTROL_PLANE_API_VERSION
        || !exact_version_compatibility(&response.desktop_app, DESKTOP_APP_VERSION)
        || !exact_version_compatibility(&response.executor_runtime, EXECUTOR_RUNTIME_VERSION)
        || !exact_protocol_compatibility(&response.executor_protocol, EXECUTOR_PROTOCOL_VERSION)
    {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn exact_version_compatibility(
    compatibility: &VersionCompatibilityResponse,
    expected: &str,
) -> bool {
    compatibility.current == expected
        && compatibility.minimum_compatible == expected
        && compatibility.maximum_compatible == expected
        && semver::Version::parse(expected).is_ok()
}

fn exact_protocol_compatibility(
    compatibility: &VersionCompatibilityResponse,
    expected: &str,
) -> bool {
    compatibility.current == expected
        && compatibility.minimum_compatible == expected
        && compatibility.maximum_compatible == expected
}

fn parse_installation_access(body: &[u8]) -> Result<String, ControlPlaneError> {
    let response: InstallationAccessResponse = parse_exact_json(body)?;
    require_canonical_uuid_v4(&response.installation_id)?;
    if response.status != "active" {
        return Err(protocol_invalid());
    }
    Ok(response.installation_id)
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TaskSnapshotResponse {
    task_id: String,
    status: String,
    revision: u32,
    last_event_sequence: u64,
    created_at: String,
    updated_at: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct WorkbenchStatusResponse {
    control_plane_status: String,
    executor_status: String,
    executor_last_heartbeat_at: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct WorkbenchMetricsResponse {
    version: String,
    tasks: WorkbenchTaskMetricsResponse,
    actions: WorkbenchActionMetricsResponse,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct WorkbenchTaskMetricsResponse {
    total: u64,
    succeeded: u64,
    failed: u64,
    handoff_required: u64,
    outcome_uncertain: u64,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct WorkbenchActionMetricsResponse {
    total: u64,
    succeeded: u64,
    failed: u64,
    outcome_uncertain: u64,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PlatformSessionResponse {
    platform: String,
    state: String,
    observed_at: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PlatformSessionLogoutPrepareResponse {
    platform: String,
    state: String,
    session_revision: u64,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TaskControlResponse {
    command_id: String,
    task_id: String,
    execution_attempt_id: String,
    sequence: u64,
    command_type: String,
    status: String,
    revision: u64,
    created_at: String,
    deadline_at: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TaskDiscoveryResponse {
    task_id: String,
    task_status: String,
    task_revision: u64,
    last_event_sequence: u64,
    command_id: String,
    execution_attempt_id: String,
    command_status: String,
    created_at: String,
    deadline_at: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TaskTargetPreviewItemResponse {
    target_id: String,
    ordinal: u16,
    display_name: String,
    public_handle: Option<String>,
    source: String,
    disposition: String,
    user_excluded: bool,
    selected: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TaskTargetPreviewResponse {
    task_id: String,
    task_status: String,
    task_revision: u64,
    confirmation_revision: u64,
    last_event_sequence: u64,
    page_revision: u64,
    action: DouyinSearchExposureAction,
    message_template: Option<String>,
    selected_target_count: u16,
    user_excluded_target_count: u16,
    confirmed: bool,
    confirmed_at: Option<String>,
    items: Vec<TaskTargetPreviewItemResponse>,
    next_cursor: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TaskTargetResultItemResponse {
    target_id: String,
    ordinal: u16,
    display_name: String,
    public_handle: Option<String>,
    result_status: String,
    evidence: String,
    action_id: Option<String>,
    updated_at: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TaskTargetResultsResponse {
    task_id: String,
    task_status: String,
    task_revision: u64,
    last_event_sequence: u64,
    items: Vec<TaskTargetResultItemResponse>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TaskListResponse {
    items: Vec<TaskSnapshotResponse>,
    next_cursor: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TaskEventResponse {
    task_id: String,
    sequence: u64,
    event_version: String,
    event_type: String,
    task_revision: u64,
    task_status: String,
    execution_attempt_id: Option<String>,
    action_id: Option<String>,
    progress_percent: Option<u8>,
    occurred_at: String,
    recorded_at: String,
    message: Option<String>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskEvent {
    task_id: String,
    sequence: u64,
    event_version: String,
    event_type: String,
    task_revision: u64,
    task_status: String,
    execution_attempt_id: Option<String>,
    action_id: Option<String>,
    progress_percent: Option<u8>,
    occurred_at: String,
    recorded_at: String,
    message: Option<String>,
}

impl TaskEvent {
    pub fn sequence(&self) -> u64 {
        self.sequence
    }

    pub fn event_type(&self) -> &str {
        &self.event_type
    }

    pub fn task_revision(&self) -> u64 {
        self.task_revision
    }

    pub fn task_status(&self) -> &str {
        &self.task_status
    }

    pub fn progress_percent(&self) -> Option<u8> {
        self.progress_percent
    }
}

pub struct TaskEventStreamResult {
    events: Vec<TaskEvent>,
    terminal: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskControlCommand {
    command_id: String,
    task_id: String,
    execution_attempt_id: String,
    sequence: u64,
    command_type: String,
    status: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskDiscoveryCommand {
    task_id: String,
    task_status: String,
    task_revision: u64,
    last_event_sequence: u64,
    command_id: String,
    execution_attempt_id: String,
    command_status: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskTargetPreviewItem {
    target_id: String,
    ordinal: u16,
    display_name: String,
    public_handle: Option<String>,
    source: String,
    disposition: String,
    user_excluded: bool,
    selected: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskTargetPreview {
    task_id: String,
    task_status: String,
    task_revision: u64,
    confirmation_revision: u64,
    last_event_sequence: u64,
    page_revision: u64,
    action: DouyinSearchExposureAction,
    message_template: Option<String>,
    selected_target_count: u16,
    user_excluded_target_count: u16,
    confirmed: bool,
    confirmed_at: Option<String>,
    items: Vec<TaskTargetPreviewItem>,
    next_cursor: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskTargetResultItem {
    target_id: String,
    ordinal: u16,
    display_name: String,
    public_handle: Option<String>,
    result_status: String,
    evidence: String,
    action_id: Option<String>,
    updated_at: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskTargetResults {
    task_id: String,
    task_status: String,
    task_revision: u64,
    last_event_sequence: u64,
    items: Vec<TaskTargetResultItem>,
}

impl TaskTargetPreview {
    pub fn task_id(&self) -> &str {
        &self.task_id
    }

    pub fn task_status(&self) -> &str {
        &self.task_status
    }

    pub fn task_revision(&self) -> u64 {
        self.task_revision
    }

    pub fn confirmation_revision(&self) -> u64 {
        self.confirmation_revision
    }

    pub fn last_event_sequence(&self) -> u64 {
        self.last_event_sequence
    }

    pub fn page_revision(&self) -> u64 {
        self.page_revision
    }

    pub fn action(&self) -> DouyinSearchExposureAction {
        self.action
    }

    pub fn message_template(&self) -> Option<&str> {
        self.message_template.as_deref()
    }

    pub fn selected_target_count(&self) -> u16 {
        self.selected_target_count
    }

    pub fn user_excluded_target_count(&self) -> u16 {
        self.user_excluded_target_count
    }

    pub fn confirmed(&self) -> bool {
        self.confirmed
    }

    pub fn items(&self) -> &[TaskTargetPreviewItem] {
        &self.items
    }
}

impl TaskTargetPreviewItem {
    pub fn target_id(&self) -> &str {
        &self.target_id
    }

    pub fn selected(&self) -> bool {
        self.selected
    }

    pub fn user_excluded(&self) -> bool {
        self.user_excluded
    }

    pub fn ordinal(&self) -> u16 {
        self.ordinal
    }
}

impl TaskDiscoveryCommand {
    pub fn task_id(&self) -> &str {
        &self.task_id
    }

    pub fn task_status(&self) -> &str {
        &self.task_status
    }

    pub fn task_revision(&self) -> u64 {
        self.task_revision
    }

    pub fn last_event_sequence(&self) -> u64 {
        self.last_event_sequence
    }

    pub fn command_id(&self) -> &str {
        &self.command_id
    }

    pub fn execution_attempt_id(&self) -> &str {
        &self.execution_attempt_id
    }

    pub fn command_status(&self) -> &str {
        &self.command_status
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkbenchRuntimeStatus {
    control_plane_status: String,
    executor_status: String,
    executor_last_heartbeat_at: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkbenchMetrics {
    version: &'static str,
    tasks: WorkbenchTaskMetrics,
    actions: WorkbenchActionMetrics,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkbenchTaskMetrics {
    total: u64,
    succeeded: u64,
    failed: u64,
    handoff_required: u64,
    outcome_uncertain: u64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkbenchActionMetrics {
    total: u64,
    succeeded: u64,
    failed: u64,
    outcome_uncertain: u64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PlatformSessionStatus {
    platform: String,
    state: String,
    observed_at: Option<String>,
}

impl PlatformSessionStatus {
    pub fn state(&self) -> &str {
        &self.state
    }
}

impl TaskControlCommand {
    pub fn command_id(&self) -> &str {
        &self.command_id
    }

    pub fn task_id(&self) -> &str {
        &self.task_id
    }

    pub fn execution_attempt_id(&self) -> &str {
        &self.execution_attempt_id
    }

    pub fn sequence(&self) -> u64 {
        self.sequence
    }

    pub fn command_type(&self) -> &str {
        &self.command_type
    }

    pub fn status(&self) -> &str {
        &self.status
    }
}

impl TaskEventStreamResult {
    pub fn events(&self) -> &[TaskEvent] {
        &self.events
    }

    pub fn terminal(&self) -> bool {
        self.terminal
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskSnapshot {
    task_id: String,
    status: String,
    revision: u32,
    last_event_sequence: u64,
    created_at: String,
    updated_at: String,
    #[serde(skip)]
    updated_at_value: OffsetDateTime,
}

impl TaskSnapshot {
    pub fn task_id(&self) -> &str {
        &self.task_id
    }

    pub fn status(&self) -> &str {
        &self.status
    }

    pub fn revision(&self) -> u32 {
        self.revision
    }

    pub fn last_event_sequence(&self) -> u64 {
        self.last_event_sequence
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskListPage {
    items: Vec<TaskSnapshot>,
    next_cursor: Option<String>,
}

impl TaskListPage {
    pub fn items(&self) -> &[TaskSnapshot] {
        &self.items
    }

    pub fn next_cursor(&self) -> Option<&str> {
        self.next_cursor.as_deref()
    }
}

fn require_idempotency_key(value: &str) -> Result<(), ControlPlaneError> {
    let mut bytes = value.bytes();
    let first = bytes.next();
    if value.len() > 128
        || !first.is_some_and(|byte| byte.is_ascii_alphanumeric())
        || !bytes.all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'/' | b'-')
        })
    {
        return Err(protocol_invalid());
    }
    Ok(())
}

pub(crate) fn validate_task_control_input(
    task_id: &str,
    idempotency_key: &str,
) -> Result<(), ControlPlaneError> {
    require_canonical_uuid_v4(task_id)?;
    require_idempotency_key(idempotency_key)
}

fn require_safe_exact_text(
    value: &str,
    maximum_characters: usize,
) -> Result<(), ControlPlaneError> {
    let folded = value.to_lowercase();
    let sensitive_names = [
        "access_token",
        "access-token",
        "api_key",
        "api-key",
        "authorization",
        "cookie",
        "credential",
        "password",
        "private_key",
        "private-key",
        "refresh_token",
        "refresh-token",
        "secret",
        "session_cookie",
        "session-cookie",
        "token",
    ];
    let sensitive_assignment = sensitive_names.iter().any(|name| {
        folded.match_indices(name).any(|(index, matched)| {
            let prefix_is_boundary = index == 0
                || folded[..index]
                    .chars()
                    .next_back()
                    .is_none_or(|character| !character.is_ascii_alphanumeric() && character != '_');
            let suffix = folded[index + matched.len()..].trim_start();
            prefix_is_boundary && (suffix.starts_with(':') || suffix.starts_with('='))
        })
    });
    let has_private_path = ["/users/", "/home/", "/root/", "/tmp/", "/var/folders/"]
        .iter()
        .any(|path| folded.contains(path));
    let has_windows_path = folded.as_bytes().windows(3).any(|window| {
        window[0].is_ascii_alphabetic() && window[1] == b':' && matches!(window[2], b'/' | b'\\')
    });
    if value.is_empty()
        || value.trim() != value
        || value.chars().count() > maximum_characters
        || value.chars().any(|character| {
            character.is_control() || matches!(character as u32, 0x202a..=0x202e | 0x2066..=0x2069)
        })
        || folded.contains("bearer ")
        || folded.contains("file://")
        || folded.contains("data:")
        || has_private_path
        || has_windows_path
        || sensitive_assignment
    {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn is_lower_hex_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
}

fn is_bilibili_resource_id(value: &str) -> bool {
    value.len() == 12
        && value.starts_with("BV")
        && value[2..].bytes().all(|byte| byte.is_ascii_alphanumeric())
}

fn require_bilibili_publish_session(value: &str) -> Result<(), ControlPlaneError> {
    if (32..=128).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        Ok(())
    } else {
        Err(protocol_invalid())
    }
}

fn require_action_message_template(value: &str) -> Result<(), ControlPlaneError> {
    require_safe_exact_text(value, MAX_MESSAGE_TEMPLATE_CHARACTERS)?;
    let literal = value.replace(TARGET_DISPLAY_NAME_VARIABLE, "");
    if literal.trim().is_empty() || literal.contains('{') || literal.contains('}') {
        return Err(protocol_invalid());
    }
    Ok(())
}

pub(crate) fn action_message_template_is_valid(value: &str) -> bool {
    require_action_message_template(value).is_ok()
}

fn require_list_cursor(value: &str) -> Result<(), ControlPlaneError> {
    if value.is_empty()
        || value.len() > 256
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn require_preview_cursor(value: &str) -> Result<(), ControlPlaneError> {
    if value.is_empty()
        || value.len() > 512
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn validate_preview_command(
    task_id: &str,
    page_revision: u64,
    expected_task_revision: u64,
    excluded_target_ids: &[String],
    idempotency_key: &str,
) -> Result<(), ControlPlaneError> {
    require_canonical_uuid_v4(task_id)?;
    require_idempotency_key(idempotency_key)?;
    if page_revision == 0
        || page_revision > MAX_CROSS_RUNTIME_SEQUENCE
        || expected_task_revision == 0
        || expected_task_revision > MAX_CROSS_RUNTIME_SEQUENCE
        || excluded_target_ids.len() > MAX_TASK_TARGET_LIMIT as usize
    {
        return Err(protocol_invalid());
    }
    let mut unique = HashSet::with_capacity(excluded_target_ids.len());
    for target_id in excluded_target_ids {
        require_canonical_uuid_v4(target_id)?;
        if !unique.insert(target_id.as_str()) {
            return Err(protocol_invalid());
        }
    }
    Ok(())
}

fn valid_task_status(value: &str) -> bool {
    matches!(
        value,
        "draft"
            | "validating"
            | "awaiting_device"
            | "awaiting_platform_login"
            | "discovering_targets"
            | "awaiting_confirmation"
            | "queued"
            | "running"
            | "paused"
            | "awaiting_human"
            | "cancelling"
            | "succeeded"
            | "partially_succeeded"
            | "failed"
            | "cancelled"
            | "outcome_uncertain"
    )
}

fn parse_task_snapshot(response: TaskSnapshotResponse) -> Result<TaskSnapshot, ControlPlaneError> {
    require_canonical_uuid_v4(&response.task_id)?;
    let created_at = require_bounded_timestamp(&response.created_at)?;
    let updated_at = require_bounded_timestamp(&response.updated_at)?;
    if !valid_task_status(&response.status)
        || response.revision == 0
        || response.last_event_sequence > MAX_CROSS_RUNTIME_SEQUENCE
        || response.last_event_sequence > u64::from(response.revision)
        || updated_at < created_at
    {
        return Err(protocol_invalid());
    }
    Ok(TaskSnapshot {
        task_id: response.task_id,
        status: response.status,
        revision: response.revision,
        last_event_sequence: response.last_event_sequence,
        created_at: response.created_at,
        updated_at: response.updated_at,
        updated_at_value: updated_at,
    })
}

fn parse_workbench_status(body: &[u8]) -> Result<WorkbenchRuntimeStatus, ControlPlaneError> {
    let response: WorkbenchStatusResponse = parse_exact_json(body)?;
    let online = response.executor_status == "online";
    if response.control_plane_status != "ready"
        || !matches!(response.executor_status.as_str(), "online" | "offline")
        || online != response.executor_last_heartbeat_at.is_some()
    {
        return Err(protocol_invalid());
    }
    if let Some(timestamp) = response.executor_last_heartbeat_at.as_deref() {
        require_bounded_timestamp(timestamp)?;
    }
    Ok(WorkbenchRuntimeStatus {
        control_plane_status: response.control_plane_status,
        executor_status: response.executor_status,
        executor_last_heartbeat_at: response.executor_last_heartbeat_at,
    })
}

fn parse_workbench_metrics(body: &[u8]) -> Result<WorkbenchMetrics, ControlPlaneError> {
    let response: WorkbenchMetricsResponse = parse_exact_json(body)?;
    let task_accounted = response
        .tasks
        .succeeded
        .checked_add(response.tasks.failed)
        .and_then(|value| value.checked_add(response.tasks.handoff_required))
        .and_then(|value| value.checked_add(response.tasks.outcome_uncertain))
        .ok_or_else(protocol_invalid)?;
    let action_accounted = response
        .actions
        .succeeded
        .checked_add(response.actions.failed)
        .and_then(|value| value.checked_add(response.actions.outcome_uncertain))
        .ok_or_else(protocol_invalid)?;
    let counts = [
        response.tasks.total,
        response.tasks.succeeded,
        response.tasks.failed,
        response.tasks.handoff_required,
        response.tasks.outcome_uncertain,
        response.actions.total,
        response.actions.succeeded,
        response.actions.failed,
        response.actions.outcome_uncertain,
    ];
    if response.version != "workbench.metrics.v1"
        || counts
            .iter()
            .any(|count| *count > MAX_CROSS_RUNTIME_SEQUENCE)
        || task_accounted > response.tasks.total
        || action_accounted > response.actions.total
    {
        return Err(protocol_invalid());
    }
    Ok(WorkbenchMetrics {
        version: "workbench.metrics.v1",
        tasks: WorkbenchTaskMetrics {
            total: response.tasks.total,
            succeeded: response.tasks.succeeded,
            failed: response.tasks.failed,
            handoff_required: response.tasks.handoff_required,
            outcome_uncertain: response.tasks.outcome_uncertain,
        },
        actions: WorkbenchActionMetrics {
            total: response.actions.total,
            succeeded: response.actions.succeeded,
            failed: response.actions.failed,
            outcome_uncertain: response.actions.outcome_uncertain,
        },
    })
}

fn parse_douyin_platform_session(body: &[u8]) -> Result<PlatformSessionStatus, ControlPlaneError> {
    let response: PlatformSessionResponse = parse_exact_json(body)?;
    if response.platform != "douyin"
        || !matches!(
            response.state.as_str(),
            "healthy" | "expired" | "missing" | "risk" | "unknown"
        )
        || response.observed_at.is_none() && response.state != "unknown"
    {
        return Err(protocol_invalid());
    }
    if let Some(timestamp) = response.observed_at.as_deref() {
        require_bounded_timestamp(timestamp)?;
    }
    Ok(PlatformSessionStatus {
        platform: response.platform,
        state: response.state,
        observed_at: response.observed_at,
    })
}

fn parse_douyin_platform_session_logout_prepare(body: &[u8]) -> Result<u64, ControlPlaneError> {
    let response: PlatformSessionLogoutPrepareResponse = parse_exact_json(body)?;
    if response.platform != "douyin"
        || response.state != "blocked"
        || response.session_revision == 0
        || response.session_revision > MAX_CROSS_RUNTIME_SEQUENCE
    {
        return Err(protocol_invalid());
    }
    Ok(response.session_revision)
}

fn parse_task_snapshot_body(body: &[u8]) -> Result<TaskSnapshot, ControlPlaneError> {
    parse_task_snapshot(parse_exact_json(body)?)
}

fn parse_created_task(body: &[u8]) -> Result<TaskSnapshot, ControlPlaneError> {
    let snapshot = parse_task_snapshot_body(body)?;
    if snapshot.status != "draft" || snapshot.revision != 1 || snapshot.last_event_sequence != 0 {
        return Err(protocol_invalid());
    }
    Ok(snapshot)
}

fn parse_task_control(
    body: &[u8],
    operation: ControlPlaneOperation,
    expected_task_id: &str,
) -> Result<TaskControlCommand, ControlPlaneError> {
    let response: TaskControlResponse = parse_exact_json(body)?;
    require_canonical_uuid_v4(&response.command_id)?;
    require_canonical_uuid_v4(&response.task_id)?;
    require_canonical_uuid_v4(&response.execution_attempt_id)?;
    let expected_type = match operation {
        ControlPlaneOperation::PauseTask => "task.pause",
        ControlPlaneOperation::ResumeTask => "task.resume",
        ControlPlaneOperation::CancelTask => "task.cancel",
        ControlPlaneOperation::EmergencyStopTask => "task.emergency_stop",
        _ => return Err(protocol_invalid()),
    };
    let created_at = require_bounded_timestamp(&response.created_at)?;
    let deadline_at = require_bounded_timestamp(&response.deadline_at)?;
    if response.task_id != expected_task_id
        || response.sequence == 0
        || response.sequence > MAX_CROSS_RUNTIME_SEQUENCE
        || response.command_type != expected_type
        || !matches!(
            response.status.as_str(),
            "pending" | "in_flight" | "delivered" | "acknowledged" | "expired"
        )
        || response.revision == 0
        || response.revision > MAX_CROSS_RUNTIME_SEQUENCE
        || deadline_at <= created_at
    {
        return Err(protocol_invalid());
    }
    Ok(TaskControlCommand {
        command_id: response.command_id,
        task_id: response.task_id,
        execution_attempt_id: response.execution_attempt_id,
        sequence: response.sequence,
        command_type: response.command_type,
        status: response.status,
    })
}

fn parse_task_discovery(
    body: &[u8],
    expected_task_id: &str,
) -> Result<TaskDiscoveryCommand, ControlPlaneError> {
    let response: TaskDiscoveryResponse = parse_exact_json(body)?;
    require_canonical_uuid_v4(&response.task_id)?;
    require_canonical_uuid_v4(&response.command_id)?;
    require_canonical_uuid_v4(&response.execution_attempt_id)?;
    let created_at = require_bounded_timestamp(&response.created_at)?;
    let deadline_at = require_bounded_timestamp(&response.deadline_at)?;
    if response.task_id != expected_task_id
        || !matches!(
            response.task_status.as_str(),
            "draft"
                | "validating"
                | "awaiting_device"
                | "awaiting_platform_login"
                | "discovering_targets"
                | "awaiting_confirmation"
                | "queued"
                | "running"
                | "paused"
                | "awaiting_human"
                | "cancelling"
                | "succeeded"
                | "partially_succeeded"
                | "failed"
                | "cancelled"
                | "outcome_uncertain"
        )
        || response.task_revision == 0
        || response.task_revision > MAX_CROSS_RUNTIME_SEQUENCE
        || response.last_event_sequence == 0
        || response.last_event_sequence > MAX_CROSS_RUNTIME_SEQUENCE
        || !matches!(
            response.command_status.as_str(),
            "pending" | "in_flight" | "delivered" | "acknowledged" | "rejected" | "expired"
        )
        || deadline_at <= created_at
    {
        return Err(protocol_invalid());
    }
    Ok(TaskDiscoveryCommand {
        task_id: response.task_id,
        task_status: response.task_status,
        task_revision: response.task_revision,
        last_event_sequence: response.last_event_sequence,
        command_id: response.command_id,
        execution_attempt_id: response.execution_attempt_id,
        command_status: response.command_status,
    })
}

fn parse_task_target_preview(
    body: &[u8],
    expected_task_id: &str,
    require_complete: bool,
) -> Result<TaskTargetPreview, ControlPlaneError> {
    let response: TaskTargetPreviewResponse = parse_exact_json(body)?;
    require_canonical_uuid_v4(&response.task_id)?;
    let confirmed_at = response
        .confirmed_at
        .as_deref()
        .map(require_bounded_timestamp)
        .transpose()?;
    let message_valid = match (response.action, response.message_template.as_deref()) {
        (DouyinSearchExposureAction::Browse, None) => true,
        (DouyinSearchExposureAction::Comment, Some(message))
        | (DouyinSearchExposureAction::DirectMessage, Some(message)) => {
            require_action_message_template(message).is_ok()
        }
        _ => false,
    };
    if response.task_id != expected_task_id
        || !valid_task_status(&response.task_status)
        || response.task_revision == 0
        || response.task_revision > MAX_CROSS_RUNTIME_SEQUENCE
        || response.confirmation_revision == 0
        || response.confirmation_revision > MAX_CROSS_RUNTIME_SEQUENCE
        || response.last_event_sequence == 0
        || response.last_event_sequence > MAX_CROSS_RUNTIME_SEQUENCE
        || response.page_revision == 0
        || response.page_revision > MAX_CROSS_RUNTIME_SEQUENCE
        || response.selected_target_count > MAX_TASK_TARGET_LIMIT
        || response.user_excluded_target_count > MAX_TASK_TARGET_LIMIT
        || response.selected_target_count + response.user_excluded_target_count
            > MAX_TASK_TARGET_LIMIT
        || response.items.len() > MAX_TASK_TARGET_LIMIT as usize
        || response.items.is_empty() && response.next_cursor.is_some()
        || response
            .next_cursor
            .as_deref()
            .is_some_and(|cursor| require_preview_cursor(cursor).is_err())
        || require_complete && response.next_cursor.is_some()
        || response.confirmed != confirmed_at.is_some()
        || !message_valid
        || (!response.confirmed && response.task_status != "awaiting_confirmation")
        || (!response.confirmed && response.confirmation_revision != response.task_revision)
        || (response.confirmed
            && matches!(
                response.task_status.as_str(),
                "draft" | "validating" | "discovering_targets" | "awaiting_confirmation"
            ))
        || response.confirmed && response.selected_target_count == 0
        || response.confirmed && response.confirmation_revision >= response.task_revision
    {
        return Err(protocol_invalid());
    }
    let mut seen = HashSet::with_capacity(response.items.len());
    let mut previous: Option<(u16, String)> = None;
    let mut computed_selected = 0_u16;
    let mut computed_excluded = 0_u16;
    let mut items = Vec::with_capacity(response.items.len());
    for item in response.items {
        require_canonical_uuid_v4(&item.target_id)?;
        require_safe_exact_text(&item.display_name, MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS)?;
        if let Some(handle) = item.public_handle.as_deref() {
            let mut bytes = handle.bytes();
            if handle.len() > MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS
                || !bytes
                    .next()
                    .is_some_and(|byte| byte.is_ascii_alphanumeric())
                || !bytes
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b'-'))
            {
                return Err(protocol_invalid());
            }
        }
        let eligible = item.disposition == "eligible";
        if item.ordinal == 0
            || item.ordinal > MAX_TASK_TARGET_LIMIT
            || item.source != "general_search_author"
            || !matches!(
                item.disposition.as_str(),
                "eligible" | "duplicate_in_task" | "duplicate_in_history" | "blacklisted"
            )
            || item.user_excluded && !eligible
            || item.selected != (eligible && !item.user_excluded)
            || !seen.insert(item.target_id.clone())
            || previous.as_ref().is_some_and(|value| {
                (item.ordinal, item.target_id.as_str()) <= (value.0, value.1.as_str())
            })
        {
            return Err(protocol_invalid());
        }
        computed_selected += u16::from(item.selected);
        computed_excluded += u16::from(item.user_excluded);
        previous = Some((item.ordinal, item.target_id.clone()));
        items.push(TaskTargetPreviewItem {
            target_id: item.target_id,
            ordinal: item.ordinal,
            display_name: item.display_name,
            public_handle: item.public_handle,
            source: item.source,
            disposition: item.disposition,
            user_excluded: item.user_excluded,
            selected: item.selected,
        });
    }
    if require_complete
        && (computed_selected != response.selected_target_count
            || computed_excluded != response.user_excluded_target_count)
    {
        return Err(protocol_invalid());
    }
    Ok(TaskTargetPreview {
        task_id: response.task_id,
        task_status: response.task_status,
        task_revision: response.task_revision,
        confirmation_revision: response.confirmation_revision,
        last_event_sequence: response.last_event_sequence,
        page_revision: response.page_revision,
        action: response.action,
        message_template: response.message_template,
        selected_target_count: response.selected_target_count,
        user_excluded_target_count: response.user_excluded_target_count,
        confirmed: response.confirmed,
        confirmed_at: response.confirmed_at,
        items,
        next_cursor: response.next_cursor,
    })
}

fn target_result_evidence_matches(
    result_status: &str,
    evidence: &str,
    action_present: bool,
) -> bool {
    let evidence_matches = match result_status {
        "pending" => matches!(evidence, "awaiting_execution" | "action_pending"),
        "running" => evidence == "action_in_progress",
        "succeeded" => matches!(
            evidence,
            "profile_visible"
                | "comment_confirmed"
                | "message_confirmed"
                | "executor_reported_success"
        ),
        "skipped" => matches!(
            evidence,
            "user_excluded"
                | "duplicate_in_task"
                | "duplicate_in_history"
                | "blacklisted"
                | "action_cancelled"
        ),
        "failed" => matches!(
            evidence,
            "admission_rejected"
                | "local_safety_limit"
                | "login_required"
                | "dialog_blocked"
                | "messaging_not_allowed"
                | "follow_required"
                | "timed_out"
                | "page_version_unknown"
                | "conflicting_anchors"
                | "page_unavailable"
                | "verification_unavailable"
                | "executor_reported_failure"
        ),
        "outcome_uncertain" => matches!(
            evidence,
            "dispatch_timed_out"
                | "dispatch_unavailable"
                | "final_state_unconfirmed"
                | "recovery_unconfirmed"
        ),
        _ => false,
    };
    let action_expected = matches!(
        result_status,
        "running" | "succeeded" | "failed" | "outcome_uncertain"
    ) || result_status == "pending" && evidence == "action_pending"
        || result_status == "skipped" && evidence == "action_cancelled";
    evidence_matches && action_present == action_expected
}

fn parse_task_target_results(
    body: &[u8],
    expected_task_id: &str,
) -> Result<TaskTargetResults, ControlPlaneError> {
    let response: TaskTargetResultsResponse = parse_exact_json(body)?;
    require_canonical_uuid_v4(&response.task_id)?;
    if response.task_id != expected_task_id
        || !valid_task_status(&response.task_status)
        || response.task_revision == 0
        || response.task_revision > MAX_CROSS_RUNTIME_SEQUENCE
        || response.last_event_sequence > response.task_revision
        || response.items.len() > MAX_TASK_TARGET_LIMIT as usize
    {
        return Err(protocol_invalid());
    }
    let mut seen = HashSet::with_capacity(response.items.len());
    let mut previous_ordinal = 0_u16;
    let mut items = Vec::with_capacity(response.items.len());
    for item in response.items {
        require_canonical_uuid_v4(&item.target_id)?;
        require_safe_exact_text(&item.display_name, MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS)?;
        if let Some(handle) = item.public_handle.as_deref() {
            let mut bytes = handle.bytes();
            if handle.len() > MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS
                || !bytes
                    .next()
                    .is_some_and(|byte| byte.is_ascii_alphanumeric())
                || !bytes
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b'-'))
            {
                return Err(protocol_invalid());
            }
        }
        if let Some(action_id) = item.action_id.as_deref() {
            require_canonical_uuid_v4(action_id)?;
        }
        require_bounded_timestamp(&item.updated_at)?;
        if item.ordinal == 0
            || item.ordinal > MAX_TASK_TARGET_LIMIT
            || item.ordinal <= previous_ordinal
            || !seen.insert(item.target_id.clone())
            || !target_result_evidence_matches(
                &item.result_status,
                &item.evidence,
                item.action_id.is_some(),
            )
        {
            return Err(protocol_invalid());
        }
        previous_ordinal = item.ordinal;
        items.push(TaskTargetResultItem {
            target_id: item.target_id,
            ordinal: item.ordinal,
            display_name: item.display_name,
            public_handle: item.public_handle,
            result_status: item.result_status,
            evidence: item.evidence,
            action_id: item.action_id,
            updated_at: item.updated_at,
        });
    }
    Ok(TaskTargetResults {
        task_id: response.task_id,
        task_status: response.task_status,
        task_revision: response.task_revision,
        last_event_sequence: response.last_event_sequence,
        items,
    })
}

fn parse_task_list(body: &[u8]) -> Result<TaskListPage, ControlPlaneError> {
    let response: TaskListResponse = parse_exact_json(body)?;
    if response.items.len() > 100
        || response.items.is_empty() && response.next_cursor.is_some()
        || response
            .next_cursor
            .as_deref()
            .is_some_and(|cursor| require_list_cursor(cursor).is_err())
    {
        return Err(protocol_invalid());
    }
    let items = response
        .items
        .into_iter()
        .map(parse_task_snapshot)
        .collect::<Result<Vec<_>, _>>()?;
    for pair in items.windows(2) {
        let previous = &pair[0];
        let current = &pair[1];
        if previous.updated_at_value < current.updated_at_value
            || previous.updated_at_value == current.updated_at_value
                && previous.task_id.as_str() <= current.task_id.as_str()
        {
            return Err(protocol_invalid());
        }
    }
    Ok(TaskListPage {
        items,
        next_cursor: response.next_cursor,
    })
}

pub struct DemoBootstrap {
    token: Zeroizing<String>,
    environment_id: String,
}

impl DemoBootstrap {
    pub fn new(token: String, environment_id: String) -> Result<Self, ControlPlaneError> {
        let valid_environment = !environment_id.is_empty()
            && environment_id.len() <= 64
            && !environment_id.starts_with('-')
            && !environment_id.ends_with('-')
            && environment_id
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-');
        let token_segments = token.split('.').collect::<Vec<_>>();
        let valid_token = token.len() <= 4096
            && token_segments.len() == 3
            && token_segments[0] == "atb1"
            && token_segments[1..].iter().all(|segment| {
                !segment.is_empty()
                    && segment
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
            });
        if !valid_environment || !valid_token {
            return Err(ControlPlaneError::new(
                ControlPlaneErrorCode::ProtocolInvalid,
                false,
            ));
        }
        Ok(Self {
            token: Zeroizing::new(token),
            environment_id,
        })
    }

    fn token(&self) -> &str {
        self.token.as_str()
    }

    fn environment_id(&self) -> &str {
        &self.environment_id
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RegistrationChallengeRequest<'a> {
    environment_id: &'a str,
    device_public_key: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct AccountBindingChallengeRequest {
    device_public_key: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RegistrationChallengeResponse {
    challenge_id: String,
    signing_payload: String,
    expires_at: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct InstallationRegistrationRequest<'a> {
    challenge_id: String,
    environment_id: &'a str,
    signing_payload: String,
    signature: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct AccountInstallationBindingRequest {
    challenge_id: String,
    signing_payload: String,
    signature: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct InstallationRegistrationResponse {
    installation_id: String,
    status: String,
    revision: u32,
    device_credential: IssuedCredentialResponse,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct IssuedCredentialResponse {
    credential: String,
    version: u32,
    scope: String,
}

pub struct InstallationRegistration {
    installation_id: String,
    credential_version: u32,
}

impl InstallationRegistration {
    pub fn installation_id(&self) -> &str {
        &self.installation_id
    }

    pub fn credential_version(&self) -> u32 {
        self.credential_version
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RotatedCredentialResponse {
    credential: String,
    version: u32,
    scope: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RevokedCredentialResponse {
    version: u32,
    status: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub enum DeviceSessionCapability {
    #[serde(rename = "app.control-plane")]
    AppControlPlane,
    #[serde(rename = "executor.connect")]
    ExecutorConnect,
}

impl DeviceSessionCapability {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::AppControlPlane => "app.control-plane",
            Self::ExecutorConnect => "executor.connect",
        }
    }
}

#[derive(Serialize)]
struct DeviceSessionRequest {
    capability: DeviceSessionCapability,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DeviceSessionResponse {
    session_token: String,
    capability: DeviceSessionCapability,
    issued_at: String,
    expires_at: String,
}

pub struct DeviceSession {
    token: Zeroizing<String>,
    capability: DeviceSessionCapability,
}

impl DeviceSession {
    pub fn token(&self) -> &str {
        self.token.as_str()
    }

    pub fn capability(&self) -> DeviceSessionCapability {
        self.capability
    }

    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    fn into_token(self) -> Zeroizing<String> {
        self.token
    }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
pub(crate) struct ExecutorConnectionMaterial {
    websocket_url: String,
    session_token: Zeroizing<String>,
    installation_id: String,
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
impl ExecutorConnectionMaterial {
    pub(crate) fn into_parts(self) -> (String, Zeroizing<String>, String) {
        (self.websocket_url, self.session_token, self.installation_id)
    }
}

fn parse_registration_challenge(
    body: &[u8],
) -> Result<RegistrationChallengeResponse, ControlPlaneError> {
    let response: RegistrationChallengeResponse = parse_exact_json(body)?;
    require_canonical_uuid_v4(&response.challenge_id)?;
    decode_canonical_base64url(&response.signing_payload, 1, 2048)?;
    require_bounded_timestamp(&response.expires_at)?;
    Ok(response)
}

fn parse_installation_registration(
    body: &[u8],
) -> Result<InstallationRegistrationResponse, ControlPlaneError> {
    let response: InstallationRegistrationResponse = parse_exact_json(body)?;
    require_canonical_uuid_v4(&response.installation_id)?;
    if response.status != "active"
        || response.revision != 1
        || response.device_credential.version != 1
        || response.device_credential.scope != "device.session.exchange"
    {
        return Err(protocol_invalid());
    }
    require_opaque_bearer(&response.device_credential.credential, "atdc1")?;
    Ok(response)
}

fn parse_account_installation_binding(
    body: &[u8],
) -> Result<InstallationRegistrationResponse, ControlPlaneError> {
    let response: InstallationRegistrationResponse = parse_exact_json(body)?;
    require_canonical_uuid_v4(&response.installation_id)?;
    if response.status != "active"
        || response.revision == 0
        || response.device_credential.version == 0
        || response.device_credential.scope != "device.session.exchange"
    {
        return Err(protocol_invalid());
    }
    require_opaque_bearer(&response.device_credential.credential, "atdc1")?;
    Ok(response)
}

fn parse_rotated_credential(body: &[u8]) -> Result<RotatedCredentialResponse, ControlPlaneError> {
    let response: RotatedCredentialResponse = parse_exact_json(body)?;
    if response.version < 2 || response.scope != "device.session.exchange" {
        return Err(protocol_invalid());
    }
    require_opaque_bearer(&response.credential, "atdc1")?;
    Ok(response)
}

fn parse_revoked_credential(body: &[u8]) -> Result<RevokedCredentialResponse, ControlPlaneError> {
    let response: RevokedCredentialResponse = parse_exact_json(body)?;
    if response.version == 0 || response.status != "revoked" {
        return Err(protocol_invalid());
    }
    Ok(response)
}

fn parse_device_session(
    body: &[u8],
    expected_capability: DeviceSessionCapability,
) -> Result<DeviceSession, ControlPlaneError> {
    let response: DeviceSessionResponse = parse_exact_json(body)?;
    if response.capability != expected_capability {
        return Err(protocol_invalid());
    }
    require_opaque_bearer(&response.session_token, "atds1")?;
    let issued_at = require_bounded_timestamp(&response.issued_at)?;
    let expires_at = require_bounded_timestamp(&response.expires_at)?;
    let lifetime = expires_at - issued_at;
    if lifetime <= TimeDuration::ZERO || lifetime > TimeDuration::minutes(5) {
        return Err(protocol_invalid());
    }
    Ok(DeviceSession {
        token: Zeroizing::new(response.session_token),
        capability: response.capability,
    })
}

fn editing_text_has_forbidden_character(value: &str, allow_layout_whitespace: bool) -> bool {
    static UNICODE_OTHER: OnceLock<Regex> = OnceLock::new();
    UNICODE_OTHER
        .get_or_init(|| Regex::new(r"\p{C}").expect("Unicode Other regex is valid"))
        .find_iter(value)
        .any(|matched| !allow_layout_whitespace || !matches!(matched.as_str(), "\n" | "\t"))
}

fn validate_editing_title(value: &str) -> Result<(), ControlPlaneError> {
    if value.is_empty()
        || value.trim() != value
        || value.chars().count() > 200
        || editing_text_has_forbidden_character(value, false)
    {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn validate_editing_local_id(value: &str) -> Result<(), ControlPlaneError> {
    let mut bytes = value.bytes();
    if value.len() > 64
        || !bytes.next().is_some_and(|byte| byte.is_ascii_lowercase())
        || !bytes.all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
    {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn validate_editing_page(cursor: Option<&str>, limit: u16) -> Result<(), ControlPlaneError> {
    if !(1..=100).contains(&limit) {
        return Err(protocol_invalid());
    }
    if let Some(value) = cursor {
        require_list_cursor(value)?;
    }
    Ok(())
}

fn validate_editing_material_description(value: &str) -> Result<(), ControlPlaneError> {
    if value.is_empty()
        || value.trim() != value
        || value.chars().count() > 2_000
        || editing_text_has_forbidden_character(value, true)
    {
        return Err(protocol_invalid());
    }
    Ok(())
}

impl EditingOutputSpec {
    fn validate(&self) -> Result<(), ControlPlaneError> {
        if !(128..=4096).contains(&self.width)
            || !(128..=4096).contains(&self.height)
            || !self.width.is_multiple_of(2)
            || !self.height.is_multiple_of(2)
            || !(12..=60).contains(&self.fps)
        {
            return Err(protocol_invalid());
        }
        Ok(())
    }
}

impl EditingCaptionStyle {
    fn validate(&self) -> Result<(), ControlPlaneError> {
        validate_editing_local_id(&self.font_key)?;
        if !(12..=200).contains(&self.font_px)
            || self.stroke_px > 20
            || u16::from(self.stroke_px) * 2 >= self.font_px
            || !self.line_spacing.is_finite()
            || !(1.0..=3.0).contains(&self.line_spacing)
        {
            return Err(protocol_invalid());
        }
        Ok(())
    }
}

impl EditingProjectCreateRequest {
    fn validate(&self) -> Result<(), ControlPlaneError> {
        validate_editing_title(&self.title)?;
        self.output.validate()?;
        self.caption_style.validate()?;
        if self.caption_style.font_px > self.output.height {
            return Err(protocol_invalid());
        }
        Ok(())
    }
}

impl EditingProjectSnapshot {
    fn validate(&self) -> Result<(), ControlPlaneError> {
        require_canonical_uuid_v4(&self.project_id)?;
        EditingProjectCreateRequest {
            title: self.title.clone(),
            output: self.output.clone(),
            caption_style: self.caption_style.clone(),
        }
        .validate()?;
        require_bounded_timestamp(&self.created_at)?;
        Ok(())
    }
}

impl EditingProjectSnapshot {
    pub(crate) fn project_id(&self) -> &str {
        &self.project_id
    }
}

impl EditingProjectListPage {
    pub(crate) fn items(&self) -> &[EditingProjectSnapshot] {
        &self.items
    }

    pub(crate) fn next_cursor(&self) -> Option<&str> {
        self.next_cursor.as_deref()
    }
}

#[cfg_attr(
    all(feature = "desktop-e2e", not(feature = "control-plane-e2e")),
    allow(dead_code)
)]
impl EditingMaterialRegistrationRequest {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        material_id: &str,
        kind: EditingMaterialKind,
        duration_ms: Option<u64>,
        width: Option<u16>,
        height: Option<u16>,
        content_digest: &str,
        has_audio: bool,
        audio_loudness_lufs: Option<f64>,
    ) -> Result<Self, ControlPlaneError> {
        let request = Self {
            material_id: material_id.to_owned(),
            kind,
            duration_ms,
            width,
            height,
            content_digest: content_digest.to_owned(),
            has_audio,
            audio_loudness_lufs,
            has_speech: false,
            speech_segments_ms: Vec::new(),
            speech_transcript: None,
            shot_boundaries_ms: Vec::new(),
            ai_description: None,
            ai_tags: Vec::new(),
            description_source: "ai",
            described_at: None,
        };
        request.validate()?;
        Ok(request)
    }

    fn validate(&self) -> Result<(), ControlPlaneError> {
        require_canonical_uuid_v4(&self.material_id)?;
        let digest_is_valid = self.content_digest.len() == 64
            && self
                .content_digest
                .bytes()
                .all(|value| value.is_ascii_digit() || (b'a'..=b'f').contains(&value));
        let duration_is_valid = match self.kind {
            EditingMaterialKind::Image => self.duration_ms.is_none(),
            EditingMaterialKind::Video | EditingMaterialKind::Audio => self
                .duration_ms
                .is_some_and(|value| (1..=MAX_EDITING_MATERIAL_DURATION_MS).contains(&value)),
        };
        let dimensions_are_valid = match self.kind {
            EditingMaterialKind::Audio => {
                self.width.is_none() && self.height.is_none() && self.has_audio
            }
            EditingMaterialKind::Image | EditingMaterialKind::Video => {
                self.width.zip(self.height).is_some_and(|(width, height)| {
                    (1..=8192).contains(&width) && (1..=8192).contains(&height)
                })
            }
        };
        let loudness_is_valid = self.audio_loudness_lufs.is_none_or(|value| {
            self.has_audio && value.is_finite() && (-70.0..=0.0).contains(&value)
        });
        if !digest_is_valid
            || !duration_is_valid
            || !dimensions_are_valid
            || !loudness_is_valid
            || self.kind == EditingMaterialKind::Image && self.has_audio
            || self.has_speech
            || !self.speech_segments_ms.is_empty()
            || self.speech_transcript.is_some()
            || !self.shot_boundaries_ms.is_empty()
            || self.ai_description.is_some()
            || !self.ai_tags.is_empty()
            || self.description_source != "ai"
            || self.described_at.is_some()
        {
            return Err(protocol_invalid());
        }
        Ok(())
    }
}

fn valid_editing_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

impl SmartEditMaterialAnalysisRequest {
    fn validate(&self) -> Result<(), ControlPlaneError> {
        require_canonical_uuid_v4(&self.material_id)?;
        if !valid_editing_digest(&self.content_digest)
            || self.speech_segments_ms.len() > 4096
            || self.shot_boundaries_ms.len() > 4096
            || self
                .speech_segments_ms
                .iter()
                .any(|(start, end)| start >= end)
            || self
                .speech_segments_ms
                .windows(2)
                .any(|values| values[0].1 > values[1].0)
            || self
                .shot_boundaries_ms
                .windows(2)
                .any(|values| values[0] >= values[1])
            || self.has_speech
                != (!self.speech_segments_ms.is_empty() && self.speech_transcript.is_some())
            || self.speech_transcript.as_ref().is_some_and(|value| {
                value.is_empty()
                    || value.trim() != value
                    || value.chars().count() > 100_000
                    || editing_text_has_forbidden_character(value, true)
            })
            || self.ai_description.as_ref().is_some_and(|value| {
                value.is_empty()
                    || value.trim() != value
                    || value.chars().count() > 2_000
                    || editing_text_has_forbidden_character(value, true)
            })
            || self.ai_tags.len() > 32
            || self.ai_tags.iter().any(|value| {
                value.is_empty()
                    || value.trim() != value
                    || value.chars().count() > 32
                    || editing_text_has_forbidden_character(value, true)
            })
            || self.ai_tags.iter().collect::<HashSet<_>>().len() != self.ai_tags.len()
            || !matches!(self.description_source.as_str(), "ai" | "user")
            || self
                .described_at
                .as_deref()
                .is_some_and(|value| require_bounded_timestamp(value).is_err())
            || self.description_source == "user"
                && (self.ai_description.is_none()
                    || !self.ai_tags.is_empty()
                    || self.described_at.is_some())
            || self.description_source == "ai"
                && (self.ai_description.is_some() != self.described_at.is_some()
                    || self.ai_description.is_none() && !self.ai_tags.is_empty())
        {
            return Err(protocol_invalid());
        }
        Ok(())
    }
}

impl SmartEditNarrationMaterialRequest {
    fn validate(&self) -> Result<(), ControlPlaneError> {
        require_canonical_uuid_v4(&self.material_id)?;
        if !valid_editing_digest(&self.content_digest)
            || !(1..=MAX_EDITING_MATERIAL_DURATION_MS).contains(&self.duration_ms)
            || self.speech_transcript.is_empty()
            || self.speech_transcript.trim() != self.speech_transcript
            || self.speech_transcript.chars().count() > 100_000
            || editing_text_has_forbidden_character(&self.speech_transcript, true)
        {
            return Err(protocol_invalid());
        }
        Ok(())
    }
}

impl SmartEditMaterialWritebackRequest {
    pub(crate) fn validate(&self) -> Result<(), ControlPlaneError> {
        if self.analyses.len() > 32
            || self.narrations.len() > 32
            || self.analyses.is_empty() && self.narrations.is_empty()
        {
            return Err(protocol_invalid());
        }
        for value in &self.analyses {
            value.validate()?;
        }
        for value in &self.narrations {
            value.validate()?;
        }
        let mut identifiers = HashSet::new();
        let mut digests = HashSet::new();
        if self
            .analyses
            .iter()
            .map(|value| (&value.material_id, &value.content_digest))
            .chain(
                self.narrations
                    .iter()
                    .map(|value| (&value.material_id, &value.content_digest)),
            )
            .any(|(identifier, digest)| {
                !identifiers.insert(identifier.as_str()) || !digests.insert(digest.as_str())
            })
        {
            return Err(protocol_invalid());
        }
        Ok(())
    }

    fn expected_materials(&self) -> Vec<(&str, &str)> {
        self.analyses
            .iter()
            .map(|value| (value.material_id.as_str(), value.content_digest.as_str()))
            .chain(
                self.narrations
                    .iter()
                    .map(|value| (value.material_id.as_str(), value.content_digest.as_str())),
            )
            .collect()
    }
}

impl EditingMaterialSnapshot {
    fn validate(&self) -> Result<(), ControlPlaneError> {
        require_canonical_uuid_v4(&self.material_id)?;
        let digest_is_valid = self.content_digest.len() == 64
            && self
                .content_digest
                .bytes()
                .all(|value| value.is_ascii_digit() || (b'a'..=b'f').contains(&value));
        if !digest_is_valid
            || self
                .duration_ms
                .is_some_and(|value| value == 0 || value > MAX_EDITING_MATERIAL_DURATION_MS)
            || self.width.is_some_and(|value| value == 0)
            || self.width.is_some_and(|value| value > 8192)
            || self.height.is_some_and(|value| value == 0 || value > 8192)
            || self
                .audio_loudness_lufs
                .is_some_and(|value| !value.is_finite() || !(-70.0..=0.0).contains(&value))
            || !self.has_audio && self.audio_loudness_lufs.is_some()
            || self.speech_segments_ms.len() > 4096
            || self.shot_boundaries_ms.len() > 4096
            || self.speech_transcript.as_ref().is_some_and(|value| {
                value.is_empty()
                    || value.trim() != value
                    || value.chars().count() > 100_000
                    || editing_text_has_forbidden_character(value, true)
            })
            || self.ai_description.as_ref().is_some_and(|value| {
                value.is_empty()
                    || value.trim() != value
                    || value.chars().count() > 2_000
                    || editing_text_has_forbidden_character(value, true)
            })
            || self.ai_tags.len() > 32
            || self.ai_tags.iter().any(|value| {
                value.is_empty()
                    || value.trim() != value
                    || value.chars().count() > 32
                    || editing_text_has_forbidden_character(value, true)
            })
            || self
                .ai_tags
                .iter()
                .collect::<std::collections::HashSet<_>>()
                .len()
                != self.ai_tags.len()
            || self
                .described_at
                .as_deref()
                .is_some_and(|value| require_bounded_timestamp(value).is_err())
        {
            return Err(protocol_invalid());
        }
        let shape_matches = match self.kind {
            EditingMaterialKind::Image => {
                self.duration_ms.is_none()
                    && self.width.is_some()
                    && self.height.is_some()
                    && !self.has_audio
            }
            EditingMaterialKind::Video => {
                self.duration_ms.is_some() && self.width.is_some() && self.height.is_some()
            }
            EditingMaterialKind::Audio => {
                self.duration_ms.is_some()
                    && self.width.is_none()
                    && self.height.is_none()
                    && self.has_audio
            }
        };
        if !shape_matches
            || self.has_speech && !self.has_audio
            || (!self.has_speech
                && (!self.speech_segments_ms.is_empty() || self.speech_transcript.is_some()))
            || (self.has_speech
                && (self.speech_segments_ms.is_empty() || self.speech_transcript.is_none()))
            || !matches!(self.description_source.as_str(), "ai" | "user")
            || (self.description_source == "user"
                && (self.ai_description.is_none()
                    || !self.ai_tags.is_empty()
                    || self.described_at.is_some()))
            || (self.ai_description.is_none()
                && (!self.ai_tags.is_empty() || self.described_at.is_some()))
            || (self.description_source == "ai"
                && self.ai_description.is_some()
                && self.described_at.is_none())
        {
            return Err(protocol_invalid());
        }
        let mut previous_speech_end = 0;
        for &(start, end) in &self.speech_segments_ms {
            if start < previous_speech_end
                || end <= start
                || self.duration_ms.is_some_and(|duration| end > duration)
            {
                return Err(protocol_invalid());
            }
            previous_speech_end = end;
        }
        if self.kind == EditingMaterialKind::Audio && !self.shot_boundaries_ms.is_empty() {
            return Err(protocol_invalid());
        }
        let mut previous_boundary = None;
        for &boundary in &self.shot_boundaries_ms {
            if previous_boundary.is_some_and(|previous| boundary <= previous)
                || self.duration_ms.is_none_or(|duration| boundary >= duration)
            {
                return Err(protocol_invalid());
            }
            previous_boundary = Some(boundary);
        }
        Ok(())
    }

    pub(crate) fn material_id(&self) -> &str {
        &self.material_id
    }

    pub(crate) const fn has_audio(&self) -> bool {
        self.has_audio
    }

    #[cfg_attr(
        all(feature = "desktop-e2e", not(feature = "control-plane-e2e")),
        allow(dead_code)
    )]
    pub(crate) fn content_digest(&self) -> &str {
        &self.content_digest
    }

    pub(crate) fn matches_smart_edit_narration(
        &self,
        expected: &SmartEditNarrationMaterialRequest,
    ) -> bool {
        self.validate().is_ok()
            && self.material_id == expected.material_id
            && self.kind == EditingMaterialKind::Audio
            && self.duration_ms == Some(expected.duration_ms)
            && self.width.is_none()
            && self.height.is_none()
            && self.content_digest == expected.content_digest
            && self.has_audio
            && self.audio_loudness_lufs.is_none()
            && self.has_speech
            && self.speech_segments_ms == [(0, expected.duration_ms)]
            && self.speech_transcript.as_deref() == Some(expected.speech_transcript.as_str())
            && self.shot_boundaries_ms.is_empty()
            && self.ai_description.is_none()
            && self.ai_tags.is_empty()
            && self.description_source == "ai"
            && self.described_at.is_none()
    }

    pub(crate) fn worker_document(&self) -> Result<serde_json::Value, ControlPlaneError> {
        self.validate()?;
        serde_json::to_value(self).map_err(|_| protocol_invalid())
    }
}

impl EditingMaterialListPage {
    pub fn items(&self) -> &[EditingMaterialSnapshot] {
        &self.items
    }

    pub fn next_cursor(&self) -> Option<&str> {
        self.next_cursor.as_deref()
    }
}

impl EditingTimelineClip {
    fn validate(&self) -> Result<(), ControlPlaneError> {
        validate_editing_local_id(&self.clip_id)?;
        if self.duration_ms == 0
            || self.duration_ms > MAX_EDITING_TIMELINE_DURATION_MS
            || self.start_ms > MAX_EDITING_TIMELINE_DURATION_MS
            || self.start_ms + self.duration_ms > MAX_EDITING_TIMELINE_DURATION_MS
        {
            return Err(protocol_invalid());
        }
        if let Some(material_id) = self.source_material_id.as_deref() {
            require_canonical_uuid_v4(material_id)?;
        }
        if let Some(text) = self.text.as_deref() {
            if text.is_empty()
                || text.trim() != text
                || text.chars().count() > 2_000
                || editing_text_has_forbidden_character(text, true)
            {
                return Err(protocol_invalid());
            }
        }
        if self.source_material_id.is_none() == self.text.is_none()
            || self.source_in_ms.is_none() != self.source_out_ms.is_none()
        {
            return Err(protocol_invalid());
        }
        match (self.source_in_ms, self.source_out_ms) {
            (Some(source_in), Some(source_out)) => {
                if self.source_material_id.is_none()
                    || source_out > MAX_EDITING_MATERIAL_DURATION_MS
                    || source_out.checked_sub(source_in) != Some(self.duration_ms)
                {
                    return Err(protocol_invalid());
                }
            }
            (None, None) => {}
            _ => return Err(protocol_invalid()),
        }
        if self.gain_db.is_some_and(|gain| {
            self.source_in_ms.is_none() || !gain.is_finite() || !(-60.0..=12.0).contains(&gain)
        }) {
            return Err(protocol_invalid());
        }
        if self.transition_in.as_ref().is_some_and(|transition| {
            transition.duration_ms == 0
                || transition.duration_ms > 10_000
                || transition.duration_ms >= self.duration_ms
        }) {
            return Err(protocol_invalid());
        }
        Ok(())
    }

    fn end_ms(&self) -> u64 {
        self.start_ms + self.duration_ms
    }
}

impl EditingTimelineTrack {
    fn validate(&self) -> Result<(), ControlPlaneError> {
        validate_editing_local_id(&self.track_id)?;
        if self.clips.is_empty() || self.clips.len() > MAX_EDITING_CLIPS_PER_TRACK {
            return Err(protocol_invalid());
        }
        let mut clip_ids = HashSet::new();
        let mut previous_end: u64 = 0;
        let mut previous_tail: u64 = 0;
        for clip in &self.clips {
            clip.validate()?;
            if !clip_ids.insert(&clip.clip_id) {
                return Err(protocol_invalid());
            }
            let shape_matches = match self.kind {
                EditingTrackKind::Caption => {
                    clip.text.is_some()
                        && clip.source_material_id.is_none()
                        && clip.gain_db.is_none()
                        && clip.transition_in.is_none()
                        && clip.original_audio_mode.is_none()
                }
                EditingTrackKind::Visual => {
                    clip.text.is_none()
                        && clip.source_material_id.is_some()
                        && clip.gain_db.is_none()
                        && clip.original_audio_mode.is_none()
                }
                EditingTrackKind::Ambient => {
                    clip.text.is_none()
                        && clip.source_material_id.is_some()
                        && clip.gain_db.is_some()
                        && clip.transition_in.is_none()
                        && clip.original_audio_mode.is_some()
                }
                EditingTrackKind::Narration | EditingTrackKind::Music => {
                    clip.text.is_none()
                        && clip.source_material_id.is_some()
                        && clip.gain_db.is_some()
                        && clip.transition_in.is_none()
                        && clip.original_audio_mode.is_none()
                }
            };
            if !shape_matches {
                return Err(protocol_invalid());
            }
            if self.kind == EditingTrackKind::Visual {
                let overlap = clip
                    .transition_in
                    .as_ref()
                    .map_or(0, |transition| transition.duration_ms);
                if clip.transition_in.is_some() && overlap >= previous_tail
                    || clip.start_ms != previous_end.saturating_sub(overlap)
                {
                    return Err(protocol_invalid());
                }
                previous_tail = clip.duration_ms - overlap;
            } else if clip.start_ms < previous_end {
                return Err(protocol_invalid());
            }
            previous_end = clip.end_ms();
        }
        Ok(())
    }
}

impl EditingTimelineDraft {
    pub(crate) fn from_worker_document(
        mut document: serde_json::Value,
        expected_revision: u64,
    ) -> Result<Self, ControlPlaneError> {
        let object = document.as_object_mut().ok_or_else(protocol_invalid)?;
        if object
            .insert(
                "expectedRevision".to_owned(),
                serde_json::json!(expected_revision),
            )
            .is_some()
        {
            return Err(protocol_invalid());
        }
        let draft: Self = serde_json::from_value(document).map_err(|_| protocol_invalid())?;
        draft.validate()?;
        Ok(draft)
    }

    fn validate(&self) -> Result<(), ControlPlaneError> {
        if !(100..=MAX_EDITING_TIMELINE_DURATION_MS).contains(&self.duration_ms)
            || self.tracks.is_empty()
            || self.tracks.len() > 5
            || self
                .expected_revision
                .is_some_and(|value| value > MAX_CROSS_RUNTIME_SEQUENCE)
        {
            return Err(protocol_invalid());
        }
        let mut track_ids = HashSet::new();
        let mut kinds = HashSet::new();
        let mut visual_end = None;
        for track in &self.tracks {
            track.validate()?;
            if !track_ids.insert(&track.track_id) || !kinds.insert(track.kind) {
                return Err(protocol_invalid());
            }
            if track
                .clips
                .iter()
                .any(|clip| clip.end_ms() > self.duration_ms)
            {
                return Err(protocol_invalid());
            }
            if track.kind == EditingTrackKind::Visual {
                visual_end = track.clips.last().map(EditingTimelineClip::end_ms);
            }
        }
        if visual_end != Some(self.duration_ms) {
            return Err(protocol_invalid());
        }
        Ok(())
    }
}

impl EditingTimelineSnapshot {
    fn validate(&self) -> Result<(), ControlPlaneError> {
        require_canonical_uuid_v4(&self.timeline_id)?;
        require_canonical_uuid_v4(&self.project_id)?;
        if self.revision == 0 || self.revision > MAX_CROSS_RUNTIME_SEQUENCE {
            return Err(protocol_invalid());
        }
        EditingTimelineDraft {
            duration_ms: self.duration_ms,
            tracks: self.tracks.clone(),
            expected_revision: None,
        }
        .validate()?;
        require_bounded_timestamp(&self.created_at)?;
        Ok(())
    }
}

impl EditingTimelineSnapshot {
    pub(crate) fn timeline_id(&self) -> &str {
        &self.timeline_id
    }

    pub(crate) fn project_id(&self) -> &str {
        &self.project_id
    }

    pub(crate) const fn revision(&self) -> u64 {
        self.revision
    }

    pub(crate) fn confirms_saved_draft(
        &self,
        project_id: &str,
        expected_revision: u64,
        draft: &EditingTimelineDraft,
    ) -> bool {
        self.project_id == project_id
            && expected_revision.checked_add(1) == Some(self.revision)
            && self.duration_ms == draft.duration_ms
            && self.tracks == draft.tracks
    }

    pub(crate) fn material_ids(&self) -> Vec<&str> {
        let mut identifiers = self
            .tracks
            .iter()
            .flat_map(|track| track.clips.iter())
            .filter_map(|clip| clip.source_material_id.as_deref())
            .collect::<Vec<_>>();
        identifiers.sort_unstable();
        identifiers.dedup();
        identifiers
    }
}

impl EditingJobSnapshot {
    fn validate(&self) -> Result<(), ControlPlaneError> {
        for identifier in [&self.job_id, &self.project_id, &self.timeline_id] {
            require_canonical_uuid_v4(identifier)?;
        }
        if let Some(artifact_id) = self.output_artifact_id.as_deref() {
            require_canonical_uuid_v4(artifact_id)?;
        }
        if self.timeline_revision == 0 || self.timeline_revision > MAX_CROSS_RUNTIME_SEQUENCE {
            return Err(protocol_invalid());
        }
        let created_at = require_bounded_timestamp(&self.created_at)?;
        let updated_at = require_bounded_timestamp(&self.updated_at)?;
        let facts_match = match self.status {
            EditingJobStatus::Succeeded => {
                self.output_artifact_id.is_some() && self.failure_code.is_none()
            }
            EditingJobStatus::Failed => {
                self.output_artifact_id.is_none() && self.failure_code.is_some()
            }
            _ => self.output_artifact_id.is_none() && self.failure_code.is_none(),
        };
        if updated_at < created_at || !facts_match {
            return Err(protocol_invalid());
        }
        Ok(())
    }
}

impl EditingJobSnapshot {
    pub(crate) fn job_id(&self) -> &str {
        &self.job_id
    }

    pub(crate) fn project_id(&self) -> &str {
        &self.project_id
    }

    pub(crate) fn timeline_id(&self) -> &str {
        &self.timeline_id
    }

    pub(crate) const fn timeline_revision(&self) -> u64 {
        self.timeline_revision
    }

    pub(crate) const fn status(&self) -> EditingJobStatus {
        self.status
    }

    pub(crate) fn output_artifact_id(&self) -> Option<&str> {
        self.output_artifact_id.as_deref()
    }
}

fn parse_editing_project(body: &[u8]) -> Result<EditingProjectSnapshot, ControlPlaneError> {
    let project: EditingProjectSnapshot = parse_exact_json(body)?;
    project.validate()?;
    Ok(project)
}

fn parse_editing_project_list(body: &[u8]) -> Result<EditingProjectListPage, ControlPlaneError> {
    let response: EditingProjectListResponse = parse_exact_json(body)?;
    if response.items.len() > 100 {
        return Err(protocol_invalid());
    }
    for project in &response.items {
        project.validate()?;
    }
    if let Some(cursor) = response.next_cursor.as_deref() {
        require_list_cursor(cursor)?;
    }
    Ok(EditingProjectListPage {
        items: response.items,
        next_cursor: response.next_cursor,
    })
}

fn parse_editing_material_list(body: &[u8]) -> Result<EditingMaterialListPage, ControlPlaneError> {
    let response: EditingMaterialListResponse = parse_exact_json(body)?;
    if response.items.len() > 100 {
        return Err(protocol_invalid());
    }
    let mut identifiers = std::collections::HashSet::with_capacity(response.items.len());
    for material in &response.items {
        material.validate()?;
        if !identifiers.insert(material.material_id.as_str()) {
            return Err(protocol_invalid());
        }
    }
    if let Some(cursor) = response.next_cursor.as_deref() {
        require_list_cursor(cursor)?;
    }
    Ok(EditingMaterialListPage {
        items: response.items,
        next_cursor: response.next_cursor,
    })
}

fn parse_editing_timeline(body: &[u8]) -> Result<EditingTimelineSnapshot, ControlPlaneError> {
    let timeline: EditingTimelineSnapshot = parse_exact_json(body)?;
    timeline.validate()?;
    Ok(timeline)
}

fn parse_editing_job(body: &[u8]) -> Result<EditingJobSnapshot, ControlPlaneError> {
    let job: EditingJobSnapshot = parse_exact_json(body)?;
    job.validate()?;
    Ok(job)
}

fn parse_editing_job_list(body: &[u8]) -> Result<EditingJobListPage, ControlPlaneError> {
    let response: EditingJobListResponse = parse_exact_json(body)?;
    if response.items.len() > 100 {
        return Err(protocol_invalid());
    }
    for job in &response.items {
        job.validate()?;
    }
    if let Some(cursor) = response.next_cursor.as_deref() {
        require_list_cursor(cursor)?;
    }
    Ok(EditingJobListPage {
        items: response.items,
        next_cursor: response.next_cursor,
    })
}

fn parse_exact_json<T>(body: &[u8]) -> Result<T, ControlPlaneError>
where
    T: for<'de> Deserialize<'de>,
{
    serde_json::from_slice(body).map_err(|_| protocol_invalid())
}

fn require_canonical_uuid_v4(value: &str) -> Result<(), ControlPlaneError> {
    let parsed = uuid::Uuid::parse_str(value).map_err(|_| protocol_invalid())?;
    if parsed.get_version_num() != 4
        || parsed.get_variant() != Variant::RFC4122
        || parsed.hyphenated().to_string() != value
    {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn require_content_digest(value: &str) -> Result<(), ControlPlaneError> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn decode_canonical_base64url(
    value: &str,
    minimum_length: usize,
    maximum_length: usize,
) -> Result<Zeroizing<Vec<u8>>, ControlPlaneError> {
    let decoded = URL_SAFE_NO_PAD
        .decode(value)
        .map_err(|_| protocol_invalid())?;
    if decoded.len() < minimum_length
        || decoded.len() > maximum_length
        || URL_SAFE_NO_PAD.encode(&decoded) != value
    {
        return Err(protocol_invalid());
    }
    Ok(Zeroizing::new(decoded))
}

fn require_opaque_bearer(value: &str, expected_prefix: &str) -> Result<(), ControlPlaneError> {
    if value.len() > 256 {
        return Err(protocol_invalid());
    }
    let mut segments = value.split('.');
    let (Some(prefix), Some(identifier), Some(secret), None) = (
        segments.next(),
        segments.next(),
        segments.next(),
        segments.next(),
    ) else {
        return Err(protocol_invalid());
    };
    if prefix != expected_prefix {
        return Err(protocol_invalid());
    }
    require_canonical_uuid_v4(identifier)?;
    let decoded = decode_canonical_base64url(secret, 32, 32)?;
    if decoded.len() != 32 {
        return Err(protocol_invalid());
    }
    Ok(())
}

fn require_bounded_timestamp(value: &str) -> Result<OffsetDateTime, ControlPlaneError> {
    if value.is_empty() || value.len() > 64 {
        return Err(protocol_invalid());
    }
    let parsed = OffsetDateTime::parse(value, &Rfc3339).map_err(|_| protocol_invalid())?;
    if parsed.offset() != UtcOffset::UTC {
        return Err(protocol_invalid());
    }
    Ok(parsed)
}

fn protocol_invalid() -> ControlPlaneError {
    ControlPlaneError::new(ControlPlaneErrorCode::ProtocolInvalid, false)
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;
    use std::collections::{BTreeSet, HashMap, HashSet};
    use std::error::Error;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::thread;

    use base64::engine::general_purpose::URL_SAFE_NO_PAD;
    use base64::Engine;
    use zeroize::Zeroizing;

    use super::{
        new_request_id,
        parse_bilibili_publish_response, parse_created_task, parse_device_session,
        parse_douyin_platform_session, parse_douyin_platform_session_logout_prepare,
        parse_editing_job, parse_editing_material_list, parse_editing_project,
        parse_editing_timeline, parse_health_response, parse_installation_access,
        parse_installation_registration, parse_registration_challenge, parse_revoked_credential,
        parse_rotated_credential, parse_sse_frame, parse_system_version_response,
        parse_task_control, parse_task_discovery, parse_task_list, parse_task_snapshot_body,
        parse_task_target_preview, parse_task_target_results, parse_workbench_metrics,
        parse_workbench_status, request_path, require_idempotency_key, require_list_cursor,
        require_preview_cursor, required_credential, sse_frame_end, transport_error,
        validate_preview_command, validate_response_metadata, validated_demo_origin,
        validated_loopback_origin, BilibiliPublishPhase, ControlPlaneErrorCode,
        ControlPlaneOperation, ControlPlaneRequestTarget, DemoBootstrap, DeviceSessionCapability,
        DouyinSearchExposureAction, DouyinSearchExposureTaskDefinition, EditingJobStatus,
        EditingTimelineDraft, ResponseMetadata, SmartEditNarrationMaterialRequest,
    };
    use crate::device_credentials::DeviceCredentialVault;
    use crate::secure_store::{SecretStore, SecureStoreError};

    const IDENTIFIER: &str = "f831a58a-a54c-4bd9-8f3e-0383c4df609d";

    struct MemorySecretStore {
        value: Option<Vec<u8>>,
        fail_load: Cell<bool>,
    }

    impl MemorySecretStore {
        fn with_value(value: &[u8]) -> Self {
            Self {
                value: Some(value.to_vec()),
                fail_load: Cell::new(false),
            }
        }
    }

    impl SecretStore for MemorySecretStore {
        fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, SecureStoreError> {
            if self.fail_load.get() {
                return Err(SecureStoreError::Unavailable);
            }
            Ok(self.value.clone().map(Zeroizing::new))
        }

        fn save(&self, _secret: &[u8]) -> Result<(), SecureStoreError> {
            Ok(())
        }

        fn delete(&self) -> Result<(), SecureStoreError> {
            Ok(())
        }
    }

    fn opaque_bearer(prefix: &str) -> String {
        format!(
            "{prefix}.{IDENTIFIER}.{}",
            URL_SAFE_NO_PAD.encode([7_u8; 32])
        )
    }

    struct ExpectedHttpExchange {
        method: &'static str,
        path: String,
        authorization: String,
        body: Option<serde_json::Value>,
        status: u16,
        response: serde_json::Value,
    }

    struct CapturedHttpRequest {
        method: String,
        path: String,
        headers: HashMap<String, String>,
        body: Vec<u8>,
    }

    fn read_http_request(stream: &mut TcpStream) -> CapturedHttpRequest {
        let mut bytes = Vec::new();
        let header_end = loop {
            let mut chunk = [0_u8; 1024];
            let count = stream.read(&mut chunk).expect("read HTTP request");
            assert!(count > 0, "HTTP request ended before its headers");
            bytes.extend_from_slice(&chunk[..count]);
            if let Some(position) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
                break position + 4;
            }
            assert!(bytes.len() <= 16 * 1024, "HTTP request headers are bounded");
        };
        let header_text =
            std::str::from_utf8(&bytes[..header_end - 4]).expect("HTTP request headers are UTF-8");
        let mut lines = header_text.split("\r\n");
        let mut request_line = lines.next().expect("HTTP request line").split(' ');
        let method = request_line.next().expect("HTTP method").to_owned();
        let path = request_line.next().expect("HTTP path").to_owned();
        assert_eq!(request_line.next(), Some("HTTP/1.1"));
        assert_eq!(request_line.next(), None);
        let headers: HashMap<String, String> = lines
            .map(|line| {
                let (name, value) = line.split_once(':').expect("HTTP header");
                (name.to_ascii_lowercase(), value.trim().to_owned())
            })
            .collect();
        let content_length = headers
            .get("content-length")
            .map_or(0, |value| value.parse().expect("HTTP content length"));
        while bytes.len() - header_end < content_length {
            let mut chunk = [0_u8; 1024];
            let count = stream.read(&mut chunk).expect("read HTTP body");
            assert!(count > 0, "HTTP request ended before its body");
            bytes.extend_from_slice(&chunk[..count]);
        }
        CapturedHttpRequest {
            method,
            path,
            headers,
            body: bytes[header_end..header_end + content_length].to_vec(),
        }
    }

    fn spawn_http_contract_server(
        exchanges: Vec<ExpectedHttpExchange>,
    ) -> (String, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind loopback HTTP server");
        let origin = format!(
            "http://{}",
            listener.local_addr().expect("loopback server address")
        );
        let handle = thread::spawn(move || {
            for expected in exchanges {
                let (mut stream, _) = listener.accept().expect("accept HTTP request");
                let request = read_http_request(&mut stream);
                assert_eq!(request.method, expected.method);
                assert_eq!(request.path, expected.path);
                assert_eq!(
                    request.headers.get("authorization"),
                    Some(&expected.authorization)
                );
                assert_eq!(
                    request.headers.get("accept").map(String::as_str),
                    Some("application/json")
                );
                match expected.body {
                    Some(expected_body) => assert_eq!(
                        serde_json::from_slice::<serde_json::Value>(&request.body)
                            .expect("request body is JSON"),
                        expected_body
                    ),
                    None => assert!(request.body.is_empty()),
                }
                let request_id = request
                    .headers
                    .get("x-request-id")
                    .expect("request ID header");
                let response_body = if expected.status == 204 {
                    Vec::new()
                } else {
                    serde_json::to_vec(&expected.response).expect("response JSON")
                };
                let status_text = match expected.status {
                    200 => "OK",
                    201 => "Created",
                    204 => "No Content",
                    404 => "Not Found",
                    status => panic!("unsupported test HTTP status {status}"),
                };
                let content_type = if expected.status == 204 {
                    ""
                } else {
                    "content-type: application/json\r\n"
                };
                let response = format!(
                    "HTTP/1.1 {} {}\r\n{}cache-control: no-store\r\nx-request-id: {}\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
                    expected.status,
                    status_text,
                    content_type,
                    request_id,
                    response_body.len()
                );
                stream
                    .write_all(response.as_bytes())
                    .expect("write HTTP response headers");
                stream
                    .write_all(&response_body)
                    .expect("write HTTP response body");
            }
        });
        (origin, handle)
    }

    fn device_session_exchange(
        device_credential: &str,
        session_token: &str,
    ) -> ExpectedHttpExchange {
        ExpectedHttpExchange {
            method: "POST",
            path: "/api/v1/device-sessions".to_owned(),
            authorization: format!("Bearer {device_credential}"),
            body: Some(serde_json::json!({"capability": "app.control-plane"})),
            status: 201,
            response: serde_json::json!({
                "sessionToken": session_token,
                "capability": "app.control-plane",
                "issuedAt": "2026-08-01T00:00:00Z",
                "expiresAt": "2026-08-01T00:05:00Z"
            }),
        }
    }

    fn bootstrap_error(
        result: Result<DemoBootstrap, super::ControlPlaneError>,
    ) -> super::ControlPlaneError {
        match result {
            Err(error) => error,
            Ok(_) => panic!("expected bootstrap input to fail"),
        }
    }

    fn credential_error(
        result: Result<crate::device_credentials::StoredDeviceCredential, super::ControlPlaneError>,
    ) -> super::ControlPlaneError {
        match result {
            Err(error) => error,
            Ok(_) => panic!("expected credential load to fail"),
        }
    }

    #[test]
    fn operations_are_a_closed_exact_allowlist() {
        let operations = [
            (
                ControlPlaneOperation::GetSystemHealth,
                "GET",
                "/api/v1/health",
                200,
            ),
            (
                ControlPlaneOperation::GetSystemVersion,
                "GET",
                "/api/v1/version",
                200,
            ),
            (
                ControlPlaneOperation::GetCurrentInstallationAccess,
                "GET",
                "/api/v1/installations/current",
                200,
            ),
            (
                ControlPlaneOperation::GetWorkbenchStatus,
                "GET",
                "/api/v1/workbench/status",
                200,
            ),
            (
                ControlPlaneOperation::GetWorkbenchMetrics,
                "GET",
                "/api/v1/workbench/metrics",
                200,
            ),
            (
                ControlPlaneOperation::GetDouyinPlatformSession,
                "GET",
                "/api/v1/platform-sessions/douyin",
                200,
            ),
            (
                ControlPlaneOperation::PrepareDouyinPlatformSessionLogout,
                "POST",
                "/api/v1/platform-sessions/douyin/logout/prepare",
                200,
            ),
            (
                ControlPlaneOperation::IssueInstallationRegistrationChallenge,
                "POST",
                "/api/v1/installations/registration-challenges",
                201,
            ),
            (
                ControlPlaneOperation::CompleteInstallationRegistration,
                "POST",
                "/api/v1/installations",
                201,
            ),
            (
                ControlPlaneOperation::RotateDeviceCredential,
                "POST",
                "/api/v1/device-credentials/rotations",
                201,
            ),
            (
                ControlPlaneOperation::RevokeDeviceCredential,
                "POST",
                "/api/v1/device-credentials/revocations",
                200,
            ),
            (
                ControlPlaneOperation::ExchangeDeviceSession,
                "POST",
                "/api/v1/device-sessions",
                201,
            ),
            (
                ControlPlaneOperation::FindEditingMaterialByDigest,
                "GET",
                "/api/v1/editing-materials",
                200,
            ),
            (
                ControlPlaneOperation::RegisterEditingMaterial,
                "POST",
                "/api/v1/editing-materials",
                201,
            ),
            (
                ControlPlaneOperation::ListEditingMaterials,
                "GET",
                "/api/v1/editing-materials/library",
                200,
            ),
            (
                ControlPlaneOperation::GetEditingMaterial,
                "GET",
                "/api/v1/editing-materials/{material_id}",
                200,
            ),
            (
                ControlPlaneOperation::DeleteEditingMaterial,
                "DELETE",
                "/api/v1/editing-materials/{material_id}",
                204,
            ),
            (
                ControlPlaneOperation::UpdateEditingMaterialDescription,
                "PUT",
                "/api/v1/editing-materials/{material_id}/description",
                200,
            ),
            (
                ControlPlaneOperation::ApplySmartEditMaterialWriteback,
                "POST",
                "/api/v1/editing-materials/smart-edit-writebacks",
                200,
            ),
            (
                ControlPlaneOperation::ListEditingProjects,
                "GET",
                "/api/v1/editing-projects",
                200,
            ),
            (
                ControlPlaneOperation::CreateEditingProject,
                "POST",
                "/api/v1/editing-projects",
                201,
            ),
            (
                ControlPlaneOperation::GetEditingProject,
                "GET",
                "/api/v1/editing-projects/{project_id}",
                200,
            ),
            (
                ControlPlaneOperation::GetEditingProjectTimeline,
                "GET",
                "/api/v1/editing-projects/{project_id}/timeline",
                200,
            ),
            (
                ControlPlaneOperation::SaveEditingProjectTimeline,
                "PUT",
                "/api/v1/editing-projects/{project_id}/timeline",
                201,
            ),
            (
                ControlPlaneOperation::ListEditingJobs,
                "GET",
                "/api/v1/editing-projects/{project_id}/jobs",
                200,
            ),
            (
                ControlPlaneOperation::SubmitEditingJob,
                "POST",
                "/api/v1/editing-projects/{project_id}/jobs",
                201,
            ),
            (
                ControlPlaneOperation::GetEditingJob,
                "GET",
                "/api/v1/editing-jobs/{job_id}",
                200,
            ),
            (
                ControlPlaneOperation::ReconcileEditingJob,
                "PATCH",
                "/api/v1/editing-jobs/{job_id}",
                200,
            ),
            (
                ControlPlaneOperation::CreateTask,
                "POST",
                "/api/v1/tasks",
                201,
            ),
            (
                ControlPlaneOperation::StartTaskDiscovery,
                "POST",
                "/api/v1/tasks/{task_id}/discoveries",
                202,
            ),
            (
                ControlPlaneOperation::ListTasks,
                "GET",
                "/api/v1/tasks",
                200,
            ),
            (
                ControlPlaneOperation::GetTask,
                "GET",
                "/api/v1/tasks/{task_id}",
                200,
            ),
            (
                ControlPlaneOperation::GetTaskTargetPreview,
                "GET",
                "/api/v1/tasks/{task_id}/target-preview",
                200,
            ),
            (
                ControlPlaneOperation::ReplaceTaskTargetExclusions,
                "PUT",
                "/api/v1/tasks/{task_id}/target-preview/exclusions",
                200,
            ),
            (
                ControlPlaneOperation::ConfirmTaskTargetPreview,
                "POST",
                "/api/v1/tasks/{task_id}/target-preview/confirmations",
                202,
            ),
            (
                ControlPlaneOperation::GetTaskTargetResults,
                "GET",
                "/api/v1/tasks/{task_id}/target-results",
                200,
            ),
            (
                ControlPlaneOperation::StreamTaskEvents,
                "GET",
                "/api/v1/tasks/{task_id}/events",
                200,
            ),
            (
                ControlPlaneOperation::PauseTask,
                "POST",
                "/api/v1/tasks/{task_id}/pause",
                202,
            ),
            (
                ControlPlaneOperation::ResumeTask,
                "POST",
                "/api/v1/tasks/{task_id}/resume",
                202,
            ),
            (
                ControlPlaneOperation::CancelTask,
                "POST",
                "/api/v1/tasks/{task_id}/cancel",
                202,
            ),
            (
                ControlPlaneOperation::EmergencyStopTask,
                "POST",
                "/api/v1/tasks/{task_id}/emergency-stop",
                202,
            ),
            (
                ControlPlaneOperation::PrepareBilibiliPublish,
                "POST",
                "/api/v1/publishing/bilibili/jobs/{publish_job_id}",
                201,
            ),
            (
                ControlPlaneOperation::UploadBilibiliPublishVideo,
                "PUT",
                "/api/v1/publishing/bilibili/jobs/{publish_job_id}/video",
                200,
            ),
            (
                ControlPlaneOperation::SubmitBilibiliPublish,
                "POST",
                "/api/v1/publishing/bilibili/jobs/{publish_job_id}/submission",
                202,
            ),
            (
                ControlPlaneOperation::CancelBilibiliPublishSession,
                "DELETE",
                "/api/v1/publishing/bilibili/jobs/{publish_job_id}/session",
                204,
            ),
        ];

        for (operation, method, path, success_status) in operations {
            assert_eq!(operation.method(), method);
            assert_eq!(operation.path(), path);
            assert_eq!(operation.success_status(), success_status);
        }
    }

    #[test]
    fn app_operation_allowlist_matches_the_complete_openapi_snapshot() {
        let operations = [
            (ControlPlaneOperation::GetSystemHealth, "getSystemHealth"),
            (ControlPlaneOperation::GetSystemVersion, "getSystemVersion"),
            (
                ControlPlaneOperation::GetCurrentInstallationAccess,
                "getCurrentInstallationAccess",
            ),
            (
                ControlPlaneOperation::GetWorkbenchStatus,
                "getWorkbenchStatus",
            ),
            (
                ControlPlaneOperation::GetWorkbenchMetrics,
                "getWorkbenchMetrics",
            ),
            (
                ControlPlaneOperation::GetDouyinPlatformSession,
                "getDouyinPlatformSession",
            ),
            (
                ControlPlaneOperation::PrepareDouyinPlatformSessionLogout,
                "prepareDouyinPlatformSessionLogout",
            ),
            (
                ControlPlaneOperation::IssueInstallationRegistrationChallenge,
                "issueInstallationRegistrationChallenge",
            ),
            (
                ControlPlaneOperation::CompleteInstallationRegistration,
                "completeInstallationRegistration",
            ),
            (
                ControlPlaneOperation::RotateDeviceCredential,
                "rotateDeviceCredential",
            ),
            (
                ControlPlaneOperation::RevokeDeviceCredential,
                "revokeDeviceCredential",
            ),
            (
                ControlPlaneOperation::ExchangeDeviceSession,
                "exchangeDeviceSession",
            ),
            (
                ControlPlaneOperation::FindEditingMaterialByDigest,
                "findEditingMaterialByDigest",
            ),
            (
                ControlPlaneOperation::RegisterEditingMaterial,
                "registerEditingMaterial",
            ),
            (
                ControlPlaneOperation::ListEditingMaterials,
                "listEditingMaterials",
            ),
            (
                ControlPlaneOperation::GetEditingMaterial,
                "getEditingMaterial",
            ),
            (
                ControlPlaneOperation::DeleteEditingMaterial,
                "deleteEditingMaterial",
            ),
            (
                ControlPlaneOperation::UpdateEditingMaterialDescription,
                "updateEditingMaterialDescription",
            ),
            (
                ControlPlaneOperation::ApplySmartEditMaterialWriteback,
                "applySmartEditMaterialWriteback",
            ),
            (
                ControlPlaneOperation::ListEditingProjects,
                "listEditingProjects",
            ),
            (
                ControlPlaneOperation::CreateEditingProject,
                "createEditingProject",
            ),
            (
                ControlPlaneOperation::GetEditingProject,
                "getEditingProject",
            ),
            (
                ControlPlaneOperation::GetEditingProjectTimeline,
                "getEditingProjectTimeline",
            ),
            (
                ControlPlaneOperation::SaveEditingProjectTimeline,
                "saveEditingProjectTimeline",
            ),
            (ControlPlaneOperation::ListEditingJobs, "listEditingJobs"),
            (ControlPlaneOperation::SubmitEditingJob, "submitEditingJob"),
            (ControlPlaneOperation::GetEditingJob, "getEditingJob"),
            (
                ControlPlaneOperation::ReconcileEditingJob,
                "reconcileEditingJob",
            ),
            (ControlPlaneOperation::CreateTask, "createTask"),
            (
                ControlPlaneOperation::StartTaskDiscovery,
                "startTaskDiscovery",
            ),
            (ControlPlaneOperation::ListTasks, "listTasks"),
            (ControlPlaneOperation::GetTask, "getTask"),
            (
                ControlPlaneOperation::GetTaskTargetPreview,
                "getTaskTargetPreview",
            ),
            (
                ControlPlaneOperation::ReplaceTaskTargetExclusions,
                "replaceTaskTargetExclusions",
            ),
            (
                ControlPlaneOperation::ConfirmTaskTargetPreview,
                "confirmTaskTargetPreview",
            ),
            (
                ControlPlaneOperation::GetTaskTargetResults,
                "getTaskTargetResults",
            ),
            (ControlPlaneOperation::StreamTaskEvents, "streamTaskEvents"),
            (ControlPlaneOperation::PauseTask, "pauseTask"),
            (ControlPlaneOperation::ResumeTask, "resumeTask"),
            (ControlPlaneOperation::CancelTask, "cancelTask"),
            (
                ControlPlaneOperation::EmergencyStopTask,
                "emergencyStopTask",
            ),
            (
                ControlPlaneOperation::PrepareBilibiliPublish,
                "prepareBilibiliPublish",
            ),
            (
                ControlPlaneOperation::UploadBilibiliPublishVideo,
                "uploadBilibiliPublishVideo",
            ),
            (
                ControlPlaneOperation::SubmitBilibiliPublish,
                "submitBilibiliPublish",
            ),
            (
                ControlPlaneOperation::CancelBilibiliPublishSession,
                "cancelBilibiliPublishSession",
            ),
        ];
        let app_operations = operations
            .iter()
            .map(|(operation, operation_id)| {
                (
                    operation.path().to_owned(),
                    operation.method().to_ascii_lowercase(),
                    (*operation_id).to_owned(),
                )
            })
            .collect::<BTreeSet<_>>();
        assert_eq!(app_operations.len(), operations.len());

        let openapi: serde_json::Value = serde_json::from_str(include_str!(
            "../../../contracts/openapi/control-plane.v1.json"
        ))
        .expect("committed OpenAPI snapshot");
        let mut openapi_operations = BTreeSet::new();
        for (path, item) in openapi["paths"]
            .as_object()
            .expect("OpenAPI path inventory")
        {
            for method in ["get", "post", "put", "patch", "delete"] {
                let Some(operation) = item.get(method) else {
                    continue;
                };
                openapi_operations.insert((
                    path.to_owned(),
                    method.to_owned(),
                    operation["operationId"]
                        .as_str()
                        .expect("OpenAPI operationId")
                        .to_owned(),
                ));
            }
        }
        assert_eq!(app_operations, openapi_operations);
    }

    #[test]
    fn configurable_test_origin_accepts_only_a_canonical_loopback_http_origin() {
        assert_eq!(
            validated_loopback_origin("http://127.0.0.1:43123").expect("canonical loopback origin"),
            (
                "http://127.0.0.1:43123".to_owned(),
                "ws://127.0.0.1:43123/api/v1/executors/connect".to_owned(),
            )
        );
        for invalid in [
            "https://127.0.0.1:43123",
            "http://localhost:43123",
            "http://127.0.0.1",
            "http://127.0.0.1:43123/extra",
            "http://user@127.0.0.1:43123",
            "http://127.0.0.1:43123?private=value",
        ] {
            assert!(validated_loopback_origin(invalid).is_err(), "{invalid}");
        }
    }

    #[test]
    fn signed_demo_origin_accepts_only_an_exact_allowlisted_https_origin() {
        let allowed = vec!["api.automation-tool.test".to_owned()];
        assert_eq!(
            validated_demo_origin("https://api.automation-tool.test", &allowed)
                .expect("canonical Demo origin"),
            (
                "https://api.automation-tool.test".to_owned(),
                "wss://api.automation-tool.test/api/v1/executors/connect".to_owned(),
            )
        );
        for invalid in [
            "http://api.automation-tool.test",
            "https://api.automation-tool.test.evil.test",
            "https://operator@api.automation-tool.test",
            "https://api.automation-tool.test:443",
            "https://api.automation-tool.test/api",
            "https://api.automation-tool.test?private=value",
        ] {
            assert!(
                validated_demo_origin(invalid, &allowed).is_err(),
                "{invalid}"
            );
        }
    }

    #[test]
    fn task_query_targets_build_only_validated_fixed_paths() {
        let list_path = request_path(
            ControlPlaneOperation::ListTasks,
            Some(ControlPlaneRequestTarget::List {
                cursor: Some("YWJj"),
                limit: 20,
            }),
        )
        .expect("valid list target");
        assert_eq!(list_path, "/api/v1/tasks?limit=20&cursor=YWJj");

        let detail_path = request_path(
            ControlPlaneOperation::GetTask,
            Some(ControlPlaneRequestTarget::Detail(IDENTIFIER)),
        )
        .expect("valid detail target");
        assert_eq!(detail_path, format!("/api/v1/tasks/{IDENTIFIER}"));

        let event_path = request_path(
            ControlPlaneOperation::StreamTaskEvents,
            Some(ControlPlaneRequestTarget::EventStream {
                task_id: IDENTIFIER,
                last_event_id: Some(42),
            }),
        )
        .expect("valid Task event stream target");
        assert_eq!(event_path, format!("/api/v1/tasks/{IDENTIFIER}/events"));

        let pause_path = request_path(
            ControlPlaneOperation::PauseTask,
            Some(ControlPlaneRequestTarget::Control(IDENTIFIER)),
        )
        .expect("valid pause target");
        let resume_path = request_path(
            ControlPlaneOperation::ResumeTask,
            Some(ControlPlaneRequestTarget::Control(IDENTIFIER)),
        )
        .expect("valid resume target");
        assert_eq!(pause_path, format!("/api/v1/tasks/{IDENTIFIER}/pause"));
        assert_eq!(resume_path, format!("/api/v1/tasks/{IDENTIFIER}/resume"));
        let cancel_path = request_path(
            ControlPlaneOperation::CancelTask,
            Some(ControlPlaneRequestTarget::Control(IDENTIFIER)),
        )
        .expect("valid cancel target");
        let emergency_stop_path = request_path(
            ControlPlaneOperation::EmergencyStopTask,
            Some(ControlPlaneRequestTarget::Control(IDENTIFIER)),
        )
        .expect("valid emergency-stop target");
        assert_eq!(cancel_path, format!("/api/v1/tasks/{IDENTIFIER}/cancel"));
        assert_eq!(
            emergency_stop_path,
            format!("/api/v1/tasks/{IDENTIFIER}/emergency-stop")
        );

        for invalid in [
            request_path(ControlPlaneOperation::ListTasks, None),
            request_path(
                ControlPlaneOperation::ListTasks,
                Some(ControlPlaneRequestTarget::List {
                    cursor: None,
                    limit: 0,
                }),
            ),
            request_path(
                ControlPlaneOperation::GetTask,
                Some(ControlPlaneRequestTarget::Detail("private-invalid")),
            ),
            request_path(
                ControlPlaneOperation::GetSystemHealth,
                Some(ControlPlaneRequestTarget::Detail(IDENTIFIER)),
            ),
            request_path(
                ControlPlaneOperation::PauseTask,
                Some(ControlPlaneRequestTarget::Control("private-invalid")),
            ),
            request_path(
                ControlPlaneOperation::StreamTaskEvents,
                Some(ControlPlaneRequestTarget::EventStream {
                    task_id: IDENTIFIER,
                    last_event_id: Some(1_u64 << 53),
                }),
            ),
        ] {
            let error = invalid.expect_err("invalid target");
            assert_eq!(error.code(), ControlPlaneErrorCode::ProtocolInvalid);
        }
    }

    #[test]
    fn editing_query_targets_build_only_validated_fixed_paths() {
        let digest = "cd".repeat(32);
        assert_eq!(
            request_path(
                ControlPlaneOperation::FindEditingMaterialByDigest,
                Some(ControlPlaneRequestTarget::EditingMaterialDigest(&digest)),
            )
            .expect("valid digest lookup"),
            format!("/api/v1/editing-materials?contentDigest={digest}")
        );
        assert_eq!(
            request_path(
                ControlPlaneOperation::ListEditingProjects,
                Some(ControlPlaneRequestTarget::EditingProjectList {
                    cursor: Some("YWJj"),
                    limit: 20,
                }),
            )
            .expect("valid project page"),
            "/api/v1/editing-projects?limit=20&cursor=YWJj"
        );
        assert_eq!(
            request_path(
                ControlPlaneOperation::GetEditingProjectTimeline,
                Some(ControlPlaneRequestTarget::EditingTimeline(IDENTIFIER)),
            )
            .expect("valid timeline detail"),
            format!("/api/v1/editing-projects/{IDENTIFIER}/timeline")
        );
        assert_eq!(
            request_path(
                ControlPlaneOperation::ListEditingJobs,
                Some(ControlPlaneRequestTarget::EditingJobList {
                    project_id: IDENTIFIER,
                    cursor: None,
                    limit: 100,
                }),
            )
            .expect("valid job page"),
            format!("/api/v1/editing-projects/{IDENTIFIER}/jobs?limit=100")
        );
        assert_eq!(
            request_path(
                ControlPlaneOperation::SubmitEditingJob,
                Some(ControlPlaneRequestTarget::EditingProjectJobs(IDENTIFIER)),
            )
            .expect("valid job submission"),
            format!("/api/v1/editing-projects/{IDENTIFIER}/jobs")
        );
        assert!(request_path(ControlPlaneOperation::ListEditingProjects, None).is_err());
        assert!(request_path(
            ControlPlaneOperation::FindEditingMaterialByDigest,
            Some(ControlPlaneRequestTarget::EditingMaterialDigest(
                "private-invalid"
            )),
        )
        .is_err());
        assert!(request_path(ControlPlaneOperation::FindEditingMaterialByDigest, None,).is_err());
        assert!(request_path(
            ControlPlaneOperation::SubmitEditingJob,
            Some(ControlPlaneRequestTarget::EditingTimeline(IDENTIFIER)),
        )
        .is_err());
    }

    #[test]
    fn editing_response_parsers_accept_only_the_current_closed_contract() {
        let project = serde_json::json!({
            "projectId": IDENTIFIER,
            "title": "发布会剪辑",
            "output": {"width": 720, "height": 1280, "fps": 20},
            "captionStyle": {
                "fontKey": "noto-sans-cjk-sc-bold",
                "fontPx": 48,
                "strokePx": 3,
                "lineSpacing": 1.2
            },
            "createdAt": "2026-08-01T00:00:00Z"
        });
        parse_editing_project(&serde_json::to_vec(&project).expect("project JSON"))
            .expect("current project");
        let mut unassigned_text_project = project.clone();
        unassigned_text_project["title"] = serde_json::json!("发布\u{0378}会剪辑");
        assert!(parse_editing_project(
            &serde_json::to_vec(&unassigned_text_project).expect("unsafe project JSON")
        )
        .is_err());
        let duplicate_project = format!(
            r#"{{"projectId":"{IDENTIFIER}","title":"first","title":"second","output":{{"width":720,"height":1280,"fps":20}},"captionStyle":{{"fontKey":"noto-sans-cjk-sc-bold","fontPx":48,"strokePx":3,"lineSpacing":1.2}},"createdAt":"2026-08-01T00:00:00Z"}}"#
        );
        assert!(parse_editing_project(duplicate_project.as_bytes()).is_err());
        let old_project = serde_json::json!({
            "projectId": IDENTIFIER,
            "title": "发布会剪辑",
            "sourceArtifactIds": [],
            "createdAt": "2026-08-01T00:00:00Z",
            "updatedAt": "2026-08-01T00:00:00Z"
        });
        assert!(parse_editing_project(
            &serde_json::to_vec(&old_project).expect("old project JSON")
        )
        .is_err());

        let timeline = serde_json::json!({
            "timelineId": "0a48954d-2df1-4168-8f33-b62c5772845a",
            "projectId": IDENTIFIER,
            "revision": 1,
            "durationMs": 3000,
            "tracks": [{
                "trackId": "picture-main",
                "kind": "visual",
                "clips": [{
                    "clipId": "opening-shot",
                    "startMs": 0,
                    "durationMs": 3000,
                    "sourceMaterialId": "9f48954d-2df1-4168-8f33-b62c5772845b",
                    "sourceInMs": 0,
                    "sourceOutMs": 3000,
                    "text": null,
                    "gainDb": null,
                    "transitionIn": null,
                    "originalAudioMode": null
                }]
            }],
            "createdAt": "2026-08-01T00:00:00Z"
        });
        parse_editing_timeline(&serde_json::to_vec(&timeline).expect("timeline JSON"))
            .expect("current timeline");
        let mut missing_original_audio_mode = timeline.clone();
        missing_original_audio_mode["tracks"][0]["clips"][0]
            .as_object_mut()
            .expect("timeline clip object")
            .remove("originalAudioMode");
        assert!(parse_editing_timeline(
            &serde_json::to_vec(&missing_original_audio_mode).expect("incomplete timeline JSON")
        )
        .is_err());

        let job = serde_json::json!({
            "jobId": "3d594650-b5f4-4498-8e38-0cf85d6dfa72",
            "projectId": IDENTIFIER,
            "timelineId": "0a48954d-2df1-4168-8f33-b62c5772845a",
            "timelineRevision": 1,
            "status": "succeeded",
            "failureCode": null,
            "outputArtifactId": "8e48954d-2df1-4168-8f33-b62c5772845c",
            "createdAt": "2026-08-01T00:00:00Z",
            "updatedAt": "2026-08-01T00:00:01Z"
        });
        parse_editing_job(&serde_json::to_vec(&job).expect("job JSON")).expect("current job");
    }

    #[test]
    fn editing_material_library_paths_and_pages_are_closed_and_path_free() {
        assert_eq!(
            request_path(
                ControlPlaneOperation::ListEditingMaterials,
                Some(ControlPlaneRequestTarget::EditingMaterialList {
                    cursor: Some("next_page"),
                    limit: 50,
                }),
            )
            .expect("valid material page"),
            "/api/v1/editing-materials/library?limit=50&cursor=next_page"
        );
        assert_eq!(
            request_path(
                ControlPlaneOperation::UpdateEditingMaterialDescription,
                Some(ControlPlaneRequestTarget::EditingMaterial(IDENTIFIER)),
            )
            .expect("valid material description target"),
            format!("/api/v1/editing-materials/{IDENTIFIER}/description")
        );

        let material = serde_json::json!({
            "materialId": IDENTIFIER,
            "kind": "video",
            "durationMs": 1234,
            "width": 720,
            "height": 1280,
            "contentDigest": "cd".repeat(32),
            "hasAudio": true,
            "audioLoudnessLufs": -18.25,
            "hasSpeech": true,
            "speechSegmentsMs": [[100, 500]],
            "speechTranscript": "发布会开场",
            "shotBoundariesMs": [600],
            "aiDescription": "发布会镜头",
            "aiTags": ["发布会"],
            "descriptionSource": "ai",
            "describedAt": "2026-08-01T00:00:00Z"
        });
        let page = serde_json::json!({"items": [material], "nextCursor": "next_page"});
        let parsed =
            parse_editing_material_list(&serde_json::to_vec(&page).expect("material page JSON"))
                .expect("closed material page");
        assert_eq!(parsed.items().len(), 1);
        assert_eq!(parsed.next_cursor(), Some("next_page"));

        let mut expanded = page;
        expanded["items"][0]["privatePath"] = serde_json::json!("/private/source.mp4");
        assert!(parse_editing_material_list(
            &serde_json::to_vec(&expanded).expect("expanded material page JSON")
        )
        .is_err());
    }

    #[test]
    fn smart_edit_compensation_matches_only_the_exact_generated_narration() {
        let digest = "ab".repeat(32);
        let page = serde_json::json!({
            "items": [{
                "materialId": IDENTIFIER,
                "kind": "audio",
                "durationMs": 1200,
                "width": null,
                "height": null,
                "contentDigest": digest,
                "hasAudio": true,
                "audioLoudnessLufs": null,
                "hasSpeech": true,
                "speechSegmentsMs": [[0, 1200]],
                "speechTranscript": "发布会开场",
                "shotBoundariesMs": [],
                "aiDescription": null,
                "aiTags": [],
                "descriptionSource": "ai",
                "describedAt": null
            }],
            "nextCursor": null
        });
        let material = parse_editing_material_list(
            &serde_json::to_vec(&page).expect("narration material page"),
        )
        .expect("exact narration material")
        .items
        .remove(0);
        let expected = SmartEditNarrationMaterialRequest {
            material_id: IDENTIFIER.to_owned(),
            content_digest: "ab".repeat(32),
            duration_ms: 1200,
            speech_transcript: "发布会开场".to_owned(),
        };
        assert!(material.matches_smart_edit_narration(&expected));

        for changed in [
            SmartEditNarrationMaterialRequest {
                content_digest: "cd".repeat(32),
                ..expected.clone()
            },
            SmartEditNarrationMaterialRequest {
                duration_ms: 1199,
                ..expected.clone()
            },
            SmartEditNarrationMaterialRequest {
                speech_transcript: "另一份旁白".to_owned(),
                ..expected.clone()
            },
        ] {
            assert!(!material.matches_smart_edit_narration(&changed));
        }
    }

    #[test]
    fn uncertain_timeline_save_is_reconciled_only_by_the_exact_next_revision() {
        let draft_document = serde_json::json!({
            "durationMs": 1200,
            "tracks": [{
                "trackId": "visual",
                "kind": "visual",
                "clips": [{
                    "clipId": "visual-0001",
                    "startMs": 0,
                    "durationMs": 1200,
                    "sourceMaterialId": "9f48954d-2df1-4168-8f33-b62c5772845b",
                    "sourceInMs": 0,
                    "sourceOutMs": 1200,
                    "text": null,
                    "gainDb": null,
                    "transitionIn": null,
                    "originalAudioMode": null
                }]
            }]
        });
        let draft = EditingTimelineDraft::from_worker_document(draft_document.clone(), 1)
            .expect("revision-bound Worker draft");
        let mut snapshot_document = draft_document;
        let snapshot = snapshot_document.as_object_mut().expect("timeline object");
        snapshot.insert(
            "timelineId".to_owned(),
            serde_json::json!("0a48954d-2df1-4168-8f33-b62c5772845a"),
        );
        snapshot.insert("projectId".to_owned(), serde_json::json!(IDENTIFIER));
        snapshot.insert("revision".to_owned(), serde_json::json!(2));
        snapshot.insert(
            "createdAt".to_owned(),
            serde_json::json!("2026-08-01T00:00:00Z"),
        );
        let saved = parse_editing_timeline(
            &serde_json::to_vec(&snapshot_document).expect("saved timeline document"),
        )
        .expect("saved timeline snapshot");
        assert!(saved.confirms_saved_draft(IDENTIFIER, 1, &draft));
        assert!(!saved.confirms_saved_draft(IDENTIFIER, 0, &draft));
        assert!(!saved.confirms_saved_draft("623e4567-e89b-42d3-a456-426614174105", 1, &draft,));
    }

    #[test]
    fn editing_mutations_have_exact_status_and_uncertain_transport_semantics() {
        assert_eq!(
            ControlPlaneOperation::SaveEditingProjectTimeline.success_status(),
            201
        );
        for operation in [
            ControlPlaneOperation::CreateEditingProject,
            ControlPlaneOperation::SaveEditingProjectTimeline,
            ControlPlaneOperation::SubmitEditingJob,
        ] {
            assert_eq!(
                transport_error(operation).code(),
                ControlPlaneErrorCode::OutcomeUncertain
            );
        }
    }

    #[test]
    fn editing_client_crosses_the_real_http_boundary_with_exact_contracts() {
        let device_credential = opaque_bearer("atdc1");
        let session_token = format!("atds1.{}", "A".repeat(43));
        let timeline_body = serde_json::json!({
            "durationMs": 3000,
            "tracks": [{
                "trackId": "picture-main",
                "kind": "visual",
                "clips": [{
                    "clipId": "opening-shot",
                    "startMs": 0,
                    "durationMs": 3000,
                    "sourceMaterialId": "9f48954d-2df1-4168-8f33-b62c5772845b",
                    "sourceInMs": 0,
                    "sourceOutMs": 3000,
                    "text": null,
                    "gainDb": null,
                    "transitionIn": null,
                    "originalAudioMode": null
                }]
            }]
        });
        let mut timeline_response = timeline_body.clone();
        let timeline_object = timeline_response
            .as_object_mut()
            .expect("timeline response object");
        timeline_object.insert(
            "timelineId".to_owned(),
            serde_json::json!("0a48954d-2df1-4168-8f33-b62c5772845a"),
        );
        timeline_object.insert("projectId".to_owned(), serde_json::json!(IDENTIFIER));
        timeline_object.insert("revision".to_owned(), serde_json::json!(1));
        timeline_object.insert(
            "createdAt".to_owned(),
            serde_json::json!("2026-08-01T00:00:00Z"),
        );
        let job_response = serde_json::json!({
            "jobId": "3d594650-b5f4-4498-8e38-0cf85d6dfa72",
            "projectId": IDENTIFIER,
            "timelineId": "0a48954d-2df1-4168-8f33-b62c5772845a",
            "timelineRevision": 1,
            "status": "queued",
            "failureCode": null,
            "outputArtifactId": null,
            "createdAt": "2026-08-01T00:00:00Z",
            "updatedAt": "2026-08-01T00:00:00Z"
        });
        let (origin, server) = spawn_http_contract_server(vec![
            ExpectedHttpExchange {
                method: "GET",
                path: "/api/v1/editing-projects?limit=20&cursor=YWJj".to_owned(),
                authorization: format!("Bearer {session_token}"),
                body: None,
                status: 200,
                response: serde_json::json!({"items": [], "nextCursor": null}),
            },
            ExpectedHttpExchange {
                method: "PUT",
                path: format!("/api/v1/editing-projects/{IDENTIFIER}/timeline"),
                authorization: format!("Bearer {session_token}"),
                body: Some(timeline_body.clone()),
                status: 201,
                response: timeline_response,
            },
            ExpectedHttpExchange {
                method: "POST",
                path: format!("/api/v1/editing-projects/{IDENTIFIER}/jobs"),
                authorization: format!("Bearer {session_token}"),
                body: Some(serde_json::json!({})),
                status: 201,
                response: job_response.clone(),
            },
            ExpectedHttpExchange {
                method: "GET",
                path: "/api/v1/editing-jobs/3d594650-b5f4-4498-8e38-0cf85d6dfa72".to_owned(),
                authorization: format!("Bearer {session_token}"),
                body: None,
                status: 200,
                response: job_response.clone(),
            },
            ExpectedHttpExchange {
                method: "PATCH",
                path: "/api/v1/editing-jobs/3d594650-b5f4-4498-8e38-0cf85d6dfa72".to_owned(),
                authorization: format!("Bearer {session_token}"),
                body: Some(serde_json::json!({
                    "expectedUpdatedAt": "2026-08-01T00:00:00Z",
                    "status": "running",
                    "failureCode": null,
                    "outputArtifactId": null
                })),
                status: 200,
                response: {
                    let mut running = job_response;
                    running["status"] = serde_json::json!("running");
                    running["updatedAt"] = serde_json::json!("2026-08-01T00:00:01Z");
                    running
                },
            },
        ]);
        let client = super::ControlPlaneClient::from_validated_origins(
            validated_loopback_origin(&origin).expect("validated loopback origin"),
        )
        .expect("loopback control-plane client");
        let vault =
            DeviceCredentialVault::new(MemorySecretStore::with_value(device_credential.as_bytes()));
        let timeline: super::EditingTimelineDraft =
            serde_json::from_value(timeline_body).expect("timeline draft");

        tauri::async_runtime::block_on(async {
            client
                .list_editing_projects(&vault, Some("YWJj"), 20)
                .await
                .expect("list projects over HTTP");
            client
                .save_editing_project_timeline(&vault, IDENTIFIER, &timeline)
                .await
                .expect("save timeline over HTTP");
            let submitted = client
                .submit_editing_job(&vault, IDENTIFIER)
                .await
                .expect("submit job over HTTP");
            let loaded = client
                .get_editing_job(&vault, submitted.job_id())
                .await
                .expect("load job over HTTP");
            client
                .reconcile_editing_job(&vault, &loaded, EditingJobStatus::Running, None, None)
                .await
                .expect("reconcile job over HTTP");
        });
        server.join().expect("HTTP contract server");
    }

    #[test]
    fn editing_material_client_finds_registers_and_deletes_exact_path_free_facts() {
        let device_credential = opaque_bearer("atdc1");
        let session_token = format!("atds1.{}", "A".repeat(43));
        let material_id = "623e4567-e89b-42d3-a456-426614174105";
        let digest = "cd".repeat(32);
        let material = serde_json::json!({
            "materialId": material_id,
            "kind": "video",
            "durationMs": 1234,
            "width": 720,
            "height": 1280,
            "contentDigest": digest,
            "hasAudio": true,
            "audioLoudnessLufs": -18.25,
            "hasSpeech": false,
            "speechSegmentsMs": [],
            "speechTranscript": null,
            "shotBoundariesMs": [],
            "aiDescription": null,
            "aiTags": [],
            "descriptionSource": "ai",
            "describedAt": null
        });
        let mut described_material = material.clone();
        described_material["aiDescription"] = serde_json::json!("人工挑选的开场镜头");
        described_material["descriptionSource"] = serde_json::json!("user");
        let (origin, server) = spawn_http_contract_server(vec![
            ExpectedHttpExchange {
                method: "GET",
                path: format!("/api/v1/editing-materials?contentDigest={digest}"),
                authorization: format!("Bearer {session_token}"),
                body: None,
                status: 200,
                response: material.clone(),
            },
            ExpectedHttpExchange {
                method: "POST",
                path: "/api/v1/editing-materials".to_owned(),
                authorization: format!("Bearer {session_token}"),
                body: Some(material.clone()),
                status: 201,
                response: material.clone(),
            },
            ExpectedHttpExchange {
                method: "GET",
                path: "/api/v1/editing-materials/library?limit=50&cursor=next_page".to_owned(),
                authorization: format!("Bearer {session_token}"),
                body: None,
                status: 200,
                response: serde_json::json!({
                    "items": [material.clone()],
                    "nextCursor": null
                }),
            },
            ExpectedHttpExchange {
                method: "PUT",
                path: format!("/api/v1/editing-materials/{material_id}/description"),
                authorization: format!("Bearer {session_token}"),
                body: Some(serde_json::json!({
                    "source": "user",
                    "description": "人工挑选的开场镜头"
                })),
                status: 200,
                response: described_material,
            },
            ExpectedHttpExchange {
                method: "DELETE",
                path: format!("/api/v1/editing-materials/{material_id}"),
                authorization: format!("Bearer {session_token}"),
                body: None,
                status: 204,
                response: serde_json::json!({}),
            },
            ExpectedHttpExchange {
                method: "GET",
                path: format!("/api/v1/editing-materials?contentDigest={digest}"),
                authorization: format!("Bearer {session_token}"),
                body: None,
                status: 404,
                response: serde_json::json!({"detail": "material_not_found"}),
            },
        ]);
        let client = super::ControlPlaneClient::from_validated_origins(
            validated_loopback_origin(&origin).expect("validated loopback origin"),
        )
        .expect("loopback control-plane client");
        let vault =
            DeviceCredentialVault::new(MemorySecretStore::with_value(device_credential.as_bytes()));
        let request = super::EditingMaterialRegistrationRequest::new(
            material_id,
            super::EditingMaterialKind::Video,
            Some(1234),
            Some(720),
            Some(1280),
            &digest,
            true,
            Some(-18.25),
        )
        .expect("validated material registration");

        tauri::async_runtime::block_on(async {
            let found = client
                .find_editing_material_by_digest(&vault, &digest)
                .await
                .expect("find material")
                .expect("existing material");
            assert_eq!(found.material_id(), material_id);
            let registered = client
                .register_editing_material(&vault, &request)
                .await
                .expect("register material");
            assert_eq!(registered.material_id(), material_id);
            let page = client
                .list_editing_materials(&vault, Some("next_page"), 50)
                .await
                .expect("list material page");
            assert_eq!(page.items().len(), 1);
            assert_eq!(page.next_cursor(), None);
            let described = client
                .update_editing_material_description(&vault, material_id, "人工挑选的开场镜头")
                .await
                .expect("update material description");
            assert_eq!(described.material_id(), material_id);
            client
                .delete_editing_material(&vault, material_id)
                .await
                .expect("delete material");
            assert!(client
                .find_editing_material_by_digest(&vault, &digest)
                .await
                .expect("missing digest is not an error")
                .is_none());
        });
        server.join().expect("HTTP contract server");
    }

    #[test]
    fn editing_timeline_get_rejects_a_cross_project_snapshot() {
        let device_credential = opaque_bearer("atdc1");
        let session_token = format!("atds1.{}", "A".repeat(43));
        let foreign_project_id = "8e48954d-2df1-4168-8f33-b62c5772845c";
        let response = serde_json::json!({
            "timelineId": "0a48954d-2df1-4168-8f33-b62c5772845a",
            "projectId": foreign_project_id,
            "revision": 1,
            "durationMs": 3000,
            "tracks": [{
                "trackId": "picture-main",
                "kind": "visual",
                "clips": [{
                    "clipId": "opening-shot",
                    "startMs": 0,
                    "durationMs": 3000,
                    "sourceMaterialId": "9f48954d-2df1-4168-8f33-b62c5772845b",
                    "sourceInMs": 0,
                    "sourceOutMs": 3000,
                    "text": null,
                    "gainDb": null,
                    "transitionIn": null,
                    "originalAudioMode": null
                }]
            }],
            "createdAt": "2026-08-01T00:00:00Z"
        });
        let (origin, server) = spawn_http_contract_server(vec![
            ExpectedHttpExchange {
                method: "GET",
                path: format!("/api/v1/editing-projects/{IDENTIFIER}/timeline"),
                authorization: format!("Bearer {session_token}"),
                body: None,
                status: 200,
                response,
            },
        ]);
        let client = super::ControlPlaneClient::from_validated_origins(
            validated_loopback_origin(&origin).expect("validated loopback origin"),
        )
        .expect("loopback control-plane client");
        let vault =
            DeviceCredentialVault::new(MemorySecretStore::with_value(device_credential.as_bytes()));

        let error =
            tauri::async_runtime::block_on(client.get_editing_project_timeline(&vault, IDENTIFIER))
                .expect_err("foreign timeline snapshot must be rejected");
        assert_eq!(error.code(), ControlPlaneErrorCode::ProtocolInvalid);
        server.join().expect("HTTP contract server");
    }

    #[test]
    fn sse_parser_accepts_exact_contiguous_public_events_and_keepalive_comments() {
        let data = serde_json::json!({
            "actionId": null,
            "eventType": "task.started",
            "eventVersion": "1.0",
            "executionAttemptId": IDENTIFIER,
            "message": null,
            "occurredAt": "2026-07-18T20:00:01.000000Z",
            "progressPercent": null,
            "recordedAt": "2026-07-18T20:00:01.000000Z",
            "sequence": 1,
            "taskId": IDENTIFIER,
            "taskRevision": 2,
            "taskStatus": "running"
        });
        let frame = format!("id: 1\nevent: task.started\ndata: {data}\n\n");
        let parsed = parse_sse_frame(frame.as_bytes(), IDENTIFIER, 1)
            .expect("valid SSE frame")
            .expect("event frame");

        assert_eq!(parsed.sequence(), 1);
        assert_eq!(parsed.event_type(), "task.started");
        assert_eq!(parsed.task_revision(), 2);
        assert_eq!(parsed.task_status(), "running");
        assert_eq!(parsed.progress_percent(), None);
        assert_eq!(
            serde_json::to_value(&parsed).expect("public event JSON"),
            data
        );
        assert!(parse_sse_frame(b": keep-alive\n\n", IDENTIFIER, 2)
            .expect("valid keepalive")
            .is_none());
        assert_eq!(sse_frame_end(frame.as_bytes()), Some(frame.len()));
        assert_eq!(
            sse_frame_end(b": keep-alive\r\n\r\n"),
            Some(b": keep-alive\r\n\r\n".len())
        );
        assert_eq!(sse_frame_end(b"id: 1\n"), None);
    }

    #[test]
    fn sse_parser_rejects_gaps_malformed_fields_and_invalid_event_projection() {
        let valid = serde_json::json!({
            "actionId": null,
            "eventType": "step.progress",
            "eventVersion": "1.0",
            "executionAttemptId": IDENTIFIER,
            "message": "公开进度",
            "occurredAt": "2026-07-18T20:00:01.000000Z",
            "progressPercent": 50,
            "recordedAt": "2026-07-18T20:00:01.000000Z",
            "sequence": 2,
            "taskId": IDENTIFIER,
            "taskRevision": 3,
            "taskStatus": "running"
        });
        let cases = [
            format!("id: 3\nevent: step.progress\ndata: {valid}\n\n"),
            format!("id: 2\nid: 2\nevent: step.progress\ndata: {valid}\n\n"),
            format!("id: 2\nevent: step.started\ndata: {valid}\n\n"),
            format!("id: 2\nevent: step.progress\nprivate: value\ndata: {valid}\n\n"),
            "id: 2\nevent: step.progress\ndata: {}\n\n".to_owned(),
        ];
        for frame in cases {
            let error = match parse_sse_frame(frame.as_bytes(), IDENTIFIER, 2) {
                Err(error) => error,
                Ok(_) => panic!("expected invalid SSE frame"),
            };
            assert_eq!(error.code(), ControlPlaneErrorCode::ProtocolInvalid);
            assert!(!error.to_string().contains("private"));
        }
    }

    #[test]
    fn request_ids_are_distinct_canonical_uuid_v4_values() {
        let identifiers = (0..128)
            .map(|_| new_request_id().expect("system request ID"))
            .collect::<Vec<_>>();
        let unique = identifiers.iter().collect::<HashSet<_>>();

        assert_eq!(unique.len(), identifiers.len());
        for identifier in identifiers {
            let parsed = uuid::Uuid::parse_str(&identifier).expect("UUID request ID");
            assert_eq!(parsed.get_version_num(), 4);
            assert_eq!(parsed.hyphenated().to_string(), identifier);
        }
    }

    #[test]
    fn response_metadata_requires_matching_correlation_json_and_no_store() {
        let valid = ResponseMetadata {
            status: 200,
            request_id: Some("f831a58a-a54c-4bd9-8f3e-0383c4df609d".to_owned()),
            content_type: Some("application/json".to_owned()),
            cache_control: Some("no-store".to_owned()),
        };

        validate_response_metadata(
            ControlPlaneOperation::GetSystemHealth,
            "f831a58a-a54c-4bd9-8f3e-0383c4df609d",
            &valid,
        )
        .expect("valid metadata");

        for invalid in [
            ResponseMetadata {
                request_id: None,
                ..valid.clone()
            },
            ResponseMetadata {
                request_id: Some("8b68db87-5c97-43dd-ad0b-304506caaa03".to_owned()),
                ..valid.clone()
            },
            ResponseMetadata {
                content_type: Some("text/html".to_owned()),
                ..valid.clone()
            },
            ResponseMetadata {
                cache_control: None,
                ..valid.clone()
            },
            ResponseMetadata {
                status: 201,
                ..valid.clone()
            },
        ] {
            assert!(validate_response_metadata(
                ControlPlaneOperation::GetSystemHealth,
                "f831a58a-a54c-4bd9-8f3e-0383c4df609d",
                &invalid,
            )
            .is_err());
        }

        let access_denied = validate_response_metadata(
            ControlPlaneOperation::GetCurrentInstallationAccess,
            "f831a58a-a54c-4bd9-8f3e-0383c4df609d",
            &ResponseMetadata {
                status: 401,
                ..valid.clone()
            },
        )
        .expect_err("401 access denial");
        assert_eq!(
            access_denied.code(),
            ControlPlaneErrorCode::InstallationAccessDenied
        );
        assert!(!access_denied.retryable());

        let unavailable = validate_response_metadata(
            ControlPlaneOperation::GetCurrentInstallationAccess,
            "f831a58a-a54c-4bd9-8f3e-0383c4df609d",
            &ResponseMetadata {
                status: 503,
                ..valid.clone()
            },
        )
        .expect_err("503 access dependency failure");
        assert_eq!(unavailable.code(), ControlPlaneErrorCode::RequestRejected);
        assert!(unavailable.retryable());

        let conflict = validate_response_metadata(
            ControlPlaneOperation::CompleteInstallationRegistration,
            "f831a58a-a54c-4bd9-8f3e-0383c4df609d",
            &ResponseMetadata {
                status: 409,
                ..valid.clone()
            },
        )
        .expect_err("this device public key already owns an installation");
        assert_eq!(
            conflict.code(),
            ControlPlaneErrorCode::InstallationConflict,
            "a service that already registered this device must be distinguishable \
             from an ordinary rejection, or a failed credential write becomes a \
             permanently unexplained failure"
        );
        assert!(!conflict.retryable());
        for other in [
            ControlPlaneOperation::IssueInstallationRegistrationChallenge,
            ControlPlaneOperation::CreateTask,
        ] {
            let unrelated = validate_response_metadata(
                other,
                "f831a58a-a54c-4bd9-8f3e-0383c4df609d",
                &ResponseMetadata {
                    status: 409,
                    ..valid.clone()
                },
            )
            .expect_err("unrelated conflict");
            assert_eq!(unrelated.code(), ControlPlaneErrorCode::RequestRejected);
        }

        let installation_busy = validate_response_metadata(
            ControlPlaneOperation::StartTaskDiscovery,
            "f831a58a-a54c-4bd9-8f3e-0383c4df609d",
            &ResponseMetadata {
                status: 423,
                request_id: Some("f831a58a-a54c-4bd9-8f3e-0383c4df609d".to_owned()),
                content_type: Some("application/json".to_owned()),
                cache_control: Some("no-store".to_owned()),
            },
        )
        .expect_err("active Installation task");
        assert_eq!(
            installation_busy.code(),
            ControlPlaneErrorCode::InstallationBusy
        );
        assert!(!installation_busy.retryable());

        validate_response_metadata(
            ControlPlaneOperation::DeleteEditingMaterial,
            "f831a58a-a54c-4bd9-8f3e-0383c4df609d",
            &ResponseMetadata {
                status: 204,
                request_id: Some("f831a58a-a54c-4bd9-8f3e-0383c4df609d".to_owned()),
                content_type: None,
                cache_control: Some("no-store".to_owned()),
            },
        )
        .expect("secret-free empty response metadata");

        for status in [200, 201] {
            validate_response_metadata(
                ControlPlaneOperation::CreateTask,
                "f831a58a-a54c-4bd9-8f3e-0383c4df609d",
                &ResponseMetadata {
                    status,
                    request_id: Some("f831a58a-a54c-4bd9-8f3e-0383c4df609d".to_owned()),
                    content_type: Some("application/json".to_owned()),
                    cache_control: Some("no-store".to_owned()),
                },
            )
            .expect("task creation or replay response");
        }
    }

    #[test]
    fn health_response_is_exact_and_rejects_unknown_or_malformed_fields() {
        let health = parse_health_response(include_bytes!(
            "../../../contracts/fixtures/control-plane-v1/health.json"
        ))
        .expect("shared valid health fixture");
        assert_eq!(health.service_version(), "0.1.0");

        for invalid in [
            br#"{"service":"other","status":"ok","version":"0.1.0"}"#.as_slice(),
            br#"{"service":"control-plane","status":"ok","version":""}"#.as_slice(),
            br#"{"service":"control-plane","status":"ok","version":"0.1.0","extra":true}"#
                .as_slice(),
            b"private-invalid-json".as_slice(),
        ] {
            assert!(parse_health_response(invalid).is_err());
        }
    }

    #[test]
    fn system_version_response_enforces_the_complete_runtime_matrix() {
        let valid = include_str!("../../../contracts/fixtures/control-plane-v1/version.json");
        parse_system_version_response(valid.as_bytes(), "0.1.0")
            .expect("shared compatible release matrix fixture");

        let fixture: serde_json::Value = serde_json::from_str(valid).expect("version fixture JSON");
        for (pointer, replacement) in [
            ("/version", serde_json::json!("0.0.9")),
            ("/apiVersion", serde_json::json!("v2")),
            ("/desktopApp/minimumCompatible", serde_json::json!("0.1.1")),
            ("/executorProtocol/current", serde_json::json!("2.0")),
        ] {
            let mut incompatible = fixture.clone();
            *incompatible
                .pointer_mut(pointer)
                .expect("version fixture pointer") = replacement;
            assert!(parse_system_version_response(
                &serde_json::to_vec(&incompatible).expect("invalid fixture JSON"),
                "0.1.0"
            )
            .is_err());
        }
        assert!(parse_system_version_response(valid.as_bytes(), "0.1.1").is_err());
    }

    #[test]
    fn bootstrap_input_is_bounded_and_errors_never_reflect_private_values() {
        DemoBootstrap::new(
            "atb1.cGF5bG9hZA.c2lnbmF0dXJl".to_owned(),
            "demo-cn-1".to_owned(),
        )
        .expect("valid bootstrap shape");

        for (token, environment_id) in [
            ("private-bootstrap", "demo-cn-1"),
            ("atb1.payload.signature", "INVALID"),
            ("atb1.payload.signature", "-invalid"),
            ("atb1.payload.signature", "invalid-"),
            ("atb1.payload.signature", ""),
        ] {
            let error = bootstrap_error(DemoBootstrap::new(
                token.to_owned(),
                environment_id.to_owned(),
            ));
            assert_eq!(error.code(), ControlPlaneErrorCode::ProtocolInvalid);
            assert_eq!(error.to_string(), "Control Plane request failed");
            assert!(!error.to_string().contains(token));
            assert!(error.source().is_none());
        }
    }

    #[test]
    fn response_parsers_accept_only_exact_bounded_contracts() {
        let device_value = opaque_bearer("atdc1");
        let session_value = opaque_bearer("atds1");
        let challenge = serde_json::json!({
            "challengeId": IDENTIFIER,
            "signingPayload": URL_SAFE_NO_PAD.encode(b"signing-payload"),
            "expiresAt": "2026-07-18T03:00:00Z"
        });
        parse_registration_challenge(&serde_json::to_vec(&challenge).expect("challenge JSON"))
            .expect("valid challenge");

        let registration = serde_json::json!({
            "installationId": IDENTIFIER,
            "status": "active",
            "revision": 1,
            "deviceCredential": {
                "credential": device_value,
                "version": 1,
                "scope": "device.session.exchange"
            }
        });
        parse_installation_registration(
            &serde_json::to_vec(&registration).expect("registration JSON"),
        )
        .expect("valid registration");

        let rotation = serde_json::json!({
            "credential": opaque_bearer("atdc1"),
            "version": 2,
            "scope": "device.session.exchange"
        });
        parse_rotated_credential(&serde_json::to_vec(&rotation).expect("rotation JSON"))
            .expect("valid rotation");

        let revocation = serde_json::json!({"version": 2, "status": "revoked"});
        parse_revoked_credential(&serde_json::to_vec(&revocation).expect("revocation JSON"))
            .expect("valid revocation");

        let access = serde_json::json!({
            "installationId": IDENTIFIER,
            "status": "active"
        });
        assert_eq!(
            parse_installation_access(&serde_json::to_vec(&access).expect("access JSON"))
                .expect("valid access"),
            IDENTIFIER
        );

        let session = serde_json::json!({
            "sessionToken": session_value,
            "capability": "executor.connect",
            "issuedAt": "2026-07-18T02:00:00+00:00",
            "expiresAt": "2026-07-18T02:05:00+00:00"
        });
        let parsed = parse_device_session(
            &serde_json::to_vec(&session).expect("session JSON"),
            DeviceSessionCapability::ExecutorConnect,
        )
        .expect("valid session");
        assert_eq!(
            parsed.capability(),
            DeviceSessionCapability::ExecutorConnect
        );

        let task = serde_json::json!({
            "taskId": IDENTIFIER,
            "status": "draft",
            "revision": 1,
            "lastEventSequence": 0,
            "createdAt": "2026-07-18T02:00:00Z",
            "updatedAt": "2026-07-18T02:00:00Z"
        });
        let parsed_task =
            parse_created_task(&serde_json::to_vec(&task).expect("task JSON")).expect("valid task");
        assert_eq!(parsed_task.task_id(), IDENTIFIER);
        assert_eq!(parsed_task.status(), "draft");
        assert_eq!(parsed_task.revision(), 1);
    }

    #[test]
    fn response_parsers_fail_closed_on_unknown_invalid_or_cross_capability_data() {
        let invalid_registration = serde_json::json!({
            "installationId": IDENTIFIER,
            "status": "active",
            "revision": 1,
            "deviceCredential": {
                "credential": "atdc1.invalid.private",
                "version": 1,
                "scope": "device.session.exchange"
            }
        });
        assert!(parse_installation_registration(
            &serde_json::to_vec(&invalid_registration).expect("invalid registration JSON")
        )
        .is_err());

        let invalid_session = serde_json::json!({
            "sessionToken": opaque_bearer("atds1"),
            "capability": "app.control-plane",
            "issuedAt": "2026-07-18T02:00:00Z",
            "expiresAt": "2026-07-18T02:05:00Z"
        });
        assert!(parse_device_session(
            &serde_json::to_vec(&invalid_session).expect("invalid session JSON"),
            DeviceSessionCapability::ExecutorConnect,
        )
        .is_err());

        let reversed_session_time = serde_json::json!({
            "sessionToken": opaque_bearer("atds1"),
            "capability": "executor.connect",
            "issuedAt": "2026-07-18T02:05:00Z",
            "expiresAt": "2026-07-18T02:00:00Z"
        });
        assert!(parse_device_session(
            &serde_json::to_vec(&reversed_session_time).expect("reversed session JSON"),
            DeviceSessionCapability::ExecutorConnect,
        )
        .is_err());

        for invalid in [
            serde_json::json!({
                "challengeId": IDENTIFIER,
                "signingPayload": "not+base64url",
                "expiresAt": "2026-07-18T03:00:00Z"
            }),
            serde_json::json!({
                "challengeId": IDENTIFIER,
                "signingPayload": URL_SAFE_NO_PAD.encode(b"payload"),
                "expiresAt": "T+00:00"
            }),
            serde_json::json!({
                "challengeId": "f831a58a-a54c-4bd9-0f3e-0383c4df609d",
                "signingPayload": URL_SAFE_NO_PAD.encode(b"payload"),
                "expiresAt": "2026-07-18T03:00:00Z"
            }),
            serde_json::json!({
                "challengeId": IDENTIFIER,
                "signingPayload": URL_SAFE_NO_PAD.encode(b"payload"),
                "expiresAt": "2026-07-18T03:00:00Z",
                "unknown": true
            }),
        ] {
            assert!(parse_registration_challenge(
                &serde_json::to_vec(&invalid).expect("invalid challenge JSON")
            )
            .is_err());
        }

        for invalid in [
            serde_json::json!({"installationId": IDENTIFIER, "status": "revoked"}),
            serde_json::json!({"installationId": "private-invalid", "status": "active"}),
            serde_json::json!({
                "installationId": IDENTIFIER,
                "status": "active",
                "credential": "private"
            }),
        ] {
            assert!(parse_installation_access(
                &serde_json::to_vec(&invalid).expect("invalid access JSON")
            )
            .is_err());
        }

        for invalid in [
            serde_json::json!({
                "taskId": IDENTIFIER,
                "status": "ready",
                "revision": 1,
                "lastEventSequence": 0,
                "createdAt": "2026-07-18T02:00:00Z",
                "updatedAt": "2026-07-18T02:00:00Z"
            }),
            serde_json::json!({
                "taskId": IDENTIFIER,
                "status": "draft",
                "revision": 1,
                "lastEventSequence": 0,
                "createdAt": "2026-07-18T02:05:00Z",
                "updatedAt": "2026-07-18T02:00:00Z"
            }),
            serde_json::json!({
                "taskId": IDENTIFIER,
                "status": "draft",
                "revision": 1,
                "lastEventSequence": 0,
                "createdAt": "2026-07-18T02:00:00Z",
                "updatedAt": "2026-07-18T02:00:00Z",
                "unknown": true
            }),
        ] {
            assert!(
                parse_created_task(&serde_json::to_vec(&invalid).expect("invalid task JSON"))
                    .is_err()
            );
        }
    }

    #[test]
    fn task_idempotency_keys_use_the_exact_protocol_alphabet_and_bounds() {
        let longest_valid = "a".repeat(128);
        for valid in ["a", "task:create/demo_1-2.3", longest_valid.as_str()] {
            require_idempotency_key(valid).expect("valid idempotency key");
        }
        let too_long = "a".repeat(129);
        for invalid in [
            "",
            "-leading",
            "contains space",
            "private@value",
            too_long.as_str(),
        ] {
            let error = require_idempotency_key(invalid).expect_err("invalid idempotency key");
            assert_eq!(error.code(), ControlPlaneErrorCode::ProtocolInvalid);
            assert_eq!(error.to_string(), "Control Plane request failed");
            if !invalid.is_empty() {
                assert!(!error.to_string().contains(invalid));
            }
        }
    }

    #[test]
    fn douyin_task_definition_serializes_exactly_and_rejects_changed_contracts() {
        let definition = DouyinSearchExposureTaskDefinition::new(
            "新能源汽车".to_owned(),
            DouyinSearchExposureAction::Comment,
            Some("您好，{{target_display_name}}，内容很有启发".to_owned()),
            12,
            30,
            90,
        )
        .expect("valid Task definition");
        assert_eq!(
            serde_json::to_value(&definition).expect("definition JSON"),
            serde_json::json!({
                "template": "douyin.search_exposure.v1",
                "searchKeyword": "新能源汽车",
                "action": "comment",
                "messageTemplate": "您好，{{target_display_name}}，内容很有启发",
                "targetLimit": 12,
                "minimumIntervalSeconds": 30,
                "maximumIntervalSeconds": 90,
                "previewRequired": true,
                "finalConfirmationRequired": true
            })
        );

        DouyinSearchExposureTaskDefinition::new(
            "😀".repeat(80),
            DouyinSearchExposureAction::Browse,
            None,
            100,
            30,
            90,
        )
        .expect("80 Unicode code points and the maximum target count remain valid");
        assert!(DouyinSearchExposureTaskDefinition::new(
            "😀".repeat(81),
            DouyinSearchExposureAction::Browse,
            None,
            10,
            30,
            90,
        )
        .is_err());
        assert!(DouyinSearchExposureTaskDefinition::new(
            "control\u{85}character".to_owned(),
            DouyinSearchExposureAction::Browse,
            None,
            10,
            30,
            90,
        )
        .is_err());

        let base = serde_json::to_value(&definition).expect("definition JSON");
        for (field, invalid) in [
            ("template", serde_json::json!("private.template")),
            (
                "searchKeyword",
                serde_json::json!(" password=private-value"),
            ),
            ("action", serde_json::json!("browse")),
            ("targetLimit", serde_json::json!(101)),
            ("minimumIntervalSeconds", serde_json::json!(91)),
            ("previewRequired", serde_json::json!(false)),
            ("finalConfirmationRequired", serde_json::json!(false)),
        ] {
            let mut candidate = base.clone();
            candidate[field] = invalid;
            let parsed: DouyinSearchExposureTaskDefinition =
                serde_json::from_value(candidate).expect("typed candidate");
            let error = parsed.validate().expect_err("invalid Task definition");
            assert_eq!(error.code(), ControlPlaneErrorCode::ProtocolInvalid);
            assert_eq!(error.to_string(), "Control Plane request failed");
            assert!(!error.to_string().contains("private-value"));
        }

        for invalid_message in [
            "{{target_display_name}}",
            "{{unknown}}您好",
            "{{ target_display_name }}您好",
            "{{target.display_name}}您好",
            "{target_display_name}您好",
            "{{target_display_name}您好",
            "{{{target_display_name}}}您好",
        ] {
            let mut candidate = base.clone();
            candidate["messageTemplate"] = serde_json::json!(invalid_message);
            let parsed: DouyinSearchExposureTaskDefinition =
                serde_json::from_value(candidate).expect("typed candidate");
            let error = parsed.validate().expect_err("invalid message template");
            assert_eq!(error.code(), ControlPlaneErrorCode::ProtocolInvalid);
            assert_eq!(error.to_string(), "Control Plane request failed");
            assert!(!error.to_string().contains(invalid_message));
        }

        let mut unknown = base;
        unknown["unknown"] = serde_json::json!(true);
        assert!(serde_json::from_value::<DouyinSearchExposureTaskDefinition>(unknown).is_err());
    }

    #[test]
    fn task_list_cursors_use_bounded_base64url_shape() {
        let longest_valid = "a".repeat(256);
        for valid in ["a", "YWJj_123-xyz", longest_valid.as_str()] {
            require_list_cursor(valid).expect("valid list cursor");
        }
        let too_long = "a".repeat(257);
        for invalid in ["", "private+cursor", "contains space", too_long.as_str()] {
            let error = require_list_cursor(invalid).expect_err("invalid list cursor");
            assert_eq!(error.code(), ControlPlaneErrorCode::ProtocolInvalid);
            assert_eq!(error.to_string(), "Control Plane request failed");
        }
    }

    #[test]
    fn task_query_parsers_require_public_ordered_snapshots() {
        let older_id = "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc";
        let detail = serde_json::json!({
            "taskId": IDENTIFIER,
            "status": "running",
            "revision": 3,
            "lastEventSequence": 2,
            "createdAt": "2026-07-18T02:00:00Z",
            "updatedAt": "2026-07-18T03:00:00Z"
        });
        let parsed_detail =
            parse_task_snapshot_body(&serde_json::to_vec(&detail).expect("task detail JSON"))
                .expect("valid task detail");
        assert_eq!(parsed_detail.task_id(), IDENTIFIER);
        assert_eq!(parsed_detail.status(), "running");
        assert_eq!(parsed_detail.revision(), 3);
        assert_eq!(parsed_detail.last_event_sequence(), 2);

        let older = serde_json::json!({
            "taskId": older_id,
            "status": "draft",
            "revision": 1,
            "lastEventSequence": 0,
            "createdAt": "2026-07-18T02:00:00Z",
            "updatedAt": "2026-07-18T02:00:00Z"
        });
        let page = serde_json::json!({
            "items": [detail, older],
            "nextCursor": "YWJj"
        });
        let parsed_page = parse_task_list(&serde_json::to_vec(&page).expect("task list JSON"))
            .expect("valid task list");
        assert_eq!(parsed_page.items().len(), 2);
        assert_eq!(parsed_page.items()[1].task_id(), older_id);
        assert_eq!(parsed_page.next_cursor(), Some("YWJj"));
    }

    #[test]
    fn workbench_status_parser_accepts_only_exact_public_runtime_state() {
        let online = serde_json::json!({
            "controlPlaneStatus": "ready",
            "executorStatus": "online",
            "executorLastHeartbeatAt": "2026-07-18T22:30:00.000000Z"
        });
        let parsed =
            parse_workbench_status(&serde_json::to_vec(&online).expect("workbench status JSON"))
                .expect("valid workbench status");
        assert_eq!(
            serde_json::to_value(parsed).expect("public workbench status JSON"),
            online
        );

        for invalid in [
            serde_json::json!({
                "controlPlaneStatus": "private",
                "executorStatus": "offline",
                "executorLastHeartbeatAt": null
            }),
            serde_json::json!({
                "controlPlaneStatus": "ready",
                "executorStatus": "online",
                "executorLastHeartbeatAt": null
            }),
            serde_json::json!({
                "controlPlaneStatus": "ready",
                "executorStatus": "offline",
                "executorLastHeartbeatAt": "2026-07-18T22:30:00Z"
            }),
            serde_json::json!({
                "controlPlaneStatus": "ready",
                "executorStatus": "offline",
                "executorLastHeartbeatAt": null,
                "executorId": IDENTIFIER
            }),
        ] {
            assert!(parse_workbench_status(
                &serde_json::to_vec(&invalid).expect("invalid workbench status JSON")
            )
            .is_err());
        }
    }

    #[test]
    fn workbench_metrics_parser_accepts_only_exact_coherent_safe_counts() {
        let valid = serde_json::json!({
            "version": "workbench.metrics.v1",
            "tasks": {
                "total": 9,
                "succeeded": 3,
                "failed": 2,
                "handoffRequired": 1,
                "outcomeUncertain": 1
            },
            "actions": {
                "total": 12,
                "succeeded": 7,
                "failed": 2,
                "outcomeUncertain": 1
            }
        });
        let parsed =
            parse_workbench_metrics(&serde_json::to_vec(&valid).expect("workbench metrics JSON"))
                .expect("valid workbench metrics");
        assert_eq!(
            serde_json::to_value(parsed).expect("public workbench metrics JSON"),
            valid
        );

        for invalid in [
            serde_json::json!({
                "version": "private",
                "tasks": valid["tasks"],
                "actions": valid["actions"]
            }),
            serde_json::json!({
                "version": "workbench.metrics.v1",
                "tasks": {
                    "total": 1,
                    "succeeded": 1,
                    "failed": 1,
                    "handoffRequired": 0,
                    "outcomeUncertain": 0
                },
                "actions": valid["actions"]
            }),
            serde_json::json!({
                "version": "workbench.metrics.v1",
                "tasks": valid["tasks"],
                "actions": {
                    "total": 1,
                    "succeeded": 2,
                    "failed": 0,
                    "outcomeUncertain": 0
                }
            }),
            serde_json::json!({
                "version": "workbench.metrics.v1",
                "tasks": valid["tasks"],
                "actions": valid["actions"],
                "diagnostics": "private"
            }),
            serde_json::json!({
                "version": "workbench.metrics.v1",
                "tasks": {
                    "total": 9007199254740992_u64,
                    "succeeded": 0,
                    "failed": 0,
                    "handoffRequired": 0,
                    "outcomeUncertain": 0
                },
                "actions": valid["actions"]
            }),
        ] {
            assert!(parse_workbench_metrics(
                &serde_json::to_vec(&invalid).expect("invalid workbench metrics JSON")
            )
            .is_err());
        }
    }

    #[test]
    fn platform_session_parser_accepts_only_exact_non_sensitive_health() {
        let healthy = serde_json::json!({
            "platform": "douyin",
            "state": "healthy",
            "observedAt": "2026-07-19T14:30:00.000000Z"
        });
        let parsed = parse_douyin_platform_session(
            &serde_json::to_vec(&healthy).expect("platform Session JSON"),
        )
        .expect("valid platform Session");
        assert_eq!(
            serde_json::to_value(parsed).expect("public platform Session JSON"),
            healthy
        );

        let unknown = serde_json::json!({
            "platform": "douyin",
            "state": "unknown",
            "observedAt": null
        });
        assert!(parse_douyin_platform_session(
            &serde_json::to_vec(&unknown).expect("unknown platform Session JSON")
        )
        .is_ok());

        assert_eq!(
            parse_douyin_platform_session_logout_prepare(
                br#"{"platform":"douyin","state":"blocked","sessionRevision":8}"#,
            )
            .expect("valid logout gate"),
            8,
        );
        for invalid_gate in [
            br#"{"platform":"douyin","state":"open","sessionRevision":8}"#.as_slice(),
            br#"{"platform":"douyin","state":"blocked","sessionRevision":0}"#.as_slice(),
            br#"{"platform":"douyin","state":"blocked","sessionRevision":8,"profile":"private"}"#
                .as_slice(),
        ] {
            assert!(parse_douyin_platform_session_logout_prepare(invalid_gate).is_err());
        }

        for invalid in [
            serde_json::json!({
                "platform": "private",
                "state": "healthy",
                "observedAt": "2026-07-19T14:30:00Z"
            }),
            serde_json::json!({
                "platform": "douyin",
                "state": "healthy",
                "observedAt": null
            }),
            serde_json::json!({
                "platform": "douyin",
                "state": "missing",
                "observedAt": null
            }),
            serde_json::json!({
                "platform": "douyin",
                "state": "unknown",
                "observedAt": null,
                "profilePath": "/private/path"
            }),
        ] {
            assert!(parse_douyin_platform_session(
                &serde_json::to_vec(&invalid).expect("invalid platform Session JSON")
            )
            .is_err());
        }
    }

    #[test]
    fn task_query_parsers_reject_invalid_status_order_cursor_and_unknown_fields() {
        let first = serde_json::json!({
            "taskId": IDENTIFIER,
            "status": "draft",
            "revision": 1,
            "lastEventSequence": 0,
            "createdAt": "2026-07-18T02:00:00Z",
            "updatedAt": "2026-07-18T02:00:00Z"
        });
        let later = serde_json::json!({
            "taskId": "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc",
            "status": "draft",
            "revision": 1,
            "lastEventSequence": 0,
            "createdAt": "2026-07-18T03:00:00Z",
            "updatedAt": "2026-07-18T03:00:00Z"
        });
        for invalid in [
            serde_json::json!({"items": [], "nextCursor": "YWJj"}),
            serde_json::json!({"items": [first.clone()], "nextCursor": "private+cursor"}),
            serde_json::json!({"items": [first.clone(), later], "nextCursor": null}),
            serde_json::json!({"items": [first.clone(), first.clone()], "nextCursor": null}),
            serde_json::json!({"items": [first.clone()], "nextCursor": null, "unknown": true}),
        ] {
            assert!(parse_task_list(
                &serde_json::to_vec(&invalid).expect("invalid task list JSON")
            )
            .is_err());
        }

        let invalid_status = serde_json::json!({
            "taskId": IDENTIFIER,
            "status": "private_unknown",
            "revision": 1,
            "lastEventSequence": 0,
            "createdAt": "2026-07-18T02:00:00Z",
            "updatedAt": "2026-07-18T02:00:00Z"
        });
        assert!(parse_task_snapshot_body(
            &serde_json::to_vec(&invalid_status).expect("invalid task detail JSON")
        )
        .is_err());

        let unsafe_watermark = serde_json::json!({
            "taskId": IDENTIFIER,
            "status": "running",
            "revision": 3,
            "lastEventSequence": 1_u64 << 53,
            "createdAt": "2026-07-18T02:00:00Z",
            "updatedAt": "2026-07-18T03:00:00Z"
        });
        assert!(parse_task_snapshot_body(
            &serde_json::to_vec(&unsafe_watermark).expect("unsafe watermark JSON")
        )
        .is_err());

        let watermark_after_revision = serde_json::json!({
            "taskId": IDENTIFIER,
            "status": "running",
            "revision": 2,
            "lastEventSequence": 3,
            "createdAt": "2026-07-18T02:00:00Z",
            "updatedAt": "2026-07-18T03:00:00Z"
        });
        assert!(parse_task_snapshot_body(
            &serde_json::to_vec(&watermark_after_revision).expect("invalid watermark JSON")
        )
        .is_err());

        let unsafe_revision = serde_json::json!({
            "taskId": IDENTIFIER,
            "status": "running",
            "revision": 1_u64 << 53,
            "lastEventSequence": 0,
            "createdAt": "2026-07-18T02:00:00Z",
            "updatedAt": "2026-07-18T03:00:00Z"
        });
        assert!(parse_task_snapshot_body(
            &serde_json::to_vec(&unsafe_revision).expect("unsafe revision JSON")
        )
        .is_err());
    }

    #[test]
    fn task_control_parser_is_operation_bound_bounded_and_secret_free() {
        let response = serde_json::json!({
            "commandId": "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc",
            "taskId": IDENTIFIER,
            "executionAttemptId": "adff54bd-3571-44da-8acd-5ea15695e5e9",
            "sequence": 2,
            "commandType": "task.pause",
            "status": "pending",
            "revision": 1,
            "createdAt": "2026-07-18T18:30:00Z",
            "deadlineAt": "2026-07-18T18:31:00Z"
        });
        let encoded = serde_json::to_vec(&response).expect("task control JSON");
        let parsed = parse_task_control(&encoded, ControlPlaneOperation::PauseTask, IDENTIFIER)
            .expect("valid pause command");
        assert_eq!(parsed.task_id(), IDENTIFIER);
        assert_eq!(parsed.sequence(), 2);
        assert_eq!(parsed.command_type(), "task.pause");
        assert_eq!(parsed.status(), "pending");
        assert_eq!(parsed.command_id(), "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc");
        assert_eq!(
            parsed.execution_attempt_id(),
            "adff54bd-3571-44da-8acd-5ea15695e5e9"
        );

        for (operation, command_type) in [
            (ControlPlaneOperation::CancelTask, "task.cancel"),
            (
                ControlPlaneOperation::EmergencyStopTask,
                "task.emergency_stop",
            ),
        ] {
            let mut termination = response.clone();
            termination["commandType"] = serde_json::Value::String(command_type.to_owned());
            let parsed = parse_task_control(
                &serde_json::to_vec(&termination).expect("termination command JSON"),
                operation,
                IDENTIFIER,
            )
            .expect("valid termination command");
            assert_eq!(parsed.command_type(), command_type);
        }

        for invalid in [
            response.clone(),
            serde_json::json!({
                "commandId": "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc",
                "taskId": IDENTIFIER,
                "executionAttemptId": "adff54bd-3571-44da-8acd-5ea15695e5e9",
                "sequence": 0,
                "commandType": "task.pause",
                "status": "pending",
                "revision": 1,
                "createdAt": "2026-07-18T18:30:00Z",
                "deadlineAt": "2026-07-18T18:31:00Z"
            }),
            serde_json::json!({
                "commandId": "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc",
                "taskId": IDENTIFIER,
                "executionAttemptId": "adff54bd-3571-44da-8acd-5ea15695e5e9",
                "sequence": 2,
                "commandType": "task.pause",
                "status": "private",
                "revision": 1,
                "createdAt": "2026-07-18T18:30:00Z",
                "deadlineAt": "2026-07-18T18:31:00Z"
            }),
        ]
        .into_iter()
        .enumerate()
        {
            let operation = if invalid.0 == 0 {
                ControlPlaneOperation::ResumeTask
            } else {
                ControlPlaneOperation::PauseTask
            };
            assert!(parse_task_control(
                &serde_json::to_vec(&invalid.1).expect("invalid task control JSON"),
                operation,
                IDENTIFIER,
            )
            .is_err());
        }
        assert!(
            parse_task_control(&encoded, ControlPlaneOperation::CreateTask, IDENTIFIER).is_err()
        );
    }

    #[test]
    fn task_discovery_parser_is_task_bound_exact_and_secret_free() {
        let response = serde_json::json!({
            "taskId": IDENTIFIER,
            "taskStatus": "discovering_targets",
            "taskRevision": 2,
            "lastEventSequence": 1,
            "commandId": "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc",
            "executionAttemptId": "adff54bd-3571-44da-8acd-5ea15695e5e9",
            "commandStatus": "pending",
            "createdAt": "2026-07-18T18:30:00Z",
            "deadlineAt": "2026-07-18T18:31:00Z"
        });
        let encoded = serde_json::to_vec(&response).expect("task discovery JSON");
        let parsed = parse_task_discovery(&encoded, IDENTIFIER).expect("valid discovery command");
        assert_eq!(parsed.task_id(), IDENTIFIER);
        assert_eq!(parsed.task_status(), "discovering_targets");
        assert_eq!(parsed.command_id(), "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc");
        assert_eq!(
            parsed.execution_attempt_id(),
            "adff54bd-3571-44da-8acd-5ea15695e5e9"
        );
        assert_eq!(parsed.command_status(), "pending");

        for invalid in [
            serde_json::json!({
                "taskId": "e2c20841-d4e0-42dc-b703-f4f2306b22f3",
                "taskStatus": "discovering_targets",
                "taskRevision": 2,
                "lastEventSequence": 1,
                "commandId": "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc",
                "executionAttemptId": "adff54bd-3571-44da-8acd-5ea15695e5e9",
                "commandStatus": "pending",
                "createdAt": "2026-07-18T18:30:00Z",
                "deadlineAt": "2026-07-18T18:31:00Z"
            }),
            serde_json::json!({
                "taskId": IDENTIFIER,
                "taskStatus": "discovering_targets",
                "taskRevision": 2,
                "lastEventSequence": 1,
                "commandId": "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc",
                "executionAttemptId": "adff54bd-3571-44da-8acd-5ea15695e5e9",
                "commandStatus": "private",
                "createdAt": "2026-07-18T18:30:00Z",
                "deadlineAt": "2026-07-18T18:31:00Z"
            }),
            serde_json::json!({
                "taskId": IDENTIFIER,
                "taskStatus": "discovering_targets",
                "taskRevision": 2,
                "lastEventSequence": 1,
                "commandId": "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc",
                "executionAttemptId": "adff54bd-3571-44da-8acd-5ea15695e5e9",
                "commandStatus": "pending",
                "createdAt": "2026-07-18T18:30:00Z",
                "deadlineAt": "2026-07-18T18:31:00Z",
                "cookie": "private"
            }),
        ] {
            assert!(parse_task_discovery(
                &serde_json::to_vec(&invalid).expect("invalid task discovery JSON"),
                IDENTIFIER,
            )
            .is_err());
        }
    }

    #[test]
    fn target_preview_parser_is_revision_bound_ordered_and_secret_free() {
        let second_target = "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc";
        let response = serde_json::json!({
            "taskId": IDENTIFIER,
            "taskStatus": "awaiting_confirmation",
            "taskRevision": 4,
            "confirmationRevision": 4,
            "lastEventSequence": 3,
            "pageRevision": 7,
            "action": "comment",
            "messageTemplate": "您好 {{target_display_name}} 期待您的分享",
            "selectedTargetCount": 1,
            "userExcludedTargetCount": 1,
            "confirmed": false,
            "confirmedAt": null,
            "items": [
                {
                    "targetId": IDENTIFIER,
                    "ordinal": 1,
                    "displayName": "目标一",
                    "publicHandle": "public_1",
                    "source": "general_search_author",
                    "disposition": "eligible",
                    "userExcluded": false,
                    "selected": true
                },
                {
                    "targetId": second_target,
                    "ordinal": 2,
                    "displayName": "目标二",
                    "publicHandle": null,
                    "source": "general_search_author",
                    "disposition": "eligible",
                    "userExcluded": true,
                    "selected": false
                }
            ],
            "nextCursor": null
        });
        let encoded = serde_json::to_vec(&response).expect("preview JSON");
        let parsed =
            parse_task_target_preview(&encoded, IDENTIFIER, true).expect("valid complete preview");
        assert_eq!(parsed.task_id(), IDENTIFIER);
        assert_eq!(parsed.task_status(), "awaiting_confirmation");
        assert_eq!(parsed.task_revision(), 4);
        assert_eq!(parsed.confirmation_revision(), 4);
        assert_eq!(parsed.page_revision(), 7);
        assert_eq!(parsed.action(), DouyinSearchExposureAction::Comment);
        assert_eq!(
            parsed.message_template(),
            Some("您好 {{target_display_name}} 期待您的分享")
        );
        assert_eq!(parsed.selected_target_count(), 1);
        assert_eq!(parsed.user_excluded_target_count(), 1);
        assert!(!parsed.confirmed());
        assert_eq!(parsed.items().len(), 2);
        assert!(parsed.items()[0].selected());
        assert!(parsed.items()[1].user_excluded());
        let public = serde_json::to_string(&parsed).expect("public preview JSON");
        assert!(!public.contains("private-platform"));
        assert!(!public.contains("dedupe"));

        let mut paged = response.clone();
        paged["items"] = serde_json::json!([response["items"][0].clone()]);
        paged["nextCursor"] = serde_json::json!("YWJj");
        assert!(parse_task_target_preview(
            &serde_json::to_vec(&paged).expect("paged preview JSON"),
            IDENTIFIER,
            false,
        )
        .is_ok());
        assert!(parse_task_target_preview(
            &serde_json::to_vec(&paged).expect("paged preview JSON"),
            IDENTIFIER,
            true,
        )
        .is_err());

        for invalid in [
            {
                let mut value = response.clone();
                value["pageRevision"] = serde_json::json!(0);
                value
            },
            {
                let mut value = response.clone();
                value["items"][1]["selected"] = serde_json::json!(true);
                value
            },
            {
                let mut value = response.clone();
                value["items"][0]["displayName"] = serde_json::json!("cookie=private");
                value
            },
            {
                let mut value = response.clone();
                value["items"][0]["publicHandle"] = serde_json::json!("invalid handle");
                value
            },
            {
                let mut value = response.clone();
                value["items"][1]["ordinal"] = serde_json::json!(1);
                value
            },
            {
                let mut value = response.clone();
                value["confirmed"] = serde_json::json!(true);
                value["confirmedAt"] = serde_json::json!("2026-07-20T03:00:00Z");
                value
            },
            {
                let mut value = response.clone();
                value["platformTargetId"] = serde_json::json!("private-platform");
                value
            },
        ] {
            assert!(parse_task_target_preview(
                &serde_json::to_vec(&invalid).expect("invalid preview JSON"),
                IDENTIFIER,
                true,
            )
            .is_err());
        }
    }

    #[test]
    fn target_preview_commands_validate_canonical_revisions_targets_and_cursors() {
        let target = "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc".to_owned();
        validate_preview_command(
            IDENTIFIER,
            7,
            4,
            std::slice::from_ref(&target),
            "task:preview:replace",
        )
        .expect("valid preview command");
        for targets in [
            vec!["private-invalid".to_owned()],
            vec![target.clone(), target.clone()],
        ] {
            assert!(
                validate_preview_command(IDENTIFIER, 7, 4, &targets, "task:preview:replace",)
                    .is_err()
            );
        }
        assert!(validate_preview_command(IDENTIFIER, 0, 4, &[], "task:preview:confirm").is_err());
        assert!(validate_preview_command(IDENTIFIER, 7, 0, &[], "task:preview:confirm").is_err());
        require_preview_cursor(&"a".repeat(512)).expect("maximum preview cursor");
        assert!(require_preview_cursor(&"a".repeat(513)).is_err());
        assert!(require_preview_cursor("private+cursor").is_err());
    }

    #[test]
    fn target_results_require_coherent_status_evidence_action_and_ordering() {
        let action = "7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc";
        let skipped_target = "df133e68-53ab-4105-9f91-8fd5812dcdb3";
        let response = serde_json::json!({
            "taskId": IDENTIFIER,
            "taskStatus": "partially_succeeded",
            "taskRevision": 8,
            "lastEventSequence": 7,
            "items": [
                {
                    "targetId": IDENTIFIER,
                    "ordinal": 1,
                    "displayName": "评论成功目标",
                    "publicHandle": "success.target",
                    "resultStatus": "succeeded",
                    "evidence": "comment_confirmed",
                    "actionId": action,
                    "updatedAt": "2026-07-21T08:00:00Z"
                },
                {
                    "targetId": skipped_target,
                    "ordinal": 2,
                    "displayName": "用户排除目标",
                    "publicHandle": null,
                    "resultStatus": "skipped",
                    "evidence": "user_excluded",
                    "actionId": null,
                    "updatedAt": "2026-07-21T08:00:01Z"
                }
            ]
        });
        let parsed = parse_task_target_results(
            &serde_json::to_vec(&response).expect("target results JSON"),
            IDENTIFIER,
        )
        .expect("valid target results");
        assert_eq!(parsed.items.len(), 2);
        let public = serde_json::to_string(&parsed).expect("public target results");
        assert!(public.contains("comment_confirmed"));
        assert!(!public.contains("platformTargetId"));

        for invalid in [
            {
                let mut value = response.clone();
                value["items"][0]["evidence"] = serde_json::json!("user_excluded");
                value
            },
            {
                let mut value = response.clone();
                value["items"][0]["actionId"] = serde_json::Value::Null;
                value
            },
            {
                let mut value = response.clone();
                value["items"][1]["ordinal"] = serde_json::json!(1);
                value
            },
            {
                let mut value = response.clone();
                value["items"][1]["displayName"] = serde_json::json!("password=private");
                value
            },
            {
                let mut value = response.clone();
                value["lastEventSequence"] = serde_json::json!(9);
                value
            },
            {
                let mut value = response.clone();
                value["privatePath"] = serde_json::json!("/Users/private");
                value
            },
        ] {
            assert!(parse_task_target_results(
                &serde_json::to_vec(&invalid).expect("invalid target results JSON"),
                IDENTIFIER,
            )
            .is_err());
        }
    }

    #[test]
    fn credential_loading_distinguishes_missing_corrupt_and_storage_failure() {
        let missing = DeviceCredentialVault::new(MemorySecretStore {
            value: None,
            fail_load: Cell::new(false),
        });
        let missing_error = credential_error(required_credential(&missing));
        assert_eq!(
            missing_error.code(),
            ControlPlaneErrorCode::CredentialMissing
        );

        let corrupt = DeviceCredentialVault::new(MemorySecretStore::with_value(b"private-bad"));
        let corrupt_error = credential_error(required_credential(&corrupt));
        assert_eq!(corrupt_error.code(), ControlPlaneErrorCode::ProtocolInvalid);

        let unavailable_store = MemorySecretStore {
            value: None,
            fail_load: Cell::new(true),
        };
        let unavailable = DeviceCredentialVault::new(unavailable_store);
        let unavailable_error = credential_error(required_credential(&unavailable));
        assert_eq!(
            unavailable_error.code(),
            ControlPlaneErrorCode::StorageUnavailable
        );
        for error in [missing_error, corrupt_error, unavailable_error] {
            assert_eq!(error.to_string(), "Control Plane request failed");
            assert!(!error.to_string().contains("private"));
        }
    }

    #[test]
    fn unregistered_installation_skips_the_authenticated_access_probe() {
        let missing = DeviceCredentialVault::new(MemorySecretStore {
            value: None,
            fail_load: Cell::new(false),
        });
        let client = super::ControlPlaneClient::local().expect("local client");

        tauri::async_runtime::block_on(client.check_installation_access_if_registered(&missing))
            .expect("unregistered App remains usable");
    }

    #[test]
    fn bilibili_publish_responses_bind_jobs_sessions_phases_and_resource_ids() {
        let prepared = serde_json::json!({
            "publishJobId": IDENTIFIER,
            "phase": "prepared",
            "requestDigest": "a".repeat(64),
            "resourceId": null,
            "replayed": false,
            "sessionToken": "b".repeat(32),
            "credentialRotation": {
                "accessToken": "rotated-access",
                "refreshToken": "rotated-refresh",
                "expiresAtEpochSeconds": 1_900_000_000_u64
            }
        });
        let parsed = parse_bilibili_publish_response(
            &serde_json::to_vec(&prepared).expect("response JSON"),
            IDENTIFIER,
        )
        .expect("valid prepared response");
        assert_eq!(parsed.phase(), BilibiliPublishPhase::Prepared);
        assert_eq!(
            parsed.session_token(),
            Some("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        );
        assert!(format!("{parsed:?}").contains("Prepared"));
        assert!(!format!("{parsed:?}").contains("rotated-access"));

        let submitted = serde_json::json!({
            "publishJobId": IDENTIFIER,
            "phase": "submitted",
            "requestDigest": "c".repeat(64),
            "resourceId": "BV17B4y1s7R1",
            "replayed": true,
            "sessionToken": null,
            "credentialRotation": null
        });
        let parsed = parse_bilibili_publish_response(
            &serde_json::to_vec(&submitted).expect("response JSON"),
            IDENTIFIER,
        )
        .expect("valid submitted response");
        assert_eq!(parsed.phase(), BilibiliPublishPhase::Submitted);
        assert_eq!(parsed.resource_id(), Some("BV17B4y1s7R1"));
        assert!(parsed.replayed());

        for invalid in [
            {
                let mut value = prepared.clone();
                value["publishJobId"] = serde_json::json!("7f7eaf60-abf6-4ef7-b067-cf85a9fbb7bc");
                value
            },
            {
                let mut value = prepared.clone();
                value["sessionToken"] = serde_json::json!("too-short");
                value
            },
            {
                let mut value = submitted.clone();
                value["resourceId"] = serde_json::json!("av17000");
                value
            },
            {
                let mut value = prepared.clone();
                value["resourceId"] = serde_json::json!("BV17B4y1s7R1");
                value
            },
            {
                let mut value = prepared.clone();
                value["accessToken"] = serde_json::json!("unexpected");
                value
            },
        ] {
            assert!(parse_bilibili_publish_response(
                &serde_json::to_vec(&invalid).expect("invalid response JSON"),
                IDENTIFIER,
            )
            .is_err());
        }
    }

    #[test]
    fn transport_failure_marks_only_stateful_or_issuing_operations_uncertain() {
        for operation in [
            ControlPlaneOperation::CompleteInstallationRegistration,
            ControlPlaneOperation::RotateDeviceCredential,
            ControlPlaneOperation::RevokeDeviceCredential,
            ControlPlaneOperation::ExchangeDeviceSession,
            ControlPlaneOperation::SubmitBilibiliPublish,
        ] {
            let error = transport_error(operation);
            assert_eq!(error.code(), ControlPlaneErrorCode::OutcomeUncertain);
            assert!(!error.retryable());
        }
        for operation in [
            ControlPlaneOperation::GetSystemHealth,
            ControlPlaneOperation::GetSystemVersion,
            ControlPlaneOperation::GetCurrentInstallationAccess,
            ControlPlaneOperation::GetWorkbenchStatus,
            ControlPlaneOperation::GetWorkbenchMetrics,
            ControlPlaneOperation::IssueInstallationRegistrationChallenge,
            ControlPlaneOperation::CreateTask,
            ControlPlaneOperation::ListTasks,
            ControlPlaneOperation::GetTask,
            ControlPlaneOperation::GetTaskTargetResults,
        ] {
            let error = transport_error(operation);
            assert_eq!(error.code(), ControlPlaneErrorCode::TransportUnavailable);
            assert!(error.retryable());
        }
    }
}

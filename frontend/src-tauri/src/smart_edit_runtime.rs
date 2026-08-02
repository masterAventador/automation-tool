//! Production smart-edit state machine: one Worker generation, one writeback,
//! one immutable Timeline save, and an optional render submission.

use crate::control_plane::{
    ControlPlaneClient, ControlPlaneErrorCode, EditingJobSnapshot, EditingMaterialSnapshot,
    EditingTimelineDraft, EditingTimelineSnapshot, SmartEditNarrationMaterialRequest,
};
use crate::device_credentials::ProductionDeviceCredentialVault;
use crate::local_editing_runtime;
use crate::local_material_library::LocalMaterialLibraryCoordinator;
use crate::local_video_orchestrator::{
    LocalVideoOrchestrator, VideoWorkerSmartEditEvent, VideoWorkerSmartEditFailureCode,
    VideoWorkerSmartEditRequest, VideoWorkerSmartEditStage,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashSet};
use std::fmt;
use std::sync::{Mutex, MutexGuard};
use std::time::Duration;
use tauri::{Manager, Runtime};
use uuid::{Uuid, Variant};

const MAX_MATERIAL_PAGES: usize = 100;
const MATERIAL_PAGE_SIZE: u16 = 100;
const MAX_GENERATIONS: usize = 32;
const POLL_INTERVAL: Duration = Duration::from_millis(50);
const MAX_POLL_ATTEMPTS: usize = 72_000;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SmartEditGenerationMode {
    Draft,
    Render,
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SmartEditGenerationRequest {
    project_id: String,
    prompt: String,
    enable_thinking: bool,
    mode: SmartEditGenerationMode,
}

impl fmt::Debug for SmartEditGenerationRequest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SmartEditGenerationRequest(<redacted>)")
    }
}

impl SmartEditGenerationRequest {
    pub fn new(
        project_id: impl Into<String>,
        prompt: impl Into<String>,
        enable_thinking: bool,
        mode: SmartEditGenerationMode,
    ) -> Result<Self, SmartEditRuntimeError> {
        let request = Self {
            project_id: project_id.into(),
            prompt: prompt.into(),
            enable_thinking,
            mode,
        };
        request.validate()?;
        Ok(request)
    }

    fn validate(&self) -> Result<(), SmartEditRuntimeError> {
        let project_id = Uuid::parse_str(&self.project_id).map_err(|_| invalid_request())?;
        if project_id.get_version_num() != 4
            || project_id.get_variant() != Variant::RFC4122
            || project_id.hyphenated().to_string() != self.project_id
            || self.prompt.is_empty()
            || self.prompt.trim() != self.prompt
            || self.prompt.chars().count() > 4_000
            || self
                .prompt
                .chars()
                .any(|character| character.is_control() && !matches!(character, '\n' | '\t'))
        {
            return Err(invalid_request());
        }
        Ok(())
    }

    pub fn project_id(&self) -> &str {
        &self.project_id
    }

    pub const fn mode(&self) -> SmartEditGenerationMode {
        self.mode
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SmartEditGenerationStatus {
    Running,
    Cancelling,
    Succeeded,
    Failed,
    Cancelled,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SmartEditRuntimeFailureCode {
    ConfigurationMissing,
    InsufficientMaterials,
    SourceTooShort,
    NoRelevantMaterial,
    MaterialUnavailable,
    MaterialSnapshotConflict,
    TimelineRevisionConflict,
    UpstreamRejected,
    WorkspaceUnusable,
    CommitFailed,
    RenderFailed,
    OperationUnavailable,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SmartEditGenerationSnapshot {
    generation_id: String,
    project_id: String,
    mode: SmartEditGenerationMode,
    status: SmartEditGenerationStatus,
    stage: Option<VideoWorkerSmartEditStage>,
    #[serde(rename = "progressPermille")]
    progress_per_mille: u16,
    timeline: Option<EditingTimelineSnapshot>,
    render_job: Option<EditingJobSnapshot>,
    failure_code: Option<SmartEditRuntimeFailureCode>,
}

impl SmartEditGenerationSnapshot {
    pub fn generation_id(&self) -> &str {
        &self.generation_id
    }

    pub const fn status(&self) -> SmartEditGenerationStatus {
        self.status
    }

    pub const fn progress_per_mille(&self) -> u16 {
        self.progress_per_mille
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SmartEditRuntimeErrorCode {
    InvalidRequest,
    GenerationNotFound,
    GenerationNotCancellable,
    StorageUnavailable,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct SmartEditRuntimeError {
    code: SmartEditRuntimeErrorCode,
}

impl SmartEditRuntimeError {
    const fn new(code: SmartEditRuntimeErrorCode) -> Self {
        Self { code }
    }

    pub const fn code(self) -> SmartEditRuntimeErrorCode {
        self.code
    }
}

impl fmt::Debug for SmartEditRuntimeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SmartEditRuntimeError")
            .field("code", &self.code)
            .finish()
    }
}

impl fmt::Display for SmartEditRuntimeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Smart edit operation is unavailable")
    }
}

impl std::error::Error for SmartEditRuntimeError {}

const fn invalid_request() -> SmartEditRuntimeError {
    SmartEditRuntimeError::new(SmartEditRuntimeErrorCode::InvalidRequest)
}

struct GenerationState {
    snapshot: SmartEditGenerationSnapshot,
    cancel_requested: bool,
}

pub struct SmartEditRuntime {
    generations: Mutex<BTreeMap<Uuid, GenerationState>>,
}

impl fmt::Debug for SmartEditRuntime {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SmartEditRuntime(<redacted>)")
    }
}

impl Default for SmartEditRuntime {
    fn default() -> Self {
        Self::new()
    }
}

impl SmartEditRuntime {
    pub fn new() -> Self {
        Self {
            generations: Mutex::new(BTreeMap::new()),
        }
    }

    pub fn start<R: Runtime>(
        &self,
        app: &tauri::AppHandle<R>,
        request: SmartEditGenerationRequest,
    ) -> Result<SmartEditGenerationSnapshot, SmartEditRuntimeError> {
        request.validate()?;
        let generation_id = new_generation_id()?;
        let snapshot = SmartEditGenerationSnapshot {
            generation_id: generation_id.hyphenated().to_string(),
            project_id: request.project_id.clone(),
            mode: request.mode,
            status: SmartEditGenerationStatus::Running,
            stage: Some(VideoWorkerSmartEditStage::Preparing),
            progress_per_mille: 0,
            timeline: None,
            render_job: None,
            failure_code: None,
        };
        {
            let mut generations = self.lock()?;
            if generations.values().any(|value| {
                matches!(
                    value.snapshot.status,
                    SmartEditGenerationStatus::Running | SmartEditGenerationStatus::Cancelling
                )
            }) {
                return Err(SmartEditRuntimeError::new(
                    SmartEditRuntimeErrorCode::GenerationNotCancellable,
                ));
            }
            while generations.len() >= MAX_GENERATIONS {
                let removable = generations
                    .iter()
                    .find(|(_, value)| {
                        !matches!(
                            value.snapshot.status,
                            SmartEditGenerationStatus::Running
                                | SmartEditGenerationStatus::Cancelling
                        )
                    })
                    .map(|(identifier, _)| *identifier)
                    .ok_or_else(|| {
                        SmartEditRuntimeError::new(SmartEditRuntimeErrorCode::StorageUnavailable)
                    })?;
                generations.remove(&removable);
            }
            generations.insert(
                generation_id,
                GenerationState {
                    snapshot: snapshot.clone(),
                    cancel_requested: false,
                },
            );
        }
        let app = app.clone();
        tauri::async_runtime::spawn(async move {
            run_generation(app, generation_id, request).await;
        });
        Ok(snapshot)
    }

    pub fn snapshot(
        &self,
        generation_id: &str,
    ) -> Result<SmartEditGenerationSnapshot, SmartEditRuntimeError> {
        let identifier = parse_generation_id(generation_id)?;
        self.lock()?
            .get(&identifier)
            .map(|value| value.snapshot.clone())
            .ok_or_else(|| {
                SmartEditRuntimeError::new(SmartEditRuntimeErrorCode::GenerationNotFound)
            })
    }

    pub fn cancel(
        &self,
        generation_id: &str,
    ) -> Result<SmartEditGenerationSnapshot, SmartEditRuntimeError> {
        let identifier = parse_generation_id(generation_id)?;
        let mut generations = self.lock()?;
        let state = generations.get_mut(&identifier).ok_or_else(|| {
            SmartEditRuntimeError::new(SmartEditRuntimeErrorCode::GenerationNotFound)
        })?;
        if state.snapshot.status != SmartEditGenerationStatus::Running {
            return Err(SmartEditRuntimeError::new(
                SmartEditRuntimeErrorCode::GenerationNotCancellable,
            ));
        }
        state.cancel_requested = true;
        state.snapshot.status = SmartEditGenerationStatus::Cancelling;
        Ok(state.snapshot.clone())
    }

    fn cancel_requested(&self, generation_id: Uuid) -> bool {
        self.generations
            .lock()
            .ok()
            .and_then(|values| {
                values
                    .get(&generation_id)
                    .map(|value| value.cancel_requested)
            })
            .unwrap_or(true)
    }

    fn progress(&self, generation_id: Uuid, stage: VideoWorkerSmartEditStage, progress: u16) {
        if let Ok(mut values) = self.generations.lock() {
            if let Some(state) = values.get_mut(&generation_id) {
                state.snapshot.stage = Some(stage);
                state.snapshot.progress_per_mille = progress;
            }
        }
    }

    fn finish(
        &self,
        generation_id: Uuid,
        status: SmartEditGenerationStatus,
        timeline: Option<EditingTimelineSnapshot>,
        render_job: Option<EditingJobSnapshot>,
        failure_code: Option<SmartEditRuntimeFailureCode>,
    ) {
        if let Ok(mut values) = self.generations.lock() {
            if let Some(state) = values.get_mut(&generation_id) {
                state.snapshot.status = status;
                state.snapshot.progress_per_mille =
                    if status == SmartEditGenerationStatus::Succeeded {
                        1_000
                    } else {
                        state.snapshot.progress_per_mille
                    };
                state.snapshot.timeline = timeline;
                state.snapshot.render_job = render_job;
                state.snapshot.failure_code = failure_code;
            }
        }
    }

    fn lock(
        &self,
    ) -> Result<MutexGuard<'_, BTreeMap<Uuid, GenerationState>>, SmartEditRuntimeError> {
        self.generations
            .lock()
            .map_err(|_| SmartEditRuntimeError::new(SmartEditRuntimeErrorCode::StorageUnavailable))
    }
}

fn new_generation_id() -> Result<Uuid, SmartEditRuntimeError> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes)
        .map_err(|_| SmartEditRuntimeError::new(SmartEditRuntimeErrorCode::StorageUnavailable))?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(Uuid::from_bytes(bytes))
}

fn parse_generation_id(value: &str) -> Result<Uuid, SmartEditRuntimeError> {
    let parsed = Uuid::parse_str(value).map_err(|_| invalid_request())?;
    if parsed.get_version_num() != 4
        || parsed.get_variant() != Variant::RFC4122
        || parsed.hyphenated().to_string() != value
    {
        return Err(invalid_request());
    }
    Ok(parsed)
}

async fn load_visual_materials(
    client: &ControlPlaneClient,
    vault: &ProductionDeviceCredentialVault,
) -> Result<Vec<EditingMaterialSnapshot>, SmartEditRuntimeFailureCode> {
    let mut materials = Vec::new();
    let mut cursor: Option<String> = None;
    let mut seen = HashSet::new();
    for _ in 0..MAX_MATERIAL_PAGES {
        let page = client
            .list_editing_materials(vault, cursor.as_deref(), MATERIAL_PAGE_SIZE)
            .await
            .map_err(|_| SmartEditRuntimeFailureCode::OperationUnavailable)?;
        for material in page.items() {
            let document = material
                .worker_document()
                .map_err(|_| SmartEditRuntimeFailureCode::OperationUnavailable)?;
            if document.get("kind") != Some(&serde_json::Value::String("audio".to_owned())) {
                materials.push(material.clone());
            }
        }
        let Some(next) = page.next_cursor() else {
            if materials.is_empty() || materials.len() > 32 {
                return Err(SmartEditRuntimeFailureCode::InsufficientMaterials);
            }
            return Ok(materials);
        };
        if !seen.insert(next.to_owned()) {
            return Err(SmartEditRuntimeFailureCode::OperationUnavailable);
        }
        cursor = Some(next.to_owned());
    }
    Err(SmartEditRuntimeFailureCode::OperationUnavailable)
}

fn map_worker_failure(code: VideoWorkerSmartEditFailureCode) -> SmartEditRuntimeFailureCode {
    match code {
        VideoWorkerSmartEditFailureCode::InsufficientMaterials => {
            SmartEditRuntimeFailureCode::InsufficientMaterials
        }
        VideoWorkerSmartEditFailureCode::SourceTooShort => {
            SmartEditRuntimeFailureCode::SourceTooShort
        }
        VideoWorkerSmartEditFailureCode::NoRelevantMaterial => {
            SmartEditRuntimeFailureCode::NoRelevantMaterial
        }
        VideoWorkerSmartEditFailureCode::ConfigurationMissing => {
            SmartEditRuntimeFailureCode::ConfigurationMissing
        }
        VideoWorkerSmartEditFailureCode::MaterialUnavailable => {
            SmartEditRuntimeFailureCode::MaterialUnavailable
        }
        VideoWorkerSmartEditFailureCode::UpstreamRejected => {
            SmartEditRuntimeFailureCode::UpstreamRejected
        }
        VideoWorkerSmartEditFailureCode::WorkspaceUnusable => {
            SmartEditRuntimeFailureCode::WorkspaceUnusable
        }
        VideoWorkerSmartEditFailureCode::CommitFailed => SmartEditRuntimeFailureCode::CommitFailed,
        VideoWorkerSmartEditFailureCode::LocalFailed => {
            SmartEditRuntimeFailureCode::OperationUnavailable
        }
    }
}

fn map_writeback_error(code: ControlPlaneErrorCode) -> SmartEditRuntimeFailureCode {
    match code {
        ControlPlaneErrorCode::RequestRejected => {
            SmartEditRuntimeFailureCode::MaterialSnapshotConflict
        }
        _ => SmartEditRuntimeFailureCode::OperationUnavailable,
    }
}

fn abort_or_emergency(orchestrator: &LocalVideoOrchestrator, generation_id: Uuid) {
    if orchestrator.abort_smart_edit_job(generation_id).is_err() {
        let _ = orchestrator.emergency_stop_smart_edit_job(generation_id);
    }
}

fn finish_or_emergency(orchestrator: &LocalVideoOrchestrator, generation_id: Uuid) {
    if orchestrator.finish_smart_edit_job(generation_id).is_err() {
        let _ = orchestrator.emergency_stop_smart_edit_job(generation_id);
    }
}

async fn compensate_materials(
    client: &ControlPlaneClient,
    vault: &ProductionDeviceCredentialVault,
    orchestrator: &LocalVideoOrchestrator,
    generation_id: Uuid,
    narrations: &[SmartEditNarrationMaterialRequest],
    committed: bool,
) {
    let mut material_ids = Vec::with_capacity(narrations.len());
    for narration in narrations {
        let Ok(material) = client
            .get_editing_material(vault, &narration.material_id)
            .await
        else {
            continue;
        };
        if material.matches_smart_edit_narration(narration)
            && client
                .delete_editing_material(vault, &narration.material_id)
                .await
                .is_ok()
        {
            if let Ok(identifier) = Uuid::parse_str(&narration.material_id) {
                material_ids.push(identifier);
            }
        }
    }
    if committed {
        let _ = orchestrator.rollback_committed_smart_edit(generation_id, &material_ids);
    }
}

async fn run_generation<R: Runtime>(
    app: tauri::AppHandle<R>,
    generation_id: Uuid,
    request: SmartEditGenerationRequest,
) {
    let Some(runtime) = app.try_state::<SmartEditRuntime>() else {
        return;
    };
    let Some(client) = app.try_state::<ControlPlaneClient>() else {
        runtime.finish(
            generation_id,
            SmartEditGenerationStatus::Failed,
            None,
            None,
            Some(SmartEditRuntimeFailureCode::OperationUnavailable),
        );
        return;
    };
    let Some(vault) = app.try_state::<ProductionDeviceCredentialVault>() else {
        runtime.finish(
            generation_id,
            SmartEditGenerationStatus::Failed,
            None,
            None,
            Some(SmartEditRuntimeFailureCode::OperationUnavailable),
        );
        return;
    };
    let Some(orchestrator) = app.try_state::<LocalVideoOrchestrator>() else {
        runtime.finish(
            generation_id,
            SmartEditGenerationStatus::Failed,
            None,
            None,
            Some(SmartEditRuntimeFailureCode::OperationUnavailable),
        );
        return;
    };
    let Some(coordinator) = app.try_state::<LocalMaterialLibraryCoordinator>() else {
        runtime.finish(
            generation_id,
            SmartEditGenerationStatus::Failed,
            None,
            None,
            Some(SmartEditRuntimeFailureCode::OperationUnavailable),
        );
        return;
    };
    let material_operation = coordinator.acquire().await;
    let initial_timeline = match client
        .get_editing_project_timeline(&vault, &request.project_id)
        .await
    {
        Ok(value) => value,
        Err(_) => {
            runtime.finish(
                generation_id,
                SmartEditGenerationStatus::Failed,
                None,
                None,
                Some(SmartEditRuntimeFailureCode::OperationUnavailable),
            );
            return;
        }
    };
    let initial_revision = initial_timeline
        .as_ref()
        .map_or(0, |value| value.revision());
    let materials = match load_visual_materials(&client, &vault).await {
        Ok(value) => value,
        Err(code) => {
            runtime.finish(
                generation_id,
                SmartEditGenerationStatus::Failed,
                None,
                None,
                Some(code),
            );
            return;
        }
    };
    if runtime.cancel_requested(generation_id) {
        runtime.finish(
            generation_id,
            SmartEditGenerationStatus::Cancelled,
            None,
            None,
            None,
        );
        return;
    }
    if local_editing_runtime::ensure_smart_edit_worker(&app).is_err() {
        runtime.finish(
            generation_id,
            SmartEditGenerationStatus::Failed,
            None,
            None,
            Some(SmartEditRuntimeFailureCode::ConfigurationMissing),
        );
        return;
    }
    let worker_materials = match materials
        .iter()
        .map(EditingMaterialSnapshot::worker_document)
        .collect::<Result<Vec<_>, _>>()
    {
        Ok(value) => value,
        Err(_) => {
            runtime.finish(
                generation_id,
                SmartEditGenerationStatus::Failed,
                None,
                None,
                Some(SmartEditRuntimeFailureCode::OperationUnavailable),
            );
            return;
        }
    };
    let worker_request = match VideoWorkerSmartEditRequest::new(
        request.prompt,
        worker_materials,
        request.enable_thinking,
    ) {
        Ok(value) => value,
        Err(_) => {
            runtime.finish(
                generation_id,
                SmartEditGenerationStatus::Failed,
                None,
                None,
                Some(SmartEditRuntimeFailureCode::OperationUnavailable),
            );
            return;
        }
    };
    if orchestrator
        .start_smart_edit_job(generation_id, &worker_request)
        .is_err()
    {
        runtime.finish(
            generation_id,
            SmartEditGenerationStatus::Failed,
            None,
            None,
            Some(SmartEditRuntimeFailureCode::OperationUnavailable),
        );
        return;
    }
    let mut cancel_sent = false;
    let prepared = 'poll: {
        for _ in 0..MAX_POLL_ATTEMPTS {
            if runtime.cancel_requested(generation_id)
                && !cancel_sent
                && orchestrator
                    .request_smart_edit_cancel(generation_id)
                    .is_ok()
            {
                cancel_sent = true;
            }
            match orchestrator.try_smart_edit_event(generation_id) {
                Ok(Some(VideoWorkerSmartEditEvent::Progress {
                    stage,
                    progress_per_mille,
                })) => runtime.progress(generation_id, stage, progress_per_mille),
                Ok(Some(VideoWorkerSmartEditEvent::Prepared { result, .. })) => {
                    break 'poll Some(result);
                }
                Ok(Some(VideoWorkerSmartEditEvent::Failed { failure_code })) => {
                    finish_or_emergency(&orchestrator, generation_id);
                    runtime.finish(
                        generation_id,
                        SmartEditGenerationStatus::Failed,
                        None,
                        None,
                        Some(map_worker_failure(failure_code)),
                    );
                    break 'poll None;
                }
                Ok(Some(VideoWorkerSmartEditEvent::Cancelled)) => {
                    finish_or_emergency(&orchestrator, generation_id);
                    runtime.finish(
                        generation_id,
                        SmartEditGenerationStatus::Cancelled,
                        None,
                        None,
                        None,
                    );
                    break 'poll None;
                }
                Ok(None) => tokio::time::sleep(POLL_INTERVAL).await,
                Err(_) => {
                    let _ = orchestrator.emergency_stop_smart_edit_job(generation_id);
                    runtime.finish(
                        generation_id,
                        SmartEditGenerationStatus::Failed,
                        None,
                        None,
                        Some(SmartEditRuntimeFailureCode::OperationUnavailable),
                    );
                    break 'poll None;
                }
            }
        }
        let _ = orchestrator.emergency_stop_smart_edit_job(generation_id);
        runtime.finish(
            generation_id,
            SmartEditGenerationStatus::Failed,
            None,
            None,
            Some(SmartEditRuntimeFailureCode::OperationUnavailable),
        );
        None
    };
    let Some(result) = prepared else {
        return;
    };
    if runtime.cancel_requested(generation_id) {
        abort_or_emergency(&orchestrator, generation_id);
        runtime.finish(
            generation_id,
            SmartEditGenerationStatus::Cancelled,
            None,
            None,
            None,
        );
        return;
    }
    let writeback = match result.writeback_request() {
        Ok(value) => value,
        Err(_) => {
            abort_or_emergency(&orchestrator, generation_id);
            runtime.finish(
                generation_id,
                SmartEditGenerationStatus::Failed,
                None,
                None,
                Some(SmartEditRuntimeFailureCode::OperationUnavailable),
            );
            return;
        }
    };
    let narrations = writeback
        .as_ref()
        .map(|value| value.narrations.clone())
        .unwrap_or_default();
    if let Some(writeback) = writeback {
        if let Err(error) = client
            .apply_smart_edit_material_writeback(&vault, &writeback)
            .await
        {
            if error.code() == ControlPlaneErrorCode::OutcomeUncertain {
                compensate_materials(
                    &client,
                    &vault,
                    &orchestrator,
                    generation_id,
                    &narrations,
                    false,
                )
                .await;
            }
            abort_or_emergency(&orchestrator, generation_id);
            runtime.finish(
                generation_id,
                SmartEditGenerationStatus::Failed,
                None,
                None,
                Some(map_writeback_error(error.code())),
            );
            return;
        }
    }
    if runtime.cancel_requested(generation_id) {
        compensate_materials(
            &client,
            &vault,
            &orchestrator,
            generation_id,
            &narrations,
            false,
        )
        .await;
        abort_or_emergency(&orchestrator, generation_id);
        runtime.finish(
            generation_id,
            SmartEditGenerationStatus::Cancelled,
            None,
            None,
            None,
        );
        return;
    }
    if orchestrator.commit_smart_edit_job(generation_id).is_err() {
        compensate_materials(
            &client,
            &vault,
            &orchestrator,
            generation_id,
            &narrations,
            false,
        )
        .await;
        finish_or_emergency(&orchestrator, generation_id);
        runtime.finish(
            generation_id,
            SmartEditGenerationStatus::Failed,
            None,
            None,
            Some(SmartEditRuntimeFailureCode::CommitFailed),
        );
        return;
    }
    let latest_timeline = match client
        .get_editing_project_timeline(&vault, &request.project_id)
        .await
    {
        Ok(value) => value,
        Err(_) => {
            compensate_materials(
                &client,
                &vault,
                &orchestrator,
                generation_id,
                &narrations,
                true,
            )
            .await;
            runtime.finish(
                generation_id,
                SmartEditGenerationStatus::Failed,
                None,
                None,
                Some(SmartEditRuntimeFailureCode::OperationUnavailable),
            );
            return;
        }
    };
    let latest_revision = latest_timeline.as_ref().map_or(0, |value| value.revision());
    if latest_revision != initial_revision {
        compensate_materials(
            &client,
            &vault,
            &orchestrator,
            generation_id,
            &narrations,
            true,
        )
        .await;
        runtime.finish(
            generation_id,
            SmartEditGenerationStatus::Failed,
            None,
            None,
            Some(SmartEditRuntimeFailureCode::TimelineRevisionConflict),
        );
        return;
    }
    let draft = match EditingTimelineDraft::from_worker_document(
        result.timeline_document(),
        initial_revision,
    ) {
        Ok(value) => value,
        Err(_) => {
            compensate_materials(
                &client,
                &vault,
                &orchestrator,
                generation_id,
                &narrations,
                true,
            )
            .await;
            runtime.finish(
                generation_id,
                SmartEditGenerationStatus::Failed,
                None,
                None,
                Some(SmartEditRuntimeFailureCode::OperationUnavailable),
            );
            return;
        }
    };
    let timeline = match client
        .save_editing_project_timeline(&vault, &request.project_id, &draft)
        .await
    {
        Ok(value) if value.confirms_saved_draft(&request.project_id, initial_revision, &draft) => {
            value
        }
        Ok(_) => {
            runtime.finish(
                generation_id,
                SmartEditGenerationStatus::Failed,
                None,
                None,
                Some(SmartEditRuntimeFailureCode::OperationUnavailable),
            );
            return;
        }
        Err(error)
            if matches!(
                error.code(),
                ControlPlaneErrorCode::OutcomeUncertain | ControlPlaneErrorCode::ProtocolInvalid
            ) =>
        {
            match client
                .get_editing_project_timeline(&vault, &request.project_id)
                .await
            {
                Ok(Some(value))
                    if value.confirms_saved_draft(
                        &request.project_id,
                        initial_revision,
                        &draft,
                    ) =>
                {
                    value
                }
                Ok(_) => {
                    compensate_materials(
                        &client,
                        &vault,
                        &orchestrator,
                        generation_id,
                        &narrations,
                        true,
                    )
                    .await;
                    runtime.finish(
                        generation_id,
                        SmartEditGenerationStatus::Failed,
                        None,
                        None,
                        Some(SmartEditRuntimeFailureCode::TimelineRevisionConflict),
                    );
                    return;
                }
                Err(_) => {
                    runtime.finish(
                        generation_id,
                        SmartEditGenerationStatus::Failed,
                        None,
                        None,
                        Some(SmartEditRuntimeFailureCode::OperationUnavailable),
                    );
                    return;
                }
            }
        }
        Err(error) => {
            compensate_materials(
                &client,
                &vault,
                &orchestrator,
                generation_id,
                &narrations,
                true,
            )
            .await;
            runtime.finish(
                generation_id,
                SmartEditGenerationStatus::Failed,
                None,
                None,
                Some(if error.code() == ControlPlaneErrorCode::RequestRejected {
                    SmartEditRuntimeFailureCode::TimelineRevisionConflict
                } else {
                    SmartEditRuntimeFailureCode::OperationUnavailable
                }),
            );
            return;
        }
    };
    drop(material_operation);
    let render_job = if request.mode == SmartEditGenerationMode::Render {
        match client.submit_editing_job(&vault, &request.project_id).await {
            Ok(value) => {
                if local_editing_runtime::dispatch_submitted_job(&app, &value)
                    .await
                    .is_err()
                {
                    let _ = local_editing_runtime::fail_submitted_job(&app, &value).await;
                    runtime.finish(
                        generation_id,
                        SmartEditGenerationStatus::Failed,
                        Some(timeline),
                        None,
                        Some(SmartEditRuntimeFailureCode::RenderFailed),
                    );
                    return;
                }
                Some(value)
            }
            Err(_) => {
                runtime.finish(
                    generation_id,
                    SmartEditGenerationStatus::Failed,
                    Some(timeline),
                    None,
                    Some(SmartEditRuntimeFailureCode::RenderFailed),
                );
                return;
            }
        }
    } else {
        None
    };
    runtime.finish(
        generation_id,
        SmartEditGenerationStatus::Succeeded,
        Some(timeline),
        render_job,
        None,
    );
}

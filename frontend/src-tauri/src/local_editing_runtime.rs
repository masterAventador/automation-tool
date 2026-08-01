//! Production bridge from one Control Plane editing job to the packaged Worker.

use crate::control_plane::{
    ControlPlaneClient, EditingJobFailureCode, EditingJobSnapshot, EditingJobStatus,
    EditingMaterialSnapshot, EditingProjectSnapshot, EditingTimelineSnapshot,
};
use crate::device_credentials::ProductionDeviceCredentialVault;
use crate::local_editing_job_ledger::{
    LocalEditingJobFailureCode, LocalEditingJobRecoveryPolicy, LocalEditingJobScheduler,
    LocalEditingJobStatus,
};
use crate::local_video_orchestrator::{
    LocalVideoOrchestrator, VideoWorkerKind, VideoWorkerLaunch, VideoWorkerLocalEditingJobRequest,
    VideoWorkerMediaToolsConfiguration, VideoWorkerRestartPolicy,
};
use crate::material_video_studio::WORKER_VERSION;
use crate::video_job_workspace::VideoJobWorkspaceStore;
use crate::video_media_toolchain::VideoMediaToolchain;
use serde::Serialize;
use std::collections::HashSet;
use std::fmt;
use std::path::{Path, PathBuf};
use std::time::Duration;
use tauri::{Manager, Runtime};
use uuid::Uuid;

const RENDER_REQUEST_SCHEMA: &str = "local-editing-render-request.v1";
const RENDER_REQUEST_CHECKPOINT: &str = "local-editing-render-request";
const OUTPUT_FILE_NAME: &str = "render.mp4";
const MAX_PROJECT_PAGES: usize = 100;
const POLL_INTERVAL: Duration = Duration::from_millis(50);
const MAX_POLL_ATTEMPTS: usize = 18_000;
const MAX_WORKER_RECOVERIES: u32 = 1;

#[derive(Clone)]
pub struct LocalEditingRuntime {
    app_data_directory: PathBuf,
}

impl LocalEditingRuntime {
    pub fn new(app_data_directory: PathBuf) -> Result<Self, LocalEditingRuntimeError> {
        if !app_data_directory.is_absolute() || !app_data_directory.is_dir() {
            return Err(runtime_unavailable());
        }
        Ok(Self { app_data_directory })
    }
}

impl fmt::Debug for LocalEditingRuntime {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("LocalEditingRuntime(<redacted>)")
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LocalEditingRuntimeError;

impl fmt::Display for LocalEditingRuntimeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Local editing runtime is unavailable")
    }
}

impl std::error::Error for LocalEditingRuntimeError {}

const fn runtime_unavailable() -> LocalEditingRuntimeError {
    LocalEditingRuntimeError
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RenderMaterial<'a> {
    material_id: &'a str,
    has_audio: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RenderRequest<'a> {
    schema_version: &'static str,
    job_id: &'a str,
    project: &'a EditingProjectSnapshot,
    timeline: &'a EditingTimelineSnapshot,
    materials: Vec<RenderMaterial<'a>>,
}

async fn find_project(
    client: &ControlPlaneClient,
    vault: &ProductionDeviceCredentialVault,
    project_id: &str,
) -> Result<EditingProjectSnapshot, LocalEditingRuntimeError> {
    let mut cursor: Option<String> = None;
    let mut seen = HashSet::new();
    for _ in 0..MAX_PROJECT_PAGES {
        let page = client
            .list_editing_projects(vault, cursor.as_deref(), 100)
            .await
            .map_err(|_| runtime_unavailable())?;
        if let Some(project) = page
            .items()
            .iter()
            .find(|project| project.project_id() == project_id)
        {
            return Ok(project.clone());
        }
        let Some(next) = page.next_cursor() else {
            break;
        };
        if !seen.insert(next.to_owned()) {
            return Err(runtime_unavailable());
        }
        cursor = Some(next.to_owned());
    }
    Err(runtime_unavailable())
}

async fn load_materials(
    client: &ControlPlaneClient,
    vault: &ProductionDeviceCredentialVault,
    timeline: &EditingTimelineSnapshot,
) -> Result<Vec<EditingMaterialSnapshot>, LocalEditingRuntimeError> {
    let mut materials = Vec::new();
    for identifier in timeline.material_ids() {
        materials.push(
            client
                .get_editing_material(vault, identifier)
                .await
                .map_err(|_| runtime_unavailable())?,
        );
    }
    Ok(materials)
}

fn worker_executable(resource_directory: &Path) -> PathBuf {
    let name = if cfg!(windows) {
        "automation-tool-material-video-worker.exe"
    } else {
        "automation-tool-material-video-worker"
    };
    resource_directory
        .join("material-video-worker/package")
        .join(name)
}

fn worker_launch(
    executable: PathBuf,
    app_data_directory: PathBuf,
    toolchain: &VideoMediaToolchain,
) -> Result<VideoWorkerLaunch, LocalEditingRuntimeError> {
    let policy = VideoWorkerRestartPolicy::new(1, Duration::from_millis(250))
        .map_err(|_| runtime_unavailable())?;
    let media = VideoWorkerMediaToolsConfiguration::new(
        toolchain.ffmpeg_path().to_path_buf(),
        toolchain.ffprobe_path().to_path_buf(),
    )
    .map_err(|_| runtime_unavailable())?;
    VideoWorkerLaunch::new(
        VideoWorkerKind::Python,
        executable,
        app_data_directory,
        WORKER_VERSION.to_owned(),
        policy,
    )
    .and_then(|launch| launch.with_media_tools(media))
    .map_err(|_| runtime_unavailable())
}

fn parse_job_request(
    job: &EditingJobSnapshot,
) -> Result<(Uuid, VideoWorkerLocalEditingJobRequest), LocalEditingRuntimeError> {
    let job_id = Uuid::parse_str(job.job_id()).map_err(|_| runtime_unavailable())?;
    let project_id = Uuid::parse_str(job.project_id()).map_err(|_| runtime_unavailable())?;
    let timeline_id = Uuid::parse_str(job.timeline_id()).map_err(|_| runtime_unavailable())?;
    let revision = u32::try_from(job.timeline_revision()).map_err(|_| runtime_unavailable())?;
    let request = VideoWorkerLocalEditingJobRequest::new(project_id, timeline_id, revision)
        .map_err(|_| runtime_unavailable())?;
    Ok((job_id, request))
}

fn local_failure_code(code: LocalEditingJobFailureCode) -> EditingJobFailureCode {
    match code {
        LocalEditingJobFailureCode::InvalidTimeline => EditingJobFailureCode::InvalidTimeline,
        LocalEditingJobFailureCode::MaterialUnavailable => {
            EditingJobFailureCode::MaterialUnavailable
        }
        LocalEditingJobFailureCode::MaterialUnsupported => {
            EditingJobFailureCode::MaterialUnsupported
        }
        LocalEditingJobFailureCode::FontUnavailable => EditingJobFailureCode::FontUnavailable,
        LocalEditingJobFailureCode::RenderFailed => EditingJobFailureCode::RenderFailed,
        LocalEditingJobFailureCode::ResourceExhausted => EditingJobFailureCode::ResourceExhausted,
        LocalEditingJobFailureCode::PermissionDenied => EditingJobFailureCode::PermissionDenied,
        LocalEditingJobFailureCode::WorkerLost => EditingJobFailureCode::WorkerLost,
    }
}

async fn mark_failed(
    client: &ControlPlaneClient,
    vault: &ProductionDeviceCredentialVault,
    previous: &EditingJobSnapshot,
    code: EditingJobFailureCode,
) -> Result<EditingJobSnapshot, LocalEditingRuntimeError> {
    client
        .reconcile_editing_job(vault, previous, EditingJobStatus::Failed, Some(code), None)
        .await
        .map_err(|_| runtime_unavailable())
}

async fn fail_current_job(
    client: &ControlPlaneClient,
    vault: &ProductionDeviceCredentialVault,
    job_id: &str,
    code: EditingJobFailureCode,
) -> Result<(), LocalEditingRuntimeError> {
    let current = client
        .get_editing_job(vault, job_id)
        .await
        .map_err(|_| runtime_unavailable())?;
    match current.status() {
        EditingJobStatus::Queued | EditingJobStatus::Running => {
            mark_failed(client, vault, &current, code).await?;
        }
        EditingJobStatus::Failed => {}
        EditingJobStatus::Cancelling
        | EditingJobStatus::Succeeded
        | EditingJobStatus::Cancelled => return Err(runtime_unavailable()),
    }
    Ok(())
}

async fn succeed_current_job(
    client: &ControlPlaneClient,
    vault: &ProductionDeviceCredentialVault,
    job_id: &str,
    artifact_id: &str,
) -> Result<(), LocalEditingRuntimeError> {
    let current = client
        .get_editing_job(vault, job_id)
        .await
        .map_err(|_| runtime_unavailable())?;
    match current.status() {
        EditingJobStatus::Running => {
            client
                .reconcile_editing_job(
                    vault,
                    &current,
                    EditingJobStatus::Succeeded,
                    None,
                    Some(artifact_id),
                )
                .await
                .map_err(|_| runtime_unavailable())?;
        }
        EditingJobStatus::Succeeded if current.output_artifact_id() == Some(artifact_id) => {}
        EditingJobStatus::Queued
        | EditingJobStatus::Cancelling
        | EditingJobStatus::Succeeded
        | EditingJobStatus::Failed
        | EditingJobStatus::Cancelled => return Err(runtime_unavailable()),
    }
    Ok(())
}

async fn settle_worker_lost<R: Runtime>(app: &tauri::AppHandle<R>, job_id: Uuid) {
    if let (Some(scheduler), Some(workspaces)) = (
        app.try_state::<LocalEditingJobScheduler>(),
        app.try_state::<VideoJobWorkspaceStore>(),
    ) {
        let _ = scheduler.fail_worker_lost(&workspaces, job_id);
    }
    if let (Some(client), Some(vault)) = (
        app.try_state::<ControlPlaneClient>(),
        app.try_state::<ProductionDeviceCredentialVault>(),
    ) {
        let _ = fail_current_job(
            &client,
            &vault,
            &job_id.hyphenated().to_string(),
            EditingJobFailureCode::WorkerLost,
        )
        .await;
    }
}

fn stop_worker<R: Runtime>(app: &tauri::AppHandle<R>) {
    if let Some(orchestrator) = app.try_state::<LocalVideoOrchestrator>() {
        let _ = orchestrator.stop(VideoWorkerKind::Python);
    }
}

async fn monitor<R: Runtime>(app: tauri::AppHandle<R>, job_id: Uuid) {
    let recovery_policy = match LocalEditingJobRecoveryPolicy::new(MAX_WORKER_RECOVERIES) {
        Ok(policy) => policy,
        Err(_) => {
            settle_worker_lost(&app, job_id).await;
            stop_worker(&app);
            return;
        }
    };
    for _ in 0..MAX_POLL_ATTEMPTS {
        tokio::time::sleep(POLL_INTERVAL).await;
        let Some(scheduler) = app.try_state::<LocalEditingJobScheduler>() else {
            break;
        };
        let Some(workspaces) = app.try_state::<VideoJobWorkspaceStore>() else {
            break;
        };
        let Some(orchestrator) = app.try_state::<LocalVideoOrchestrator>() else {
            break;
        };
        let snapshot =
            match scheduler.poll_with_recovery(&workspaces, &orchestrator, job_id, recovery_policy)
            {
                Ok(Some(snapshot)) => snapshot,
                Ok(None) => continue,
                Err(_) => {
                    break;
                }
            };
        if !matches!(
            snapshot.status(),
            LocalEditingJobStatus::Succeeded
                | LocalEditingJobStatus::Failed
                | LocalEditingJobStatus::Cancelled
        ) {
            continue;
        }
        let (Some(client), Some(vault)) = (
            app.try_state::<ControlPlaneClient>(),
            app.try_state::<ProductionDeviceCredentialVault>(),
        ) else {
            break;
        };
        match snapshot.status() {
            LocalEditingJobStatus::Succeeded => {
                let Some(artifact_id) = snapshot.output_artifact_id() else {
                    let _ = fail_current_job(
                        &client,
                        &vault,
                        &job_id.hyphenated().to_string(),
                        EditingJobFailureCode::WorkerLost,
                    )
                    .await;
                    break;
                };
                let imported = workspaces.open(job_id).and_then(|workspace| {
                    workspaces.import_output_with_id(
                        &workspace,
                        artifact_id,
                        OUTPUT_FILE_NAME,
                        "video/mp4",
                        "rendered_video",
                    )
                });
                if imported.is_err() {
                    let _ = fail_current_job(
                        &client,
                        &vault,
                        &job_id.hyphenated().to_string(),
                        EditingJobFailureCode::ResourceExhausted,
                    )
                    .await;
                    break;
                }
                let _ = succeed_current_job(
                    &client,
                    &vault,
                    &job_id.hyphenated().to_string(),
                    &artifact_id.hyphenated().to_string(),
                )
                .await;
            }
            LocalEditingJobStatus::Failed => {
                let code = snapshot
                    .failure_code()
                    .map(local_failure_code)
                    .unwrap_or(EditingJobFailureCode::WorkerLost);
                let _ =
                    fail_current_job(&client, &vault, &job_id.hyphenated().to_string(), code).await;
            }
            LocalEditingJobStatus::Cancelled => {
                let _ = fail_current_job(
                    &client,
                    &vault,
                    &job_id.hyphenated().to_string(),
                    EditingJobFailureCode::WorkerLost,
                )
                .await;
            }
            _ => {}
        }
        stop_worker(&app);
        return;
    }
    settle_worker_lost(&app, job_id).await;
    stop_worker(&app);
}

pub async fn dispatch_submitted_job<R: Runtime>(
    app: &tauri::AppHandle<R>,
    submitted: &EditingJobSnapshot,
) -> Result<(), LocalEditingRuntimeError> {
    if submitted.status() != EditingJobStatus::Queued {
        return Err(runtime_unavailable());
    }
    let client = app
        .try_state::<ControlPlaneClient>()
        .ok_or_else(runtime_unavailable)?;
    let vault = app
        .try_state::<ProductionDeviceCredentialVault>()
        .ok_or_else(runtime_unavailable)?;
    let runtime = app
        .try_state::<LocalEditingRuntime>()
        .ok_or_else(runtime_unavailable)?;
    let scheduler = app
        .try_state::<LocalEditingJobScheduler>()
        .ok_or_else(runtime_unavailable)?;
    let workspaces = app
        .try_state::<VideoJobWorkspaceStore>()
        .ok_or_else(runtime_unavailable)?;
    let orchestrator = app
        .try_state::<LocalVideoOrchestrator>()
        .ok_or_else(runtime_unavailable)?;
    let project = find_project(&client, &vault, submitted.project_id()).await?;
    let timeline = client
        .get_editing_project_timeline(&vault, submitted.project_id())
        .await
        .map_err(|_| runtime_unavailable())?
        .ok_or_else(runtime_unavailable)?;
    if timeline.timeline_id() != submitted.timeline_id()
        || timeline.project_id() != submitted.project_id()
        || timeline.revision() != submitted.timeline_revision()
    {
        return Err(runtime_unavailable());
    }
    let materials = load_materials(&client, &vault, &timeline).await?;
    let request_document = RenderRequest {
        schema_version: RENDER_REQUEST_SCHEMA,
        job_id: submitted.job_id(),
        project: &project,
        timeline: &timeline,
        materials: materials
            .iter()
            .map(|material| RenderMaterial {
                material_id: material.material_id(),
                has_audio: material.has_audio(),
            })
            .collect(),
    };
    let payload = serde_json::to_vec(&request_document).map_err(|_| runtime_unavailable())?;
    let (job_id, request) = parse_job_request(submitted)?;
    scheduler
        .create(&workspaces, job_id, &request)
        .map_err(|_| runtime_unavailable())?;
    let workspace = workspaces.open(job_id).map_err(|_| runtime_unavailable())?;
    workspaces
        .save_checkpoint(&workspace, RENDER_REQUEST_CHECKPOINT, &payload)
        .map_err(|_| runtime_unavailable())?;
    let resource_directory = app
        .path()
        .resource_dir()
        .map_err(|_| runtime_unavailable())?;
    let toolchain =
        VideoMediaToolchain::load(&resource_directory).map_err(|_| runtime_unavailable())?;
    let launch = worker_launch(
        worker_executable(&resource_directory),
        runtime.app_data_directory.clone(),
        &toolchain,
    )?;
    orchestrator
        .start(launch)
        .map_err(|_| runtime_unavailable())?;
    let running = match client
        .reconcile_editing_job(&vault, submitted, EditingJobStatus::Running, None, None)
        .await
    {
        Ok(running) => running,
        Err(_) => {
            let _ = orchestrator.stop(VideoWorkerKind::Python);
            return Err(runtime_unavailable());
        }
    };
    if scheduler
        .dispatch(&workspaces, &orchestrator, job_id)
        .is_err()
    {
        let _ = mark_failed(&client, &vault, &running, EditingJobFailureCode::WorkerLost).await;
        let _ = orchestrator.stop(VideoWorkerKind::Python);
        return Err(runtime_unavailable());
    }
    tauri::async_runtime::spawn(monitor(app.clone(), job_id));
    Ok(())
}

pub async fn fail_submitted_job<R: Runtime>(
    app: &tauri::AppHandle<R>,
    submitted: &EditingJobSnapshot,
) -> Result<(), LocalEditingRuntimeError> {
    let client = app
        .try_state::<ControlPlaneClient>()
        .ok_or_else(runtime_unavailable)?;
    let vault = app
        .try_state::<ProductionDeviceCredentialVault>()
        .ok_or_else(runtime_unavailable)?;
    fail_current_job(
        &client,
        &vault,
        submitted.job_id(),
        EditingJobFailureCode::WorkerLost,
    )
    .await
}

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
    VideoWorkerMediaToolsConfiguration, VideoWorkerRestartPolicy, VideoWorkerState,
};
use crate::material_video_studio::WORKER_VERSION;
use crate::model_service_settings::ProductionModelServiceSettings;
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

pub(crate) fn ensure_worker<R: Runtime>(
    app: &tauri::AppHandle<R>,
) -> Result<(), LocalEditingRuntimeError> {
    let orchestrator = app
        .try_state::<LocalVideoOrchestrator>()
        .ok_or_else(runtime_unavailable)?;
    let status = orchestrator
        .status(VideoWorkerKind::Python)
        .map_err(|_| runtime_unavailable())?;
    if status.state() == VideoWorkerState::Running {
        return orchestrator
            .health(VideoWorkerKind::Python)
            .map_err(|_| runtime_unavailable());
    }
    let runtime = app
        .try_state::<LocalEditingRuntime>()
        .ok_or_else(runtime_unavailable)?;
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
        .map(|_| ())
        .map_err(|_| runtime_unavailable())
}

pub(crate) fn ensure_smart_edit_worker<R: Runtime>(
    app: &tauri::AppHandle<R>,
) -> Result<(), LocalEditingRuntimeError> {
    let orchestrator = app
        .try_state::<LocalVideoOrchestrator>()
        .ok_or_else(runtime_unavailable)?;
    let settings = app
        .try_state::<ProductionModelServiceSettings>()
        .ok_or_else(runtime_unavailable)?;
    let script_model = settings
        .material_video_script_model()
        .map_err(|_| runtime_unavailable())?;
    let status = orchestrator
        .status(VideoWorkerKind::Python)
        .map_err(|_| runtime_unavailable())?;
    if status.state() == VideoWorkerState::Running {
        if orchestrator
            .worker_uses_script_model(&script_model)
            .map_err(|_| runtime_unavailable())?
        {
            return orchestrator
                .health(VideoWorkerKind::Python)
                .map_err(|_| runtime_unavailable());
        }
        if orchestrator
            .local_editing_job_owner()
            .map_err(|_| runtime_unavailable())?
            .is_some()
            || orchestrator
                .smart_edit_job_owner()
                .map_err(|_| runtime_unavailable())?
                .is_some()
        {
            return Err(runtime_unavailable());
        }
        orchestrator
            .stop(VideoWorkerKind::Python)
            .map_err(|_| runtime_unavailable())?;
    }
    let runtime = app
        .try_state::<LocalEditingRuntime>()
        .ok_or_else(runtime_unavailable)?;
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
    )?
    .with_script_model(script_model);
    orchestrator
        .start(launch)
        .map(|_| ())
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
    if failure_reconciliation_required(current.status())? {
        mark_failed(client, vault, &current, code).await?;
    }
    Ok(())
}

/// Whether a job in this status still has to be marked failed.
///
/// The same shape as `cancel_reconciliation_required` below, and for the same
/// reason: the decision is a table, and a table is worth reading and testing on
/// its own rather than inline in an async call chain. `Failed` answers `false`
/// rather than erroring because settling an already-failed job is a retry, not
/// a fault.
fn failure_reconciliation_required(
    status: EditingJobStatus,
) -> Result<bool, LocalEditingRuntimeError> {
    match status {
        EditingJobStatus::Queued | EditingJobStatus::Running => Ok(true),
        EditingJobStatus::Failed => Ok(false),
        EditingJobStatus::Cancelling
        | EditingJobStatus::Succeeded
        | EditingJobStatus::Cancelled => Err(runtime_unavailable()),
    }
}

fn cancel_reconciliation_required(
    status: EditingJobStatus,
) -> Result<bool, LocalEditingRuntimeError> {
    match status {
        EditingJobStatus::Cancelling => Ok(true),
        EditingJobStatus::Cancelled => Ok(false),
        EditingJobStatus::Queued
        | EditingJobStatus::Running
        | EditingJobStatus::Succeeded
        | EditingJobStatus::Failed => Err(runtime_unavailable()),
    }
}

async fn cancel_current_job(
    client: &ControlPlaneClient,
    vault: &ProductionDeviceCredentialVault,
    job_id: &str,
) -> Result<(), LocalEditingRuntimeError> {
    let current = client
        .get_editing_job(vault, job_id)
        .await
        .map_err(|_| runtime_unavailable())?;
    if cancel_reconciliation_required(current.status())? {
        client
            .reconcile_editing_job(vault, &current, EditingJobStatus::Cancelled, None, None)
            .await
            .map_err(|_| runtime_unavailable())?;
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
                let _ = cancel_current_job(&client, &vault, &job_id.hyphenated().to_string()).await;
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
    let coordinator = app
        .try_state::<crate::local_material_library::LocalMaterialLibraryCoordinator>()
        .ok_or_else(runtime_unavailable)?;
    let _operation = coordinator.acquire().await;
    ensure_worker(app)?;
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

#[cfg(test)]
mod tests {
    use super::*;

    fn snapshot(
        job_id: &str,
        project_id: &str,
        timeline_id: &str,
        timeline_revision: u64,
    ) -> EditingJobSnapshot {
        serde_json::from_value(serde_json::json!({
            "jobId": job_id,
            "projectId": project_id,
            "timelineId": timeline_id,
            "timelineRevision": timeline_revision,
            "status": "queued",
            "failureCode": null,
            "outputArtifactId": null,
            "createdAt": "2026-08-04T00:00:00Z",
            "updatedAt": "2026-08-04T00:00:00Z",
        }))
        .expect("editing job snapshot")
    }

    const JOB: &str = "123e4567-e89b-42d3-a456-426614174160";
    const PROJECT: &str = "223e4567-e89b-42d3-a456-426614174161";
    const TIMELINE: &str = "323e4567-e89b-42d3-a456-426614174162";

    /// 失败收尾的决策表，和上面那条取消的决策表是对称的一对。
    ///
    /// 三种答案各有各的后果：`Ok(true)` 去标失败；`Ok(false)` 是**幂等**——已经
    /// 失败过的任务再收一次不该报错，否则一次重试就会把正常路径变成错误路径；
    /// `Err` 是拒绝——已成功或已取消的任务被要求标失败，说明调用方拿的是过期快照，
    /// 这时候改状态会把用户已经看到的结果改掉。
    #[test]
    fn only_an_unfinished_job_is_marked_failed_and_a_failed_one_is_idempotent() {
        for status in [EditingJobStatus::Queued, EditingJobStatus::Running] {
            assert_eq!(failure_reconciliation_required(status), Ok(true));
        }
        assert_eq!(
            failure_reconciliation_required(EditingJobStatus::Failed),
            Ok(false),
            "已经失败的任务再收一次必须是幂等的，不是错误"
        );
        for status in [
            EditingJobStatus::Cancelling,
            EditingJobStatus::Succeeded,
            EditingJobStatus::Cancelled,
        ] {
            assert_eq!(
                failure_reconciliation_required(status),
                Err(runtime_unavailable()),
                "{status:?} 已经有终态了，把它改成失败等于改掉用户看到的结果"
            );
        }
    }

    /// 本机失败码到 Control Plane 失败码的映射。
    ///
    /// 编译器保证这张表是**全的**（match 没有 `_` 分支），但保证不了它是**对的**：
    /// 把「字体缺失」映到「渲染失败」照样编译通过，用户看到的就是一条查不下去的
    /// 错误原因。所以这里逐条钉死，并额外断言映射是单射——两个本机码塌成同一个
    /// 上报码，等于永久丢掉区分度。
    #[test]
    fn every_local_failure_code_keeps_its_own_meaning_upstream() {
        let pairs = [
            (
                LocalEditingJobFailureCode::InvalidTimeline,
                EditingJobFailureCode::InvalidTimeline,
            ),
            (
                LocalEditingJobFailureCode::MaterialUnavailable,
                EditingJobFailureCode::MaterialUnavailable,
            ),
            (
                LocalEditingJobFailureCode::MaterialUnsupported,
                EditingJobFailureCode::MaterialUnsupported,
            ),
            (
                LocalEditingJobFailureCode::FontUnavailable,
                EditingJobFailureCode::FontUnavailable,
            ),
            (
                LocalEditingJobFailureCode::RenderFailed,
                EditingJobFailureCode::RenderFailed,
            ),
            (
                LocalEditingJobFailureCode::ResourceExhausted,
                EditingJobFailureCode::ResourceExhausted,
            ),
            (
                LocalEditingJobFailureCode::PermissionDenied,
                EditingJobFailureCode::PermissionDenied,
            ),
            (
                LocalEditingJobFailureCode::WorkerLost,
                EditingJobFailureCode::WorkerLost,
            ),
        ];
        for (local, upstream) in pairs {
            assert_eq!(local_failure_code(local), upstream, "{local:?} 映错了");
        }
        for (index, (_, first)) in pairs.iter().enumerate() {
            for (_, second) in pairs.iter().skip(index + 1) {
                assert_ne!(first, second, "两个本机失败码塌成了同一个上报码");
            }
        }
    }

    /// 快照到 Worker 请求的转换，判据全在边界上。
    ///
    /// `timeline_revision` 在快照里是 `u64`，在 Worker 请求里是 `u32`，而请求本身
    /// 只接受 `1..=i32::MAX`。三段不同的上界叠在一起，端点最容易漏。
    #[test]
    fn a_job_request_is_parsed_only_from_canonical_identifiers_and_a_usable_revision() {
        let (job_id, request) =
            parse_job_request(&snapshot(JOB, PROJECT, TIMELINE, 3)).expect("canonical job");
        assert_eq!(job_id, Uuid::parse_str(JOB).unwrap());
        assert_eq!(request.project_id(), Uuid::parse_str(PROJECT).unwrap());
        assert_eq!(request.timeline_id(), Uuid::parse_str(TIMELINE).unwrap());
        assert_eq!(request.timeline_revision(), 3);

        // 两个端点都要在：只测被拒的一侧，把下界改成 2 也照样绿。
        assert!(parse_job_request(&snapshot(JOB, PROJECT, TIMELINE, 1)).is_ok());
        assert!(
            parse_job_request(&snapshot(JOB, PROJECT, TIMELINE, i32::MAX as u64)).is_ok(),
            "上界本身必须是可用的"
        );
        for revision in [
            0,
            i32::MAX as u64 + 1,
            u32::MAX as u64 + 1,
            u64::MAX,
            // 这一个是**必须**的，别的都替代不了它：`u32::try_from` 换成 `as u32`
            // 时上面那几个仍然会被拒（截断后落在 0 或 > i32::MAX，下游照样拦），
            // 于是「上界还检查着吗」这个问题根本没人问。只有截断后落回**合法
            // 区间**的值才问得出来——2^32 + 5 会变成 5，一个 revision 高得离谱的
            // 任务会被当成第 5 版渲染，而且全程不报错。
            u32::MAX as u64 + 6,
        ] {
            assert!(
                parse_job_request(&snapshot(JOB, PROJECT, TIMELINE, revision)).is_err(),
                "revision {revision} 应当被拒"
            );
        }

        for (job, project, timeline) in [
            ("not-a-uuid", PROJECT, TIMELINE),
            (JOB, "not-a-uuid", TIMELINE),
            (JOB, PROJECT, "not-a-uuid"),
            // 非 v4：版本位不是 4，Worker 请求明确拒绝。
            (JOB, "223e4567-e89b-12d3-a456-426614174161", TIMELINE),
            // 项目与时间轴不能是同一个标识。
            (JOB, PROJECT, PROJECT),
        ] {
            assert!(
                parse_job_request(&snapshot(job, project, timeline, 3)).is_err(),
                "({job}, {project}, {timeline}) 应当被拒",
            );
        }
    }

    #[test]
    fn a_worker_cancel_only_confirms_a_control_plane_cancellation() {
        assert_eq!(
            cancel_reconciliation_required(EditingJobStatus::Cancelling),
            Ok(true)
        );
        assert_eq!(
            cancel_reconciliation_required(EditingJobStatus::Cancelled),
            Ok(false)
        );
        for status in [
            EditingJobStatus::Queued,
            EditingJobStatus::Running,
            EditingJobStatus::Succeeded,
            EditingJobStatus::Failed,
        ] {
            assert_eq!(
                cancel_reconciliation_required(status),
                Err(runtime_unavailable())
            );
        }
    }
}

//! Tauri-owned bridge from the product entry to the private material-video WebUI.

use crate::local_video_orchestrator::{
    LocalVideoOrchestrator, VideoWorkerKind, VideoWorkerLaunch, VideoWorkerRestartPolicy,
    VideoWorkerState,
};
use crate::model_service_settings::ProductionModelServiceSettings;
use crate::video_job_workspace::{
    RenderedVideoArtifactPayload, VideoJobWorkspaceStore, VideoWorkspaceDisposition,
    VideoWorkspaceError, VideoWorkspaceErrorCode,
};
use crate::video_media_toolchain::VideoMediaToolchain;
use serde::{Deserialize, Serialize};
use std::fmt;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;
use tauri::webview::NewWindowResponse;
use tauri::{Manager, WebviewBuilder, WebviewUrl};

const MAIN_WINDOW_LABEL: &str = "main";
const WEBVIEW_LABEL: &str = "material-video-studio";
pub(crate) const WORKER_VERSION: &str = "1.3.2";
const INIT_SCRIPT: &str = include_str!("material_video_studio_init.js");
const JOB_CHECKPOINT: &str = "material-render-job";
const OBSERVATION_FILE: &str = "material-render-job-observation.json";
const CANCEL_FILE: &str = "material-render-job-cancel-request";
const MAX_PROJECTED_JOBS: usize = 100;
static ACTIVE_WORKSPACE: Mutex<Option<uuid::Uuid>> = Mutex::new(None);
#[cfg(feature = "control-plane-e2e")]
static ACCEPTANCE_RESULT: Mutex<Option<MaterialVideoStudioAcceptanceSnapshot>> = Mutex::new(None);

#[cfg(feature = "control-plane-e2e")]
const EMBEDDED_READINESS_SCRIPT: &str = r##"
(() => {
  const state = document.documentElement.getAttribute("data-automation-tool-studio-state");
  if (state === "ready" || state === "failed") {
    history.replaceState(
      null,
      "",
      `${location.pathname}${location.search}#automation-tool-im05-probe-${state}`,
    );
  }
})();
"##;

#[cfg(feature = "control-plane-e2e")]
const EMBEDDED_ACCEPTANCE_SCRIPT: &str = r##"
(() => {
  const passed = "#automation-tool-im05-accepted";
  const failed = (reason) => {
    history.replaceState(null, "", `${location.pathname}${location.search}#automation-tool-im05-failed-${reason}`);
  };
  const deadline = Date.now() + 120000;
  const waitForSettings = (subject) => {
    const timer = setInterval(() => {
      const body = document.body?.innerText || "";
      if (body.includes("Pexels")) {
        clearInterval(timer);
        const modelTabs = Array.from(document.querySelectorAll("[role='tab']")).filter(
          (node) => /大模型设置/.test(node.textContent || "") && getComputedStyle(node).display !== "none",
        );
        if (modelTabs.length || subject.value !== "用三十秒解释为什么雨后空气更清新") {
          failed("interaction");
        } else {
          history.replaceState(null, "", `${location.pathname}${location.search}${passed}`);
        }
      } else if (Date.now() > deadline) {
        clearInterval(timer);
        failed("settings-timeout");
      }
    }, 250);
  };
  const timer = setInterval(() => {
    const state = document.documentElement.getAttribute("data-automation-tool-studio-state");
    if (state === "failed") {
      clearInterval(timer);
      failed("guard");
      return;
    }
    if (state !== "ready") {
      if (Date.now() > deadline) {
        clearInterval(timer);
        failed("ready-timeout");
      }
      return;
    }
    clearInterval(timer);
    const body = document.body?.innerText || "";
    const subject = document.querySelector("input[aria-label*='视频主题']");
    const settings = document.querySelector("button[aria-label='制作服务设置']");
    const externalLinks = Array.from(document.querySelectorAll("a[href]")).filter((anchor) => {
      try {
        const resolved = document.createElement("a");
        resolved.href = anchor.getAttribute("href") || "";
        return resolved.origin !== location.origin;
      } catch {
        return true;
      }
    });
    const duplicateTaskManager = document.querySelector(".st-key-task_manager_entry");
    const font = getComputedStyle(document.body).fontFamily;
    if (
      !subject ||
      !settings ||
      !/视频文案/.test(body) ||
      !/生成视频/.test(body) ||
      !/智能素材成片/.test(body) ||
      /sk-im05-invalid-desktop-key/.test(body) ||
      externalLinks.length !== 0 ||
      !/PingFang SC|Microsoft YaHei|Inter/.test(font) ||
      (duplicateTaskManager && getComputedStyle(duplicateTaskManager).display !== "none")
    ) {
      failed("structure");
      return;
    }
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    if (!setter) {
      failed("input");
      return;
    }
    setter.call(subject, "用三十秒解释为什么雨后空气更清新");
    subject.dispatchEvent(new Event("input", { bubbles: true }));
    subject.dispatchEvent(new Event("change", { bubbles: true }));
    settings.click();
    waitForSettings(subject);
  }, 250);
})();
"##;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MaterialRenderJobStatus {
    Running,
    Succeeded,
    Failed,
    Cancelled,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct MaterialRenderObservation {
    schema_version: u8,
    render_job_id: uuid::Uuid,
    worker_task_id: uuid::Uuid,
    revision: u64,
    status: MaterialRenderJobStatus,
    progress_percent: u8,
    subject: String,
    output_file: Option<String>,
    failure_code: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MaterialRenderJobSnapshot {
    render_job_id: uuid::Uuid,
    revision: u64,
    status: MaterialRenderJobStatus,
    progress_percent: u8,
    subject: String,
    artifact_id: Option<uuid::Uuid>,
    artifact_size_bytes: Option<u64>,
    failure_code: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MaterialVideoStudioState {
    Opened,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MaterialVideoStudioSnapshot {
    state: MaterialVideoStudioState,
    model_id: String,
}

/// Logical bounds owned by the product page, never by the private WebUI.
///
/// React sends only this rectangle.  The loopback port and capability path
/// remain native-only and are used directly to construct the child WebView.
#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MaterialVideoStudioView {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    visible: bool,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MaterialVideoStudioAcceptanceSnapshot {
    state: &'static str,
    failure: Option<String>,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MaterialVideoStudioCleanupSnapshot {
    view_mounted: bool,
    worker_stopped: bool,
    active_workspace: bool,
}

impl MaterialVideoStudioView {
    fn logical_geometry(
        self,
    ) -> Result<(tauri::LogicalPosition<f64>, tauri::LogicalSize<f64>), MaterialVideoStudioError>
    {
        let finite = [self.x, self.y, self.width, self.height]
            .into_iter()
            .all(f64::is_finite);
        if !finite
            || self.x.abs() > 8_192.0
            || self.y.abs() > 8_192.0
            || !(320.0..=8_192.0).contains(&self.width)
            || !(240.0..=8_192.0).contains(&self.height)
        {
            return Err(view_unavailable());
        }
        Ok((
            tauri::LogicalPosition::new(self.x, self.y),
            tauri::LogicalSize::new(self.width, self.height),
        ))
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MaterialVideoStudioErrorCode {
    ConfigurationRequired,
    ProcessUnavailable,
    StorageUnavailable,
    ViewUnavailable,
    JobUnavailable,
    // A submission whose fields failed validation: retrying without changing
    // the input is useless, which is exactly what JobUnavailable (its former
    // disguise) invites the user to do (REVIEW-2026-08-06 M2).
    ProtocolMismatch,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MaterialVideoStudioError {
    code: MaterialVideoStudioErrorCode,
    retryable: bool,
}

impl Serialize for MaterialVideoStudioError {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        crate::command_error::serialize(&self.code, Some(self.retryable), serializer)
    }
}

impl MaterialVideoStudioError {
    pub const fn code(self) -> MaterialVideoStudioErrorCode {
        self.code
    }
}

impl fmt::Display for MaterialVideoStudioError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Material video studio is unavailable")
    }
}

impl std::error::Error for MaterialVideoStudioError {}

struct StartedMaterialVideoService {
    snapshot: MaterialVideoStudioSnapshot,
    endpoint_url: url::Url,
    allowed_port: u16,
    allowed_path: String,
    workspace_id: uuid::Uuid,
}

pub(crate) fn open(
    app: &tauri::AppHandle,
    orchestrator: &LocalVideoOrchestrator,
    settings: &ProductionModelServiceSettings,
    workspaces: &VideoJobWorkspaceStore,
    view: MaterialVideoStudioView,
) -> Result<MaterialVideoStudioSnapshot, MaterialVideoStudioError> {
    view.logical_geometry()?;
    if app.get_webview(WEBVIEW_LABEL).is_some() {
        let status = orchestrator
            .status(VideoWorkerKind::Python)
            .map_err(|_| process_unavailable())?;
        if status.state() != VideoWorkerState::Running || !status.web_ui_available() {
            return Err(process_unavailable());
        }
        update_embedded_view(app, view)?;
        return Ok(MaterialVideoStudioSnapshot {
            state: MaterialVideoStudioState::Opened,
            model_id: status
                .script_model_id()
                .ok_or_else(configuration_required)?
                .to_owned(),
        });
    }

    let started = start_service(app, orchestrator, settings, workspaces)?;
    if let Err(error) = mount_embedded_view(app, &started, view) {
        let _ = set_active_workspace(None);
        let _ = orchestrator.stop(VideoWorkerKind::Python);
        if let Ok(workspace) = workspaces.open(started.workspace_id) {
            cleanup_workspace(workspaces, &workspace);
        }
        return Err(error);
    }
    Ok(started.snapshot)
}

fn start_service(
    app: &tauri::AppHandle,
    orchestrator: &LocalVideoOrchestrator,
    settings: &ProductionModelServiceSettings,
    workspaces: &VideoJobWorkspaceStore,
) -> Result<StartedMaterialVideoService, MaterialVideoStudioError> {
    let script_model = settings
        .material_video_script_model()
        .map_err(|_| configuration_required())?;
    let model_id = script_model.model_id().to_owned();
    // Both packaged resources are resolved before a workspace exists, so a
    // tampered or missing package refuses the studio instead of leaving an
    // orphan job directory behind.
    let executable = worker_executable(app)?;
    let toolchain = media_toolchain(app)?;
    let workspace = workspaces.create_new().map_err(map_workspace_error)?;
    let asset_root = workspaces
        .worker_asset_directory(&workspace)
        .map_err(map_workspace_error)?;
    let launch = match material_worker_launch(executable, asset_root, &toolchain) {
        Ok(launch) => launch.with_script_model(script_model).with_web_ui(),
        Err(error) => {
            cleanup_workspace(workspaces, &workspace);
            return Err(error);
        }
    };
    if set_active_workspace(Some(workspace.job_id())).is_err() {
        cleanup_workspace(workspaces, &workspace);
        return Err(job_unavailable());
    }
    let status = match orchestrator.start(launch) {
        Ok(status) => status,
        Err(_) => {
            let _ = set_active_workspace(None);
            cleanup_workspace(workspaces, &workspace);
            return Err(process_unavailable());
        }
    };
    let endpoint = match orchestrator.web_ui_endpoint(VideoWorkerKind::Python) {
        Ok(endpoint) => endpoint,
        Err(_) => {
            let _ = set_active_workspace(None);
            let _ = orchestrator.stop(VideoWorkerKind::Python);
            cleanup_workspace(workspaces, &workspace);
            return Err(process_unavailable());
        }
    };
    if orchestrator.verify_web_ui(VideoWorkerKind::Python).is_err() {
        let _ = set_active_workspace(None);
        let _ = orchestrator.stop(VideoWorkerKind::Python);
        cleanup_workspace(workspaces, &workspace);
        return Err(process_unavailable());
    }
    let endpoint_url = match endpoint.url() {
        Ok(value) => value,
        Err(_) => {
            let _ = set_active_workspace(None);
            let _ = orchestrator.stop(VideoWorkerKind::Python);
            cleanup_workspace(workspaces, &workspace);
            return Err(process_unavailable());
        }
    };
    Ok(StartedMaterialVideoService {
        snapshot: MaterialVideoStudioSnapshot {
            state: MaterialVideoStudioState::Opened,
            model_id: status
                .script_model_id()
                .unwrap_or(model_id.as_str())
                .to_owned(),
        },
        endpoint_url,
        allowed_port: endpoint.port(),
        allowed_path: format!("/{}/", endpoint.path()),
        workspace_id: workspace.job_id(),
    })
}

fn mount_embedded_view(
    app: &tauri::AppHandle,
    started: &StartedMaterialVideoService,
    view: MaterialVideoStudioView,
) -> Result<(), MaterialVideoStudioError> {
    let (position, size) = view.logical_geometry()?;
    let allowed_port = started.allowed_port;
    let allowed_path = started.allowed_path.clone();
    let builder = WebviewBuilder::new(
        WEBVIEW_LABEL,
        WebviewUrl::External(started.endpoint_url.clone()),
    )
    .initialization_script(INIT_SCRIPT)
    .devtools(false)
    .on_navigation(move |url| {
        url.scheme() == "http"
            && url.host_str() == Some("127.0.0.1")
            && url.port() == Some(allowed_port)
            && (url.path() == allowed_path.trim_end_matches('/')
                || url.path().starts_with(&allowed_path))
    })
    .on_new_window(|_, _| NewWindowResponse::Deny)
    .on_download(|_, _| false);
    let window = app
        .get_window(MAIN_WINDOW_LABEL)
        .ok_or_else(view_unavailable)?;
    let webview = window
        .add_child(builder, position, size)
        .map_err(|_| view_unavailable())?;
    if view.visible {
        webview.show().map_err(|_| view_unavailable())?;
    } else {
        webview.hide().map_err(|_| view_unavailable())?;
    }
    Ok(())
}

pub(crate) fn update_embedded_view(
    app: &tauri::AppHandle,
    view: MaterialVideoStudioView,
) -> Result<(), MaterialVideoStudioError> {
    let (position, size) = view.logical_geometry()?;
    let webview = app
        .get_webview(WEBVIEW_LABEL)
        .ok_or_else(view_unavailable)?;
    webview
        .set_bounds(tauri::Rect {
            position: tauri::Position::Logical(position),
            size: tauri::Size::Logical(size),
        })
        .map_err(|_| view_unavailable())?;
    if view.visible {
        webview.show().map_err(|_| view_unavailable())
    } else {
        webview.hide().map_err(|_| view_unavailable())
    }
}

pub(crate) fn close_embedded_view(
    app: &tauri::AppHandle,
    orchestrator: &LocalVideoOrchestrator,
    workspaces: &VideoJobWorkspaceStore,
) -> Result<(), MaterialVideoStudioError> {
    let view_failed = app
        .get_webview(WEBVIEW_LABEL)
        .is_some_and(|webview| webview.close().is_err());
    let process_failed = orchestrator.stop(VideoWorkerKind::Python).is_err();
    let workspace_id = active_workspace()?;
    set_active_workspace(None)?;
    if let Some(workspace_id) = workspace_id {
        let workspace = workspaces.open(workspace_id).map_err(map_workspace_error)?;
        workspaces
            .finish(&workspace, VideoWorkspaceDisposition::Keep)
            .map_err(map_workspace_error)?;
    }
    if view_failed {
        Err(view_unavailable())
    } else if process_failed {
        Err(process_unavailable())
    } else {
        Ok(())
    }
}

#[cfg(feature = "control-plane-e2e")]
pub(crate) fn exercise_embedded_view_for_acceptance(
    app: tauri::AppHandle,
) -> Result<MaterialVideoStudioAcceptanceSnapshot, MaterialVideoStudioError> {
    let running = MaterialVideoStudioAcceptanceSnapshot {
        state: "running",
        failure: None,
    };
    *ACCEPTANCE_RESULT.lock().map_err(|_| view_unavailable())? = Some(running.clone());
    tauri::async_runtime::spawn(async move {
        let result = probe_embedded_view_for_acceptance(&app).await;
        let orchestrator = app.state::<LocalVideoOrchestrator>();
        let workspaces = app.state::<VideoJobWorkspaceStore>();
        let cleanup = close_embedded_view(&app, &orchestrator, &workspaces);
        let snapshot = match (result, cleanup) {
            (Ok(snapshot), Ok(())) => snapshot,
            (Err(_), _) => MaterialVideoStudioAcceptanceSnapshot {
                state: "failed",
                failure: Some("native_probe".to_owned()),
            },
            (Ok(_), Err(_)) => MaterialVideoStudioAcceptanceSnapshot {
                state: "failed",
                failure: Some("native_cleanup".to_owned()),
            },
        };
        if let Ok(mut acceptance_result) = ACCEPTANCE_RESULT.lock() {
            *acceptance_result = Some(snapshot);
        }
    });
    Ok(running)
}

#[cfg(feature = "control-plane-e2e")]
pub(crate) fn inspect_embedded_view_exercise_for_acceptance(
) -> Result<MaterialVideoStudioAcceptanceSnapshot, MaterialVideoStudioError> {
    ACCEPTANCE_RESULT
        .lock()
        .map_err(|_| view_unavailable())?
        .clone()
        .ok_or_else(view_unavailable)
}

#[cfg(feature = "control-plane-e2e")]
async fn probe_embedded_view_for_acceptance(
    app: &tauri::AppHandle,
) -> Result<MaterialVideoStudioAcceptanceSnapshot, MaterialVideoStudioError> {
    let native_windows = app.webview_windows();
    if native_windows.len() != 1 || !native_windows.contains_key(MAIN_WINDOW_LABEL) {
        return Ok(MaterialVideoStudioAcceptanceSnapshot {
            state: "failed",
            failure: Some("native_window_count".to_owned()),
        });
    }
    let mount_deadline = std::time::Instant::now() + Duration::from_secs(30);
    let webview = loop {
        if let Some(webview) = app.get_webview(WEBVIEW_LABEL) {
            break webview;
        }
        if std::time::Instant::now() >= mount_deadline {
            return Ok(MaterialVideoStudioAcceptanceSnapshot {
                state: "failed",
                failure: Some("view_mount_timeout".to_owned()),
            });
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    };
    let readiness_deadline = std::time::Instant::now() + Duration::from_secs(120);
    loop {
        webview
            .eval(EMBEDDED_READINESS_SCRIPT)
            .map_err(|_| view_unavailable())?;
        let url = webview.url().map_err(|_| view_unavailable())?;
        match url.fragment() {
            Some("automation-tool-im05-probe-ready") => break,
            Some("automation-tool-im05-probe-failed") => {
                return Ok(MaterialVideoStudioAcceptanceSnapshot {
                    state: "failed",
                    failure: Some("guard".to_owned()),
                });
            }
            _ if std::time::Instant::now() >= readiness_deadline => {
                return Ok(MaterialVideoStudioAcceptanceSnapshot {
                    state: "failed",
                    failure: Some("ready_probe_timeout".to_owned()),
                });
            }
            _ => tokio::time::sleep(Duration::from_millis(250)).await,
        }
    }
    webview
        .eval(EMBEDDED_ACCEPTANCE_SCRIPT)
        .map_err(|_| view_unavailable())?;
    let deadline = std::time::Instant::now() + Duration::from_secs(125);
    loop {
        let url = webview.url().map_err(|_| view_unavailable())?;
        match url.fragment() {
            Some("automation-tool-im05-accepted") => {
                return Ok(MaterialVideoStudioAcceptanceSnapshot {
                    state: "passed",
                    failure: None,
                });
            }
            Some(fragment) if fragment.starts_with("automation-tool-im05-failed-") => {
                return Ok(MaterialVideoStudioAcceptanceSnapshot {
                    state: "failed",
                    failure: fragment
                        .strip_prefix("automation-tool-im05-failed-")
                        .map(str::to_owned),
                });
            }
            _ if std::time::Instant::now() >= deadline => {
                return Ok(MaterialVideoStudioAcceptanceSnapshot {
                    state: "failed",
                    failure: Some("native_timeout".to_owned()),
                });
            }
            _ => tokio::time::sleep(Duration::from_millis(250)).await,
        }
    }
}

#[cfg(feature = "control-plane-e2e")]
pub(crate) fn cleanup_snapshot_for_acceptance(
    app: &tauri::AppHandle,
    orchestrator: &LocalVideoOrchestrator,
) -> Result<MaterialVideoStudioCleanupSnapshot, MaterialVideoStudioError> {
    Ok(MaterialVideoStudioCleanupSnapshot {
        view_mounted: app.get_webview(WEBVIEW_LABEL).is_some(),
        worker_stopped: orchestrator
            .status(VideoWorkerKind::Python)
            .map_err(|_| process_unavailable())?
            .state()
            == VideoWorkerState::Stopped,
        active_workspace: active_workspace()?.is_some(),
    })
}

/// Start one headless montage job: the React form's parameters, the packaged
/// worker, no WebView. The workspace lifecycle, the observation projection,
/// cancel and artifact reads are exactly the interactive studio's — the only
/// thing removed is the Streamlit surface.
pub(crate) fn submit_montage(
    app: &tauri::AppHandle,
    orchestrator: &LocalVideoOrchestrator,
    settings: &ProductionModelServiceSettings,
    workspaces: &VideoJobWorkspaceStore,
    request: crate::local_video_orchestrator::VideoWorkerMontageRequest,
) -> Result<uuid::Uuid, MaterialVideoStudioError> {
    let script_model = settings
        .material_video_script_model()
        .map_err(|_| configuration_required())?;
    let executable = worker_executable(app)?;
    let toolchain = media_toolchain(app)?;
    let workspace = workspaces.create_new().map_err(map_workspace_error)?;
    let asset_root = workspaces
        .worker_asset_directory(&workspace)
        .map_err(map_workspace_error)?;
    let launch = match material_worker_launch(executable, asset_root, &toolchain) {
        Ok(launch) => launch
            .with_script_model(script_model)
            .with_montage_request(request),
        Err(error) => {
            cleanup_workspace(workspaces, &workspace);
            return Err(error);
        }
    };
    if set_active_workspace(Some(workspace.job_id())).is_err() {
        cleanup_workspace(workspaces, &workspace);
        return Err(job_unavailable());
    }
    if orchestrator.start(launch).is_err() {
        let _ = set_active_workspace(None);
        cleanup_workspace(workspaces, &workspace);
        return Err(process_unavailable());
    }
    Ok(workspace.job_id())
}

pub(crate) fn jobs(
    workspaces: &VideoJobWorkspaceStore,
) -> Result<Vec<MaterialRenderJobSnapshot>, MaterialVideoStudioError> {
    if let Some(job_id) = active_workspace()? {
        let workspace = workspaces.open(job_id).map_err(map_workspace_error)?;
        reconcile_active_observation(workspaces, &workspace)?;
    }
    let mut snapshots = Vec::new();
    for workspace in workspaces.list_workspaces().map_err(map_workspace_error)? {
        if let Some(snapshot) = load_projection(workspaces, &workspace)? {
            snapshots.push(snapshot);
            if snapshots.len() > MAX_PROJECTED_JOBS {
                return Err(job_unavailable());
            }
        }
    }
    snapshots.sort_by_key(|value| value.render_job_id);
    Ok(snapshots)
}

fn reconcile_active_observation(
    workspaces: &VideoJobWorkspaceStore,
    workspace: &crate::video_job_workspace::VideoJobWorkspace,
) -> Result<(), MaterialVideoStudioError> {
    let Some(observation) = read_observation(workspaces, workspace)? else {
        return Ok(());
    };
    if load_projection(workspaces, workspace)?
        .as_ref()
        .is_some_and(|value| value.revision >= observation.revision)
    {
        return Ok(());
    }
    let artifact = if observation.status == MaterialRenderJobStatus::Succeeded {
        match workspaces
            .list_artifacts()
            .map_err(map_workspace_error)?
            .into_iter()
            .find(|value| value.job_id() == workspace.job_id())
        {
            Some(value) => Some(value),
            None => Some(
                workspaces
                    .import_output(
                        workspace,
                        observation
                            .output_file
                            .as_deref()
                            .ok_or_else(job_unavailable)?,
                        "video/mp4",
                        "rendered_video",
                    )
                    .map_err(map_workspace_error)?,
            ),
        }
    } else {
        None
    };
    let snapshot = MaterialRenderJobSnapshot {
        render_job_id: observation.render_job_id,
        revision: observation.revision,
        status: observation.status,
        progress_percent: observation.progress_percent,
        subject: observation.subject,
        artifact_id: artifact.as_ref().map(|value| value.artifact_id()),
        artifact_size_bytes: artifact.as_ref().map(|value| value.size_bytes()),
        failure_code: observation.failure_code,
    };
    let payload = serde_json::to_vec(&snapshot).map_err(|_| job_unavailable())?;
    workspaces
        .save_checkpoint(workspace, JOB_CHECKPOINT, &payload)
        .map_err(map_workspace_error)
}

pub(crate) fn cancel(
    workspaces: &VideoJobWorkspaceStore,
    render_job_id: uuid::Uuid,
) -> Result<(), MaterialVideoStudioError> {
    if active_workspace()? != Some(render_job_id) {
        return Err(job_unavailable());
    }
    let workspace = workspaces
        .open(render_job_id)
        .map_err(map_workspace_error)?;
    let runtime = runtime_directory(workspaces, &workspace)?.ok_or_else(job_unavailable)?;
    let marker = runtime.join(CANCEL_FILE);
    match OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&marker)
    {
        Ok(mut file) => file
            .write_all(b"cancel\n")
            .and_then(|_| file.sync_all())
            .map_err(|_| job_unavailable()),
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            let metadata = fs::symlink_metadata(marker).map_err(|_| job_unavailable())?;
            if metadata.file_type().is_file() && !metadata.file_type().is_symlink() {
                Ok(())
            } else {
                Err(job_unavailable())
            }
        }
        Err(_) => Err(job_unavailable()),
    }
}

/// Read a finished smart-material film back so the App can play it.
///
/// Both creation methods import the same kind of artifact, so the reading is
/// the store's shared one; only the failure vocabulary is this studio's. A
/// film that is gone or too large to hold answers "pick another one" rather
/// than reporting a storage fault the user cannot act on.
pub fn read_artifact(
    workspaces: &VideoJobWorkspaceStore,
    artifact_id: uuid::Uuid,
) -> Result<RenderedVideoArtifactPayload, MaterialVideoStudioError> {
    workspaces
        .read_rendered_video_artifact(artifact_id)
        .map_err(|error| match error.code() {
            VideoWorkspaceErrorCode::NotFound | VideoWorkspaceErrorCode::QuotaExceeded => {
                job_unavailable()
            }
            _ => map_workspace_error(error),
        })
}

pub(crate) fn delete_artifact(
    workspaces: &VideoJobWorkspaceStore,
    artifact_id: uuid::Uuid,
) -> Result<(), MaterialVideoStudioError> {
    let mut matched = None;
    for workspace in workspaces.list_workspaces().map_err(map_workspace_error)? {
        if let Some(projection) = load_projection(workspaces, &workspace)? {
            if projection.artifact_id == Some(artifact_id) {
                if matched.is_some() {
                    return Err(job_unavailable());
                }
                matched = Some((workspace, projection));
            }
        }
    }
    let (workspace, mut projection) = matched.ok_or_else(job_unavailable)?;
    workspaces
        .delete_artifact(artifact_id)
        .map_err(map_workspace_error)?;
    projection.artifact_id = None;
    projection.artifact_size_bytes = None;
    let payload = serde_json::to_vec(&projection).map_err(|_| job_unavailable())?;
    workspaces
        .save_checkpoint(&workspace, JOB_CHECKPOINT, &payload)
        .map_err(map_workspace_error)
}

fn active_workspace() -> Result<Option<uuid::Uuid>, MaterialVideoStudioError> {
    ACTIVE_WORKSPACE
        .lock()
        .map(|value| *value)
        .map_err(|_| job_unavailable())
}

fn set_active_workspace(value: Option<uuid::Uuid>) -> Result<(), MaterialVideoStudioError> {
    *ACTIVE_WORKSPACE.lock().map_err(|_| job_unavailable())? = value;
    Ok(())
}

fn load_projection(
    workspaces: &VideoJobWorkspaceStore,
    workspace: &crate::video_job_workspace::VideoJobWorkspace,
) -> Result<Option<MaterialRenderJobSnapshot>, MaterialVideoStudioError> {
    match workspaces.load_checkpoint(workspace, JOB_CHECKPOINT) {
        Ok(payload) => {
            let snapshot: MaterialRenderJobSnapshot =
                serde_json::from_slice(&payload).map_err(|_| job_unavailable())?;
            let valid_failure = match snapshot.status {
                MaterialRenderJobStatus::Failed => {
                    snapshot.failure_code.as_deref() == Some("generation_failed")
                }
                _ => snapshot.failure_code.is_none(),
            };
            if snapshot.render_job_id != workspace.job_id()
                || snapshot.render_job_id.get_version_num() != 4
                || snapshot.revision == 0
                || snapshot.subject.is_empty()
                || snapshot.subject.chars().count() > 240
                || snapshot.progress_percent > 100
                || (snapshot.status == MaterialRenderJobStatus::Succeeded
                    && snapshot.progress_percent != 100)
                || (snapshot.status == MaterialRenderJobStatus::Running
                    && snapshot.progress_percent >= 100)
                || snapshot
                    .artifact_id
                    .is_some_and(|value| value.get_version_num() != 4)
                || snapshot.artifact_id.is_some() != snapshot.artifact_size_bytes.is_some()
                || !valid_failure
            {
                return Err(job_unavailable());
            }
            Ok(Some(snapshot))
        }
        Err(error)
            if error.code() == crate::video_job_workspace::VideoWorkspaceErrorCode::NotFound =>
        {
            Ok(None)
        }
        Err(error) => Err(map_workspace_error(error)),
    }
}

fn read_observation(
    workspaces: &VideoJobWorkspaceStore,
    workspace: &crate::video_job_workspace::VideoJobWorkspace,
) -> Result<Option<MaterialRenderObservation>, MaterialVideoStudioError> {
    let Some(runtime) = runtime_directory(workspaces, workspace)? else {
        return Ok(None);
    };
    let path = runtime.join(OBSERVATION_FILE);
    let metadata = match fs::symlink_metadata(&path) {
        Ok(value) => value,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err(job_unavailable()),
    };
    if !metadata.file_type().is_file() || metadata.len() == 0 || metadata.len() > 64 * 1024 {
        return Err(job_unavailable());
    }
    let observation: MaterialRenderObservation =
        serde_json::from_slice(&fs::read(path).map_err(|_| job_unavailable())?)
            .map_err(|_| job_unavailable())?;
    let valid_output = match observation.status {
        MaterialRenderJobStatus::Succeeded => {
            observation.output_file.as_deref() == Some("material-result.mp4")
        }
        _ => observation.output_file.is_none(),
    };
    let valid_failure = match observation.status {
        MaterialRenderJobStatus::Failed => {
            observation.failure_code.as_deref() == Some("generation_failed")
        }
        _ => observation.failure_code.is_none(),
    };
    let valid_progress = match observation.status {
        MaterialRenderJobStatus::Succeeded => observation.progress_percent == 100,
        MaterialRenderJobStatus::Running => observation.progress_percent < 100,
        MaterialRenderJobStatus::Failed | MaterialRenderJobStatus::Cancelled => true,
    };
    if observation.schema_version != 1
        || observation.render_job_id != workspace.job_id()
        || observation.render_job_id.get_version_num() != 4
        || observation.worker_task_id.get_version_num() != 4
        || observation.revision == 0
        || observation.subject.is_empty()
        || observation.subject.chars().count() > 240
        || !valid_output
        || !valid_failure
        || !valid_progress
    {
        return Err(job_unavailable());
    }
    Ok(Some(observation))
}

fn runtime_directory(
    workspaces: &VideoJobWorkspaceStore,
    workspace: &crate::video_job_workspace::VideoJobWorkspace,
) -> Result<Option<PathBuf>, MaterialVideoStudioError> {
    // Two runtime parents, one occupant: the interactive WebUI session and the
    // headless montage pipeline are mutually exclusive per workspace (the
    // worker bootstrap refuses both at once), so across both directories at
    // most one runtime may exist.
    let asset_root = workspaces
        .worker_asset_directory(workspace)
        .map_err(map_workspace_error)?;
    let mut directories = Vec::new();
    for parent_name in [".automation-tool-webui", ".automation-tool-montage"] {
        let entries = match fs::read_dir(asset_root.join(parent_name)) {
            Ok(value) => value,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(_) => return Err(job_unavailable()),
        };
        for entry in entries {
            let entry = entry.map_err(|_| job_unavailable())?;
            let metadata = fs::symlink_metadata(entry.path()).map_err(|_| job_unavailable())?;
            if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
                return Err(job_unavailable());
            }
            directories.push(entry.path());
        }
    }
    if directories.len() > 1 {
        return Err(job_unavailable());
    }
    Ok(directories.pop())
}

/// Resolve the packaged smart-material Worker.
///
/// Every build resolves it from the packaged resource directory. Test builds
/// used to accept an environment-variable override, which meant no acceptance
/// run ever proved the installer carries this executable.
fn worker_executable(app: &tauri::AppHandle) -> Result<PathBuf, MaterialVideoStudioError> {
    let name = if cfg!(windows) {
        "automation-tool-material-video-worker.exe"
    } else {
        "automation-tool-material-video-worker"
    };
    app.path()
        .resource_dir()
        .map(|root| root.join("material-video-worker/package").join(name))
        .map_err(|_| process_unavailable())
}

/// Verify the packaged FFmpeg pair that ships beside the Worker.
fn media_toolchain(
    app: &tauri::AppHandle,
) -> Result<VideoMediaToolchain, MaterialVideoStudioError> {
    let resource_directory = app
        .path()
        .resource_dir()
        .map_err(|_| process_unavailable())?;
    VideoMediaToolchain::load(&resource_directory).map_err(|_| process_unavailable())
}

/// Build the one launch configuration the smart-material studio starts.
///
/// The packaged FFmpeg is passed here rather than left to the Worker: its
/// upstream resolver takes `IMAGEIO_FFMPEG_EXE` first and otherwise searches
/// the user's `PATH`, so a Worker started without this variable encodes with
/// whatever FFmpeg that machine happens to have installed.
pub fn material_worker_launch(
    executable: PathBuf,
    asset_root: PathBuf,
    toolchain: &VideoMediaToolchain,
) -> Result<VideoWorkerLaunch, MaterialVideoStudioError> {
    let policy = VideoWorkerRestartPolicy::new(1, Duration::from_millis(250))
        .map_err(|_| process_unavailable())?;
    VideoWorkerLaunch::new(
        VideoWorkerKind::Python,
        executable,
        asset_root,
        WORKER_VERSION.to_owned(),
        policy,
    )
    .and_then(|launch| launch.with_environment(toolchain.intelligent_material_environment()))
    .map_err(|_| process_unavailable())
}

fn cleanup_workspace(
    store: &VideoJobWorkspaceStore,
    workspace: &crate::video_job_workspace::VideoJobWorkspace,
) {
    let _ = store.finish(workspace, VideoWorkspaceDisposition::Delete);
}

fn map_workspace_error(_error: VideoWorkspaceError) -> MaterialVideoStudioError {
    MaterialVideoStudioError {
        code: MaterialVideoStudioErrorCode::StorageUnavailable,
        retryable: false,
    }
}

const fn configuration_required() -> MaterialVideoStudioError {
    MaterialVideoStudioError {
        code: MaterialVideoStudioErrorCode::ConfigurationRequired,
        retryable: false,
    }
}

const fn process_unavailable() -> MaterialVideoStudioError {
    MaterialVideoStudioError {
        code: MaterialVideoStudioErrorCode::ProcessUnavailable,
        retryable: true,
    }
}

const fn view_unavailable() -> MaterialVideoStudioError {
    MaterialVideoStudioError {
        code: MaterialVideoStudioErrorCode::ViewUnavailable,
        retryable: true,
    }
}

const fn job_unavailable() -> MaterialVideoStudioError {
    MaterialVideoStudioError {
        code: MaterialVideoStudioErrorCode::JobUnavailable,
        retryable: false,
    }
}

/// A montage submission whose fields the product form should never produce.
pub(crate) const fn invalid_montage_request() -> MaterialVideoStudioError {
    MaterialVideoStudioError {
        code: MaterialVideoStudioErrorCode::ProtocolMismatch,
        retryable: false,
    }
}

#[cfg(test)]
mod theme_tests {
    use super::INIT_SCRIPT;

    /// Only the test reads the contract; the window carries a plain constant so
    /// the shipped binary does not haul a document around to look up one word.
    const THEME_CONTRACT: &str =
        include_str!("../../../contracts/quality/material-video-studio-theme.v1.json");

    /// The appearance is a decision, and this pins it against the one other
    /// place that states it. What it cannot show is that Tauri applies it to
    /// the real window — that is the desktop acceptance in `docs/development/T108.md`.
    #[test]
    fn studio_window_forces_the_appearance_the_theme_contract_declares() {
        let contract: serde_json::Value = serde_json::from_str(THEME_CONTRACT).unwrap();
        assert_eq!(contract["colorScheme"], "light");
        assert!(INIT_SCRIPT.contains("color-scheme: light"));
        assert!(INIT_SCRIPT.contains("prefers-color-scheme"));
    }

    #[test]
    fn initialization_script_is_fail_closed_and_keeps_material_settings() {
        for required in [
            "data-automation-tool-studio-state",
            "制作界面暂时不可用",
            "制作服务设置",
            "素材\\s*API",
            "removeExternalNavigation",
            "120_000",
        ] {
            assert!(
                INIT_SCRIPT.contains(required),
                "missing theme guard: {required}"
            );
        }
        assert!(!INIT_SCRIPT.contains(".st-key-open_settings_dialog_button,\n"));
    }
}

#[cfg(test)]
mod montage_error_tests {
    use super::*;

    #[test]
    fn an_invalid_montage_request_reports_protocol_mismatch_not_job_state() {
        // REVIEW-2026-08-06 M2: parameter validation failure shared
        // JobUnavailable with "the job state cannot be read" — the front end
        // could not tell "fix your input" apart from "try again later".
        assert_eq!(
            invalid_montage_request().code(),
            MaterialVideoStudioErrorCode::ProtocolMismatch,
        );
    }
}

#[cfg(test)]
mod embedded_view_tests {
    use super::*;

    #[test]
    fn logical_bounds_accept_a_visible_in_app_surface_and_reject_hostile_geometry() {
        let bounds: MaterialVideoStudioView = serde_json::from_value(serde_json::json!({
            "x": 40.0,
            "y": 120.0,
            "width": 900.0,
            "height": 640.0,
            "visible": true
        }))
        .unwrap();
        let (position, size) = bounds.logical_geometry().unwrap();
        assert_eq!(position, tauri::LogicalPosition::new(40.0, 120.0));
        assert_eq!(size, tauri::LogicalSize::new(900.0, 640.0));

        for value in [
            serde_json::json!({"x": 0, "y": 0, "width": 0, "height": 640, "visible": true}),
            serde_json::json!({"x": 0, "y": 0, "width": 900, "height": 20, "visible": true}),
            serde_json::json!({"x": 9000, "y": 0, "width": 900, "height": 640, "visible": true}),
        ] {
            let rejected: MaterialVideoStudioView = serde_json::from_value(value).unwrap();
            assert_eq!(
                rejected.logical_geometry().unwrap_err().code(),
                MaterialVideoStudioErrorCode::ViewUnavailable
            );
        }
    }
}

#[cfg(test)]
mod job_tests {
    use super::*;
    use crate::video_job_workspace::VideoJobWorkspacePolicy;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static SEQUENCE: AtomicU64 = AtomicU64::new(0);

    struct Root(PathBuf);

    impl Root {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "automation-tool-im07-{}-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos(),
                SEQUENCE.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
            }
            Self(fs::canonicalize(path).unwrap())
        }
    }

    impl Drop for Root {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn reconciles_monotonic_worker_observations_into_one_app_owned_artifact() {
        let root = Root::new();
        let policy = VideoJobWorkspacePolicy::new(1024 * 1024, 512 * 1024, 8, 3600, 0).unwrap();
        let store = VideoJobWorkspaceStore::initialize(&root.0, policy).unwrap();
        let workspace = store.create_new().unwrap();
        set_active_workspace(Some(workspace.job_id())).unwrap();
        let runtime = store
            .worker_asset_directory(&workspace)
            .unwrap()
            .join(".automation-tool-webui/capability-v1");
        fs::create_dir_all(&runtime).unwrap();
        let task_id = uuid::Uuid::parse_str("3d594650-b5f4-4498-8e38-0cf85d6dfa72").unwrap();
        let observation = |revision: u64, status: &str, progress: u8, output: Option<&str>| {
            serde_json::json!({
                "schemaVersion": 1,
                "renderJobId": workspace.job_id(),
                "workerTaskId": task_id,
                "revision": revision,
                "status": status,
                "progressPercent": progress,
                "subject": "新品介绍",
                "outputFile": output,
                "failureCode": null
            })
        };
        fs::write(
            runtime.join(OBSERVATION_FILE),
            serde_json::to_vec(&observation(1, "running", 32, None)).unwrap(),
        )
        .unwrap();
        let running = jobs(&store).unwrap();
        assert_eq!(running[0].status, MaterialRenderJobStatus::Running);
        assert_eq!(running[0].progress_percent, 32);

        fs::write(
            store
                .worker_output_directory(&workspace)
                .unwrap()
                .join("material-result.mp4"),
            b"verified-video",
        )
        .unwrap();
        fs::write(
            runtime.join(OBSERVATION_FILE),
            serde_json::to_vec(&observation(
                2,
                "succeeded",
                100,
                Some("material-result.mp4"),
            ))
            .unwrap(),
        )
        .unwrap();
        let completed = jobs(&store).unwrap();
        let artifact_id = completed[0].artifact_id.expect("imported artifact");
        assert_eq!(store.list_artifacts().unwrap().len(), 1);
        assert_eq!(jobs(&store).unwrap()[0].artifact_id, Some(artifact_id));
        assert_eq!(store.list_artifacts().unwrap().len(), 1);
        set_active_workspace(None).unwrap();
        assert_eq!(jobs(&store).unwrap()[0].artifact_id, Some(artifact_id));

        set_active_workspace(Some(workspace.job_id())).unwrap();
        cancel(&store, workspace.job_id()).unwrap();
        assert_eq!(fs::read(runtime.join(CANCEL_FILE)).unwrap(), b"cancel\n");
        delete_artifact(&store, artifact_id).unwrap();
        assert!(store.list_artifacts().unwrap().is_empty());
        assert_eq!(jobs(&store).unwrap()[0].artifact_id, None);
        set_active_workspace(None).unwrap();
        store
            .save_checkpoint(
                &workspace,
                JOB_CHECKPOINT,
                br#"{"renderJobId":"3d594650-b5f4-4498-8e38-0cf85d6dfa72","revision":3,"status":"succeeded","progressPercent":100,"subject":"task","artifactId":null,"artifactSizeBytes":null,"failureCode":null,"outputPath":"/private/video.mp4"}"#,
            )
            .unwrap();
        assert_eq!(
            jobs(&store).unwrap_err().code(),
            MaterialVideoStudioErrorCode::JobUnavailable
        );
    }
}

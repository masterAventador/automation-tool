//! Tauri-owned bridge from the product entry to the private material-video WebUI.

use crate::local_video_orchestrator::{
    LocalVideoOrchestrator, VideoWorkerKind, VideoWorkerLaunch, VideoWorkerRestartPolicy,
    VideoWorkerState,
};
use crate::model_service_settings::ProductionModelServiceSettings;
use crate::video_job_workspace::{
    VideoJobWorkspaceStore, VideoWorkspaceDisposition, VideoWorkspaceError,
};
use serde::Serialize;
use std::fmt;
use std::path::PathBuf;
use std::time::Duration;
use tauri::webview::NewWindowResponse;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};

const WINDOW_LABEL: &str = "material-video-studio";
const WORKER_VERSION: &str = "1.3.2";
const INIT_SCRIPT: &str = include_str!("material_video_studio_init.js");

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

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MaterialVideoStudioErrorCode {
    ConfigurationRequired,
    ProcessUnavailable,
    StorageUnavailable,
    ViewUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct MaterialVideoStudioError {
    code: MaterialVideoStudioErrorCode,
    retryable: bool,
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

pub(crate) fn open(
    app: &tauri::AppHandle,
    orchestrator: &LocalVideoOrchestrator,
    settings: &ProductionModelServiceSettings,
    workspaces: &VideoJobWorkspaceStore,
) -> Result<MaterialVideoStudioSnapshot, MaterialVideoStudioError> {
    if let Some(window) = app.get_webview_window(WINDOW_LABEL) {
        let status = orchestrator
            .status(VideoWorkerKind::Python)
            .map_err(|_| process_unavailable())?;
        if status.state() != VideoWorkerState::Running || !status.web_ui_available() {
            return Err(process_unavailable());
        }
        window.show().map_err(|_| view_unavailable())?;
        window.set_focus().map_err(|_| view_unavailable())?;
        return Ok(MaterialVideoStudioSnapshot {
            state: MaterialVideoStudioState::Opened,
            model_id: status
                .script_model_id()
                .ok_or_else(configuration_required)?
                .to_owned(),
        });
    }

    let script_model = settings
        .material_video_script_model()
        .map_err(|_| configuration_required())?;
    let model_id = script_model.model_id().to_owned();
    let workspace = workspaces.create_new().map_err(map_workspace_error)?;
    let asset_root = workspaces
        .worker_asset_directory(&workspace)
        .map_err(map_workspace_error)?;
    let executable = worker_executable(app)?;
    let policy = VideoWorkerRestartPolicy::new(1, Duration::from_millis(250))
        .map_err(|_| process_unavailable())?;
    let launch = VideoWorkerLaunch::new(
        VideoWorkerKind::Python,
        executable,
        asset_root,
        WORKER_VERSION.to_owned(),
        policy,
    )
    .map_err(|_| process_unavailable())?
    .with_script_model(script_model)
    .with_web_ui();
    let status = match orchestrator.start(launch) {
        Ok(status) => status,
        Err(_) => {
            cleanup_workspace(workspaces, &workspace);
            return Err(process_unavailable());
        }
    };
    let endpoint = match orchestrator.web_ui_endpoint(VideoWorkerKind::Python) {
        Ok(endpoint) => endpoint,
        Err(_) => {
            let _ = orchestrator.stop(VideoWorkerKind::Python);
            cleanup_workspace(workspaces, &workspace);
            return Err(process_unavailable());
        }
    };
    if orchestrator.verify_web_ui(VideoWorkerKind::Python).is_err() {
        let _ = orchestrator.stop(VideoWorkerKind::Python);
        cleanup_workspace(workspaces, &workspace);
        return Err(process_unavailable());
    }
    let endpoint_url = endpoint.url().map_err(|_| process_unavailable())?;
    let allowed_port = endpoint.port();
    let allowed_path = format!("/{}/", endpoint.path());
    let builder = WebviewWindowBuilder::new(app, WINDOW_LABEL, WebviewUrl::External(endpoint_url))
        .title("智能素材成片")
        .inner_size(1180.0, 760.0)
        .min_inner_size(960.0, 640.0)
        .resizable(true)
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
    let window = match builder.build() {
        Ok(window) => window,
        Err(_) => {
            let _ = orchestrator.stop(VideoWorkerKind::Python);
            cleanup_workspace(workspaces, &workspace);
            return Err(view_unavailable());
        }
    };
    let cleanup_app = app.clone();
    let job_id = workspace.job_id();
    window.on_window_event(move |event| {
        if matches!(event, WindowEvent::Destroyed) {
            if let Some(orchestrator) = cleanup_app.try_state::<LocalVideoOrchestrator>() {
                let _ = orchestrator.stop(VideoWorkerKind::Python);
            }
            if let Some(workspaces) = cleanup_app.try_state::<VideoJobWorkspaceStore>() {
                if let Ok(workspace) = workspaces.open(job_id) {
                    let _ = workspaces.finish(&workspace, VideoWorkspaceDisposition::Delete);
                }
            }
        }
    });
    Ok(MaterialVideoStudioSnapshot {
        state: MaterialVideoStudioState::Opened,
        model_id: status
            .script_model_id()
            .unwrap_or(model_id.as_str())
            .to_owned(),
    })
}

fn worker_executable(app: &tauri::AppHandle) -> Result<PathBuf, MaterialVideoStudioError> {
    #[cfg(feature = "video-studio-e2e")]
    if let Some(value) = std::env::var_os("AUTOMATION_TOOL_IM05_WORKER") {
        return std::fs::canonicalize(PathBuf::from(value)).map_err(|_| process_unavailable());
    }
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

#[cfg(test)]
mod theme_tests {
    use super::INIT_SCRIPT;

    #[test]
    fn initialization_script_is_fail_closed_and_keeps_material_settings() {
        for required in [
            "data-automation-tool-studio-state",
            "制作界面暂时不可用",
            "制作服务设置",
            "素材\\s*API",
            "removeExternalNavigation",
            "60_000",
        ] {
            assert!(
                INIT_SCRIPT.contains(required),
                "missing theme guard: {required}"
            );
        }
        assert!(!INIT_SCRIPT.contains(".st-key-open_settings_dialog_button,\n"));
    }
}

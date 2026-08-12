mod app_logging;
pub mod app_update_cache;
pub mod app_update_coordinator;
pub mod app_update_installation;
pub mod app_update_policy;
pub mod app_updates;
pub mod bilibili_service_settings;
pub mod browser_discovery;
pub mod browser_profiles;
pub mod browser_settings;
pub mod command_error;
pub mod control_plane;
pub mod deployment_profile;
pub mod device_credentials;
pub mod device_identity;
mod diagnostic_export;
pub mod embedded_browser_authority;
pub mod embedded_browser_distribution;
pub mod executor_bootstrap;
mod executor_diagnostics;
pub mod executor_manager;
pub mod executor_package;
pub mod executor_platform;
pub mod executor_protocol;
pub mod local_editing_job_ledger;
pub mod local_editing_runtime;
// The desktop-only acceptance build deliberately omits every Control Plane
// material command. Keep the module available to its unit tests, while the
// normal and control-plane builds continue to lint every private path.
#[cfg_attr(
    all(feature = "desktop-e2e", not(feature = "control-plane-e2e")),
    allow(dead_code)
)]
pub mod local_material_library;
pub mod local_registration;
pub mod local_video_orchestrator;
mod managed_process_tree;
pub mod material_video_studio;
pub mod model_service_settings;
pub mod motion_video_studio;
pub mod publish_workspace;
mod runtime_compatibility;
pub mod secure_store;
pub mod smart_edit_runtime;
pub mod startup_environment;
pub mod video_job_workspace;
pub mod video_media_toolchain;

use device_credentials::initialize_production_device_credential_vault;
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
use device_credentials::ProductionDeviceCredentialVault;
#[cfg(all(feature = "desktop-e2e", not(feature = "control-plane-e2e")))]
use device_identity::initialize_ephemeral_identity;
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
use device_identity::initialize_production_identity;
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
use device_identity::ProductionDeviceIdentity;
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
use local_registration::{
    initialize_local_registration_handoff_store, ProductionLocalRegistrationHandoffStore,
};
use tauri::Manager;
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
use tauri_plugin_dialog::{DialogExt, FilePath};
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]

struct ControlPlaneCommandError {
    code: &'static str,
    retryable: bool,
}

#[cfg_attr(
    all(feature = "desktop-e2e", not(feature = "control-plane-e2e")),
    allow(dead_code)
)]
struct LocalMaterialCommandError {
    code: &'static str,
    retryable: bool,
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
struct SmartEditCommandError {
    code: &'static str,
    retryable: bool,
}

#[cfg(feature = "control-plane-e2e")]
static MATERIAL_ACCEPTANCE_PICK_INDEX: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(1);

#[cfg(feature = "control-plane-e2e")]
static MATERIAL_ACCEPTANCE_SOURCES: std::sync::OnceLock<
    std::sync::Mutex<std::collections::HashMap<uuid::Uuid, std::path::PathBuf>>,
> = std::sync::OnceLock::new();

impl serde::Serialize for LocalMaterialCommandError {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        command_error::serialize(&self.code, Some(self.retryable), serializer)
    }
}

impl serde::Serialize for ControlPlaneCommandError {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        command_error::serialize(&self.code, Some(self.retryable), serializer)
    }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
impl serde::Serialize for SmartEditCommandError {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        command_error::serialize(&self.code, Some(self.retryable), serializer)
    }
}

struct ExecutorPlatformCommandError {
    code: &'static str,
    retryable: bool,
}

impl serde::Serialize for ExecutorPlatformCommandError {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        command_error::serialize(&self.code, Some(self.retryable), serializer)
    }
}

#[derive(serde::Serialize)]
struct ExecutorDiagnosticsSnapshot {
    lines: Vec<String>,
}

struct DiagnosticExportCommandError {
    code: &'static str,
    retryable: bool,
}

impl serde::Serialize for DiagnosticExportCommandError {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        command_error::serialize(&self.code, Some(self.retryable), serializer)
    }
}

#[cfg(all(feature = "desktop-e2e", not(feature = "control-plane-e2e")))]
struct UpdatePolicyAcceptanceError {
    code: &'static str,
}

#[cfg(all(feature = "desktop-e2e", not(feature = "control-plane-e2e")))]
impl serde::Serialize for UpdatePolicyAcceptanceError {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        command_error::serialize(&self.code, None, serializer)
    }
}

#[derive(Debug)]
struct AppUpdateDecisionCommandError {
    code: &'static str,
}

impl serde::Serialize for AppUpdateDecisionCommandError {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        command_error::serialize(&self.code, None, serializer)
    }
}

#[cfg(all(feature = "desktop-e2e", not(feature = "control-plane-e2e")))]
#[tauri::command]
fn get_update_policy_record_for_acceptance(
    policy: tauri::State<'_, std::sync::Arc<app_update_policy::UpdatePolicyService>>,
) -> Result<app_update_policy::UpdatePolicyRecord, UpdatePolicyAcceptanceError> {
    policy.record().map_err(|_| UpdatePolicyAcceptanceError {
        code: "storage_unavailable",
    })
}

/// 协调器只在这个构建真的配置了更新时才存在。它不存在说明更新被显式关掉了——那是
/// 受支持的正常配置；它存在但读不出状态才是故障。两条路必须分开报，否则界面只能在
/// 「对正常构建报错」和「对坏掉的更新器说未启用」之间二选一。
fn app_update_state_of(
    coordinator: Option<&std::sync::Arc<app_update_coordinator::AppUpdateCoordinator>>,
) -> app_updates::UpdateState {
    match coordinator {
        Some(coordinator) => coordinator.observed_state(),
        None => app_updates::UpdateState::Disabled,
    }
}

#[tauri::command]
fn get_app_update_state(
    coordinator: tauri::State<
        '_,
        Option<std::sync::Arc<app_update_coordinator::AppUpdateCoordinator>>,
    >,
) -> app_updates::UpdateState {
    app_update_state_of(coordinator.as_ref())
}

#[tauri::command]
async fn check_app_update_now(
    coordinator: tauri::State<
        '_,
        Option<std::sync::Arc<app_update_coordinator::AppUpdateCoordinator>>,
    >,
) -> Result<app_updates::UpdateState, ()> {
    let Some(coordinator) = coordinator.as_ref().cloned() else {
        return Ok(app_update_state_of(None));
    };
    Ok(coordinator
        .check(app_updates::UpdateCheckTrigger::Manual)
        .await)
}

#[tauri::command]
fn decide_app_update(
    coordinator: tauri::State<
        '_,
        Option<std::sync::Arc<app_update_coordinator::AppUpdateCoordinator>>,
    >,
    decision: app_updates::UpdateDecision,
) -> Result<app_updates::UpdateState, AppUpdateDecisionCommandError> {
    let Some(coordinator) = coordinator.as_ref() else {
        return Err(AppUpdateDecisionCommandError {
            code: "configuration_unavailable",
        });
    };
    coordinator.decide(decision).map_err(|error| {
        use app_update_coordinator::UpdateCoordinationErrorCode;

        AppUpdateDecisionCommandError {
            code: match error.code() {
                UpdateCoordinationErrorCode::OperationInProgress => "operation_in_progress",
                UpdateCoordinationErrorCode::DecisionUnavailable => "decision_unavailable",
            },
        }
    })
}

fn map_diagnostic_export_error(
    error: diagnostic_export::DiagnosticExportError,
) -> DiagnosticExportCommandError {
    let code = match error.code() {
        diagnostic_export::DiagnosticExportErrorCode::StorageUnavailable => "storage_unavailable",
    };
    DiagnosticExportCommandError {
        code,
        retryable: false,
    }
}

#[tauri::command]
fn get_model_service_settings(
    settings: tauri::State<'_, model_service_settings::ProductionModelServiceSettings>,
) -> Result<
    model_service_settings::ModelServiceSnapshot,
    model_service_settings::ModelServiceCommandError,
> {
    settings.snapshot().map_err(Into::into)
}

#[tauri::command]
fn configure_model_service(
    request: model_service_settings::ConfigureModelServiceRequest,
    settings: tauri::State<'_, model_service_settings::ProductionModelServiceSettings>,
) -> Result<
    model_service_settings::ModelServiceSnapshot,
    model_service_settings::ModelServiceCommandError,
> {
    settings.configure(&request).map_err(Into::into)
}

#[tauri::command]
fn reuse_script_model_service_for_video(
    settings: tauri::State<'_, model_service_settings::ProductionModelServiceSettings>,
) -> Result<
    model_service_settings::ModelServiceSnapshot,
    model_service_settings::ModelServiceCommandError,
> {
    settings.reuse_script_for_video().map_err(Into::into)
}

#[tauri::command]
fn clear_model_service(
    purpose: model_service_settings::ModelServicePurpose,
    settings: tauri::State<'_, model_service_settings::ProductionModelServiceSettings>,
) -> Result<
    model_service_settings::ModelServiceSnapshot,
    model_service_settings::ModelServiceCommandError,
> {
    settings.clear(purpose).map_err(Into::into)
}

#[tauri::command]
async fn test_model_service_connection(
    purpose: model_service_settings::ModelServicePurpose,
    settings: tauri::State<'_, model_service_settings::ProductionModelServiceSettings>,
) -> Result<
    model_service_settings::ModelConnectionSnapshot,
    model_service_settings::ModelServiceCommandError,
> {
    settings.test_connection(purpose).await.map_err(Into::into)
}

#[tauri::command]
fn get_bilibili_service_settings(
    settings: tauri::State<'_, bilibili_service_settings::ProductionBilibiliServiceSettings>,
) -> Result<
    bilibili_service_settings::BilibiliServiceSnapshot,
    bilibili_service_settings::BilibiliServiceCommandError,
> {
    settings.snapshot().map_err(Into::into)
}

#[tauri::command]
fn configure_bilibili_service(
    request: bilibili_service_settings::ConfigureBilibiliServiceRequest,
    settings: tauri::State<'_, bilibili_service_settings::ProductionBilibiliServiceSettings>,
) -> Result<
    bilibili_service_settings::BilibiliServiceSnapshot,
    bilibili_service_settings::BilibiliServiceCommandError,
> {
    settings.configure(&request).map_err(Into::into)
}

#[tauri::command]
fn clear_bilibili_service(
    settings: tauri::State<'_, bilibili_service_settings::ProductionBilibiliServiceSettings>,
) -> Result<
    bilibili_service_settings::BilibiliServiceSnapshot,
    bilibili_service_settings::BilibiliServiceCommandError,
> {
    settings.clear().map_err(Into::into)
}

#[tauri::command]
async fn open_material_video_studio(
    app: tauri::AppHandle,
    orchestrator: tauri::State<'_, local_video_orchestrator::LocalVideoOrchestrator>,
    settings: tauri::State<'_, model_service_settings::ProductionModelServiceSettings>,
    workspaces: tauri::State<'_, video_job_workspace::VideoJobWorkspaceStore>,
    view: material_video_studio::MaterialVideoStudioView,
) -> Result<
    material_video_studio::MaterialVideoStudioSnapshot,
    material_video_studio::MaterialVideoStudioError,
> {
    material_video_studio::open(&app, &orchestrator, &settings, &workspaces, view)
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct MaterialMontageSubmission {
    subject: String,
    script: Option<String>,
    aspect: String,
    clip_duration_seconds: u8,
    voice_name: String,
    subtitle_enabled: bool,
    font_size_px: u16,
    text_color: String,
    stroke_color: String,
    stroke_width_px: f32,
}

#[tauri::command]
async fn submit_material_montage(
    app: tauri::AppHandle,
    orchestrator: tauri::State<'_, local_video_orchestrator::LocalVideoOrchestrator>,
    settings: tauri::State<'_, model_service_settings::ProductionModelServiceSettings>,
    workspaces: tauri::State<'_, video_job_workspace::VideoJobWorkspaceStore>,
    request: MaterialMontageSubmission,
) -> Result<uuid::Uuid, material_video_studio::MaterialVideoStudioError> {
    let validated = local_video_orchestrator::VideoWorkerMontageRequest::new(
        request.subject,
        request.script,
        request.aspect,
        request.clip_duration_seconds,
        request.voice_name,
        request.subtitle_enabled,
        request.font_size_px,
        request.text_color,
        request.stroke_color,
        request.stroke_width_px,
    )
    .map_err(|_| material_video_studio::invalid_montage_request())?;
    material_video_studio::submit_montage(
        &app,
        &orchestrator,
        &settings,
        &workspaces,
        validated,
    )
}

#[tauri::command]
fn update_material_video_studio_view(
    app: tauri::AppHandle,
    view: material_video_studio::MaterialVideoStudioView,
) -> Result<(), material_video_studio::MaterialVideoStudioError> {
    material_video_studio::update_embedded_view(&app, view)
}

#[tauri::command]
fn close_material_video_studio(
    app: tauri::AppHandle,
    orchestrator: tauri::State<'_, local_video_orchestrator::LocalVideoOrchestrator>,
    workspaces: tauri::State<'_, video_job_workspace::VideoJobWorkspaceStore>,
) -> Result<(), material_video_studio::MaterialVideoStudioError> {
    material_video_studio::close_embedded_view(&app, &orchestrator, &workspaces)
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
fn exercise_material_video_studio_for_acceptance(
    app: tauri::AppHandle,
) -> Result<
    material_video_studio::MaterialVideoStudioAcceptanceSnapshot,
    material_video_studio::MaterialVideoStudioError,
> {
    material_video_studio::exercise_embedded_view_for_acceptance(app)
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
fn inspect_material_video_studio_exercise_for_acceptance() -> Result<
    material_video_studio::MaterialVideoStudioAcceptanceSnapshot,
    material_video_studio::MaterialVideoStudioError,
> {
    material_video_studio::inspect_embedded_view_exercise_for_acceptance()
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
fn inspect_material_video_studio_cleanup_for_acceptance(
    app: tauri::AppHandle,
    orchestrator: tauri::State<'_, local_video_orchestrator::LocalVideoOrchestrator>,
) -> Result<
    material_video_studio::MaterialVideoStudioCleanupSnapshot,
    material_video_studio::MaterialVideoStudioError,
> {
    material_video_studio::cleanup_snapshot_for_acceptance(&app, &orchestrator)
}

#[tauri::command]
fn get_material_render_jobs(
    workspaces: tauri::State<'_, video_job_workspace::VideoJobWorkspaceStore>,
) -> Result<
    Vec<material_video_studio::MaterialRenderJobSnapshot>,
    material_video_studio::MaterialVideoStudioError,
> {
    material_video_studio::jobs(&workspaces)
}

#[tauri::command]
fn cancel_material_render_job(
    render_job_id: uuid::Uuid,
    workspaces: tauri::State<'_, video_job_workspace::VideoJobWorkspaceStore>,
) -> Result<(), material_video_studio::MaterialVideoStudioError> {
    material_video_studio::cancel(&workspaces, render_job_id)
}

#[tauri::command]
fn read_material_video_artifact(
    artifact_id: uuid::Uuid,
    workspaces: tauri::State<'_, video_job_workspace::VideoJobWorkspaceStore>,
) -> Result<
    video_job_workspace::RenderedVideoArtifactPayload,
    material_video_studio::MaterialVideoStudioError,
> {
    material_video_studio::read_artifact(&workspaces, artifact_id)
}

#[tauri::command]
fn delete_material_video_artifact(
    artifact_id: uuid::Uuid,
    workspaces: tauri::State<'_, video_job_workspace::VideoJobWorkspaceStore>,
) -> Result<(), material_video_studio::MaterialVideoStudioError> {
    material_video_studio::delete_artifact(&workspaces, artifact_id)
}

/// Packaged-resource layout for the brand-motion Worker. `release_assembly`
/// installs it here; nothing else in the App may name these directories.
const MOTION_VIDEO_WORKER_DIRECTORY: &str = "motion-video-worker";
const PACKAGED_WORKER_SUBDIRECTORY: &str = "package";

/// Where the installer puts the 134 packaged motion parts.
///
/// Declared in `release-package-resources.v1.json` as the `catalog` resource
/// and asserted against it by the runtime tests, because this string is the
/// only thing connecting a tree the release audit requires to a child process
/// that cannot use parts it is never pointed at.
pub const MOTION_CATALOG_DIRECTORY: &str = "motion-catalog";

struct MotionRuntimePaths {
    worker_package: std::path::PathBuf,
    catalog: std::path::PathBuf,
    browser: std::path::PathBuf,
    chromium_major: u32,
    toolchain: video_media_toolchain::VideoMediaToolchain,
}

/// Resolve the packaged brand-motion runtime.
///
/// Every build resolves it the same way. Test builds used to read these paths
/// from environment variables instead, which made the whole video line immune
/// to the one question that mattered — whether the installer actually carries
/// the Worker, the browser and ffmpeg.
fn motion_runtime_paths(
    app: &tauri::AppHandle,
    authority: &embedded_browser_authority::EmbeddedBrowserAuthority,
) -> Result<MotionRuntimePaths, motion_video_studio::MotionVideoStudioError> {
    let resource_directory = app
        .path()
        .resource_dir()
        .map_err(|_| motion_video_studio::render_unavailable())?;
    let browser = authority
        .resolve()
        .map_err(|_| motion_video_studio::render_unavailable())?;
    let compatibility: serde_json::Value = serde_json::from_str(include_str!(
        "../../../contracts/browser/embedded-chromium-compatibility.v1.json"
    ))
    .map_err(|_| motion_video_studio::render_unavailable())?;
    let chromium_major = compatibility
        .pointer("/production_runtime/chromium/browser_version")
        .and_then(serde_json::Value::as_str)
        .and_then(|value| value.split('.').next())
        .and_then(|value| value.parse::<u32>().ok())
        .ok_or_else(motion_video_studio::render_unavailable)?;
    let toolchain = video_media_toolchain::VideoMediaToolchain::load(&resource_directory)
        .map_err(|_| motion_video_studio::render_unavailable())?;
    Ok(MotionRuntimePaths {
        worker_package: resource_directory
            .join(MOTION_VIDEO_WORKER_DIRECTORY)
            .join(PACKAGED_WORKER_SUBDIRECTORY),
        catalog: resource_directory.join(MOTION_CATALOG_DIRECTORY),
        browser,
        chromium_major,
        toolchain,
    })
}

/// Build the one launch configuration the brand-motion studio starts.
///
/// The Worker runs with a cleared environment, so it is also told where the
/// packaged FFmpeg pair lives: its upstream engine otherwise falls back to
/// `which`, a manual `PATH` scan and finally the well-known Homebrew and
/// `/usr/bin` locations, which would encode with the user's own build.
pub fn motion_worker_launch(
    package: std::path::PathBuf,
    asset_root: std::path::PathBuf,
    browser: std::path::PathBuf,
    chromium_major: u32,
    media_environment: std::collections::BTreeMap<&'static str, &std::path::Path>,
) -> Result<local_video_orchestrator::VideoWorkerLaunch, motion_video_studio::MotionVideoStudioError>
{
    let policy =
        local_video_orchestrator::VideoWorkerRestartPolicy::new(0, std::time::Duration::ZERO)
            .map_err(|_| motion_video_studio::render_unavailable())?;
    let launch =
        local_video_orchestrator::VideoWorkerLaunch::bundled_node(&package, asset_root, policy)
            .and_then(|launch| launch.with_environment(media_environment))
            .map_err(|_| motion_video_studio::render_unavailable())?;
    let browser = local_video_orchestrator::VideoWorkerRenderBrowserConfiguration::new(
        browser,
        chromium_major,
        std::time::Duration::from_secs(30),
    )
    .map_err(|_| motion_video_studio::render_unavailable())?;
    Ok(launch.with_render_browser(browser))
}

/// Run a command's blocking work somewhere that is not the UI thread.
///
/// A `#[tauri::command]` that is not declared `async` compiles to
/// `ExecutionContext::Blocking` and is dispatched inside the IPC protocol
/// callback, which on macOS is delivered on the main thread. The window is then
/// frozen for the whole duration of the command: it does not repaint, no other
/// command is dispatched, and a cancel button cannot even be clicked.
///
/// The video commands verify packaged bytes, start processes and wait for them
/// to finish, so they are blocking by nature — which is why they belong on the
/// blocking pool rather than on an async runtime worker, the same place
/// `export_diagnostics` and `restart_executor` already put their own.
async fn off_the_ui_thread<T, F>(work: F) -> Result<T, motion_video_studio::MotionVideoStudioError>
where
    F: FnOnce() -> Result<T, motion_video_studio::MotionVideoStudioError> + Send + 'static,
    T: Send + 'static,
{
    tauri::async_runtime::spawn_blocking(work)
        .await
        // The work panicked or the runtime is shutting down. Nothing was
        // authored, started or rendered, which is what this code already means
        // everywhere else on this path.
        .map_err(|_| motion_video_studio::render_unavailable())?
}

/// The services the render path needs, resolved from an owned handle.
///
/// A `tauri::State` argument borrows the command's handle, so it cannot travel
/// to the blocking pool; the work looks the services up itself instead.
/// `try_state` rather than `state` because a service that was never managed has
/// to come back as this command's own error, not as a panic on a pool thread.
type MotionRenderServices<'a> = (
    tauri::State<'a, embedded_browser_authority::EmbeddedBrowserAuthority>,
    tauri::State<'a, local_video_orchestrator::LocalVideoOrchestrator>,
    tauri::State<'a, video_job_workspace::VideoJobWorkspaceStore>,
);

fn motion_render_services(
    app: &tauri::AppHandle,
) -> Result<MotionRenderServices<'_>, motion_video_studio::MotionVideoStudioError> {
    let authority = app
        .try_state::<embedded_browser_authority::EmbeddedBrowserAuthority>()
        .ok_or_else(motion_video_studio::render_unavailable)?;
    let orchestrator = app
        .try_state::<local_video_orchestrator::LocalVideoOrchestrator>()
        .ok_or_else(motion_video_studio::render_unavailable)?;
    let workspaces = app
        .try_state::<video_job_workspace::VideoJobWorkspaceStore>()
        .ok_or_else(motion_video_studio::storage_unavailable)?;
    Ok((authority, orchestrator, workspaces))
}

#[tauri::command]
async fn submit_motion_video_draft(
    app: tauri::AppHandle,
    request: motion_video_studio::MotionVideoDraftRequest,
) -> Result<motion_video_studio::MotionRenderJobSnapshot, motion_video_studio::MotionVideoStudioError>
{
    off_the_ui_thread(move || {
        let (authority, orchestrator, workspaces) = motion_render_services(&app)?;
        let runtime = motion_runtime_paths(&app, &authority)?;
        let prepared = motion_video_studio::prepare_manual_render_job(&workspaces, &request)?;
        start_motion_render(app.clone(), &orchestrator, &workspaces, &runtime, prepared)
    })
    .await
}

/// Start the render for a prepared job, whichever way its composition was made.
///
/// The fixed template and the authored brief differ only in how the workspace
/// came to hold a composition. From here they are the same job, so they run the
/// same launch, the same sandbox and the same still-image gate rather than two
/// paths that could drift.
fn start_motion_render(
    app: tauri::AppHandle,
    orchestrator: &local_video_orchestrator::LocalVideoOrchestrator,
    workspaces: &video_job_workspace::VideoJobWorkspaceStore,
    runtime: &MotionRuntimePaths,
    prepared: motion_video_studio::PreparedMotionRenderJob,
) -> Result<motion_video_studio::MotionRenderJobSnapshot, motion_video_studio::MotionVideoStudioError>
{
    let render_job_id = prepared.render_job_id();
    let (asset_root, _, _) =
        motion_video_studio::workspace_render_paths(workspaces, render_job_id)?;
    let discard = || {
        if let Ok(workspace) = workspaces.open(render_job_id) {
            let _ = workspaces.finish(
                &workspace,
                video_job_workspace::VideoWorkspaceDisposition::Delete,
            );
        }
    };
    let launch = match motion_worker_launch(
        runtime.worker_package.clone(),
        asset_root,
        runtime.browser.clone(),
        runtime.chromium_major,
        runtime.toolchain.brand_motion_environment(),
    ) {
        Ok(value) => value,
        Err(error) => {
            discard();
            return Err(error);
        }
    };
    if orchestrator.start(launch).is_err()
        || orchestrator
            .health(local_video_orchestrator::VideoWorkerKind::Node)
            .is_err()
    {
        let _ = orchestrator.stop(local_video_orchestrator::VideoWorkerKind::Node);
        discard();
        return Err(motion_video_studio::render_unavailable());
    }
    let initial = motion_video_studio::snapshot(workspaces, render_job_id)?;
    let segments = prepared.segments().to_vec();
    let film_canvas = prepared.film_canvas();
    let ffmpeg = runtime.toolchain.ffmpeg_path().to_path_buf();
    let ffprobe = runtime.toolchain.ffprobe_path().to_path_buf();
    std::thread::spawn(move || {
        run_motion_render_job(app, render_job_id, segments, film_canvas, ffmpeg, ffprobe);
    });
    Ok(initial)
}

/// How long the one-shot authoring run may take.
///
/// It is a model call with a bounded local-fix loop behind it, so the budget is
/// generous; it is still bounded, because a hung child would otherwise hold the
/// submit command open for as long as the model keeps the socket alive.
const MOTION_AUTHORING_DEADLINE: std::time::Duration = std::time::Duration::from_secs(600);

/// Run the authoring agent once, in the Executor package the user installed.
///
/// The model credential travels on stdin and nowhere else: not in `argv`, where
/// every process listing would carry it, and not in the environment, which is
/// inherited by whatever the child starts. The child is killed if it outlives
/// its budget, so a stalled model cannot pin the command open.
///
/// Each way this can end has its own error code. It used to have one — the same
/// code a missing packaged runtime and a worker that will not start return —
/// and the child's standard error is discarded on purpose so a model echo
/// cannot reach a log, which left a failed run with nothing to read at all.
/// Everything reported here is known on this side: an exit status, a budget we
/// set, and whether the bytes handed back were readable. None of it repeats
/// anything the model produced.
/// The one authoring request, built in one place.
///
/// Separated from the command that sends it so the fields can be checked
/// without a running App. What made that worth doing is `catalogRoot`: it was
/// the field whose absence let the agent build an empty catalog, refuse every
/// beat that chose a part, and fall back to the single built-in composition —
/// PC-04's silence surviving one layer further along than anyone looked.
pub fn motion_authoring_request(
    work: &std::path::Path,
    catalog_root: &std::path::Path,
    browser: &std::path::Path,
    ffprobe: &std::path::Path,
    request: &motion_video_studio::MotionVideoBriefRequest,
    model_id: &str,
    api_key: &str,
) -> serde_json::Value {
    let work = child_filesystem_path(work);
    let catalog_root = child_filesystem_path(catalog_root);
    let browser = child_filesystem_path(browser);
    let ffprobe = child_filesystem_path(ffprobe);
    serde_json::json!({
        "schemaVersion": 1,
        "workspace": work,
        "catalogRoot": catalog_root,
        // PC-14: the overflow probe launches exactly this executable — the one
        // the embedded-browser authority already verified (EB-07). Omitting it
        // would not fail anything; the child would simply author unmeasured,
        // which is the catalogRoot silence all over again.
        "browserExecutable": browser,
        // PC-26: narration length is measured with the packaged toolchain's
        // ffprobe — the same binary the render loop already trusts. Omitting
        // it would not fail anything either; the film would simply be silent.
        "ffprobeExecutable": ffprobe,
        "brief": request.brief(),
        "aspectRatio": request.aspect_ratio(),
        "durationSeconds": request.duration_seconds(),
        "language": request.language(),
        "catalogPartOverrides": request.catalog_part_overrides(),
        "modelThinking": request.model_thinking(),
        "brandAssets": [],
        "model": {
            "baseUrl": model_service_settings::PRODUCTION_BASE_URL,
            "modelId": model_id,
            "apiKey": api_key,
        },
    })
}

/// Hand an out-of-process Windows worker paths that remain usable past
/// `MAX_PATH`.
///
/// The App's private job root is already long before catalog assets add their
/// nested font names. Rust can create that tree through modern Windows APIs,
/// while the frozen Python worker needs the extended-length spelling to keep
/// writing below it. Existing verbatim/device paths are left untouched and UNC
/// paths use Windows' required `\\?\UNC\` form.
#[cfg(windows)]
fn child_filesystem_path(path: &std::path::Path) -> std::path::PathBuf {
    use std::ffi::OsString;
    use std::os::windows::ffi::{OsStrExt, OsStringExt};

    let encoded = path.as_os_str().encode_wide().collect::<Vec<_>>();
    const SEPARATOR: u16 = b'\\' as u16;
    const QUESTION: u16 = b'?' as u16;
    const DOT: u16 = b'.' as u16;
    let already_device = encoded.starts_with(&[SEPARATOR, SEPARATOR, QUESTION, SEPARATOR])
        || encoded.starts_with(&[SEPARATOR, SEPARATOR, DOT, SEPARATOR]);
    if already_device || !path.is_absolute() {
        return path.to_path_buf();
    }

    let mut extended = if encoded.starts_with(&[SEPARATOR, SEPARATOR]) {
        "\\\\?\\UNC\\".encode_utf16().collect::<Vec<_>>()
    } else {
        "\\\\?\\".encode_utf16().collect::<Vec<_>>()
    };
    extended.extend_from_slice(if encoded.starts_with(&[SEPARATOR, SEPARATOR]) {
        &encoded[2..]
    } else {
        &encoded
    });
    std::path::PathBuf::from(OsString::from_wide(&extended))
}

#[cfg(not(windows))]
fn child_filesystem_path(path: &std::path::Path) -> std::path::PathBuf {
    path.to_path_buf()
}

pub fn run_motion_authoring(
    entrypoint: &std::path::Path,
    request: &serde_json::Value,
    budget: std::time::Duration,
) -> Result<String, motion_video_studio::MotionVideoStudioError> {
    use std::io::Write as _;
    // Nothing was authored because nothing started: that is the packaged
    // Executor, not the authoring run, and it keeps the component code.
    let mut child = std::process::Command::new(entrypoint)
        .arg("--author-motion")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|_| motion_video_studio::render_unavailable())?;
    let payload =
        serde_json::to_vec(request).map_err(|_| motion_video_studio::render_unavailable())?;
    let written = child
        .stdin
        .take()
        .ok_or_else(motion_video_studio::render_unavailable)
        .and_then(|mut stdin| {
            // A broken pipe here means the child is already gone before it
            // ever read the request, so it cannot have decided anything.
            stdin
                .write_all(&payload)
                .map_err(|_| motion_video_studio::authoring_crashed())
        });
    if let Err(error) = written {
        let _ = child.kill();
        let _ = child.wait();
        return Err(error);
    }
    let deadline = std::time::Instant::now() + budget;
    let exit_status = loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                break status;
            }
            Ok(None) if std::time::Instant::now() < deadline => {
                std::thread::sleep(std::time::Duration::from_millis(200));
            }
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(motion_video_studio::authoring_timed_out());
            }
            Err(_) => {
                // The child can no longer be observed, so the run is over
                // whatever it was doing.
                let _ = child.kill();
                let _ = child.wait();
                return Err(motion_video_studio::authoring_crashed());
            }
        }
    };
    // Stdout is read whichever way the child exited. On the failure path it is
    // the only thing that separates "the agent completed the protocol and said
    // no" from every way the run can fail without anything having read the
    // brief: an unreachable model service, one that went silent, a damaged
    // install, a malformed request, a child that simply fell over. Only the
    // first of those is the product working, and it is the only one whose
    // answer to the user is "describe it differently".
    // Standard error stays discarded — the answer document carries a closed
    // reason token and no model output, so nothing the model produced is read
    // here.
    let answer = read_bounded_child_output(&mut child)?;
    if exit_status.success() {
        return Ok(answer);
    }
    eprintln!(
        "motion authoring child failed: exitCode={:?} answerBytes={} {}",
        exit_status.code(),
        answer.len(),
        motion_video_studio::safe_failed_authoring_diagnostic(&answer),
    );
    Err(motion_video_studio::classify_failed_authoring_answer(
        &answer,
    ))
}

/// Read what the authoring child wrote, up to a fixed ceiling.
///
/// The bound is what keeps a runaway child from being read into memory without
/// limit; anything past it is not one of the two protocol documents anyway.
fn read_bounded_child_output(
    child: &mut std::process::Child,
) -> Result<String, motion_video_studio::MotionVideoStudioError> {
    use std::io::Read as _;
    let mut stdout = child
        .stdout
        .take()
        .ok_or_else(motion_video_studio::render_unavailable)?;
    let mut bounded = Vec::new();
    // Bytes that cannot be read back as text are an unusable answer, not a
    // renderer that is unavailable.
    std::io::Read::take(&mut stdout, 64 * 1024)
        .read_to_end(&mut bounded)
        .map_err(|_| motion_video_studio::authoring_answer_invalid())?;
    String::from_utf8(bounded).map_err(|_| motion_video_studio::authoring_answer_invalid())
}

/// Submit one typed sentence for automatic authoring and rendering.
///
/// The render half is deliberately the same one the fixed template uses: the
/// agent produces a composition and a declared asset list, and from there the
/// worker launch, the sandbox, the still-image gate, the encode and the
/// artifact import are the code that is already in production. Only the way
/// the composition came to exist is new.
#[tauri::command]
async fn submit_motion_video_brief(
    app: tauri::AppHandle,
    request: motion_video_studio::MotionVideoBriefRequest,
) -> Result<motion_video_studio::MotionRenderJobSnapshot, motion_video_studio::MotionVideoStudioError>
{
    off_the_ui_thread(move || {
        request.validate()?;
        let (authority, orchestrator, workspaces) = motion_render_services(&app)?;
        let platform = app
            .try_state::<executor_platform::ExecutorPlatformService>()
            .ok_or_else(motion_video_studio::render_unavailable)?;
        let settings = app
            .try_state::<model_service_settings::ProductionModelServiceSettings>()
            .ok_or_else(motion_video_studio::configuration_required)?;
        let runtime = motion_runtime_paths(&app, &authority)?;
        let credential = settings
            .credential_for_worker(model_service_settings::ModelServicePurpose::VideoCreative)
            .map_err(|_| motion_video_studio::configuration_required())?;
        let entrypoint = platform
            .verified_entrypoint()
            .map_err(|_| motion_video_studio::render_unavailable())?;
        let workspace = workspaces
            .create_new()
            .map_err(|_| motion_video_studio::storage_unavailable())?;
        let outcome = (|| {
            motion_video_studio::seed_authoring_runtime(
                &workspaces,
                &workspace,
                &runtime
                    .worker_package
                    .join(motion_video_studio::AUTHORING_RUNTIME_ASSET),
            )?;
            let work = workspaces
                .worker_asset_directory(&workspace)
                .map_err(|_| motion_video_studio::storage_unavailable())?;
            let answer = run_motion_authoring(
                &entrypoint,
                &motion_authoring_request(
                    &work,
                    &runtime.catalog,
                    &runtime.browser,
                    runtime.toolchain.ffprobe_path(),
                    &request,
                    credential.model_id().as_str(),
                    credential.api_key(),
                ),
                MOTION_AUTHORING_DEADLINE,
            )?;
            motion_video_studio::accept_authored_render_job(
                &workspaces,
                &workspace,
                &request,
                &answer,
            )
        })();
        let prepared = match outcome {
            Ok(prepared) => prepared,
            Err(error) => {
                let _ = workspaces.finish(
                    &workspace,
                    video_job_workspace::VideoWorkspaceDisposition::Delete,
                );
                return Err(error);
            }
        };
        start_motion_render(app.clone(), &orchestrator, &workspaces, &runtime, prepared)
    })
    .await
}

/// The shortest deadline the MP4 encode step may be given, kept at the value
/// the fixed three second template shipped with so no existing film loses time.
const MOTION_ENCODE_DEADLINE_FLOOR_SECONDS: u64 = 120;

enum MotionRenderStageFailure {
    Render,
    Encoding,
    Cancelled,
    /// The render captured every frame it was asked for and they are all the
    /// same image. Encoding that produces a well-formed MP4 of the right
    /// length which is, in fact, a still picture — the one failure shape the
    /// worker, FFmpeg and the artifact store all read as success.
    StaticFilm,
}

/// Where the shots of a film wait between being encoded and being joined.
///
/// Under the workspace's output directory rather than its worker asset root:
/// these are finished video files, and the asset root is what the render
/// sandbox is pointed at. Removed once the film exists, on every path.
const MOTION_SEGMENT_DIRECTORY: &str = "segments";

fn run_motion_render_job(
    app: tauri::AppHandle,
    render_job_id: uuid::Uuid,
    segments: Vec<motion_video_studio::MotionRenderSegment>,
    film_canvas: motion_video_studio::MotionFilmCanvas,
    ffmpeg: std::path::PathBuf,
    ffprobe: std::path::PathBuf,
) {
    let workspaces = app.state::<video_job_workspace::VideoJobWorkspaceStore>();
    let orchestrator = app.state::<local_video_orchestrator::LocalVideoOrchestrator>();
    let outcome = (|| -> Result<(), MotionRenderStageFailure> {
        let (work, outputs, film) =
            motion_video_studio::workspace_render_paths(&workspaces, render_job_id)
                .map_err(|_| MotionRenderStageFailure::Render)?;
        let frames = work.join("frames");
        let segment_directory = outputs.join(MOTION_SEGMENT_DIRECTORY);
        std::fs::create_dir_all(&segment_directory)
            .map_err(|_| MotionRenderStageFailure::Encoding)?;
        // One render per shot, and the shots are captured in order so the
        // person watching the progress bar sees the film being made in the
        // order they will watch it.
        let mut encoded = Vec::with_capacity(segments.len());
        for (index, segment) in segments.iter().enumerate() {
            if motion_video_studio::cancellation_requested(&workspaces, render_job_id)
                .map_err(|_| MotionRenderStageFailure::Render)?
            {
                return Err(MotionRenderStageFailure::Cancelled);
            }
            motion_video_studio::advance(
                &workspaces,
                render_job_id,
                motion_video_studio::MotionRenderJobStatus::Rendering,
                // Asked of the module that also judges it. This used to be a
                // formula written here, and the judgement still required the
                // single number a one-render film had — so every render failed
                // on its first shot.
                motion_video_studio::rendering_progress_percent(index, segments.len()),
                None,
                None,
            )
            .map_err(|_| MotionRenderStageFailure::Render)?;
            // A shot left over from the previous one would be read by the
            // still-frame gate and by the encoder's numbered-file pattern
            // alike, so the capture directory starts empty every time.
            let _ = std::fs::remove_dir_all(&frames);
            // Wall clock and CPU seconds both follow this shot's length; a
            // fixed budget would kill every shot longer than the retired
            // template's three seconds.
            let budget = motion_video_studio::render_sandbox_budget(segment.frame_count())
                .map_err(|_| MotionRenderStageFailure::Render)?;
            let request = local_video_orchestrator::VideoWorkerRenderSandboxRequest::new(
                work.clone(),
                segment.entry_html().to_owned(),
                // Resolved before the Worker is asked to render anything: a render
                // that starts without a cancellation marker it recognises is one the
                // user's cancel button cannot reach.
                motion_video_studio::cancel_marker_file_name()
                    .map_err(|_| MotionRenderStageFailure::Render)?
                    .to_owned(),
                // The stage this shot declares. A catalog part is an
                // independent composition drawn for its own stage, and
                // capturing it on the template's would capture the top-left
                // corner of it.
                local_video_orchestrator::VideoWorkerRenderCanvas::new(
                    segment.width(),
                    segment.height(),
                    segment.device_scale_factor(),
                )
                .map_err(|_| MotionRenderStageFailure::Render)?,
                // Which stretch of the loaded document this shot is. Template
                // shots all load the one composition, so without this they all
                // rendered the whole film — see `VideoWorkerSourceWindow`.
                local_video_orchestrator::VideoWorkerSourceWindow::new(
                    segment.source_start_millis(),
                    segment.source_end_millis(),
                )
                .map_err(|_| MotionRenderStageFailure::Render)?,
                segment.allowed_assets().to_vec(),
                segment.frame_count(),
                budget.wall_seconds(),
                budget.cpu_seconds(),
                2048,
                256 * 1024 * 1024,
            )
            .map_err(|_| MotionRenderStageFailure::Render)?;
            orchestrator
                .render_sandbox(
                    local_video_orchestrator::VideoWorkerKind::Node,
                    render_job_id,
                    &request,
                )
                .map_err(|_| {
                    if motion_video_studio::cancellation_requested(&workspaces, render_job_id)
                        .unwrap_or(false)
                    {
                        MotionRenderStageFailure::Cancelled
                    } else {
                        MotionRenderStageFailure::Render
                    }
                })?;
            if motion_video_studio::cancellation_requested(&workspaces, render_job_id)
                .map_err(|_| MotionRenderStageFailure::Render)?
            {
                return Err(MotionRenderStageFailure::Cancelled);
            }
            // Between "the worker captured N frames" and "here is your video"
            // this is the only check that can tell a film from a still image. A
            // composition sized to the wrong stage, or one whose clips never take
            // turns, reaches exactly this point with a full set of identical
            // frames and every other signal green. Asked per shot, because a
            // film assembled from nine shots of which one never moved is nine
            // times as easy to produce and reads exactly the same at the end.
            if motion_video_studio::rendered_film_is_static(&frames, segment.frame_count())
                .map_err(|_| MotionRenderStageFailure::Render)?
            {
                return Err(MotionRenderStageFailure::StaticFilm);
            }
            let encoded_segment = segment_directory.join(format!("segment-{index:03}.mp4"));
            encode_motion_segment(
                &workspaces,
                render_job_id,
                &ffmpeg,
                &frames,
                &encoded_segment,
                &film_canvas,
                segment.frame_count(),
            )?;
            encoded.push(encoded_segment);
        }
        motion_video_studio::advance(
            &workspaces,
            render_job_id,
            motion_video_studio::MotionRenderJobStatus::Encoding,
            85,
            None,
            None,
        )
        .map_err(|_| MotionRenderStageFailure::Encoding)?;
        let expected_segment_frames = segments
            .iter()
            .map(motion_video_studio::MotionRenderSegment::frame_count)
            .collect::<Vec<_>>();
        let rendered_frames = motion_video_studio::join_motion_film(
            &encoded,
            &film,
            &film_canvas,
            &ffmpeg,
            &ffprobe,
            &expected_segment_frames,
        )
        .map_err(|_| MotionRenderStageFailure::Encoding)?;
        motion_video_studio::record_rendered_shot_frames(
            &workspaces,
            render_job_id,
            &rendered_frames,
        )
        .map_err(|_| MotionRenderStageFailure::Encoding)?;
        let total_frames = rendered_frames
            .iter()
            .try_fold(0_u32, |total, frames| total.checked_add(*frames))
            .ok_or(MotionRenderStageFailure::Encoding)?;
        // PC-26: lay each shot's narration onto the joined film. A silent film
        // returns immediately, so the pre-narration pipeline is byte-identical.
        motion_video_studio::mix_narration_into_film(
            &film,
            &work,
            &segments,
            &film_canvas,
            &ffmpeg,
            &ffprobe,
            total_frames,
        )
        .map_err(|_| MotionRenderStageFailure::Encoding)?;
        let artifact = motion_video_studio::import_rendered_output(&workspaces, render_job_id)
            .map_err(|_| MotionRenderStageFailure::Encoding)?;
        motion_video_studio::advance(
            &workspaces,
            render_job_id,
            motion_video_studio::MotionRenderJobStatus::Succeeded,
            100,
            Some(&artifact),
            None,
        )
        .map_err(|_| MotionRenderStageFailure::Encoding)?;
        Ok(())
    })();
    let _ = orchestrator.stop(local_video_orchestrator::VideoWorkerKind::Node);
    if let Err(failure) = outcome {
        let current = motion_video_studio::snapshot(&workspaces, render_job_id).ok();
        let stop_was_requested = current.as_ref().is_some_and(|snapshot| {
            snapshot.status() == motion_video_studio::MotionRenderJobStatus::Cancelling
        });
        if matches!(failure, MotionRenderStageFailure::Cancelled) || stop_was_requested {
            // Everything this thread owns has now stopped: the Worker is
            // stopped above and FFmpeg was killed and its partial output
            // removed before the stage returned. This is the first moment
            // anything is entitled to say the render is over, so this is where
            // the terminal state is written — not in the command that asked.
            //
            // A stage that failed for its own reasons while a stop was pending
            // also settles as cancelled: the operator asked for the run to end
            // and it ended with no film, which is what 已取消 says. Reporting
            // 制作失败 would offer him a fix for a run he had already abandoned.
            let progress = current
                .map(|snapshot| snapshot.progress_percent())
                .unwrap_or(5)
                .min(99);
            let _ = motion_video_studio::advance(
                &workspaces,
                render_job_id,
                motion_video_studio::MotionRenderJobStatus::Cancelled,
                progress,
                None,
                None,
            );
        } else {
            let code = match failure {
                MotionRenderStageFailure::Render => {
                    motion_video_studio::MotionRenderFailureCode::RenderFailed
                }
                MotionRenderStageFailure::Encoding => {
                    motion_video_studio::MotionRenderFailureCode::EncodingFailed
                }
                MotionRenderStageFailure::StaticFilm => {
                    motion_video_studio::MotionRenderFailureCode::StaticRender
                }
                MotionRenderStageFailure::Cancelled => unreachable!(),
            };
            let progress = current
                .map(|snapshot| snapshot.progress_percent())
                .unwrap_or(5)
                .min(99);
            let _ = motion_video_studio::advance(
                &workspaces,
                render_job_id,
                motion_video_studio::MotionRenderJobStatus::Failed,
                progress,
                None,
                Some(code),
            );
        }
    }
    if let Ok((work, outputs, _)) =
        motion_video_studio::workspace_render_paths(&workspaces, render_job_id)
    {
        let _ = std::fs::remove_dir_all(work.join("frames"));
        // The shots are an intermediate the film no longer needs, and a
        // cancelled or failed run leaves as many of them as it got through.
        let _ = std::fs::remove_dir_all(outputs.join(MOTION_SEGMENT_DIRECTORY));
    }
    if let Ok(workspace) = workspaces.open(render_job_id) {
        let _ = workspaces.finish(
            &workspace,
            video_job_workspace::VideoWorkspaceDisposition::Keep,
        );
    }
}

/// Encode one captured shot onto the film's canvas, and wait for it here.
///
/// The waiting is why this stays in the render job rather than beside the
/// arguments it runs: this is the loop that notices the user pressed stop, and
/// kills FFmpeg and removes the half-written file before returning. A shot that
/// outlives its deadline is treated the same way, because a file that FFmpeg was
/// still writing is not a shot the join may pick up.
fn encode_motion_segment(
    workspaces: &video_job_workspace::VideoJobWorkspaceStore,
    render_job_id: uuid::Uuid,
    ffmpeg: &std::path::Path,
    frames_directory: &std::path::Path,
    output: &std::path::Path,
    canvas: &motion_video_studio::MotionFilmCanvas,
    frame_count: u32,
) -> Result<(), MotionRenderStageFailure> {
    let mut child = motion_video_studio::motion_segment_encode_command(
        ffmpeg,
        frames_directory,
        output,
        canvas,
        frame_count,
    )
    .spawn()
    .map_err(|_| MotionRenderStageFailure::Encoding)?;
    // Encoding six times the frames cannot share a fixed deadline either. The
    // derived render budget is the per-frame yardstick; the floor keeps the
    // shortest films on the deadline they already shipped with.
    let deadline_seconds = motion_video_studio::render_sandbox_budget(frame_count)
        .map(|budget| u64::from(budget.wall_seconds()))
        .unwrap_or(MOTION_ENCODE_DEADLINE_FLOOR_SECONDS)
        .max(MOTION_ENCODE_DEADLINE_FLOOR_SECONDS);
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(deadline_seconds);
    loop {
        if motion_video_studio::cancellation_requested(workspaces, render_job_id).unwrap_or(false) {
            let _ = child.kill();
            let _ = child.wait();
            let _ = std::fs::remove_file(output);
            return Err(MotionRenderStageFailure::Cancelled);
        }
        match child.try_wait() {
            Ok(Some(status)) if status.success() => return Ok(()),
            Ok(Some(_)) | Err(_) => return Err(MotionRenderStageFailure::Encoding),
            Ok(None) if std::time::Instant::now() < deadline => {
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                let _ = std::fs::remove_file(output);
                return Err(MotionRenderStageFailure::Encoding);
            }
        }
    }
}

#[tauri::command]
fn get_motion_render_jobs(
    workspaces: tauri::State<'_, video_job_workspace::VideoJobWorkspaceStore>,
) -> Result<
    Vec<motion_video_studio::MotionRenderJobSnapshot>,
    motion_video_studio::MotionVideoStudioError,
> {
    motion_video_studio::jobs(&workspaces)
}

#[tauri::command]
fn cancel_motion_render_job(
    render_job_id: uuid::Uuid,
    workspaces: tauri::State<'_, video_job_workspace::VideoJobWorkspaceStore>,
) -> Result<(), motion_video_studio::MotionVideoStudioError> {
    motion_video_studio::cancel(&workspaces, render_job_id)
}

#[tauri::command]
fn read_motion_video_artifact(
    artifact_id: uuid::Uuid,
    workspaces: tauri::State<'_, video_job_workspace::VideoJobWorkspaceStore>,
) -> Result<
    video_job_workspace::RenderedVideoArtifactPayload,
    motion_video_studio::MotionVideoStudioError,
> {
    motion_video_studio::read_artifact(&workspaces, artifact_id)
}

#[tauri::command]
fn delete_motion_video_artifact(
    artifact_id: uuid::Uuid,
    workspaces: tauri::State<'_, video_job_workspace::VideoJobWorkspaceStore>,
) -> Result<(), motion_video_studio::MotionVideoStudioError> {
    motion_video_studio::delete_artifact(&workspaces, artifact_id)
}

/// The startup gate. It is the only place that refuses to mount the workbench
/// when this installation is missing a runtime dependency, so every build —
/// including every acceptance build — compiles exactly this one and performs
/// exactly these probes.
#[tauri::command]
async fn check_local_startup_environment(
    app: tauri::AppHandle,
) -> startup_environment::StartupEnvironmentSnapshot {
    app_logging::record(app_logging::DesktopLogEvent::StartupLocalCheckStarted);
    // The gate hashes every byte of the packaged Executor and, the first time,
    // of the packaged browser as well — 519 MB between them, off a disk that is
    // cold on the launch that matters most. That is not work the UI thread may
    // own: a window that cannot repaint while it happens is the first thing a
    // customer sees, and it looks exactly like a hang.
    let result = tauri::async_runtime::spawn_blocking(move || {
        let (Some(startup), Some(profiles), Some(authority), Some(platform)) = (
            app.try_state::<startup_environment::StartupEnvironmentService>(),
            app.try_state::<browser_profiles::BrowserProfileStore>(),
            app.try_state::<embedded_browser_authority::EmbeddedBrowserAuthority>(),
            app.try_state::<executor_platform::ExecutorPlatformService>(),
        ) else {
            return startup_environment_unusable();
        };
        let app_data = if startup.app_data_state()
            == startup_environment::AppDataStartupState::Ready
            && profiles.revalidate_storage().is_ok()
        {
            startup_environment::AppDataStartupState::Ready
        } else {
            startup_environment::AppDataStartupState::Unavailable
        };
        app_logging::record(app_logging::DesktopLogEvent::StartupAppDataCheckCompleted);
        // EB-08：健康状态来自内置发行物验证，不再询问系统浏览器发现/选择。
        let embedded_browser = match authority.resolve() {
            Ok(_) => startup_environment::EmbeddedBrowserStartupState::Ready,
            Err(embedded_browser_authority::EmbeddedBrowserAuthorityError::ComponentMissing) => {
                startup_environment::EmbeddedBrowserStartupState::ComponentMissing
            }
            Err(embedded_browser_authority::EmbeddedBrowserAuthorityError::VersionIncompatible) => {
                startup_environment::EmbeddedBrowserStartupState::VersionIncompatible
            }
            Err(_) => startup_environment::EmbeddedBrowserStartupState::ComponentDamaged,
        };
        app_logging::record(app_logging::DesktopLogEvent::StartupBrowserCheckCompleted);
        app_logging::record(app_logging::DesktopLogEvent::StartupExecutorCheckStarted);
        let executor = platform.startup_environment_state();
        app_logging::record(app_logging::DesktopLogEvent::StartupExecutorCheckCompleted);
        startup_environment::StartupEnvironmentSnapshot::new(app_data, executor, embedded_browser)
    })
    .await;
    match result {
        Ok(snapshot) => {
            app_logging::record(app_logging::DesktopLogEvent::StartupLocalCheckCompleted);
            snapshot
        }
        Err(_) => {
            app_logging::record(app_logging::DesktopLogEvent::StartupLocalCheckRejected);
            startup_environment_unusable()
        }
    }
}

/// What the startup gate answers when it could not run its probes at all.
///
/// The gate is the only thing that keeps the workbench closed on an install
/// that cannot run, so a probe that did not happen is not a pass. Nothing here
/// is reachable once the App's own `setup` has succeeded — it manages all four
/// of these services unconditionally — but a gate that reports ready because it
/// could not ask is precisely the shape `single_build_path` exists to forbid.
fn startup_environment_unusable() -> startup_environment::StartupEnvironmentSnapshot {
    startup_environment::StartupEnvironmentSnapshot::new(
        startup_environment::AppDataStartupState::Unavailable,
        startup_environment::ExecutorStartupState::Unavailable,
        startup_environment::EmbeddedBrowserStartupState::ComponentDamaged,
    )
}

fn map_executor_platform_error(
    error: executor_platform::ExecutorPlatformError,
) -> ExecutorPlatformCommandError {
    let (code, retryable) = match error.code() {
        executor_platform::ExecutorPlatformErrorCode::ConfigurationInvalid => {
            ("configuration_invalid", false)
        }
        executor_platform::ExecutorPlatformErrorCode::StorageUnavailable => {
            ("storage_unavailable", false)
        }
        executor_platform::ExecutorPlatformErrorCode::AlreadyRunning => ("already_running", true),
        executor_platform::ExecutorPlatformErrorCode::AuthenticationRejected => {
            ("authentication_rejected", false)
        }
        executor_platform::ExecutorPlatformErrorCode::PackageRejected => {
            ("package_rejected", false)
        }
        executor_platform::ExecutorPlatformErrorCode::ProcessUnavailable => {
            ("process_unavailable", true)
        }
        executor_platform::ExecutorPlatformErrorCode::TimedOut => ("timed_out", true),
        // Recoverable, but only by the operator: pressing the same button again
        // hits the same abandoned lock marker forever.
        executor_platform::ExecutorPlatformErrorCode::RecoveryRequired => {
            ("profile_recovery_required", false)
        }
    };
    ExecutorPlatformCommandError { code, retryable }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn map_browser_profile_logout_error(
    error: browser_profiles::BrowserProfileError,
) -> ExecutorPlatformCommandError {
    let (code, retryable) = match error.code() {
        browser_profiles::BrowserProfileErrorCode::ProfileInUse => ("profile_in_use", true),
        browser_profiles::BrowserProfileErrorCode::RecoveryRequired => {
            ("profile_recovery_required", false)
        }
        // PC-25：这五种此前一起塌成 `storage_unavailable`，于是界面对每一种
        // 都说「这是本产品自身的问题，重新操作不会有效」，而 b5_13 查了四轮
        // 也没能从那个字符串里读出是哪一步失败的。各自成码。
        browser_profiles::BrowserProfileErrorCode::IdentityChanged => {
            ("profile_identity_changed", false)
        }
        browser_profiles::BrowserProfileErrorCode::ProfileNotFound => ("profile_missing", false),
        browser_profiles::BrowserProfileErrorCode::UnsafeDirectory => {
            ("profile_directory_unsafe", false)
        }
        browser_profiles::BrowserProfileErrorCode::InvalidProfileId => {
            ("profile_marker_invalid", false)
        }
        browser_profiles::BrowserProfileErrorCode::StorageUnavailable => {
            ("storage_unavailable", false)
        }
    };
    ExecutorPlatformCommandError { code, retryable }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn map_executor_connection_error(
    error: control_plane::ControlPlaneError,
) -> ExecutorPlatformCommandError {
    let retryable = error.retryable();
    let code = match error.code() {
        control_plane::ControlPlaneErrorCode::CredentialMissing => "credential_missing",
        control_plane::ControlPlaneErrorCode::InstallationAccessDenied => {
            "installation_access_denied"
        }
        control_plane::ControlPlaneErrorCode::InstallationBusy => "operation_unavailable",
        control_plane::ControlPlaneErrorCode::InstallationConflict => "installation_conflict",
        control_plane::ControlPlaneErrorCode::TransportUnavailable
        | control_plane::ControlPlaneErrorCode::OutcomeUncertain => "transport_unavailable",
        control_plane::ControlPlaneErrorCode::IdentityUnavailable
        | control_plane::ControlPlaneErrorCode::StorageUnavailable => "storage_unavailable",
        control_plane::ControlPlaneErrorCode::AuthenticationInvalid
        | control_plane::ControlPlaneErrorCode::RecoveryInvalid
        | control_plane::ControlPlaneErrorCode::AccountSessionInvalid => "operation_unavailable",
        control_plane::ControlPlaneErrorCode::ProtocolInvalid
        | control_plane::ControlPlaneErrorCode::RequestRejected
        | control_plane::ControlPlaneErrorCode::ResourceNotFound => "operation_unavailable",
    };
    ExecutorPlatformCommandError { code, retryable }
}

#[tauri::command]
fn get_executor_status(
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<executor_manager::ExecutorManagerStatus, ExecutorPlatformCommandError> {
    platform.status().map_err(map_executor_platform_error)
}

#[tauri::command]
fn get_executor_diagnostics(
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<ExecutorDiagnosticsSnapshot, ExecutorPlatformCommandError> {
    platform
        .diagnostics()
        .map(|lines| ExecutorDiagnosticsSnapshot { lines })
        .map_err(map_executor_platform_error)
}

#[tauri::command]
async fn export_diagnostics(
    app: tauri::AppHandle,
    exporter: tauri::State<'_, diagnostic_export::DiagnosticExportService>,
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<diagnostic_export::DiagnosticExportReceipt, DiagnosticExportCommandError> {
    let diagnostics = platform
        .diagnostics()
        .map_err(map_executor_platform_error)
        .map_err(|error| DiagnosticExportCommandError {
            code: error.code,
            retryable: error.retryable,
        })?;
    #[cfg(feature = "desktop-e2e")]
    let export_directory = match std::env::var_os("AUTOMATION_TOOL_H813_EXPORT_DIRECTORY") {
        Some(directory) => std::path::PathBuf::from(directory),
        None => app
            .path()
            .download_dir()
            .map_err(|_| DiagnosticExportCommandError {
                code: "storage_unavailable",
                retryable: false,
            })?,
    };
    #[cfg(not(feature = "desktop-e2e"))]
    let export_directory = app
        .path()
        .download_dir()
        .map_err(|_| DiagnosticExportCommandError {
            code: "storage_unavailable",
            retryable: false,
        })?;
    let service = exporter.inner().clone();
    tauri::async_runtime::spawn_blocking(move || service.export(&export_directory, &diagnostics))
        .await
        .map_err(|_| DiagnosticExportCommandError {
            code: "storage_unavailable",
            retryable: false,
        })?
        .map_err(map_diagnostic_export_error)
}

#[tauri::command]
fn get_browser_diagnostic_settings(
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<executor_platform::BrowserDiagnosticSettingsSnapshot, ExecutorPlatformCommandError> {
    platform
        .browser_diagnostic_settings()
        .map_err(map_executor_platform_error)
}

#[tauri::command]
fn set_capture_successful_diagnostics(
    enabled: bool,
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<executor_platform::BrowserDiagnosticSettingsSnapshot, ExecutorPlatformCommandError> {
    platform
        .set_capture_successful_diagnostics(enabled)
        .map_err(map_executor_platform_error)
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct HostileDiagnosticFixtureDocument {
    cases: Vec<HostileDiagnosticFixtureCase>,
    fixture_version: String,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct HostileDiagnosticFixtureCase {
    expected: String,
    input: String,
    name: String,
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
fn inject_hostile_executor_diagnostics_for_acceptance(
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<(), ExecutorPlatformCommandError> {
    let document: HostileDiagnosticFixtureDocument = serde_json::from_str(include_str!(
        "../../../contracts/fixtures/executor-diagnostics-v1.json"
    ))
    .map_err(|_| ExecutorPlatformCommandError {
        code: "configuration_invalid",
        retryable: false,
    })?;
    if document.fixture_version != "2" || document.cases.len() < 18 {
        return Err(ExecutorPlatformCommandError {
            code: "configuration_invalid",
            retryable: false,
        });
    }
    for case in document.cases {
        if case.expected.is_empty() || case.name.is_empty() {
            return Err(ExecutorPlatformCommandError {
                code: "configuration_invalid",
                retryable: false,
            });
        }
        platform.inject_raw_diagnostic_for_acceptance(case.input.as_bytes());
    }
    Ok(())
}

#[tauri::command]
fn emergency_stop_executor(
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<executor_manager::ExecutorManagerStatus, ExecutorPlatformCommandError> {
    app_logging::record(app_logging::DesktopLogEvent::ExecutorEmergencyStopRequested);
    platform
        .emergency_stop()
        .map_err(map_executor_platform_error)
}

#[tauri::command]
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
async fn restart_executor(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<executor_manager::ExecutorManagerStatus, ExecutorPlatformCommandError> {
    app_logging::record(app_logging::DesktopLogEvent::ExecutorRestartRequested);
    let connection = client
        .issue_executor_connection(&vault)
        .await
        .map_err(map_executor_connection_error)?;
    let service = platform.inner().clone();
    tauri::async_runtime::spawn_blocking(move || service.restart(connection))
        .await
        .map_err(|_| ExecutorPlatformCommandError {
            code: "process_unavailable",
            retryable: true,
        })?
        .map_err(map_executor_platform_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
async fn ensure_executor_running(
    client: &control_plane::ControlPlaneClient,
    vault: &ProductionDeviceCredentialVault,
    platform: &executor_platform::ExecutorPlatformService,
) -> Result<(), ExecutorPlatformCommandError> {
    let status = platform.status().map_err(map_executor_platform_error)?;
    match status.state() {
        executor_manager::ExecutorManagerState::Stopped => {
            app_logging::record(app_logging::DesktopLogEvent::ExecutorAutoStartRequested);
            let connection = client
                .issue_executor_connection(vault)
                .await
                .map_err(map_executor_connection_error)?;
            let service = platform.clone();
            tauri::async_runtime::spawn_blocking(move || service.restart(connection))
                .await
                .map_err(|_| ExecutorPlatformCommandError {
                    code: "process_unavailable",
                    retryable: true,
                })?
                .map_err(map_executor_platform_error)?;
            Ok(())
        }
        executor_manager::ExecutorManagerState::Restarting => Err(ExecutorPlatformCommandError {
            code: "process_unavailable",
            retryable: true,
        }),
        executor_manager::ExecutorManagerState::Running => Ok(()),
    }
}

/// EB-07：运营浏览器唯一路径源是内置发行物 Authority，无系统浏览器发现 fallback。
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn resolve_embedded_browser(
    authority: &embedded_browser_authority::EmbeddedBrowserAuthority,
) -> Result<std::path::PathBuf, ExecutorPlatformCommandError> {
    authority
        .resolve()
        .map_err(|error| ExecutorPlatformCommandError {
            code: match error {
                embedded_browser_authority::EmbeddedBrowserAuthorityError::ComponentMissing => {
                    "browser_component_missing"
                }
                embedded_browser_authority::EmbeddedBrowserAuthorityError::ComponentInvalid => {
                    "browser_component_invalid"
                }
                embedded_browser_authority::EmbeddedBrowserAuthorityError::VersionIncompatible => {
                    "browser_component_version_incompatible"
                }
                embedded_browser_authority::EmbeddedBrowserAuthorityError::Unavailable => {
                    "storage_unavailable"
                }
            },
            retryable: false,
        })
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
async fn execute_douyin_login_command(
    command: executor_bootstrap::LocalPlatformCommand,
    client: &control_plane::ControlPlaneClient,
    vault: &ProductionDeviceCredentialVault,
    platform: &executor_platform::ExecutorPlatformService,
    authority: &embedded_browser_authority::EmbeddedBrowserAuthority,
    profiles: &browser_profiles::BrowserProfileStore,
) -> Result<executor_bootstrap::LocalPlatformCommandResult, ExecutorPlatformCommandError> {
    ensure_executor_running(client, vault, platform).await?;
    let executable_path = resolve_embedded_browser(authority)?;
    let profile = profiles
        .current_douyin_profile()
        .map_err(|_| ExecutorPlatformCommandError {
            code: "storage_unavailable",
            retryable: false,
        })?;
    let service = platform.clone();
    tauri::async_runtime::spawn_blocking(move || {
        service.execute_platform_command(
            command,
            executable_path,
            profile,
            cfg!(feature = "control-plane-e2e"),
        )
    })
    .await
    .map_err(|_| ExecutorPlatformCommandError {
        code: "process_unavailable",
        retryable: true,
    })?
    .map_err(map_executor_platform_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn open_douyin_login(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
    authority: tauri::State<'_, embedded_browser_authority::EmbeddedBrowserAuthority>,
    profiles: tauri::State<'_, browser_profiles::BrowserProfileStore>,
) -> Result<executor_bootstrap::LocalPlatformCommandResult, ExecutorPlatformCommandError> {
    execute_douyin_login_command(
        executor_bootstrap::LocalPlatformCommand::OpenDouyinLogin,
        &client,
        &vault,
        &platform,
        &authority,
        &profiles,
    )
    .await
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn recheck_douyin_login(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
    authority: tauri::State<'_, embedded_browser_authority::EmbeddedBrowserAuthority>,
    profiles: tauri::State<'_, browser_profiles::BrowserProfileStore>,
) -> Result<executor_bootstrap::LocalPlatformCommandResult, ExecutorPlatformCommandError> {
    execute_douyin_login_command(
        executor_bootstrap::LocalPlatformCommand::RecheckDouyinLogin,
        &client,
        &vault,
        &platform,
        &authority,
        &profiles,
    )
    .await
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
const DOUYIN_LOGOUT_PROJECTION_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(60);

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn logout_douyin_session(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
    profiles: tauri::State<'_, browser_profiles::BrowserProfileStore>,
) -> Result<control_plane::PlatformSessionStatus, ExecutorPlatformCommandError> {
    client
        .prepare_douyin_platform_session_logout(&vault)
        .await
        .map_err(map_executor_connection_error)?;

    // PC-25：删除 Profile 之前，先让浏览器的主人送走浏览器。此前的顺序是
    // 「紧停 → 删 → 重启 → 由新执行器补记注销」，而登录浏览器由旧执行器经
    // Playwright 拉起、不在紧停所杀的进程组里——删除刚完成 6 毫秒，垂死的
    // Chromium 就把目录重建了回来（b5_13 实测时间线），终检于是正确拒绝。
    // 现在：执行器还在跑就让它优雅关闭登录会话（Playwright 会等浏览器真正
    // 退出）并记录注销；关不掉就把可重试错误原样抛出去，Profile 一根汗毛
    // 不动（fail-closed）。执行器本就停着的情况下没有它拉起的浏览器可关，
    // 跳过即可；更早被紧停遗留的孤儿浏览器仍由删除终检兜底报错。
    let running = {
        let service = platform.inner().clone();
        matches!(
            service
                .status()
                .map_err(map_executor_platform_error)?
                .state(),
            executor_manager::ExecutorManagerState::Running
        )
    };
    if running {
        let service = platform.inner().clone();
        let result = tauri::async_runtime::spawn_blocking(move || {
            service.execute_session_command(
                executor_bootstrap::LocalPlatformCommand::CompleteDouyinLogout,
            )
        })
        .await
        .map_err(|_| ExecutorPlatformCommandError {
            code: "process_unavailable",
            retryable: true,
        })?
        .map_err(map_executor_platform_error)?;
        if result.state() != "logged_out" {
            return Err(ExecutorPlatformCommandError {
                code: "authentication_rejected",
                retryable: false,
            });
        }
    }

    // 执行器在跑的路径不再紧停：浏览器已被上面那条命令优雅关闭（Playwright
    // 等它真正退出），而执行器进程自己并不持有 Profile 目录——停它只会连带
    // 杀掉刚入队、尚未送达的注销信封，让权威投影停在旧版本上（b5_13 的
    // 「闸版本 2、健康版本 1」正是这个）。留着它跑，信封照常送达。
    // 执行器本来停着的那条路径仍需紧停+重启：那种情况下可能有更早遗留的
    // 孤儿浏览器，且需要一个活着的执行器来补记注销信封。
    if !running {
        let service = platform.inner().clone();
        app_logging::record(app_logging::DesktopLogEvent::ExecutorEmergencyStopRequested);
        tauri::async_runtime::spawn_blocking(move || service.emergency_stop())
            .await
            .map_err(|_| ExecutorPlatformCommandError {
                code: "process_unavailable",
                retryable: true,
            })?
            .map_err(map_executor_platform_error)?;
    }

    profiles
        .remove_current_douyin_profile()
        .map_err(map_browser_profile_logout_error)?;

    if !running {
        let connection = client
            .issue_executor_connection(&vault)
            .await
            .map_err(map_executor_connection_error)?;
        let service = platform.inner().clone();
        tauri::async_runtime::spawn_blocking(move || service.restart(connection))
            .await
            .map_err(|_| ExecutorPlatformCommandError {
                code: "process_unavailable",
                retryable: true,
            })?
            .map_err(map_executor_platform_error)?;
    }

    if !running {
        // 执行器本来停着：上面没有任何人发注销信封，投影永远到不了
        // missing——由重启后的执行器补记（它没有可关的浏览器，只做记录）。
        let service = platform.inner().clone();
        let result = tauri::async_runtime::spawn_blocking(move || {
            service.execute_session_command(
                executor_bootstrap::LocalPlatformCommand::CompleteDouyinLogout,
            )
        })
        .await
        .map_err(|_| ExecutorPlatformCommandError {
            code: "process_unavailable",
            retryable: true,
        })?
        .map_err(map_executor_platform_error)?;
        if result.state() != "logged_out" {
            return Err(ExecutorPlatformCommandError {
                code: "authentication_rejected",
                retryable: false,
            });
        }
    }

    match tokio::time::timeout(DOUYIN_LOGOUT_PROJECTION_TIMEOUT, async {
        loop {
            let snapshot = client
                .get_douyin_platform_session(&vault)
                .await
                .map_err(map_executor_connection_error)?;
            if snapshot.state() == "missing" {
                return Ok(snapshot);
            }
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }
    })
    .await
    {
        Ok(result) => result,
        Err(_) => Err(ExecutorPlatformCommandError {
            code: "timed_out",
            retryable: true,
        }),
    }
}

/// PB-07: one operator's publishing view, shared by the four publish Commands.
///
/// It lives in the App rather than the executor because it *is* a view: the
/// executor owns the irreversible facts (its durable at-most-once ledger), and
/// this owns only what the operator has been shown and has agreed to.
/// The composition root manages this in every build, so it is declared in
/// every build. Gating the type but not the `manage` call left the
/// `video-studio-e2e` combination unable to compile at all.
pub struct PublishWorkspaceState(pub std::sync::Mutex<publish_workspace::PublishWorkspace>);

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
struct BilibiliPendingPublish {
    job_id: String,
    session_token: zeroize::Zeroizing<String>,
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
pub struct BilibiliPublishSessionState(std::sync::Mutex<Option<BilibiliPendingPublish>>);

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
impl BilibiliPublishSessionState {
    fn new() -> Self {
        Self(std::sync::Mutex::new(None))
    }

    fn replace(
        &self,
        job_id: String,
        session_token: zeroize::Zeroizing<String>,
    ) -> Result<(), ExecutorPlatformCommandError> {
        let mut held = self.0.lock().map_err(|_| publish_workspace_unavailable())?;
        *held = Some(BilibiliPendingPublish {
            job_id,
            session_token,
        });
        Ok(())
    }

    fn take(
        &self,
        expected_job_id: &str,
    ) -> Result<BilibiliPendingPublish, ExecutorPlatformCommandError> {
        let mut held = self.0.lock().map_err(|_| publish_workspace_unavailable())?;
        if held
            .as_ref()
            .is_none_or(|pending| pending.job_id != expected_job_id)
        {
            return Err(ExecutorPlatformCommandError {
                code: "publish_nothing_to_confirm",
                retryable: false,
            });
        }
        held.take().ok_or(ExecutorPlatformCommandError {
            code: "publish_nothing_to_confirm",
            retryable: false,
        })
    }

    fn clear(&self) -> Result<(), ExecutorPlatformCommandError> {
        let mut held = self.0.lock().map_err(|_| publish_workspace_unavailable())?;
        held.take();
        Ok(())
    }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn publish_workspace_unavailable() -> ExecutorPlatformCommandError {
    ExecutorPlatformCommandError {
        code: "storage_unavailable",
        retryable: false,
    }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn map_publish_control_plane_error(
    error: control_plane::ControlPlaneError,
) -> ExecutorPlatformCommandError {
    let mapped = map_control_plane_error(error);
    ExecutorPlatformCommandError {
        code: mapped.code,
        retryable: mapped.retryable,
    }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn map_bilibili_service_error(
    error: bilibili_service_settings::BilibiliServiceError,
) -> ExecutorPlatformCommandError {
    use bilibili_service_settings::BilibiliServiceErrorCode;
    let code = match error.code() {
        BilibiliServiceErrorCode::ConfigurationRequired => "publish_not_available",
        BilibiliServiceErrorCode::ConfigurationInvalid => "configuration_invalid",
        BilibiliServiceErrorCode::StorageUnavailable => "storage_unavailable",
    };
    ExecutorPlatformCommandError {
        code,
        retryable: error.retryable(),
    }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn map_publish_workspace_error(
    error: publish_workspace::PublishWorkspaceError,
) -> ExecutorPlatformCommandError {
    let code = match error {
        publish_workspace::PublishWorkspaceError::UnknownPlatform => "configuration_invalid",
        publish_workspace::PublishWorkspaceError::NotPublishable => "publish_not_available",
        publish_workspace::PublishWorkspaceError::UnreadableApproval => "publish_not_confirmable",
        publish_workspace::PublishWorkspaceError::NoApprovalPending => "publish_nothing_to_confirm",
        publish_workspace::PublishWorkspaceError::AlreadyDispatched => "publish_already_dispatched",
        publish_workspace::PublishWorkspaceError::NothingInFlight => "publish_nothing_in_flight",
    };
    ExecutorPlatformCommandError {
        code,
        retryable: false,
    }
}

/// Say why a chosen video cannot be published, without naming a local path.
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn map_video_workspace_error(
    error: video_job_workspace::VideoWorkspaceError,
) -> ExecutorPlatformCommandError {
    use video_job_workspace::VideoWorkspaceErrorCode;
    let code = match error.code() {
        // The operator picked a video that is no longer there — most likely
        // deleted from the finished-videos page while it sat selected.
        VideoWorkspaceErrorCode::NotFound => "publish_video_unavailable",
        // Registered, but not a finished video this App may publish.
        VideoWorkspaceErrorCode::ConfigurationInvalid => "publish_video_not_publishable",
        VideoWorkspaceErrorCode::QuotaExceeded => "storage_quota_exceeded",
        VideoWorkspaceErrorCode::AlreadyExists
        | VideoWorkspaceErrorCode::PathRejected
        | VideoWorkspaceErrorCode::StorageUnavailable => "storage_unavailable",
    };
    ExecutorPlatformCommandError {
        code,
        retryable: false,
    }
}

/// Refresh what the operator is allowed to do, then hand back the whole view.
///
/// Availability is read here rather than accepted from the App: a page that
/// could assert its own platform was ready would be asserting its way past the
/// one check that stops a publish being typed into a signed-out browser.
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn get_publish_workspace(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    settings: tauri::State<'_, bilibili_service_settings::ProductionBilibiliServiceSettings>,
    workspace: tauri::State<'_, PublishWorkspaceState>,
) -> Result<publish_workspace::PublishWorkspaceSnapshot, ExecutorPlatformCommandError> {
    // A session this App cannot read is not a session it may publish through.
    let signed_in = client
        .get_douyin_platform_session(&vault)
        .await
        .map(|session| session.state() == "healthy")
        .unwrap_or(false);
    let bilibili_configured = settings
        .snapshot()
        .map(|snapshot| snapshot.configured())
        .unwrap_or(false);
    let mut held = workspace
        .0
        .lock()
        .map_err(|_| publish_workspace_unavailable())?;
    held.observe_douyin_signed_in(signed_in);
    held.observe_bilibili_configured(bilibili_configured);
    Ok(held.snapshot())
}

/// Open the publish page for one video and stop before submission.
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
#[allow(clippy::too_many_arguments)]
async fn begin_publish(
    app: tauri::AppHandle,
    platform: String,
    artifact_id: uuid::Uuid,
    video_summary: String,
    title: String,
    description: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    executor: tauri::State<'_, executor_platform::ExecutorPlatformService>,
    authority: tauri::State<'_, embedded_browser_authority::EmbeddedBrowserAuthority>,
    profiles: tauri::State<'_, browser_profiles::BrowserProfileStore>,
    workspaces: tauri::State<'_, video_job_workspace::VideoJobWorkspaceStore>,
    settings: tauri::State<'_, bilibili_service_settings::ProductionBilibiliServiceSettings>,
    bilibili_session: tauri::State<'_, BilibiliPublishSessionState>,
    workspace: tauri::State<'_, PublishWorkspaceState>,
) -> Result<publish_workspace::PublishWorkspaceSnapshot, ExecutorPlatformCommandError> {
    // One publish, one identity, minted here. The page never supplies it, so it
    // cannot make two publishes share one.
    let publish_job_id = video_job_workspace::generate_uuid_v4()
        .map_err(map_video_workspace_error)?
        .hyphenated()
        .to_string();
    let target = {
        let mut held = workspace
            .0
            .lock()
            .map_err(|_| publish_workspace_unavailable())?;
        held.begin(&platform, publish_job_id.clone())
            .map_err(map_publish_workspace_error)?
    };
    // Route before touching anything. Whether this platform is *allowed* and
    // how it is *reached* are two questions, and answering only the first is
    // how a B站 publish would have been typed into 抖音's browser.
    match target.route() {
        publish_workspace::PublishRoute::OperationsBrowser => {}
        publish_workspace::PublishRoute::OfficialApi => {}
    }
    // The publishable set is "a finished video this App produced", resolved by
    // the store from an identity. The page never holds a local path, and the
    // executor is handed a copy named the one way it will accept.
    let staged = match workspaces.stage_publishable_artifact(artifact_id) {
        Ok(staged) => staged,
        Err(error) => {
            let mut held = workspace
                .0
                .lock()
                .map_err(|_| publish_workspace_unavailable())?;
            held.settle(publish_workspace::PublishOutcome::NotPublished);
            return Err(map_video_workspace_error(error));
        }
    };
    if target.route() == publish_workspace::PublishRoute::OfficialApi {
        return begin_bilibili_publish(
            &app,
            publish_job_id,
            video_summary,
            title,
            description,
            &client,
            &vault,
            &settings,
            &bilibili_session,
            &workspaces,
            &workspace,
            staged,
        )
        .await;
    }
    let prepared = async {
        ensure_executor_running(&client, &vault, &executor).await?;
        let executable_path = resolve_embedded_browser(&authority)?;
        let profile =
            profiles
                .current_douyin_profile()
                .map_err(|_| ExecutorPlatformCommandError {
                    code: "storage_unavailable",
                    retryable: false,
                })?;
        Ok::<_, ExecutorPlatformCommandError>((executable_path, profile))
    }
    .await;
    let (executable_path, profile) = match prepared {
        Ok(prepared) => prepared,
        Err(error) => {
            // The copy of the video outlives nothing: a publish that never got
            // as far as the browser must not leave one on disk.
            let _ = workspaces.discard_staged_publish_artifacts();
            let mut held = workspace
                .0
                .lock()
                .map_err(|_| publish_workspace_unavailable())?;
            held.settle(publish_workspace::PublishOutcome::NotPublished);
            return Err(error);
        }
    };
    let artifact_path = staged.path().to_path_buf();
    let service = executor.inner().clone();
    // The same copy the operator will be asked to approve, kept here because the
    // command below consumes one moving into the blocking call.
    let approved_title = title.clone();
    let approved_description = description.clone();
    let outcome = tauri::async_runtime::spawn_blocking(move || {
        service.execute_publish_command(
            publish_job_id,
            executable_path,
            profile,
            cfg!(feature = "control-plane-e2e"),
            artifact_path,
            title,
            description,
        )
    })
    .await
    .map_err(|_| ExecutorPlatformCommandError {
        code: "process_unavailable",
        retryable: true,
    })?;

    let mut held = workspace
        .0
        .lock()
        .map_err(|_| publish_workspace_unavailable())?;
    let result = match outcome {
        Err(error) => {
            // Nothing ever reached a page the operator could have approved.
            held.settle(publish_workspace::PublishOutcome::NotPublished);
            let _ = workspaces.discard_staged_publish_artifacts();
            return Err(map_executor_platform_error(error));
        }
        Ok(result) => result,
    };
    match publish_workspace::preflight_outcome(result.state()) {
        Some(outcome) => {
            held.settle(outcome);
            // Settled before approval: the browser is not holding this file
            // open for anyone, so the copy goes.
            let _ = workspaces.discard_staged_publish_artifacts();
        }
        None => {
            // The executor signed these terms; they are not the App's to invent.
            let (Some(confirmation_id), Some(target_account)) =
                (result.confirmation_id(), result.target_account())
            else {
                held.settle(publish_workspace::PublishOutcome::NotPublished);
                let _ = workspaces.discard_staged_publish_artifacts();
                return Err(ExecutorPlatformCommandError {
                    code: "publish_not_confirmable",
                    retryable: false,
                });
            };
            let approval = match publish_workspace::PublishApproval::new(
                target_account,
                &video_summary,
                &approved_title,
                &approved_description,
                confirmation_id,
            ) {
                Ok(approval) => approval,
                Err(error) => {
                    held.settle(publish_workspace::PublishOutcome::NotPublished);
                    let _ = workspaces.discard_staged_publish_artifacts();
                    return Err(map_publish_workspace_error(error));
                }
            };
            // Held on purpose past this point: the page the operator is about
            // to approve already has this file in it, and it stays until the
            // publish settles one way or the other.
            held.await_approval(approval);
        }
    }
    Ok(held.snapshot())
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[allow(clippy::too_many_arguments)]
async fn begin_bilibili_publish(
    app: &tauri::AppHandle,
    publish_job_id: String,
    video_summary: String,
    title: String,
    description: String,
    client: &control_plane::ControlPlaneClient,
    vault: &ProductionDeviceCredentialVault,
    settings: &bilibili_service_settings::ProductionBilibiliServiceSettings,
    bilibili_session: &BilibiliPublishSessionState,
    workspaces: &video_job_workspace::VideoJobWorkspaceStore,
    workspace: &PublishWorkspaceState,
    staged: video_job_workspace::StagedPublishArtifact,
) -> Result<publish_workspace::PublishWorkspaceSnapshot, ExecutorPlatformCommandError> {
    let prepared = async {
        bilibili_session.clear()?;
        let credential = settings
            .credential_for_publish()
            .map_err(map_bilibili_service_error)?;
        let target_account = credential.target_account().to_owned();
        let resource_directory =
            app.path()
                .resource_dir()
                .map_err(|_| ExecutorPlatformCommandError {
                    code: "publish_video_not_publishable",
                    retryable: false,
                })?;
        let video_path = staged.path().to_path_buf();
        let duration_seconds = tauri::async_runtime::spawn_blocking(move || {
            video_media_toolchain::VideoMediaToolchain::load(&resource_directory)
                .and_then(|toolchain| toolchain.probe_duration_seconds(&video_path))
        })
        .await
        .map_err(|_| ExecutorPlatformCommandError {
            code: "process_unavailable",
            retryable: true,
        })?
        .map_err(|_| ExecutorPlatformCommandError {
            code: "publish_video_not_publishable",
            retryable: false,
        })?;
        let prepared = client
            .prepare_bilibili_publish(
                vault,
                &publish_job_id,
                staged.size_bytes(),
                duration_seconds,
                staged.sha256(),
                &title,
                &description,
                &credential,
            )
            .await
            .map_err(map_publish_control_plane_error)?;
        if let Some(rotation) = prepared.credential_rotation() {
            settings
                .apply_rotation(rotation)
                .map_err(map_bilibili_service_error)?;
        }
        let publish_session =
            prepared
                .into_session_token()
                .ok_or(ExecutorPlatformCommandError {
                    code: "operation_unavailable",
                    retryable: false,
                })?;
        let uploaded = client
            .upload_bilibili_publish_video(
                vault,
                &publish_job_id,
                publish_session.as_str(),
                staged.path(),
                staged.size_bytes(),
            )
            .await
            .map_err(map_publish_control_plane_error)?;
        if let Some(rotation) = uploaded.credential_rotation() {
            settings
                .apply_rotation(rotation)
                .map_err(map_bilibili_service_error)?;
        }
        let confirmation_id = video_job_workspace::generate_uuid_v4()
            .map_err(map_video_workspace_error)?
            .hyphenated()
            .to_string();
        let approval = publish_workspace::PublishApproval::new(
            &target_account,
            &video_summary,
            &title,
            &description,
            &confirmation_id,
        )
        .map_err(map_publish_workspace_error)?;
        bilibili_session.replace(publish_job_id, publish_session)?;
        Ok::<_, ExecutorPlatformCommandError>(approval)
    }
    .await;

    let approval = match prepared {
        Ok(approval) => approval,
        Err(error) => {
            let _ = bilibili_session.clear();
            let _ = workspaces.discard_staged_publish_artifacts();
            let mut held = workspace
                .0
                .lock()
                .map_err(|_| publish_workspace_unavailable())?;
            held.settle(publish_workspace::PublishOutcome::NotPublished);
            return Err(error);
        }
    };
    let mut held = workspace
        .0
        .lock()
        .map_err(|_| publish_workspace_unavailable())?;
    held.await_approval(approval);
    Ok(held.snapshot())
}

/// Spend the operator's approval on the one click it authorizes.
///
/// The confirmation the App sends back must be the one the executor issued.
/// Checking it here as well as in the executor is deliberate: this is the last
/// place that still knows what was rendered, and an approval spent on terms
/// nobody saw is the failure this whole chain exists to prevent.
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
#[allow(clippy::too_many_arguments)]
async fn approve_publish(
    confirmation_id: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    executor: tauri::State<'_, executor_platform::ExecutorPlatformService>,
    settings: tauri::State<'_, bilibili_service_settings::ProductionBilibiliServiceSettings>,
    bilibili_session: tauri::State<'_, BilibiliPublishSessionState>,
    workspaces: tauri::State<'_, video_job_workspace::VideoJobWorkspaceStore>,
    workspace: tauri::State<'_, PublishWorkspaceState>,
) -> Result<publish_workspace::PublishWorkspaceSnapshot, ExecutorPlatformCommandError> {
    let (publish_job_id, target, pending_bilibili) = {
        let mut held = workspace
            .0
            .lock()
            .map_err(|_| publish_workspace_unavailable())?;
        let pending = held.snapshot();
        let terms = pending
            .approval
            .as_ref()
            .ok_or(publish_workspace::PublishWorkspaceError::NoApprovalPending)
            .map_err(map_publish_workspace_error)?;
        if terms.confirmation_id != confirmation_id {
            return Err(map_publish_workspace_error(
                publish_workspace::PublishWorkspaceError::NoApprovalPending,
            ));
        }
        // The job this approval belongs to is the one the bridge minted when the
        // publish began, not the confirmation identity wearing a second hat.
        let publish_job_id = held
            .job_id()
            .ok_or(publish_workspace::PublishWorkspaceError::NoApprovalPending)
            .map_err(map_publish_workspace_error)?
            .to_owned();
        let target = pending
            .target
            .ok_or(publish_workspace::PublishWorkspaceError::NoApprovalPending)
            .map_err(map_publish_workspace_error)?;
        let pending_bilibili = if target == publish_workspace::PublishPlatform::Bilibili {
            Some(bilibili_session.take(&publish_job_id)?)
        } else {
            None
        };
        held.approve().map_err(map_publish_workspace_error)?;
        (publish_job_id, target, pending_bilibili)
    };
    if target == publish_workspace::PublishPlatform::Bilibili {
        let pending = pending_bilibili.ok_or(ExecutorPlatformCommandError {
            code: "publish_nothing_to_confirm",
            retryable: false,
        })?;
        let outcome = client
            .submit_bilibili_publish(&vault, &publish_job_id, pending.session_token.as_str())
            .await;
        let _ = workspaces.discard_staged_publish_artifacts();
        let mut held = workspace
            .0
            .lock()
            .map_err(|_| publish_workspace_unavailable())?;
        held.begin_verification();
        return match outcome {
            Err(error) => {
                held.settle(publish_workspace::PublishOutcome::OutcomeUncertain);
                drop(held);
                Err(map_publish_control_plane_error(error))
            }
            Ok(result) => {
                if let Some(rotation) = result.credential_rotation() {
                    if let Err(error) = settings.apply_rotation(rotation) {
                        held.settle(publish_workspace::PublishOutcome::OutcomeUncertain);
                        return Err(map_bilibili_service_error(error));
                    }
                }
                let publish_outcome = match result.phase() {
                    control_plane::BilibiliPublishPhase::Submitted => {
                        publish_workspace::PublishOutcome::Submitted
                    }
                    control_plane::BilibiliPublishPhase::Failed => {
                        publish_workspace::PublishOutcome::NotPublished
                    }
                    control_plane::BilibiliPublishPhase::Dispatched
                    | control_plane::BilibiliPublishPhase::OutcomeUncertain => {
                        publish_workspace::PublishOutcome::OutcomeUncertain
                    }
                    control_plane::BilibiliPublishPhase::Prepared
                    | control_plane::BilibiliPublishPhase::VideoUploaded => {
                        publish_workspace::PublishOutcome::OutcomeUncertain
                    }
                };
                held.settle(publish_outcome);
                Ok(held.snapshot())
            }
        };
    }
    let service = executor.inner().clone();
    let job = publish_job_id.clone();
    let outcome = tauri::async_runtime::spawn_blocking(move || {
        service.execute_publish_dispatch_command(job, confirmation_id)
    })
    .await
    .map_err(|_| ExecutorPlatformCommandError {
        code: "process_unavailable",
        retryable: true,
    })?;

    let mut held = workspace
        .0
        .lock()
        .map_err(|_| publish_workspace_unavailable())?;
    held.begin_verification();
    // The click has been spent either way, so the copy handed to the browser is
    // finished with — including when the outcome is unknown.
    let _ = workspaces.discard_staged_publish_artifacts();
    match outcome {
        // The click may already have happened, so a transport failure is not a
        // clean "did not publish"; it is exactly what uncertain means.
        Err(error) => {
            held.settle(publish_workspace::PublishOutcome::OutcomeUncertain);
            drop(held);
            Err(map_executor_platform_error(error))
        }
        Ok(result) => {
            held.settle(publish_workspace::dispatch_outcome(result.state()));
            Ok(held.snapshot())
        }
    }
}

/// Give up a publish that has not been dispatched, and hand the browser back.
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn cancel_publish(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    executor: tauri::State<'_, executor_platform::ExecutorPlatformService>,
    bilibili_session: tauri::State<'_, BilibiliPublishSessionState>,
    workspaces: tauri::State<'_, video_job_workspace::VideoJobWorkspaceStore>,
    workspace: tauri::State<'_, PublishWorkspaceState>,
) -> Result<publish_workspace::PublishWorkspaceSnapshot, ExecutorPlatformCommandError> {
    let (target, pending_bilibili) = {
        // Refuse before touching the browser: a dispatched publish has nothing
        // local left to cancel, and saying otherwise would be a lie.
        let mut held = workspace
            .0
            .lock()
            .map_err(|_| publish_workspace_unavailable())?;
        let target = held
            .snapshot()
            .target
            .ok_or(publish_workspace::PublishWorkspaceError::NothingInFlight)
            .map_err(map_publish_workspace_error)?;
        let pending_bilibili = if target == publish_workspace::PublishPlatform::Bilibili {
            let job_id = held
                .job_id()
                .ok_or(publish_workspace::PublishWorkspaceError::NothingInFlight)
                .map_err(map_publish_workspace_error)?;
            Some(bilibili_session.take(job_id)?)
        } else {
            None
        };
        held.cancel().map_err(map_publish_workspace_error)?;
        (target, pending_bilibili)
    };
    // The cancel was accepted, so nothing will be published from this copy.
    let _ = workspaces.discard_staged_publish_artifacts();
    if target == publish_workspace::PublishPlatform::Bilibili {
        if let Some(pending) = pending_bilibili {
            let _ = client
                .cancel_bilibili_publish_session(
                    &vault,
                    &pending.job_id,
                    pending.session_token.as_str(),
                )
                .await;
        }
        let held = workspace
            .0
            .lock()
            .map_err(|_| publish_workspace_unavailable())?;
        return Ok(held.snapshot());
    }
    let service = executor.inner().clone();
    tauri::async_runtime::spawn_blocking(move || service.release_publish_surface())
        .await
        .map_err(|_| ExecutorPlatformCommandError {
            code: "process_unavailable",
            retryable: true,
        })?
        .map_err(map_executor_platform_error)?;
    let held = workspace
        .0
        .lock()
        .map_err(|_| publish_workspace_unavailable())?;
    Ok(held.snapshot())
}

#[tauri::command]
#[cfg(all(feature = "desktop-e2e", not(feature = "control-plane-e2e")))]
async fn restart_executor(
) -> Result<executor_manager::ExecutorManagerStatus, ExecutorPlatformCommandError> {
    Err(ExecutorPlatformCommandError {
        code: "operation_unavailable",
        retryable: false,
    })
}

/// The UI-only desktop build has an ephemeral identity and no production
/// credential vault, so it cannot run the installation-access check. It still
/// contacts the real Control Plane: a build that reported a service it never
/// called would hide an unreachable backend from every acceptance run.
#[tauri::command]
#[cfg(feature = "desktop-e2e")]
async fn check_control_plane_health(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
) -> Result<control_plane::ControlPlaneHealth, ControlPlaneCommandError> {
    app_logging::record(app_logging::DesktopLogEvent::ControlPlaneHealthCheckStarted);
    match client.check_health().await.map_err(map_control_plane_error) {
        Ok(health) => {
            app_logging::record(app_logging::DesktopLogEvent::ControlPlaneServiceHealthCompleted);
            app_logging::record(app_logging::DesktopLogEvent::ControlPlaneHealthCheckCompleted);
            Ok(health)
        }
        Err(error) => {
            app_logging::record(app_logging::DesktopLogEvent::ControlPlaneHealthCheckRejected);
            Err(error)
        }
    }
}

#[tauri::command]
#[cfg(not(feature = "desktop-e2e"))]
async fn check_control_plane_health(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    handoff: tauri::State<'_, ProductionLocalRegistrationHandoffStore>,
) -> Result<control_plane::ControlPlaneHealth, ControlPlaneCommandError> {
    app_logging::record(app_logging::DesktopLogEvent::ControlPlaneHealthCheckStarted);
    let health = match client.check_health().await.map_err(map_control_plane_error) {
        Ok(health) => {
            app_logging::record(app_logging::DesktopLogEvent::ControlPlaneServiceHealthCompleted);
            health
        }
        Err(error) => {
            app_logging::record(app_logging::DesktopLogEvent::ControlPlaneHealthCheckRejected);
            return Err(error);
        }
    };
    if let Err(error) =
        register_installation_from_local_handoff(&client, &identity, &vault, &handoff).await
    {
        app_logging::record(app_logging::DesktopLogEvent::ControlPlaneHealthCheckRejected);
        return Err(error);
    }
    app_logging::record(app_logging::DesktopLogEvent::ControlPlaneRegistrationCompleted);
    if let Err(error) = client
        .check_installation_access_if_registered(&vault)
        .await
        .map_err(map_control_plane_error)
    {
        app_logging::record(app_logging::DesktopLogEvent::ControlPlaneHealthCheckRejected);
        return Err(error);
    }
    app_logging::record(app_logging::DesktopLogEvent::ControlPlaneInstallationAccessCompleted);
    app_logging::record(app_logging::DesktopLogEvent::ControlPlaneHealthCheckCompleted);
    Ok(health)
}

/// The one production path from "no device credential" to a registered
/// Installation.
///
/// A machine that runs its own Control Plane leaves a short-lived grant in the
/// App private directory; this is where the App spends it. Every build compiles
/// the same code, and the grant is completed through the ordinary
/// challenge/device-proof registration, so no deployment has a second way to
/// obtain a credential.
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
struct ProductionInstallationRegistrar<'a> {
    client: &'a control_plane::ControlPlaneClient,
    identity: &'a ProductionDeviceIdentity,
    vault: &'a ProductionDeviceCredentialVault,
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
impl local_registration::InstallationRegistrar for ProductionInstallationRegistrar<'_> {
    fn has_credential(&self) -> Result<bool, control_plane::ControlPlaneErrorCode> {
        self.vault
            .load()
            .map(|stored| stored.is_some())
            .map_err(|_| control_plane::ControlPlaneErrorCode::StorageUnavailable)
    }

    async fn register(
        &self,
        bootstrap: &control_plane::DemoBootstrap,
    ) -> Result<(), control_plane::ControlPlaneErrorCode> {
        self.client
            .register_installation(bootstrap, self.identity, self.vault)
            .await
            .map(|_| ())
            .map_err(|error| error.code())
    }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
async fn register_installation_from_local_handoff(
    client: &control_plane::ControlPlaneClient,
    identity: &ProductionDeviceIdentity,
    vault: &ProductionDeviceCredentialVault,
    handoff: &ProductionLocalRegistrationHandoffStore,
) -> Result<(), ControlPlaneCommandError> {
    // 设备身份注册机制已删除：服务端固定使用本地 Installation，
    // 客户端不再注册设备，也不可能出现安装记录冲突。
    let _ = (client, identity, vault, handoff);
    Ok(())
}

fn map_control_plane_error(error: control_plane::ControlPlaneError) -> ControlPlaneCommandError {
    let code = match error.code() {
        control_plane::ControlPlaneErrorCode::TransportUnavailable => "transport_unavailable",
        control_plane::ControlPlaneErrorCode::CredentialMissing => "credential_missing",
        control_plane::ControlPlaneErrorCode::IdentityUnavailable => "identity_unavailable",
        control_plane::ControlPlaneErrorCode::StorageUnavailable => "storage_unavailable",
        control_plane::ControlPlaneErrorCode::OutcomeUncertain => "outcome_uncertain",
        control_plane::ControlPlaneErrorCode::InstallationAccessDenied => {
            "installation_access_denied"
        }
        control_plane::ControlPlaneErrorCode::InstallationBusy => "installation_busy",
        control_plane::ControlPlaneErrorCode::InstallationConflict => "installation_conflict",
        control_plane::ControlPlaneErrorCode::AuthenticationInvalid => "authentication_invalid",
        control_plane::ControlPlaneErrorCode::RecoveryInvalid => "recovery_invalid",
        control_plane::ControlPlaneErrorCode::AccountSessionInvalid => "session_invalid",
        control_plane::ControlPlaneErrorCode::ProtocolInvalid
        | control_plane::ControlPlaneErrorCode::RequestRejected
        | control_plane::ControlPlaneErrorCode::ResourceNotFound => "operation_unavailable",
    };
    ControlPlaneCommandError {
        code,
        retryable: error.retryable(),
    }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn map_task_emergency_stop_platform_error(
    error: executor_platform::ExecutorPlatformError,
) -> ControlPlaneCommandError {
    let mapped = map_executor_platform_error(error);
    ControlPlaneCommandError {
        code: mapped.code,
        retryable: mapped.retryable,
    }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
async fn reconcile_pending_task_emergency_stop(
    client: &control_plane::ControlPlaneClient,
    vault: &ProductionDeviceCredentialVault,
    platform: &executor_platform::ExecutorPlatformService,
) -> Result<Option<control_plane::TaskControlCommand>, ControlPlaneCommandError> {
    let Some(reconciliation) = platform
        .begin_task_emergency_stop_reconciliation()
        .map_err(map_task_emergency_stop_platform_error)?
    else {
        return Ok(None);
    };
    let pending = reconciliation.pending();

    let service = platform.clone();
    let task_id = pending.task_id().to_owned();
    let idempotency_key = pending.idempotency_key().to_owned();
    tauri::async_runtime::spawn_blocking(move || {
        service.engage_task_emergency_stop(&task_id, &idempotency_key)
    })
    .await
    .map_err(|_| ControlPlaneCommandError {
        code: "process_unavailable",
        retryable: true,
    })?
    .map_err(map_task_emergency_stop_platform_error)?;

    let command = client
        .emergency_stop_task(vault, pending.task_id(), pending.idempotency_key())
        .await
        .map_err(map_control_plane_error)?;
    let connection = client
        .issue_executor_connection(vault)
        .await
        .map_err(map_control_plane_error)?;
    let service = platform.clone();
    let expected = pending.clone();
    tauri::async_runtime::spawn_blocking(move || {
        service.restart_for_task_emergency_stop(connection, &expected)
    })
    .await
    .map_err(|_| ControlPlaneCommandError {
        code: "process_unavailable",
        retryable: true,
    })?
    .map_err(map_task_emergency_stop_platform_error)?;
    Ok(Some(command))
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn map_task_target_preview_error(
    error: control_plane::ControlPlaneError,
) -> ControlPlaneCommandError {
    if error.code() == control_plane::ControlPlaneErrorCode::RequestRejected {
        return ControlPlaneCommandError {
            code: "request_rejected",
            retryable: error.retryable(),
        };
    }
    map_control_plane_error(error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskProjectionStreamSummary {
    last_sequence: u64,
    terminal: bool,
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn get_workbench_status(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<control_plane::WorkbenchRuntimeStatus, ControlPlaneCommandError> {
    reconcile_pending_task_emergency_stop(&client, &vault, &platform).await?;
    client
        .get_workbench_status(&vault)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn get_workbench_metrics(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::WorkbenchMetrics, ControlPlaneCommandError> {
    client
        .get_workbench_metrics(&vault)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn list_editing_projects(
    cursor: Option<String>,
    limit: u16,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::EditingProjectListPage, ControlPlaneCommandError> {
    client
        .list_editing_projects(&vault, cursor.as_deref(), limit)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn list_editing_materials(
    cursor: Option<String>,
    limit: u16,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::EditingMaterialListPage, ControlPlaneCommandError> {
    client
        .list_editing_materials(&vault, cursor.as_deref(), limit)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn update_editing_material_description(
    material_id: String,
    description: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::EditingMaterialSnapshot, ControlPlaneCommandError> {
    client
        .update_editing_material_description(&vault, &material_id, &description)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn create_editing_project(
    request: control_plane::EditingProjectCreateRequest,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::EditingProjectSnapshot, ControlPlaneCommandError> {
    client
        .create_editing_project(&vault, &request)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn get_editing_project_timeline(
    project_id: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<Option<control_plane::EditingTimelineSnapshot>, ControlPlaneCommandError> {
    client
        .get_editing_project_timeline(&vault, &project_id)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn save_editing_project_timeline(
    project_id: String,
    draft: control_plane::EditingTimelineDraft,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::EditingTimelineSnapshot, ControlPlaneCommandError> {
    client
        .save_editing_project_timeline(&vault, &project_id, &draft)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn list_editing_jobs(
    project_id: String,
    cursor: Option<String>,
    limit: u16,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::EditingJobListPage, ControlPlaneCommandError> {
    client
        .list_editing_jobs(&vault, &project_id, cursor.as_deref(), limit)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn submit_editing_job(
    project_id: String,
    app: tauri::AppHandle,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::EditingJobSnapshot, ControlPlaneCommandError> {
    let submitted = client
        .submit_editing_job(&vault, &project_id)
        .await
        .map_err(map_control_plane_error)?;
    if local_editing_runtime::dispatch_submitted_job(&app, &submitted)
        .await
        .is_err()
    {
        let settled = local_editing_runtime::fail_submitted_job(&app, &submitted)
            .await
            .is_ok();
        return Err(ControlPlaneCommandError {
            code: if settled {
                "operation_unavailable"
            } else {
                "outcome_uncertain"
            },
            retryable: false,
        });
    }
    Ok(submitted)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn map_smart_edit_error(error: smart_edit_runtime::SmartEditRuntimeError) -> SmartEditCommandError {
    use smart_edit_runtime::SmartEditRuntimeErrorCode;

    SmartEditCommandError {
        code: match error.code() {
            SmartEditRuntimeErrorCode::InvalidRequest => "invalid_request",
            SmartEditRuntimeErrorCode::GenerationNotFound => "generation_not_found",
            SmartEditRuntimeErrorCode::GenerationNotCancellable => "generation_not_cancellable",
            SmartEditRuntimeErrorCode::StorageUnavailable => "storage_unavailable",
        },
        retryable: false,
    }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
fn start_smart_edit_generation(
    request: smart_edit_runtime::SmartEditGenerationRequest,
    app: tauri::AppHandle,
    runtime: tauri::State<'_, smart_edit_runtime::SmartEditRuntime>,
) -> Result<smart_edit_runtime::SmartEditGenerationSnapshot, SmartEditCommandError> {
    runtime.start(&app, request).map_err(map_smart_edit_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
fn get_smart_edit_generation(
    generation_id: String,
    runtime: tauri::State<'_, smart_edit_runtime::SmartEditRuntime>,
) -> Result<smart_edit_runtime::SmartEditGenerationSnapshot, SmartEditCommandError> {
    runtime
        .snapshot(&generation_id)
        .map_err(map_smart_edit_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
fn cancel_smart_edit_generation(
    generation_id: String,
    runtime: tauri::State<'_, smart_edit_runtime::SmartEditRuntime>,
) -> Result<smart_edit_runtime::SmartEditGenerationSnapshot, SmartEditCommandError> {
    runtime.cancel(&generation_id).map_err(map_smart_edit_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn map_local_material_error(
    error: local_material_library::LocalMaterialLibraryError,
) -> LocalMaterialCommandError {
    use control_plane::ControlPlaneErrorCode as ControlPlane;
    use local_material_library::LocalMaterialLibraryErrorCode as Library;
    use local_video_orchestrator::VideoWorkerErrorCode as Worker;

    let code = match error.code() {
        Library::ConfigurationInvalid => "configuration_invalid",
        Library::SourceChanged => "source_changed",
        Library::CompensationFailed => "compensation_failed",
        Library::RemapUncertain => "outcome_uncertain",
        Library::WorkerRejected(code) => code.as_str(),
        Library::WorkerLifecycle(Worker::AuthenticationRejected) => "authentication_rejected",
        Library::WorkerLifecycle(Worker::ConfigurationInvalid) => "operation_in_progress",
        Library::WorkerLifecycle(Worker::TimedOut) => "timed_out",
        Library::WorkerLifecycle(Worker::VersionMismatch) => "version_mismatch",
        Library::WorkerLifecycle(
            Worker::AlreadyRunning
            | Worker::NotRunning
            | Worker::ProcessUnavailable
            | Worker::RenderRejected,
        ) => "worker_unavailable",
        Library::ControlPlane(ControlPlane::CredentialMissing) => "credential_missing",
        Library::ControlPlane(ControlPlane::InstallationAccessDenied) => {
            "installation_access_denied"
        }
        Library::ControlPlane(ControlPlane::OutcomeUncertain) => "outcome_uncertain",
        Library::ControlPlane(
            ControlPlane::IdentityUnavailable | ControlPlane::StorageUnavailable,
        ) => "storage_unavailable",
        Library::ControlPlane(ControlPlane::TransportUnavailable) => "transport_unavailable",
        Library::ControlPlane(
            ControlPlane::ProtocolInvalid
            | ControlPlane::RequestRejected
            | ControlPlane::InstallationBusy
            | ControlPlane::InstallationConflict
            | ControlPlane::AuthenticationInvalid
            | ControlPlane::RecoveryInvalid
            | ControlPlane::AccountSessionInvalid
            | ControlPlane::ResourceNotFound,
        ) => "control_plane_rejected",
    };
    LocalMaterialCommandError {
        code,
        retryable: error.retryable(),
    }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn parse_material_id(value: &str) -> Result<uuid::Uuid, LocalMaterialCommandError> {
    let parsed = uuid::Uuid::parse_str(value).map_err(|_| LocalMaterialCommandError {
        code: "configuration_invalid",
        retryable: false,
    })?;
    if parsed.get_version_num() != 4
        || parsed.get_variant() != uuid::Variant::RFC4122
        || parsed.hyphenated().to_string() != value
    {
        return Err(LocalMaterialCommandError {
            code: "configuration_invalid",
            retryable: false,
        });
    }
    Ok(parsed)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn pick_editing_material(app: &tauri::AppHandle) -> Option<FilePath> {
    #[cfg(feature = "control-plane-e2e")]
    if std::env::var("AUTOMATION_TOOL_LE18_ACCEPTANCE_PICKER").as_deref() == Ok("1") {
        use std::sync::atomic::Ordering;

        let index = MATERIAL_ACCEPTANCE_PICK_INDEX.fetch_add(1, Ordering::SeqCst);
        return std::env::var(format!("AUTOMATION_TOOL_LE18_PICK_{index}"))
            .ok()
            .map(std::path::PathBuf::from)
            .map(FilePath::Path);
    }
    app.dialog().file().blocking_pick_file()
}

#[cfg(feature = "control-plane-e2e")]
fn remember_material_acceptance_source(
    outcome: &local_material_library::LocalMaterialImportOutcome,
    source_path: &std::path::Path,
) -> Result<(), LocalMaterialCommandError> {
    if std::env::var("AUTOMATION_TOOL_LE18_ACCEPTANCE_PICKER").as_deref() != Ok("1") {
        return Ok(());
    }
    let material_id = parse_material_id(outcome.material().material_id())?;
    let sources = MATERIAL_ACCEPTANCE_SOURCES
        .get_or_init(|| std::sync::Mutex::new(std::collections::HashMap::new()));
    sources
        .lock()
        .map_err(|_| LocalMaterialCommandError {
            code: "worker_unavailable",
            retryable: true,
        })?
        .insert(material_id, source_path.to_path_buf());
    Ok(())
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn import_editing_material(
    app: tauri::AppHandle,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    orchestrator: tauri::State<'_, local_video_orchestrator::LocalVideoOrchestrator>,
    coordinator: tauri::State<'_, local_material_library::LocalMaterialLibraryCoordinator>,
) -> Result<Option<local_material_library::LocalMaterialImportOutcome>, LocalMaterialCommandError> {
    let selected = pick_editing_material(&app);
    let Some(selected) = selected else {
        return Ok(None);
    };
    let FilePath::Path(source_path) = selected else {
        return Err(LocalMaterialCommandError {
            code: "configuration_invalid",
            retryable: false,
        });
    };
    let _operation = coordinator.acquire().await;
    local_editing_runtime::ensure_worker(&app).map_err(|_| LocalMaterialCommandError {
        code: "worker_unavailable",
        retryable: true,
    })?;
    let outcome =
        local_material_library::import_material(&orchestrator, &client, &vault, &source_path)
            .await
            .map_err(map_local_material_error)?;
    #[cfg(feature = "control-plane-e2e")]
    remember_material_acceptance_source(&outcome, &source_path)?;
    Ok(Some(outcome))
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn get_local_editing_material_status(
    material_id: String,
    app: tauri::AppHandle,
    orchestrator: tauri::State<'_, local_video_orchestrator::LocalVideoOrchestrator>,
    coordinator: tauri::State<'_, local_material_library::LocalMaterialLibraryCoordinator>,
) -> Result<local_video_orchestrator::VideoWorkerLocalMaterialStatus, LocalMaterialCommandError> {
    let material_id = parse_material_id(&material_id)?;
    let _operation = coordinator.acquire().await;
    local_editing_runtime::ensure_worker(&app).map_err(|_| LocalMaterialCommandError {
        code: "worker_unavailable",
        retryable: true,
    })?;
    local_material_library::material_status(&orchestrator, material_id)
        .map_err(map_local_material_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn get_local_editing_material_preview_url(
    material_id: String,
    app: tauri::AppHandle,
    orchestrator: tauri::State<'_, local_video_orchestrator::LocalVideoOrchestrator>,
    coordinator: tauri::State<'_, local_material_library::LocalMaterialLibraryCoordinator>,
) -> Result<String, LocalMaterialCommandError> {
    let material_id = parse_material_id(&material_id)?;
    let _operation = coordinator.acquire().await;
    local_editing_runtime::ensure_worker(&app).map_err(|_| LocalMaterialCommandError {
        code: "worker_unavailable",
        retryable: true,
    })?;
    local_material_library::material_preview_url(&orchestrator, material_id)
        .map_err(map_local_material_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn delete_editing_material(
    material_id: String,
    app: tauri::AppHandle,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    orchestrator: tauri::State<'_, local_video_orchestrator::LocalVideoOrchestrator>,
    coordinator: tauri::State<'_, local_material_library::LocalMaterialLibraryCoordinator>,
) -> Result<(), LocalMaterialCommandError> {
    let material_id = parse_material_id(&material_id)?;
    let _operation = coordinator.acquire().await;
    local_editing_runtime::ensure_worker(&app).map_err(|_| LocalMaterialCommandError {
        code: "worker_unavailable",
        retryable: true,
    })?;
    local_material_library::delete_material(&orchestrator, &client, &vault, material_id)
        .await
        .map_err(map_local_material_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn get_douyin_platform_session(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::PlatformSessionStatus, ControlPlaneCommandError> {
    client
        .get_douyin_platform_session(&vault)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn create_douyin_search_exposure_task(
    definition: control_plane::DouyinSearchExposureTaskDefinition,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskSnapshot, ControlPlaneCommandError> {
    client
        .create_task(&vault, &idempotency_key, &definition)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn start_task_discovery(
    task_id: String,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<control_plane::TaskDiscoveryCommand, ControlPlaneCommandError> {
    ensure_executor_running(&client, &vault, &platform)
        .await
        .map_err(|error| ControlPlaneCommandError {
            code: error.code,
            retryable: error.retryable,
        })?;
    client
        .start_task_discovery(&vault, &task_id, &idempotency_key)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn get_task_target_preview(
    task_id: String,
    cursor: Option<String>,
    limit: u16,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskTargetPreview, ControlPlaneCommandError> {
    client
        .get_task_target_preview(&vault, &task_id, cursor.as_deref(), limit)
        .await
        .map_err(map_task_target_preview_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn replace_task_target_exclusions(
    task_id: String,
    page_revision: u64,
    expected_task_revision: u64,
    excluded_target_ids: Vec<String>,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskTargetPreview, ControlPlaneCommandError> {
    client
        .replace_task_target_exclusions(
            &vault,
            &task_id,
            page_revision,
            expected_task_revision,
            &excluded_target_ids,
            &idempotency_key,
        )
        .await
        .map_err(map_task_target_preview_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn confirm_task_target_preview(
    task_id: String,
    page_revision: u64,
    confirmation_revision: u64,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskTargetPreview, ControlPlaneCommandError> {
    client
        .confirm_task_target_preview(
            &vault,
            &task_id,
            page_revision,
            confirmation_revision,
            &idempotency_key,
        )
        .await
        .map_err(map_task_target_preview_error)
}

#[cfg(feature = "control-plane-e2e")]
fn acceptance_task_definition() -> control_plane::DouyinSearchExposureTaskDefinition {
    control_plane::DouyinSearchExposureTaskDefinition::new(
        "新能源汽车".to_owned(),
        control_plane::DouyinSearchExposureAction::Comment,
        Some("您好 {{target_display_name}} 期待您的分享".to_owned()),
        10,
        30,
        90,
    )
    .expect("acceptance Task definition must remain valid")
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct PreparedTargetPreviewAcceptance {
    installation_id: String,
    task_id: String,
}

#[cfg(feature = "control-plane-e2e")]
async fn prepare_target_preview_acceptance(
    client: &control_plane::ControlPlaneClient,
    identity: &ProductionDeviceIdentity,
    vault: &ProductionDeviceCredentialVault,
    token_environment_variable: &str,
    environment_id_variable: &str,
    task_idempotency_key: &str,
    discovery_idempotency_key: &str,
) -> Result<PreparedTargetPreviewAcceptance, ControlPlaneCommandError> {
    let token =
        std::env::var(token_environment_variable).map_err(|_| ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        })?;
    let environment_id =
        std::env::var(environment_id_variable).map_err(|_| ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, identity, vault)
        .await
        .map_err(map_control_plane_error)?;
    let task = client
        .create_task(vault, task_idempotency_key, &acceptance_task_definition())
        .await
        .map_err(map_control_plane_error)?;

    let mut platform_ready = false;
    for _ in 0..120 {
        let platform = client
            .get_douyin_platform_session(vault)
            .await
            .map_err(map_control_plane_error)?;
        if platform.state() == "healthy" {
            platform_ready = true;
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    if !platform_ready {
        return Err(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: true,
        });
    }
    client
        .start_task_discovery(vault, task.task_id(), discovery_idempotency_key)
        .await
        .map_err(map_control_plane_error)?;
    for _ in 0..240 {
        let snapshot = client
            .get_task(vault, task.task_id())
            .await
            .map_err(map_control_plane_error)?;
        if snapshot.status() == "awaiting_confirmation" {
            return Ok(PreparedTargetPreviewAcceptance {
                installation_id: registration.installation_id().to_owned(),
                task_id: task.task_id().to_owned(),
            });
        }
        if matches!(
            snapshot.status(),
            "failed" | "cancelled" | "outcome_uncertain"
        ) {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    Err(ControlPlaneCommandError {
        code: "operation_unavailable",
        retryable: true,
    })
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskDiscoveryAcceptancePreparation {
    installation_id: String,
    task_id: String,
    competing_task_id: String,
    task_status: String,
    task_revision: u32,
    last_event_sequence: u64,
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_task_discovery_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskDiscoveryAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_D610_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_D610_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let task = client
        .create_task(
            &vault,
            "task:discovery:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let competing_task = client
        .create_task(
            &vault,
            "task:discovery:tauri-acceptance-competing",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;

    let mut platform_ready = false;
    for _ in 0..120 {
        let platform = client
            .get_douyin_platform_session(&vault)
            .await
            .map_err(map_control_plane_error)?;
        if platform.state() == "healthy" {
            platform_ready = true;
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    if !platform_ready {
        return Err(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: true,
        });
    }

    Ok(TaskDiscoveryAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
        task_id: task.task_id().to_owned(),
        competing_task_id: competing_task.task_id().to_owned(),
        task_status: task.status().to_owned(),
        task_revision: task.revision(),
        last_event_sequence: task.last_event_sequence(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
fn signal_task_discovery_busy_for_acceptance(
    app: tauri::AppHandle,
) -> Result<(), ControlPlaneCommandError> {
    let directory = app
        .path()
        .app_data_dir()
        .map_err(|_| ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        })?;
    std::fs::write(directory.join("h8-16b-busy-observed"), b"observed").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskTargetPreviewAcceptanceSummary {
    installation_id: String,
    task_id: String,
    page_revision: u64,
    initial_task_revision: u64,
    excluded_task_revision: u64,
    confirmed_task_revision: u64,
    selected_target_count: u16,
    user_excluded_target_count: u16,
    confirmed: bool,
    final_status: String,
    replay_revision: u64,
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn preview_task_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskTargetPreviewAcceptanceSummary, ControlPlaneCommandError> {
    let prepared = prepare_target_preview_acceptance(
        &client,
        &identity,
        &vault,
        "AUTOMATION_TOOL_D611_BOOTSTRAP_TOKEN",
        "AUTOMATION_TOOL_D611_ENVIRONMENT_ID",
        "task:preview:tauri-acceptance",
        "task:preview:discover:tauri-acceptance",
    )
    .await?;
    let initial = client
        .get_task_target_preview(&vault, &prepared.task_id, None, 100)
        .await
        .map_err(map_control_plane_error)?;
    if initial.items().len() != 2
        || initial.items()[0].ordinal() != 1
        || initial.items()[1].ordinal() != 2
        || !initial.items()[0].selected()
        || !initial.items()[1].selected()
    {
        return Err(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        });
    }
    let excluded_target = initial.items()[1].target_id().to_owned();
    let excluded = client
        .replace_task_target_exclusions(
            &vault,
            &prepared.task_id,
            initial.page_revision(),
            initial.task_revision(),
            &[excluded_target],
            "task:preview:exclude:tauri-acceptance",
        )
        .await
        .map_err(map_control_plane_error)?;
    let confirmed = client
        .confirm_task_target_preview(
            &vault,
            &prepared.task_id,
            excluded.page_revision(),
            excluded.confirmation_revision(),
            "task:preview:confirm:tauri-acceptance",
        )
        .await
        .map_err(map_control_plane_error)?;
    let replayed = client
        .confirm_task_target_preview(
            &vault,
            &prepared.task_id,
            excluded.page_revision(),
            excluded.confirmation_revision(),
            "task:preview:confirm:tauri-acceptance",
        )
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskTargetPreviewAcceptanceSummary {
        installation_id: prepared.installation_id,
        task_id: prepared.task_id,
        page_revision: initial.page_revision(),
        initial_task_revision: initial.task_revision(),
        excluded_task_revision: excluded.task_revision(),
        confirmed_task_revision: confirmed.task_revision(),
        selected_target_count: confirmed.selected_target_count(),
        user_excluded_target_count: confirmed.user_excluded_target_count(),
        confirmed: confirmed.confirmed(),
        final_status: confirmed.task_status().to_owned(),
        replay_revision: replayed.task_revision(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_task_target_preview_ui_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<PreparedTargetPreviewAcceptance, ControlPlaneCommandError> {
    prepare_target_preview_acceptance(
        &client,
        &identity,
        &vault,
        "AUTOMATION_TOOL_D612_BOOTSTRAP_TOKEN",
        "AUTOMATION_TOOL_D612_ENVIRONMENT_ID",
        "task:preview-ui:tauri-acceptance",
        "task:preview-ui:discover:tauri-acceptance",
    )
    .await
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn advance_task_target_confirmation_revision_for_acceptance(
    task_id: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskTargetPreview, ControlPlaneCommandError> {
    let preview = client
        .get_task_target_preview(&vault, &task_id, None, 100)
        .await
        .map_err(map_control_plane_error)?;
    client
        .replace_task_target_exclusions(
            &vault,
            &task_id,
            preview.page_revision(),
            preview.task_revision(),
            &[],
            "task:preview-ui:restore:tauri-acceptance",
        )
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn emergency_stop_workbench_task(
    task_id: String,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<control_plane::TaskControlCommand, ControlPlaneCommandError> {
    let service = platform.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        service.engage_task_emergency_stop(&task_id, &idempotency_key)
    })
    .await
    .map_err(|_| ControlPlaneCommandError {
        code: "process_unavailable",
        retryable: true,
    })?
    .map_err(map_task_emergency_stop_platform_error)?;
    reconcile_pending_task_emergency_stop(&client, &vault, &platform)
        .await?
        .ok_or(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        })
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn pause_task_run(
    task_id: String,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskControlCommand, ControlPlaneCommandError> {
    client
        .pause_task(&vault, &task_id, &idempotency_key)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn resume_task_run(
    task_id: String,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskControlCommand, ControlPlaneCommandError> {
    client
        .resume_task(&vault, &task_id, &idempotency_key)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn cancel_task_run(
    task_id: String,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskControlCommand, ControlPlaneCommandError> {
    client
        .cancel_task(&vault, &task_id, &idempotency_key)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn emergency_stop_task_run(
    task_id: String,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<control_plane::TaskControlCommand, ControlPlaneCommandError> {
    let service = platform.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        service.engage_task_emergency_stop(&task_id, &idempotency_key)
    })
    .await
    .map_err(|_| ControlPlaneCommandError {
        code: "process_unavailable",
        retryable: true,
    })?
    .map_err(map_task_emergency_stop_platform_error)?;
    reconcile_pending_task_emergency_stop(&client, &vault, &platform)
        .await?
        .ok_or(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        })
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn get_task_snapshot(
    task_id: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<control_plane::TaskSnapshot, ControlPlaneCommandError> {
    reconcile_pending_task_emergency_stop(&client, &vault, &platform).await?;
    client
        .get_task(&vault, &task_id)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn get_task_target_results(
    task_id: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskTargetResults, ControlPlaneCommandError> {
    client
        .get_task_target_results(&vault, &task_id)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn list_task_snapshots(
    cursor: Option<String>,
    limit: u16,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<control_plane::TaskListPage, ControlPlaneCommandError> {
    reconcile_pending_task_emergency_stop(&client, &vault, &platform).await?;
    client
        .list_tasks(&vault, cursor.as_deref(), limit)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn stream_task_projection_events(
    task_id: String,
    after_sequence: u64,
    on_event: tauri::ipc::Channel<control_plane::TaskEvent>,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskProjectionStreamSummary, ControlPlaneCommandError> {
    let result = client
        .stream_task_events_with(&vault, &task_id, Some(after_sequence), None, |event| {
            on_event.send(event.clone()).is_ok()
        })
        .await
        .map_err(map_control_plane_error)?;
    let mut last_sequence = after_sequence;
    for event in result.events() {
        last_sequence = event.sequence();
    }
    Ok(TaskProjectionStreamSummary {
        last_sequence,
        terminal: result.terminal(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct ControlPlaneAcceptanceSummary {
    health_available: bool,
    installation_id: String,
    initial_version: u32,
    first_capability: &'static str,
    rotated_version: u32,
    second_capability: &'static str,
    revoked_version: u32,
    app_secret_removed: bool,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct InstallationRevocationAcceptanceRegistration {
    installation_id: String,
    revision: u32,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskCreationAcceptanceSummary {
    installation_id: String,
    task_id: String,
    status: String,
    revision: u32,
    replayed: bool,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskCreateFormAcceptancePreparation {
    installation_id: String,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct VideoEditingAcceptancePreparation {
    installation_id: String,
    material_id: String,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct MaterialLibraryAcceptancePreparation {
    installation_id: String,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Deserialize)]
#[serde(rename_all = "snake_case")]
enum MaterialAcceptanceSourceAction {
    Missing,
    Unreadable,
    Changed,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskRunAcceptancePreparation {
    installation_id: String,
    controlled_task_id: String,
    emergency_task_id: String,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskQueryAcceptanceSummary {
    installation_id: String,
    first_page_count: usize,
    second_page_count: usize,
    detail_matched: bool,
    foreign_hidden: bool,
    cursor_opaque: bool,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskEventStreamAcceptanceSummary {
    installation_id: String,
    task_id: String,
    initial_sequences: Vec<u64>,
    resumed_sequences: Vec<u64>,
    terminal: bool,
    progress_percent: Option<u8>,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskProjectionAcceptancePreparation {
    installation_id: String,
    task_id: String,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkbenchAcceptancePreparation {
    installation_id: String,
    task_id: String,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkbenchMetricsAcceptancePreparation {
    installation_id: String,
    task_id: String,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskControlAcceptanceSummary {
    installation_id: String,
    task_id: String,
    pause_command_type: String,
    pause_command_status: String,
    pause_sequence: u64,
    paused_event_type: String,
    resume_command_type: String,
    resume_command_status: String,
    resume_sequence: u64,
    resumed_event_type: String,
    final_status: String,
    final_revision: u32,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskTerminationAcceptanceSummary {
    installation_id: String,
    cancel_task_id: String,
    cancel_command_type: String,
    cancel_command_status: String,
    cancel_sequence: u64,
    cancel_event_type: String,
    cancel_final_status: String,
    cancel_final_revision: u32,
    emergency_task_id: String,
    emergency_command_type: String,
    emergency_command_status: String,
    emergency_sequence: u64,
    emergency_event_type: String,
    emergency_final_status: String,
    emergency_final_revision: u32,
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn control_task_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskControlAcceptanceSummary, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T313_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T313_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let task = client
        .create_task(
            &vault,
            "task:control:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let initial = client
        .stream_task_events(&vault, task.task_id(), None, Some(2))
        .await
        .map_err(map_control_plane_error)?;
    let initial_sequence = initial
        .events()
        .last()
        .map(control_plane::TaskEvent::sequence)
        .ok_or(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        })?;
    let pause = client
        .pause_task(
            &vault,
            task.task_id(),
            "task:control:pause:tauri-acceptance",
        )
        .await
        .map_err(map_control_plane_error)?;
    let paused = client
        .stream_task_events(&vault, task.task_id(), Some(initial_sequence), Some(1))
        .await
        .map_err(map_control_plane_error)?;
    let paused_event = paused.events().first().ok_or(ControlPlaneCommandError {
        code: "operation_unavailable",
        retryable: false,
    })?;
    let resume = client
        .resume_task(
            &vault,
            task.task_id(),
            "task:control:resume:tauri-acceptance",
        )
        .await
        .map_err(map_control_plane_error)?;
    let resumed = client
        .stream_task_events(
            &vault,
            task.task_id(),
            Some(paused_event.sequence()),
            Some(1),
        )
        .await
        .map_err(map_control_plane_error)?;
    let resumed_event = resumed.events().first().ok_or(ControlPlaneCommandError {
        code: "operation_unavailable",
        retryable: false,
    })?;
    let final_snapshot = client
        .get_task(&vault, task.task_id())
        .await
        .map_err(map_control_plane_error)?;

    Ok(TaskControlAcceptanceSummary {
        installation_id: registration.installation_id().to_owned(),
        task_id: task.task_id().to_owned(),
        pause_command_type: pause.command_type().to_owned(),
        pause_command_status: pause.status().to_owned(),
        pause_sequence: pause.sequence(),
        paused_event_type: paused_event.event_type().to_owned(),
        resume_command_type: resume.command_type().to_owned(),
        resume_command_status: resume.status().to_owned(),
        resume_sequence: resume.sequence(),
        resumed_event_type: resumed_event.event_type().to_owned(),
        final_status: final_snapshot.status().to_owned(),
        final_revision: final_snapshot.revision(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn terminate_tasks_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskTerminationAcceptanceSummary, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T314_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T314_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;

    let cancel_task = client
        .create_task(
            &vault,
            "task:termination:cancel:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let cancel_initial = client
        .stream_task_events(&vault, cancel_task.task_id(), None, Some(2))
        .await
        .map_err(map_control_plane_error)?;
    let cancel_cursor = cancel_initial
        .events()
        .last()
        .map(control_plane::TaskEvent::sequence)
        .ok_or(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        })?;
    let cancel = client
        .cancel_task(
            &vault,
            cancel_task.task_id(),
            "task:termination:cancel-command:tauri-acceptance",
        )
        .await
        .map_err(map_control_plane_error)?;
    let cancel_terminal = client
        .stream_task_events(&vault, cancel_task.task_id(), Some(cancel_cursor), Some(1))
        .await
        .map_err(map_control_plane_error)?;
    let cancel_event = cancel_terminal
        .events()
        .first()
        .ok_or(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        })?;
    let cancel_final = client
        .get_task(&vault, cancel_task.task_id())
        .await
        .map_err(map_control_plane_error)?;

    let emergency_task = client
        .create_task(
            &vault,
            "task:termination:emergency:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let emergency_initial = client
        .stream_task_events(&vault, emergency_task.task_id(), None, Some(2))
        .await
        .map_err(map_control_plane_error)?;
    let emergency_cursor = emergency_initial
        .events()
        .last()
        .map(control_plane::TaskEvent::sequence)
        .ok_or(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        })?;
    let emergency = client
        .emergency_stop_task(
            &vault,
            emergency_task.task_id(),
            "task:termination:emergency-command:tauri-acceptance",
        )
        .await
        .map_err(map_control_plane_error)?;
    let emergency_terminal = client
        .stream_task_events(
            &vault,
            emergency_task.task_id(),
            Some(emergency_cursor),
            Some(1),
        )
        .await
        .map_err(map_control_plane_error)?;
    let emergency_event = emergency_terminal
        .events()
        .first()
        .ok_or(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        })?;
    let emergency_final = client
        .get_task(&vault, emergency_task.task_id())
        .await
        .map_err(map_control_plane_error)?;

    Ok(TaskTerminationAcceptanceSummary {
        installation_id: registration.installation_id().to_owned(),
        cancel_task_id: cancel_task.task_id().to_owned(),
        cancel_command_type: cancel.command_type().to_owned(),
        cancel_command_status: cancel.status().to_owned(),
        cancel_sequence: cancel.sequence(),
        cancel_event_type: cancel_event.event_type().to_owned(),
        cancel_final_status: cancel_final.status().to_owned(),
        cancel_final_revision: cancel_final.revision(),
        emergency_task_id: emergency_task.task_id().to_owned(),
        emergency_command_type: emergency.command_type().to_owned(),
        emergency_command_status: emergency.status().to_owned(),
        emergency_sequence: emergency.sequence(),
        emergency_event_type: emergency_event.event_type().to_owned(),
        emergency_final_status: emergency_final.status().to_owned(),
        emergency_final_revision: emergency_final.revision(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn stream_task_events_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskEventStreamAcceptanceSummary, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T312_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T312_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let task = client
        .create_task(
            &vault,
            "task:stream:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let initial = client
        .stream_task_events(&vault, task.task_id(), None, Some(2))
        .await
        .map_err(map_control_plane_error)?;
    let last_event_id = initial
        .events()
        .last()
        .map(control_plane::TaskEvent::sequence)
        .ok_or(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        })?;
    let resumed = client
        .stream_task_events(&vault, task.task_id(), Some(last_event_id), None)
        .await
        .map_err(map_control_plane_error)?;
    let progress_percent = resumed
        .events()
        .iter()
        .find(|event| event.event_type() == "step.progress")
        .and_then(control_plane::TaskEvent::progress_percent);

    Ok(TaskEventStreamAcceptanceSummary {
        installation_id: registration.installation_id().to_owned(),
        task_id: task.task_id().to_owned(),
        initial_sequences: initial
            .events()
            .iter()
            .map(control_plane::TaskEvent::sequence)
            .collect(),
        resumed_sequences: resumed
            .events()
            .iter()
            .map(control_plane::TaskEvent::sequence)
            .collect(),
        terminal: resumed.terminal(),
        progress_percent,
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_task_projection_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskProjectionAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T315_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T315_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let task = client
        .create_task(
            &vault,
            "task:projection:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskProjectionAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
        task_id: task.task_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_workbench_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<WorkbenchAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T316_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T316_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let task = client
        .create_task(
            &vault,
            "task:workbench:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    Ok(WorkbenchAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
        task_id: task.task_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_workbench_metrics_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<WorkbenchMetricsAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_H814_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_H814_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let task = client
        .create_task(
            &vault,
            "task:workbench-metrics:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    Ok(WorkbenchMetricsAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
        task_id: task.task_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_platform_session_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_B513_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_B513_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskCreateFormAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_platform_session_reuse_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_B515_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_B515_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskCreateFormAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_task_create_form_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T317_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T317_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskCreateFormAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_video_editing_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<VideoEditingAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_LE17_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_LE17_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let material_id = std::env::var("AUTOMATION_TOOL_LE17_MATERIAL_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let content_digest = std::env::var("AUTOMATION_TOOL_LE17_MATERIAL_DIGEST").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(|error| map_video_editing_acceptance_error("registration", error))?;
    let material = client
        .register_editing_video_material_for_acceptance(&vault, &material_id, &content_digest)
        .await
        .map_err(|error| map_video_editing_acceptance_error("material", error))?;
    Ok(VideoEditingAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
        material_id: material.material_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_material_library_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<MaterialLibraryAcceptancePreparation, ControlPlaneCommandError> {
    use std::sync::atomic::Ordering;

    let token = std::env::var("AUTOMATION_TOOL_LE18_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_LE18_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    if std::env::var("AUTOMATION_TOOL_LE18_ACCEPTANCE_PICKER").as_deref() != Ok("1") {
        return Err(ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        });
    }
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    MATERIAL_ACCEPTANCE_PICK_INDEX.store(1, Ordering::SeqCst);
    MATERIAL_ACCEPTANCE_SOURCES
        .get_or_init(|| std::sync::Mutex::new(std::collections::HashMap::new()))
        .lock()
        .map_err(|_| ControlPlaneCommandError {
            code: "acceptance_state_unavailable",
            retryable: false,
        })?
        .clear();
    Ok(MaterialLibraryAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
fn set_material_source_state_for_acceptance(
    material_id: String,
    action: MaterialAcceptanceSourceAction,
) -> Result<(), LocalMaterialCommandError> {
    use std::io::Write;

    if std::env::var("AUTOMATION_TOOL_LE18_ACCEPTANCE_PICKER").as_deref() != Ok("1") {
        return Err(LocalMaterialCommandError {
            code: "configuration_invalid",
            retryable: false,
        });
    }
    let material_id = parse_material_id(&material_id)?;
    let source_path = MATERIAL_ACCEPTANCE_SOURCES
        .get_or_init(|| std::sync::Mutex::new(std::collections::HashMap::new()))
        .lock()
        .map_err(|_| LocalMaterialCommandError {
            code: "worker_unavailable",
            retryable: true,
        })?
        .get(&material_id)
        .cloned()
        .ok_or(LocalMaterialCommandError {
            code: "configuration_invalid",
            retryable: false,
        })?;
    let state_error = || LocalMaterialCommandError {
        code: "worker_unavailable",
        retryable: false,
    };
    match action {
        MaterialAcceptanceSourceAction::Missing => {
            std::fs::remove_file(source_path).map_err(|_| state_error())?;
        }
        MaterialAcceptanceSourceAction::Unreadable => {
            std::fs::remove_file(&source_path).map_err(|_| state_error())?;
            std::fs::create_dir(source_path).map_err(|_| state_error())?;
        }
        MaterialAcceptanceSourceAction::Changed => {
            let mut source = std::fs::OpenOptions::new()
                .append(true)
                .open(source_path)
                .map_err(|_| state_error())?;
            source.write_all(&[0]).map_err(|_| state_error())?;
            source.sync_all().map_err(|_| state_error())?;
        }
    }
    Ok(())
}

#[cfg(feature = "control-plane-e2e")]
fn map_video_editing_acceptance_error(
    stage: &'static str,
    error: control_plane::ControlPlaneError,
) -> ControlPlaneCommandError {
    let retryable = error.retryable();
    let code = match (stage, error.code()) {
        ("registration", control_plane::ControlPlaneErrorCode::ProtocolInvalid) => {
            "acceptance_registration_protocol_invalid"
        }
        ("registration", control_plane::ControlPlaneErrorCode::RequestRejected) => {
            "acceptance_registration_rejected"
        }
        ("material", control_plane::ControlPlaneErrorCode::ProtocolInvalid) => {
            "acceptance_material_protocol_invalid"
        }
        ("material", control_plane::ControlPlaneErrorCode::RequestRejected) => {
            "acceptance_material_rejected"
        }
        _ => map_control_plane_error(error).code,
    };
    ControlPlaneCommandError { code, retryable }
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_task_run_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskRunAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T318_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T318_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let controlled_task = client
        .create_task(
            &vault,
            "task:run:controlled:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let emergency_task = client
        .create_task(
            &vault,
            "task:run:emergency:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskRunAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
        controlled_task_id: controlled_task.task_id().to_owned(),
        emergency_task_id: emergency_task.task_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_task_lifecycle_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T319_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T319_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskCreateFormAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_executor_lifecycle_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_E414_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_E414_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskCreateFormAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
fn inject_executor_crash_for_acceptance(
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<(), ExecutorPlatformCommandError> {
    platform
        .inject_crash_for_acceptance()
        .map_err(map_executor_platform_error)
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
fn inject_executor_hang_for_acceptance(
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<(), ExecutorPlatformCommandError> {
    platform
        .inject_hang_for_acceptance()
        .map_err(map_executor_platform_error)
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
fn exit_app_for_acceptance(
    app: tauri::AppHandle,
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<(), ExecutorPlatformCommandError> {
    platform
        .shutdown_for_app_exit()
        .map_err(map_executor_platform_error)?;
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_secs(2));
        app.exit(0);
    });
    Ok(())
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_task_restart_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T320_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T320_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskCreateFormAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_app_crash_recovery_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_H804_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_H804_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskCreateFormAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_executor_crash_recovery_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_H805_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_H805_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskCreateFormAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
async fn prepare_recovery_for_acceptance(
    client: &control_plane::ControlPlaneClient,
    identity: &ProductionDeviceIdentity,
    vault: &ProductionDeviceCredentialVault,
    token_variable: &str,
    environment_variable: &str,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var(token_variable).map_err(|_| ControlPlaneCommandError {
        code: "acceptance_configuration_unavailable",
        retryable: false,
    })?;
    let environment_id =
        std::env::var(environment_variable).map_err(|_| ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, identity, vault)
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskCreateFormAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_control_plane_recovery_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    prepare_recovery_for_acceptance(
        client.inner(),
        identity.inner(),
        vault.inner(),
        "AUTOMATION_TOOL_H806_BOOTSTRAP_TOKEN",
        "AUTOMATION_TOOL_H806_ENVIRONMENT_ID",
    )
    .await
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_network_recovery_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    prepare_recovery_for_acceptance(
        client.inner(),
        identity.inner(),
        vault.inner(),
        "AUTOMATION_TOOL_H807_BOOTSTRAP_TOKEN",
        "AUTOMATION_TOOL_H807_ENVIRONMENT_ID",
    )
    .await
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_system_resume_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    prepare_recovery_for_acceptance(
        client.inner(),
        identity.inner(),
        vault.inner(),
        "AUTOMATION_TOOL_H808_BOOTSTRAP_TOKEN",
        "AUTOMATION_TOOL_H808_ENVIRONMENT_ID",
    )
    .await
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
fn app_process_id_for_acceptance() -> u32 {
    std::process::id()
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn query_tasks_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskQueryAcceptanceSummary, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T307_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T307_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let foreign_task_id = std::env::var("AUTOMATION_TOOL_T307_FOREIGN_TASK_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;

    let mut expected_ids = Vec::new();
    for key in [
        "task:query:tauri-acceptance:1",
        "task:query:tauri-acceptance:2",
        "task:query:tauri-acceptance:3",
    ] {
        let task = client
            .create_task(&vault, key, &acceptance_task_definition())
            .await
            .map_err(map_control_plane_error)?;
        expected_ids.push(task.task_id().to_owned());
    }

    let first_page = client
        .list_tasks(&vault, None, 2)
        .await
        .map_err(map_control_plane_error)?;
    let cursor = first_page.next_cursor().ok_or(ControlPlaneCommandError {
        code: "operation_unavailable",
        retryable: false,
    })?;
    let cursor_opaque = !expected_ids.iter().any(|task_id| cursor.contains(task_id));
    let second_page = client
        .list_tasks(&vault, Some(cursor), 2)
        .await
        .map_err(map_control_plane_error)?;
    let detail_task_id = expected_ids[1].clone();
    let detail = client
        .get_task(&vault, &detail_task_id)
        .await
        .map_err(map_control_plane_error)?;

    let mut listed_ids = first_page
        .items()
        .iter()
        .chain(second_page.items())
        .map(|task| task.task_id().to_owned())
        .collect::<Vec<_>>();
    listed_ids.sort();
    expected_ids.sort();
    let detail_matched = detail.task_id() == detail_task_id;
    let foreign_hidden = client
        .get_task(&vault, &foreign_task_id)
        .await
        .is_err_and(|error| error.code() == control_plane::ControlPlaneErrorCode::RequestRejected);
    if listed_ids != expected_ids
        || first_page.items().len() != 2
        || second_page.items().len() != 1
        || second_page.next_cursor().is_some()
        || !detail_matched
        || !foreign_hidden
        || !cursor_opaque
    {
        return Err(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        });
    }

    Ok(TaskQueryAcceptanceSummary {
        installation_id: registration.installation_id().to_owned(),
        first_page_count: first_page.items().len(),
        second_page_count: second_page.items().len(),
        detail_matched,
        foreign_hidden,
        cursor_opaque,
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn create_task_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreationAcceptanceSummary, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T306_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T306_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let first = client
        .create_task(
            &vault,
            "task:create:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let replay = client
        .create_task(
            &vault,
            "task:create:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let replayed = first.task_id() == replay.task_id()
        && first.status() == replay.status()
        && first.revision() == replay.revision();
    if !replayed {
        return Err(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        });
    }

    Ok(TaskCreationAcceptanceSummary {
        installation_id: registration.installation_id().to_owned(),
        task_id: first.task_id().to_owned(),
        status: first.status().to_owned(),
        revision: first.revision(),
        replayed,
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn register_installation_for_revocation_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<InstallationRevocationAcceptanceRegistration, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_I214_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_I214_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    client
        .check_installation_access_if_registered(&vault)
        .await
        .map_err(map_control_plane_error)?;

    Ok(InstallationRevocationAcceptanceRegistration {
        installation_id: registration.installation_id().to_owned(),
        revision: 1,
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn run_control_plane_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<ControlPlaneAcceptanceSummary, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_I209_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_I209_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;

    client
        .check_health()
        .await
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let first_session = client
        .exchange_device_session(
            &vault,
            control_plane::DeviceSessionCapability::AppControlPlane,
        )
        .await
        .map_err(map_control_plane_error)?;
    let rotated_version = client
        .rotate_device_credential(&vault)
        .await
        .map_err(map_control_plane_error)?;
    let second_session = client
        .exchange_device_session(
            &vault,
            control_plane::DeviceSessionCapability::ExecutorConnect,
        )
        .await
        .map_err(map_control_plane_error)?;
    let revoked_version = client
        .revoke_device_credential(&vault)
        .await
        .map_err(map_control_plane_error)?;
    let app_secret_removed = vault
        .load()
        .map_err(|_| ControlPlaneCommandError {
            code: "storage_unavailable",
            retryable: false,
        })?
        .is_none();
    if !app_secret_removed {
        return Err(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        });
    }

    Ok(ControlPlaneAcceptanceSummary {
        health_available: true,
        installation_id: registration.installation_id().to_owned(),
        initial_version: registration.credential_version(),
        first_capability: first_session.capability().as_str(),
        rotated_version,
        second_capability: second_session.capability().as_str(),
        revoked_version,
        app_secret_removed,
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let deployment_profile =
        deployment_profile::DeploymentProfile::load().expect("deployment profile rejected");
    let update_configuration = app_update_coordinator::UpdateRuntimeConfiguration::load()
        .expect("desktop update configuration rejected");
    let unified_login_config = unified_login_tauri::auth::AuthConfig::builder(
        "https://login.xuanbai.tech",
        "automation-tool-desktop",
        "com.automation-tool.desktop",
    )
    .scopes(["openid", "profile", "email"])
    .build();
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(
            unified_login_tauri::plugin::Builder::from_config_result(unified_login_config).build(),
        )
        .setup(move |app| {
            let app_data_root = app.path().app_data_dir()?;
            if app_logging::initialize(&app_data_root).is_err() {
                eprintln!("desktop fixed-event log unavailable");
            }
            app_logging::record(app_logging::DesktopLogEvent::AppSetupStarted);
            app.manage(control_plane::ControlPlaneClient::for_deployment_profile(
                &deployment_profile,
            )?);
            app_logging::record(app_logging::DesktopLogEvent::ControlPlaneClientInitialized);
            let app_data_directory = deployment_profile.prepare_data_directory(&app_data_root)?;
            app_logging::record(app_logging::DesktopLogEvent::ProfileDataDirectoryReady);
            let update_policy =
                std::sync::Arc::new(app_update_policy::UpdatePolicyService::initialize(
                    &app_data_directory,
                    &app.package_info().version.to_string(),
                    app_updates::DEFAULT_UPDATE_CHANNEL,
                )?);
            app.manage(std::sync::Arc::clone(&update_policy));
            let update_coordinator = match update_configuration.as_ref() {
                Some(configuration) => {
                    let cache = std::sync::Arc::new(app_update_cache::AppUpdateCache::initialize(
                        &app_data_directory,
                        configuration.public_key(),
                    )?);
                    let backend = std::sync::Arc::new(
                        app_update_coordinator::OfficialUpdateCheckBackend::new(
                            app.handle().clone(),
                            configuration.endpoint().clone(),
                            configuration.public_key().to_owned(),
                            configuration.accept_invalid_tls(),
                            configuration.install_probe(),
                        ),
                    );
                    let lifecycle = std::sync::Arc::new(
                        app_update_installation::TauriUpdateInstallLifecycle::new(
                            app.handle().clone(),
                            configuration.install_probe(),
                        ),
                    );
                    let installation = std::sync::Arc::new(
                        app_update_installation::AppUpdateInstallationCoordinator::new(
                            cache.clone(),
                            lifecycle,
                        ),
                    );
                    let coordinator =
                        std::sync::Arc::new(app_update_coordinator::AppUpdateCoordinator::new(
                            backend,
                            update_policy,
                            cache,
                            installation,
                            configuration.download_client()?,
                        )?);
                    coordinator.start_background();
                    Some(coordinator)
                }
                None => None,
            };
            app.manage(update_coordinator);
            app_logging::record(app_logging::DesktopLogEvent::UpdateCoordinatorInitialized);
            app.manage(embedded_browser_authority::EmbeddedBrowserAuthority::new(
                app.path()
                    .resource_dir()
                    .map_err(|error| Box::new(error) as Box<dyn std::error::Error>)?,
                embedded_browser_authority::release_target_id(),
            ));
            app.manage(
                model_service_settings::initialize_production_model_service_settings(
                    &app_data_directory,
                )?,
            );
            app.manage(
                bilibili_service_settings::initialize_production_bilibili_service_settings(
                    &app_data_directory,
                )?,
            );
            app.manage(PublishWorkspaceState(std::sync::Mutex::new(
                publish_workspace::PublishWorkspace::new(false),
            )));
            #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
            app.manage(BilibiliPublishSessionState::new());
            app.manage(startup_environment::StartupEnvironmentService::initialize(
                &app_data_directory,
            )?);
            app.manage(local_video_orchestrator::LocalVideoOrchestrator::new(
                local_video_orchestrator::DEFAULT_VIDEO_WORKER_START_TIMEOUT,
                local_video_orchestrator::DEFAULT_VIDEO_WORKER_REQUEST_TIMEOUT,
            )?);
            app.manage(local_editing_job_ledger::LocalEditingJobScheduler::new());
            app.manage(local_material_library::LocalMaterialLibraryCoordinator::new());
            app.manage(smart_edit_runtime::SmartEditRuntime::new());
            app.manage(local_editing_runtime::LocalEditingRuntime::new(
                app_data_directory.clone(),
            )?);
            app_logging::record(app_logging::DesktopLogEvent::LocalServicesInitialized);
            app.manage(video_job_workspace::VideoJobWorkspaceStore::initialize(
                &app_data_directory,
                video_job_workspace::production_video_workspace_policy(),
            )?);
            app.manage(browser_profiles::BrowserProfileStore::initialize(
                &app_data_directory,
            )?);
            app_logging::record(app_logging::DesktopLogEvent::WorkspaceInitialized);
            let package_root = app
                .path()
                .resource_dir()?
                .join("local-executor")
                .join("package");
            let executor_platform =
                executor_platform::ExecutorPlatformService::initialize_with_package_root(
                    &app_data_directory,
                    &package_root,
                )?;
            app.manage(executor_platform);
            app_logging::record(app_logging::DesktopLogEvent::ExecutorServiceInitialized);
            app.manage(diagnostic_export::DiagnosticExportService::initialize(
                &app_data_directory,
            )?);
            // The account session vault is prepared in every build, because the
            // account gate answers before the workbench mounts and its command
            // cannot be reached without this state. Preparing it costs nothing

            #[cfg(all(feature = "desktop-e2e", not(feature = "control-plane-e2e")))]
            {
                let _production_identity_boundary = device_identity::initialize_production_identity;
                let _production_credential_boundary = initialize_production_device_credential_vault;
                let device_identity = initialize_ephemeral_identity()?;
                debug_assert_eq!(device_identity.as_bytes().len(), 32);
                app.manage(device_identity);
            }

            #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
            {
                let device_identity = initialize_production_identity(&app_data_directory)?;
                let device_credential_vault =
                    initialize_production_device_credential_vault(&app_data_directory)?;
                let local_registration_handoff =
                    initialize_local_registration_handoff_store(&app_data_directory)?;
                debug_assert_eq!(device_identity.public_key().len(), 32);
                app.manage(device_identity);
                app.manage(device_credential_vault);
                app.manage(local_registration_handoff);
            }
            app_logging::record(app_logging::DesktopLogEvent::CredentialsInitialized);
            // 双击即用：App 启动即拉起合并本地服务（控制面 HTTP + 执行器同进程），
            // 不再要求任何手动启动的后端。失败不阻塞窗口——启动页会如实报不可用，
            // 用户点「重新检查」时 ensure_executor_running 会再试。
            #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
            {
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    let client = handle.state::<control_plane::ControlPlaneClient>();
                    let vault = handle.state::<ProductionDeviceCredentialVault>();
                    let platform = handle.state::<executor_platform::ExecutorPlatformService>();
                    let _ = ensure_executor_running(&client, &vault, &platform).await;
                });
            }
            app_logging::record(app_logging::DesktopLogEvent::AppSetupCompleted);
            Ok(())
        });

    #[cfg(feature = "desktop-test-driver")]
    let builder = builder
        .plugin(tauri_plugin_wdio::init())
        .plugin(tauri_plugin_wdio_webdriver::init());

    #[cfg(all(not(feature = "control-plane-e2e"), feature = "desktop-e2e"))]
    let builder = builder.invoke_handler(tauri::generate_handler![
        check_control_plane_health,
        check_local_startup_environment,
        // On the path to the workbench in every deployment: the gate blocks the
        // whole App until this answers, and a deployment that issues no product
        // accounts is answered without opening the vault or touching the network.
        get_executor_status,
        restart_executor,
        get_executor_diagnostics,
        export_diagnostics,
        emergency_stop_executor,
        get_browser_diagnostic_settings,
        set_capture_successful_diagnostics,
        get_model_service_settings,
        configure_model_service,
        reuse_script_model_service_for_video,
        clear_model_service,
        test_model_service_connection,
        get_bilibili_service_settings,
        configure_bilibili_service,
        clear_bilibili_service,
        open_material_video_studio,
        submit_material_montage,
        update_material_video_studio_view,
        close_material_video_studio,
        get_material_render_jobs,
        cancel_material_render_job,
        read_material_video_artifact,
        delete_material_video_artifact,
        submit_motion_video_draft,
        submit_motion_video_brief,
        get_motion_render_jobs,
        cancel_motion_render_job,
        read_motion_video_artifact,
        delete_motion_video_artifact,
        get_update_policy_record_for_acceptance,
        get_app_update_state,
        check_app_update_now,
        decide_app_update
    ]);
    #[cfg(all(not(feature = "control-plane-e2e"), not(feature = "desktop-e2e")))]
    let builder = builder.invoke_handler(tauri::generate_handler![
        check_control_plane_health,
        check_local_startup_environment,
        create_douyin_search_exposure_task,
        start_task_discovery,
        get_task_target_preview,
        replace_task_target_exclusions,
        confirm_task_target_preview,
        get_douyin_platform_session,
        get_workbench_status,
        get_workbench_metrics,
        list_editing_projects,
        list_editing_materials,
        update_editing_material_description,
        create_editing_project,
        get_editing_project_timeline,
        save_editing_project_timeline,
        list_editing_jobs,
        submit_editing_job,
        start_smart_edit_generation,
        get_smart_edit_generation,
        cancel_smart_edit_generation,
        import_editing_material,
        get_local_editing_material_status,
        get_local_editing_material_preview_url,
        delete_editing_material,
        open_douyin_login,
        recheck_douyin_login,
        logout_douyin_session,
        get_publish_workspace,
        begin_publish,
        approve_publish,
        cancel_publish,
        emergency_stop_workbench_task,
        pause_task_run,
        resume_task_run,
        cancel_task_run,
        emergency_stop_task_run,
        get_task_snapshot,
        get_task_target_results,
        list_task_snapshots,
        stream_task_projection_events,
        get_executor_status,
        restart_executor,
        get_executor_diagnostics,
        export_diagnostics,
        emergency_stop_executor,
        get_browser_diagnostic_settings,
        set_capture_successful_diagnostics,
        get_model_service_settings,
        configure_model_service,
        reuse_script_model_service_for_video,
        clear_model_service,
        test_model_service_connection,
        get_bilibili_service_settings,
        configure_bilibili_service,
        clear_bilibili_service,
        open_material_video_studio,
        submit_material_montage,
        update_material_video_studio_view,
        close_material_video_studio,
        get_material_render_jobs,
        cancel_material_render_job,
        read_material_video_artifact,
        delete_material_video_artifact,
        submit_motion_video_draft,
        submit_motion_video_brief,
        get_motion_render_jobs,
        cancel_motion_render_job,
        read_motion_video_artifact,
        delete_motion_video_artifact,
        get_app_update_state,
        check_app_update_now,
        decide_app_update
    ]);
    #[cfg(feature = "control-plane-e2e")]
    let builder = builder.invoke_handler(tauri::generate_handler![
        check_control_plane_health,
        check_local_startup_environment,
        create_douyin_search_exposure_task,
        start_task_discovery,
        get_task_target_preview,
        replace_task_target_exclusions,
        confirm_task_target_preview,
        get_douyin_platform_session,
        get_workbench_status,
        get_workbench_metrics,
        list_editing_projects,
        list_editing_materials,
        update_editing_material_description,
        create_editing_project,
        get_editing_project_timeline,
        save_editing_project_timeline,
        list_editing_jobs,
        submit_editing_job,
        start_smart_edit_generation,
        get_smart_edit_generation,
        cancel_smart_edit_generation,
        import_editing_material,
        get_local_editing_material_status,
        get_local_editing_material_preview_url,
        delete_editing_material,
        open_douyin_login,
        recheck_douyin_login,
        logout_douyin_session,
        get_publish_workspace,
        begin_publish,
        approve_publish,
        cancel_publish,
        emergency_stop_workbench_task,
        pause_task_run,
        resume_task_run,
        cancel_task_run,
        emergency_stop_task_run,
        get_task_snapshot,
        get_task_target_results,
        list_task_snapshots,
        stream_task_projection_events,
        run_control_plane_acceptance,
        register_installation_for_revocation_acceptance,
        create_task_for_acceptance,
        query_tasks_for_acceptance,
        stream_task_events_for_acceptance,
        prepare_task_projection_for_acceptance,
        prepare_task_create_form_for_acceptance,
        prepare_video_editing_for_acceptance,
        prepare_material_library_for_acceptance,
        set_material_source_state_for_acceptance,
        prepare_task_run_for_acceptance,
        prepare_task_lifecycle_for_acceptance,
        prepare_executor_lifecycle_for_acceptance,
        prepare_task_restart_for_acceptance,
        prepare_app_crash_recovery_for_acceptance,
        prepare_executor_crash_recovery_for_acceptance,
        prepare_control_plane_recovery_for_acceptance,
        prepare_network_recovery_for_acceptance,
        prepare_system_resume_for_acceptance,
        app_process_id_for_acceptance,
        prepare_workbench_for_acceptance,
        prepare_workbench_metrics_for_acceptance,
        prepare_platform_session_for_acceptance,
        prepare_platform_session_reuse_for_acceptance,
        prepare_task_discovery_for_acceptance,
        signal_task_discovery_busy_for_acceptance,
        preview_task_for_acceptance,
        prepare_task_target_preview_ui_for_acceptance,
        advance_task_target_confirmation_revision_for_acceptance,
        control_task_for_acceptance,
        terminate_tasks_for_acceptance,
        get_executor_status,
        restart_executor,
        get_executor_diagnostics,
        export_diagnostics,
        emergency_stop_executor,
        get_browser_diagnostic_settings,
        set_capture_successful_diagnostics,
        inject_executor_crash_for_acceptance,
        inject_executor_hang_for_acceptance,
        inject_hostile_executor_diagnostics_for_acceptance,
        exit_app_for_acceptance,
        get_model_service_settings,
        configure_model_service,
        reuse_script_model_service_for_video,
        clear_model_service,
        test_model_service_connection,
        get_bilibili_service_settings,
        configure_bilibili_service,
        clear_bilibili_service,
        open_material_video_studio,
        submit_material_montage,
        update_material_video_studio_view,
        close_material_video_studio,
        exercise_material_video_studio_for_acceptance,
        inspect_material_video_studio_exercise_for_acceptance,
        inspect_material_video_studio_cleanup_for_acceptance,
        get_material_render_jobs,
        cancel_material_render_job,
        read_material_video_artifact,
        delete_material_video_artifact,
        submit_motion_video_draft,
        submit_motion_video_brief,
        get_motion_render_jobs,
        cancel_motion_render_job,
        read_motion_video_artifact,
        delete_motion_video_artifact,
        get_app_update_state,
        check_app_update_now,
        decide_app_update
    ]);

    let app = builder
        .build(tauri::generate_context!())
        .expect("failed to build desktop application");
    app.run(|app_handle, event| {
        if matches!(
            event,
            tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
        ) {
            app_logging::record(app_logging::DesktopLogEvent::AppShutdownStarted);
            if let (Some(orchestrator), Some(workspaces)) = (
                app_handle.try_state::<local_video_orchestrator::LocalVideoOrchestrator>(),
                app_handle.try_state::<video_job_workspace::VideoJobWorkspaceStore>(),
            ) {
                let _ = material_video_studio::close_embedded_view(
                    app_handle,
                    &orchestrator,
                    &workspaces,
                );
            }
            if let Some(platform) =
                app_handle.try_state::<executor_platform::ExecutorPlatformService>()
            {
                let _ = platform.shutdown_for_app_exit();
            }
            if let Some(orchestrator) =
                app_handle.try_state::<local_video_orchestrator::LocalVideoOrchestrator>()
            {
                let _ = orchestrator.stop_all();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    #[test]
    fn smart_edit_command_errors_are_fixed_and_path_free() {
        let runtime = smart_edit_runtime::SmartEditRuntime::new();
        for (generation_id, expected_code) in [
            ("private-invalid-id", "invalid_request"),
            (
                "3d594650-b5f4-4498-8e38-0cf85d6dfa72",
                "generation_not_found",
            ),
        ] {
            let error = runtime
                .snapshot(generation_id)
                .expect_err("invalid or unknown generation must fail closed");
            let wire = serde_json::to_value(map_smart_edit_error(error))
                .expect("smart-edit command error must serialize");
            assert_eq!(
                wire,
                serde_json::json!({
                    "code": expected_code,
                    "message": format!("native command error: {expected_code}"),
                    "retryable": false,
                })
            );
            assert!(!wire.to_string().contains(generation_id));
        }
    }

    /// The JavaScript side of every Tauri command rejection reads `error.message`
    /// before it falls back to `String(error)`, and `String()` of a plain JSON
    /// object is always `[object Object]`. A command error that carries no
    /// `message` therefore reaches every JavaScript consumer — the product, the
    /// browser console and the desktop E2E runner — with its code erased.
    #[test]
    fn a_control_plane_command_error_carries_a_readable_message() {
        let wire = serde_json::to_value(ControlPlaneCommandError {
            code: "installation_access_denied",
            retryable: false,
        })
        .expect("a command error must serialize");

        assert_eq!(
            wire,
            serde_json::json!({
                "code": "installation_access_denied",
                "message": "native command error: installation_access_denied",
                "retryable": false,
            }),
            "the structured fields must survive unchanged and `message` must name the code"
        );
    }

    #[test]
    fn a_local_material_command_error_is_path_free_and_structured() {
        let wire = serde_json::to_value(LocalMaterialCommandError {
            code: "compensation_failed",
            retryable: true,
        })
        .expect("a command error must serialize");

        assert_eq!(
            wire,
            serde_json::json!({
                "code": "compensation_failed",
                "message": "native command error: compensation_failed",
                "retryable": true,
            })
        );
        assert!(!wire.to_string().contains("sourcePath"));
    }

    /// PC-25：安全注销失败时，界面能说出的话取决于这个映射区分了几种失败。
    ///
    /// 五个互不相同的 Profile 失败此前一起塌成 `storage_unavailable`——那句
    /// 文案是「这是本产品自身的问题，重新操作不会有效」。b5_13 的四轮插桩
    /// 之所以只查到「删 Profile 一步失败了」而定位不到是哪一步，就是因为
    /// 到达用户和日志的那个字符串已经把五种原因抹平。每一种都要有自己的码。
    #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
    #[test]
    fn every_profile_removal_failure_reaches_the_operator_as_its_own_code() {
        use browser_profiles::BrowserProfileErrorCode as Code;

        let mapped = |code: Code| {
            let error = map_browser_profile_logout_error(
                browser_profiles::BrowserProfileError::for_tests(code),
            );
            (error.code, error.retryable)
        };

        assert_eq!(mapped(Code::ProfileInUse), ("profile_in_use", true));
        assert_eq!(
            mapped(Code::RecoveryRequired),
            ("profile_recovery_required", false)
        );
        assert_eq!(
            mapped(Code::IdentityChanged),
            ("profile_identity_changed", false)
        );
        assert_eq!(mapped(Code::ProfileNotFound), ("profile_missing", false));
        assert_eq!(
            mapped(Code::UnsafeDirectory),
            ("profile_directory_unsafe", false)
        );
        assert_eq!(
            mapped(Code::InvalidProfileId),
            ("profile_marker_invalid", false)
        );
        assert_eq!(
            mapped(Code::StorageUnavailable),
            ("storage_unavailable", false)
        );

        let codes = [
            Code::ProfileInUse,
            Code::RecoveryRequired,
            Code::IdentityChanged,
            Code::ProfileNotFound,
            Code::UnsafeDirectory,
            Code::InvalidProfileId,
            Code::StorageUnavailable,
        ]
        .map(|code| mapped(code).0);
        let distinct: std::collections::BTreeSet<&str> = codes.iter().copied().collect();
        assert_eq!(
            distinct.len(),
            codes.len(),
            "两种失败共用一个码，用户和日志就分不出该关浏览器还是该报障"
        );
    }

    #[test]
    fn an_executor_platform_command_error_carries_a_readable_message() {
        let wire = serde_json::to_value(ExecutorPlatformCommandError {
            code: "executor_unavailable",
            retryable: true,
        })
        .expect("a command error must serialize");

        assert_eq!(
            wire,
            serde_json::json!({
                "code": "executor_unavailable",
                "message": "native command error: executor_unavailable",
                "retryable": true,
            })
        );
    }

    #[test]
    fn a_diagnostic_export_command_error_carries_a_readable_message() {
        let wire = serde_json::to_value(DiagnosticExportCommandError {
            code: "storage_unavailable",
            retryable: false,
        })
        .expect("a command error must serialize");

        assert_eq!(
            wire,
            serde_json::json!({
                "code": "storage_unavailable",
                "message": "native command error: storage_unavailable",
                "retryable": false,
            })
        );
    }

    /// 发布构建可以显式关闭更新（`AUTOMATION_TOOL_UPDATE_DISABLED=1`，客户 Demo 包
    /// 就是这样构建的）。那时协调器不存在，但这是受支持的正常配置，不是失败：把它
    /// 报成 `failed` 会让设置页在用户什么都没点的情况下显示错误，也会逼前端把真正
    /// 的 `configuration_invalid` 失败一起降级成中性文案。
    #[test]
    fn a_build_with_updates_switched_off_is_not_reported_as_a_failure() {
        let wire = serde_json::to_value(app_update_state_of(None))
            .expect("an update state must serialize");

        assert_eq!(wire, serde_json::json!({ "state": "disabled" }));
    }

    /// This one has no `retryable`, so it proves the shared serializer does not
    /// invent a field the command never had.
    #[test]
    fn an_app_update_decision_error_carries_a_message_without_gaining_a_field() {
        let wire = serde_json::to_value(AppUpdateDecisionCommandError {
            code: "decision_unavailable",
        })
        .expect("a command error must serialize");

        assert_eq!(
            wire,
            serde_json::json!({
                "code": "decision_unavailable",
                "message": "native command error: decision_unavailable",
            })
        );
    }
}

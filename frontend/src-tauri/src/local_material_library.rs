//! Compensating transaction between private Worker mappings and CP materials.

use crate::control_plane::{
    ControlPlaneClient, ControlPlaneErrorCode, EditingMaterialKind,
    EditingMaterialRegistrationRequest, EditingMaterialSnapshot,
};
use crate::device_credentials::DeviceCredentialVault;
use crate::local_video_orchestrator::{
    LocalVideoOrchestrator, VideoWorkerErrorCode, VideoWorkerLocalMaterialError,
    VideoWorkerLocalMaterialFacts, VideoWorkerLocalMaterialFailureCode,
    VideoWorkerLocalMaterialKind, VideoWorkerLocalMaterialStatus,
};
use crate::secure_store::SecretStore;
use serde::Serialize;
use std::fmt;
use std::path::Path;
use uuid::{Uuid, Variant};

pub struct LocalMaterialLibraryCoordinator {
    gate: tokio::sync::Mutex<()>,
}

impl LocalMaterialLibraryCoordinator {
    pub fn new() -> Self {
        Self {
            gate: tokio::sync::Mutex::new(()),
        }
    }

    pub(crate) async fn acquire(&self) -> tokio::sync::MutexGuard<'_, ()> {
        self.gate.lock().await
    }
}

impl Default for LocalMaterialLibraryCoordinator {
    fn default() -> Self {
        Self::new()
    }
}

impl fmt::Debug for LocalMaterialLibraryCoordinator {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("LocalMaterialLibraryCoordinator")
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LocalMaterialLibraryErrorCode {
    ConfigurationInvalid,
    SourceChanged,
    WorkerLifecycle(VideoWorkerErrorCode),
    WorkerRejected(VideoWorkerLocalMaterialFailureCode),
    ControlPlane(ControlPlaneErrorCode),
    CompensationFailed,
    RemapUncertain,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LocalMaterialLibraryError {
    code: LocalMaterialLibraryErrorCode,
    retryable: bool,
}

impl LocalMaterialLibraryError {
    pub const fn code(self) -> LocalMaterialLibraryErrorCode {
        self.code
    }

    pub const fn retryable(self) -> bool {
        self.retryable
    }
}

impl fmt::Display for LocalMaterialLibraryError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Local material library operation is unavailable")
    }
}

impl std::error::Error for LocalMaterialLibraryError {}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalMaterialImportOutcome {
    material: EditingMaterialSnapshot,
    deduplicated: bool,
}

impl LocalMaterialImportOutcome {
    pub fn material(&self) -> &EditingMaterialSnapshot {
        &self.material
    }

    pub const fn deduplicated(&self) -> bool {
        self.deduplicated
    }
}

#[derive(Clone, Copy)]
struct ControlPlaneFailure {
    code: ControlPlaneErrorCode,
    retryable: bool,
}

trait MaterialWorkerPort {
    fn import(
        &self,
        material_id: Uuid,
        source_path: &Path,
    ) -> Result<VideoWorkerLocalMaterialFacts, VideoWorkerLocalMaterialError>;

    fn forget(&self, material_id: Uuid) -> Result<(), VideoWorkerLocalMaterialError>;

    fn status(
        &self,
        material_id: Uuid,
    ) -> Result<VideoWorkerLocalMaterialStatus, VideoWorkerLocalMaterialError>;
}

impl MaterialWorkerPort for LocalVideoOrchestrator {
    fn import(
        &self,
        material_id: Uuid,
        source_path: &Path,
    ) -> Result<VideoWorkerLocalMaterialFacts, VideoWorkerLocalMaterialError> {
        self.import_local_material(material_id, source_path)
    }

    fn forget(&self, material_id: Uuid) -> Result<(), VideoWorkerLocalMaterialError> {
        self.forget_local_material(material_id)
    }

    fn status(
        &self,
        material_id: Uuid,
    ) -> Result<VideoWorkerLocalMaterialStatus, VideoWorkerLocalMaterialError> {
        self.local_material_status(material_id)
    }
}

trait MaterialControlPlanePort {
    async fn find_by_digest(
        &self,
        digest: &str,
    ) -> Result<Option<EditingMaterialSnapshot>, ControlPlaneFailure>;

    async fn register(
        &self,
        request: &EditingMaterialRegistrationRequest,
    ) -> Result<EditingMaterialSnapshot, ControlPlaneFailure>;

    async fn delete(&self, material_id: &str) -> Result<(), ControlPlaneFailure>;
}

struct AuthenticatedControlPlane<'a, S> {
    client: &'a ControlPlaneClient,
    vault: &'a DeviceCredentialVault<S>,
}

impl<S: SecretStore> MaterialControlPlanePort for AuthenticatedControlPlane<'_, S> {
    async fn find_by_digest(
        &self,
        digest: &str,
    ) -> Result<Option<EditingMaterialSnapshot>, ControlPlaneFailure> {
        self.client
            .find_editing_material_by_digest(self.vault, digest)
            .await
            .map_err(control_plane_failure)
    }

    async fn register(
        &self,
        request: &EditingMaterialRegistrationRequest,
    ) -> Result<EditingMaterialSnapshot, ControlPlaneFailure> {
        self.client
            .register_editing_material(self.vault, request)
            .await
            .map_err(control_plane_failure)
    }

    async fn delete(&self, material_id: &str) -> Result<(), ControlPlaneFailure> {
        self.client
            .delete_editing_material(self.vault, material_id)
            .await
            .map_err(control_plane_failure)
    }
}

pub(crate) async fn import_material<S: SecretStore>(
    worker: &LocalVideoOrchestrator,
    client: &ControlPlaneClient,
    vault: &DeviceCredentialVault<S>,
    source_path: &Path,
) -> Result<LocalMaterialImportOutcome, LocalMaterialLibraryError> {
    let control_plane = AuthenticatedControlPlane { client, vault };
    let material_id = new_material_id()?;
    import_material_with(worker, &control_plane, source_path, || material_id).await
}

pub(crate) async fn delete_material<S: SecretStore>(
    worker: &LocalVideoOrchestrator,
    client: &ControlPlaneClient,
    vault: &DeviceCredentialVault<S>,
    material_id: Uuid,
) -> Result<(), LocalMaterialLibraryError> {
    let control_plane = AuthenticatedControlPlane { client, vault };
    delete_material_with(worker, &control_plane, material_id).await
}

pub(crate) fn material_status(
    worker: &LocalVideoOrchestrator,
    material_id: Uuid,
) -> Result<VideoWorkerLocalMaterialStatus, LocalMaterialLibraryError> {
    MaterialWorkerPort::status(worker, material_id).map_err(worker_error)
}

async fn import_material_with<W, C, F>(
    worker: &W,
    control_plane: &C,
    source_path: &Path,
    material_id_factory: F,
) -> Result<LocalMaterialImportOutcome, LocalMaterialLibraryError>
where
    W: MaterialWorkerPort,
    C: MaterialControlPlanePort,
    F: FnOnce() -> Uuid,
{
    let temporary_id = material_id_factory();
    if !valid_uuid_v4(temporary_id) {
        return Err(library_error(
            LocalMaterialLibraryErrorCode::ConfigurationInvalid,
            false,
        ));
    }
    let facts = worker
        .import(temporary_id, source_path)
        .map_err(worker_error)?;
    let registration = registration_request(temporary_id, &facts)?;
    match control_plane.find_by_digest(facts.content_digest()).await {
        Ok(Some(existing)) => remap_duplicate(worker, source_path, temporary_id, existing).await,
        Ok(None) => match control_plane.register(&registration).await {
            Ok(material) => Ok(LocalMaterialImportOutcome {
                material,
                deduplicated: false,
            }),
            Err(registration_failure) => {
                match control_plane.find_by_digest(facts.content_digest()).await {
                    Ok(Some(material)) if material.material_id() == temporary_id.to_string() => {
                        Ok(LocalMaterialImportOutcome {
                            material,
                            deduplicated: false,
                        })
                    }
                    Ok(Some(existing)) => {
                        remap_duplicate(worker, source_path, temporary_id, existing).await
                    }
                    Ok(None) | Err(_) => compensate(worker, temporary_id, registration_failure),
                }
            }
        },
        Err(failure) => compensate(worker, temporary_id, failure),
    }
}

async fn remap_duplicate<W: MaterialWorkerPort>(
    worker: &W,
    source_path: &Path,
    temporary_id: Uuid,
    existing: EditingMaterialSnapshot,
) -> Result<LocalMaterialImportOutcome, LocalMaterialLibraryError> {
    let existing_id = parse_uuid_v4(existing.material_id())?;
    worker
        .forget(temporary_id)
        .map_err(|_| compensation_failed())?;
    let remapped = worker.import(existing_id, source_path).map_err(|error| {
        let mapped = worker_error(error);
        library_error(
            LocalMaterialLibraryErrorCode::RemapUncertain,
            mapped.retryable(),
        )
    })?;
    if remapped.content_digest() != existing.content_digest() {
        worker
            .forget(existing_id)
            .map_err(|_| compensation_failed())?;
        return Err(library_error(
            LocalMaterialLibraryErrorCode::SourceChanged,
            true,
        ));
    }
    Ok(LocalMaterialImportOutcome {
        material: existing,
        deduplicated: true,
    })
}

fn compensate<W: MaterialWorkerPort>(
    worker: &W,
    material_id: Uuid,
    failure: ControlPlaneFailure,
) -> Result<LocalMaterialImportOutcome, LocalMaterialLibraryError> {
    worker
        .forget(material_id)
        .map_err(|_| compensation_failed())?;
    Err(library_error(
        LocalMaterialLibraryErrorCode::ControlPlane(failure.code),
        failure.retryable,
    ))
}

async fn delete_material_with<W: MaterialWorkerPort, C: MaterialControlPlanePort>(
    worker: &W,
    control_plane: &C,
    material_id: Uuid,
) -> Result<(), LocalMaterialLibraryError> {
    if !valid_uuid_v4(material_id) {
        return Err(library_error(
            LocalMaterialLibraryErrorCode::ConfigurationInvalid,
            false,
        ));
    }
    worker.status(material_id).map_err(worker_error)?;
    let material_id_text = material_id.hyphenated().to_string();
    match control_plane.delete(&material_id_text).await {
        Ok(()) => {}
        Err(failure) if failure.code == ControlPlaneErrorCode::ResourceNotFound => {}
        Err(failure) => {
            return Err(library_error(
                LocalMaterialLibraryErrorCode::ControlPlane(failure.code),
                failure.retryable,
            ));
        }
    }
    worker
        .forget(material_id)
        .map_err(|_| compensation_failed())
}

fn registration_request(
    material_id: Uuid,
    facts: &VideoWorkerLocalMaterialFacts,
) -> Result<EditingMaterialRegistrationRequest, LocalMaterialLibraryError> {
    let kind = match facts.kind() {
        VideoWorkerLocalMaterialKind::Video => EditingMaterialKind::Video,
        VideoWorkerLocalMaterialKind::Image => EditingMaterialKind::Image,
        VideoWorkerLocalMaterialKind::Audio => EditingMaterialKind::Audio,
    };
    EditingMaterialRegistrationRequest::new(
        &material_id.hyphenated().to_string(),
        kind,
        facts.duration_ms().map(u64::from),
        facts.width().and_then(|value| u16::try_from(value).ok()),
        facts.height().and_then(|value| u16::try_from(value).ok()),
        facts.content_digest(),
        facts.has_audio(),
        facts.audio_loudness_lufs(),
    )
    .map_err(|_| library_error(LocalMaterialLibraryErrorCode::ConfigurationInvalid, false))
}

fn parse_uuid_v4(value: &str) -> Result<Uuid, LocalMaterialLibraryError> {
    let parsed = Uuid::parse_str(value)
        .map_err(|_| library_error(LocalMaterialLibraryErrorCode::ConfigurationInvalid, false))?;
    if !valid_uuid_v4(parsed) || parsed.hyphenated().to_string() != value {
        return Err(library_error(
            LocalMaterialLibraryErrorCode::ConfigurationInvalid,
            false,
        ));
    }
    Ok(parsed)
}

fn valid_uuid_v4(value: Uuid) -> bool {
    value.get_version_num() == 4 && value.get_variant() == Variant::RFC4122
}

fn new_material_id() -> Result<Uuid, LocalMaterialLibraryError> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes).map_err(|_| {
        library_error(
            LocalMaterialLibraryErrorCode::WorkerLifecycle(
                VideoWorkerErrorCode::ProcessUnavailable,
            ),
            true,
        )
    })?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(Uuid::from_bytes(bytes))
}

fn worker_error(error: VideoWorkerLocalMaterialError) -> LocalMaterialLibraryError {
    match error {
        VideoWorkerLocalMaterialError::Lifecycle(code) => library_error(
            LocalMaterialLibraryErrorCode::WorkerLifecycle(code),
            matches!(
                code,
                VideoWorkerErrorCode::NotRunning
                    | VideoWorkerErrorCode::ProcessUnavailable
                    | VideoWorkerErrorCode::TimedOut
            ),
        ),
        VideoWorkerLocalMaterialError::Rejected(code) => library_error(
            LocalMaterialLibraryErrorCode::WorkerRejected(code),
            matches!(
                code,
                VideoWorkerLocalMaterialFailureCode::SourceNotAtRest
                    | VideoWorkerLocalMaterialFailureCode::FileMissing
                    | VideoWorkerLocalMaterialFailureCode::FileUnreadable
                    | VideoWorkerLocalMaterialFailureCode::FileChanged
                    | VideoWorkerLocalMaterialFailureCode::RegistryUnreadable
                    | VideoWorkerLocalMaterialFailureCode::RegistryUnwritable
            ),
        ),
    }
}

fn control_plane_failure(error: crate::control_plane::ControlPlaneError) -> ControlPlaneFailure {
    ControlPlaneFailure {
        code: error.code(),
        retryable: error.retryable(),
    }
}

const fn library_error(
    code: LocalMaterialLibraryErrorCode,
    retryable: bool,
) -> LocalMaterialLibraryError {
    LocalMaterialLibraryError { code, retryable }
}

const fn compensation_failed() -> LocalMaterialLibraryError {
    library_error(LocalMaterialLibraryErrorCode::CompensationFailed, true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::VecDeque;
    use std::future::ready;

    const TEMPORARY_ID: &str = "623e4567-e89b-42d3-a456-426614174105";
    const EXISTING_ID: &str = "723e4567-e89b-42d3-a456-426614174106";

    struct FakeWorker {
        imports:
            RefCell<VecDeque<Result<VideoWorkerLocalMaterialFacts, VideoWorkerLocalMaterialError>>>,
        forgets: RefCell<VecDeque<Result<(), VideoWorkerLocalMaterialError>>>,
        calls: RefCell<Vec<String>>,
    }

    impl MaterialWorkerPort for FakeWorker {
        fn import(
            &self,
            material_id: Uuid,
            _source_path: &Path,
        ) -> Result<VideoWorkerLocalMaterialFacts, VideoWorkerLocalMaterialError> {
            self.calls
                .borrow_mut()
                .push(format!("import:{material_id}"));
            self.imports
                .borrow_mut()
                .pop_front()
                .expect("import result")
        }

        fn forget(&self, material_id: Uuid) -> Result<(), VideoWorkerLocalMaterialError> {
            self.calls
                .borrow_mut()
                .push(format!("forget:{material_id}"));
            self.forgets.borrow_mut().pop_front().unwrap_or(Ok(()))
        }

        fn status(
            &self,
            material_id: Uuid,
        ) -> Result<VideoWorkerLocalMaterialStatus, VideoWorkerLocalMaterialError> {
            self.calls
                .borrow_mut()
                .push(format!("status:{material_id}"));
            Ok(VideoWorkerLocalMaterialStatus::Available)
        }
    }

    struct FakeControlPlane {
        finds: RefCell<VecDeque<Result<Option<EditingMaterialSnapshot>, ControlPlaneFailure>>>,
        register: RefCell<Option<Result<EditingMaterialSnapshot, ControlPlaneFailure>>>,
        delete: RefCell<Option<Result<(), ControlPlaneFailure>>>,
        calls: RefCell<Vec<String>>,
    }

    impl MaterialControlPlanePort for FakeControlPlane {
        fn find_by_digest(
            &self,
            digest: &str,
        ) -> impl std::future::Future<
            Output = Result<Option<EditingMaterialSnapshot>, ControlPlaneFailure>,
        > {
            self.calls.borrow_mut().push(format!("find:{digest}"));
            ready(self.finds.borrow_mut().pop_front().expect("find result"))
        }

        fn register(
            &self,
            _request: &EditingMaterialRegistrationRequest,
        ) -> impl std::future::Future<Output = Result<EditingMaterialSnapshot, ControlPlaneFailure>>
        {
            self.calls.borrow_mut().push("register".to_owned());
            ready(self.register.borrow_mut().take().expect("register result"))
        }

        fn delete(
            &self,
            material_id: &str,
        ) -> impl std::future::Future<Output = Result<(), ControlPlaneFailure>> {
            self.calls
                .borrow_mut()
                .push(format!("delete:{material_id}"));
            ready(self.delete.borrow_mut().take().expect("delete result"))
        }
    }

    fn facts(digest: &str) -> VideoWorkerLocalMaterialFacts {
        serde_json::from_value(serde_json::json!({
            "audioLoudnessLufs": -18.25,
            "contentDigest": digest,
            "durationMs": 1234,
            "hasAudio": true,
            "height": 1280,
            "kind": "video",
            "width": 720
        }))
        .expect("Worker facts")
    }

    fn material(material_id: &str, digest: &str) -> EditingMaterialSnapshot {
        serde_json::from_value(serde_json::json!({
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
        }))
        .expect("CP material")
    }

    fn worker_with(
        imports: Vec<Result<VideoWorkerLocalMaterialFacts, VideoWorkerLocalMaterialError>>,
        forgets: Vec<Result<(), VideoWorkerLocalMaterialError>>,
    ) -> FakeWorker {
        FakeWorker {
            imports: RefCell::new(imports.into()),
            forgets: RefCell::new(forgets.into()),
            calls: RefCell::new(Vec::new()),
        }
    }

    fn control_plane_with(
        finds: Vec<Result<Option<EditingMaterialSnapshot>, ControlPlaneFailure>>,
        register: Option<Result<EditingMaterialSnapshot, ControlPlaneFailure>>,
        delete: Option<Result<(), ControlPlaneFailure>>,
    ) -> FakeControlPlane {
        FakeControlPlane {
            finds: RefCell::new(finds.into()),
            register: RefCell::new(register),
            delete: RefCell::new(delete),
            calls: RefCell::new(Vec::new()),
        }
    }

    #[test]
    fn a_new_digest_registers_once_without_compensation() {
        let digest = "cd".repeat(32);
        let worker = worker_with(vec![Ok(facts(&digest))], vec![]);
        let control_plane = control_plane_with(
            vec![Ok(None)],
            Some(Ok(material(TEMPORARY_ID, &digest))),
            None,
        );

        let outcome = tauri::async_runtime::block_on(import_material_with(
            &worker,
            &control_plane,
            Path::new("/private/source.mp4"),
            || Uuid::parse_str(TEMPORARY_ID).unwrap(),
        ))
        .expect("new material");

        assert!(!outcome.deduplicated());
        assert_eq!(outcome.material().material_id(), TEMPORARY_ID);
        assert_eq!(&*worker.calls.borrow(), &[format!("import:{TEMPORARY_ID}")]);
        assert_eq!(control_plane.calls.borrow().last().unwrap(), "register");
    }

    #[test]
    fn a_duplicate_forgets_the_temporary_mapping_and_rebinds_the_existing_id() {
        let digest = "cd".repeat(32);
        let worker = worker_with(vec![Ok(facts(&digest)), Ok(facts(&digest))], vec![Ok(())]);
        let control_plane =
            control_plane_with(vec![Ok(Some(material(EXISTING_ID, &digest)))], None, None);

        let outcome = tauri::async_runtime::block_on(import_material_with(
            &worker,
            &control_plane,
            Path::new("/private/source.mp4"),
            || Uuid::parse_str(TEMPORARY_ID).unwrap(),
        ))
        .expect("deduplicated material");

        assert!(outcome.deduplicated());
        assert_eq!(outcome.material().material_id(), EXISTING_ID);
        assert_eq!(
            &*worker.calls.borrow(),
            &[
                format!("import:{TEMPORARY_ID}"),
                format!("forget:{TEMPORARY_ID}"),
                format!("import:{EXISTING_ID}"),
            ]
        );
    }

    #[test]
    fn a_control_plane_failure_forgets_the_new_mapping_before_returning() {
        let digest = "cd".repeat(32);
        let worker = worker_with(vec![Ok(facts(&digest))], vec![Ok(())]);
        let failure = ControlPlaneFailure {
            code: ControlPlaneErrorCode::RequestRejected,
            retryable: false,
        };
        let control_plane = control_plane_with(vec![Ok(None), Ok(None)], Some(Err(failure)), None);

        let error = tauri::async_runtime::block_on(import_material_with(
            &worker,
            &control_plane,
            Path::new("/private/source.mp4"),
            || Uuid::parse_str(TEMPORARY_ID).unwrap(),
        ))
        .expect_err("failed registration");

        assert_eq!(
            error.code(),
            LocalMaterialLibraryErrorCode::ControlPlane(ControlPlaneErrorCode::RequestRejected)
        );
        assert_eq!(
            worker.calls.borrow().last().unwrap(),
            &format!("forget:{TEMPORARY_ID}")
        );
    }

    #[test]
    fn compensation_failure_is_never_reported_as_clean() {
        let digest = "cd".repeat(32);
        let worker = worker_with(
            vec![Ok(facts(&digest))],
            vec![Err(VideoWorkerLocalMaterialError::Rejected(
                VideoWorkerLocalMaterialFailureCode::RegistryUnwritable,
            ))],
        );
        let control_plane = control_plane_with(
            vec![Err(ControlPlaneFailure {
                code: ControlPlaneErrorCode::TransportUnavailable,
                retryable: true,
            })],
            None,
            None,
        );

        let error = tauri::async_runtime::block_on(import_material_with(
            &worker,
            &control_plane,
            Path::new("/private/source.mp4"),
            || Uuid::parse_str(TEMPORARY_ID).unwrap(),
        ))
        .expect_err("failed compensation");

        assert_eq!(
            error.code(),
            LocalMaterialLibraryErrorCode::CompensationFailed
        );
    }

    #[test]
    fn a_changed_source_during_dedupe_forgets_the_wrong_rebinding() {
        let first = "cd".repeat(32);
        let changed = "ef".repeat(32);
        let worker = worker_with(
            vec![Ok(facts(&first)), Ok(facts(&changed))],
            vec![Ok(()), Ok(())],
        );
        let control_plane =
            control_plane_with(vec![Ok(Some(material(EXISTING_ID, &first)))], None, None);

        let error = tauri::async_runtime::block_on(import_material_with(
            &worker,
            &control_plane,
            Path::new("/private/source.mp4"),
            || Uuid::parse_str(TEMPORARY_ID).unwrap(),
        ))
        .expect_err("source changed between probes");

        assert_eq!(error.code(), LocalMaterialLibraryErrorCode::SourceChanged);
        assert_eq!(
            worker.calls.borrow().last().unwrap(),
            &format!("forget:{EXISTING_ID}")
        );
    }

    #[test]
    fn delete_constrains_control_plane_first_and_retries_local_forget_after_404() {
        let material_id = Uuid::parse_str(EXISTING_ID).unwrap();
        let worker = worker_with(vec![], vec![Ok(())]);
        let control_plane = control_plane_with(
            vec![],
            None,
            Some(Err(ControlPlaneFailure {
                code: ControlPlaneErrorCode::ResourceNotFound,
                retryable: false,
            })),
        );

        tauri::async_runtime::block_on(delete_material_with(&worker, &control_plane, material_id))
            .expect("retry completes local forget");

        assert_eq!(
            control_plane.calls.borrow()[0],
            format!("delete:{EXISTING_ID}")
        );
        assert_eq!(
            &*worker.calls.borrow(),
            &[
                format!("status:{EXISTING_ID}"),
                format!("forget:{EXISTING_ID}"),
            ]
        );
    }

    #[test]
    fn delete_rejection_keeps_the_local_mapping_for_a_later_valid_attempt() {
        let material_id = Uuid::parse_str(EXISTING_ID).unwrap();
        let worker = worker_with(vec![], vec![]);
        let control_plane = control_plane_with(
            vec![],
            None,
            Some(Err(ControlPlaneFailure {
                code: ControlPlaneErrorCode::RequestRejected,
                retryable: false,
            })),
        );

        let error = tauri::async_runtime::block_on(delete_material_with(
            &worker,
            &control_plane,
            material_id,
        ))
        .expect_err("Control Plane constraint rejection");

        assert_eq!(
            error.code(),
            LocalMaterialLibraryErrorCode::ControlPlane(ControlPlaneErrorCode::RequestRejected)
        );
        assert_eq!(
            &*worker.calls.borrow(),
            &[format!("status:{EXISTING_ID}")],
            "a rejected CP delete must not forget the local source mapping"
        );
    }

    #[test]
    fn the_shared_gate_serializes_material_transactions_and_render_dispatch() {
        let coordinator = LocalMaterialLibraryCoordinator::new();
        tauri::async_runtime::block_on(async {
            let first = coordinator.acquire().await;
            assert!(coordinator.gate.try_lock().is_err());
            drop(first);
            assert!(coordinator.gate.try_lock().is_ok());
        });
    }
}

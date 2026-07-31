//! Durable App-owned state for one local-editing job per RenderJob workspace.

use crate::local_video_orchestrator::{
    LocalVideoOrchestrator, VideoWorkerError, VideoWorkerErrorCode, VideoWorkerKind,
    VideoWorkerLocalEditingEvent, VideoWorkerLocalEditingFailureCode,
    VideoWorkerLocalEditingJobRequest, VideoWorkerLocalEditingPhase, VideoWorkerState,
};
use crate::video_job_workspace::{
    VideoJobWorkspace, VideoJobWorkspaceStore, VideoWorkspaceDisposition, VideoWorkspaceError,
    VideoWorkspaceErrorCode,
};
use serde::{Deserialize, Deserializer, Serialize};
use std::fmt;
use std::sync::{Mutex, MutexGuard};
use uuid::{Uuid, Variant};

const SCHEMA_VERSION: &str = "local-editing-job.v1";
const CHECKPOINT_NAME: &str = "local-editing-job";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LocalEditingJobLedgerErrorCode {
    AlreadyExists,
    ConfigurationInvalid,
    Conflict,
    DataRejected,
    NotFound,
    StorageUnavailable,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct LocalEditingJobLedgerError {
    code: LocalEditingJobLedgerErrorCode,
}

impl LocalEditingJobLedgerError {
    const fn new(code: LocalEditingJobLedgerErrorCode) -> Self {
        Self { code }
    }

    pub const fn code(self) -> LocalEditingJobLedgerErrorCode {
        self.code
    }
}

impl fmt::Debug for LocalEditingJobLedgerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("LocalEditingJobLedgerError")
            .field("code", &self.code)
            .finish()
    }
}

impl fmt::Display for LocalEditingJobLedgerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Local editing job state is unavailable")
    }
}

impl std::error::Error for LocalEditingJobLedgerError {}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalEditingJobStatus {
    Queued,
    Running,
    Cancelling,
    Succeeded,
    Failed,
    Cancelled,
}

impl LocalEditingJobStatus {
    const fn is_terminal(self) -> bool {
        matches!(self, Self::Succeeded | Self::Failed | Self::Cancelled)
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalEditingJobFailureCode {
    InvalidTimeline,
    MaterialUnavailable,
    MaterialUnsupported,
    FontUnavailable,
    RenderFailed,
    ResourceExhausted,
    PermissionDenied,
    WorkerLost,
}

impl From<VideoWorkerLocalEditingFailureCode> for LocalEditingJobFailureCode {
    fn from(value: VideoWorkerLocalEditingFailureCode) -> Self {
        match value {
            VideoWorkerLocalEditingFailureCode::InvalidTimeline => Self::InvalidTimeline,
            VideoWorkerLocalEditingFailureCode::MaterialUnavailable => Self::MaterialUnavailable,
            VideoWorkerLocalEditingFailureCode::MaterialUnsupported => Self::MaterialUnsupported,
            VideoWorkerLocalEditingFailureCode::FontUnavailable => Self::FontUnavailable,
            VideoWorkerLocalEditingFailureCode::RenderFailed => Self::RenderFailed,
            VideoWorkerLocalEditingFailureCode::ResourceExhausted
            | VideoWorkerLocalEditingFailureCode::WorkspaceUnusable => Self::ResourceExhausted,
            VideoWorkerLocalEditingFailureCode::PermissionDenied => Self::PermissionDenied,
        }
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct LocalEditingJobSnapshot {
    job_id: Uuid,
    project_id: Uuid,
    timeline_id: Uuid,
    timeline_revision: u32,
    status: LocalEditingJobStatus,
    phase: Option<VideoWorkerLocalEditingPhase>,
    progress_per_mille: u16,
    worker_generation: u32,
    recovery_attempts: u32,
    revision: u64,
    failure_code: Option<LocalEditingJobFailureCode>,
    output_artifact_id: Option<Uuid>,
}

impl fmt::Debug for LocalEditingJobSnapshot {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("LocalEditingJobSnapshot(<redacted>)")
    }
}

impl LocalEditingJobSnapshot {
    pub const fn job_id(&self) -> Uuid {
        self.job_id
    }

    pub const fn project_id(&self) -> Uuid {
        self.project_id
    }

    pub const fn timeline_id(&self) -> Uuid {
        self.timeline_id
    }

    pub const fn timeline_revision(&self) -> u32 {
        self.timeline_revision
    }

    pub const fn status(&self) -> LocalEditingJobStatus {
        self.status
    }

    pub const fn phase(&self) -> Option<VideoWorkerLocalEditingPhase> {
        self.phase
    }

    pub const fn progress_per_mille(&self) -> u16 {
        self.progress_per_mille
    }

    pub const fn worker_generation(&self) -> u32 {
        self.worker_generation
    }

    pub const fn recovery_attempts(&self) -> u32 {
        self.recovery_attempts
    }

    pub const fn revision(&self) -> u64 {
        self.revision
    }

    pub const fn failure_code(&self) -> Option<LocalEditingJobFailureCode> {
        self.failure_code
    }

    pub const fn output_artifact_id(&self) -> Option<Uuid> {
        self.output_artifact_id
    }

    fn validate(&self) -> Result<(), LocalEditingJobLedgerError> {
        if !valid_uuid_v4(self.job_id)
            || !valid_uuid_v4(self.project_id)
            || !valid_uuid_v4(self.timeline_id)
            || self.job_id == self.project_id
            || self.job_id == self.timeline_id
            || self.project_id == self.timeline_id
            || self.timeline_revision == 0
            || self.timeline_revision > i32::MAX as u32
            || self.revision == 0
            || self.recovery_attempts > 8
            || self.progress_per_mille > 1000
            || (self.phase.is_none() && self.progress_per_mille != 0)
        {
            return Err(data_rejected());
        }
        let facts_match = match self.status {
            LocalEditingJobStatus::Queued => {
                self.phase.is_none()
                    && self.progress_per_mille == 0
                    && self.worker_generation == 0
                    && self.recovery_attempts == 0
                    && self.failure_code.is_none()
                    && self.output_artifact_id.is_none()
            }
            LocalEditingJobStatus::Running | LocalEditingJobStatus::Cancelling => {
                self.failure_code.is_none() && self.output_artifact_id.is_none()
            }
            LocalEditingJobStatus::Succeeded => {
                self.phase == Some(VideoWorkerLocalEditingPhase::Publishing)
                    && self.progress_per_mille == 1000
                    && self.failure_code.is_none()
                    && self.output_artifact_id.is_some_and(valid_uuid_v4)
            }
            LocalEditingJobStatus::Failed => {
                self.failure_code.is_some() && self.output_artifact_id.is_none()
            }
            LocalEditingJobStatus::Cancelled => {
                self.failure_code.is_none() && self.output_artifact_id.is_none()
            }
        };
        if !facts_match {
            return Err(data_rejected());
        }
        Ok(())
    }

    fn bumped(mut self) -> Result<Self, LocalEditingJobLedgerError> {
        self.revision = self.revision.checked_add(1).ok_or_else(data_rejected)?;
        self.validate()?;
        Ok(self)
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct StoredLocalEditingJob {
    #[serde(deserialize_with = "deserialize_required_option")]
    failure_code: Option<LocalEditingJobFailureCode>,
    job_id: String,
    #[serde(deserialize_with = "deserialize_required_option")]
    output_artifact_id: Option<String>,
    #[serde(deserialize_with = "deserialize_required_option")]
    phase: Option<VideoWorkerLocalEditingPhase>,
    progress_per_mille: u16,
    project_id: String,
    revision: u64,
    recovery_attempts: u32,
    schema_version: String,
    status: LocalEditingJobStatus,
    timeline_id: String,
    timeline_revision: u32,
    worker_generation: u32,
}

impl TryFrom<StoredLocalEditingJob> for LocalEditingJobSnapshot {
    type Error = LocalEditingJobLedgerError;

    fn try_from(value: StoredLocalEditingJob) -> Result<Self, Self::Error> {
        if value.schema_version != SCHEMA_VERSION {
            return Err(data_rejected());
        }
        let snapshot = Self {
            job_id: parse_uuid_v4(&value.job_id)?,
            project_id: parse_uuid_v4(&value.project_id)?,
            timeline_id: parse_uuid_v4(&value.timeline_id)?,
            timeline_revision: value.timeline_revision,
            status: value.status,
            phase: value.phase,
            progress_per_mille: value.progress_per_mille,
            worker_generation: value.worker_generation,
            revision: value.revision,
            recovery_attempts: value.recovery_attempts,
            failure_code: value.failure_code,
            output_artifact_id: value
                .output_artifact_id
                .as_deref()
                .map(parse_uuid_v4)
                .transpose()?,
        };
        snapshot.validate()?;
        Ok(snapshot)
    }
}

impl From<&LocalEditingJobSnapshot> for StoredLocalEditingJob {
    fn from(value: &LocalEditingJobSnapshot) -> Self {
        Self {
            failure_code: value.failure_code,
            job_id: value.job_id.hyphenated().to_string(),
            output_artifact_id: value
                .output_artifact_id
                .map(|identifier| identifier.hyphenated().to_string()),
            phase: value.phase,
            progress_per_mille: value.progress_per_mille,
            project_id: value.project_id.hyphenated().to_string(),
            revision: value.revision,
            recovery_attempts: value.recovery_attempts,
            schema_version: SCHEMA_VERSION.to_owned(),
            status: value.status,
            timeline_id: value.timeline_id.hyphenated().to_string(),
            timeline_revision: value.timeline_revision,
            worker_generation: value.worker_generation,
        }
    }
}

pub struct LocalEditingJobLedger {
    gate: Mutex<()>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LocalEditingJobSchedulerErrorCode {
    AuthenticationRejected,
    ConfigurationInvalid,
    Conflict,
    StateUnavailable,
    WorkerUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LocalEditingJobRecoveryPolicy {
    maximum_recoveries: u32,
}

impl LocalEditingJobRecoveryPolicy {
    pub fn new(maximum_recoveries: u32) -> Result<Self, LocalEditingJobSchedulerError> {
        if maximum_recoveries > 8 {
            return Err(LocalEditingJobSchedulerError::new(
                LocalEditingJobSchedulerErrorCode::ConfigurationInvalid,
            ));
        }
        Ok(Self { maximum_recoveries })
    }

    pub const fn maximum_recoveries(self) -> u32 {
        self.maximum_recoveries
    }
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct LocalEditingJobSchedulerError {
    code: LocalEditingJobSchedulerErrorCode,
}

impl LocalEditingJobSchedulerError {
    const fn new(code: LocalEditingJobSchedulerErrorCode) -> Self {
        Self { code }
    }

    pub const fn code(self) -> LocalEditingJobSchedulerErrorCode {
        self.code
    }
}

impl fmt::Debug for LocalEditingJobSchedulerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("LocalEditingJobSchedulerError")
            .field("code", &self.code)
            .finish()
    }
}

impl fmt::Display for LocalEditingJobSchedulerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Local editing job scheduling is unavailable")
    }
}

impl std::error::Error for LocalEditingJobSchedulerError {}

/// The only public bridge between durable editing state and Worker IPC.
pub struct LocalEditingJobScheduler {
    gate: Mutex<()>,
    ledger: LocalEditingJobLedger,
}

impl Default for LocalEditingJobScheduler {
    fn default() -> Self {
        Self::new()
    }
}

impl LocalEditingJobScheduler {
    pub const fn new() -> Self {
        Self {
            gate: Mutex::new(()),
            ledger: LocalEditingJobLedger::new(),
        }
    }

    pub fn create(
        &self,
        store: &VideoJobWorkspaceStore,
        job_id: Uuid,
        request: &VideoWorkerLocalEditingJobRequest,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobSchedulerError> {
        let _guard = self.lock()?;
        self.ledger
            .create(store, job_id, request)
            .map_err(map_ledger_error)
    }

    pub fn snapshot(
        &self,
        store: &VideoJobWorkspaceStore,
        job_id: Uuid,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobSchedulerError> {
        let _guard = self.lock()?;
        self.ledger.load(store, job_id).map_err(map_ledger_error)
    }

    pub fn dispatch(
        &self,
        store: &VideoJobWorkspaceStore,
        orchestrator: &LocalVideoOrchestrator,
        job_id: Uuid,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobSchedulerError> {
        let _guard = self.lock()?;
        let current = self.ledger.load(store, job_id).map_err(map_ledger_error)?;
        if current.status == LocalEditingJobStatus::Cancelling {
            return self
                .ledger
                .confirm_cancelled(store, job_id)
                .map_err(map_ledger_error);
        }
        if current.status != LocalEditingJobStatus::Queued {
            return Err(scheduler_conflict());
        }
        let worker = orchestrator
            .status(VideoWorkerKind::Python)
            .map_err(map_worker_error)?;
        let running = self
            .ledger
            .mark_running(store, job_id, u32::from(worker.restart_count()))
            .map_err(map_ledger_error)?;
        let request = VideoWorkerLocalEditingJobRequest::new(
            running.project_id,
            running.timeline_id,
            running.timeline_revision,
        )
        .map_err(map_worker_error)?;
        if let Err(error) = orchestrator.start_local_editing_job(job_id, &request) {
            self.ledger
                .fail_worker_lost(store, job_id)
                .map_err(map_ledger_error)?;
            return Err(map_worker_error(error));
        }
        Ok(running)
    }

    /// Poll one event, persist it, then and only then return its durable view.
    pub fn poll(
        &self,
        store: &VideoJobWorkspaceStore,
        orchestrator: &LocalVideoOrchestrator,
        job_id: Uuid,
    ) -> Result<Option<LocalEditingJobSnapshot>, LocalEditingJobSchedulerError> {
        let _guard = self.lock()?;
        let Some(event) = orchestrator
            .try_local_editing_event(job_id)
            .map_err(map_worker_error)?
        else {
            return Ok(None);
        };
        self.persist_event(store, orchestrator, job_id, event)
            .map(Some)
    }

    pub fn poll_with_recovery(
        &self,
        store: &VideoJobWorkspaceStore,
        orchestrator: &LocalVideoOrchestrator,
        job_id: Uuid,
        policy: LocalEditingJobRecoveryPolicy,
    ) -> Result<Option<LocalEditingJobSnapshot>, LocalEditingJobSchedulerError> {
        let _guard = self.lock()?;
        match orchestrator.try_local_editing_event(job_id) {
            Ok(Some(event)) => self
                .persist_event(store, orchestrator, job_id, event)
                .map(Some),
            Ok(None) => Ok(None),
            Err(error)
                if matches!(
                    error.code(),
                    VideoWorkerErrorCode::NotRunning | VideoWorkerErrorCode::ProcessUnavailable
                ) =>
            {
                let current = self.ledger.load(store, job_id).map_err(map_ledger_error)?;
                self.reconcile_loaded(store, orchestrator, current, policy)
                    .map(Some)
            }
            Err(error) => Err(map_worker_error(error)),
        }
    }

    fn persist_event(
        &self,
        store: &VideoJobWorkspaceStore,
        orchestrator: &LocalVideoOrchestrator,
        job_id: Uuid,
        event: VideoWorkerLocalEditingEvent,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobSchedulerError> {
        let snapshot = self
            .ledger
            .apply_event(store, job_id, &event)
            .map_err(map_ledger_error)?;
        if snapshot.status.is_terminal() {
            // The checkpoint is authoritative once this point is reached. A
            // concurrent Worker stop may make the in-memory acknowledgement
            // impossible, but must not hide the already-durable terminal fact.
            let _ = orchestrator.finish_local_editing_job(job_id);
        }
        Ok(snapshot)
    }

    pub fn request_cancel(
        &self,
        store: &VideoJobWorkspaceStore,
        orchestrator: &LocalVideoOrchestrator,
        job_id: Uuid,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobSchedulerError> {
        let _guard = self.lock()?;
        let before = self.ledger.load(store, job_id).map_err(map_ledger_error)?;
        let cancelling = self
            .ledger
            .request_cancel(store, job_id)
            .map_err(map_ledger_error)?;
        if before.status == LocalEditingJobStatus::Queued {
            return self
                .ledger
                .confirm_cancelled(store, job_id)
                .map_err(map_ledger_error);
        }
        if before.status == LocalEditingJobStatus::Cancelling {
            return Ok(cancelling);
        }
        orchestrator
            .request_local_editing_cancel(job_id)
            .map_err(map_worker_error)?;
        Ok(cancelling)
    }

    pub fn emergency_stop(
        &self,
        store: &VideoJobWorkspaceStore,
        orchestrator: &LocalVideoOrchestrator,
        job_id: Uuid,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobSchedulerError> {
        let _guard = self.lock()?;
        let before = self.ledger.load(store, job_id).map_err(map_ledger_error)?;
        let cancelling = self
            .ledger
            .request_cancel(store, job_id)
            .map_err(map_ledger_error)?;
        if before.status == LocalEditingJobStatus::Queued {
            return self
                .ledger
                .confirm_cancelled(store, job_id)
                .map_err(map_ledger_error);
        }
        if cancelling.status != LocalEditingJobStatus::Cancelling {
            return Err(scheduler_conflict());
        }
        orchestrator
            .emergency_stop_local_editing_job(job_id)
            .map_err(map_worker_error)?;
        self.ledger
            .confirm_cancelled(store, job_id)
            .map_err(map_ledger_error)
    }

    pub fn fail_worker_lost(
        &self,
        store: &VideoJobWorkspaceStore,
        job_id: Uuid,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobSchedulerError> {
        let _guard = self.lock()?;
        self.ledger
            .fail_worker_lost(store, job_id)
            .map_err(map_ledger_error)
    }

    pub fn reconcile_job(
        &self,
        store: &VideoJobWorkspaceStore,
        orchestrator: &LocalVideoOrchestrator,
        job_id: Uuid,
        policy: LocalEditingJobRecoveryPolicy,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobSchedulerError> {
        let _guard = self.lock()?;
        let current = self.ledger.load(store, job_id).map_err(map_ledger_error)?;
        self.reconcile_loaded(store, orchestrator, current, policy)
    }

    pub fn reconcile_all(
        &self,
        store: &VideoJobWorkspaceStore,
        orchestrator: &LocalVideoOrchestrator,
        policy: LocalEditingJobRecoveryPolicy,
    ) -> Result<Vec<LocalEditingJobSnapshot>, LocalEditingJobSchedulerError> {
        let _guard = self.lock()?;
        let workspaces = store
            .list_workspaces()
            .map_err(map_workspace_error)
            .map_err(map_ledger_error)?;
        let mut candidates = Vec::new();
        for workspace in workspaces {
            let current = match self.ledger.load(store, workspace.job_id()) {
                Ok(snapshot) => snapshot,
                Err(error) if error.code() == LocalEditingJobLedgerErrorCode::NotFound => continue,
                Err(error) => return Err(map_ledger_error(error)),
            };
            candidates.push(current);
        }
        let mut snapshots = Vec::with_capacity(candidates.len());
        for current in candidates {
            snapshots.push(self.reconcile_loaded(store, orchestrator, current, policy)?);
        }
        Ok(snapshots)
    }

    fn reconcile_loaded(
        &self,
        store: &VideoJobWorkspaceStore,
        orchestrator: &LocalVideoOrchestrator,
        current: LocalEditingJobSnapshot,
        policy: LocalEditingJobRecoveryPolicy,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobSchedulerError> {
        let job_id = current.job_id;
        if current.status.is_terminal() || current.status == LocalEditingJobStatus::Queued {
            return Ok(current);
        }

        let worker = match orchestrator.status(VideoWorkerKind::Python) {
            Ok(worker) => worker,
            Err(_) => {
                return self
                    .ledger
                    .fail_worker_lost(store, job_id)
                    .map_err(map_ledger_error);
            }
        };
        let owner = match orchestrator.local_editing_job_owner() {
            Ok(owner) => owner,
            Err(_) => {
                return self
                    .ledger
                    .fail_worker_lost(store, job_id)
                    .map_err(map_ledger_error);
            }
        };
        if owner == Some(job_id) {
            return Ok(current);
        }
        if current.status == LocalEditingJobStatus::Cancelling
            || worker.state() != VideoWorkerState::Running
            || owner.is_some()
            || current.recovery_attempts >= policy.maximum_recoveries()
        {
            return self
                .ledger
                .fail_worker_lost(store, job_id)
                .map_err(map_ledger_error);
        }

        let recovered = self
            .ledger
            .prepare_recovery(store, job_id, u32::from(worker.restart_count()))
            .map_err(map_ledger_error)?;
        let request = VideoWorkerLocalEditingJobRequest::new(
            recovered.project_id,
            recovered.timeline_id,
            recovered.timeline_revision,
        )
        .map_err(map_worker_error)?;
        if orchestrator
            .start_local_editing_job(job_id, &request)
            .is_err()
        {
            return self
                .ledger
                .fail_worker_lost(store, job_id)
                .map_err(map_ledger_error);
        }
        Ok(recovered)
    }

    fn lock(&self) -> Result<MutexGuard<'_, ()>, LocalEditingJobSchedulerError> {
        self.gate.lock().map_err(|_| scheduler_state_unavailable())
    }
}

impl Default for LocalEditingJobLedger {
    fn default() -> Self {
        Self::new()
    }
}

impl LocalEditingJobLedger {
    pub const fn new() -> Self {
        Self {
            gate: Mutex::new(()),
        }
    }

    pub fn create(
        &self,
        store: &VideoJobWorkspaceStore,
        job_id: Uuid,
        request: &VideoWorkerLocalEditingJobRequest,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobLedgerError> {
        if !valid_uuid_v4(job_id)
            || job_id == request.project_id()
            || job_id == request.timeline_id()
        {
            return Err(configuration_invalid());
        }
        let _guard = self.lock()?;
        let workspace = store.create(job_id).map_err(map_workspace_error)?;
        let snapshot = LocalEditingJobSnapshot {
            job_id,
            project_id: request.project_id(),
            timeline_id: request.timeline_id(),
            timeline_revision: request.timeline_revision(),
            status: LocalEditingJobStatus::Queued,
            phase: None,
            progress_per_mille: 0,
            worker_generation: 0,
            recovery_attempts: 0,
            revision: 1,
            failure_code: None,
            output_artifact_id: None,
        };
        if snapshot
            .validate()
            .and_then(|()| save(store, &workspace, &snapshot))
            .is_err()
        {
            let _ = store.finish(&workspace, VideoWorkspaceDisposition::Delete);
            return Err(storage_unavailable());
        }
        Ok(snapshot)
    }

    pub fn load(
        &self,
        store: &VideoJobWorkspaceStore,
        job_id: Uuid,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobLedgerError> {
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let _guard = self.lock()?;
        load(store, job_id)
    }

    pub fn mark_running(
        &self,
        store: &VideoJobWorkspaceStore,
        job_id: Uuid,
        worker_generation: u32,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobLedgerError> {
        self.mutate(store, job_id, |current| {
            if current.status == LocalEditingJobStatus::Running
                && current.worker_generation == worker_generation
            {
                return Ok(current);
            }
            if current.status != LocalEditingJobStatus::Queued {
                return Err(conflict());
            }
            let mut next = current;
            next.status = LocalEditingJobStatus::Running;
            next.worker_generation = worker_generation;
            next.bumped()
        })
    }

    pub fn request_cancel(
        &self,
        store: &VideoJobWorkspaceStore,
        job_id: Uuid,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobLedgerError> {
        self.mutate(store, job_id, |current| {
            if current.status == LocalEditingJobStatus::Cancelling {
                return Ok(current);
            }
            if !matches!(
                current.status,
                LocalEditingJobStatus::Queued | LocalEditingJobStatus::Running
            ) {
                return Err(conflict());
            }
            let mut next = current;
            next.status = LocalEditingJobStatus::Cancelling;
            next.bumped()
        })
    }

    pub fn confirm_cancelled(
        &self,
        store: &VideoJobWorkspaceStore,
        job_id: Uuid,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobLedgerError> {
        self.mutate(store, job_id, |current| {
            if current.status == LocalEditingJobStatus::Cancelled {
                return Ok(current);
            }
            if current.status != LocalEditingJobStatus::Cancelling {
                return Err(conflict());
            }
            let mut next = current;
            next.status = LocalEditingJobStatus::Cancelled;
            next.bumped()
        })
    }

    pub fn fail_worker_lost(
        &self,
        store: &VideoJobWorkspaceStore,
        job_id: Uuid,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobLedgerError> {
        self.mutate(store, job_id, |current| {
            if current.status == LocalEditingJobStatus::Failed
                && current.failure_code == Some(LocalEditingJobFailureCode::WorkerLost)
            {
                return Ok(current);
            }
            if !matches!(
                current.status,
                LocalEditingJobStatus::Running | LocalEditingJobStatus::Cancelling
            ) {
                return Err(conflict());
            }
            let mut next = current;
            next.status = LocalEditingJobStatus::Failed;
            next.failure_code = Some(LocalEditingJobFailureCode::WorkerLost);
            next.bumped()
        })
    }

    fn prepare_recovery(
        &self,
        store: &VideoJobWorkspaceStore,
        job_id: Uuid,
        observed_worker_generation: u32,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobLedgerError> {
        self.mutate(store, job_id, |current| {
            if current.status != LocalEditingJobStatus::Running {
                return Err(conflict());
            }
            let mut next = current;
            next.worker_generation = next
                .worker_generation
                .checked_add(1)
                .ok_or_else(data_rejected)?
                .max(observed_worker_generation);
            next.recovery_attempts = next
                .recovery_attempts
                .checked_add(1)
                .ok_or_else(data_rejected)?;
            next.phase = None;
            next.progress_per_mille = 0;
            next.bumped()
        })
    }

    pub fn apply_event(
        &self,
        store: &VideoJobWorkspaceStore,
        job_id: Uuid,
        event: &VideoWorkerLocalEditingEvent,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobLedgerError> {
        self.mutate(store, job_id, |current| apply_event(current, event))
    }

    fn mutate(
        &self,
        store: &VideoJobWorkspaceStore,
        job_id: Uuid,
        transition: impl FnOnce(
            LocalEditingJobSnapshot,
        ) -> Result<LocalEditingJobSnapshot, LocalEditingJobLedgerError>,
    ) -> Result<LocalEditingJobSnapshot, LocalEditingJobLedgerError> {
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let _guard = self.lock()?;
        let workspace = store.open(job_id).map_err(map_workspace_error)?;
        let current = load_from_workspace(store, &workspace)?;
        let next = transition(current.clone())?;
        if next == current {
            return Ok(current);
        }
        if next.job_id != current.job_id
            || next.project_id != current.project_id
            || next.timeline_id != current.timeline_id
            || next.timeline_revision != current.timeline_revision
            || next.revision != current.revision.checked_add(1).ok_or_else(data_rejected)?
        {
            return Err(conflict());
        }
        next.validate()?;
        save(store, &workspace, &next)?;
        Ok(next)
    }

    fn lock(&self) -> Result<MutexGuard<'_, ()>, LocalEditingJobLedgerError> {
        self.gate.lock().map_err(|_| storage_unavailable())
    }
}

fn apply_event(
    current: LocalEditingJobSnapshot,
    event: &VideoWorkerLocalEditingEvent,
) -> Result<LocalEditingJobSnapshot, LocalEditingJobLedgerError> {
    match event {
        VideoWorkerLocalEditingEvent::Progress {
            phase,
            progress_per_mille,
        } => {
            if current.status.is_terminal()
                || !matches!(
                    current.status,
                    LocalEditingJobStatus::Running | LocalEditingJobStatus::Cancelling
                )
                || *progress_per_mille > 1000
            {
                return Err(conflict());
            }
            if current.phase == Some(*phase) && current.progress_per_mille == *progress_per_mille {
                return Ok(current);
            }
            match current.phase {
                None if *phase != VideoWorkerLocalEditingPhase::Preparing
                    || *progress_per_mille != 0 =>
                {
                    return Err(conflict());
                }
                Some(previous)
                    if *phase < previous || *progress_per_mille < current.progress_per_mille =>
                {
                    return Err(conflict());
                }
                _ => {}
            }
            let mut next = current;
            next.phase = Some(*phase);
            next.progress_per_mille = *progress_per_mille;
            next.bumped()
        }
        VideoWorkerLocalEditingEvent::Succeeded { output_artifact_id } => {
            if current.status == LocalEditingJobStatus::Succeeded
                && current.output_artifact_id == Some(*output_artifact_id)
            {
                return Ok(current);
            }
            if !matches!(
                current.status,
                LocalEditingJobStatus::Running | LocalEditingJobStatus::Cancelling
            ) || current.phase != Some(VideoWorkerLocalEditingPhase::Publishing)
                || current.progress_per_mille != 1000
                || !valid_uuid_v4(*output_artifact_id)
            {
                return Err(conflict());
            }
            let mut next = current;
            next.status = LocalEditingJobStatus::Succeeded;
            next.output_artifact_id = Some(*output_artifact_id);
            next.bumped()
        }
        VideoWorkerLocalEditingEvent::Failed { failure_code } => {
            let mapped = LocalEditingJobFailureCode::from(*failure_code);
            if current.status == LocalEditingJobStatus::Failed
                && current.failure_code == Some(mapped)
            {
                return Ok(current);
            }
            if !matches!(
                current.status,
                LocalEditingJobStatus::Running | LocalEditingJobStatus::Cancelling
            ) {
                return Err(conflict());
            }
            let mut next = current;
            next.status = LocalEditingJobStatus::Failed;
            next.failure_code = Some(mapped);
            next.bumped()
        }
        VideoWorkerLocalEditingEvent::Cancelled => {
            if current.status == LocalEditingJobStatus::Cancelled {
                return Ok(current);
            }
            if current.status != LocalEditingJobStatus::Cancelling {
                return Err(conflict());
            }
            let mut next = current;
            next.status = LocalEditingJobStatus::Cancelled;
            next.bumped()
        }
    }
}

fn load(
    store: &VideoJobWorkspaceStore,
    job_id: Uuid,
) -> Result<LocalEditingJobSnapshot, LocalEditingJobLedgerError> {
    let workspace = store.open(job_id).map_err(map_workspace_error)?;
    let snapshot = load_from_workspace(store, &workspace)?;
    if snapshot.job_id != job_id {
        return Err(data_rejected());
    }
    Ok(snapshot)
}

fn load_from_workspace(
    store: &VideoJobWorkspaceStore,
    workspace: &VideoJobWorkspace,
) -> Result<LocalEditingJobSnapshot, LocalEditingJobLedgerError> {
    let bytes = store
        .load_checkpoint(workspace, CHECKPOINT_NAME)
        .map_err(map_workspace_error)?;
    let stored: StoredLocalEditingJob =
        serde_json::from_slice(&bytes).map_err(|_| data_rejected())?;
    LocalEditingJobSnapshot::try_from(stored)
}

fn deserialize_required_option<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

fn save(
    store: &VideoJobWorkspaceStore,
    workspace: &VideoJobWorkspace,
    snapshot: &LocalEditingJobSnapshot,
) -> Result<(), LocalEditingJobLedgerError> {
    snapshot.validate()?;
    let payload =
        serde_json::to_vec(&StoredLocalEditingJob::from(snapshot)).map_err(|_| data_rejected())?;
    store
        .save_checkpoint(workspace, CHECKPOINT_NAME, &payload)
        .map_err(map_workspace_error)
}

fn parse_uuid_v4(value: &str) -> Result<Uuid, LocalEditingJobLedgerError> {
    let parsed = Uuid::parse_str(value).map_err(|_| data_rejected())?;
    if !valid_uuid_v4(parsed) || parsed.hyphenated().to_string() != value {
        return Err(data_rejected());
    }
    Ok(parsed)
}

fn valid_uuid_v4(value: Uuid) -> bool {
    value.get_version_num() == 4 && value.get_variant() == Variant::RFC4122
}

fn map_workspace_error(error: VideoWorkspaceError) -> LocalEditingJobLedgerError {
    match error.code() {
        VideoWorkspaceErrorCode::AlreadyExists => {
            LocalEditingJobLedgerError::new(LocalEditingJobLedgerErrorCode::AlreadyExists)
        }
        VideoWorkspaceErrorCode::ConfigurationInvalid => configuration_invalid(),
        VideoWorkspaceErrorCode::NotFound => {
            LocalEditingJobLedgerError::new(LocalEditingJobLedgerErrorCode::NotFound)
        }
        VideoWorkspaceErrorCode::PathRejected
        | VideoWorkspaceErrorCode::QuotaExceeded
        | VideoWorkspaceErrorCode::StorageUnavailable => storage_unavailable(),
    }
}

fn map_ledger_error(error: LocalEditingJobLedgerError) -> LocalEditingJobSchedulerError {
    let code = match error.code() {
        LocalEditingJobLedgerErrorCode::ConfigurationInvalid => {
            LocalEditingJobSchedulerErrorCode::ConfigurationInvalid
        }
        LocalEditingJobLedgerErrorCode::Conflict
        | LocalEditingJobLedgerErrorCode::AlreadyExists => {
            LocalEditingJobSchedulerErrorCode::Conflict
        }
        LocalEditingJobLedgerErrorCode::DataRejected
        | LocalEditingJobLedgerErrorCode::NotFound
        | LocalEditingJobLedgerErrorCode::StorageUnavailable => {
            LocalEditingJobSchedulerErrorCode::StateUnavailable
        }
    };
    LocalEditingJobSchedulerError::new(code)
}

fn map_worker_error(error: VideoWorkerError) -> LocalEditingJobSchedulerError {
    let code = match error.code() {
        VideoWorkerErrorCode::AuthenticationRejected => {
            LocalEditingJobSchedulerErrorCode::AuthenticationRejected
        }
        VideoWorkerErrorCode::ConfigurationInvalid => {
            LocalEditingJobSchedulerErrorCode::ConfigurationInvalid
        }
        VideoWorkerErrorCode::AlreadyRunning
        | VideoWorkerErrorCode::NotRunning
        | VideoWorkerErrorCode::ProcessUnavailable
        | VideoWorkerErrorCode::RenderRejected
        | VideoWorkerErrorCode::TimedOut
        | VideoWorkerErrorCode::VersionMismatch => {
            LocalEditingJobSchedulerErrorCode::WorkerUnavailable
        }
    };
    LocalEditingJobSchedulerError::new(code)
}

const fn scheduler_conflict() -> LocalEditingJobSchedulerError {
    LocalEditingJobSchedulerError::new(LocalEditingJobSchedulerErrorCode::Conflict)
}

const fn scheduler_state_unavailable() -> LocalEditingJobSchedulerError {
    LocalEditingJobSchedulerError::new(LocalEditingJobSchedulerErrorCode::StateUnavailable)
}

const fn configuration_invalid() -> LocalEditingJobLedgerError {
    LocalEditingJobLedgerError::new(LocalEditingJobLedgerErrorCode::ConfigurationInvalid)
}

const fn conflict() -> LocalEditingJobLedgerError {
    LocalEditingJobLedgerError::new(LocalEditingJobLedgerErrorCode::Conflict)
}

const fn data_rejected() -> LocalEditingJobLedgerError {
    LocalEditingJobLedgerError::new(LocalEditingJobLedgerErrorCode::DataRejected)
}

const fn storage_unavailable() -> LocalEditingJobLedgerError {
    LocalEditingJobLedgerError::new(LocalEditingJobLedgerErrorCode::StorageUnavailable)
}

//! Private, bounded RenderJob workspaces and atomic local video Artifact imports.

use base64::Engine;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read, Seek, SeekFrom, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use uuid::{Uuid, Variant};

const STORE_DIRECTORY: &str = "video-workspaces-v1";
const JOBS_DIRECTORY: &str = "jobs";
const ARTIFACTS_DIRECTORY: &str = "artifacts";
const PUBLISH_STAGING_DIRECTORY: &str = "publish-staging";
const OUTPUTS_DIRECTORY: &str = "outputs";
const CHECKPOINTS_DIRECTORY: &str = "checkpoints";
const WORK_DIRECTORY: &str = "work";
const ARTIFACT_PAYLOAD: &str = "payload";
const ARTIFACT_MANIFEST: &str = "manifest.json";
/// The one role and media type a publish may ever be handed.
///
/// Both creation lines label a finished video exactly this way, and narrowing
/// the publishable set to it is what turns "some local file" into "something
/// this App produced".
const PUBLISHABLE_ROLE: &str = "rendered_video";
const PUBLISHABLE_MEDIA_TYPE: &str = "video/mp4";
/// The extension the executor requires, and it must be the only one.
///
/// `douyin/publish_artifact.py` accepts a path with exactly one suffix drawn
/// from a frozen set. The stored payload has none at all, so a publish is
/// handed a staged copy named `<artifactId>.mp4` — a hyphenated UUID carries
/// no dot of its own, so the staged name has exactly one suffix.
const PUBLISHABLE_EXTENSION: &str = "mp4";
const MAX_WORKSPACE_BYTES: u64 = 64 * 1024 * 1024 * 1024;
const MAX_ARTIFACT_BYTES: u64 = 32 * 1024 * 1024 * 1024;
const MAX_CHECKPOINT_BYTES: u64 = 1024 * 1024;
const MAX_CHECKPOINTS: u16 = 1024;
const MAX_RETENTION_SECONDS: u64 = 365 * 24 * 60 * 60;
const MAX_MEDIA_TYPE_BYTES: usize = 192;
const MAX_ROLE_BYTES: usize = 64;
const MAX_FILE_NAME_BYTES: usize = 128;
const COPY_BUFFER_BYTES: usize = 1024 * 1024;
/// The largest finished film the in-App player will be handed. It is buffered
/// whole and base64 encoded, so a bound belongs here rather than in whichever
/// studio happens to ask.
pub const MAX_RENDERED_VIDEO_READ_BYTES: u64 = 32 * 1024 * 1024;
const WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
static TEMPORARY_SEQUENCE: AtomicU64 = AtomicU64::new(1);

pub fn production_video_workspace_policy() -> VideoJobWorkspacePolicy {
    VideoJobWorkspacePolicy {
        maximum_workspace_bytes: MAX_WORKSPACE_BYTES,
        maximum_artifact_bytes: MAX_ARTIFACT_BYTES,
        maximum_checkpoints: MAX_CHECKPOINTS,
        retention: Duration::from_secs(30 * 24 * 60 * 60),
        minimum_free_bytes: 1024 * 1024 * 1024,
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VideoWorkspaceErrorCode {
    AlreadyExists,
    ConfigurationInvalid,
    NotFound,
    PathRejected,
    QuotaExceeded,
    StorageUnavailable,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct VideoWorkspaceError {
    code: VideoWorkspaceErrorCode,
}

impl VideoWorkspaceError {
    const fn new(code: VideoWorkspaceErrorCode) -> Self {
        Self { code }
    }

    pub const fn code(self) -> VideoWorkspaceErrorCode {
        self.code
    }
}

impl fmt::Debug for VideoWorkspaceError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VideoWorkspaceError")
            .field("code", &self.code)
            .finish()
    }
}

impl fmt::Display for VideoWorkspaceError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Video workspace storage is unavailable")
    }
}

impl std::error::Error for VideoWorkspaceError {}

#[derive(Clone, Copy)]
pub struct VideoJobWorkspacePolicy {
    maximum_workspace_bytes: u64,
    maximum_artifact_bytes: u64,
    maximum_checkpoints: u16,
    retention: Duration,
    minimum_free_bytes: u64,
}

impl VideoJobWorkspacePolicy {
    pub fn new(
        maximum_workspace_bytes: u64,
        maximum_artifact_bytes: u64,
        maximum_checkpoints: u16,
        retention_seconds: u64,
        minimum_free_bytes: u64,
    ) -> Result<Self, VideoWorkspaceError> {
        if !(1..=MAX_WORKSPACE_BYTES).contains(&maximum_workspace_bytes)
            || !(1..=MAX_ARTIFACT_BYTES).contains(&maximum_artifact_bytes)
            || maximum_artifact_bytes > maximum_workspace_bytes
            || !(1..=MAX_CHECKPOINTS).contains(&maximum_checkpoints)
            || !(1..=MAX_RETENTION_SECONDS).contains(&retention_seconds)
        {
            return Err(configuration_invalid());
        }
        Ok(Self {
            maximum_workspace_bytes,
            maximum_artifact_bytes,
            maximum_checkpoints,
            retention: Duration::from_secs(retention_seconds),
            minimum_free_bytes,
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VideoWorkspaceDisposition {
    Keep,
    Delete,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct VideoWorkspaceCleanup {
    removed_workspaces: u32,
}

impl VideoWorkspaceCleanup {
    pub const fn removed_workspaces(self) -> u32 {
        self.removed_workspaces
    }
}

#[derive(Clone)]
pub struct VideoJobWorkspace {
    job_id: Uuid,
    directory: PathBuf,
    identity: DirectoryIdentity,
}

impl VideoJobWorkspace {
    pub const fn job_id(&self) -> Uuid {
        self.job_id
    }
}

impl fmt::Debug for VideoJobWorkspace {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VideoJobWorkspace")
            .field("job_id", &self.job_id)
            .finish()
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct VideoArtifactRecord {
    artifact_id: Uuid,
    job_id: Uuid,
    sha256: String,
    size_bytes: u64,
    media_type: String,
    role: String,
}

pub struct VideoArtifactReader {
    file: File,
    remaining_bytes: u64,
}

/// One finished film, encoded for the in-App player.
///
/// The player is fed a `data:` URL, which is why the bytes travel base64 and
/// not as a path: a path would need the file system opened up to the WebView
/// for a file the App already owns and has already verified.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RenderedVideoArtifactPayload {
    artifact_id: Uuid,
    media_type: &'static str,
    base64: String,
}

impl RenderedVideoArtifactPayload {
    pub const fn artifact_id(&self) -> Uuid {
        self.artifact_id
    }

    pub const fn media_type(&self) -> &'static str {
        self.media_type
    }

    pub fn base64(&self) -> &str {
        &self.base64
    }
}

impl fmt::Debug for VideoArtifactReader {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VideoArtifactReader")
            .field("remaining_bytes", &self.remaining_bytes)
            .finish()
    }
}

impl Read for VideoArtifactReader {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        if self.remaining_bytes == 0 || buffer.is_empty() {
            return Ok(0);
        }
        let maximum = usize::try_from(self.remaining_bytes.min(buffer.len() as u64))
            .expect("bounded by the caller buffer length");
        let read = self.file.read(&mut buffer[..maximum])?;
        if read == 0 {
            return Err(std::io::Error::new(
                ErrorKind::UnexpectedEof,
                "verified video artifact ended early",
            ));
        }
        self.remaining_bytes -= read as u64;
        Ok(read)
    }
}

impl VideoArtifactRecord {
    pub const fn artifact_id(&self) -> Uuid {
        self.artifact_id
    }

    pub const fn job_id(&self) -> Uuid {
        self.job_id
    }

    pub fn sha256(&self) -> &str {
        &self.sha256
    }

    pub const fn size_bytes(&self) -> u64 {
        self.size_bytes
    }

    pub fn media_type(&self) -> &str {
        &self.media_type
    }

    pub fn role(&self) -> &str {
        &self.role
    }

    fn validate(&self) -> Result<(), VideoWorkspaceError> {
        if !valid_uuid_v4(self.artifact_id)
            || !valid_uuid_v4(self.job_id)
            || !valid_digest(&self.sha256)
            || self.size_bytes == 0
            || self.size_bytes > MAX_ARTIFACT_BYTES
            || !valid_media_type(&self.media_type)
            || !valid_role(&self.role)
        {
            return Err(storage_unavailable());
        }
        Ok(())
    }
}

pub struct VideoJobWorkspaceStore {
    jobs_directory: PathBuf,
    artifacts_directory: PathBuf,
    staging_directory: PathBuf,
    jobs_identity: DirectoryIdentity,
    artifacts_identity: DirectoryIdentity,
    staging_identity: DirectoryIdentity,
    policy: VideoJobWorkspacePolicy,
}

/// One finished video, copied out under a name the executor will accept.
///
/// It exists only for the length of one publish. The Artifact it came from is
/// never renamed, moved or exposed: this is a second, disposable file whose
/// digest is re-proved against the manifest before it is handed over.
#[derive(Clone, Eq, PartialEq)]
pub struct StagedPublishArtifact {
    path: PathBuf,
    artifact_id: Uuid,
    sha256: String,
    size_bytes: u64,
}

impl StagedPublishArtifact {
    pub fn path(&self) -> &Path {
        &self.path
    }

    pub const fn artifact_id(&self) -> Uuid {
        self.artifact_id
    }

    pub fn sha256(&self) -> &str {
        &self.sha256
    }

    pub const fn size_bytes(&self) -> u64 {
        self.size_bytes
    }
}

/// A local path is not something to print. Everything but it is safe to see.
impl fmt::Debug for StagedPublishArtifact {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("StagedPublishArtifact")
            .field("artifact_id", &self.artifact_id)
            .field("size_bytes", &self.size_bytes)
            .finish_non_exhaustive()
    }
}

impl fmt::Debug for VideoJobWorkspaceStore {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("VideoJobWorkspaceStore")
    }
}

impl VideoJobWorkspaceStore {
    pub fn initialize(
        app_data_directory: &Path,
        policy: VideoJobWorkspacePolicy,
    ) -> Result<Self, VideoWorkspaceError> {
        if !app_data_directory.is_absolute() {
            return Err(configuration_invalid());
        }
        reject_linked_ancestors(app_data_directory)?;
        require_private_directory(app_data_directory)?;
        let store_directory = app_data_directory.join(STORE_DIRECTORY);
        ensure_private_directory(&store_directory)?;
        let jobs_directory = store_directory.join(JOBS_DIRECTORY);
        let artifacts_directory = store_directory.join(ARTIFACTS_DIRECTORY);
        let staging_directory = store_directory.join(PUBLISH_STAGING_DIRECTORY);
        ensure_private_directory(&jobs_directory)?;
        ensure_private_directory(&artifacts_directory)?;
        ensure_private_directory(&staging_directory)?;
        let jobs_identity = directory_identity(&jobs_directory)?;
        let artifacts_identity = directory_identity(&artifacts_directory)?;
        let staging_identity = directory_identity(&staging_directory)?;
        let store = Self {
            jobs_directory,
            artifacts_directory,
            staging_directory,
            jobs_identity,
            artifacts_identity,
            staging_identity,
            policy,
        };
        store.recover_interrupted_imports()?;
        store.cleanup_invalid_artifacts()?;
        store.cleanup_expired(current_unix_seconds()?)?;
        // A publish that was interrupted by a crash left a whole copy of a
        // video behind. Nothing is ever resumed from it, so it goes now rather
        // than sitting in the App's data directory until someone notices.
        store.discard_staged_publish_artifacts()?;
        Ok(store)
    }

    pub fn create(&self, job_id: Uuid) -> Result<VideoJobWorkspace, VideoWorkspaceError> {
        self.revalidate_roots()?;
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let directory = self.jobs_directory.join(job_id.hyphenated().to_string());
        match fs::symlink_metadata(&directory) {
            Ok(_) => {
                return Err(VideoWorkspaceError::new(
                    VideoWorkspaceErrorCode::AlreadyExists,
                ))
            }
            Err(error) if error.kind() == ErrorKind::NotFound => {}
            Err(_) => return Err(storage_unavailable()),
        }
        create_private_directory(&directory)?;
        let result = (|| {
            for child in [OUTPUTS_DIRECTORY, CHECKPOINTS_DIRECTORY, WORK_DIRECTORY] {
                create_private_directory(&directory.join(child))?;
            }
            let identity = directory_identity(&directory)?;
            Ok(VideoJobWorkspace {
                job_id,
                directory: directory.clone(),
                identity,
            })
        })();
        if result.is_err() {
            let _ = fs::remove_dir_all(&directory);
        }
        result
    }

    pub fn create_new(&self) -> Result<VideoJobWorkspace, VideoWorkspaceError> {
        self.create(generate_uuid_v4()?)
    }

    pub fn open(&self, job_id: Uuid) -> Result<VideoJobWorkspace, VideoWorkspaceError> {
        self.revalidate_roots()?;
        if !valid_uuid_v4(job_id) {
            return Err(configuration_invalid());
        }
        let directory = self.jobs_directory.join(job_id.hyphenated().to_string());
        let metadata = match fs::symlink_metadata(&directory) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == ErrorKind::NotFound => {
                return Err(VideoWorkspaceError::new(VideoWorkspaceErrorCode::NotFound));
            }
            Err(_) => return Err(storage_unavailable()),
        };
        validate_private_directory_metadata(&directory, &metadata)?;
        let workspace = VideoJobWorkspace {
            job_id,
            identity: identity_from_metadata(&metadata),
            directory,
        };
        self.revalidate_workspace(&workspace)?;
        Ok(workspace)
    }

    pub fn list_workspaces(&self) -> Result<Vec<VideoJobWorkspace>, VideoWorkspaceError> {
        self.revalidate_roots()?;
        let mut workspaces = Vec::new();
        for entry in fs::read_dir(&self.jobs_directory).map_err(|_| storage_unavailable())? {
            let entry = entry.map_err(|_| storage_unavailable())?;
            let name = entry.file_name();
            let name = name.to_str().ok_or_else(storage_unavailable)?;
            let job_id = Uuid::parse_str(name).map_err(|_| storage_unavailable())?;
            workspaces.push(self.open(job_id)?);
        }
        workspaces.sort_by_key(VideoJobWorkspace::job_id);
        Ok(workspaces)
    }

    pub fn worker_output_directory(
        &self,
        workspace: &VideoJobWorkspace,
    ) -> Result<PathBuf, VideoWorkspaceError> {
        self.revalidate_workspace(workspace)?;
        Ok(workspace.directory.join(OUTPUTS_DIRECTORY))
    }

    pub fn worker_asset_directory(
        &self,
        workspace: &VideoJobWorkspace,
    ) -> Result<PathBuf, VideoWorkspaceError> {
        self.revalidate_workspace(workspace)?;
        Ok(workspace.directory.join(WORK_DIRECTORY))
    }

    pub fn save_checkpoint(
        &self,
        workspace: &VideoJobWorkspace,
        name: &str,
        payload: &[u8],
    ) -> Result<(), VideoWorkspaceError> {
        self.revalidate_workspace(workspace)?;
        if !valid_checkpoint_name(name)
            || payload.is_empty()
            || payload.len() as u64 > MAX_CHECKPOINT_BYTES
        {
            return Err(path_rejected());
        }
        let checkpoint_directory = workspace.directory.join(CHECKPOINTS_DIRECTORY);
        let destination = checkpoint_directory.join(format!("{name}.checkpoint"));
        let replacing_size = safe_regular_file_metadata(&destination)?
            .map(|metadata| metadata.len())
            .unwrap_or(0);
        let checkpoint_count = count_checkpoint_files(&checkpoint_directory)?;
        if replacing_size == 0 && checkpoint_count >= self.policy.maximum_checkpoints as usize {
            return Err(quota_exceeded());
        }
        let current_usage = workspace_usage(&workspace.directory)?;
        let projected = current_usage
            .checked_sub(replacing_size)
            .and_then(|value| value.checked_add(payload.len() as u64))
            .ok_or_else(quota_exceeded)?;
        if projected > self.policy.maximum_workspace_bytes {
            return Err(quota_exceeded());
        }
        ensure_free_space(&workspace.directory, payload.len() as u64, self.policy)?;
        atomic_write(&checkpoint_directory, &destination, payload)
    }

    pub fn load_checkpoint(
        &self,
        workspace: &VideoJobWorkspace,
        name: &str,
    ) -> Result<Vec<u8>, VideoWorkspaceError> {
        self.revalidate_workspace(workspace)?;
        if !valid_checkpoint_name(name) {
            return Err(path_rejected());
        }
        let path = workspace
            .directory
            .join(CHECKPOINTS_DIRECTORY)
            .join(format!("{name}.checkpoint"));
        let metadata = safe_regular_file_metadata(&path)?
            .ok_or_else(|| VideoWorkspaceError::new(VideoWorkspaceErrorCode::NotFound))?;
        if metadata.len() == 0 || metadata.len() > MAX_CHECKPOINT_BYTES {
            return Err(storage_unavailable());
        }
        read_bounded_file(&path, metadata.len())
    }

    pub fn import_output(
        &self,
        workspace: &VideoJobWorkspace,
        file_name: &str,
        media_type: &str,
        role: &str,
    ) -> Result<VideoArtifactRecord, VideoWorkspaceError> {
        self.revalidate_workspace(workspace)?;
        self.revalidate_roots()?;
        if !valid_file_name(file_name) || !valid_media_type(media_type) || !valid_role(role) {
            return Err(path_rejected());
        }
        let source = workspace.directory.join(OUTPUTS_DIRECTORY).join(file_name);
        let source_metadata = safe_regular_file_metadata(&source)?
            .ok_or_else(|| VideoWorkspaceError::new(VideoWorkspaceErrorCode::NotFound))?;
        if source_metadata.len() == 0
            || source_metadata.len() > self.policy.maximum_artifact_bytes
            || workspace_usage(&workspace.directory)? > self.policy.maximum_workspace_bytes
        {
            return Err(quota_exceeded());
        }
        ensure_free_space(
            &self.artifacts_directory,
            source_metadata.len(),
            self.policy,
        )?;
        let artifact_id = generate_uuid_v4()?;
        let sequence = TEMPORARY_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let temporary = self.artifacts_directory.join(format!(
            ".import-{}-{}-{sequence}",
            artifact_id.hyphenated(),
            std::process::id(),
        ));
        create_private_directory(&temporary)?;
        let final_directory = self
            .artifacts_directory
            .join(artifact_id.hyphenated().to_string());
        let result = (|| {
            let (size_bytes, sha256) = copy_stable_file(
                &source,
                &temporary.join(ARTIFACT_PAYLOAD),
                source_metadata,
                self.policy.maximum_artifact_bytes,
            )?;
            let record = VideoArtifactRecord {
                artifact_id,
                job_id: workspace.job_id,
                sha256,
                size_bytes,
                media_type: media_type.to_owned(),
                role: role.to_owned(),
            };
            record.validate()?;
            let manifest = serde_json::to_vec(&record).map_err(|_| storage_unavailable())?;
            atomic_write(&temporary, &temporary.join(ARTIFACT_MANIFEST), &manifest)?;
            sync_directory(&temporary)?;
            fs::rename(&temporary, &final_directory).map_err(|_| storage_unavailable())?;
            sync_directory(&self.artifacts_directory)?;
            Ok(record)
        })();
        if result.is_err() {
            let _ = fs::remove_dir_all(&temporary);
        }
        result
    }

    pub fn remove_output(
        &self,
        workspace: &VideoJobWorkspace,
        file_name: &str,
    ) -> Result<(), VideoWorkspaceError> {
        self.revalidate_workspace(workspace)?;
        if !valid_file_name(file_name) {
            return Err(path_rejected());
        }
        let output_directory = workspace.directory.join(OUTPUTS_DIRECTORY);
        let output = output_directory.join(file_name);
        safe_regular_file_metadata(&output)?
            .ok_or_else(|| VideoWorkspaceError::new(VideoWorkspaceErrorCode::NotFound))?;
        fs::remove_file(output).map_err(|_| storage_unavailable())?;
        sync_directory(&output_directory)
    }

    pub fn open_artifact(
        &self,
        record: &VideoArtifactRecord,
    ) -> Result<VideoArtifactReader, VideoWorkspaceError> {
        self.revalidate_roots()?;
        record.validate()?;
        let stored = self.load_artifact_record(record.artifact_id)?;
        if &stored != record {
            return Err(storage_unavailable());
        }
        let path = self
            .artifact_directory(record.artifact_id)
            .join(ARTIFACT_PAYLOAD);
        let before = fs::symlink_metadata(&path).map_err(|_| storage_unavailable())?;
        let mut file = open_read_no_follow(&path)?;
        let opened = file.metadata().map_err(|_| storage_unavailable())?;
        if !same_file(&before, &opened) || opened.len() != record.size_bytes {
            return Err(storage_unavailable());
        }
        let mut hasher = Sha256::new();
        let mut total = 0_u64;
        let mut buffer = vec![0_u8; COPY_BUFFER_BYTES];
        loop {
            let read = file.read(&mut buffer).map_err(|_| storage_unavailable())?;
            if read == 0 {
                break;
            }
            total = total
                .checked_add(read as u64)
                .ok_or_else(storage_unavailable)?;
            if total > record.size_bytes {
                return Err(storage_unavailable());
            }
            hasher.update(&buffer[..read]);
        }
        let after = fs::symlink_metadata(&path).map_err(|_| storage_unavailable())?;
        if total != record.size_bytes
            || !same_file(&before, &after)
            || lower_hex(&hasher.finalize()) != record.sha256
        {
            return Err(storage_unavailable());
        }
        file.seek(SeekFrom::Start(0))
            .map_err(|_| storage_unavailable())?;
        Ok(VideoArtifactReader {
            file,
            remaining_bytes: record.size_bytes,
        })
    }

    /// Read one finished film back for playback inside the App.
    ///
    /// Both creation methods import their result here under the same role and
    /// media type, and both studios need the same three steps — find the
    /// record, refuse one too large to hold in memory, verify and encode it —
    /// so those steps live once, next to the store that owns them. The
    /// verification in `open_artifact` is what makes the encoded payload
    /// trustworthy; skipping it would hand the player whatever is on disk now.
    pub fn read_rendered_video_artifact(
        &self,
        artifact_id: Uuid,
    ) -> Result<RenderedVideoArtifactPayload, VideoWorkspaceError> {
        let record = self
            .list_artifacts()?
            .into_iter()
            .find(|record| {
                record.artifact_id() == artifact_id
                    && record.media_type() == PUBLISHABLE_MEDIA_TYPE
                    && record.role() == PUBLISHABLE_ROLE
            })
            .ok_or_else(|| VideoWorkspaceError::new(VideoWorkspaceErrorCode::NotFound))?;
        if record.size_bytes() > MAX_RENDERED_VIDEO_READ_BYTES {
            return Err(quota_exceeded());
        }
        let mut reader = self.open_artifact(&record)?;
        let mut bytes = Vec::with_capacity(record.size_bytes() as usize);
        reader
            .read_to_end(&mut bytes)
            .map_err(|_| storage_unavailable())?;
        Ok(RenderedVideoArtifactPayload {
            artifact_id,
            media_type: PUBLISHABLE_MEDIA_TYPE,
            base64: base64::engine::general_purpose::STANDARD.encode(bytes),
        })
    }

    pub fn list_artifacts(&self) -> Result<Vec<VideoArtifactRecord>, VideoWorkspaceError> {
        self.revalidate_roots()?;
        let mut records = Vec::new();
        for entry in fs::read_dir(&self.artifacts_directory).map_err(|_| storage_unavailable())? {
            let entry = entry.map_err(|_| storage_unavailable())?;
            let name = entry.file_name();
            let name = name.to_str().ok_or_else(storage_unavailable)?;
            if name.starts_with(".import-") {
                return Err(storage_unavailable());
            }
            let artifact_id = Uuid::parse_str(name).map_err(|_| storage_unavailable())?;
            records.push(self.load_artifact_record(artifact_id)?);
        }
        records.sort_by_key(|record| record.artifact_id);
        Ok(records)
    }

    /// Hand one finished video to a publish, under a name the executor takes.
    ///
    /// Three things are settled here rather than anywhere upstream:
    ///
    /// * **Which files may be published at all.** Only a registered Artifact
    ///   labelled as a finished MP4 qualifies, so the publish boundary can no
    ///   longer be pointed at an arbitrary local file.
    /// * **The name.** The stored payload has no extension and the executor
    ///   requires exactly one, so it is copied to `<artifactId>.mp4`. No
    ///   check on the executor side is relaxed to make this fit.
    /// * **That it is a copy.** A hard link would be cheaper and does not
    ///   work: the executor requires a single-link regular file, and linking
    ///   makes both names multiply-linked.
    ///
    /// Only one video is ever staged at a time, so an abandoned publish cannot
    /// leave a second copy behind.
    pub fn stage_publishable_artifact(
        &self,
        artifact_id: Uuid,
    ) -> Result<StagedPublishArtifact, VideoWorkspaceError> {
        self.revalidate_roots()?;
        if !valid_uuid_v4(artifact_id) {
            return Err(configuration_invalid());
        }
        // A video deleted between choosing it and publishing it is its own
        // answer: the operator has to pick again, not see a storage fault.
        let artifact_directory = self.artifact_directory(artifact_id);
        match fs::symlink_metadata(&artifact_directory) {
            Ok(metadata) => validate_private_directory_metadata(&artifact_directory, &metadata)?,
            Err(error) if error.kind() == ErrorKind::NotFound => {
                return Err(VideoWorkspaceError::new(VideoWorkspaceErrorCode::NotFound))
            }
            Err(_) => return Err(storage_unavailable()),
        }
        let record = self.load_artifact_record(artifact_id)?;
        if record.role != PUBLISHABLE_ROLE || record.media_type != PUBLISHABLE_MEDIA_TYPE {
            return Err(configuration_invalid());
        }
        self.discard_staged_publish_artifacts()?;
        ensure_free_space(&self.staging_directory, record.size_bytes, self.policy)?;
        let source = self.artifact_directory(artifact_id).join(ARTIFACT_PAYLOAD);
        let source_metadata =
            safe_regular_file_metadata(&source)?.ok_or_else(storage_unavailable)?;
        let destination = self.staging_directory.join(format!(
            "{}.{PUBLISHABLE_EXTENSION}",
            artifact_id.hyphenated(),
        ));
        let result = (|| {
            let (size_bytes, sha256) = copy_stable_file(
                &source,
                &destination,
                source_metadata,
                self.policy.maximum_artifact_bytes,
            )?;
            // What was copied has to be what the manifest says it is; a payload
            // replaced between listing and staging is refused, not published.
            if size_bytes != record.size_bytes || sha256 != record.sha256 {
                return Err(storage_unavailable());
            }
            sync_directory(&self.staging_directory)?;
            Ok(StagedPublishArtifact {
                path: destination.clone(),
                artifact_id,
                sha256,
                size_bytes,
            })
        })();
        if result.is_err() {
            let _ = fs::remove_file(&destination);
        }
        result
    }

    /// Drop whatever was staged for publishing. Artifacts are never touched.
    ///
    /// Called on every exit path of a publish and once at startup, so it has to
    /// be safe to call when there is nothing staged.
    pub fn discard_staged_publish_artifacts(&self) -> Result<(), VideoWorkspaceError> {
        require_directory_identity(&self.staging_directory, self.staging_identity)?;
        let mut removed = false;
        for entry in fs::read_dir(&self.staging_directory).map_err(|_| storage_unavailable())? {
            let entry = entry.map_err(|_| storage_unavailable())?;
            let path = entry.path();
            // Anything that is not a plain private file here was not put here
            // by this App, and removing it blindly is how a link gets followed.
            safe_regular_file_metadata(&path)?.ok_or_else(path_rejected)?;
            fs::remove_file(&path).map_err(|_| storage_unavailable())?;
            removed = true;
        }
        if removed {
            sync_directory(&self.staging_directory)?;
        }
        Ok(())
    }

    pub fn delete_artifact(&self, artifact_id: Uuid) -> Result<(), VideoWorkspaceError> {
        self.revalidate_roots()?;
        if !valid_uuid_v4(artifact_id) {
            return Err(configuration_invalid());
        }
        let directory = self.artifact_directory(artifact_id);
        match fs::symlink_metadata(&directory) {
            Ok(metadata) => validate_private_directory_metadata(&directory, &metadata)?,
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(()),
            Err(_) => return Err(storage_unavailable()),
        }
        self.load_artifact_record(artifact_id)?;
        fs::remove_dir_all(&directory).map_err(|_| storage_unavailable())?;
        sync_directory(&self.artifacts_directory)
    }

    pub fn finish(
        &self,
        workspace: &VideoJobWorkspace,
        disposition: VideoWorkspaceDisposition,
    ) -> Result<(), VideoWorkspaceError> {
        self.revalidate_workspace(workspace)?;
        match disposition {
            VideoWorkspaceDisposition::Keep => {
                let marker = workspace.directory.join("retained-until");
                let deadline = current_unix_seconds()?
                    .checked_add(self.policy.retention.as_secs())
                    .ok_or_else(storage_unavailable)?;
                atomic_write(
                    &workspace.directory,
                    &marker,
                    deadline.to_string().as_bytes(),
                )
            }
            VideoWorkspaceDisposition::Delete => {
                validate_workspace_tree(&workspace.directory)?;
                fs::remove_dir_all(&workspace.directory).map_err(|_| storage_unavailable())?;
                sync_directory(&self.jobs_directory)
            }
        }
    }

    pub fn cleanup_expired(
        &self,
        now_unix_seconds: u64,
    ) -> Result<VideoWorkspaceCleanup, VideoWorkspaceError> {
        self.revalidate_roots()?;
        let mut removed_workspaces = 0_u32;
        for entry in fs::read_dir(&self.jobs_directory).map_err(|_| storage_unavailable())? {
            let entry = entry.map_err(|_| storage_unavailable())?;
            let name = entry.file_name();
            let name = name.to_str().ok_or_else(path_rejected)?;
            let job_id = Uuid::parse_str(name).map_err(|_| path_rejected())?;
            if !valid_uuid_v4(job_id) {
                return Err(path_rejected());
            }
            let workspace = self.open(job_id)?;
            let marker = workspace.directory.join("retained-until");
            let Some(metadata) = safe_regular_file_metadata(&marker)? else {
                continue;
            };
            if metadata.len() == 0 || metadata.len() > 20 {
                return Err(storage_unavailable());
            }
            let deadline = String::from_utf8(read_bounded_file(&marker, metadata.len())?)
                .map_err(|_| storage_unavailable())?
                .parse::<u64>()
                .map_err(|_| storage_unavailable())?;
            if deadline > now_unix_seconds {
                continue;
            }
            validate_workspace_tree(&workspace.directory)?;
            fs::remove_dir_all(&workspace.directory).map_err(|_| storage_unavailable())?;
            removed_workspaces = removed_workspaces
                .checked_add(1)
                .ok_or_else(storage_unavailable)?;
        }
        if removed_workspaces > 0 {
            sync_directory(&self.jobs_directory)?;
        }
        Ok(VideoWorkspaceCleanup { removed_workspaces })
    }

    fn artifact_directory(&self, artifact_id: Uuid) -> PathBuf {
        self.artifacts_directory
            .join(artifact_id.hyphenated().to_string())
    }

    fn load_artifact_record(
        &self,
        artifact_id: Uuid,
    ) -> Result<VideoArtifactRecord, VideoWorkspaceError> {
        if !valid_uuid_v4(artifact_id) {
            return Err(storage_unavailable());
        }
        let directory = self.artifact_directory(artifact_id);
        validate_artifact_directory(&directory)?;
        let manifest_path = directory.join(ARTIFACT_MANIFEST);
        let metadata =
            safe_regular_file_metadata(&manifest_path)?.ok_or_else(storage_unavailable)?;
        if metadata.len() == 0 || metadata.len() > 4096 {
            return Err(storage_unavailable());
        }
        let manifest = read_bounded_file(&manifest_path, metadata.len())?;
        let record: VideoArtifactRecord =
            serde_json::from_slice(&manifest).map_err(|_| storage_unavailable())?;
        record.validate()?;
        if record.artifact_id != artifact_id
            || record.size_bytes > self.policy.maximum_artifact_bytes
        {
            return Err(storage_unavailable());
        }
        let payload = safe_regular_file_metadata(&directory.join(ARTIFACT_PAYLOAD))?
            .ok_or_else(storage_unavailable)?;
        if payload.len() != record.size_bytes {
            return Err(storage_unavailable());
        }
        Ok(record)
    }

    fn cleanup_invalid_artifacts(&self) -> Result<(), VideoWorkspaceError> {
        self.revalidate_roots()?;
        let mut removed = false;
        for entry in fs::read_dir(&self.artifacts_directory).map_err(|_| storage_unavailable())? {
            let entry = entry.map_err(|_| storage_unavailable())?;
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path).map_err(|_| storage_unavailable())?;
            let artifact_id = entry
                .file_name()
                .to_str()
                .and_then(|name| Uuid::parse_str(name).ok());
            let private_directory = metadata.is_dir() && !unsafe_path_component(&metadata);
            let valid = if private_directory {
                validate_private_directory_metadata(&path, &metadata)?;
                artifact_id
                    .is_some_and(|artifact_id| self.load_artifact_record(artifact_id).is_ok())
            } else {
                false
            };
            if valid {
                continue;
            }
            if private_directory {
                fs::remove_dir_all(&path).map_err(|_| storage_unavailable())?;
            } else {
                fs::remove_file(&path).map_err(|_| storage_unavailable())?;
            }
            removed = true;
        }
        if removed {
            sync_directory(&self.artifacts_directory)?;
        }
        Ok(())
    }

    fn recover_interrupted_imports(&self) -> Result<(), VideoWorkspaceError> {
        self.revalidate_roots()?;
        let mut removed = false;
        for entry in fs::read_dir(&self.artifacts_directory).map_err(|_| storage_unavailable())? {
            let entry = entry.map_err(|_| storage_unavailable())?;
            let name = entry.file_name();
            let name = name.to_str().ok_or_else(storage_unavailable)?;
            if !name.starts_with(".import-") {
                continue;
            }
            if !valid_partial_import_name(name) {
                return Err(storage_unavailable());
            }
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path).map_err(|_| storage_unavailable())?;
            validate_private_directory_metadata(&path, &metadata)?;
            validate_workspace_tree(&path)?;
            fs::remove_dir_all(path).map_err(|_| storage_unavailable())?;
            removed = true;
        }
        if removed {
            sync_directory(&self.artifacts_directory)?;
        }
        Ok(())
    }

    fn revalidate_roots(&self) -> Result<(), VideoWorkspaceError> {
        require_directory_identity(&self.jobs_directory, self.jobs_identity)?;
        require_directory_identity(&self.artifacts_directory, self.artifacts_identity)?;
        require_directory_identity(&self.staging_directory, self.staging_identity)
    }

    fn revalidate_workspace(
        &self,
        workspace: &VideoJobWorkspace,
    ) -> Result<(), VideoWorkspaceError> {
        self.revalidate_roots()?;
        if !valid_uuid_v4(workspace.job_id)
            || workspace.directory
                != self
                    .jobs_directory
                    .join(workspace.job_id.hyphenated().to_string())
        {
            return Err(path_rejected());
        }
        require_directory_identity(&workspace.directory, workspace.identity)?;
        for child in [OUTPUTS_DIRECTORY, CHECKPOINTS_DIRECTORY, WORK_DIRECTORY] {
            require_private_directory(&workspace.directory.join(child))?;
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Eq, PartialEq)]
struct DirectoryIdentity {
    #[cfg(unix)]
    device: u64,
    #[cfg(unix)]
    inode: u64,
}

fn directory_identity(path: &Path) -> Result<DirectoryIdentity, VideoWorkspaceError> {
    let metadata = fs::symlink_metadata(path).map_err(|_| path_rejected())?;
    validate_private_directory_metadata(path, &metadata)?;
    Ok(identity_from_metadata(&metadata))
}

fn identity_from_metadata(metadata: &fs::Metadata) -> DirectoryIdentity {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        DirectoryIdentity {
            device: metadata.dev(),
            inode: metadata.ino(),
        }
    }
    #[cfg(not(unix))]
    {
        let _ = metadata;
        DirectoryIdentity {}
    }
}

fn require_directory_identity(
    path: &Path,
    expected: DirectoryIdentity,
) -> Result<(), VideoWorkspaceError> {
    let actual = directory_identity(path)?;
    if actual != expected {
        return Err(path_rejected());
    }
    Ok(())
}

fn validate_workspace_tree(directory: &Path) -> Result<(), VideoWorkspaceError> {
    for entry in walkdir::WalkDir::new(directory).follow_links(false) {
        let entry = entry.map_err(|_| path_rejected())?;
        let metadata = fs::symlink_metadata(entry.path()).map_err(|_| path_rejected())?;
        if unsafe_path_component(&metadata) || (!metadata.is_dir() && !metadata.is_file()) {
            return Err(path_rejected());
        }
    }
    Ok(())
}

fn workspace_usage(directory: &Path) -> Result<u64, VideoWorkspaceError> {
    let mut total = 0_u64;
    for entry in walkdir::WalkDir::new(directory).follow_links(false) {
        let entry = entry.map_err(|_| path_rejected())?;
        let metadata = fs::symlink_metadata(entry.path()).map_err(|_| path_rejected())?;
        if unsafe_path_component(&metadata) {
            return Err(path_rejected());
        }
        if metadata.is_file() {
            total = total
                .checked_add(metadata.len())
                .ok_or_else(quota_exceeded)?;
        } else if !metadata.is_dir() {
            return Err(path_rejected());
        }
    }
    Ok(total)
}

fn count_checkpoint_files(directory: &Path) -> Result<usize, VideoWorkspaceError> {
    let mut count = 0;
    for entry in fs::read_dir(directory).map_err(|_| storage_unavailable())? {
        let entry = entry.map_err(|_| storage_unavailable())?;
        let name = entry.file_name();
        let name = name.to_str().ok_or_else(path_rejected)?;
        if !name.ends_with(".checkpoint") || safe_regular_file_metadata(&entry.path())?.is_none() {
            return Err(path_rejected());
        }
        count += 1;
    }
    Ok(count)
}

fn validate_artifact_directory(directory: &Path) -> Result<(), VideoWorkspaceError> {
    require_private_directory(directory)?;
    let mut names = Vec::new();
    for entry in fs::read_dir(directory).map_err(|_| storage_unavailable())? {
        let entry = entry.map_err(|_| storage_unavailable())?;
        if safe_regular_file_metadata(&entry.path())?.is_none() {
            return Err(storage_unavailable());
        }
        names.push(entry.file_name());
    }
    names.sort();
    let mut expected = vec![
        std::ffi::OsString::from(ARTIFACT_MANIFEST),
        std::ffi::OsString::from(ARTIFACT_PAYLOAD),
    ];
    expected.sort();
    if names != expected {
        return Err(storage_unavailable());
    }
    Ok(())
}

fn copy_stable_file(
    source: &Path,
    destination: &Path,
    before: fs::Metadata,
    maximum_bytes: u64,
) -> Result<(u64, String), VideoWorkspaceError> {
    let mut source_file = open_read_no_follow(source)?;
    let opened = source_file.metadata().map_err(|_| path_rejected())?;
    if !same_file(&before, &opened) || opened.len() == 0 || opened.len() > maximum_bytes {
        return Err(path_rejected());
    }
    let mut destination_file = open_private_new(destination)?;
    let mut hasher = Sha256::new();
    let mut total = 0_u64;
    let mut buffer = vec![0_u8; COPY_BUFFER_BYTES];
    loop {
        let read = source_file
            .read(&mut buffer)
            .map_err(|_| storage_unavailable())?;
        if read == 0 {
            break;
        }
        total = total.checked_add(read as u64).ok_or_else(quota_exceeded)?;
        if total > maximum_bytes {
            return Err(quota_exceeded());
        }
        destination_file
            .write_all(&buffer[..read])
            .map_err(|_| storage_unavailable())?;
        hasher.update(&buffer[..read]);
    }
    destination_file
        .sync_all()
        .map_err(|_| storage_unavailable())?;
    let after = fs::symlink_metadata(source).map_err(|_| path_rejected())?;
    if total != before.len() || !same_file(&before, &after) {
        return Err(path_rejected());
    }
    Ok((total, lower_hex(&hasher.finalize())))
}

fn atomic_write(
    directory: &Path,
    destination: &Path,
    payload: &[u8],
) -> Result<(), VideoWorkspaceError> {
    require_private_directory(directory)?;
    if destination.parent() != Some(directory) {
        return Err(path_rejected());
    }
    let _ = safe_regular_file_metadata(destination)?;
    let sequence = TEMPORARY_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let temporary = directory.join(format!(".write-{}-{sequence}.tmp", std::process::id()));
    let result = (|| {
        let mut file = open_private_new(&temporary)?;
        file.write_all(payload)
            .and_then(|()| file.sync_all())
            .map_err(|_| storage_unavailable())?;
        drop(file);
        atomic_replace(&temporary, destination)?;
        sync_directory(directory)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

#[cfg(not(target_os = "windows"))]
fn atomic_replace(source: &Path, destination: &Path) -> Result<(), VideoWorkspaceError> {
    fs::rename(source, destination).map_err(|_| storage_unavailable())
}

#[cfg(target_os = "windows")]
fn atomic_replace(source: &Path, destination: &Path) -> Result<(), VideoWorkspaceError> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let wide = |path: &Path| -> Result<Vec<u16>, VideoWorkspaceError> {
        let units = path.as_os_str().encode_wide().collect::<Vec<_>>();
        if units.is_empty() || units.len() >= 32_768 || units.contains(&0) {
            return Err(path_rejected());
        }
        Ok(units.into_iter().chain(std::iter::once(0)).collect())
    };
    let source = wide(source)?;
    let destination = wide(destination)?;
    if unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    } == 0
    {
        Err(storage_unavailable())
    } else {
        Ok(())
    }
}

fn read_bounded_file(path: &Path, expected_bytes: u64) -> Result<Vec<u8>, VideoWorkspaceError> {
    let capacity = usize::try_from(expected_bytes).map_err(|_| storage_unavailable())?;
    let mut payload = Vec::with_capacity(capacity);
    open_read_no_follow(path)?
        .take(expected_bytes.saturating_add(1))
        .read_to_end(&mut payload)
        .map_err(|_| storage_unavailable())?;
    if payload.len() as u64 != expected_bytes {
        return Err(storage_unavailable());
    }
    Ok(payload)
}

fn ensure_free_space(
    directory: &Path,
    required_bytes: u64,
    policy: VideoJobWorkspacePolicy,
) -> Result<(), VideoWorkspaceError> {
    let required = policy
        .minimum_free_bytes
        .checked_add(required_bytes)
        .ok_or_else(storage_unavailable)?;
    if available_bytes(directory)? < required {
        return Err(storage_unavailable());
    }
    Ok(())
}

#[cfg(unix)]
fn available_bytes(path: &Path) -> Result<u64, VideoWorkspaceError> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let encoded = CString::new(path.as_os_str().as_bytes()).map_err(|_| path_rejected())?;
    let mut status = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    if unsafe { libc::statvfs(encoded.as_ptr(), status.as_mut_ptr()) } != 0 {
        return Err(storage_unavailable());
    }
    let status = unsafe { status.assume_init() };
    u64::from(status.f_bavail)
        .checked_mul(status.f_frsize)
        .ok_or_else(storage_unavailable)
}

#[cfg(windows)]
fn available_bytes(path: &Path) -> Result<u64, VideoWorkspaceError> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;

    let encoded = path
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let mut available = 0_u64;
    if unsafe {
        GetDiskFreeSpaceExW(
            encoded.as_ptr(),
            &mut available,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    } == 0
    {
        Err(storage_unavailable())
    } else {
        Ok(available)
    }
}

#[cfg(not(any(unix, windows)))]
fn available_bytes(_path: &Path) -> Result<u64, VideoWorkspaceError> {
    Err(storage_unavailable())
}

fn ensure_private_directory(path: &Path) -> Result<(), VideoWorkspaceError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => validate_private_directory_metadata(path, &metadata),
        Err(error) if error.kind() == ErrorKind::NotFound => create_private_directory(path),
        Err(_) => Err(storage_unavailable()),
    }
}

fn create_private_directory(path: &Path) -> Result<(), VideoWorkspaceError> {
    let mut builder = fs::DirBuilder::new();
    builder.recursive(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(path).map_err(|_| storage_unavailable())?;
    require_private_directory(path)
}

fn require_private_directory(path: &Path) -> Result<(), VideoWorkspaceError> {
    let metadata = fs::symlink_metadata(path).map_err(|_| path_rejected())?;
    validate_private_directory_metadata(path, &metadata)
}

fn validate_private_directory_metadata(
    _path: &Path,
    metadata: &fs::Metadata,
) -> Result<(), VideoWorkspaceError> {
    if unsafe_path_component(metadata) || !metadata.is_dir() {
        return Err(path_rejected());
    }
    #[cfg(unix)]
    {
        use std::os::fd::AsRawFd;
        use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};

        if metadata.permissions().mode() & 0o7777 != 0o700 {
            let directory = OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
                .open(_path)
                .map_err(|_| storage_unavailable())?;
            let opened = directory.metadata().map_err(|_| storage_unavailable())?;
            if !opened.is_dir() || opened.dev() != metadata.dev() || opened.ino() != metadata.ino()
            {
                return Err(path_rejected());
            }
            if unsafe { libc::fchmod(directory.as_raw_fd(), 0o700) } != 0 {
                return Err(storage_unavailable());
            }
            let repaired = directory.metadata().map_err(|_| storage_unavailable())?;
            if repaired.dev() != metadata.dev()
                || repaired.ino() != metadata.ino()
                || repaired.permissions().mode() & 0o7777 != 0o700
            {
                return Err(storage_unavailable());
            }
        }
    }
    Ok(())
}

fn safe_regular_file_metadata(path: &Path) -> Result<Option<fs::Metadata>, VideoWorkspaceError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if unsafe_path_component(&metadata) || !metadata.is_file() {
                return Err(path_rejected());
            }
            Ok(Some(metadata))
        }
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(_) => Err(storage_unavailable()),
    }
}

fn reject_linked_ancestors(path: &Path) -> Result<(), VideoWorkspaceError> {
    for ancestor in path.ancestors() {
        let metadata = fs::symlink_metadata(ancestor).map_err(|_| path_rejected())?;
        if unsafe_path_component(&metadata) {
            return Err(path_rejected());
        }
    }
    Ok(())
}

fn unsafe_path_component(metadata: &fs::Metadata) -> bool {
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT != 0
    }
    #[cfg(not(windows))]
    {
        let _ = WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT;
        false
    }
}

fn open_private_new(path: &Path) -> Result<File, VideoWorkspaceError> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::FILE_FLAG_OPEN_REPARSE_POINT;
        options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }
    options.open(path).map_err(|_| storage_unavailable())
}

fn open_read_no_follow(path: &Path) -> Result<File, VideoWorkspaceError> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::FILE_FLAG_OPEN_REPARSE_POINT;
        options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }
    options.open(path).map_err(|_| path_rejected())
}

#[cfg(unix)]
fn same_file(left: &fs::Metadata, right: &fs::Metadata) -> bool {
    use std::os::unix::fs::MetadataExt;
    left.dev() == right.dev() && left.ino() == right.ino() && left.len() == right.len()
}

#[cfg(not(unix))]
fn same_file(left: &fs::Metadata, right: &fs::Metadata) -> bool {
    left.len() == right.len()
        && left.modified().ok() == right.modified().ok()
        && left.created().ok() == right.created().ok()
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<(), VideoWorkspaceError> {
    File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(|_| storage_unavailable())
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<(), VideoWorkspaceError> {
    Ok(())
}

fn valid_uuid_v4(value: Uuid) -> bool {
    value.get_version_num() == 4 && value.get_variant() == Variant::RFC4122
}

/// The crate's one UUIDv4 source, so nothing has to grow a second one.
pub fn generate_uuid_v4() -> Result<Uuid, VideoWorkspaceError> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes).map_err(|_| storage_unavailable())?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(Uuid::from_bytes(bytes))
}

fn valid_file_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_FILE_NAME_BYTES
        && Path::new(value).components().count() == 1
        && matches!(
            Path::new(value).components().next(),
            Some(Component::Normal(_))
        )
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

fn valid_checkpoint_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
}

fn valid_partial_import_name(value: &str) -> bool {
    let Some(remainder) = value.strip_prefix(".import-") else {
        return false;
    };
    if remainder.len() < 40 {
        return false;
    }
    let (uuid, suffix) = remainder.split_at(36);
    if Uuid::parse_str(uuid).is_err() {
        return false;
    }
    let mut numbers = suffix.strip_prefix('-').unwrap_or_default().split('-');
    matches!(
        (numbers.next(), numbers.next(), numbers.next()),
        (Some(process), Some(sequence), None)
            if !process.is_empty()
                && !sequence.is_empty()
                && process.bytes().all(|byte| byte.is_ascii_digit())
                && sequence.bytes().all(|byte| byte.is_ascii_digit())
    )
}

fn valid_media_type(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_MEDIA_TYPE_BYTES
        && value.matches('/').count() == 1
        && value.bytes().all(|byte| {
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || matches!(byte, b'/' | b'.' | b'+' | b'-')
        })
}

fn valid_role(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_ROLE_BYTES
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_')
}

fn valid_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn lower_hex(bytes: &[u8]) -> String {
    use fmt::Write as _;
    let mut value = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        write!(&mut value, "{byte:02x}").expect("writing to a String cannot fail");
    }
    value
}

fn current_unix_seconds() -> Result<u64, VideoWorkspaceError> {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .map_err(|_| storage_unavailable())
}

const fn configuration_invalid() -> VideoWorkspaceError {
    VideoWorkspaceError::new(VideoWorkspaceErrorCode::ConfigurationInvalid)
}

const fn path_rejected() -> VideoWorkspaceError {
    VideoWorkspaceError::new(VideoWorkspaceErrorCode::PathRejected)
}

const fn quota_exceeded() -> VideoWorkspaceError {
    VideoWorkspaceError::new(VideoWorkspaceErrorCode::QuotaExceeded)
}

const fn storage_unavailable() -> VideoWorkspaceError {
    VideoWorkspaceError::new(VideoWorkspaceErrorCode::StorageUnavailable)
}

//! One-shot bridge from the native App boundary to the signed Local Executor.
//!
//! The child receives one bounded JSON document through stdin and returns one
//! bounded, provider-neutral result through stdout. Stderr is discarded and no
//! error type stores request bytes, credentials or local paths.

use crate::managed_process_tree::{configure_managed_process, ManagedProcessTree};
use crate::video_editing_service_settings::{AliyunEditingRegion, EditingServiceCredential};
use crate::video_editing_workspace::{
    EditingFailureCode, EditingJobSnapshot, EditingJobStatus, EditingTimelineSnapshot,
};
use crate::video_job_workspace::StagedEditingArtifact;
use serde::{Deserialize, Serialize};
use std::fmt::{self, Display, Formatter};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};
use uuid::{Uuid, Variant};
use zeroize::Zeroizing;

const ARGUMENT: &str = "--execute-video-editing";
const MAX_REQUEST_BYTES: usize = 1024 * 1024;
const MAX_RESPONSE_BYTES: u64 = 64 * 1024;
const MAX_OUTPUT_BYTES: u64 = 32 * 1024 * 1024 * 1024;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ChildCredential<'a> {
    access_key_id: &'a str,
    access_key_secret: &'a str,
    region: AliyunEditingRegion,
    oss_bucket: &'a str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ChildAsset<'a> {
    artifact_id: Uuid,
    path: &'a Path,
    sha256: &'a str,
    size_bytes: u64,
    extension: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ChildRequest<'a> {
    schema_version: u8,
    credential: ChildCredential<'a>,
    editing_job_id: Uuid,
    project_id: Uuid,
    timeline: &'a EditingTimelineSnapshot,
    assets: Vec<ChildAsset<'a>>,
    input_directory: &'a Path,
    output_directory: &'a Path,
    output_width: u16,
    output_height: u16,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VideoEditingChildErrorKind {
    InvalidResponse,
    NotStarted,
    OutcomeUncertain,
    RequestRejected,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct VideoEditingChildError {
    kind: VideoEditingChildErrorKind,
}

impl VideoEditingChildError {
    const fn new(kind: VideoEditingChildErrorKind) -> Self {
        Self { kind }
    }

    pub const fn kind(self) -> VideoEditingChildErrorKind {
        self.kind
    }
}

impl fmt::Debug for VideoEditingChildError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VideoEditingChildError")
            .field("kind", &self.kind)
            .finish()
    }
}

impl Display for VideoEditingChildError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str("video editing child operation unavailable")
    }
}

impl std::error::Error for VideoEditingChildError {}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum VideoEditingChildStatus {
    Succeeded,
    Failed,
    OutcomeUncertain,
}

#[derive(Clone, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct VideoEditingChildResult {
    schema_version: u8,
    status: VideoEditingChildStatus,
    editing_job_id: Uuid,
    output_path: Option<PathBuf>,
    output_sha256: Option<String>,
    output_size_bytes: Option<u64>,
    failure_code: Option<EditingFailureCode>,
}

impl fmt::Debug for VideoEditingChildResult {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VideoEditingChildResult")
            .field("status", &self.status)
            .field("editing_job_id", &self.editing_job_id)
            .field("output_size_bytes", &self.output_size_bytes)
            .field("failure_code", &self.failure_code)
            .finish_non_exhaustive()
    }
}

impl VideoEditingChildResult {
    pub const fn status(&self) -> VideoEditingChildStatus {
        self.status
    }

    pub fn output_path(&self) -> Option<&Path> {
        self.output_path.as_deref()
    }

    pub fn output_sha256(&self) -> Option<&str> {
        self.output_sha256.as_deref()
    }

    pub const fn output_size_bytes(&self) -> Option<u64> {
        self.output_size_bytes
    }

    pub const fn failure_code(&self) -> Option<EditingFailureCode> {
        self.failure_code
    }
}

pub fn build_video_editing_child_request(
    credential: &EditingServiceCredential,
    job: &EditingJobSnapshot,
    timeline: &EditingTimelineSnapshot,
    assets: &[StagedEditingArtifact],
    input_directory: &Path,
    output_directory: &Path,
    output_size: (u16, u16),
) -> Result<Zeroizing<Vec<u8>>, VideoEditingChildError> {
    let invalid = || VideoEditingChildError::new(VideoEditingChildErrorKind::NotStarted);
    let (output_width, output_height) = output_size;
    if job.status != EditingJobStatus::Queued
        || job.project_id != timeline.project_id
        || job.timeline_id != timeline.timeline_id
        || job.timeline_revision != timeline.revision
        || assets.len() != job.input_artifact_ids.len()
        || !(128..=4096).contains(&output_width)
        || !(128..=4096).contains(&output_height)
        || !input_directory.is_absolute()
        || !output_directory.is_absolute()
        || input_directory == output_directory
        || input_directory.starts_with(output_directory)
        || output_directory.starts_with(input_directory)
    {
        return Err(invalid());
    }
    let mut child_assets = Vec::with_capacity(assets.len());
    for (expected, asset) in job.input_artifact_ids.iter().zip(assets) {
        if *expected != asset.artifact_id() || asset.path().parent() != Some(input_directory) {
            return Err(invalid());
        }
        child_assets.push(ChildAsset {
            artifact_id: asset.artifact_id(),
            path: asset.path(),
            sha256: asset.sha256(),
            size_bytes: asset.size_bytes(),
            extension: asset.extension(),
        });
    }
    let request = ChildRequest {
        schema_version: 1,
        credential: ChildCredential {
            access_key_id: credential.access_key_id(),
            access_key_secret: credential.access_key_secret(),
            region: credential.region(),
            oss_bucket: credential.oss_bucket(),
        },
        editing_job_id: job.editing_job_id,
        project_id: job.project_id,
        timeline,
        assets: child_assets,
        input_directory,
        output_directory,
        output_width,
        output_height,
    };
    let payload = serde_json::to_vec(&request).map_err(|_| invalid())?;
    if payload.is_empty() || payload.len() > MAX_REQUEST_BYTES {
        return Err(invalid());
    }
    Ok(Zeroizing::new(payload))
}

pub fn run_video_editing_child(
    entrypoint: &Path,
    request: &[u8],
    editing_job_id: Uuid,
    output_directory: &Path,
    budget: Duration,
) -> Result<VideoEditingChildResult, VideoEditingChildError> {
    if !entrypoint.is_absolute()
        || request.is_empty()
        || request.len() > MAX_REQUEST_BYTES
        || !valid_uuid_v4(editing_job_id)
        || !output_directory.is_absolute()
        || budget.is_zero()
        || budget > Duration::from_secs(24 * 60 * 60)
    {
        return Err(VideoEditingChildError::new(
            VideoEditingChildErrorKind::NotStarted,
        ));
    }
    let mut command = Command::new(entrypoint);
    command
        .arg(ARGUMENT)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    configure_managed_process(&mut command);
    let mut child = command
        .spawn()
        .map_err(|_| VideoEditingChildError::new(VideoEditingChildErrorKind::NotStarted))?;
    let mut process_tree = match ManagedProcessTree::attach(&child) {
        Ok(process_tree) => process_tree,
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(VideoEditingChildError::new(
                VideoEditingChildErrorKind::NotStarted,
            ));
        }
    };
    let written = child
        .stdin
        .take()
        .is_some_and(|mut stdin| stdin.write_all(request).is_ok() && stdin.flush().is_ok());
    if !written {
        terminate_child_tree(&mut process_tree, &mut child);
        return Err(VideoEditingChildError::new(
            VideoEditingChildErrorKind::NotStarted,
        ));
    }
    let deadline = Instant::now() + budget;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => {
                std::thread::sleep(Duration::from_millis(100));
            }
            Ok(None) | Err(_) => {
                terminate_child_tree(&mut process_tree, &mut child);
                return Err(VideoEditingChildError::new(
                    VideoEditingChildErrorKind::OutcomeUncertain,
                ));
            }
        }
    };
    if process_tree.terminate().is_err() {
        let _ = child.kill();
        let _ = child.wait();
        return Err(VideoEditingChildError::new(
            VideoEditingChildErrorKind::OutcomeUncertain,
        ));
    }
    let mut stdout = child
        .stdout
        .take()
        .ok_or_else(|| VideoEditingChildError::new(VideoEditingChildErrorKind::InvalidResponse))?;
    let mut response = Vec::new();
    Read::take(&mut stdout, MAX_RESPONSE_BYTES + 1)
        .read_to_end(&mut response)
        .map_err(|_| VideoEditingChildError::new(VideoEditingChildErrorKind::InvalidResponse))?;
    if !status.success() {
        let kind = if status.code() == Some(2) {
            VideoEditingChildErrorKind::RequestRejected
        } else {
            VideoEditingChildErrorKind::OutcomeUncertain
        };
        return Err(VideoEditingChildError::new(kind));
    }
    if response.is_empty() || response.len() as u64 > MAX_RESPONSE_BYTES {
        return Err(VideoEditingChildError::new(
            VideoEditingChildErrorKind::InvalidResponse,
        ));
    }
    let result: VideoEditingChildResult = serde_json::from_slice(&response)
        .map_err(|_| VideoEditingChildError::new(VideoEditingChildErrorKind::InvalidResponse))?;
    validate_result(&result, editing_job_id, output_directory)?;
    Ok(result)
}

fn terminate_child_tree(process_tree: &mut ManagedProcessTree, child: &mut std::process::Child) {
    let _ = process_tree.terminate();
    let _ = child.kill();
    let _ = child.wait();
}

fn validate_result(
    result: &VideoEditingChildResult,
    editing_job_id: Uuid,
    output_directory: &Path,
) -> Result<(), VideoEditingChildError> {
    let invalid = || VideoEditingChildError::new(VideoEditingChildErrorKind::InvalidResponse);
    if result.schema_version != 1 || result.editing_job_id != editing_job_id {
        return Err(invalid());
    }
    match result.status {
        VideoEditingChildStatus::Succeeded => {
            let (Some(path), Some(digest), Some(size_bytes)) = (
                result.output_path.as_ref(),
                result.output_sha256.as_deref(),
                result.output_size_bytes,
            ) else {
                return Err(invalid());
            };
            let valid_name = path
                .file_stem()
                .and_then(|value| value.to_str())
                .and_then(|value| Uuid::parse_str(value).ok())
                .is_some_and(valid_uuid_v4)
                && path.extension().and_then(|value| value.to_str()) == Some("mp4");
            if !path.is_absolute()
                || path.parent() != Some(output_directory)
                || !valid_name
                || !valid_digest(digest)
                || !(1..=MAX_OUTPUT_BYTES).contains(&size_bytes)
                || result.failure_code.is_some()
            {
                return Err(invalid());
            }
        }
        VideoEditingChildStatus::Failed => {
            if result.output_path.is_some()
                || result.output_sha256.is_some()
                || result.output_size_bytes.is_some()
                || result.failure_code.is_none()
            {
                return Err(invalid());
            }
        }
        VideoEditingChildStatus::OutcomeUncertain => {
            if result.output_path.is_some()
                || result.output_sha256.is_some()
                || result.output_size_bytes.is_some()
                || result.failure_code.is_some()
            {
                return Err(invalid());
            }
        }
    }
    Ok(())
}

fn valid_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn valid_uuid_v4(value: Uuid) -> bool {
    value.get_version_num() == 4 && value.get_variant() == Variant::RFC4122
}

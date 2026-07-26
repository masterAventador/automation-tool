use std::error::Error;
use std::fmt::{Debug, Display, Formatter};
use std::fs;
use std::path::Path;
use std::sync::Mutex;

use semver::Version;
use serde::{Deserialize, Serialize};

pub use crate::app_updates::UpdatePolicyAction;
use crate::app_updates::{
    UpdateArchitecture, UpdateDecision, UpdatePolicy, UpdateRelease, UpdateTarget,
    MAX_UPDATE_ARTIFACT_BYTES,
};
use crate::secure_store::{AppDataSecretStore, SecretStore};

const UPDATE_POLICY_SCHEMA_VERSION: u8 = 1;
const PREVIOUS_UPDATE_POLICY_SCHEMA_VERSION: u8 = 0;
const UPDATE_POLICY_DIRECTORY: &str = "app-updates";
const UPDATE_POLICY_FILE: &str = "update-policy-v1";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum UpdatePolicyErrorCode {
    ConfigurationInvalid,
    StorageUnavailable,
    CandidateUnavailable,
    DecisionNotAllowed,
    ReleaseStale,
    ReleaseMutation,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct UpdatePolicyError {
    code: UpdatePolicyErrorCode,
}

impl UpdatePolicyError {
    pub fn code(self) -> UpdatePolicyErrorCode {
        self.code
    }

    fn new(code: UpdatePolicyErrorCode) -> Self {
        Self { code }
    }

    fn configuration_invalid() -> Self {
        Self::new(UpdatePolicyErrorCode::ConfigurationInvalid)
    }

    fn storage_unavailable() -> Self {
        Self::new(UpdatePolicyErrorCode::StorageUnavailable)
    }

    fn candidate_unavailable() -> Self {
        Self::new(UpdatePolicyErrorCode::CandidateUnavailable)
    }

    fn decision_not_allowed() -> Self {
        Self::new(UpdatePolicyErrorCode::DecisionNotAllowed)
    }

    fn release_stale() -> Self {
        Self::new(UpdatePolicyErrorCode::ReleaseStale)
    }

    fn release_mutation() -> Self {
        Self::new(UpdatePolicyErrorCode::ReleaseMutation)
    }
}

impl Display for UpdatePolicyError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("update policy unavailable")
    }
}

impl Error for UpdatePolicyError {}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdatePolicyEvaluation {
    release: UpdateRelease,
    action: UpdatePolicyAction,
}

impl UpdatePolicyEvaluation {
    pub fn release(&self) -> &UpdateRelease {
        &self.release
    }

    pub fn action(&self) -> UpdatePolicyAction {
        self.action
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdatePolicyRecord {
    minimum_version: String,
    highest_observed_version: Option<String>,
    decision: Option<UpdateDecision>,
    revision: u64,
}

impl UpdatePolicyRecord {
    pub fn minimum_version(&self) -> &str {
        &self.minimum_version
    }

    pub fn highest_observed_version(&self) -> Option<&str> {
        self.highest_observed_version.as_deref()
    }

    pub fn decision(&self) -> Option<UpdateDecision> {
        self.decision
    }

    pub fn revision(&self) -> u64 {
        self.revision
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct StoredReleaseIdentity {
    version: String,
    channel: String,
    policy: UpdatePolicy,
    target: UpdateTarget,
    arch: UpdateArchitecture,
    sha256: String,
    size_bytes: u64,
}

impl StoredReleaseIdentity {
    fn from_release(release: &UpdateRelease) -> Self {
        Self {
            version: release.version.as_str().to_owned(),
            channel: release.channel.as_str().to_owned(),
            policy: release.policy,
            target: release.artifact.target,
            arch: release.artifact.arch,
            sha256: release.artifact.sha256.clone(),
            size_bytes: release.artifact.size_bytes,
        }
    }

    fn parsed_version(&self) -> Result<Version, UpdatePolicyError> {
        parse_canonical_version(&self.version).map_err(|_| UpdatePolicyError::storage_unavailable())
    }

    fn validate(&self, configured_channel: &str) -> Result<(), UpdatePolicyError> {
        self.parsed_version()?;
        if self.channel != configured_channel
            || !is_lower_hex_digest(&self.sha256)
            || !(1..=MAX_UPDATE_ARTIFACT_BYTES).contains(&self.size_bytes)
        {
            return Err(UpdatePolicyError::storage_unavailable());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct StoredDecision {
    version: String,
    decision: UpdateDecision,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct StoredUpdatePolicy {
    schema_version: u8,
    configured_channel: String,
    minimum_version: String,
    highest_observed: Option<StoredReleaseIdentity>,
    decision: Option<StoredDecision>,
    revision: u64,
}

impl StoredUpdatePolicy {
    fn new(configured_channel: &str, current_version: &Version) -> Self {
        Self {
            schema_version: UPDATE_POLICY_SCHEMA_VERSION,
            configured_channel: configured_channel.to_owned(),
            minimum_version: current_version.to_string(),
            highest_observed: None,
            decision: None,
            revision: 0,
        }
    }

    fn validate(&self) -> Result<(), UpdatePolicyError> {
        self.validate_schema(UPDATE_POLICY_SCHEMA_VERSION)
    }

    fn validate_schema(&self, expected_schema: u8) -> Result<(), UpdatePolicyError> {
        if self.schema_version != expected_schema
            || !is_safe_channel(&self.configured_channel)
            || self.revision == 0
        {
            return Err(UpdatePolicyError::storage_unavailable());
        }
        let minimum = parse_canonical_version(&self.minimum_version)
            .map_err(|_| UpdatePolicyError::storage_unavailable())?;
        match (&self.highest_observed, &self.decision) {
            (None, None) => Ok(()),
            (None, Some(_)) => Err(UpdatePolicyError::storage_unavailable()),
            (Some(observed), decision) => {
                observed.validate(&self.configured_channel)?;
                if observed.parsed_version()? <= minimum {
                    return Err(UpdatePolicyError::storage_unavailable());
                }
                if observed.policy == UpdatePolicy::Forced && decision.is_some() {
                    return Err(UpdatePolicyError::storage_unavailable());
                }
                if let Some(decision) = decision {
                    parse_canonical_version(&decision.version)
                        .map_err(|_| UpdatePolicyError::storage_unavailable())?;
                    if decision.version != observed.version {
                        return Err(UpdatePolicyError::storage_unavailable());
                    }
                }
                Ok(())
            }
        }
    }

    fn record(&self) -> UpdatePolicyRecord {
        UpdatePolicyRecord {
            minimum_version: self.minimum_version.clone(),
            highest_observed_version: self
                .highest_observed
                .as_ref()
                .map(|observed| observed.version.clone()),
            decision: self.decision.as_ref().map(|decision| decision.decision),
            revision: self.revision,
        }
    }
}

struct UpdatePolicyState {
    store: AppDataSecretStore,
    document: StoredUpdatePolicy,
    active_release: Option<UpdateRelease>,
}

pub struct UpdatePolicyService {
    state: Mutex<UpdatePolicyState>,
}

impl Debug for UpdatePolicyService {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("UpdatePolicyService(<redacted>)")
    }
}

impl UpdatePolicyService {
    pub fn initialize(
        app_data_directory: &Path,
        current_version: &str,
        configured_channel: &str,
    ) -> Result<Self, UpdatePolicyError> {
        if !app_data_directory.is_absolute() {
            return Err(UpdatePolicyError::storage_unavailable());
        }
        validate_app_data_root(app_data_directory)?;
        let current_version = parse_canonical_version(current_version)
            .map_err(|_| UpdatePolicyError::configuration_invalid())?;
        if !is_safe_channel(configured_channel) {
            return Err(UpdatePolicyError::configuration_invalid());
        }
        let store = AppDataSecretStore::new(
            &app_data_directory.join(UPDATE_POLICY_DIRECTORY),
            UPDATE_POLICY_FILE,
        )
        .map_err(|_| UpdatePolicyError::storage_unavailable())?;
        let stored = store
            .load()
            .map_err(|_| UpdatePolicyError::storage_unavailable())?;
        let (mut document, mut needs_save) = match stored
            .as_deref()
            .map(|stored| usable_document(stored, configured_channel))
        {
            Some(Some((document, repaired))) => {
                if repaired {
                    crate::app_logging::record(
                        crate::app_logging::DesktopLogEvent::UpdatePolicyDocumentMigrated,
                    );
                }
                (document, repaired)
            }
            Some(None) => {
                crate::app_logging::record(
                    crate::app_logging::DesktopLogEvent::UpdatePolicyDocumentReplaced,
                );
                (
                    StoredUpdatePolicy::new(configured_channel, &current_version),
                    true,
                )
            }
            None => (
                StoredUpdatePolicy::new(configured_channel, &current_version),
                true,
            ),
        };

        let minimum = parse_canonical_version(&document.minimum_version)
            .map_err(|_| UpdatePolicyError::storage_unavailable())?;
        if current_version > minimum {
            document.minimum_version = current_version.to_string();
            if document
                .highest_observed
                .as_ref()
                .map(StoredReleaseIdentity::parsed_version)
                .transpose()?
                .is_some_and(|observed| observed <= current_version)
            {
                document.highest_observed = None;
                document.decision = None;
            }
            needs_save = true;
        }
        if needs_save {
            document = saved_document(&store, &document)?;
        }

        Ok(Self {
            state: Mutex::new(UpdatePolicyState {
                store,
                document,
                active_release: None,
            }),
        })
    }

    pub fn observe_release(
        &self,
        release: UpdateRelease,
    ) -> Result<UpdatePolicyEvaluation, UpdatePolicyError> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| UpdatePolicyError::storage_unavailable())?;
        if release.channel.as_str() != state.document.configured_channel {
            return Err(UpdatePolicyError::release_mutation());
        }
        let release_version = parse_canonical_version(release.version.as_str())
            .map_err(|_| UpdatePolicyError::release_mutation())?;
        let minimum = parse_canonical_version(&state.document.minimum_version)
            .map_err(|_| UpdatePolicyError::storage_unavailable())?;
        if release_version <= minimum {
            return Err(UpdatePolicyError::release_stale());
        }
        let identity = StoredReleaseIdentity::from_release(&release);
        identity
            .validate(&state.document.configured_channel)
            .map_err(|_| UpdatePolicyError::release_mutation())?;

        let replace_observed = match state.document.highest_observed.as_ref() {
            None => true,
            Some(observed) => {
                let observed_version = observed.parsed_version()?;
                if release_version < observed_version {
                    return Err(UpdatePolicyError::release_stale());
                }
                if release_version == observed_version {
                    if observed != &identity {
                        return Err(UpdatePolicyError::release_mutation());
                    }
                    false
                } else {
                    true
                }
            }
        };

        if replace_observed {
            let mut next = state.document.clone();
            next.highest_observed = Some(identity);
            next.decision = None;
            state.document = saved_document(&state.store, &next)?;
        }
        let action = action_for_observation(&state.document, release.policy)?;
        state.active_release = matches!(
            action,
            UpdatePolicyAction::Prompt | UpdatePolicyAction::Forced
        )
        .then(|| release.clone());
        Ok(UpdatePolicyEvaluation { release, action })
    }

    pub fn decide(
        &self,
        decision: UpdateDecision,
    ) -> Result<UpdatePolicyEvaluation, UpdatePolicyError> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| UpdatePolicyError::storage_unavailable())?;
        let release = state
            .active_release
            .clone()
            .ok_or_else(UpdatePolicyError::candidate_unavailable)?;
        if release.policy == UpdatePolicy::Forced {
            return Err(UpdatePolicyError::decision_not_allowed());
        }
        let observed = state
            .document
            .highest_observed
            .as_ref()
            .ok_or_else(UpdatePolicyError::candidate_unavailable)?;
        if observed != &StoredReleaseIdentity::from_release(&release) {
            return Err(UpdatePolicyError::release_mutation());
        }
        let mut next = state.document.clone();
        next.decision = Some(StoredDecision {
            version: release.version.as_str().to_owned(),
            decision,
        });
        state.document = saved_document(&state.store, &next)?;
        let action = match decision {
            UpdateDecision::InstallNow => UpdatePolicyAction::InstallRequested,
            UpdateDecision::Defer => UpdatePolicyAction::Deferred,
            UpdateDecision::SkipVersion => UpdatePolicyAction::Skipped,
        };
        state.active_release = None;
        Ok(UpdatePolicyEvaluation { release, action })
    }

    pub fn record(&self) -> Result<UpdatePolicyRecord, UpdatePolicyError> {
        self.state
            .lock()
            .map(|state| state.document.record())
            .map_err(|_| UpdatePolicyError::storage_unavailable())
    }
}

fn action_for_observation(
    document: &StoredUpdatePolicy,
    policy: UpdatePolicy,
) -> Result<UpdatePolicyAction, UpdatePolicyError> {
    if policy == UpdatePolicy::Forced {
        return if document.decision.is_none() {
            Ok(UpdatePolicyAction::Forced)
        } else {
            Err(UpdatePolicyError::storage_unavailable())
        };
    }
    Ok(
        match document.decision.as_ref().map(|decision| decision.decision) {
            None | Some(UpdateDecision::Defer) => UpdatePolicyAction::Prompt,
            Some(UpdateDecision::SkipVersion) => UpdatePolicyAction::Suppressed,
            Some(UpdateDecision::InstallNow) => UpdatePolicyAction::InstallRequested,
        },
    )
}

/// The stored policy this build can go on using, and whether reading it had to
/// repair anything.
///
/// `None` means the file has to be replaced. That covers a schema this build
/// does not know - which is what a rollback from a newer build leaves behind -
/// a file that is not a document at all, and a document that no longer holds
/// its own invariants. None of those is worth refusing to launch over. All the
/// file carries is an update floor plus at most one deferred or skipped choice;
/// a fresh document rebuilds the floor from the version that is actually
/// installed, so the whole cost is one re-prompt for an update the user may
/// have skipped - and being re-asked to update is the safe direction to err in.
///
/// The byte comparison below is deliberately not a rejection. It asks whether
/// the file is exactly what this build's serializer would emit, which stops a
/// hand edit that survived parsing but stops nothing deliberate: the directory
/// is ours and private, so anyone who can write the file at all can equally
/// write it in canonical form. What actually holds the line is checked
/// elsewhere and on every read - the invariants in `validate_schema`, and
/// `observe_release` re-deriving the release identity from the feed rather than
/// trusting the copy on disk. So a mismatch means "rewrite this into our own
/// form", not "give up", and the rewrite is reported rather than done quietly.
fn usable_document(stored: &[u8], configured_channel: &str) -> Option<(StoredUpdatePolicy, bool)> {
    let mut document: StoredUpdatePolicy = serde_json::from_slice(stored).ok()?;
    let mut repaired = match document.schema_version {
        UPDATE_POLICY_SCHEMA_VERSION => {
            document.validate().ok()?;
            false
        }
        PREVIOUS_UPDATE_POLICY_SCHEMA_VERSION => {
            document
                .validate_schema(PREVIOUS_UPDATE_POLICY_SCHEMA_VERSION)
                .ok()?;
            document.schema_version = UPDATE_POLICY_SCHEMA_VERSION;
            true
        }
        _ => return None,
    };
    if serde_json::to_vec(&document).ok()? != stored {
        repaired = true;
    }
    if document.configured_channel != configured_channel {
        document.configured_channel = configured_channel.to_owned();
        document.highest_observed = None;
        document.decision = None;
        repaired = true;
    }
    Some((document, repaired))
}

fn saved_document(
    store: &AppDataSecretStore,
    document: &StoredUpdatePolicy,
) -> Result<StoredUpdatePolicy, UpdatePolicyError> {
    let mut next = document.clone();
    next.revision = next
        .revision
        .checked_add(1)
        .ok_or_else(UpdatePolicyError::storage_unavailable)?;
    next.validate()?;
    let encoded =
        serde_json::to_vec(&next).map_err(|_| UpdatePolicyError::storage_unavailable())?;
    store
        .save(&encoded)
        .map_err(|_| UpdatePolicyError::storage_unavailable())?;
    Ok(next)
}

fn parse_canonical_version(value: &str) -> Result<Version, ()> {
    let version = Version::parse(value).map_err(|_| ())?;
    if version.to_string() != value {
        return Err(());
    }
    Ok(version)
}

fn is_safe_channel(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 32
        && value.as_bytes()[0].is_ascii_lowercase()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
}

fn is_lower_hex_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn validate_app_data_root(path: &Path) -> Result<(), UpdatePolicyError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if !metadata.file_type().is_symlink() && metadata.is_dir() => Ok(()),
        Ok(_) => Err(UpdatePolicyError::storage_unavailable()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(UpdatePolicyError::storage_unavailable()),
    }
}

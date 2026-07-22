use std::fmt;

use semver::Version;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

pub const UPDATE_CONTRACT_VERSION: u16 = 1;
pub const DEFAULT_UPDATE_CHANNEL: &str = "stable";
pub const MAX_UPDATE_ARTIFACT_BYTES: u64 = 1024 * 1024 * 1024;

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(transparent)]
pub struct UpdateVersion(String);

impl fmt::Display for UpdateVersion {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl UpdateVersion {
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(transparent)]
pub struct UpdateChannel(String);

impl UpdateChannel {
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UpdatePolicy {
    Optional,
    Forced,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UpdateTarget {
    Darwin,
    Windows,
}

impl UpdateTarget {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Darwin => "darwin",
            Self::Windows => "windows",
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UpdateArchitecture {
    Aarch64,
    X86_64,
}

impl UpdateArchitecture {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Aarch64 => "aarch64",
            Self::X86_64 => "x86_64",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateArtifact {
    pub target: UpdateTarget,
    pub arch: UpdateArchitecture,
    pub sha256: String,
    pub size_bytes: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateRelease {
    pub version: UpdateVersion,
    pub channel: UpdateChannel,
    pub policy: UpdatePolicy,
    pub notes: Option<String>,
    pub published_at: Option<String>,
    pub artifact: UpdateArtifact,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UpdateCheckTrigger {
    Startup,
    Periodic,
    Manual,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UpdateDecision {
    InstallNow,
    Defer,
    SkipVersion,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UpdatePolicyAction {
    Prompt,
    Deferred,
    Skipped,
    Suppressed,
    InstallRequested,
    Forced,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UpdateErrorStage {
    Configuration,
    Check,
    Download,
    Storage,
    Install,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UpdateErrorCode {
    ConfigurationInvalid,
    ManifestRejected,
    TransportUnavailable,
    SignatureRejected,
    StorageUnavailable,
    InstallationFailed,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum UpdateState {
    Idle,
    Checking {
        trigger: UpdateCheckTrigger,
    },
    UpToDate {
        trigger: UpdateCheckTrigger,
    },
    Available {
        release: UpdateRelease,
    },
    Downloading {
        release: UpdateRelease,
        downloaded_bytes: u64,
        total_bytes: Option<u64>,
    },
    Ready {
        release: UpdateRelease,
        action: UpdatePolicyAction,
    },
    Installing {
        release: UpdateRelease,
    },
    InstallationLaunched {
        release: UpdateRelease,
    },
    Failed {
        stage: UpdateErrorStage,
        code: UpdateErrorCode,
        retryable: bool,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct UpdateContractError;

impl fmt::Display for UpdateContractError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("update release contract rejected")
    }
}

impl std::error::Error for UpdateContractError {}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RemoteRelease {
    version: String,
    url: String,
    signature: String,
    #[serde(default)]
    notes: Option<String>,
    #[serde(default)]
    pub_date: Option<String>,
    update_contract: UpdateContract,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct UpdateContract {
    version: u16,
    channel: String,
    policy: UpdatePolicy,
    artifact: RemoteArtifact,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RemoteArtifact {
    target: UpdateTarget,
    arch: UpdateArchitecture,
    sha256: String,
    size_bytes: u64,
}

pub fn parse_update_release(
    announced_version: &str,
    raw_json: &Value,
) -> Result<UpdateRelease, UpdateContractError> {
    let remote: RemoteRelease =
        serde_json::from_value(raw_json.clone()).map_err(|_| UpdateContractError)?;
    let announced = Version::parse(announced_version).map_err(|_| UpdateContractError)?;
    let remote_version = Version::parse(&remote.version).map_err(|_| UpdateContractError)?;
    if remote_version != announced || remote_version.to_string() != remote.version {
        return Err(UpdateContractError);
    }
    validate_https_url(&remote.url)?;
    validate_safe_bounded_text(&remote.signature, 4096)?;
    if remote.update_contract.version != UPDATE_CONTRACT_VERSION {
        return Err(UpdateContractError);
    }
    let channel = parse_channel(remote.update_contract.channel)?;
    let notes = match remote.notes {
        Some(notes) => {
            validate_safe_bounded_text(&notes, 8192)?;
            Some(notes)
        }
        None => None,
    };
    let published_at = match remote.pub_date {
        Some(published_at) => {
            OffsetDateTime::parse(&published_at, &Rfc3339).map_err(|_| UpdateContractError)?;
            Some(published_at)
        }
        None => None,
    };
    let artifact = remote.update_contract.artifact;
    if !is_lower_hex_digest(&artifact.sha256)
        || !(1..=MAX_UPDATE_ARTIFACT_BYTES).contains(&artifact.size_bytes)
    {
        return Err(UpdateContractError);
    }
    Ok(UpdateRelease {
        version: UpdateVersion(remote.version),
        channel,
        policy: remote.update_contract.policy,
        notes,
        published_at,
        artifact: UpdateArtifact {
            target: artifact.target,
            arch: artifact.arch,
            sha256: artifact.sha256,
            size_bytes: artifact.size_bytes,
        },
    })
}

pub fn parse_official_update(
    update: &tauri_plugin_updater::Update,
) -> Result<UpdateRelease, UpdateContractError> {
    let release = parse_update_release(&update.version, &update.raw_json)?;
    let (target, arch) = current_update_platform().ok_or(UpdateContractError)?;
    if release.artifact.target != target || release.artifact.arch != arch {
        return Err(UpdateContractError);
    }
    Ok(release)
}

fn current_update_platform() -> Option<(UpdateTarget, UpdateArchitecture)> {
    let target = match std::env::consts::OS {
        "macos" => UpdateTarget::Darwin,
        "windows" => UpdateTarget::Windows,
        _ => return None,
    };
    let arch = match std::env::consts::ARCH {
        "aarch64" => UpdateArchitecture::Aarch64,
        "x86_64" => UpdateArchitecture::X86_64,
        _ => return None,
    };

    Some((target, arch))
}

fn validate_https_url(value: &str) -> Result<(), UpdateContractError> {
    let url = reqwest::Url::parse(value).map_err(|_| UpdateContractError)?;
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err(UpdateContractError);
    }
    Ok(())
}

fn parse_channel(value: String) -> Result<UpdateChannel, UpdateContractError> {
    if value.is_empty()
        || value.len() > 32
        || !value.as_bytes()[0].is_ascii_lowercase()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
    {
        return Err(UpdateContractError);
    }
    Ok(UpdateChannel(value))
}

fn validate_safe_bounded_text(value: &str, max_bytes: usize) -> Result<(), UpdateContractError> {
    if value.is_empty() || value.len() > max_bytes {
        return Err(UpdateContractError);
    }
    if value.chars().any(|character| {
        let codepoint = u32::from(character);
        (codepoint <= 0x1f && character != '\n' && character != '\t')
            || codepoint == 0x7f
            || (0x202a..=0x202e).contains(&codepoint)
            || (0x2066..=0x2069).contains(&codepoint)
    }) {
        return Err(UpdateContractError);
    }
    Ok(())
}

fn is_lower_hex_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{
        current_update_platform, parse_update_release, UpdateArchitecture, UpdateCheckTrigger,
        UpdateDecision, UpdateErrorCode, UpdateErrorStage, UpdatePolicy, UpdatePolicyAction,
        UpdateState, UpdateTarget,
    };

    fn release_json(policy: &str) -> serde_json::Value {
        json!({
            "version": "1.2.3",
            "url": "https://updates.example.test/app-1.2.3.tar.gz",
            "signature": "trusted-minisign-signature",
            "notes": "A safe release",
            "pub_date": "2026-07-22T00:00:00Z",
            "update_contract": {
                "version": 1,
                "channel": "stable",
                "policy": policy,
                "artifact": {
                    "target": "darwin",
                    "arch": "aarch64",
                    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "size_bytes": 1024
                }
            }
        })
    }

    #[test]
    fn parses_optional_and_forced_policy_from_the_official_updater_raw_json() {
        let optional = parse_update_release("1.2.3", &release_json("optional"))
            .expect("optional release must satisfy the generic contract");
        assert_eq!(optional.version.to_string(), "1.2.3");
        assert_eq!(optional.channel.as_str(), "stable");
        assert_eq!(optional.policy, UpdatePolicy::Optional);
        assert_eq!(optional.artifact.target.as_str(), "darwin");
        assert_eq!(optional.artifact.arch.as_str(), "aarch64");
        assert_eq!(optional.artifact.size_bytes, 1024);

        let forced = parse_update_release("1.2.3", &release_json("forced"))
            .expect("forced release must satisfy the generic contract");
        assert_eq!(forced.policy, UpdatePolicy::Forced);
    }

    #[test]
    fn rejects_mismatched_versions_insecure_urls_and_untrusted_contract_extensions() {
        for malformed in [
            {
                let mut value = release_json("optional");
                value["version"] = json!("1.2.4");
                value
            },
            {
                let mut value = release_json("optional");
                value["url"] = json!("http://updates.example.test/app.tar.gz");
                value
            },
            {
                let mut value = release_json("optional");
                value["update_contract"]["version"] = json!(2);
                value
            },
            release_json("silent_business_override"),
            {
                let mut value = release_json("optional");
                value["update_contract"]["artifact"]["target"] = json!("android");
                value
            },
            {
                let mut value = release_json("optional");
                value["update_contract"]["artifact"]["sha256"] = json!("not-a-digest");
                value
            },
        ] {
            assert!(parse_update_release("1.2.3", &malformed).is_err());
        }
    }

    #[test]
    fn exposes_a_business_agnostic_closed_state_and_decision_contract() {
        let release = parse_update_release("1.2.3", &release_json("optional"))
            .expect("fixture release must be valid");
        let states = [
            UpdateState::Idle,
            UpdateState::Checking {
                trigger: UpdateCheckTrigger::Startup,
            },
            UpdateState::UpToDate {
                trigger: UpdateCheckTrigger::Manual,
            },
            UpdateState::Available {
                release: release.clone(),
            },
            UpdateState::Downloading {
                release: release.clone(),
                downloaded_bytes: 512,
                total_bytes: Some(1024),
            },
            UpdateState::Ready {
                release: release.clone(),
                action: UpdatePolicyAction::Prompt,
            },
            UpdateState::Installing {
                release: release.clone(),
            },
            UpdateState::InstallationLaunched { release },
            UpdateState::Failed {
                stage: UpdateErrorStage::Download,
                code: UpdateErrorCode::TransportUnavailable,
                retryable: true,
            },
        ];
        let serialized = serde_json::to_string(&states).expect("state contract must serialize");
        for expected in [
            "idle",
            "checking",
            "up_to_date",
            "available",
            "downloading",
            "ready",
            "installing",
            "installation_launched",
            "failed",
            "startup",
            "manual",
            "transport_unavailable",
        ] {
            assert!(
                serialized.contains(expected),
                "missing state token {expected}"
            );
        }
        for decision in [
            UpdateDecision::InstallNow,
            UpdateDecision::Defer,
            UpdateDecision::SkipVersion,
        ] {
            let serialized = serde_json::to_string(&decision).expect("decision must serialize");
            assert_eq!(
                serde_json::from_str::<UpdateDecision>(&serialized)
                    .expect("decision must deserialize"),
                decision
            );
        }
        for forbidden in ["douyin", "task", "customer", "automation_tool"] {
            assert!(!serialized.contains(forbidden));
        }
    }

    #[test]
    fn binds_the_release_contract_to_the_current_supported_desktop_target() {
        let (target, arch) =
            current_update_platform().expect("the desktop build target must be supported");
        #[cfg(target_os = "macos")]
        assert_eq!(target, UpdateTarget::Darwin);
        #[cfg(target_os = "windows")]
        assert_eq!(target, UpdateTarget::Windows);
        #[cfg(target_arch = "aarch64")]
        assert_eq!(arch, UpdateArchitecture::Aarch64);
        #[cfg(target_arch = "x86_64")]
        assert_eq!(arch, UpdateArchitecture::X86_64);
    }
}

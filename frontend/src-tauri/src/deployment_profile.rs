use std::error::Error;
use std::fmt::{Display, Formatter};
use std::fs;
use std::path::{Path, PathBuf};

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine as _;
use ed25519_dalek::{Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use url::Url;

const PROFILE_VERSION: &str = "customer-demo-profile.v1";
const LOCAL_PROFILE_ID: &str = "local";
const LOCAL_BASE_URL: &str = "http://127.0.0.1:8765";
const PROFILE_DIRECTORY: &str = "profiles";
const MAX_ENCODED_PAYLOAD_LENGTH: usize = 4096;
const MAX_ALLOWED_HOSTS: usize = 8;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DeploymentProfileKind {
    Local,
    Demo,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DeploymentProfile {
    kind: DeploymentProfileKind,
    profile_id: String,
    base_url: String,
    allowed_hosts: Vec<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DeploymentProfileError;

impl Display for DeploymentProfileError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("deployment profile is invalid")
    }
}

impl Error for DeploymentProfileError {}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SignedDemoProfile {
    version: String,
    profile: String,
    profile_id: String,
    base_url: String,
    allowed_hosts: Vec<String>,
}

impl DeploymentProfile {
    pub fn local() -> Self {
        Self {
            kind: DeploymentProfileKind::Local,
            profile_id: LOCAL_PROFILE_ID.to_owned(),
            base_url: LOCAL_BASE_URL.to_owned(),
            allowed_hosts: Vec::new(),
        }
    }

    pub fn load() -> Result<Self, DeploymentProfileError> {
        let payload = option_env!("AUTOMATION_TOOL_COMPILED_DEPLOYMENT_PROFILE_PAYLOAD");
        let signature = option_env!("AUTOMATION_TOOL_COMPILED_DEPLOYMENT_PROFILE_SIGNATURE");
        let verifying_key =
            option_env!("AUTOMATION_TOOL_COMPILED_DEPLOYMENT_PROFILE_VERIFYING_KEY");
        match (payload, signature, verifying_key) {
            (None, None, None) => Ok(Self::local()),
            (Some(payload), Some(signature), Some(verifying_key)) => {
                Self::verify_signed(payload, signature, verifying_key)
            }
            _ => Err(DeploymentProfileError),
        }
    }

    pub fn verify_signed(
        encoded_payload: &str,
        encoded_signature: &str,
        encoded_verifying_key: &str,
    ) -> Result<Self, DeploymentProfileError> {
        if encoded_payload.is_empty() || encoded_payload.len() > MAX_ENCODED_PAYLOAD_LENGTH {
            return Err(DeploymentProfileError);
        }
        let payload = decode_canonical(encoded_payload)?;
        let signature = decode_canonical(encoded_signature)?;
        let verifying_key = decode_canonical(encoded_verifying_key)?;
        let signature = Signature::from_slice(&signature).map_err(|_| DeploymentProfileError)?;
        let verifying_key: [u8; 32] = verifying_key
            .try_into()
            .map_err(|_| DeploymentProfileError)?;
        let verifying_key =
            VerifyingKey::from_bytes(&verifying_key).map_err(|_| DeploymentProfileError)?;
        if verifying_key.is_weak() {
            return Err(DeploymentProfileError);
        }
        verifying_key
            .verify_strict(&payload, &signature)
            .map_err(|_| DeploymentProfileError)?;
        let manifest = serde_json::from_slice::<SignedDemoProfile>(&payload)
            .map_err(|_| DeploymentProfileError)?;
        if serde_json::to_vec(&manifest).map_err(|_| DeploymentProfileError)? != payload {
            return Err(DeploymentProfileError);
        }
        validate_manifest(manifest)
    }

    pub fn kind(&self) -> DeploymentProfileKind {
        self.kind
    }

    pub fn profile_id(&self) -> &str {
        &self.profile_id
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    pub fn allowed_hosts(&self) -> &[String] {
        &self.allowed_hosts
    }

    pub fn prepare_data_directory(
        &self,
        app_data_root: &Path,
    ) -> Result<PathBuf, DeploymentProfileError> {
        if !app_data_root.is_absolute() {
            return Err(DeploymentProfileError);
        }
        ensure_private_directory(app_data_root)?;
        if self.kind == DeploymentProfileKind::Local {
            return Ok(app_data_root.to_path_buf());
        }
        let profiles = app_data_root.join(PROFILE_DIRECTORY);
        ensure_private_directory(&profiles)?;
        let profile = profiles.join(&self.profile_id);
        ensure_private_directory(&profile)?;
        Ok(profile)
    }
}

fn decode_canonical(value: &str) -> Result<Vec<u8>, DeploymentProfileError> {
    let decoded = URL_SAFE_NO_PAD
        .decode(value)
        .map_err(|_| DeploymentProfileError)?;
    if URL_SAFE_NO_PAD.encode(&decoded) != value {
        return Err(DeploymentProfileError);
    }
    Ok(decoded)
}

fn validate_manifest(
    manifest: SignedDemoProfile,
) -> Result<DeploymentProfile, DeploymentProfileError> {
    if manifest.version != PROFILE_VERSION
        || manifest.profile != "demo"
        || !valid_profile_id(&manifest.profile_id)
        || manifest.allowed_hosts.is_empty()
        || manifest.allowed_hosts.len() > MAX_ALLOWED_HOSTS
        || manifest
            .allowed_hosts
            .windows(2)
            .any(|hosts| hosts[0] >= hosts[1])
        || manifest
            .allowed_hosts
            .iter()
            .any(|host| !valid_hostname(host))
    {
        return Err(DeploymentProfileError);
    }
    let parsed = Url::parse(&manifest.base_url).map_err(|_| DeploymentProfileError)?;
    let host = parsed.host_str().ok_or(DeploymentProfileError)?;
    if parsed.scheme() != "https"
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.port().is_some()
        || parsed.path() != "/"
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || manifest.base_url != format!("https://{host}")
        || !manifest.allowed_hosts.iter().any(|allowed| allowed == host)
    {
        return Err(DeploymentProfileError);
    }
    Ok(DeploymentProfile {
        kind: DeploymentProfileKind::Demo,
        profile_id: manifest.profile_id,
        base_url: manifest.base_url,
        allowed_hosts: manifest.allowed_hosts,
    })
}

fn valid_profile_id(value: &str) -> bool {
    value.len() >= 6
        && value.len() <= 48
        && value.starts_with("demo-")
        && !value.ends_with('-')
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
}

fn valid_hostname(value: &str) -> bool {
    if value.is_empty() || value.len() > 253 || !value.contains('.') {
        return false;
    }
    let mut labels = value.split('.');
    let valid_labels = labels.clone().all(|label| {
        !label.is_empty()
            && label.len() <= 63
            && label
                .as_bytes()
                .first()
                .is_some_and(u8::is_ascii_alphanumeric)
            && label
                .as_bytes()
                .last()
                .is_some_and(u8::is_ascii_alphanumeric)
            && label
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
    });
    let top_level = labels.next_back().unwrap_or_default();
    valid_labels && top_level.len() >= 2 && top_level.bytes().all(|byte| byte.is_ascii_lowercase())
}

fn ensure_private_directory(path: &Path) -> Result<(), DeploymentProfileError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() || !metadata.is_dir() {
                return Err(DeploymentProfileError);
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(path).map_err(|_| DeploymentProfileError)?;
        }
        Err(_) => return Err(DeploymentProfileError),
    }
    set_private_permissions(path)
}

#[cfg(unix)]
fn set_private_permissions(path: &Path) -> Result<(), DeploymentProfileError> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(|_| DeploymentProfileError)
}

#[cfg(not(unix))]
fn set_private_permissions(_path: &Path) -> Result<(), DeploymentProfileError> {
    Ok(())
}

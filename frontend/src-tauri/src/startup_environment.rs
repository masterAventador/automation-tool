//! Path-free startup diagnostics for the desktop-owned local environment.

use std::error::Error;
use std::fmt::{Debug, Display, Formatter};
use std::fs;
use std::path::{Component, Path, PathBuf};

use serde::Serialize;

use crate::executor_package::ensure_no_symlink_ancestors;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AppDataStartupState {
    Ready,
    Unavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutorStartupState {
    Ready,
    ConfigurationRequired,
    Unavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TrustedBrowserStartupState {
    Ready,
    SelectionRequired,
    Unavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StartupEnvironmentSnapshot {
    app_data: AppDataStartupState,
    executor: ExecutorStartupState,
    trusted_browser: TrustedBrowserStartupState,
}

impl StartupEnvironmentSnapshot {
    pub const fn new(
        app_data: AppDataStartupState,
        executor: ExecutorStartupState,
        trusted_browser: TrustedBrowserStartupState,
    ) -> Self {
        Self {
            app_data,
            executor,
            trusted_browser,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct StartupEnvironmentError;

impl Display for StartupEnvironmentError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("startup environment is unavailable")
    }
}

impl Error for StartupEnvironmentError {}

pub struct StartupEnvironmentService {
    app_data_directory: PathBuf,
}

impl StartupEnvironmentService {
    pub fn initialize(app_data_directory: &Path) -> Result<Self, StartupEnvironmentError> {
        validate_app_data_directory(app_data_directory)?;
        Ok(Self {
            app_data_directory: app_data_directory.to_path_buf(),
        })
    }

    pub fn app_data_state(&self) -> AppDataStartupState {
        if validate_app_data_directory(&self.app_data_directory).is_ok() {
            AppDataStartupState::Ready
        } else {
            AppDataStartupState::Unavailable
        }
    }
}

impl Debug for StartupEnvironmentService {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("StartupEnvironmentService(<redacted>)")
    }
}

fn validate_app_data_directory(path: &Path) -> Result<(), StartupEnvironmentError> {
    let encoded = path.to_str().ok_or(StartupEnvironmentError)?;
    if !path.is_absolute()
        || path.parent().is_none()
        || encoded.is_empty()
        || encoded.len() > 4096
        || encoded.chars().any(|character| {
            let codepoint = character as u32;
            character.is_control()
                || (0x202a..=0x202e).contains(&codepoint)
                || (0x2066..=0x2069).contains(&codepoint)
        })
        || path
            .components()
            .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
        || ensure_no_symlink_ancestors(path).is_err()
    {
        return Err(StartupEnvironmentError);
    }
    let metadata = fs::symlink_metadata(path).map_err(|_| StartupEnvironmentError)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(StartupEnvironmentError);
    }
    require_private_permissions(&metadata)
}

#[cfg(unix)]
fn require_private_permissions(metadata: &fs::Metadata) -> Result<(), StartupEnvironmentError> {
    use std::os::unix::fs::PermissionsExt;

    if metadata.permissions().mode() & 0o777 == 0o700 {
        Ok(())
    } else {
        Err(StartupEnvironmentError)
    }
}

#[cfg(not(unix))]
fn require_private_permissions(_metadata: &fs::Metadata) -> Result<(), StartupEnvironmentError> {
    Ok(())
}

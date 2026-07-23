//! EB-07: the single production authority for the operations-browser path.
//!
//! Platform commands never discover, select or fall back to a system browser:
//! the only executable they may launch is the one resolved here from the
//! packaged embedded distribution (EB-06 verification). The first resolution
//! in a process runs the full manifest/digest verification; later resolutions
//! re-check cheaply that the cached executable is still a plain executable
//! file and re-run the full verification if anything changed. Errors map to
//! the closed component vocabulary EB-08 will surface; Debug output never
//! reveals installation paths.

use crate::embedded_browser_distribution::{
    cached_executable_still_sound, EmbeddedBrowserDistribution, EmbeddedBrowserError,
};
use std::fmt;
use std::io;
use std::path::PathBuf;
use std::sync::Mutex;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EmbeddedBrowserAuthorityError {
    /// The packaged distribution directory or manifest is absent.
    ComponentMissing,
    /// The packaged distribution exists but failed verification.
    ComponentInvalid,
    /// The packaged distribution's locked versions do not match this build.
    VersionIncompatible,
    /// The authority state is unavailable (poisoned lock).
    Unavailable,
}

impl fmt::Display for EmbeddedBrowserAuthorityError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = match self {
            Self::ComponentMissing => "embedded browser component is missing",
            Self::ComponentInvalid => "embedded browser component failed verification",
            Self::VersionIncompatible => "embedded browser component version is incompatible",
            Self::Unavailable => "embedded browser authority is unavailable",
        };
        formatter.write_str(message)
    }
}

impl std::error::Error for EmbeddedBrowserAuthorityError {}

/// Deliberately not serializable; Debug is redacted.
pub struct EmbeddedBrowserAuthority {
    resource_dir: PathBuf,
    target_id: &'static str,
    verified: Mutex<Option<PathBuf>>,
}

impl fmt::Debug for EmbeddedBrowserAuthority {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EmbeddedBrowserAuthority")
            .field("target_id", &self.target_id)
            .field(
                "verified",
                &self
                    .verified
                    .lock()
                    .map(|cache| cache.is_some())
                    .unwrap_or(false),
            )
            .finish_non_exhaustive()
    }
}

impl EmbeddedBrowserAuthority {
    pub fn new(resource_dir: PathBuf, target_id: &'static str) -> Self {
        Self {
            resource_dir,
            target_id,
            verified: Mutex::new(None),
        }
    }

    /// Resolve the verified embedded-browser executable for launching.
    pub fn resolve(&self) -> Result<PathBuf, EmbeddedBrowserAuthorityError> {
        let mut cache = self
            .verified
            .lock()
            .map_err(|_| EmbeddedBrowserAuthorityError::Unavailable)?;
        if let Some(executable) = cache.as_ref() {
            if cached_executable_still_sound(&self.resource_dir, executable, self.target_id) {
                return Ok(executable.clone());
            }
            *cache = None;
        }
        let distribution =
            EmbeddedBrowserDistribution::load_for_target(&self.resource_dir, self.target_id)
                .map_err(classify)?;
        let executable = distribution.executable_path().to_path_buf();
        *cache = Some(executable.clone());
        Ok(executable)
    }
}

fn classify(error: EmbeddedBrowserError) -> EmbeddedBrowserAuthorityError {
    match error {
        EmbeddedBrowserError::Io(io_error) if io_error.kind() == io::ErrorKind::NotFound => {
            EmbeddedBrowserAuthorityError::ComponentMissing
        }
        EmbeddedBrowserError::VersionIncompatible => {
            EmbeddedBrowserAuthorityError::VersionIncompatible
        }
        EmbeddedBrowserError::Io(_) | EmbeddedBrowserError::Invalid(_) => {
            EmbeddedBrowserAuthorityError::ComponentInvalid
        }
    }
}

pub fn release_target_id() -> &'static str {
    if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        "macos-arm64"
    } else if cfg!(all(target_os = "macos", target_arch = "x86_64")) {
        "macos-x86_64"
    } else if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        "windows-x86_64"
    } else {
        "unsupported"
    }
}

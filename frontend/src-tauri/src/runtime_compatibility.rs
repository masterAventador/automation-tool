//! Closed release matrix shared by the desktop Control Plane and Executor boundaries.

pub(crate) const DESKTOP_APP_VERSION: &str = env!("CARGO_PKG_VERSION");
pub(crate) const CONTROL_PLANE_VERSION: &str = "0.1.0";
pub(crate) const CONTROL_PLANE_API_VERSION: &str = "v1";
pub(crate) const EXECUTOR_RUNTIME_VERSION: &str = "0.1.0";
pub(crate) const EXECUTOR_RUNTIME_VERSION_REQUIREMENT: &str = "=0.1.0";
pub(crate) const EXECUTOR_PROTOCOL_VERSION: &str = "1.0";

#[cfg(test)]
mod tests {
    use semver::{Version, VersionReq};

    use super::{
        CONTROL_PLANE_VERSION, DESKTOP_APP_VERSION, EXECUTOR_RUNTIME_VERSION,
        EXECUTOR_RUNTIME_VERSION_REQUIREMENT,
    };

    #[test]
    fn current_release_matrix_is_canonical_and_executor_exact() {
        assert_eq!(DESKTOP_APP_VERSION, "0.1.0");
        Version::parse(CONTROL_PLANE_VERSION).expect("canonical Control Plane SemVer");
        let executor = Version::parse(EXECUTOR_RUNTIME_VERSION).expect("canonical Executor SemVer");
        let requirement =
            VersionReq::parse(EXECUTOR_RUNTIME_VERSION_REQUIREMENT).expect("exact requirement");

        assert!(requirement.matches(&executor));
        assert!(!requirement.matches(&Version::new(0, 0, 9)));
        assert!(!requirement.matches(&Version::new(0, 1, 1)));
    }
}

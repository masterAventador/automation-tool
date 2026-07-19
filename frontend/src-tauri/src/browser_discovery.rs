use std::fmt;
use std::path::{Path, PathBuf};

#[cfg(target_os = "windows")]
#[path = "browser_discovery_windows.rs"]
mod browser_discovery_windows;

#[cfg(target_os = "macos")]
const MACOS_APPLICATIONS_DIRECTORY: &str = "/Applications";
#[cfg(target_os = "macos")]
const GOOGLE_CHROME_APPLICATION_PATH: &str = "/Applications/Google Chrome.app";
#[cfg(target_os = "macos")]
const GOOGLE_CHROME_EXECUTABLE_PATH: &str =
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
#[cfg(target_os = "macos")]
const MICROSOFT_EDGE_APPLICATION_PATH: &str = "/Applications/Microsoft Edge.app";
#[cfg(target_os = "macos")]
const MICROSOFT_EDGE_EXECUTABLE_PATH: &str =
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge";

#[cfg(target_os = "macos")]
const GOOGLE_CHROME_REQUIREMENT: &str = "identifier \"com.google.Chrome\" and anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists and certificate leaf[subject.OU] = \"EQHXZ8M8AV\"";
#[cfg(target_os = "macos")]
const MICROSOFT_EDGE_REQUIREMENT: &str = "identifier \"com.microsoft.edgemac\" and anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists and certificate leaf[subject.OU] = \"UBF8T346G9\"";

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Deserialize, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SupportedBrowser {
    GoogleChrome,
    MicrosoftEdge,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrowserDiscoveryErrorCode {
    CandidateRejected,
    DiscoveryUnavailable,
    PathInvalidated,
    UnsupportedPlatform,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BrowserDiscoveryError {
    code: BrowserDiscoveryErrorCode,
    browser: Option<SupportedBrowser>,
}

impl BrowserDiscoveryError {
    pub fn code(self) -> BrowserDiscoveryErrorCode {
        self.code
    }

    pub fn browser(self) -> Option<SupportedBrowser> {
        self.browser
    }

    #[cfg(any(target_os = "macos", target_os = "windows"))]
    fn candidate_rejected(browser: SupportedBrowser) -> Self {
        Self {
            code: BrowserDiscoveryErrorCode::CandidateRejected,
            browser: Some(browser),
        }
    }

    #[cfg(any(target_os = "macos", target_os = "windows"))]
    fn path_invalidated(browser: SupportedBrowser) -> Self {
        Self {
            code: BrowserDiscoveryErrorCode::PathInvalidated,
            browser: Some(browser),
        }
    }

    fn unsupported_platform() -> Self {
        Self {
            code: BrowserDiscoveryErrorCode::UnsupportedPlatform,
            browser: None,
        }
    }

    #[cfg(target_os = "windows")]
    fn discovery_unavailable() -> Self {
        Self {
            code: BrowserDiscoveryErrorCode::DiscoveryUnavailable,
            browser: None,
        }
    }
}

impl fmt::Display for BrowserDiscoveryError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = match self.code {
            BrowserDiscoveryErrorCode::CandidateRejected => "browser candidate is rejected",
            BrowserDiscoveryErrorCode::DiscoveryUnavailable => "browser discovery is unavailable",
            BrowserDiscoveryErrorCode::PathInvalidated => "browser path is no longer trusted",
            BrowserDiscoveryErrorCode::UnsupportedPlatform => {
                "browser discovery is unsupported on this platform"
            }
        };
        formatter.write_str(message)
    }
}

impl std::error::Error for BrowserDiscoveryError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct PathIdentity {
    device: u64,
    inode: u64,
    #[cfg(target_os = "windows")]
    length: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrustedBrowser {
    browser: SupportedBrowser,
    application_path: PathBuf,
    executable_path: PathBuf,
    bundle_identifier: &'static str,
    team_identifier: &'static str,
    application_identity: PathIdentity,
    executable_identity: PathIdentity,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrustedWindowsBrowser {
    browser: SupportedBrowser,
    executable_path: PathBuf,
    product_name: &'static str,
    publisher: &'static str,
    executable_identity: PathIdentity,
}

impl TrustedWindowsBrowser {
    pub fn browser(&self) -> SupportedBrowser {
        self.browser
    }

    pub fn executable_path(&self) -> &Path {
        &self.executable_path
    }

    pub fn product_name(&self) -> &'static str {
        self.product_name
    }

    pub fn publisher(&self) -> &'static str {
        self.publisher
    }
}

impl TrustedBrowser {
    pub fn browser(&self) -> SupportedBrowser {
        self.browser
    }

    pub fn application_path(&self) -> &Path {
        &self.application_path
    }

    pub fn executable_path(&self) -> &Path {
        &self.executable_path
    }

    pub fn bundle_identifier(&self) -> &'static str {
        self.bundle_identifier
    }

    pub fn team_identifier(&self) -> &'static str {
        self.team_identifier
    }
}

#[cfg(target_os = "macos")]
#[derive(Clone, Copy)]
struct BrowserDefinition {
    browser: SupportedBrowser,
    application_name: &'static str,
    executable_relative_path: &'static str,
    application_path: &'static str,
    executable_path: &'static str,
    bundle_identifier: &'static str,
    team_identifier: &'static str,
    signing_requirement: &'static str,
}

#[cfg(target_os = "macos")]
const MACOS_BROWSER_DEFINITIONS: [BrowserDefinition; 2] = [
    BrowserDefinition {
        browser: SupportedBrowser::GoogleChrome,
        application_name: "Google Chrome.app",
        executable_relative_path: "Contents/MacOS/Google Chrome",
        application_path: GOOGLE_CHROME_APPLICATION_PATH,
        executable_path: GOOGLE_CHROME_EXECUTABLE_PATH,
        bundle_identifier: "com.google.Chrome",
        team_identifier: "EQHXZ8M8AV",
        signing_requirement: GOOGLE_CHROME_REQUIREMENT,
    },
    BrowserDefinition {
        browser: SupportedBrowser::MicrosoftEdge,
        application_name: "Microsoft Edge.app",
        executable_relative_path: "Contents/MacOS/Microsoft Edge",
        application_path: MICROSOFT_EDGE_APPLICATION_PATH,
        executable_path: MICROSOFT_EDGE_EXECUTABLE_PATH,
        bundle_identifier: "com.microsoft.edgemac",
        team_identifier: "UBF8T346G9",
        signing_requirement: MICROSOFT_EDGE_REQUIREMENT,
    },
];

#[cfg(target_os = "macos")]
trait CodeSignatureVerifier {
    fn verify(&self, application_path: &Path, requirement: &str) -> Result<(), ()>;
}

#[cfg(target_os = "macos")]
struct AppleCodeSignatureVerifier;

#[cfg(target_os = "macos")]
impl CodeSignatureVerifier for AppleCodeSignatureVerifier {
    fn verify(&self, application_path: &Path, requirement: &str) -> Result<(), ()> {
        use core_foundation::url::CFURL;
        use security_framework::os::macos::code_signing::{Flags, SecRequirement, SecStaticCode};

        let url = CFURL::from_path(application_path, true).ok_or(())?;
        let code = SecStaticCode::from_path(&url, Flags::NONE).map_err(|_| ())?;
        let requirement = requirement.parse::<SecRequirement>().map_err(|_| ())?;
        let flags = Flags::CHECK_ALL_ARCHITECTURES
            | Flags::CHECK_NESTED_CODE
            | Flags::RESTRICT_SYMLINKS
            | Flags::NO_NETWORK_ACCESS;
        code.check_validity(flags, &requirement).map_err(|_| ())
    }
}

#[cfg(target_os = "macos")]
pub fn discover_macos_browsers() -> Result<Vec<TrustedBrowser>, BrowserDiscoveryError> {
    discover_from_root(
        Path::new(MACOS_APPLICATIONS_DIRECTORY),
        &AppleCodeSignatureVerifier,
    )
}

#[cfg(not(target_os = "macos"))]
pub fn discover_macos_browsers() -> Result<Vec<TrustedBrowser>, BrowserDiscoveryError> {
    Err(BrowserDiscoveryError::unsupported_platform())
}

#[cfg(target_os = "macos")]
pub fn revalidate_macos_browser(browser: &TrustedBrowser) -> Result<(), BrowserDiscoveryError> {
    let definition = definition_for(browser.browser);
    if browser.application_path != Path::new(definition.application_path)
        || browser.executable_path != Path::new(definition.executable_path)
    {
        return Err(BrowserDiscoveryError::path_invalidated(browser.browser));
    }
    revalidate_with(browser, &AppleCodeSignatureVerifier)
}

#[cfg(not(target_os = "macos"))]
pub fn revalidate_macos_browser(_browser: &TrustedBrowser) -> Result<(), BrowserDiscoveryError> {
    Err(BrowserDiscoveryError::unsupported_platform())
}

#[cfg(target_os = "windows")]
pub fn discover_windows_browsers() -> Result<Vec<TrustedWindowsBrowser>, BrowserDiscoveryError> {
    browser_discovery_windows::discover()
}

#[cfg(not(target_os = "windows"))]
pub fn discover_windows_browsers() -> Result<Vec<TrustedWindowsBrowser>, BrowserDiscoveryError> {
    Err(BrowserDiscoveryError::unsupported_platform())
}

#[cfg(target_os = "windows")]
pub fn revalidate_windows_browser(
    browser: &TrustedWindowsBrowser,
) -> Result<(), BrowserDiscoveryError> {
    browser_discovery_windows::revalidate(browser)
}

#[cfg(not(target_os = "windows"))]
pub fn revalidate_windows_browser(
    _browser: &TrustedWindowsBrowser,
) -> Result<(), BrowserDiscoveryError> {
    Err(BrowserDiscoveryError::unsupported_platform())
}

#[cfg(target_os = "macos")]
fn definition_for(browser: SupportedBrowser) -> &'static BrowserDefinition {
    MACOS_BROWSER_DEFINITIONS
        .iter()
        .find(|definition| definition.browser == browser)
        .expect("every supported browser has a fixed definition")
}

#[cfg(target_os = "macos")]
fn discover_from_root(
    applications_root: &Path,
    verifier: &dyn CodeSignatureVerifier,
) -> Result<Vec<TrustedBrowser>, BrowserDiscoveryError> {
    let mut discovered = Vec::new();
    for definition in &MACOS_BROWSER_DEFINITIONS {
        let application_path = applications_root.join(definition.application_name);
        match std::fs::symlink_metadata(&application_path) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(_) => {
                return Err(BrowserDiscoveryError::candidate_rejected(
                    definition.browser,
                ))
            }
            Ok(_) => {}
        }
        let executable_path = application_path.join(definition.executable_relative_path);
        discovered.push(validate_candidate(
            definition,
            application_path,
            executable_path,
            verifier,
        )?);
    }
    Ok(discovered)
}

#[cfg(target_os = "macos")]
fn validate_candidate(
    definition: &BrowserDefinition,
    application_path: PathBuf,
    executable_path: PathBuf,
    verifier: &dyn CodeSignatureVerifier,
) -> Result<TrustedBrowser, BrowserDiscoveryError> {
    let rejected = || BrowserDiscoveryError::candidate_rejected(definition.browser);
    let application_identity = directory_identity(&application_path).map_err(|_| rejected())?;
    let initial_executable_identity =
        executable_identity(&executable_path).map_err(|_| rejected())?;
    verifier
        .verify(&application_path, definition.signing_requirement)
        .map_err(|_| rejected())?;
    if directory_identity(&application_path).map_err(|_| rejected())? != application_identity
        || executable_identity(&executable_path).map_err(|_| rejected())?
            != initial_executable_identity
    {
        return Err(BrowserDiscoveryError::path_invalidated(definition.browser));
    }
    Ok(TrustedBrowser {
        browser: definition.browser,
        application_path,
        executable_path,
        bundle_identifier: definition.bundle_identifier,
        team_identifier: definition.team_identifier,
        application_identity,
        executable_identity: initial_executable_identity,
    })
}

#[cfg(target_os = "macos")]
fn revalidate_with(
    browser: &TrustedBrowser,
    verifier: &dyn CodeSignatureVerifier,
) -> Result<(), BrowserDiscoveryError> {
    let definition = definition_for(browser.browser);
    let invalidated = || BrowserDiscoveryError::path_invalidated(browser.browser);
    let application_identity =
        directory_identity(&browser.application_path).map_err(|_| invalidated())?;
    let initial_executable_identity =
        executable_identity(&browser.executable_path).map_err(|_| invalidated())?;
    if application_identity != browser.application_identity
        || initial_executable_identity != browser.executable_identity
    {
        return Err(invalidated());
    }
    verifier
        .verify(&browser.application_path, definition.signing_requirement)
        .map_err(|_| invalidated())?;
    if directory_identity(&browser.application_path).map_err(|_| invalidated())?
        != application_identity
        || executable_identity(&browser.executable_path).map_err(|_| invalidated())?
            != initial_executable_identity
    {
        return Err(invalidated());
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn directory_identity(path: &Path) -> Result<PathIdentity, ()> {
    use std::os::unix::fs::MetadataExt;

    ensure_no_symlink_components(path)?;
    let metadata = std::fs::symlink_metadata(path).map_err(|_| ())?;
    if !metadata.is_dir() {
        return Err(());
    }
    Ok(PathIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
    })
}

#[cfg(target_os = "macos")]
fn executable_identity(path: &Path) -> Result<PathIdentity, ()> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};

    ensure_no_symlink_components(path)?;
    let metadata = std::fs::symlink_metadata(path).map_err(|_| ())?;
    if !metadata.is_file() || metadata.permissions().mode() & 0o111 == 0 {
        return Err(());
    }
    Ok(PathIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
    })
}

#[cfg(target_os = "macos")]
fn ensure_no_symlink_components(path: &Path) -> Result<(), ()> {
    if !path.is_absolute() {
        return Err(());
    }
    let mut normalized = path.to_path_buf();
    for alias in ["var", "tmp", "etc"] {
        let prefix = Path::new("/").join(alias);
        if let Ok(suffix) = path.strip_prefix(&prefix) {
            normalized = Path::new("/private").join(alias).join(suffix);
            break;
        }
    }
    let mut current = PathBuf::new();
    for component in normalized.components() {
        current.push(component.as_os_str());
        let metadata = std::fs::symlink_metadata(&current).map_err(|_| ())?;
        if metadata.file_type().is_symlink() {
            return Err(());
        }
    }
    Ok(())
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::{symlink, PermissionsExt};
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static NEXT_TEMPORARY_DIRECTORY: AtomicU64 = AtomicU64::new(0);

    struct TemporaryApplications {
        path: PathBuf,
    }

    impl TemporaryApplications {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "automation-tool-b5-02-{}-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .expect("system time")
                    .as_nanos(),
                NEXT_TEMPORARY_DIRECTORY.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).expect("temporary applications root");
            Self { path }
        }

        fn install_fixture(&self, definition: &BrowserDefinition) -> PathBuf {
            let application = self.path.join(definition.application_name);
            let executable = application.join(definition.executable_relative_path);
            fs::create_dir_all(executable.parent().expect("executable parent"))
                .expect("fixture hierarchy");
            fs::write(&executable, b"fixture-browser").expect("fixture executable");
            fs::set_permissions(&executable, fs::Permissions::from_mode(0o700))
                .expect("fixture executable permissions");
            executable
        }
    }

    impl Drop for TemporaryApplications {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    struct AcceptAll;

    impl CodeSignatureVerifier for AcceptAll {
        fn verify(&self, _application_path: &Path, _requirement: &str) -> Result<(), ()> {
            Ok(())
        }
    }

    struct RejectAll;

    impl CodeSignatureVerifier for RejectAll {
        fn verify(&self, _application_path: &Path, _requirement: &str) -> Result<(), ()> {
            Err(())
        }
    }

    #[test]
    fn fixed_standard_candidates_are_discovered_in_allowlist_order() {
        let applications = TemporaryApplications::new();
        for definition in &MACOS_BROWSER_DEFINITIONS {
            applications.install_fixture(definition);
        }

        let discovered = discover_from_root(&applications.path, &AcceptAll).expect("discover");

        assert_eq!(discovered.len(), 2);
        assert_eq!(discovered[0].browser(), SupportedBrowser::GoogleChrome);
        assert_eq!(discovered[1].browser(), SupportedBrowser::MicrosoftEdge);
        assert_eq!(discovered[0].bundle_identifier(), "com.google.Chrome");
        assert_eq!(discovered[0].team_identifier(), "EQHXZ8M8AV");
        assert_eq!(discovered[1].bundle_identifier(), "com.microsoft.edgemac");
        assert_eq!(discovered[1].team_identifier(), "UBF8T346G9");
    }

    #[test]
    fn missing_candidates_and_arbitrary_applications_are_not_discovered() {
        let applications = TemporaryApplications::new();
        let arbitrary = applications
            .path
            .join("Untrusted Browser.app/Contents/MacOS");
        fs::create_dir_all(&arbitrary).expect("arbitrary application");

        assert!(discover_from_root(&applications.path, &AcceptAll)
            .expect("missing candidates are valid")
            .is_empty());
    }

    #[test]
    fn invalid_signature_or_incomplete_bundle_rejects_the_candidate() {
        let applications = TemporaryApplications::new();
        applications.install_fixture(&MACOS_BROWSER_DEFINITIONS[0]);
        assert_eq!(
            discover_from_root(&applications.path, &RejectAll)
                .expect_err("signature rejection must fail")
                .code(),
            BrowserDiscoveryErrorCode::CandidateRejected
        );

        let incomplete = TemporaryApplications::new();
        fs::create_dir(incomplete.path.join("Google Chrome.app")).expect("incomplete app");
        assert_eq!(
            discover_from_root(&incomplete.path, &AcceptAll)
                .expect_err("missing executable must fail")
                .code(),
            BrowserDiscoveryErrorCode::CandidateRejected
        );
    }

    #[test]
    fn symlinked_application_or_executable_is_rejected() {
        let applications = TemporaryApplications::new();
        let outside = TemporaryApplications::new();
        outside.install_fixture(&MACOS_BROWSER_DEFINITIONS[0]);
        symlink(
            outside.path.join("Google Chrome.app"),
            applications.path.join("Google Chrome.app"),
        )
        .expect("symlink application");
        assert_eq!(
            discover_from_root(&applications.path, &AcceptAll)
                .expect_err("symlink application must fail")
                .code(),
            BrowserDiscoveryErrorCode::CandidateRejected
        );

        let executable_link = TemporaryApplications::new();
        let executable = executable_link.install_fixture(&MACOS_BROWSER_DEFINITIONS[0]);
        fs::remove_file(&executable).expect("remove fixture executable");
        symlink("/bin/sh", &executable).expect("symlink executable");
        assert_eq!(
            discover_from_root(&executable_link.path, &AcceptAll)
                .expect_err("symlink executable must fail")
                .code(),
            BrowserDiscoveryErrorCode::CandidateRejected
        );
    }

    #[test]
    fn replaced_or_missing_path_is_invalidated_before_launch() {
        let applications = TemporaryApplications::new();
        let executable = applications.install_fixture(&MACOS_BROWSER_DEFINITIONS[0]);
        let trusted = discover_from_root(&applications.path, &AcceptAll)
            .expect("discover")
            .remove(0);

        let previous_executable = executable.with_extension("previous");
        fs::rename(&executable, &previous_executable).expect("retain old executable identity");
        fs::write(&executable, b"replacement").expect("replace executable");
        fs::set_permissions(&executable, fs::Permissions::from_mode(0o700))
            .expect("replacement permissions");
        assert_eq!(
            revalidate_with(&trusted, &AcceptAll)
                .expect_err("replacement must invalidate path")
                .code(),
            BrowserDiscoveryErrorCode::PathInvalidated
        );

        fs::remove_file(&executable).expect("remove replacement");
        assert_eq!(
            revalidate_with(&trusted, &AcceptAll)
                .expect_err("missing path must invalidate")
                .code(),
            BrowserDiscoveryErrorCode::PathInvalidated
        );
    }
}

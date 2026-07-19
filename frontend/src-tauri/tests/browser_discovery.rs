#[cfg(not(target_os = "macos"))]
use automation_tool_desktop_lib::browser_discovery::BrowserDiscoveryErrorCode;
use automation_tool_desktop_lib::browser_discovery::{
    discover_macos_browsers, revalidate_macos_browser, SupportedBrowser,
};

#[cfg(target_os = "macos")]
#[test]
fn real_installed_macos_browsers_use_the_production_signature_path() {
    let discovered = discover_macos_browsers().expect("discover trusted standard browsers");

    for browser in &discovered {
        let expected = match browser.browser() {
            SupportedBrowser::GoogleChrome => (
                "/Applications/Google Chrome.app",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "com.google.Chrome",
                "EQHXZ8M8AV",
            ),
            SupportedBrowser::MicrosoftEdge => (
                "/Applications/Microsoft Edge.app",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "com.microsoft.edgemac",
                "UBF8T346G9",
            ),
        };
        assert_eq!(browser.application_path().to_str(), Some(expected.0));
        assert_eq!(browser.executable_path().to_str(), Some(expected.1));
        assert_eq!(browser.bundle_identifier(), expected.2);
        assert_eq!(browser.team_identifier(), expected.3);
        revalidate_macos_browser(browser).expect("revalidate unchanged trusted browser");
    }

    if std::path::Path::new("/Applications/Google Chrome.app").exists() {
        assert!(discovered
            .iter()
            .any(|browser| browser.browser() == SupportedBrowser::GoogleChrome));
    }
    if !std::path::Path::new("/Applications/Microsoft Edge.app").exists() {
        assert!(!discovered
            .iter()
            .any(|browser| browser.browser() == SupportedBrowser::MicrosoftEdge));
    }
}

#[cfg(not(target_os = "macos"))]
#[test]
fn real_installed_macos_browsers_use_the_production_signature_path() {
    assert_eq!(
        discover_macos_browsers()
            .expect_err("non-macOS target must reject macOS discovery")
            .code(),
        BrowserDiscoveryErrorCode::UnsupportedPlatform
    );
    let _ = revalidate_macos_browser;
    let _ = SupportedBrowser::GoogleChrome;
}

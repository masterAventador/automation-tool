use automation_tool_desktop_lib::browser_discovery::BrowserDiscoveryErrorCode;
use automation_tool_desktop_lib::browser_discovery::{
    discover_macos_browsers, discover_windows_browsers, revalidate_macos_browser,
    revalidate_windows_browser, SupportedBrowser,
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

#[cfg(target_os = "windows")]
#[test]
fn real_installed_windows_browsers_use_the_production_authenticode_path() {
    let discovered = discover_windows_browsers().expect("discover trusted standard browsers");

    for browser in &discovered {
        let (file_name, product, publisher) = match browser.browser() {
            SupportedBrowser::GoogleChrome => ("chrome.exe", "Google Chrome", "Google LLC"),
            SupportedBrowser::MicrosoftEdge => {
                ("msedge.exe", "Microsoft Edge", "Microsoft Corporation")
            }
        };
        assert_eq!(
            browser
                .executable_path()
                .file_name()
                .and_then(|value| value.to_str()),
            Some(file_name)
        );
        assert_eq!(browser.product_name(), product);
        assert_eq!(browser.publisher(), publisher);
        revalidate_windows_browser(browser).expect("revalidate unchanged trusted browser");
    }

    assert!(
        !discovered.is_empty(),
        "Windows runner must provide Chrome or Edge"
    );
}

#[cfg(not(target_os = "windows"))]
#[test]
fn real_installed_windows_browsers_use_the_production_authenticode_path() {
    assert_eq!(
        discover_windows_browsers()
            .expect_err("non-Windows target must reject Windows discovery")
            .code(),
        BrowserDiscoveryErrorCode::UnsupportedPlatform
    );
    let _ = revalidate_windows_browser;
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

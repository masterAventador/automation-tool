//! EB-16: the installed release package must satisfy every startup gate input.
//!
//! The desktop startup gate only mounts the workbench when the production
//! `check_local_startup_environment` command reports app data, Local Executor
//! and embedded browser all ready. This test runs that exact production logic
//! against a real installed `.app`, so a packaging regression (a dropped
//! framework symlink, a re-signed browser binary, a broken Executor manifest
//! or the wrong target) fails here instead of silently showing users a blocked
//! startup screen.
//!
//! It is `#[ignore]`d because it needs a real installed package; the EB-16
//! acceptance runner supplies `EB16_INSTALLED_RESOURCES` and runs it in release
//! mode with the same compile-time configuration as the shipped binary.

#![cfg(not(debug_assertions))]

use std::fs;
use std::path::PathBuf;

use automation_tool_desktop_lib::browser_profiles::BrowserProfileStore;
use automation_tool_desktop_lib::embedded_browser_authority::{
    release_target_id, EmbeddedBrowserAuthority,
};
use automation_tool_desktop_lib::executor_platform::ExecutorPlatformService;
use automation_tool_desktop_lib::startup_environment::{
    AppDataStartupState, ExecutorStartupState, StartupEnvironmentService,
};

fn private_app_data() -> PathBuf {
    let directory = PathBuf::from(
        std::env::var("EB16_APP_DATA").expect("EB16_APP_DATA names an empty private directory"),
    );
    fs::create_dir_all(&directory).expect("create the private App data directory");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))
            .expect("App data must stay private");
    }
    directory
}

#[test]
#[ignore]
fn installed_release_package_satisfies_every_startup_gate_input() {
    let resources = PathBuf::from(
        std::env::var("EB16_INSTALLED_RESOURCES")
            .expect("EB16_INSTALLED_RESOURCES names the installed Contents/Resources"),
    );
    let app_data = private_app_data();

    let executor = ExecutorPlatformService::initialize_with_package_root(
        &app_data,
        &resources.join("local-executor").join("package"),
    )
    .expect("the installed Local Executor package initializes");
    executor
        .status()
        .expect("the installed Local Executor manager status is readable");
    executor
        .verified_entrypoint()
        .expect("the installed Local Executor signature and inventory verify");
    assert_eq!(
        executor.startup_environment_state(),
        ExecutorStartupState::Ready,
        "the installed Local Executor package must verify against the shipped key"
    );

    let startup = StartupEnvironmentService::initialize(&app_data)
        .expect("the private App data directory is valid");
    assert_eq!(startup.app_data_state(), AppDataStartupState::Ready);

    let profiles =
        BrowserProfileStore::initialize(&app_data).expect("the browser profile store initializes");
    profiles
        .revalidate_storage()
        .expect("the browser profile storage stays private and valid");

    let authority = EmbeddedBrowserAuthority::new(resources, release_target_id());
    authority
        .resolve()
        .expect("the packaged embedded browser must verify for this release target");
}

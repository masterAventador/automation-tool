pub mod device_credentials;
mod device_identity;
pub mod secure_store;

use device_credentials::initialize_production_device_credential_vault;
#[cfg(feature = "desktop-e2e")]
use device_identity::initialize_ephemeral_identity;
#[cfg(not(feature = "desktop-e2e"))]
use device_identity::initialize_production_identity;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default().setup(|app| {
        #[cfg(feature = "desktop-e2e")]
        {
            let _production_identity_boundary = device_identity::initialize_production_identity;
            let _production_credential_boundary = initialize_production_device_credential_vault;
            let device_identity = initialize_ephemeral_identity()?;
            debug_assert_eq!(device_identity.as_bytes().len(), 32);
            app.manage(device_identity);
        }

        #[cfg(not(feature = "desktop-e2e"))]
        {
            let app_data_directory = app.path().app_data_dir()?;
            let device_identity = initialize_production_identity(&app_data_directory)?;
            let device_credential_vault =
                initialize_production_device_credential_vault(&app_data_directory)?;
            debug_assert_eq!(device_identity.as_bytes().len(), 32);
            app.manage(device_identity);
            app.manage(device_credential_vault);
        }
        Ok(())
    });

    #[cfg(feature = "desktop-e2e")]
    let builder = builder
        .plugin(tauri_plugin_wdio::init())
        .plugin(tauri_plugin_wdio_webdriver::init());

    builder
        .run(tauri::generate_context!())
        .expect("failed to run desktop application");
}

mod device_identity;

#[cfg(feature = "desktop-e2e")]
use device_identity::initialize_ephemeral_identity;
#[cfg(not(feature = "desktop-e2e"))]
use device_identity::initialize_production_identity;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(feature = "desktop-e2e")]
    let _production_identity_boundary = device_identity::initialize_production_identity;

    #[cfg(feature = "desktop-e2e")]
    let device_identity = initialize_ephemeral_identity()
        .expect("failed to initialize isolated desktop test identity");

    #[cfg(not(feature = "desktop-e2e"))]
    let device_identity =
        initialize_production_identity().expect("failed to initialize device identity");

    debug_assert_eq!(device_identity.as_bytes().len(), 32);

    let builder = tauri::Builder::default().manage(device_identity);

    #[cfg(feature = "desktop-e2e")]
    let builder = builder
        .plugin(tauri_plugin_wdio::init())
        .plugin(tauri_plugin_wdio_webdriver::init());

    builder
        .run(tauri::generate_context!())
        .expect("failed to run desktop application");
}

use super::{BrowserDiscoveryError, PathIdentity, SupportedBrowser, TrustedWindowsBrowser};
use std::collections::HashSet;
use std::ffi::{c_void, OsString};
use std::fs::{File, OpenOptions};
use std::mem::{size_of, zeroed};
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::os::windows::fs::OpenOptionsExt;
use std::os::windows::io::AsRawHandle;
use std::path::{Component, Path, PathBuf};
use std::ptr::{null, null_mut};
use windows_sys::core::GUID;
use windows_sys::Win32::Foundation::{ERROR_SUCCESS, HANDLE};
use windows_sys::Win32::Security::Cryptography::{
    szOID_COMMON_NAME, CertGetNameStringW, CERT_CONTEXT, CERT_NAME_ATTR_TYPE,
};
use windows_sys::Win32::Security::WinTrust::{
    WTHelperGetProvCertFromChain, WTHelperGetProvSignerFromChain, WTHelperProvDataFromStateData,
    WinVerifyTrust, WINTRUST_ACTION_GENERIC_VERIFY_V2, WINTRUST_DATA, WINTRUST_DATA_0,
    WINTRUST_FILE_INFO, WTD_CACHE_ONLY_URL_RETRIEVAL, WTD_CHOICE_FILE, WTD_REVOKE_NONE,
    WTD_SAFER_FLAG, WTD_STATEACTION_CLOSE, WTD_STATEACTION_VERIFY, WTD_UI_NONE,
};
use windows_sys::Win32::Storage::FileSystem::{
    GetFileAttributesW, GetFileInformationByHandle, GetFileVersionInfoSizeW, GetFileVersionInfoW,
    GetFinalPathNameByHandleW, VerQueryValueW, BY_HANDLE_FILE_INFORMATION,
    FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_OPEN_REPARSE_POINT, FILE_NAME_NORMALIZED,
    FILE_SHARE_READ, INVALID_FILE_ATTRIBUTES, VOLUME_NAME_DOS,
};
use windows_sys::Win32::System::Com::CoTaskMemFree;
use windows_sys::Win32::System::Registry::{
    RegGetValueW, HKEY, HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, RRF_RT_REG_SZ,
    RRF_SUBKEY_WOW6432KEY, RRF_SUBKEY_WOW6464KEY, RRF_ZEROONFAILURE,
};
use windows_sys::Win32::UI::Shell::{
    FOLDERID_LocalAppData, FOLDERID_ProgramFiles, FOLDERID_ProgramFilesX86, SHGetKnownFolderPath,
    KF_FLAG_DONT_VERIFY, KF_FLAG_NO_ALIAS,
};

const MAX_WINDOWS_PATH_UNITS: usize = 32_768;
const MAX_REGISTRY_VALUE_BYTES: u32 = 64 * 1024;
const MAX_VERSION_INFO_BYTES: u32 = 4 * 1024 * 1024;
const MAX_VERSION_STRING_UNITS: usize = 512;

#[derive(Clone, Copy)]
struct WindowsBrowserDefinition {
    browser: SupportedBrowser,
    registry_key: &'static str,
    relative_executable_path: &'static str,
    executable_name: &'static str,
    product_name: &'static str,
    publisher: &'static str,
}

const WINDOWS_BROWSER_DEFINITIONS: [WindowsBrowserDefinition; 2] = [
    WindowsBrowserDefinition {
        browser: SupportedBrowser::GoogleChrome,
        registry_key: "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\chrome.exe",
        relative_executable_path: "Google\\Chrome\\Application\\chrome.exe",
        executable_name: "chrome.exe",
        product_name: "Google Chrome",
        publisher: "Google LLC",
    },
    WindowsBrowserDefinition {
        browser: SupportedBrowser::MicrosoftEdge,
        registry_key: "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\msedge.exe",
        relative_executable_path: "Microsoft\\Edge\\Application\\msedge.exe",
        executable_name: "msedge.exe",
        product_name: "Microsoft Edge",
        publisher: "Microsoft Corporation",
    },
];

struct KnownFolders {
    program_files: Option<PathBuf>,
    program_files_x86: Option<PathBuf>,
    local_app_data: Option<PathBuf>,
}

impl KnownFolders {
    fn resolve() -> Result<Self, BrowserDiscoveryError> {
        let folders = Self {
            program_files: known_folder_path(&FOLDERID_ProgramFiles),
            program_files_x86: known_folder_path(&FOLDERID_ProgramFilesX86),
            local_app_data: known_folder_path(&FOLDERID_LocalAppData),
        };
        if folders.program_files.is_none()
            && folders.program_files_x86.is_none()
            && folders.local_app_data.is_none()
        {
            Err(BrowserDiscoveryError::discovery_unavailable())
        } else {
            Ok(folders)
        }
    }

    fn standard_paths(&self, definition: &WindowsBrowserDefinition) -> Vec<PathBuf> {
        [
            self.program_files.as_ref(),
            self.program_files_x86.as_ref(),
            self.local_app_data.as_ref(),
        ]
        .into_iter()
        .flatten()
        .map(|root| root.join(definition.relative_executable_path))
        .collect()
    }
}

trait WindowsBrowserVerifier {
    fn verify(
        &self,
        executable: &File,
        executable_path: &Path,
        definition: &WindowsBrowserDefinition,
    ) -> Result<(), ()>;
}

struct SystemWindowsBrowserVerifier;

impl WindowsBrowserVerifier for SystemWindowsBrowserVerifier {
    fn verify(
        &self,
        executable: &File,
        executable_path: &Path,
        definition: &WindowsBrowserDefinition,
    ) -> Result<(), ()> {
        let publisher = verify_authenticode_and_read_publisher(executable, executable_path)?;
        if publisher != definition.publisher {
            return Err(());
        }
        verify_version_resource(executable_path, definition)
    }
}

pub(super) fn discover() -> Result<Vec<TrustedWindowsBrowser>, BrowserDiscoveryError> {
    let folders = KnownFolders::resolve()?;
    discover_with(&folders, &SystemWindowsBrowserVerifier)
}

pub(super) fn revalidate(browser: &TrustedWindowsBrowser) -> Result<(), BrowserDiscoveryError> {
    let folders = KnownFolders::resolve()?;
    let definition = definition_for(browser.browser);
    if !folders
        .standard_paths(definition)
        .iter()
        .any(|path| paths_equal(path, &browser.executable_path))
    {
        return Err(BrowserDiscoveryError::path_invalidated(browser.browser));
    }
    revalidate_with(browser, definition, &SystemWindowsBrowserVerifier)
}

fn discover_with(
    folders: &KnownFolders,
    verifier: &dyn WindowsBrowserVerifier,
) -> Result<Vec<TrustedWindowsBrowser>, BrowserDiscoveryError> {
    let mut discovered = Vec::new();
    for definition in &WINDOWS_BROWSER_DEFINITIONS {
        let standard_paths = folders.standard_paths(definition);
        let registry_paths = read_app_paths_registry(definition.registry_key);
        let mut candidates = Vec::new();
        for registry_path in registry_paths {
            if standard_paths
                .iter()
                .any(|standard| paths_equal(standard, &registry_path))
            {
                candidates.push(registry_path);
            }
        }
        candidates.extend(standard_paths);
        deduplicate_paths(&mut candidates);

        for executable_path in candidates {
            match std::fs::symlink_metadata(&executable_path) {
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                Err(_) => {
                    return Err(BrowserDiscoveryError::candidate_rejected(
                        definition.browser,
                    ));
                }
                Ok(_) => {}
            }
            discovered.push(validate_candidate(definition, executable_path, verifier)?);
            break;
        }
    }
    Ok(discovered)
}

fn validate_candidate(
    definition: &WindowsBrowserDefinition,
    executable_path: PathBuf,
    verifier: &dyn WindowsBrowserVerifier,
) -> Result<TrustedWindowsBrowser, BrowserDiscoveryError> {
    let rejected = || BrowserDiscoveryError::candidate_rejected(definition.browser);
    let (file, initial_identity) =
        open_stable_executable(&executable_path).map_err(|_| rejected())?;
    verifier
        .verify(&file, &executable_path, definition)
        .map_err(|_| rejected())?;
    if file_identity(&file).map_err(|_| rejected())? != initial_identity
        || open_stable_executable(&executable_path)
            .map_err(|_| rejected())?
            .1
            != initial_identity
    {
        return Err(BrowserDiscoveryError::path_invalidated(definition.browser));
    }
    Ok(TrustedWindowsBrowser {
        browser: definition.browser,
        executable_path,
        product_name: definition.product_name,
        publisher: definition.publisher,
        executable_identity: initial_identity,
    })
}

fn revalidate_with(
    browser: &TrustedWindowsBrowser,
    definition: &WindowsBrowserDefinition,
    verifier: &dyn WindowsBrowserVerifier,
) -> Result<(), BrowserDiscoveryError> {
    let invalidated = || BrowserDiscoveryError::path_invalidated(browser.browser);
    let (file, initial_identity) =
        open_stable_executable(&browser.executable_path).map_err(|_| invalidated())?;
    if initial_identity != browser.executable_identity {
        return Err(invalidated());
    }
    verifier
        .verify(&file, &browser.executable_path, definition)
        .map_err(|_| invalidated())?;
    if file_identity(&file).map_err(|_| invalidated())? != initial_identity
        || open_stable_executable(&browser.executable_path)
            .map_err(|_| invalidated())?
            .1
            != initial_identity
    {
        return Err(invalidated());
    }
    Ok(())
}

fn definition_for(browser: SupportedBrowser) -> &'static WindowsBrowserDefinition {
    WINDOWS_BROWSER_DEFINITIONS
        .iter()
        .find(|definition| definition.browser == browser)
        .expect("every Windows browser has a fixed definition")
}

fn open_stable_executable(path: &Path) -> Result<(File, PathIdentity), ()> {
    ensure_no_reparse_components(path)?;
    let metadata = std::fs::symlink_metadata(path).map_err(|_| ())?;
    if !metadata.is_file() {
        return Err(());
    }
    let file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(|_| ())?;
    let identity = file_identity(&file)?;
    let canonical_path = std::fs::canonicalize(path).map_err(|_| ())?;
    if final_path(&file)? != normalized_path_key(&canonical_path) {
        return Err(());
    }
    ensure_no_reparse_components(path)?;
    Ok((file, identity))
}

fn file_identity(file: &File) -> Result<PathIdentity, ()> {
    let mut information: BY_HANDLE_FILE_INFORMATION = unsafe { zeroed() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle() as HANDLE, &mut information) } == 0
    {
        return Err(());
    }
    if information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(());
    }
    Ok(PathIdentity {
        device: u64::from(information.dwVolumeSerialNumber),
        inode: (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow),
        length: (u64::from(information.nFileSizeHigh) << 32) | u64::from(information.nFileSizeLow),
    })
}

fn final_path(file: &File) -> Result<String, ()> {
    let handle = file.as_raw_handle() as HANDLE;
    let flags = FILE_NAME_NORMALIZED | VOLUME_NAME_DOS;
    let required = unsafe { GetFinalPathNameByHandleW(handle, null_mut(), 0, flags) };
    if required == 0 || required as usize >= MAX_WINDOWS_PATH_UNITS {
        return Err(());
    }
    let mut buffer = vec![0_u16; required as usize + 1];
    let written = unsafe {
        GetFinalPathNameByHandleW(
            handle,
            buffer.as_mut_ptr(),
            buffer.len().try_into().map_err(|_| ())?,
            flags,
        )
    };
    if written == 0 || written as usize >= buffer.len() {
        return Err(());
    }
    let path = PathBuf::from(OsString::from_wide(&buffer[..written as usize]));
    Ok(normalized_path_key(&path))
}

fn ensure_no_reparse_components(path: &Path) -> Result<(), ()> {
    if !path.is_absolute() {
        return Err(());
    }
    let mut current = PathBuf::new();
    for component in path.components() {
        current.push(component.as_os_str());
        if matches!(component, Component::Prefix(_) | Component::RootDir) {
            continue;
        }
        let wide = wide_null(&current)?;
        let attributes = unsafe { GetFileAttributesW(wide.as_ptr()) };
        if attributes == INVALID_FILE_ATTRIBUTES || attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(());
        }
    }
    Ok(())
}

fn verify_authenticode_and_read_publisher(file: &File, path: &Path) -> Result<String, ()> {
    let wide_path = wide_null(path)?;
    let mut file_info = WINTRUST_FILE_INFO {
        cbStruct: size_of::<WINTRUST_FILE_INFO>().try_into().map_err(|_| ())?,
        pcwszFilePath: wide_path.as_ptr(),
        hFile: file.as_raw_handle() as HANDLE,
        pgKnownSubject: null_mut(),
    };
    let mut trust_data: WINTRUST_DATA = unsafe { zeroed() };
    trust_data.cbStruct = size_of::<WINTRUST_DATA>().try_into().map_err(|_| ())?;
    trust_data.dwUIChoice = WTD_UI_NONE;
    trust_data.fdwRevocationChecks = WTD_REVOKE_NONE;
    trust_data.dwUnionChoice = WTD_CHOICE_FILE;
    trust_data.Anonymous = WINTRUST_DATA_0 {
        pFile: &mut file_info,
    };
    trust_data.dwStateAction = WTD_STATEACTION_VERIFY;
    trust_data.dwProvFlags = WTD_SAFER_FLAG | WTD_CACHE_ONLY_URL_RETRIEVAL;
    let mut action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
    let verify_status = unsafe {
        WinVerifyTrust(
            null_mut(),
            &mut action,
            (&mut trust_data as *mut WINTRUST_DATA).cast(),
        )
    };
    let result = if verify_status == ERROR_SUCCESS as i32 {
        publisher_from_verified_state(trust_data.hWVTStateData)
    } else {
        Err(())
    };
    trust_data.dwStateAction = WTD_STATEACTION_CLOSE;
    unsafe {
        WinVerifyTrust(
            null_mut(),
            &mut action,
            (&mut trust_data as *mut WINTRUST_DATA).cast(),
        );
    }
    result
}

fn publisher_from_verified_state(state: HANDLE) -> Result<String, ()> {
    let provider = unsafe { WTHelperProvDataFromStateData(state) };
    if provider.is_null() {
        return Err(());
    }
    let signer = unsafe { WTHelperGetProvSignerFromChain(provider, 0, 0, 0) };
    if signer.is_null() {
        return Err(());
    }
    let provider_certificate = unsafe { WTHelperGetProvCertFromChain(signer, 0) };
    if provider_certificate.is_null() {
        return Err(());
    }
    let certificate = unsafe { (*provider_certificate).pCert };
    certificate_common_name(certificate)
}

fn certificate_common_name(certificate: *const CERT_CONTEXT) -> Result<String, ()> {
    if certificate.is_null() {
        return Err(());
    }
    let length = unsafe {
        CertGetNameStringW(
            certificate,
            CERT_NAME_ATTR_TYPE,
            0,
            szOID_COMMON_NAME.cast(),
            null_mut(),
            0,
        )
    };
    if length < 2 || length as usize > MAX_VERSION_STRING_UNITS {
        return Err(());
    }
    let mut buffer = vec![0_u16; length as usize];
    let written = unsafe {
        CertGetNameStringW(
            certificate,
            CERT_NAME_ATTR_TYPE,
            0,
            szOID_COMMON_NAME.cast(),
            buffer.as_mut_ptr(),
            length,
        )
    };
    if written != length || buffer.last().copied() != Some(0) {
        return Err(());
    }
    String::from_utf16(&buffer[..buffer.len() - 1]).map_err(|_| ())
}

fn verify_version_resource(path: &Path, definition: &WindowsBrowserDefinition) -> Result<(), ()> {
    let wide_path = wide_null(path)?;
    let mut ignored = 0_u32;
    let size = unsafe { GetFileVersionInfoSizeW(wide_path.as_ptr(), &mut ignored) };
    if size == 0 || size > MAX_VERSION_INFO_BYTES {
        return Err(());
    }
    let mut data = vec![0_u8; size as usize];
    if unsafe { GetFileVersionInfoW(wide_path.as_ptr(), 0, size, data.as_mut_ptr().cast()) } == 0 {
        return Err(());
    }
    for (language, code_page) in version_translations(&data)? {
        let product_name = query_version_string(&data, language, code_page, "ProductName")?;
        let company_name = query_version_string(&data, language, code_page, "CompanyName")?;
        let original_filename =
            query_version_string(&data, language, code_page, "OriginalFilename")?;
        if product_name == definition.product_name
            && company_name == definition.publisher
            && original_filename.eq_ignore_ascii_case(definition.executable_name)
        {
            return Ok(());
        }
    }
    Err(())
}

fn version_translations(data: &[u8]) -> Result<Vec<(u16, u16)>, ()> {
    let query = wide_null_str("\\VarFileInfo\\Translation")?;
    let mut pointer = null_mut::<c_void>();
    let mut length = 0_u32;
    if unsafe {
        VerQueryValueW(
            data.as_ptr().cast(),
            query.as_ptr(),
            &mut pointer,
            &mut length,
        )
    } == 0
        || pointer.is_null()
        || !(4..=256).contains(&length)
        || !length.is_multiple_of(4)
    {
        return Err(());
    }
    let bytes = unsafe { std::slice::from_raw_parts(pointer.cast::<u8>(), length as usize) };
    let mut translations = Vec::new();
    for chunk in bytes.chunks_exact(4) {
        translations.push((
            u16::from_le_bytes([chunk[0], chunk[1]]),
            u16::from_le_bytes([chunk[2], chunk[3]]),
        ));
    }
    Ok(translations)
}

fn query_version_string(
    data: &[u8],
    language: u16,
    code_page: u16,
    key: &str,
) -> Result<String, ()> {
    let query = wide_null_str(&format!(
        "\\StringFileInfo\\{language:04x}{code_page:04x}\\{key}"
    ))?;
    let mut pointer = null_mut::<c_void>();
    let mut length = 0_u32;
    if unsafe {
        VerQueryValueW(
            data.as_ptr().cast(),
            query.as_ptr(),
            &mut pointer,
            &mut length,
        )
    } == 0
        || pointer.is_null()
        || length < 2
        || length as usize > MAX_VERSION_STRING_UNITS
    {
        return Err(());
    }
    let units = unsafe { std::slice::from_raw_parts(pointer.cast::<u16>(), length as usize) };
    if units.last().copied() != Some(0) || units[..units.len() - 1].contains(&0) {
        return Err(());
    }
    let value = String::from_utf16(&units[..units.len() - 1]).map_err(|_| ())?;
    if value.is_empty() || value.chars().any(char::is_control) {
        return Err(());
    }
    Ok(value)
}

fn read_app_paths_registry(subkey: &str) -> Vec<PathBuf> {
    let mut values = Vec::new();
    for root in [HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER] {
        for view in [RRF_SUBKEY_WOW6464KEY, RRF_SUBKEY_WOW6432KEY] {
            if let Some(value) = read_registry_string(root, subkey, view) {
                values.push(value);
            }
        }
    }
    deduplicate_paths(&mut values);
    values
}

fn read_registry_string(root: HKEY, subkey: &str, view: u32) -> Option<PathBuf> {
    let subkey = wide_null_str(subkey).ok()?;
    let flags = RRF_RT_REG_SZ | RRF_ZEROONFAILURE | view;
    let mut value_type = 0_u32;
    let mut byte_count = 0_u32;
    let first = unsafe {
        RegGetValueW(
            root,
            subkey.as_ptr(),
            null(),
            flags,
            &mut value_type,
            null_mut(),
            &mut byte_count,
        )
    };
    if first != ERROR_SUCCESS
        || !(2..=MAX_REGISTRY_VALUE_BYTES).contains(&byte_count)
        || !byte_count.is_multiple_of(2)
    {
        return None;
    }
    let mut value = vec![0_u16; (byte_count / 2) as usize];
    let second = unsafe {
        RegGetValueW(
            root,
            subkey.as_ptr(),
            null(),
            flags,
            &mut value_type,
            value.as_mut_ptr().cast(),
            &mut byte_count,
        )
    };
    if second != ERROR_SUCCESS || value_type != 1 || value.last().copied() != Some(0) {
        return None;
    }
    while value.last().copied() == Some(0) {
        value.pop();
    }
    if value.is_empty() || value.contains(&0) {
        return None;
    }
    let path = PathBuf::from(OsString::from_wide(&value));
    path.is_absolute().then_some(path)
}

fn known_folder_path(folder_id: &GUID) -> Option<PathBuf> {
    let mut pointer = null_mut::<u16>();
    let result = unsafe {
        SHGetKnownFolderPath(
            folder_id,
            (KF_FLAG_DONT_VERIFY | KF_FLAG_NO_ALIAS) as u32,
            null_mut(),
            &mut pointer,
        )
    };
    if result < 0 {
        if !pointer.is_null() {
            unsafe { CoTaskMemFree(pointer.cast()) };
        }
        return None;
    }
    if pointer.is_null() {
        return None;
    }
    let converted = (|| {
        let mut length = 0_usize;
        while length < MAX_WINDOWS_PATH_UNITS && unsafe { *pointer.add(length) } != 0 {
            length += 1;
        }
        if length == 0 || length == MAX_WINDOWS_PATH_UNITS {
            return None;
        }
        let units = unsafe { std::slice::from_raw_parts(pointer, length) };
        let path = PathBuf::from(OsString::from_wide(units));
        path.is_absolute().then_some(path)
    })();
    unsafe { CoTaskMemFree(pointer.cast()) };
    converted
}

fn deduplicate_paths(paths: &mut Vec<PathBuf>) {
    let mut seen = HashSet::new();
    paths.retain(|path| seen.insert(normalized_path_key(path)));
}

fn paths_equal(left: &Path, right: &Path) -> bool {
    normalized_path_key(left) == normalized_path_key(right)
}

fn normalized_path_key(path: &Path) -> String {
    let mut value = path.to_string_lossy().replace('/', "\\");
    if let Some(stripped) = value.strip_prefix("\\\\?\\") {
        value = stripped.to_owned();
    }
    while value.ends_with('\\') && value.len() > 3 {
        value.pop();
    }
    value.to_lowercase()
}

fn wide_null(path: &Path) -> Result<Vec<u16>, ()> {
    let units = path.as_os_str().encode_wide().collect::<Vec<_>>();
    if units.is_empty() || units.len() >= MAX_WINDOWS_PATH_UNITS || units.contains(&0) {
        return Err(());
    }
    Ok(units.into_iter().chain(std::iter::once(0)).collect())
}

fn wide_null_str(value: &str) -> Result<Vec<u16>, ()> {
    let units = value.encode_utf16().collect::<Vec<_>>();
    if units.is_empty() || units.len() >= MAX_WINDOWS_PATH_UNITS || units.contains(&0) {
        return Err(());
    }
    Ok(units.into_iter().chain(std::iter::once(0)).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static NEXT_TEMPORARY_DIRECTORY: AtomicU64 = AtomicU64::new(0);

    struct TemporaryWindowsRoot {
        path: PathBuf,
    }

    impl TemporaryWindowsRoot {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "automation-tool-b5-03-{}-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .expect("system time")
                    .as_nanos(),
                NEXT_TEMPORARY_DIRECTORY.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir(&path).expect("temporary Windows root");
            Self { path }
        }

        fn install_fixture(&self, definition: &WindowsBrowserDefinition) -> PathBuf {
            let path = self.path.join(definition.relative_executable_path);
            std::fs::create_dir_all(path.parent().expect("browser parent"))
                .expect("browser hierarchy");
            std::fs::write(&path, b"fixture-browser").expect("browser fixture");
            path
        }
    }

    impl Drop for TemporaryWindowsRoot {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.path);
        }
    }

    struct AcceptAll;

    impl WindowsBrowserVerifier for AcceptAll {
        fn verify(
            &self,
            _executable: &File,
            _executable_path: &Path,
            _definition: &WindowsBrowserDefinition,
        ) -> Result<(), ()> {
            Ok(())
        }
    }

    struct RejectAll;

    impl WindowsBrowserVerifier for RejectAll {
        fn verify(
            &self,
            _executable: &File,
            _executable_path: &Path,
            _definition: &WindowsBrowserDefinition,
        ) -> Result<(), ()> {
            Err(())
        }
    }

    fn fixture_folders(root: &TemporaryWindowsRoot) -> KnownFolders {
        KnownFolders {
            program_files: Some(root.path.clone()),
            program_files_x86: None,
            local_app_data: None,
        }
    }

    #[test]
    fn standard_products_are_discovered_and_bad_signatures_fail_closed() {
        let root = TemporaryWindowsRoot::new();
        for definition in &WINDOWS_BROWSER_DEFINITIONS {
            root.install_fixture(definition);
        }
        let folders = fixture_folders(&root);
        let discovered = discover_with(&folders, &AcceptAll).expect("discover fixtures");
        assert_eq!(discovered.len(), 2);
        assert_eq!(discovered[0].product_name(), "Google Chrome");
        assert_eq!(discovered[1].product_name(), "Microsoft Edge");
        assert!(discover_with(&folders, &RejectAll).is_err());
    }

    #[test]
    fn replaced_path_is_invalidated_before_launch() {
        let root = TemporaryWindowsRoot::new();
        let executable = root.install_fixture(&WINDOWS_BROWSER_DEFINITIONS[0]);
        let folders = fixture_folders(&root);
        let browser = discover_with(&folders, &AcceptAll)
            .expect("discover fixture")
            .remove(0);
        let previous = executable.with_extension("previous");
        std::fs::rename(&executable, previous).expect("retain original identity");
        std::fs::write(&executable, b"replacement").expect("replace executable");
        assert!(revalidate_with(&browser, &WINDOWS_BROWSER_DEFINITIONS[0], &AcceptAll).is_err());
    }
}

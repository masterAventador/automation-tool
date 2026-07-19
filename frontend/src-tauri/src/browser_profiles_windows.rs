use super::{BrowserProfileError, CreateProfileError};
use std::ffi::{c_void, OsStr};
use std::fs::{File, OpenOptions};
use std::mem::{size_of, zeroed};
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::os::windows::fs::OpenOptionsExt;
use std::os::windows::io::{AsRawHandle, FromRawHandle};
use std::path::{Path, PathBuf};
use std::ptr::{null, null_mut};
use windows_sys::Wdk::Foundation::OBJECT_ATTRIBUTES;
use windows_sys::Wdk::Storage::FileSystem::{
    NtCreateFile, FILE_CREATE, FILE_DIRECTORY_FILE, FILE_OPEN, FILE_OPEN_FOR_BACKUP_INTENT,
    FILE_OPEN_IF, FILE_OPEN_REPARSE_POINT,
};
use windows_sys::Win32::Foundation::{
    CloseHandle, LocalFree, ERROR_SUCCESS, HANDLE, HLOCAL, INVALID_HANDLE_VALUE,
    OBJ_CASE_INSENSITIVE, STATUS_NOT_A_DIRECTORY, STATUS_OBJECT_NAME_COLLISION,
    STATUS_OBJECT_NAME_NOT_FOUND, STATUS_REPARSE_POINT_ENCOUNTERED, STATUS_SUCCESS, UNICODE_STRING,
};
use windows_sys::Win32::Security::Authorization::{
    GetSecurityInfo, SetSecurityInfo, SE_FILE_OBJECT,
};
use windows_sys::Win32::Security::{
    AclSizeInformation, AddAccessAllowedAceEx, EqualSid, GetAce, GetAclInformation, GetLengthSid,
    GetSecurityDescriptorControl, GetTokenInformation, InitializeAcl, TokenUser,
    ACCESS_ALLOWED_ACE, ACL, ACL_REVISION, ACL_SIZE_INFORMATION, CONTAINER_INHERIT_ACE,
    DACL_SECURITY_INFORMATION, OBJECT_INHERIT_ACE, PROTECTED_DACL_SECURITY_INFORMATION, PSID,
    SE_DACL_PROTECTED, TOKEN_QUERY, TOKEN_USER,
};
use windows_sys::Win32::Storage::FileSystem::{
    GetFileAttributesW, GetFileInformationByHandle, GetFinalPathNameByHandleW,
    BY_HANDLE_FILE_INFORMATION, FILE_ALL_ACCESS, FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_NORMAL,
    FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_GENERIC_READ, FILE_NAME_NORMALIZED, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE,
    INVALID_FILE_ATTRIBUTES, READ_CONTROL, VOLUME_NAME_DOS, WRITE_DAC,
};
use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
use windows_sys::Win32::System::IO::IO_STATUS_BLOCK;

const MAX_WINDOWS_PATH_UNITS: usize = 32_768;

#[derive(Clone, Copy, Eq, PartialEq)]
struct DirectoryIdentity {
    volume: u64,
    index: u64,
}

struct DirectoryHandle {
    file: File,
    identity: DirectoryIdentity,
    path: PathBuf,
}

#[derive(Clone, Copy)]
enum AclPolicy {
    Ignore,
    Repair,
    Require,
}

pub(super) struct PlatformProfileStore {
    app_data: DirectoryHandle,
    profile_root: DirectoryHandle,
    platform: DirectoryHandle,
    profile_root_name: String,
    platform_name: String,
}

pub(super) struct PlatformProfile {
    directory: DirectoryHandle,
}

impl PlatformProfileStore {
    pub(super) fn initialize(
        app_data_path: &Path,
        profile_root_name: &str,
        platform_name: &str,
    ) -> Result<Self, BrowserProfileError> {
        require_safe_name(profile_root_name)?;
        require_safe_name(platform_name)?;
        let app_data = open_absolute_directory(app_data_path, AclPolicy::Repair)?;
        let profile_root = open_relative_directory(
            &app_data,
            profile_root_name,
            FILE_OPEN_IF,
            AclPolicy::Repair,
        )?;
        let platform = open_relative_directory(
            &profile_root,
            platform_name,
            FILE_OPEN_IF,
            AclPolicy::Repair,
        )?;
        let store = Self {
            app_data,
            profile_root,
            platform,
            profile_root_name: profile_root_name.to_owned(),
            platform_name: platform_name.to_owned(),
        };
        store.revalidate_layout()?;
        Ok(store)
    }

    pub(super) fn create_profile(
        &self,
        profile_id: &str,
    ) -> Result<PlatformProfile, CreateProfileError> {
        self.revalidate_layout()
            .map_err(CreateProfileError::Failure)?;
        let directory = match open_relative_directory(
            &self.platform,
            profile_id,
            FILE_CREATE,
            AclPolicy::Repair,
        ) {
            Ok(directory) => directory,
            Err(error) if error.code() == super::BrowserProfileErrorCode::ProfileNotFound => {
                return Err(CreateProfileError::Collision);
            }
            Err(error) => return Err(CreateProfileError::Failure(error)),
        };
        self.revalidate_layout()
            .map_err(CreateProfileError::Failure)?;
        let reopened =
            open_relative_directory(&self.platform, profile_id, FILE_OPEN, AclPolicy::Require)
                .map_err(CreateProfileError::Failure)?;
        if reopened.identity != directory.identity {
            return Err(CreateProfileError::Failure(
                BrowserProfileError::identity_changed(),
            ));
        }
        Ok(PlatformProfile { directory })
    }

    pub(super) fn open_profile(
        &self,
        profile_id: &str,
    ) -> Result<PlatformProfile, BrowserProfileError> {
        self.revalidate_layout()?;
        let directory =
            open_relative_directory(&self.platform, profile_id, FILE_OPEN, AclPolicy::Require)?;
        self.revalidate_layout()?;
        let reopened =
            open_relative_directory(&self.platform, profile_id, FILE_OPEN, AclPolicy::Require)?;
        if reopened.identity != directory.identity {
            return Err(BrowserProfileError::identity_changed());
        }
        Ok(PlatformProfile { directory })
    }

    pub(super) fn revalidate_profile(
        &self,
        profile_id: &str,
        profile: &PlatformProfile,
    ) -> Result<(), BrowserProfileError> {
        self.revalidate_layout()?;
        if directory_identity(&profile.directory.file)? != profile.directory.identity {
            return Err(BrowserProfileError::identity_changed());
        }
        let reopened =
            open_relative_directory(&self.platform, profile_id, FILE_OPEN, AclPolicy::Ignore)
                .map_err(|error| match error.code() {
                    super::BrowserProfileErrorCode::ProfileNotFound => {
                        BrowserProfileError::identity_changed()
                    }
                    _ => error,
                })?;
        if reopened.identity != profile.directory.identity {
            return Err(BrowserProfileError::identity_changed());
        }
        verify_private_acl(&profile.directory.file)?;
        verify_private_acl(&reopened.file)?;
        self.revalidate_layout()
    }

    fn revalidate_layout(&self) -> Result<(), BrowserProfileError> {
        for directory in [&self.app_data, &self.profile_root, &self.platform] {
            if directory_identity(&directory.file)? != directory.identity
                || final_path(&directory.file)? != normalized_path_key(&directory.path)
            {
                return Err(BrowserProfileError::identity_changed());
            }
            verify_private_acl(&directory.file)?;
        }
        let app_data = open_absolute_directory(&self.app_data.path, AclPolicy::Ignore)?;
        if app_data.identity != self.app_data.identity {
            return Err(BrowserProfileError::identity_changed());
        }
        verify_private_acl(&app_data.file)?;
        let profile_root = open_relative_directory(
            &self.app_data,
            &self.profile_root_name,
            FILE_OPEN,
            AclPolicy::Ignore,
        )?;
        if profile_root.identity != self.profile_root.identity {
            return Err(BrowserProfileError::identity_changed());
        }
        verify_private_acl(&profile_root.file)?;
        let platform = open_relative_directory(
            &self.profile_root,
            &self.platform_name,
            FILE_OPEN,
            AclPolicy::Ignore,
        )?;
        if platform.identity != self.platform.identity {
            return Err(BrowserProfileError::identity_changed());
        }
        verify_private_acl(&platform.file)?;
        Ok(())
    }
}

fn open_absolute_directory(
    path: &Path,
    acl_policy: AclPolicy,
) -> Result<DirectoryHandle, BrowserProfileError> {
    ensure_no_reparse_components(path)?;
    let file = OpenOptions::new()
        .access_mode(FILE_GENERIC_READ | READ_CONTROL | WRITE_DAC)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(|_| BrowserProfileError::storage_unavailable())?;
    let identity = directory_identity(&file)?;
    if final_path(&file)? != normalized_path_key(path) {
        return Err(BrowserProfileError::unsafe_directory());
    }
    enforce_acl_policy(&file, acl_policy)?;
    ensure_no_reparse_components(path)?;
    if directory_identity(&file)? != identity {
        return Err(BrowserProfileError::identity_changed());
    }
    Ok(DirectoryHandle {
        file,
        identity,
        path: path.to_path_buf(),
    })
}

fn open_relative_directory(
    parent: &DirectoryHandle,
    name: &str,
    disposition: u32,
    acl_policy: AclPolicy,
) -> Result<DirectoryHandle, BrowserProfileError> {
    require_safe_name(name)?;
    if directory_identity(&parent.file)? != parent.identity {
        return Err(BrowserProfileError::identity_changed());
    }
    let mut name_units = OsStr::new(name).encode_wide().collect::<Vec<_>>();
    let byte_length = name_units
        .len()
        .checked_mul(2)
        .and_then(|value| u16::try_from(value).ok())
        .ok_or_else(BrowserProfileError::unsafe_directory)?;
    let object_name = UNICODE_STRING {
        Length: byte_length,
        MaximumLength: byte_length,
        Buffer: name_units.as_mut_ptr(),
    };
    let attributes = OBJECT_ATTRIBUTES {
        Length: size_of::<OBJECT_ATTRIBUTES>() as u32,
        RootDirectory: parent.file.as_raw_handle() as HANDLE,
        ObjectName: &object_name,
        Attributes: OBJ_CASE_INSENSITIVE,
        SecurityDescriptor: null(),
        SecurityQualityOfService: null(),
    };
    let mut io_status = IO_STATUS_BLOCK::default();
    let mut handle = INVALID_HANDLE_VALUE;
    let status = unsafe {
        NtCreateFile(
            &mut handle,
            FILE_GENERIC_READ | READ_CONTROL | WRITE_DAC,
            &attributes,
            &mut io_status,
            null(),
            FILE_ATTRIBUTE_NORMAL,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            disposition,
            FILE_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT | FILE_OPEN_FOR_BACKUP_INTENT,
            null(),
            0,
        )
    };
    if status != STATUS_SUCCESS {
        return Err(match status {
            STATUS_OBJECT_NAME_NOT_FOUND => BrowserProfileError::profile_not_found(),
            STATUS_OBJECT_NAME_COLLISION if disposition == FILE_CREATE => {
                BrowserProfileError::profile_not_found()
            }
            STATUS_REPARSE_POINT_ENCOUNTERED | STATUS_NOT_A_DIRECTORY => {
                BrowserProfileError::unsafe_directory()
            }
            _ => BrowserProfileError::storage_unavailable(),
        });
    }
    let file = unsafe { File::from_raw_handle(handle as _) };
    let path = parent.path.join(name);
    let identity = directory_identity(&file)?;
    if final_path(&file)? != normalized_path_key(&path) {
        return Err(BrowserProfileError::unsafe_directory());
    }
    enforce_acl_policy(&file, acl_policy)?;
    if directory_identity(&parent.file)? != parent.identity
        || directory_identity(&file)? != identity
    {
        return Err(BrowserProfileError::identity_changed());
    }
    Ok(DirectoryHandle {
        file,
        identity,
        path,
    })
}

fn enforce_acl_policy(file: &File, policy: AclPolicy) -> Result<(), BrowserProfileError> {
    match policy {
        AclPolicy::Ignore => Ok(()),
        AclPolicy::Repair => {
            apply_private_acl(file)?;
            verify_private_acl(file)
        }
        AclPolicy::Require => verify_private_acl(file),
    }
}

fn directory_identity(file: &File) -> Result<DirectoryIdentity, BrowserProfileError> {
    let mut information: BY_HANDLE_FILE_INFORMATION = unsafe { zeroed() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle() as HANDLE, &mut information) } == 0
    {
        return Err(BrowserProfileError::storage_unavailable());
    }
    if information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY == 0
        || information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
    {
        return Err(BrowserProfileError::unsafe_directory());
    }
    Ok(DirectoryIdentity {
        volume: u64::from(information.dwVolumeSerialNumber),
        index: (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow),
    })
}

fn apply_private_acl(file: &File) -> Result<(), BrowserProfileError> {
    let user = CurrentUserSid::load()?;
    let sid = user.sid();
    let sid_length = unsafe { GetLengthSid(sid) } as usize;
    if sid_length == 0 {
        return Err(BrowserProfileError::storage_unavailable());
    }
    let acl_length = size_of::<ACL>()
        .checked_add(size_of::<ACCESS_ALLOWED_ACE>())
        .and_then(|value| value.checked_sub(size_of::<u32>()))
        .and_then(|value| value.checked_add(sid_length))
        .ok_or_else(BrowserProfileError::storage_unavailable)?;
    let mut acl_buffer = vec![0_u32; acl_length.div_ceil(size_of::<u32>())];
    let acl = acl_buffer.as_mut_ptr().cast::<ACL>();
    if unsafe { InitializeAcl(acl, acl_length as u32, ACL_REVISION) } == 0
        || unsafe {
            AddAccessAllowedAceEx(
                acl,
                ACL_REVISION,
                OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE,
                FILE_ALL_ACCESS,
                sid,
            )
        } == 0
    {
        return Err(BrowserProfileError::storage_unavailable());
    }
    let status = unsafe {
        SetSecurityInfo(
            file.as_raw_handle() as HANDLE,
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            null_mut(),
            null_mut(),
            acl,
            null(),
        )
    };
    if status != ERROR_SUCCESS {
        return Err(BrowserProfileError::storage_unavailable());
    }
    Ok(())
}

fn verify_private_acl(file: &File) -> Result<(), BrowserProfileError> {
    let user = CurrentUserSid::load()?;
    let mut owner: PSID = null_mut();
    let mut dacl: *mut ACL = null_mut();
    let mut descriptor = null_mut();
    let status = unsafe {
        GetSecurityInfo(
            file.as_raw_handle() as HANDLE,
            SE_FILE_OBJECT,
            windows_sys::Win32::Security::OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &mut owner,
            null_mut(),
            &mut dacl,
            null_mut(),
            &mut descriptor,
        )
    };
    if status != ERROR_SUCCESS || descriptor.is_null() {
        return Err(BrowserProfileError::storage_unavailable());
    }
    let result = verify_private_acl_parts(descriptor, owner, dacl, user.sid());
    unsafe {
        LocalFree(descriptor as HLOCAL);
    }
    result
}

fn verify_private_acl_parts(
    descriptor: *mut c_void,
    owner: PSID,
    dacl: *mut ACL,
    user: PSID,
) -> Result<(), BrowserProfileError> {
    if owner.is_null() || dacl.is_null() || unsafe { EqualSid(owner, user) } == 0 {
        return Err(BrowserProfileError::unsafe_directory());
    }
    let mut control = 0_u16;
    let mut revision = 0_u32;
    if unsafe { GetSecurityDescriptorControl(descriptor, &mut control, &mut revision) } == 0
        || control & SE_DACL_PROTECTED == 0
    {
        return Err(BrowserProfileError::unsafe_directory());
    }
    let mut information = ACL_SIZE_INFORMATION::default();
    if unsafe {
        GetAclInformation(
            dacl,
            (&mut information as *mut ACL_SIZE_INFORMATION).cast(),
            size_of::<ACL_SIZE_INFORMATION>() as u32,
            AclSizeInformation,
        )
    } == 0
        || information.AceCount != 1
    {
        return Err(BrowserProfileError::unsafe_directory());
    }
    let mut ace: *mut c_void = null_mut();
    if unsafe { GetAce(dacl, 0, &mut ace) } == 0 || ace.is_null() {
        return Err(BrowserProfileError::unsafe_directory());
    }
    let allowed = unsafe { &*ace.cast::<ACCESS_ALLOWED_ACE>() };
    let ace_sid = (&allowed.SidStart as *const u32)
        .cast_mut()
        .cast::<c_void>();
    if allowed.Header.AceType
        != windows_sys::Win32::System::SystemServices::ACCESS_ALLOWED_ACE_TYPE as u8
        || allowed.Header.AceFlags & (OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE) as u8
            != (OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE) as u8
        || allowed.Mask != FILE_ALL_ACCESS
        || unsafe { EqualSid(ace_sid, user) } == 0
    {
        return Err(BrowserProfileError::unsafe_directory());
    }
    Ok(())
}

struct CurrentUserSid {
    buffer: Vec<usize>,
}

impl CurrentUserSid {
    fn load() -> Result<Self, BrowserProfileError> {
        let mut token: HANDLE = null_mut();
        if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
            return Err(BrowserProfileError::storage_unavailable());
        }
        let mut required = 0_u32;
        unsafe {
            GetTokenInformation(token, TokenUser, null_mut(), 0, &mut required);
        }
        if required < size_of::<TOKEN_USER>() as u32 {
            unsafe {
                CloseHandle(token);
            }
            return Err(BrowserProfileError::storage_unavailable());
        }
        let mut buffer = vec![0_usize; (required as usize).div_ceil(size_of::<usize>())];
        let success = unsafe {
            GetTokenInformation(
                token,
                TokenUser,
                buffer.as_mut_ptr().cast(),
                required,
                &mut required,
            )
        };
        unsafe {
            CloseHandle(token);
        }
        if success == 0 {
            return Err(BrowserProfileError::storage_unavailable());
        }
        Ok(Self { buffer })
    }

    fn sid(&self) -> PSID {
        unsafe { (*(self.buffer.as_ptr().cast::<TOKEN_USER>())).User.Sid }
    }
}

fn ensure_no_reparse_components(path: &Path) -> Result<(), BrowserProfileError> {
    if !path.is_absolute() {
        return Err(BrowserProfileError::unsafe_directory());
    }
    let mut current = PathBuf::new();
    for component in path.components() {
        current.push(component.as_os_str());
        if matches!(
            component,
            std::path::Component::Prefix(_) | std::path::Component::RootDir
        ) {
            continue;
        }
        let wide = wide_null(&current)?;
        let attributes = unsafe { GetFileAttributesW(wide.as_ptr()) };
        if attributes == INVALID_FILE_ATTRIBUTES {
            return Err(BrowserProfileError::storage_unavailable());
        }
        if attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || attributes & FILE_ATTRIBUTE_DIRECTORY == 0
        {
            return Err(BrowserProfileError::unsafe_directory());
        }
    }
    Ok(())
}

fn final_path(file: &File) -> Result<String, BrowserProfileError> {
    let handle = file.as_raw_handle() as HANDLE;
    let flags = FILE_NAME_NORMALIZED | VOLUME_NAME_DOS;
    let required = unsafe { GetFinalPathNameByHandleW(handle, null_mut(), 0, flags) };
    if required == 0 || required as usize >= MAX_WINDOWS_PATH_UNITS {
        return Err(BrowserProfileError::storage_unavailable());
    }
    let mut buffer = vec![0_u16; required as usize + 1];
    let written = unsafe {
        GetFinalPathNameByHandleW(handle, buffer.as_mut_ptr(), buffer.len() as u32, flags)
    };
    if written == 0 || written as usize >= buffer.len() {
        return Err(BrowserProfileError::storage_unavailable());
    }
    Ok(normalized_path_key(&PathBuf::from(
        std::ffi::OsString::from_wide(&buffer[..written as usize]),
    )))
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

fn wide_null(path: &Path) -> Result<Vec<u16>, BrowserProfileError> {
    let units = path.as_os_str().encode_wide().collect::<Vec<_>>();
    if units.is_empty() || units.len() >= MAX_WINDOWS_PATH_UNITS || units.contains(&0) {
        return Err(BrowserProfileError::unsafe_directory());
    }
    Ok(units.into_iter().chain(std::iter::once(0)).collect())
}

fn require_safe_name(value: &str) -> Result<(), BrowserProfileError> {
    if value.is_empty()
        || value == "."
        || value == ".."
        || value.len() > 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
    {
        return Err(BrowserProfileError::unsafe_directory());
    }
    Ok(())
}

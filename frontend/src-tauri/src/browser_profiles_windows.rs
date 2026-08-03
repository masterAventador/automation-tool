use super::{BrowserProfileError, CreateProfileError};
use std::ffi::{c_void, OsStr};
use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read, Seek, SeekFrom, Write};
use std::mem::{size_of, zeroed};
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::os::windows::fs::OpenOptionsExt;
use std::os::windows::io::{AsRawHandle, FromRawHandle};
use std::path::{Path, PathBuf};
use std::ptr::{null, null_mut};
use windows_sys::Wdk::Foundation::OBJECT_ATTRIBUTES;
use windows_sys::Wdk::Storage::FileSystem::{
    NtCreateFile, FILE_CREATE, FILE_DIRECTORY_FILE, FILE_NON_DIRECTORY_FILE, FILE_OPEN,
    FILE_OPEN_FOR_BACKUP_INTENT, FILE_OPEN_IF, FILE_OPEN_REPARSE_POINT,
    FILE_SYNCHRONOUS_IO_NONALERT,
};
use windows_sys::Win32::Foundation::{
    CloseHandle, GetLastError, LocalFree, ERROR_LOCK_VIOLATION, ERROR_SUCCESS, HANDLE, HLOCAL,
    INVALID_HANDLE_VALUE, OBJ_CASE_INSENSITIVE, STATUS_FILE_IS_A_DIRECTORY, STATUS_NOT_A_DIRECTORY,
    STATUS_OBJECT_NAME_COLLISION, STATUS_OBJECT_NAME_NOT_FOUND, STATUS_REPARSE_POINT_ENCOUNTERED,
    STATUS_SUCCESS, UNICODE_STRING,
};
use windows_sys::Win32::Security::Authorization::{
    GetSecurityInfo, SetSecurityInfo, SE_FILE_OBJECT,
};
use windows_sys::Win32::Security::{
    AclSizeInformation, AddAccessAllowedAceEx, EqualSid, GetAce, GetAclInformation, GetLengthSid,
    GetSecurityDescriptorControl, GetTokenInformation, InitializeAcl, InitializeSecurityDescriptor,
    SetSecurityDescriptorControl, SetSecurityDescriptorDacl, SetSecurityDescriptorOwner, TokenUser,
    ACCESS_ALLOWED_ACE, ACL, ACL_REVISION, ACL_SIZE_INFORMATION, CONTAINER_INHERIT_ACE,
    DACL_SECURITY_INFORMATION, OBJECT_INHERIT_ACE, OWNER_SECURITY_INFORMATION,
    PROTECTED_DACL_SECURITY_INFORMATION, PSID, SECURITY_DESCRIPTOR, SE_DACL_PROTECTED, TOKEN_QUERY,
    TOKEN_USER,
};
use windows_sys::Win32::Storage::FileSystem::{
    GetFileAttributesW, GetFileInformationByHandle, GetFinalPathNameByHandleW, LockFileEx,
    BY_HANDLE_FILE_INFORMATION, FILE_ALL_ACCESS, FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_NORMAL,
    FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_GENERIC_READ, FILE_GENERIC_WRITE, FILE_NAME_NORMALIZED, FILE_SHARE_DELETE,
    FILE_SHARE_READ, FILE_SHARE_WRITE, INVALID_FILE_ATTRIBUTES, LOCKFILE_EXCLUSIVE_LOCK,
    LOCKFILE_FAIL_IMMEDIATELY, READ_CONTROL, VOLUME_NAME_DOS, WRITE_DAC, WRITE_OWNER,
};
use windows_sys::Win32::System::SystemServices::SECURITY_DESCRIPTOR_REVISION;
use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
use windows_sys::Win32::System::IO::{IO_STATUS_BLOCK, OVERLAPPED};

const MAX_WINDOWS_PATH_UNITS: usize = 32_768;
// The lease lives beside, never inside, Chromium's user-data-dir. The frozen
// Playwright runtime rejects a profile containing any file locked by the App.
const PROFILE_LEASE_FILE_PREFIX: &str = ".automation-tool-profile-lease-v1-";
const ACTIVE_LOCK_MARKER: &[u8] = br#"{"state":"active","version":1}"#;
const MAX_LOCK_STATE_BYTES: usize = 64;
const DIRECTORY_ACE_FLAGS: u32 = OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE;
const FILE_ACE_FLAGS: u32 = 0;

struct PrivateSecurityDescriptor {
    descriptor: SECURITY_DESCRIPTOR,
    _acl_buffer: Vec<u32>,
    _user: CurrentUserSid,
}

impl PrivateSecurityDescriptor {
    fn new(ace_flags: u32) -> Result<Self, BrowserProfileError> {
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
        let mut descriptor = SECURITY_DESCRIPTOR::default();
        if unsafe { InitializeAcl(acl, acl_length as u32, ACL_REVISION) } == 0
            || unsafe { AddAccessAllowedAceEx(acl, ACL_REVISION, ace_flags, FILE_ALL_ACCESS, sid) }
                == 0
            || unsafe {
                InitializeSecurityDescriptor(
                    (&mut descriptor as *mut SECURITY_DESCRIPTOR).cast(),
                    SECURITY_DESCRIPTOR_REVISION,
                )
            } == 0
            || unsafe {
                SetSecurityDescriptorOwner(
                    (&mut descriptor as *mut SECURITY_DESCRIPTOR).cast(),
                    sid,
                    0,
                )
            } == 0
            || unsafe {
                SetSecurityDescriptorDacl(
                    (&mut descriptor as *mut SECURITY_DESCRIPTOR).cast(),
                    1,
                    acl,
                    0,
                )
            } == 0
            || unsafe {
                SetSecurityDescriptorControl(
                    (&mut descriptor as *mut SECURITY_DESCRIPTOR).cast(),
                    SE_DACL_PROTECTED,
                    SE_DACL_PROTECTED,
                )
            } == 0
        {
            return Err(BrowserProfileError::storage_unavailable());
        }
        Ok(Self {
            descriptor,
            _acl_buffer: acl_buffer,
            _user: user,
        })
    }

    fn as_ptr(&self) -> *const SECURITY_DESCRIPTOR {
        &self.descriptor
    }
}

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

impl AclPolicy {
    /// The extra access repair needs, and that only repair is allowed to ask for.
    ///
    /// A process creates directories owned by its token's *default* owner, which
    /// under elevation is `BUILTIN\Administrators` rather than the token user.
    /// Repair therefore has to write an owner as well as a DACL, and
    /// `SetSecurityInfo` refuses to write one without `WRITE_OWNER`. Verifying
    /// and read-only opens keep the narrower mask they have always had.
    fn extra_access(self) -> u32 {
        match self {
            Self::Repair => WRITE_OWNER,
            Self::Ignore | Self::Require => 0,
        }
    }
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
    lease_directory: DirectoryHandle,
    lease_name: String,
}

pub(super) struct PlatformProfileLock {
    file: File,
    file_identity: FileIdentity,
    profile_directory: DirectoryHandle,
    lease_directory: DirectoryHandle,
    lease_name: String,
    share_delete: bool,
}

#[derive(Clone, Copy, Eq, PartialEq)]
struct FileIdentity {
    volume: u64,
    index: u64,
}

impl PlatformProfile {
    pub(super) fn try_acquire_lock(&self) -> Result<PlatformProfileLock, BrowserProfileError> {
        self.acquire_lock(false)
    }

    fn try_acquire_removal_lock(&self) -> Result<PlatformProfileLock, BrowserProfileError> {
        self.acquire_lock(true)
    }

    fn acquire_lock(
        &self,
        allow_abandoned_active_marker: bool,
    ) -> Result<PlatformProfileLock, BrowserProfileError> {
        if directory_identity(&self.directory.file)? != self.directory.identity {
            return Err(BrowserProfileError::identity_changed());
        }
        if directory_identity(&self.lease_directory.file)? != self.lease_directory.identity {
            return Err(BrowserProfileError::identity_changed());
        }
        verify_private_acl(&self.directory.file)?;
        verify_private_acl(&self.lease_directory.file)?;
        let profile_directory = DirectoryHandle {
            file: self
                .directory
                .file
                .try_clone()
                .map_err(|_| BrowserProfileError::storage_unavailable())?,
            identity: self.directory.identity,
            path: self.directory.path.clone(),
        };
        let lease_directory = DirectoryHandle {
            file: self
                .lease_directory
                .file
                .try_clone()
                .map_err(|_| BrowserProfileError::storage_unavailable())?,
            identity: self.lease_directory.identity,
            path: self.lease_directory.path.clone(),
        };
        let mut file = match open_relative_lock_file(
            &lease_directory,
            &self.lease_name,
            FILE_CREATE,
            AclPolicy::Repair,
            allow_abandoned_active_marker,
        ) {
            Ok(file) => file,
            Err(error) if error.code() == super::BrowserProfileErrorCode::ProfileNotFound => {
                open_relative_lock_file(
                    &lease_directory,
                    &self.lease_name,
                    FILE_OPEN,
                    AclPolicy::Require,
                    allow_abandoned_active_marker,
                )?
            }
            Err(error) => return Err(error),
        };
        let file_identity = lock_file_identity(&file)?;
        let mut overlapped = OVERLAPPED::default();
        let locked = unsafe {
            LockFileEx(
                file.as_raw_handle() as HANDLE,
                LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY,
                0,
                u32::MAX,
                u32::MAX,
                &mut overlapped,
            )
        };
        if locked == 0 {
            return Err(if unsafe { GetLastError() } == ERROR_LOCK_VIOLATION {
                BrowserProfileError::profile_in_use()
            } else {
                BrowserProfileError::storage_unavailable()
            });
        }
        let reopened = open_relative_lock_file(
            &lease_directory,
            &self.lease_name,
            FILE_OPEN,
            AclPolicy::Require,
            allow_abandoned_active_marker,
        )?;
        if lock_file_identity(&reopened)? != file_identity
            || directory_identity(&profile_directory.file)? != profile_directory.identity
            || directory_identity(&lease_directory.file)? != lease_directory.identity
        {
            return Err(BrowserProfileError::identity_changed());
        }
        let state = read_lock_state(&mut file)?;
        if !(state.is_empty() || allow_abandoned_active_marker && state == ACTIVE_LOCK_MARKER) {
            return Err(BrowserProfileError::recovery_required());
        }
        write_lock_state(&mut file, ACTIVE_LOCK_MARKER)?;
        let reopened = open_relative_lock_file(
            &lease_directory,
            &self.lease_name,
            FILE_OPEN,
            AclPolicy::Require,
            allow_abandoned_active_marker,
        )?;
        if lock_file_identity(&reopened)? != file_identity
            || directory_identity(&profile_directory.file)? != profile_directory.identity
            || directory_identity(&lease_directory.file)? != lease_directory.identity
        {
            return Err(BrowserProfileError::identity_changed());
        }
        Ok(PlatformProfileLock {
            file,
            file_identity,
            profile_directory,
            lease_directory,
            lease_name: self.lease_name.clone(),
            share_delete: allow_abandoned_active_marker,
        })
    }
}

impl PlatformProfileLock {
    pub(super) fn release(mut self) -> Result<(), BrowserProfileError> {
        if directory_identity(&self.profile_directory.file)? != self.profile_directory.identity {
            return Err(BrowserProfileError::identity_changed());
        }
        if directory_identity(&self.lease_directory.file)? != self.lease_directory.identity {
            return Err(BrowserProfileError::identity_changed());
        }
        let reopened = open_relative_lock_file(
            &self.lease_directory,
            &self.lease_name,
            FILE_OPEN,
            AclPolicy::Require,
            self.share_delete,
        )?;
        if lock_file_identity(&self.file)? != self.file_identity
            || lock_file_identity(&reopened)? != self.file_identity
        {
            return Err(BrowserProfileError::identity_changed());
        }
        if read_lock_state(&mut self.file)? != ACTIVE_LOCK_MARKER {
            return Err(BrowserProfileError::recovery_required());
        }
        write_lock_state(&mut self.file, b"")
    }
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
        self.profile(profile_id, directory)
            .map_err(CreateProfileError::Failure)
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
        self.profile(profile_id, directory)
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
        if directory_identity(&profile.lease_directory.file)? != self.platform.identity {
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
        verify_private_acl(&profile.lease_directory.file)?;
        self.revalidate_layout()
    }

    pub(super) fn remove_profile(&self, profile_id: &str) -> Result<(), BrowserProfileError> {
        crate::app_logging::record(crate::app_logging::DesktopLogEvent::ProfileRemovalStarted);
        let result = self.remove_profile_steps(profile_id);
        crate::app_logging::record(match result {
            Ok(()) => crate::app_logging::DesktopLogEvent::ProfileRemovalCompleted,
            Err(_) => crate::app_logging::DesktopLogEvent::ProfileRemovalRejected,
        });
        result
    }

    fn remove_profile_steps(&self, profile_id: &str) -> Result<(), BrowserProfileError> {
        self.revalidate_layout()?;
        require_safe_name(profile_id)?;
        let removal_id = format!(".removing-{profile_id}");
        require_safe_relative_name(&removal_id)?;
        let original =
            open_optional_directory_with_acl(&self.platform, profile_id, AclPolicy::Ignore)?;
        let staged =
            open_optional_directory_with_acl(&self.platform, &removal_id, AclPolicy::Ignore)?;
        if original.is_some() && staged.is_some() {
            return Err(BrowserProfileError::recovery_required());
        }
        if let Some(directory) = original {
            verify_private_acl(&directory.file)?;
            let profile = self.profile(profile_id, directory)?;
            let lock = profile.try_acquire_removal_lock()?;
            let staged_path = self.platform.path.join(&removal_id);
            fs::rename(&profile.directory.path, &staged_path)
                .map_err(|_| BrowserProfileError::storage_unavailable())?;
            crate::app_logging::record(crate::app_logging::DesktopLogEvent::ProfileRemovalStaged);
            let reopened = open_relative_directory(
                &self.platform,
                &removal_id,
                FILE_OPEN,
                AclPolicy::Require,
            )?;
            if reopened.identity != profile.directory.identity {
                return Err(BrowserProfileError::identity_changed());
            }
            lock.release()?;
            drop(profile);
            drop(reopened);
        } else if let Some(directory) = staged {
            verify_private_acl(&directory.file)?;
            let profile = self.profile(profile_id, directory)?;
            let lock = profile.try_acquire_removal_lock()?;
            lock.release()?;
            drop(profile);
        } else {
            return self.remove_profile_lease(profile_id);
        }
        self.revalidate_layout()?;
        fs::remove_dir_all(self.platform.path.join(&removal_id))
            .map_err(|_| BrowserProfileError::storage_unavailable())?;
        self.remove_profile_lease(profile_id)?;
        crate::app_logging::record(crate::app_logging::DesktopLogEvent::ProfileRemovalDeleted);
        if open_optional_directory(&self.platform, profile_id)?.is_some()
            || open_optional_directory(&self.platform, &removal_id)?.is_some()
        {
            return Err(BrowserProfileError::identity_changed());
        }
        self.revalidate_layout()
    }

    fn remove_profile_lease(&self, profile_id: &str) -> Result<(), BrowserProfileError> {
        match fs::remove_file(self.platform.path.join(profile_lease_name(profile_id)?)) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
            Err(_) => Err(BrowserProfileError::storage_unavailable()),
        }
    }

    fn profile(
        &self,
        profile_id: &str,
        directory: DirectoryHandle,
    ) -> Result<PlatformProfile, BrowserProfileError> {
        Ok(PlatformProfile {
            directory,
            lease_directory: DirectoryHandle {
                file: self
                    .platform
                    .file
                    .try_clone()
                    .map_err(|_| BrowserProfileError::storage_unavailable())?,
                identity: self.platform.identity,
                path: self.platform.path.clone(),
            },
            lease_name: profile_lease_name(profile_id)?,
        })
    }

    pub(super) fn revalidate_layout(&self) -> Result<(), BrowserProfileError> {
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

fn profile_lease_name(profile_id: &str) -> Result<String, BrowserProfileError> {
    require_safe_name(profile_id)?;
    let name = format!("{PROFILE_LEASE_FILE_PREFIX}{profile_id}");
    require_safe_relative_name(&name)?;
    Ok(name)
}

fn open_absolute_directory(
    path: &Path,
    acl_policy: AclPolicy,
) -> Result<DirectoryHandle, BrowserProfileError> {
    ensure_no_reparse_components(path)?;
    let file = OpenOptions::new()
        .access_mode(FILE_GENERIC_READ | READ_CONTROL | WRITE_DAC | acl_policy.extra_access())
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
    require_safe_relative_name(name)?;
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
    let private_security = match acl_policy {
        AclPolicy::Repair => Some(PrivateSecurityDescriptor::new(DIRECTORY_ACE_FLAGS)?),
        AclPolicy::Ignore | AclPolicy::Require => None,
    };
    let security_descriptor = private_security
        .as_ref()
        .map_or(null(), PrivateSecurityDescriptor::as_ptr);
    let attributes = OBJECT_ATTRIBUTES {
        Length: size_of::<OBJECT_ATTRIBUTES>() as u32,
        RootDirectory: parent.file.as_raw_handle() as HANDLE,
        ObjectName: &object_name,
        Attributes: OBJ_CASE_INSENSITIVE,
        SecurityDescriptor: security_descriptor,
        SecurityQualityOfService: null(),
    };
    let mut io_status = IO_STATUS_BLOCK::default();
    let mut handle = INVALID_HANDLE_VALUE;
    let status = unsafe {
        NtCreateFile(
            &mut handle,
            FILE_GENERIC_READ | READ_CONTROL | WRITE_DAC | acl_policy.extra_access(),
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

fn open_optional_directory(
    parent: &DirectoryHandle,
    name: &str,
) -> Result<Option<DirectoryHandle>, BrowserProfileError> {
    open_optional_directory_with_acl(parent, name, AclPolicy::Require)
}

fn open_optional_directory_with_acl(
    parent: &DirectoryHandle,
    name: &str,
    acl_policy: AclPolicy,
) -> Result<Option<DirectoryHandle>, BrowserProfileError> {
    match open_relative_directory(parent, name, FILE_OPEN, acl_policy) {
        Ok(directory) => Ok(Some(directory)),
        Err(error) if error.code() == super::BrowserProfileErrorCode::ProfileNotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn open_relative_lock_file(
    parent: &DirectoryHandle,
    name: &str,
    disposition: u32,
    acl_policy: AclPolicy,
    share_delete: bool,
) -> Result<File, BrowserProfileError> {
    require_safe_relative_name(name)?;
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
    let private_security = match acl_policy {
        AclPolicy::Repair => Some(PrivateSecurityDescriptor::new(FILE_ACE_FLAGS)?),
        AclPolicy::Ignore | AclPolicy::Require => None,
    };
    let security_descriptor = private_security
        .as_ref()
        .map_or(null(), PrivateSecurityDescriptor::as_ptr);
    let attributes = OBJECT_ATTRIBUTES {
        Length: size_of::<OBJECT_ATTRIBUTES>() as u32,
        RootDirectory: parent.file.as_raw_handle() as HANDLE,
        ObjectName: &object_name,
        Attributes: OBJ_CASE_INSENSITIVE,
        SecurityDescriptor: security_descriptor,
        SecurityQualityOfService: null(),
    };
    let mut io_status = IO_STATUS_BLOCK::default();
    let mut handle = INVALID_HANDLE_VALUE;
    let share_access = if share_delete {
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
    } else {
        FILE_SHARE_READ | FILE_SHARE_WRITE
    };
    let status = unsafe {
        NtCreateFile(
            &mut handle,
            FILE_GENERIC_READ
                | FILE_GENERIC_WRITE
                | READ_CONTROL
                | WRITE_DAC
                | acl_policy.extra_access(),
            &attributes,
            &mut io_status,
            null(),
            FILE_ATTRIBUTE_NORMAL,
            share_access,
            disposition,
            FILE_NON_DIRECTORY_FILE
                | FILE_OPEN_REPARSE_POINT
                | FILE_OPEN_FOR_BACKUP_INTENT
                | FILE_SYNCHRONOUS_IO_NONALERT,
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
            STATUS_REPARSE_POINT_ENCOUNTERED | STATUS_FILE_IS_A_DIRECTORY => {
                BrowserProfileError::unsafe_directory()
            }
            _ => BrowserProfileError::storage_unavailable(),
        });
    }
    let file = unsafe { File::from_raw_handle(handle as _) };
    let identity = lock_file_identity(&file)?;
    if final_path(&file)? != normalized_path_key(&parent.path.join(name)) {
        return Err(BrowserProfileError::unsafe_directory());
    }
    enforce_file_acl_policy(&file, acl_policy)?;
    if directory_identity(&parent.file)? != parent.identity
        || lock_file_identity(&file)? != identity
    {
        return Err(BrowserProfileError::identity_changed());
    }
    Ok(file)
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

fn enforce_file_acl_policy(file: &File, policy: AclPolicy) -> Result<(), BrowserProfileError> {
    match policy {
        AclPolicy::Ignore => Ok(()),
        AclPolicy::Repair => {
            apply_private_file_acl(file)?;
            verify_private_file_acl(file)
        }
        AclPolicy::Require => verify_private_file_acl(file),
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

fn lock_file_identity(file: &File) -> Result<FileIdentity, BrowserProfileError> {
    let mut information: BY_HANDLE_FILE_INFORMATION = unsafe { zeroed() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle() as HANDLE, &mut information) } == 0
    {
        return Err(BrowserProfileError::storage_unavailable());
    }
    if information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY != 0
        || information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || information.nNumberOfLinks != 1
    {
        return Err(BrowserProfileError::unsafe_directory());
    }
    Ok(FileIdentity {
        volume: u64::from(information.dwVolumeSerialNumber),
        index: (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow),
    })
}

fn apply_private_acl(file: &File) -> Result<(), BrowserProfileError> {
    apply_private_acl_with_flags(file, DIRECTORY_ACE_FLAGS)
}

fn apply_private_file_acl(file: &File) -> Result<(), BrowserProfileError> {
    apply_private_acl_with_flags(file, FILE_ACE_FLAGS)
}

fn apply_private_acl_with_flags(file: &File, ace_flags: u32) -> Result<(), BrowserProfileError> {
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
        || unsafe { AddAccessAllowedAceEx(acl, ACL_REVISION, ace_flags, FILE_ALL_ACCESS, sid) } == 0
    {
        return Err(BrowserProfileError::storage_unavailable());
    }
    // The owner is written, not just the DACL. An owner implicitly holds
    // `READ_CONTROL` and `WRITE_DAC` no matter what the DACL says, so leaving
    // the directory owned by `BUILTIN\Administrators` -- which is what an
    // elevated process's default owner produces -- would let every other
    // administrator on the machine rewrite this DACL and read the platform
    // session it protects. It is also exactly what verification checks, so
    // repairing without it produced a directory that could never verify.
    let status = unsafe {
        SetSecurityInfo(
            file.as_raw_handle() as HANDLE,
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION
                | DACL_SECURITY_INFORMATION
                | PROTECTED_DACL_SECURITY_INFORMATION,
            sid,
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
    verify_private_acl_with_flags(file, DIRECTORY_ACE_FLAGS as u8)
}

fn verify_private_file_acl(file: &File) -> Result<(), BrowserProfileError> {
    verify_private_acl_with_flags(file, FILE_ACE_FLAGS as u8)
}

fn verify_private_acl_with_flags(
    file: &File,
    expected_ace_flags: u8,
) -> Result<(), BrowserProfileError> {
    let user = CurrentUserSid::load()?;
    let mut owner: PSID = null_mut();
    let mut dacl: *mut ACL = null_mut();
    let mut descriptor = null_mut();
    let status = unsafe {
        GetSecurityInfo(
            file.as_raw_handle() as HANDLE,
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
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
    let result = verify_private_acl_parts(descriptor, owner, dacl, user.sid(), expected_ace_flags);
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
    expected_ace_flags: u8,
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
        || allowed.Header.AceFlags != expected_ace_flags
        || allowed.Mask != FILE_ALL_ACCESS
        || unsafe { EqualSid(ace_sid, user) } == 0
    {
        return Err(BrowserProfileError::unsafe_directory());
    }
    Ok(())
}

fn read_lock_state(file: &mut File) -> Result<Vec<u8>, BrowserProfileError> {
    file.seek(SeekFrom::Start(0))
        .map_err(|_| BrowserProfileError::storage_unavailable())?;
    let mut state = Vec::with_capacity(MAX_LOCK_STATE_BYTES + 1);
    file.take((MAX_LOCK_STATE_BYTES + 1) as u64)
        .read_to_end(&mut state)
        .map_err(|_| BrowserProfileError::storage_unavailable())?;
    if state.len() > MAX_LOCK_STATE_BYTES {
        return Err(BrowserProfileError::recovery_required());
    }
    Ok(state)
}

fn write_lock_state(file: &mut File, state: &[u8]) -> Result<(), BrowserProfileError> {
    file.set_len(0)
        .and_then(|()| file.seek(SeekFrom::Start(0)).map(|_| ()))
        .and_then(|()| file.write_all(state))
        .and_then(|()| file.sync_all())
        .map_err(|_| BrowserProfileError::storage_unavailable())
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
    let mut value = path.to_string_lossy().replace('/', "\\").to_lowercase();
    if let Some(stripped) = value.strip_prefix("\\\\?\\") {
        // `\\?\UNC\server\share` is the extended spelling of `\\server\share`,
        // and `GetFinalPathNameByHandleW` only ever returns the extended one.
        // Trimming just the `\\?\` left `unc\server\share`, which equals no
        // path anyone can request, so a UNC-backed profile root could never
        // match its own resolved path and always read as an unsafe directory.
        value = match stripped.strip_prefix("unc\\") {
            Some(share) => format!("\\\\{share}"),
            None => stripped.to_owned(),
        };
    }
    while value.ends_with('\\') && value.len() > 3 {
        value.pop();
    }
    value
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

fn require_safe_relative_name(value: &str) -> Result<(), BrowserProfileError> {
    if let Some(profile_id) = value.strip_prefix(".removing-") {
        require_safe_name(profile_id)
    } else if let Some(profile_id) = value.strip_prefix(PROFILE_LEASE_FILE_PREFIX) {
        require_safe_name(profile_id)
    } else {
        require_safe_name(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::browser_profiles::BrowserProfileErrorCode;
    use std::process::Command;
    use std::sync::atomic::{AtomicU32, Ordering};
    use windows_sys::Win32::Security::TokenOwner;

    const PROFILE_ROOT: &str = "browser-profiles";
    const PLATFORM: &str = "douyin";
    const PROFILE_ID: &str = "00000000-0000-4000-8000-000000000001";

    /// A directory named for this project, this process and this case.
    ///
    /// Anything left behind has to be attributable to `automation-tool` and to
    /// one run, so a parallel run or another project's leftovers can never be
    /// mistaken for this one's.
    struct TemporaryDirectory {
        path: PathBuf,
    }

    impl TemporaryDirectory {
        fn create(tag: &str) -> Self {
            static COUNTER: AtomicU32 = AtomicU32::new(0);
            let unique = COUNTER.fetch_add(1, Ordering::Relaxed);
            // Canonicalised for the same reason the integration fixtures do it:
            // `%TEMP%` can be an 8.3 short path or a redirected one, and the
            // guards under test compare a requested path against its resolved
            // form -- an unresolved root fails them for reasons of environment
            // rather than of behaviour.
            let path = std::env::temp_dir()
                .canonicalize()
                .expect("the temporary root must be canonicalisable")
                .join(format!(
                    "automation-tool-browser-profiles-{tag}-{}-{unique}",
                    std::process::id()
                ));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir_all(&path).expect("temporary directory must be creatable");
            Self { path }
        }
    }

    impl Drop for TemporaryDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    /// A `subst` drive letter, unmapped again however the case ends.
    struct SubstDrive {
        letter: String,
    }

    impl SubstDrive {
        fn map(target: &Path) -> Option<Self> {
            let letter = ('P'..='Z')
                .rev()
                .map(|value| format!("{value}:"))
                .find(|candidate| !Path::new(&format!("{candidate}\\")).exists())?;
            let mapped = Command::new("cmd")
                .args(["/c", "subst", &letter, &target.to_string_lossy()])
                .status()
                .ok()?
                .success();
            mapped.then(|| Self { letter })
        }

        fn path(&self, name: &str) -> PathBuf {
            PathBuf::from(format!("{}\\{name}", self.letter))
        }
    }

    impl Drop for SubstDrive {
        fn drop(&mut self) {
            let _ = Command::new("cmd")
                .args(["/c", "subst", &self.letter, "/d"])
                .status();
        }
    }

    fn opened(path: &Path, policy: AclPolicy, purpose: &str) -> DirectoryHandle {
        match open_absolute_directory(path, policy) {
            Ok(directory) => directory,
            Err(error) => panic!("{purpose} (error code {:?})", error.code()),
        }
    }

    fn refusal_code(path: &Path, purpose: &str) -> BrowserProfileErrorCode {
        match open_absolute_directory(path, AclPolicy::Ignore) {
            Ok(_) => panic!("{purpose}"),
            Err(error) => error.code(),
        }
    }

    fn created_profile(store: &PlatformProfileStore) -> PlatformProfile {
        match store.create_profile(PROFILE_ID) {
            Ok(profile) => profile,
            Err(CreateProfileError::Collision) => panic!("a fresh profile id collided"),
            Err(CreateProfileError::Failure(error)) => {
                panic!("profile creation failed (error code {:?})", error.code())
            }
        }
    }

    fn owner_matches_token_user(file: &File) -> bool {
        let user = CurrentUserSid::load().expect("the token user must be readable");
        let mut owner: PSID = null_mut();
        let mut descriptor = null_mut();
        let status = unsafe {
            GetSecurityInfo(
                file.as_raw_handle() as HANDLE,
                SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION,
                &mut owner,
                null_mut(),
                null_mut(),
                null_mut(),
                &mut descriptor,
            )
        };
        assert_eq!(status, ERROR_SUCCESS, "the owner must be readable");
        let matches = !owner.is_null() && unsafe { EqualSid(owner, user.sid()) != 0 };
        unsafe {
            LocalFree(descriptor as HLOCAL);
        }
        matches
    }

    /// Whether this process's *default* owner is something other than its user.
    ///
    /// True under elevation, where new objects are owned by
    /// `BUILTIN\Administrators`. Printed so a run can be read as covering the
    /// elevated case or not, rather than looking identical either way.
    fn default_owner_differs_from_token_user() -> bool {
        let mut token: HANDLE = null_mut();
        assert_ne!(
            unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) },
            0,
            "the process token must be openable"
        );
        let mut required = 0_u32;
        unsafe {
            GetTokenInformation(token, TokenOwner, null_mut(), 0, &mut required);
        }
        let mut buffer = vec![0_usize; (required as usize).div_ceil(size_of::<usize>()).max(1)];
        let read = unsafe {
            GetTokenInformation(
                token,
                TokenOwner,
                buffer.as_mut_ptr().cast(),
                required,
                &mut required,
            )
        };
        unsafe {
            CloseHandle(token);
        }
        assert_ne!(read, 0, "the token owner must be readable");
        let owner = unsafe { *buffer.as_ptr().cast::<PSID>() };
        let user = CurrentUserSid::load().expect("the token user must be readable");
        unsafe { EqualSid(owner, user.sid()) == 0 }
    }

    #[test]
    fn repair_takes_ownership_instead_of_only_rewriting_the_dacl() {
        let root = TemporaryDirectory::create("acl-owner");
        // Printed so an elevated run and a non-elevated one are distinguishable
        // in the output; only the elevated one reproduces the defect.
        println!(
            "default owner differs from token user: {}",
            default_owner_differs_from_token_user()
        );

        let directory = opened(
            &root.path,
            AclPolicy::Repair,
            "repair must be able to correct a directory it did not create",
        );

        assert!(
            owner_matches_token_user(&directory.file),
            "repair left the directory owned by the process default owner"
        );
        assert!(
            verify_private_acl(&directory.file).is_ok(),
            "a repaired directory must verify"
        );
    }

    #[test]
    fn a_store_initializes_under_the_process_default_owner() {
        let root = TemporaryDirectory::create("store-init");

        let store = match PlatformProfileStore::initialize(&root.path, PROFILE_ROOT, PLATFORM) {
            Ok(store) => store,
            Err(error) => panic!(
                "store construction must not depend on who owns the app data directory \
                 (error code {:?})",
                error.code()
            ),
        };
        let profile = created_profile(&store);
        assert!(
            store.revalidate_profile(PROFILE_ID, &profile).is_ok(),
            "a freshly created profile must revalidate"
        );
        drop(profile);
        assert!(
            store.remove_profile(PROFILE_ID).is_ok(),
            "profile removal must succeed"
        );
    }

    #[test]
    fn a_profile_lock_round_trips_under_the_process_default_owner() {
        let root = TemporaryDirectory::create("store-lock");
        let store = match PlatformProfileStore::initialize(&root.path, PROFILE_ROOT, PLATFORM) {
            Ok(store) => store,
            Err(error) => panic!("store construction failed (error code {:?})", error.code()),
        };
        let profile = created_profile(&store);

        let lock = match profile.try_acquire_lock() {
            Ok(lock) => lock,
            Err(error) => panic!(
                "the lock file must be creatable and verifiable (error code {:?})",
                error.code()
            ),
        };
        assert!(lock.release().is_ok(), "the lock must release");
    }

    #[test]
    fn a_unc_path_and_its_final_path_form_share_one_key() {
        // `GetFinalPathNameByHandleW` spells a UNC path `\\?\UNC\server\share`.
        // Stripping only `\\?\` left `unc\server\share`, which matched nothing,
        // so every UNC-backed profile root failed as an unsafe directory.
        assert_eq!(
            normalized_path_key(Path::new(r"\\server\share\browser-profiles")),
            normalized_path_key(Path::new(r"\\?\UNC\server\share\browser-profiles")),
        );
    }

    #[test]
    fn a_drive_path_and_its_extended_form_share_one_key() {
        assert_eq!(
            normalized_path_key(Path::new(r"C:\Users\Test\browser-profiles")),
            normalized_path_key(Path::new(r"\\?\C:\Users\Test\browser-profiles")),
        );
    }

    #[test]
    fn keys_ignore_case_separator_and_trailing_slashes() {
        assert_eq!(
            normalized_path_key(Path::new("C:/Users/Test/")),
            normalized_path_key(Path::new(r"c:\users\test")),
        );
        assert_eq!(normalized_path_key(Path::new(r"C:\")), "c:\\");
    }

    #[test]
    fn an_alias_drive_does_not_share_a_key_with_its_target() {
        // A `subst` letter is a per-session alias any process in that session
        // can re-point. Refusing it is the point of comparing the requested
        // path against the resolved one, so the keys must stay different.
        assert_ne!(
            normalized_path_key(Path::new(r"Y:\browser-profiles")),
            normalized_path_key(Path::new(r"\\?\C:\real\browser-profiles")),
        );
    }

    #[test]
    fn a_subst_alias_drive_is_refused() {
        let root = TemporaryDirectory::create("subst");
        let target = root.path.join("target");
        fs::create_dir(&target).expect("target must be creatable");
        fs::create_dir(target.join(PROFILE_ROOT)).expect("child must be creatable");

        let Some(drive) = SubstDrive::map(&target) else {
            panic!("no drive letter could be mapped, so this case did not run");
        };

        assert_eq!(
            refusal_code(
                &drive.path(PROFILE_ROOT),
                "a path that only resolves through an alias drive must be refused",
            ),
            BrowserProfileErrorCode::UnsafeDirectory,
        );
        opened(
            &target.join(PROFILE_ROOT),
            AclPolicy::Ignore,
            "the same directory reached by its real path must be accepted",
        );
    }

    #[test]
    fn a_junction_component_is_refused() {
        let root = TemporaryDirectory::create("junction");
        let target = root.path.join("target");
        fs::create_dir(&target).expect("target must be creatable");
        fs::create_dir(target.join(PROFILE_ROOT)).expect("child must be creatable");
        let link = root.path.join("link");

        let created = Command::new("cmd")
            .args([
                "/c",
                "mklink",
                "/J",
                &link.to_string_lossy(),
                &target.to_string_lossy(),
            ])
            .status()
            .expect("mklink must be runnable")
            .success();
        assert!(created, "a junction is creatable without elevation");

        // A junction is re-pointable by anyone who can write the parent, so a
        // component that is one is refused rather than followed. Moving AppData
        // and leaving a junction behind is therefore a refused layout -- loudly,
        // which is the intended trade, not an accident of string comparison.
        assert_eq!(
            refusal_code(
                &link.join(PROFILE_ROOT),
                "a path crossing a junction must be refused",
            ),
            BrowserProfileErrorCode::UnsafeDirectory,
        );
        opened(
            &target.join(PROFILE_ROOT),
            AclPolicy::Ignore,
            "the same directory reached without the junction must be accepted",
        );
    }
}

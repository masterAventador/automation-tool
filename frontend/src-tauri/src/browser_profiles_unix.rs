use super::{BrowserProfileError, CreateProfileError};
use std::ffi::{CString, OsStr};
use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read, Seek, SeekFrom, Write};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};

// The lease lives beside, never inside, Chromium's user-data-dir. The frozen
// Playwright runtime rejects a profile containing any file locked by the App.
const PROFILE_LEASE_FILE_PREFIX: &str = ".automation-tool-profile-lease-v1-";
const ACTIVE_LOCK_MARKER: &[u8] = br#"{"state":"active","version":1}"#;
const MAX_LOCK_STATE_BYTES: usize = 64;

#[derive(Clone, Copy, Eq, PartialEq)]
struct DirectoryIdentity {
    device: u64,
    inode: u64,
}

struct DirectoryHandle {
    file: File,
    identity: DirectoryIdentity,
}

#[derive(Clone, Copy)]
enum PermissionPolicy {
    Ignore,
    Repair,
    Require,
}

pub(super) struct PlatformProfileStore {
    app_data_path: PathBuf,
    app_data: DirectoryHandle,
    profile_root: DirectoryHandle,
    platform: DirectoryHandle,
    profile_root_name: CString,
    platform_name: CString,
}

pub(super) struct PlatformProfile {
    directory: DirectoryHandle,
    lease_directory: DirectoryHandle,
    lease_name: CString,
}

pub(super) struct PlatformProfileLock {
    file: File,
    file_identity: FileIdentity,
    profile_directory: File,
    profile_identity: DirectoryIdentity,
    lease_directory: File,
    lease_identity: DirectoryIdentity,
    lease_name: CString,
}

#[derive(Clone, Copy, Eq, PartialEq)]
struct FileIdentity {
    device: u64,
    inode: u64,
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
        require_private_directory(&self.directory.file)?;
        require_private_directory(&self.lease_directory.file)?;
        let profile_directory = self
            .directory
            .file
            .try_clone()
            .map_err(|_| BrowserProfileError::storage_unavailable())?;
        let lease_directory = self
            .lease_directory
            .file
            .try_clone()
            .map_err(|_| BrowserProfileError::storage_unavailable())?;
        let name = self.lease_name.clone();
        let mut file = open_lock_file(&lease_directory, &name, true)?;
        let file_identity = lock_file_identity(&file)?;
        let lock_result = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
        if lock_result != 0 {
            let error = std::io::Error::last_os_error();
            let raw_error = error.raw_os_error();
            return Err(
                if raw_error == Some(libc::EWOULDBLOCK) || raw_error == Some(libc::EAGAIN) {
                    BrowserProfileError::profile_in_use()
                } else {
                    BrowserProfileError::storage_unavailable()
                },
            );
        }
        let reopened = open_lock_file(&lease_directory, &name, false)?;
        if lock_file_identity(&reopened)? != file_identity
            || directory_identity(&profile_directory)? != self.directory.identity
            || directory_identity(&lease_directory)? != self.lease_directory.identity
        {
            return Err(BrowserProfileError::identity_changed());
        }
        let state = read_lock_state(&mut file)?;
        if !(state.is_empty() || allow_abandoned_active_marker && state == ACTIVE_LOCK_MARKER) {
            return Err(BrowserProfileError::recovery_required());
        }
        write_lock_state(&mut file, ACTIVE_LOCK_MARKER)?;
        let reopened = open_lock_file(&lease_directory, &name, false)?;
        if lock_file_identity(&reopened)? != file_identity
            || directory_identity(&profile_directory)? != self.directory.identity
            || directory_identity(&lease_directory)? != self.lease_directory.identity
        {
            return Err(BrowserProfileError::identity_changed());
        }
        sync_directory_handle(&lease_directory)?;
        Ok(PlatformProfileLock {
            file,
            file_identity,
            profile_directory,
            profile_identity: self.directory.identity,
            lease_directory,
            lease_identity: self.lease_directory.identity,
            lease_name: name,
        })
    }
}

impl PlatformProfileLock {
    pub(super) fn release(mut self) -> Result<(), BrowserProfileError> {
        if directory_identity(&self.profile_directory)? != self.profile_identity {
            return Err(BrowserProfileError::identity_changed());
        }
        if directory_identity(&self.lease_directory)? != self.lease_identity {
            return Err(BrowserProfileError::identity_changed());
        }
        let reopened = open_lock_file(&self.lease_directory, &self.lease_name, false)?;
        if lock_file_identity(&self.file)? != self.file_identity
            || lock_file_identity(&reopened)? != self.file_identity
        {
            return Err(BrowserProfileError::identity_changed());
        }
        if read_lock_state(&mut self.file)? != ACTIVE_LOCK_MARKER {
            return Err(BrowserProfileError::recovery_required());
        }
        write_lock_state(&mut self.file, b"")?;
        sync_directory_handle(&self.lease_directory)
    }
}

impl PlatformProfileStore {
    pub(super) fn initialize(
        app_data_path: &Path,
        profile_root_name: &str,
        platform_name: &str,
    ) -> Result<Self, BrowserProfileError> {
        let profile_root_name = safe_name(profile_root_name)?;
        let platform_name = safe_name(platform_name)?;
        let app_data = open_absolute_directory(app_data_path, PermissionPolicy::Repair)?;
        let profile_root = create_or_open_private_child(&app_data, &profile_root_name)?;
        let platform = create_or_open_private_child(&profile_root, &platform_name)?;
        let store = Self {
            app_data_path: app_data_path.to_path_buf(),
            app_data,
            profile_root,
            platform,
            profile_root_name,
            platform_name,
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
        let name = safe_name(profile_id).map_err(CreateProfileError::Failure)?;
        let result = unsafe { libc::mkdirat(self.platform.file.as_raw_fd(), name.as_ptr(), 0o700) };
        if result != 0 {
            let error = std::io::Error::last_os_error();
            if error.kind() == ErrorKind::AlreadyExists {
                return Err(CreateProfileError::Collision);
            }
            return Err(CreateProfileError::Failure(
                BrowserProfileError::storage_unavailable(),
            ));
        }
        let directory = open_child(&self.platform, &name, PermissionPolicy::Repair)
            .map_err(CreateProfileError::Failure)?;
        self.revalidate_layout()
            .map_err(CreateProfileError::Failure)?;
        let reopened = open_child(&self.platform, &name, PermissionPolicy::Require)
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
        let name = safe_name(profile_id)?;
        let directory = open_child(&self.platform, &name, PermissionPolicy::Require)?;
        self.revalidate_layout()?;
        let reopened = open_child(&self.platform, &name, PermissionPolicy::Require)?;
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
        let name = safe_name(profile_id)?;
        let reopened = open_child(&self.platform, &name, PermissionPolicy::Ignore).map_err(
            |error| match error.code() {
                super::BrowserProfileErrorCode::ProfileNotFound => {
                    BrowserProfileError::identity_changed()
                }
                _ => error,
            },
        )?;
        if reopened.identity != profile.directory.identity {
            return Err(BrowserProfileError::identity_changed());
        }
        require_private_directory(&profile.directory.file)?;
        require_private_directory(&reopened.file)?;
        require_private_directory(&profile.lease_directory.file)?;
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
        let profile_name = safe_name(profile_id)?;
        let removal_id = format!(".removing-{profile_id}");
        let removal_name = safe_name(&removal_id)?;
        let original = open_optional_child(&self.platform, &profile_name)?;
        let staged = open_optional_child(&self.platform, &removal_name)?;
        if original.is_some() && staged.is_some() {
            return Err(BrowserProfileError::recovery_required());
        }
        if let Some(directory) = original {
            let profile = self.profile(profile_id, directory)?;
            let lock = profile.try_acquire_removal_lock()?;
            if unsafe {
                libc::renameat(
                    self.platform.file.as_raw_fd(),
                    profile_name.as_ptr(),
                    self.platform.file.as_raw_fd(),
                    removal_name.as_ptr(),
                )
            } != 0
            {
                return Err(BrowserProfileError::storage_unavailable());
            }
            let reopened = open_child(&self.platform, &removal_name, PermissionPolicy::Require)?;
            if reopened.identity != profile.directory.identity {
                return Err(BrowserProfileError::identity_changed());
            }
            sync_directory_handle(&self.platform.file)?;
            crate::app_logging::record(crate::app_logging::DesktopLogEvent::ProfileRemovalStaged);
            lock.release()?;
            drop(reopened);
            drop(profile);
        } else if let Some(directory) = staged {
            let profile = self.profile(profile_id, directory)?;
            let lock = profile.try_acquire_removal_lock()?;
            lock.release()?;
            drop(profile);
        } else {
            return self.remove_profile_lease(profile_id);
        }
        self.revalidate_layout()?;
        let removal_path = self
            .app_data_path
            .join(self.profile_root_name.to_string_lossy().as_ref())
            .join(self.platform_name.to_string_lossy().as_ref())
            .join(&removal_id);
        fs::remove_dir_all(&removal_path)
            .map_err(|_| BrowserProfileError::storage_unavailable())?;
        self.remove_profile_lease(profile_id)?;
        crate::app_logging::record(crate::app_logging::DesktopLogEvent::ProfileRemovalDeleted);
        sync_directory_handle(&self.platform.file)?;
        if open_optional_child(&self.platform, &profile_name)?.is_some()
            || open_optional_child(&self.platform, &removal_name)?.is_some()
        {
            return Err(BrowserProfileError::identity_changed());
        }
        self.revalidate_layout()
    }

    fn remove_profile_lease(&self, profile_id: &str) -> Result<(), BrowserProfileError> {
        let path = self
            .app_data_path
            .join(self.profile_root_name.to_string_lossy().as_ref())
            .join(self.platform_name.to_string_lossy().as_ref())
            .join(profile_lease_name(profile_id)?.to_string_lossy().as_ref());
        match fs::remove_file(path) {
            Ok(()) => {
                sync_directory_handle(&self.platform.file)?;
                Ok(())
            }
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
            },
            lease_name: profile_lease_name(profile_id)?,
        })
    }

    pub(super) fn revalidate_layout(&self) -> Result<(), BrowserProfileError> {
        if directory_identity(&self.app_data.file)? != self.app_data.identity
            || directory_identity(&self.profile_root.file)? != self.profile_root.identity
            || directory_identity(&self.platform.file)? != self.platform.identity
        {
            return Err(BrowserProfileError::identity_changed());
        }
        require_private_directory(&self.app_data.file)?;
        require_private_directory(&self.profile_root.file)?;
        require_private_directory(&self.platform.file)?;
        let app_data = open_absolute_directory(&self.app_data_path, PermissionPolicy::Ignore)?;
        if app_data.identity != self.app_data.identity {
            return Err(BrowserProfileError::identity_changed());
        }
        require_private_directory(&app_data.file)?;
        let profile_root = open_child(
            &self.app_data,
            &self.profile_root_name,
            PermissionPolicy::Ignore,
        )?;
        if profile_root.identity != self.profile_root.identity {
            return Err(BrowserProfileError::identity_changed());
        }
        require_private_directory(&profile_root.file)?;
        let platform = open_child(
            &self.profile_root,
            &self.platform_name,
            PermissionPolicy::Ignore,
        )?;
        if platform.identity != self.platform.identity {
            return Err(BrowserProfileError::identity_changed());
        }
        require_private_directory(&platform.file)?;
        Ok(())
    }
}

fn profile_lease_name(profile_id: &str) -> Result<CString, BrowserProfileError> {
    safe_name(&format!("{PROFILE_LEASE_FILE_PREFIX}{profile_id}"))
}

fn open_absolute_directory(
    path: &Path,
    final_policy: PermissionPolicy,
) -> Result<DirectoryHandle, BrowserProfileError> {
    let components = path
        .components()
        .map(|component| match component {
            Component::Normal(value) => Ok(value.to_owned()),
            Component::RootDir => Err(None),
            _ => Err(Some(BrowserProfileError::unsafe_directory())),
        })
        .filter_map(|result| match result {
            Ok(value) => Some(Ok(value)),
            Err(None) => None,
            Err(Some(error)) => Some(Err(error)),
        })
        .collect::<Result<Vec<_>, _>>()?;
    if components.is_empty() || !path.is_absolute() {
        return Err(BrowserProfileError::unsafe_directory());
    }
    let root = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(Path::new("/"))
        .map_err(|_| BrowserProfileError::storage_unavailable())?;
    let mut current = DirectoryHandle {
        identity: directory_identity(&root)?,
        file: root,
    };
    for (index, component) in components.iter().enumerate() {
        let name = safe_os_name(component)?;
        let policy = if index + 1 == components.len() {
            final_policy
        } else {
            PermissionPolicy::Ignore
        };
        current = open_child(&current, &name, policy)?;
    }
    Ok(current)
}

fn create_or_open_private_child(
    parent: &DirectoryHandle,
    name: &CString,
) -> Result<DirectoryHandle, BrowserProfileError> {
    let result = unsafe { libc::mkdirat(parent.file.as_raw_fd(), name.as_ptr(), 0o700) };
    if result != 0 && std::io::Error::last_os_error().kind() != ErrorKind::AlreadyExists {
        return Err(BrowserProfileError::storage_unavailable());
    }
    open_child(parent, name, PermissionPolicy::Repair)
}

fn open_child(
    parent: &DirectoryHandle,
    name: &CString,
    permission_policy: PermissionPolicy,
) -> Result<DirectoryHandle, BrowserProfileError> {
    let descriptor = unsafe {
        libc::openat(
            parent.file.as_raw_fd(),
            name.as_ptr(),
            libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
        )
    };
    if descriptor < 0 {
        let error = std::io::Error::last_os_error();
        return Err(match error.kind() {
            ErrorKind::NotFound => BrowserProfileError::profile_not_found(),
            _ if matches!(
                error.raw_os_error(),
                Some(libc::ELOOP) | Some(libc::ENOTDIR)
            ) =>
            {
                BrowserProfileError::unsafe_directory()
            }
            _ => BrowserProfileError::storage_unavailable(),
        });
    }
    let file = unsafe { File::from_raw_fd(descriptor) };
    if matches!(permission_policy, PermissionPolicy::Repair)
        && unsafe { libc::fchmod(file.as_raw_fd(), 0o700) } != 0
    {
        return Err(BrowserProfileError::storage_unavailable());
    }
    let metadata = file
        .metadata()
        .map_err(|_| BrowserProfileError::storage_unavailable())?;
    if !metadata.is_dir() {
        return Err(BrowserProfileError::unsafe_directory());
    }
    if !matches!(permission_policy, PermissionPolicy::Ignore)
        && metadata.permissions().mode() & 0o777 != 0o700
    {
        return Err(BrowserProfileError::unsafe_directory());
    }
    Ok(DirectoryHandle {
        identity: DirectoryIdentity {
            device: metadata.dev(),
            inode: metadata.ino(),
        },
        file,
    })
}

fn open_optional_child(
    parent: &DirectoryHandle,
    name: &CString,
) -> Result<Option<DirectoryHandle>, BrowserProfileError> {
    match open_child(parent, name, PermissionPolicy::Require) {
        Ok(directory) => Ok(Some(directory)),
        Err(error) if error.code() == super::BrowserProfileErrorCode::ProfileNotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn require_private_directory(file: &File) -> Result<(), BrowserProfileError> {
    let metadata = file
        .metadata()
        .map_err(|_| BrowserProfileError::storage_unavailable())?;
    if !metadata.is_dir() || metadata.permissions().mode() & 0o777 != 0o700 {
        return Err(BrowserProfileError::unsafe_directory());
    }
    Ok(())
}

fn open_lock_file(
    profile_directory: &File,
    name: &CString,
    create: bool,
) -> Result<File, BrowserProfileError> {
    let mut flags = libc::O_RDWR | libc::O_NOFOLLOW | libc::O_CLOEXEC;
    if create {
        flags |= libc::O_CREAT;
    }
    let descriptor =
        unsafe { libc::openat(profile_directory.as_raw_fd(), name.as_ptr(), flags, 0o600) };
    if descriptor < 0 {
        let error = std::io::Error::last_os_error();
        return Err(match error.kind() {
            ErrorKind::NotFound => BrowserProfileError::identity_changed(),
            _ if matches!(error.raw_os_error(), Some(libc::ELOOP) | Some(libc::EISDIR)) => {
                BrowserProfileError::unsafe_directory()
            }
            _ => BrowserProfileError::storage_unavailable(),
        });
    }
    let file = unsafe { File::from_raw_fd(descriptor) };
    lock_file_identity(&file)?;
    Ok(file)
}

fn lock_file_identity(file: &File) -> Result<FileIdentity, BrowserProfileError> {
    let metadata = file
        .metadata()
        .map_err(|_| BrowserProfileError::storage_unavailable())?;
    if !metadata.is_file()
        || metadata.nlink() != 1
        || metadata.uid() != unsafe { libc::geteuid() }
        || metadata.permissions().mode() & 0o777 != 0o600
    {
        return Err(BrowserProfileError::unsafe_directory());
    }
    Ok(FileIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
    })
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

fn sync_directory_handle(directory: &File) -> Result<(), BrowserProfileError> {
    directory
        .sync_all()
        .map_err(|_| BrowserProfileError::storage_unavailable())
}

fn directory_identity(file: &File) -> Result<DirectoryIdentity, BrowserProfileError> {
    let metadata = file
        .metadata()
        .map_err(|_| BrowserProfileError::storage_unavailable())?;
    if !metadata.is_dir() {
        return Err(BrowserProfileError::unsafe_directory());
    }
    Ok(DirectoryIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
    })
}

fn safe_name(value: &str) -> Result<CString, BrowserProfileError> {
    safe_os_name(OsStr::new(value))
}

fn safe_os_name(value: &OsStr) -> Result<CString, BrowserProfileError> {
    let bytes = value.as_bytes();
    if bytes.is_empty() || bytes == b"." || bytes == b".." || bytes.contains(&b'/') {
        return Err(BrowserProfileError::unsafe_directory());
    }
    CString::new(bytes).map_err(|_| BrowserProfileError::unsafe_directory())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(0);

    struct TemporaryDirectory(PathBuf);

    impl TemporaryDirectory {
        fn new() -> Self {
            let root = std::env::temp_dir()
                .canonicalize()
                .expect("canonical temporary directory");
            let path = root.join(format!(
                "automation-tool-b5-05-unix-{}-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .expect("system time")
                    .as_nanos(),
                NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).expect("temporary directory");
            Self(path)
        }
    }

    impl Drop for TemporaryDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn absolute_traversal_rejects_a_symlinked_ancestor() {
        use std::os::unix::fs::symlink;

        let temporary = TemporaryDirectory::new();
        let actual = temporary.0.join("actual");
        let linked = temporary.0.join("linked");
        fs::create_dir(&actual).expect("actual directory");
        symlink(&actual, &linked).expect("linked directory");

        assert_eq!(
            open_absolute_directory(&linked, PermissionPolicy::Repair)
                .err()
                .expect("ancestor symlink must fail")
                .code(),
            super::super::BrowserProfileErrorCode::UnsafeDirectory
        );
    }
}

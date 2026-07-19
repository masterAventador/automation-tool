use super::{BrowserProfileError, CreateProfileError};
use std::ffi::{CString, OsStr};
use std::fs::{File, OpenOptions};
use std::io::ErrorKind;
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};

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
        Ok(PlatformProfile { directory })
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
        self.revalidate_layout()
    }

    fn revalidate_layout(&self) -> Result<(), BrowserProfileError> {
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

fn require_private_directory(file: &File) -> Result<(), BrowserProfileError> {
    let metadata = file
        .metadata()
        .map_err(|_| BrowserProfileError::storage_unavailable())?;
    if !metadata.is_dir() || metadata.permissions().mode() & 0o777 != 0o700 {
        return Err(BrowserProfileError::unsafe_directory());
    }
    Ok(())
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

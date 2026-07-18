use std::error::Error;
use std::fmt::{Display, Formatter};
use std::fs::{self, OpenOptions};
use std::io::{ErrorKind, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use zeroize::Zeroizing;

const MAX_STORED_SECRET_LENGTH: usize = 4096;
static TEMP_FILE_SEQUENCE: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SecureStoreError {
    Unavailable,
}

impl Display for SecureStoreError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("secure store unavailable")
    }
}

impl Error for SecureStoreError {}

pub trait SecretStore {
    fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, SecureStoreError>;
    fn save(&self, secret: &[u8]) -> Result<(), SecureStoreError>;
    fn delete(&self) -> Result<(), SecureStoreError>;
}

pub(crate) struct AppDataSecretStore {
    directory: PathBuf,
    path: PathBuf,
    file_name: String,
}

impl AppDataSecretStore {
    pub(crate) fn new(directory: &Path, file_name: &str) -> Result<Self, SecureStoreError> {
        if !is_safe_file_name(file_name) {
            return Err(SecureStoreError::Unavailable);
        }
        ensure_private_directory(directory)?;
        Ok(Self {
            directory: directory.to_path_buf(),
            path: directory.join(file_name),
            file_name: file_name.to_owned(),
        })
    }
}

impl SecretStore for AppDataSecretStore {
    fn load(&self) -> Result<Option<Zeroizing<Vec<u8>>>, SecureStoreError> {
        ensure_private_directory(&self.directory)?;
        let Some(metadata) = safe_secret_metadata(&self.path)? else {
            return Ok(None);
        };
        if metadata.len() > MAX_STORED_SECRET_LENGTH as u64 {
            return Err(SecureStoreError::Unavailable);
        }
        ensure_private_file_permissions(&metadata)?;
        let file = OpenOptions::new()
            .read(true)
            .open(&self.path)
            .map_err(|_| SecureStoreError::Unavailable)?;
        let mut secret = Vec::with_capacity(metadata.len() as usize);
        file.take((MAX_STORED_SECRET_LENGTH + 1) as u64)
            .read_to_end(&mut secret)
            .map_err(|_| SecureStoreError::Unavailable)?;
        if secret.len() > MAX_STORED_SECRET_LENGTH {
            return Err(SecureStoreError::Unavailable);
        }
        Ok(Some(Zeroizing::new(secret)))
    }

    fn save(&self, secret: &[u8]) -> Result<(), SecureStoreError> {
        if secret.is_empty() || secret.len() > MAX_STORED_SECRET_LENGTH {
            return Err(SecureStoreError::Unavailable);
        }
        ensure_private_directory(&self.directory)?;
        if let Some(metadata) = safe_secret_metadata(&self.path)? {
            ensure_private_file_permissions(&metadata)?;
        }
        let sequence = TEMP_FILE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let temporary_path = self.directory.join(format!(
            ".{}.{}.{}.tmp",
            self.file_name,
            std::process::id(),
            sequence
        ));
        let result = write_and_replace(&temporary_path, &self.path, secret);
        if result.is_err() {
            let _ = fs::remove_file(&temporary_path);
        }
        result?;
        sync_directory(&self.directory)
    }

    fn delete(&self) -> Result<(), SecureStoreError> {
        ensure_private_directory(&self.directory)?;
        let Some(metadata) = safe_secret_metadata(&self.path)? else {
            return Ok(());
        };
        ensure_private_file_permissions(&metadata)?;
        match fs::remove_file(&self.path) {
            Ok(()) => sync_directory(&self.directory),
            Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
            Err(_) => Err(SecureStoreError::Unavailable),
        }
    }
}

fn is_safe_file_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value != "."
        && value != ".."
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

fn ensure_private_directory(path: &Path) -> Result<(), SecureStoreError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() || !metadata.is_dir() {
                return Err(SecureStoreError::Unavailable);
            }
        }
        Err(error) if error.kind() == ErrorKind::NotFound => create_private_directory(path)?,
        Err(_) => return Err(SecureStoreError::Unavailable),
    }
    set_private_directory_permissions(path)
}

#[cfg(unix)]
fn create_private_directory(path: &Path) -> Result<(), SecureStoreError> {
    use std::os::unix::fs::DirBuilderExt;

    let mut builder = fs::DirBuilder::new();
    builder.recursive(true).mode(0o700);
    builder
        .create(path)
        .map_err(|_| SecureStoreError::Unavailable)
}

#[cfg(not(unix))]
fn create_private_directory(path: &Path) -> Result<(), SecureStoreError> {
    fs::create_dir_all(path).map_err(|_| SecureStoreError::Unavailable)
}

#[cfg(unix)]
fn set_private_directory_permissions(path: &Path) -> Result<(), SecureStoreError> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .map_err(|_| SecureStoreError::Unavailable)
}

#[cfg(not(unix))]
fn set_private_directory_permissions(_path: &Path) -> Result<(), SecureStoreError> {
    Ok(())
}

fn safe_secret_metadata(path: &Path) -> Result<Option<fs::Metadata>, SecureStoreError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() || !metadata.is_file() {
                return Err(SecureStoreError::Unavailable);
            }
            Ok(Some(metadata))
        }
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(_) => Err(SecureStoreError::Unavailable),
    }
}

#[cfg(unix)]
fn ensure_private_file_permissions(metadata: &fs::Metadata) -> Result<(), SecureStoreError> {
    use std::os::unix::fs::PermissionsExt;

    if metadata.permissions().mode() & 0o077 == 0 {
        Ok(())
    } else {
        Err(SecureStoreError::Unavailable)
    }
}

#[cfg(not(unix))]
fn ensure_private_file_permissions(_metadata: &fs::Metadata) -> Result<(), SecureStoreError> {
    Ok(())
}

fn write_and_replace(
    temporary_path: &Path,
    destination: &Path,
    secret: &[u8],
) -> Result<(), SecureStoreError> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut temporary = options
        .open(temporary_path)
        .map_err(|_| SecureStoreError::Unavailable)?;
    temporary
        .write_all(secret)
        .and_then(|()| temporary.sync_all())
        .map_err(|_| SecureStoreError::Unavailable)?;
    drop(temporary);
    fs::rename(temporary_path, destination).map_err(|_| SecureStoreError::Unavailable)
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<(), SecureStoreError> {
    fs::File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(|_| SecureStoreError::Unavailable)
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<(), SecureStoreError> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    use super::{AppDataSecretStore, SecretStore, SecureStoreError};

    struct TemporaryAppData {
        path: std::path::PathBuf,
    }

    impl TemporaryAppData {
        fn new() -> Self {
            let unique = format!(
                "automation-tool-i2-08-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .expect("system time")
                    .as_nanos()
            );
            Self {
                path: std::env::temp_dir().join(unique),
            }
        }
    }

    impl Drop for TemporaryAppData {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    #[test]
    fn app_data_store_round_trips_replaces_and_deletes_without_temp_files() {
        let app_data = TemporaryAppData::new();
        let store = AppDataSecretStore::new(&app_data.path, "device-credential-v1")
            .expect("create app data store");

        assert!(store.load().expect("initial load").is_none());
        store.save(b"first-private-value").expect("save first");
        store.save(b"second-private-value").expect("replace");

        let loaded = store
            .load()
            .expect("load replacement")
            .expect("stored replacement");
        assert_eq!(loaded.as_slice(), b"second-private-value");
        let entries = fs::read_dir(&app_data.path)
            .expect("list private directory")
            .collect::<Result<Vec<_>, _>>()
            .expect("directory entries");
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].file_name(), "device-credential-v1");

        store.delete().expect("delete");
        store.delete().expect("idempotent delete");
        assert!(store.load().expect("deleted load").is_none());
    }

    #[test]
    fn unsafe_file_names_and_oversized_secrets_fail_closed() {
        let app_data = TemporaryAppData::new();
        for invalid_name in ["", ".", "..", "../escape", "nested/secret"] {
            assert_eq!(
                AppDataSecretStore::new(&app_data.path, invalid_name).err(),
                Some(SecureStoreError::Unavailable)
            );
        }
        let store =
            AppDataSecretStore::new(&app_data.path, "device-credential-v1").expect("create store");
        assert_eq!(store.save(b""), Err(SecureStoreError::Unavailable));
        assert_eq!(
            store.save(&vec![b'x'; 4097]),
            Err(SecureStoreError::Unavailable)
        );
        assert!(store.load().expect("unchanged empty store").is_none());

        fs::write(app_data.path.join("device-credential-v1"), vec![b'x'; 4097])
            .expect("write oversized stored secret");
        assert_eq!(store.load().err(), Some(SecureStoreError::Unavailable));
    }

    #[cfg(unix)]
    #[test]
    fn app_data_directory_and_secret_file_use_private_permissions() {
        use std::os::unix::fs::PermissionsExt;

        let app_data = TemporaryAppData::new();
        let store =
            AppDataSecretStore::new(&app_data.path, "device-credential-v1").expect("create store");
        store.save(b"private-value").expect("save");

        let directory_mode = fs::metadata(&app_data.path)
            .expect("directory metadata")
            .permissions()
            .mode();
        let file_mode = fs::metadata(app_data.path.join("device-credential-v1"))
            .expect("file metadata")
            .permissions()
            .mode();
        assert_eq!(directory_mode & 0o077, 0);
        assert_eq!(file_mode & 0o077, 0);
    }

    #[cfg(unix)]
    #[test]
    fn symlink_or_over_permissive_secret_files_are_rejected() {
        use std::os::unix::fs::{symlink, PermissionsExt};

        let app_data = TemporaryAppData::new();
        fs::create_dir_all(&app_data.path).expect("create app data");
        let outside = app_data.path.with_extension("outside");
        fs::write(&outside, b"outside-private-value").expect("write outside");
        symlink(&outside, app_data.path.join("device-credential-v1")).expect("create symlink");
        let store = AppDataSecretStore::new(&app_data.path, "device-credential-v1")
            .expect("create store adapter");
        assert_eq!(store.load().err(), Some(SecureStoreError::Unavailable));
        fs::remove_file(app_data.path.join("device-credential-v1")).expect("remove symlink");
        fs::write(app_data.path.join("device-credential-v1"), b"private").expect("write file");
        fs::set_permissions(
            app_data.path.join("device-credential-v1"),
            fs::Permissions::from_mode(0o644),
        )
        .expect("set unsafe permissions");
        assert_eq!(store.load().err(), Some(SecureStoreError::Unavailable));
        fs::remove_file(outside).expect("remove outside file");
    }

    #[cfg(unix)]
    #[test]
    fn symlink_or_non_directory_app_data_roots_are_rejected() {
        use std::os::unix::fs::symlink;

        let symlink_root = TemporaryAppData::new();
        let outside_directory = symlink_root.path.with_extension("outside-directory");
        fs::create_dir_all(&outside_directory).expect("create outside directory");
        symlink(&outside_directory, &symlink_root.path).expect("create app data symlink");
        assert_eq!(
            AppDataSecretStore::new(&symlink_root.path, "device-credential-v1").err(),
            Some(SecureStoreError::Unavailable)
        );
        fs::remove_file(&symlink_root.path).expect("remove app data symlink");
        fs::remove_dir_all(outside_directory).expect("remove outside directory");

        let file_root = TemporaryAppData::new();
        fs::write(&file_root.path, b"not-a-directory").expect("create non-directory root");
        assert_eq!(
            AppDataSecretStore::new(&file_root.path, "device-credential-v1").err(),
            Some(SecureStoreError::Unavailable)
        );
        fs::remove_file(&file_root.path).expect("remove non-directory root");
    }
}

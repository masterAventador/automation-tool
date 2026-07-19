use automation_tool_desktop_lib::browser_profiles::{BrowserProfileErrorCode, BrowserProfileStore};
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

const LOCK_FILE_NAME: &str = ".automation-tool-profile-lock-v1";
const ACTIVE_MARKER: &[u8] = br#"{"state":"active","version":1}"#;
const HELPER_MODE_ENV: &str = "AUTOMATION_TOOL_B506_LOCK_HELPER";
const HELPER_APP_DATA_ENV: &str = "AUTOMATION_TOOL_B506_APP_DATA";
const HELPER_PROFILE_ID_ENV: &str = "AUTOMATION_TOOL_B506_PROFILE_ID";

static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(0);

struct TemporaryAppData {
    path: std::path::PathBuf,
}

impl TemporaryAppData {
    fn new() -> Self {
        let path = std::env::temp_dir()
            .canonicalize()
            .expect("canonical temporary root")
            .join(format!(
                "automation-tool-b5-06-integration-{}-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .expect("system time")
                    .as_nanos(),
                NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed)
            ));
        fs::create_dir(&path).expect("create AppData fixture");
        Self { path }
    }
}

impl Drop for TemporaryAppData {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

#[test]
fn same_profile_is_exclusive_and_explicit_release_allows_reacquire() {
    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let profile = store.create_douyin_profile().expect("profile");
    let same_profile = store
        .open_douyin_profile(profile.profile_id())
        .expect("same profile");

    let lock = profile.try_acquire_lock().expect("first lock");
    assert_eq!(
        same_profile
            .try_acquire_lock()
            .expect_err("second lock must fail")
            .code(),
        BrowserProfileErrorCode::ProfileInUse
    );
    lock.release().expect("explicit release");

    same_profile
        .try_acquire_lock()
        .expect("lock after release")
        .release()
        .expect("second explicit release");
}

#[test]
fn owned_profile_lease_keeps_the_profile_exclusive_until_explicit_release() {
    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let profile = store.current_douyin_profile().expect("current profile");
    let profile_id = profile.profile_id().to_owned();
    let competing = store
        .open_douyin_profile(&profile_id)
        .expect("competing profile handle");

    let lease = profile
        .try_acquire_owned_lock()
        .expect("owned Profile lease");
    assert_eq!(lease.profile_id(), profile_id);
    assert_eq!(
        competing
            .try_acquire_lock()
            .expect_err("owned lease must remain exclusive")
            .code(),
        BrowserProfileErrorCode::ProfileInUse,
    );
    lease.release().expect("release owned lease");
    competing
        .try_acquire_lock()
        .expect("reacquire after owned release")
        .release()
        .expect("release competing lock");
}

#[test]
fn different_profiles_may_be_locked_at_the_same_time() {
    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let first = store.create_douyin_profile().expect("first profile");
    let second = store.create_douyin_profile().expect("second profile");

    let first_lock = first.try_acquire_lock().expect("first lock");
    let second_lock = second.try_acquire_lock().expect("second lock");
    first_lock.release().expect("release first");
    second_lock.release().expect("release second");
}

#[test]
fn cross_process_contention_uses_the_production_profile_lock() {
    if std::env::var_os(HELPER_MODE_ENV).is_some() {
        run_lock_holder_helper();
        return;
    }

    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let profile = store.create_douyin_profile().expect("profile");
    let mut child = Command::new(std::env::current_exe().expect("test executable"))
        .args([
            "--exact",
            "cross_process_contention_uses_the_production_profile_lock",
            "--nocapture",
        ])
        .env(HELPER_MODE_ENV, "hold")
        .env(HELPER_APP_DATA_ENV, &app_data.path)
        .env(HELPER_PROFILE_ID_ENV, profile.profile_id())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .expect("spawn lock holder");
    let mut output = BufReader::new(child.stdout.take().expect("helper stdout"));
    let mut line = String::new();
    loop {
        line.clear();
        assert_ne!(output.read_line(&mut line).expect("helper output"), 0);
        if line.contains("B5_06_LOCKED") {
            break;
        }
    }

    assert_eq!(
        profile
            .try_acquire_lock()
            .expect_err("cross-process contention must fail")
            .code(),
        BrowserProfileErrorCode::ProfileInUse
    );
    child
        .stdin
        .take()
        .expect("helper stdin")
        .write_all(b"release")
        .expect("release helper");
    assert!(child.wait().expect("helper status").success());

    profile
        .try_acquire_lock()
        .expect("lock after helper release")
        .release()
        .expect("release parent lock");
}

fn run_lock_holder_helper() {
    let app_data =
        std::path::PathBuf::from(std::env::var_os(HELPER_APP_DATA_ENV).expect("helper AppData"));
    let profile_id = std::env::var(HELPER_PROFILE_ID_ENV).expect("helper profile ID");
    let store = BrowserProfileStore::initialize(&app_data).expect("helper store");
    let profile = store
        .open_douyin_profile(&profile_id)
        .expect("helper profile");
    let lock = profile.try_acquire_lock().expect("helper lock");
    println!("B5_06_LOCKED");
    std::io::stdout().flush().expect("flush helper ready");
    let mut release = [0_u8; 7];
    std::io::stdin()
        .read_exact(&mut release)
        .expect("helper release signal");
    assert_eq!(&release, b"release");
    lock.release().expect("helper explicit release");
}

#[test]
fn killed_lock_holder_preserves_marker_and_requires_explicit_recovery() {
    if std::env::var_os(HELPER_MODE_ENV).is_some() {
        run_lock_holder_helper();
        return;
    }

    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let profile = store.create_douyin_profile().expect("profile");
    let lock_path = profile.directory().join(LOCK_FILE_NAME);
    let mut child = Command::new(std::env::current_exe().expect("test executable"))
        .args([
            "--exact",
            "killed_lock_holder_preserves_marker_and_requires_explicit_recovery",
            "--nocapture",
        ])
        .env(HELPER_MODE_ENV, "crash")
        .env(HELPER_APP_DATA_ENV, &app_data.path)
        .env(HELPER_PROFILE_ID_ENV, profile.profile_id())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .expect("spawn lock holder");
    let mut output = BufReader::new(child.stdout.take().expect("helper stdout"));
    let mut line = String::new();
    loop {
        line.clear();
        assert_ne!(output.read_line(&mut line).expect("helper output"), 0);
        if line.contains("B5_06_LOCKED") {
            break;
        }
    }

    child.kill().expect("kill lock holder");
    let status = child.wait().expect("killed helper status");
    assert!(!status.success());
    assert_eq!(fs::read(&lock_path).expect("active marker"), ACTIVE_MARKER);
    assert_eq!(
        profile
            .try_acquire_lock()
            .expect_err("killed holder must require recovery")
            .code(),
        BrowserProfileErrorCode::RecoveryRequired
    );
    assert_eq!(
        fs::read(lock_path).expect("unchanged marker"),
        ACTIVE_MARKER
    );
}

#[test]
fn dropping_without_explicit_release_preserves_recovery_required_marker() {
    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let profile = store.create_douyin_profile().expect("profile");
    let lock_path = profile.directory().join(LOCK_FILE_NAME);

    drop(profile.try_acquire_lock().expect("profile lock"));
    assert_eq!(fs::read(&lock_path).expect("active marker"), ACTIVE_MARKER);
    assert_eq!(
        profile
            .try_acquire_lock()
            .expect_err("unclean drop must require recovery")
            .code(),
        BrowserProfileErrorCode::RecoveryRequired
    );
    assert_eq!(
        fs::read(lock_path).expect("unchanged marker"),
        ACTIVE_MARKER
    );
}

#[cfg(unix)]
#[test]
fn lock_file_symlinks_permissions_corruption_and_replacement_fail_closed() {
    use std::os::unix::fs::{symlink, PermissionsExt};

    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");

    let symlink_profile = store.create_douyin_profile().expect("symlink profile");
    let outside = app_data.path.join("outside-lock");
    fs::write(&outside, b"").expect("outside lock file");
    symlink(&outside, symlink_profile.directory().join(LOCK_FILE_NAME)).expect("lock file symlink");
    assert_eq!(
        symlink_profile
            .try_acquire_lock()
            .expect_err("lock symlink must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );

    let permission_profile = store.create_douyin_profile().expect("permission profile");
    let permission_lock = permission_profile.directory().join(LOCK_FILE_NAME);
    fs::write(&permission_lock, b"").expect("permission lock file");
    fs::set_permissions(&permission_lock, fs::Permissions::from_mode(0o644))
        .expect("unsafe lock permissions");
    assert_eq!(
        permission_profile
            .try_acquire_lock()
            .expect_err("over-permissive lock must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );

    let corrupt_profile = store.create_douyin_profile().expect("corrupt profile");
    let corrupt_lock = corrupt_profile.directory().join(LOCK_FILE_NAME);
    fs::write(&corrupt_lock, b"corrupt").expect("corrupt lock state");
    fs::set_permissions(&corrupt_lock, fs::Permissions::from_mode(0o600))
        .expect("private corrupt fixture");
    assert_eq!(
        corrupt_profile
            .try_acquire_lock()
            .expect_err("corrupt state must require recovery")
            .code(),
        BrowserProfileErrorCode::RecoveryRequired
    );
    assert_eq!(fs::read(&corrupt_lock).expect("corrupt bytes"), b"corrupt");

    let replaced_profile = store.create_douyin_profile().expect("replacement profile");
    let replaced_lock_path = replaced_profile.directory().join(LOCK_FILE_NAME);
    let replaced_lock = replaced_profile
        .try_acquire_lock()
        .expect("lock to replace");
    let original_lock_path = replaced_lock_path.with_extension("original");
    fs::rename(&replaced_lock_path, &original_lock_path).expect("move locked file");
    fs::write(&replaced_lock_path, b"").expect("replacement lock file");
    fs::set_permissions(&replaced_lock_path, fs::Permissions::from_mode(0o600))
        .expect("replacement permissions");
    assert_eq!(
        replaced_lock
            .release()
            .expect_err("replacement identity must fail")
            .code(),
        BrowserProfileErrorCode::IdentityChanged
    );
    assert_eq!(
        fs::read(original_lock_path).expect("original marker"),
        ACTIVE_MARKER
    );
}

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

#[cfg(target_os = "windows")]
fn create_lock_junction(link: &std::path::Path, target: &std::path::Path) {
    let output = Command::new("cmd.exe")
        .args(["/d", "/c", "mklink", "/J"])
        .arg(link)
        .arg(target)
        .output()
        .expect("run mklink");
    assert!(
        output.status.success(),
        "mklink failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(target_os = "windows")]
#[test]
fn windows_lock_links_dacl_corruption_and_replacement_fail_closed() {
    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");

    let junction_profile = store.create_douyin_profile().expect("junction profile");
    let outside_directory = app_data.path.join("outside-lock-directory");
    fs::create_dir(&outside_directory).expect("outside lock directory");
    let junction_path = junction_profile.directory().join(LOCK_FILE_NAME);
    create_lock_junction(&junction_path, &outside_directory);
    assert_eq!(
        junction_profile
            .try_acquire_lock()
            .expect_err("lock junction must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );
    fs::remove_dir(&junction_path).expect("remove lock junction");

    let hard_link_profile = store.create_douyin_profile().expect("hard-link profile");
    let outside_file = app_data.path.join("outside-lock-file");
    fs::write(&outside_file, b"").expect("outside lock file");
    fs::hard_link(
        &outside_file,
        hard_link_profile.directory().join(LOCK_FILE_NAME),
    )
    .expect("hard-link lock file");
    assert_eq!(
        hard_link_profile
            .try_acquire_lock()
            .expect_err("multi-link lock file must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );

    let dacl_profile = store.create_douyin_profile().expect("DACL profile");
    let dacl_path = dacl_profile.directory().join(LOCK_FILE_NAME);
    dacl_profile
        .try_acquire_lock()
        .expect("create private lock file")
        .release()
        .expect("release private lock file");
    let output = Command::new("icacls.exe")
        .arg(&dacl_path)
        .args(["/grant", "*S-1-5-11:(R)"])
        .output()
        .expect("run icacls");
    assert!(
        output.status.success(),
        "icacls failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        dacl_profile
            .try_acquire_lock()
            .expect_err("broadened lock DACL must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );

    let corrupt_profile = store.create_douyin_profile().expect("corrupt profile");
    let corrupt_path = corrupt_profile.directory().join(LOCK_FILE_NAME);
    corrupt_profile
        .try_acquire_lock()
        .expect("create corrupt fixture lock")
        .release()
        .expect("release corrupt fixture lock");
    fs::write(&corrupt_path, b"corrupt").expect("corrupt lock state");
    assert_eq!(
        corrupt_profile
            .try_acquire_lock()
            .expect_err("corrupt state must require recovery")
            .code(),
        BrowserProfileErrorCode::RecoveryRequired
    );
    assert_eq!(fs::read(&corrupt_path).expect("corrupt bytes"), b"corrupt");

    let replacement_profile = store.create_douyin_profile().expect("replacement profile");
    let replacement_path = replacement_profile.directory().join(LOCK_FILE_NAME);
    let replacement_lock = replacement_profile
        .try_acquire_lock()
        .expect("lock replacement profile");
    let original_path = replacement_path.with_extension("original");
    assert!(
        fs::rename(&replacement_path, &original_path).is_err(),
        "Windows lock handle must deny rename/delete replacement"
    );
    assert_eq!(
        fs::read(&replacement_path)
            .expect_err("byte-range lock must reject another handle")
            .raw_os_error(),
        Some(33)
    );
    replacement_lock
        .release()
        .expect("release replacement profile");
    assert_eq!(fs::read(&replacement_path).expect("released marker"), b"");
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

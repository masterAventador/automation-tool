use automation_tool_desktop_lib::browser_profiles::{
    BrowserProfileErrorCode, BrowserProfileStore, SocialPlatform,
};
use std::fs;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};
use uuid::{Uuid, Variant};

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
                "automation-tool-b5-05-integration-{}-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .expect("system time")
                    .as_nanos(),
                NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed)
            ));
        fs::create_dir(&path).expect("create app data fixture");
        Self { path }
    }
}

impl Drop for TemporaryAppData {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

#[test]
fn startup_storage_probe_revalidates_the_existing_app_data_layout() {
    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");

    store
        .revalidate_storage()
        .expect("private AppData layout remains valid");

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&app_data.path, fs::Permissions::from_mode(0o755))
            .expect("broaden AppData permissions");
        assert_eq!(
            store
                .revalidate_storage()
                .expect_err("broadened AppData must fail")
                .code(),
            BrowserProfileErrorCode::UnsafeDirectory
        );
    }
}

#[test]
fn creates_and_reopens_only_the_fixed_douyin_uuid_profile() {
    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");

    let created = store.create_douyin_profile().expect("create profile");
    let parsed = Uuid::parse_str(created.profile_id()).expect("UUID profile ID");
    assert_eq!(parsed.get_version_num(), 4);
    assert_eq!(parsed.get_variant(), Variant::RFC4122);
    assert_eq!(parsed.hyphenated().to_string(), created.profile_id());
    assert_eq!(created.platform(), SocialPlatform::Douyin);
    assert_eq!(
        created.directory(),
        app_data
            .path
            .join("embedded-browser-profiles")
            .join("douyin")
            .join(created.profile_id())
    );
    created
        .revalidate()
        .expect("created identity remains stable");

    let reopened = store
        .open_douyin_profile(created.profile_id())
        .expect("reopen profile");
    assert_eq!(reopened.profile_id(), created.profile_id());
    assert_eq!(reopened.directory(), created.directory());
    reopened
        .revalidate()
        .expect("reopened identity remains stable");
}

#[test]
fn current_douyin_profile_is_created_once_and_reused_across_app_restarts() {
    let app_data = TemporaryAppData::new();
    let first_store = BrowserProfileStore::initialize(&app_data.path).expect("first store");
    let first = first_store
        .current_douyin_profile()
        .expect("create current Profile");
    let profile_id = first.profile_id().to_owned();
    drop(first);
    drop(first_store);

    let reopened = BrowserProfileStore::initialize(&app_data.path).expect("reopened store");
    let current = reopened
        .current_douyin_profile()
        .expect("reopen current Profile");

    assert_eq!(current.profile_id(), profile_id);
    assert_eq!(
        fs::read_dir(app_data.path.join("embedded-browser-profiles/douyin"))
            .expect("profile directory")
            .filter_map(Result::ok)
            .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_dir()))
            .count(),
        1,
    );
}

#[test]
fn safe_removal_deletes_only_the_current_profile_and_clears_its_marker() {
    let app_data = TemporaryAppData::new();
    fs::write(app_data.path.join("keep-me"), b"app data").expect("app sentinel");
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let current = store.current_douyin_profile().expect("current profile");
    let current_id = current.profile_id().to_owned();
    let current_directory = current.directory().to_path_buf();
    fs::write(current_directory.join("Cookies"), b"session").expect("profile data");
    let sibling = store.create_douyin_profile().expect("sibling profile");
    let sibling_directory = sibling.directory().to_path_buf();
    fs::write(sibling_directory.join("keep"), b"sibling").expect("sibling data");
    drop(current);
    drop(sibling);

    store
        .remove_current_douyin_profile()
        .expect("remove current profile");

    assert!(!current_directory.exists());
    assert!(sibling_directory.join("keep").is_file());
    assert_eq!(
        fs::read(app_data.path.join("keep-me")).unwrap(),
        b"app data"
    );
    assert!(!app_data
        .path
        .join("embedded-browser-profiles/current-douyin-profile-v1")
        .exists());
    let replacement = store.current_douyin_profile().expect("replacement current");
    assert_ne!(replacement.profile_id(), current_id);
}

#[test]
fn safe_removal_resumes_one_staged_tombstone_after_a_crash() {
    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let current = store.current_douyin_profile().expect("current profile");
    let profile_id = current.profile_id().to_owned();
    let staged = current
        .directory()
        .parent()
        .expect("platform directory")
        .join(format!(".removing-{profile_id}"));
    fs::rename(current.directory(), &staged).expect("simulate staged removal");
    drop(current);

    store
        .remove_current_douyin_profile()
        .expect("resume staged removal");

    assert!(!staged.exists());
    assert!(!app_data
        .path
        .join("embedded-browser-profiles/current-douyin-profile-v1")
        .exists());
}

#[test]
fn safe_removal_fails_closed_when_original_and_tombstone_both_exist() {
    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let current = store.current_douyin_profile().expect("current profile");
    let staged = current
        .directory()
        .parent()
        .expect("platform directory")
        .join(format!(".removing-{}", current.profile_id()));
    fs::create_dir(&staged).expect("conflicting staged directory");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&staged, fs::Permissions::from_mode(0o700))
            .expect("private conflicting directory");
    }

    assert_eq!(
        store
            .remove_current_douyin_profile()
            .expect_err("ambiguous removal must fail")
            .code(),
        BrowserProfileErrorCode::RecoveryRequired,
    );
    assert!(current.directory().is_dir());
    assert!(staged.is_dir());
}

#[cfg(unix)]
#[test]
fn safe_removal_never_follows_a_staged_leaf_symlink() {
    use std::os::unix::fs::symlink;

    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let current = store.current_douyin_profile().expect("current profile");
    let staged = current
        .directory()
        .parent()
        .expect("platform directory")
        .join(format!(".removing-{}", current.profile_id()));
    let outside = app_data.path.with_extension("outside-profile");
    fs::rename(current.directory(), &outside).expect("move profile outside fixed root");
    fs::write(outside.join("keep"), b"outside").expect("outside sentinel");
    symlink(&outside, &staged).expect("staged leaf symlink");
    drop(current);

    assert_eq!(
        store
            .remove_current_douyin_profile()
            .expect_err("staged symlink must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory,
    );
    assert_eq!(fs::read(outside.join("keep")).unwrap(), b"outside");
    fs::remove_file(staged).expect("remove staged symlink");
    fs::rename(&outside, app_data.path.join("restored-profile")).expect("restore cleanup scope");
}

#[test]
fn rejects_noncanonical_or_non_v4_profile_identifiers_without_path_escape() {
    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let invalid = [
        "../outside",
        "550e8400-e29b-41d4-a716-446655440000/child",
        "550E8400-E29B-41D4-A716-446655440000",
        "550e8400e29b41d4a716446655440000",
        "550e8400-e29b-11d4-a716-446655440000",
        "nickname",
        "",
    ];

    for profile_id in invalid {
        assert_eq!(
            store
                .open_douyin_profile(profile_id)
                .expect_err("invalid identifier must fail")
                .code(),
            BrowserProfileErrorCode::InvalidProfileId
        );
    }
    assert!(!app_data.path.join("outside").exists());
}

#[cfg(unix)]
#[test]
fn every_created_directory_is_private_and_symlink_components_are_rejected() {
    use std::os::unix::fs::{symlink, PermissionsExt};

    let app_data = TemporaryAppData::new();
    fs::set_permissions(&app_data.path, fs::Permissions::from_mode(0o755))
        .expect("make fixture over-permissive");
    let store = BrowserProfileStore::initialize(&app_data.path).expect("repair private root");
    let profile = store.create_douyin_profile().expect("create profile");
    for directory in [
        app_data.path.clone(),
        app_data.path.join("embedded-browser-profiles"),
        app_data.path.join("embedded-browser-profiles/douyin"),
        profile.directory().to_path_buf(),
    ] {
        assert_eq!(
            fs::symlink_metadata(directory)
                .expect("directory metadata")
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
    }
    fs::set_permissions(profile.directory(), fs::Permissions::from_mode(0o755))
        .expect("tamper profile permissions");
    assert_eq!(
        profile
            .revalidate()
            .expect_err("permission tampering must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );
    assert_eq!(
        fs::symlink_metadata(profile.directory())
            .expect("tampered profile metadata")
            .permissions()
            .mode()
            & 0o777,
        0o755,
        "revalidation must not mutate a path before accepting its identity",
    );
    fs::set_permissions(profile.directory(), fs::Permissions::from_mode(0o700))
        .expect("restore fixture permissions");

    let symlink_app_data = TemporaryAppData::new();
    let actual = symlink_app_data.path.with_extension("actual");
    fs::rename(&symlink_app_data.path, &actual).expect("move real app data");
    symlink(&actual, &symlink_app_data.path).expect("symlink app data");
    assert_eq!(
        BrowserProfileStore::initialize(&symlink_app_data.path)
            .expect_err("symlink app data must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );
    fs::remove_file(&symlink_app_data.path).expect("remove symlink fixture");
    fs::rename(&actual, &symlink_app_data.path).expect("restore fixture");

    let child_symlink_app_data = TemporaryAppData::new();
    let outside = child_symlink_app_data.path.with_extension("outside");
    fs::create_dir(&outside).expect("outside directory");
    symlink(
        &outside,
        child_symlink_app_data
            .path
            .join("embedded-browser-profiles"),
    )
    .expect("symlink fixed child");
    assert_eq!(
        BrowserProfileStore::initialize(&child_symlink_app_data.path)
            .expect_err("symlink child must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );
    fs::remove_file(
        child_symlink_app_data
            .path
            .join("embedded-browser-profiles"),
    )
    .expect("remove child symlink");
    fs::remove_dir(&outside).expect("remove outside fixture");
}

#[cfg(target_os = "windows")]
fn create_junction(link: &std::path::Path, target: &std::path::Path) {
    let output = std::process::Command::new("cmd.exe")
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
fn windows_reparse_components_and_regular_file_children_fail_closed() {
    let app_data_link = TemporaryAppData::new();
    let actual = app_data_link.path.join("actual");
    let junction = app_data_link.path.join("junction");
    fs::create_dir(&actual).expect("actual AppData directory");
    create_junction(&junction, &actual);
    assert_eq!(
        BrowserProfileStore::initialize(&junction)
            .expect_err("junction AppData must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );
    fs::remove_dir(&junction).expect("remove AppData junction");

    let fixed_child = TemporaryAppData::new();
    let outside = fixed_child.path.join("outside");
    fs::create_dir(&outside).expect("outside fixed child");
    create_junction(
        &fixed_child.path.join("embedded-browser-profiles"),
        &outside,
    );
    assert_eq!(
        BrowserProfileStore::initialize(&fixed_child.path)
            .expect_err("junction fixed child must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );
    fs::remove_dir(fixed_child.path.join("embedded-browser-profiles"))
        .expect("remove fixed child junction");

    let regular_child = TemporaryAppData::new();
    fs::write(
        regular_child.path.join("embedded-browser-profiles"),
        b"not a directory",
    )
    .expect("regular fixed child");
    assert_eq!(
        BrowserProfileStore::initialize(&regular_child.path)
            .expect_err("regular fixed child must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );

    let profile_leaf = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&profile_leaf.path).expect("profile store");
    let profile_id = "550e8400-e29b-41d4-a716-446655440000";
    let outside = profile_leaf.path.join("outside");
    fs::create_dir(&outside).expect("outside profile directory");
    let junction = profile_leaf
        .path
        .join("embedded-browser-profiles/douyin")
        .join(profile_id);
    create_junction(&junction, &outside);
    assert_eq!(
        store
            .open_douyin_profile(profile_id)
            .expect_err("junction Profile leaf must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );
    fs::remove_dir(&junction).expect("remove Profile junction");
}

#[cfg(target_os = "windows")]
#[test]
fn windows_private_dacl_is_applied_and_tampering_fails_closed() {
    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("repair private AppData");
    let profile = store
        .create_douyin_profile()
        .expect("create private Profile");
    profile.revalidate().expect("private DACL validates");

    let output = std::process::Command::new("icacls.exe")
        .arg(profile.directory())
        .args(["/grant", "*S-1-5-11:(OI)(CI)(M)"])
        .output()
        .expect("run icacls");
    assert!(
        output.status.success(),
        "icacls failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        profile
            .revalidate()
            .expect_err("broadened Profile DACL must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );
}
#[test]
fn replacing_a_profile_directory_invalidates_the_open_profile() {
    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let profile = store.create_douyin_profile().expect("create profile");
    let original = profile.directory().with_extension("original");
    fs::rename(profile.directory(), &original).expect("move original profile");
    fs::create_dir(profile.directory()).expect("replace profile directory");

    assert_eq!(
        profile
            .revalidate()
            .expect_err("identity replacement must fail")
            .code(),
        BrowserProfileErrorCode::IdentityChanged
    );
}

#[cfg(unix)]
#[test]
fn regular_files_leaf_symlinks_and_missing_profiles_fail_closed() {
    use std::os::unix::fs::symlink;

    let invalid_root = TemporaryAppData::new();
    fs::write(
        invalid_root.path.join("embedded-browser-profiles"),
        b"not a directory",
    )
    .expect("regular fixed child");
    assert_eq!(
        BrowserProfileStore::initialize(&invalid_root.path)
            .expect_err("regular fixed child must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );

    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let profile_id = "550e8400-e29b-41d4-a716-446655440000";
    assert_eq!(
        store
            .open_douyin_profile(profile_id)
            .expect_err("missing profile must fail")
            .code(),
        BrowserProfileErrorCode::ProfileNotFound
    );
    let outside = app_data.path.with_extension("profile-outside");
    fs::create_dir(&outside).expect("outside profile directory");
    symlink(
        &outside,
        app_data
            .path
            .join("embedded-browser-profiles/douyin")
            .join(profile_id),
    )
    .expect("profile leaf symlink");
    assert_eq!(
        store
            .open_douyin_profile(profile_id)
            .expect_err("profile symlink must fail")
            .code(),
        BrowserProfileErrorCode::UnsafeDirectory
    );
    fs::remove_file(
        app_data
            .path
            .join("embedded-browser-profiles/douyin")
            .join(profile_id),
    )
    .expect("remove profile symlink");
    fs::remove_dir(&outside).expect("remove outside directory");
}

#[test]
fn concurrent_profile_creation_is_atomic_and_never_reuses_an_identifier() {
    let app_data = TemporaryAppData::new();
    let app_data_path = app_data.path.clone();
    let workers = (0..8)
        .map(|_| {
            let path = app_data_path.clone();
            std::thread::spawn(move || {
                BrowserProfileStore::initialize(&path)
                    .expect("concurrent store")
                    .create_douyin_profile()
                    .expect("concurrent profile")
                    .profile_id()
                    .to_owned()
            })
        })
        .collect::<Vec<_>>();
    let mut profile_ids = workers
        .into_iter()
        .map(|worker| worker.join().expect("worker result"))
        .collect::<Vec<_>>();
    profile_ids.sort();
    profile_ids.dedup();
    assert_eq!(profile_ids.len(), 8);
}

/// EB-09：内置浏览器时代使用全新 Profile 根目录，绝不读取或迁移旧根。
#[test]
fn embedded_era_uses_a_fresh_root_and_never_reads_the_legacy_root() {
    let app_data = TemporaryAppData::new();

    // 预置一个"开发期旧根"，内含伪造的历史 Profile 与指针文件。
    let legacy_root = app_data.path.join("browser-profiles");
    fs::create_dir_all(legacy_root.join("douyin/legacy-profile")).expect("legacy tree");
    fs::write(
        legacy_root.join("current-douyin-profile-v1"),
        b"legacy-profile",
    )
    .expect("legacy pointer");

    let store = BrowserProfileStore::initialize(&app_data.path).expect("initialize");
    // 生产首启入口：current 不存在时创建全新 Profile。
    let profile = store.current_douyin_profile().expect("fresh profile");

    // 新 Profile 必须落在全新内置根目录，不落旧根。
    assert!(profile
        .directory()
        .starts_with(app_data.path.join("embedded-browser-profiles/douyin")));
    assert_ne!(profile.profile_id(), "legacy-profile");
    // 登录后复用：第二次读取返回同一 Profile（稳定路径）。
    let reused = store.current_douyin_profile().expect("reused profile");
    assert_eq!(reused.profile_id(), profile.profile_id());
    assert_eq!(reused.directory(), profile.directory());
    // 旧根保持原样：既不被迁移也不被删除（开发清理规则另行处理）。
    assert!(legacy_root.join("douyin/legacy-profile").is_dir());

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let root_mode = fs::metadata(app_data.path.join("embedded-browser-profiles"))
            .expect("root metadata")
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(root_mode, 0o700, "fresh root must be private");
    }
}

/// PC-25：登录处理的浏览器还开着时按下「安全注销」。
///
/// 这是 b5_13 立案的第五个产品真缺陷的最小复现。用户能做的补救确实存在
/// ——关掉那个运营浏览器窗口再来一次——但只有 `ProfileInUse` 这个码会把
/// 这句话说出口（`PlatformSessions.tsx` 已经为它写好了「先关掉已经打开的
/// 运营浏览器窗口」）。任何其它码都会让界面说成「这是本产品自身的问题，
/// 重新操作不会有效」，于是既没删掉、又劝人别重试。
#[test]
fn safe_removal_while_the_login_browser_holds_the_profile_stays_actionable() {
    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let current = store.current_douyin_profile().expect("current profile");
    let current_directory = current.directory().to_path_buf();
    // 登录处理开着 = 这个 Profile 的锁被持有。
    let held = current.try_acquire_lock().expect("login browser lock");

    let error = store
        .remove_current_douyin_profile()
        .expect_err("removal must refuse while the profile is held");

    assert_eq!(error.code(), BrowserProfileErrorCode::ProfileInUse);
    // 拒绝必须是干净的：登录态原样留在盘上，标记还在，重试才有意义。
    assert!(current_directory.is_dir());
    assert!(app_data
        .path
        .join("embedded-browser-profiles/current-douyin-profile-v1")
        .exists());

    held.release().expect("release the login browser lock");
    drop(current);
    store
        .remove_current_douyin_profile()
        .expect("removal succeeds once the browser is closed");
    assert!(!current_directory.exists());
}

/// PC-25 诊断：被真 Chromium 摸过的 Profile 还能不能干净删除。
///
/// b5_13 的登出在真实场景里报 `profile_identity_changed`，幸存进程为零、
/// 残留物是年轻 Chromium profile 的骨架（含未清收尾的 sqlite journal）。
/// 本测试把最小要素搬进实验室：内置 Chromium 以该 Profile 起一次、SIGKILL
/// 整组（模拟紧急停止）、随后删除。红 = 实验室内复现，可单步定位是四处
/// 身份检查里的哪一处；绿 = 触发条件在执行器并发，而不在 Chromium 文件。
#[cfg(target_os = "macos")]
#[test]
#[ignore = "requires the staged embedded Chromium under target/debug"]
fn chromium_touched_profile_still_removes_cleanly() {
    use std::io::Read;
    use std::os::unix::process::CommandExt;
    use std::process::{Command, Stdio};

    let browser = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join(
        "target/debug/embedded-browser/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    );
    assert!(browser.is_file(), "staged embedded Chromium is required");

    let app_data = TemporaryAppData::new();
    let store = BrowserProfileStore::initialize(&app_data.path).expect("profile store");
    let current = store.current_douyin_profile().expect("current profile");
    let profile_directory = current.directory().to_path_buf();
    drop(current);

    let mut child = Command::new(&browser)
        .arg("--headless")
        .arg("--no-first-run")
        .arg("--no-default-browser-check")
        .arg(format!("--user-data-dir={}", profile_directory.display()))
        .arg("about:blank")
        .process_group(0)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .expect("launch embedded chromium on the profile");
    // 给它时间落盘 profile 骨架（Local State / Default / first_party_sets…）。
    std::thread::sleep(std::time::Duration::from_millis(2500));
    let group = child.id() as i32;
    let killed = unsafe { libc::kill(-group, libc::SIGKILL) };
    let kill_errno = std::io::Error::last_os_error().raw_os_error();
    assert!(
        killed == 0 || kill_errno == Some(libc::ESRCH),
        "SIGKILL the browser group: {kill_errno:?}",
    );
    let _ = child.wait();
    let mut stderr = Vec::new();
    if let Some(mut pipe) = child.stderr.take() {
        let _ = pipe.read_to_end(&mut stderr);
    }

    let removal = store.remove_current_douyin_profile();
    assert!(
        removal.is_ok(),
        "removal after a killed Chromium must succeed, got {:?}; chromium stderr: {}",
        removal.map(|_| ()).unwrap_err().code(),
        String::from_utf8_lossy(&stderr),
    );
    assert!(!profile_directory.exists());
}

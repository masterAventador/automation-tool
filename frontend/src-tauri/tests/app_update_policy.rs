use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::app_update_policy::{
    UpdatePolicyAction, UpdatePolicyErrorCode, UpdatePolicyService,
};
use automation_tool_desktop_lib::app_updates::{
    parse_update_release, UpdateDecision, UpdateRelease,
};
use serde_json::json;

static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(0);

struct TemporaryAppData(PathBuf);

impl TemporaryAppData {
    fn new() -> Self {
        Self(std::env::temp_dir().join(format!(
            "automation-tool-h8-19-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed),
        )))
    }

    fn policy_file(&self) -> PathBuf {
        self.0.join("app-updates/update-policy-v1")
    }
}

impl Drop for TemporaryAppData {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn release(version: &str, policy: &str, sha: char) -> UpdateRelease {
    parse_update_release(
        version,
        &json!({
            "version": version,
            "url": format!("https://updates.example.test/app-{version}.tar.gz"),
            "signature": "trusted-minisign-signature",
            "notes": "A safe release",
            "pub_date": "2026-07-22T00:00:00Z",
            "update_contract": {
                "version": 1,
                "channel": "stable",
                "policy": policy,
                "artifact": {
                    "target": "darwin",
                    "arch": "aarch64",
                    "sha256": sha.to_string().repeat(64),
                    "size_bytes": 1024
                }
            }
        }),
    )
    .expect("valid release fixture")
}

#[test]
fn optional_update_defer_reprompts_skip_suppresses_and_newer_version_resets_the_choice() {
    let app_data = TemporaryAppData::new();
    let service =
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable").expect("policy service");
    let optional = release("1.1.0", "optional", 'a');

    assert_eq!(
        service
            .observe_release(optional.clone())
            .expect("observe optional")
            .action(),
        UpdatePolicyAction::Prompt
    );
    assert_eq!(
        service
            .decide(UpdateDecision::Defer)
            .expect("defer optional")
            .action(),
        UpdatePolicyAction::Deferred
    );
    drop(service);

    let reopened =
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable").expect("reopen policy");
    assert_eq!(
        reopened
            .observe_release(optional.clone())
            .expect("startup check re-observes deferred release")
            .action(),
        UpdatePolicyAction::Prompt
    );
    assert_eq!(
        reopened
            .decide(UpdateDecision::SkipVersion)
            .expect("skip optional")
            .action(),
        UpdatePolicyAction::Skipped
    );
    drop(reopened);

    let reopened =
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable").expect("reopen policy");
    assert_eq!(
        reopened
            .observe_release(optional)
            .expect("observe skipped release")
            .action(),
        UpdatePolicyAction::Suppressed
    );
    assert_eq!(
        reopened
            .observe_release(release("1.2.0", "optional", 'b'))
            .expect("newer release clears old skip")
            .action(),
        UpdatePolicyAction::Prompt
    );
    assert_eq!(reopened.record().expect("policy record").decision(), None);
}

#[test]
fn optional_install_now_is_persistent_until_the_installed_version_advances() {
    let app_data = TemporaryAppData::new();
    let service =
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable").expect("policy service");
    let optional = release("1.1.0", "optional", 'a');
    service
        .observe_release(optional.clone())
        .expect("observe optional");
    assert_eq!(
        service
            .decide(UpdateDecision::InstallNow)
            .expect("request install")
            .action(),
        UpdatePolicyAction::InstallRequested
    );
    assert_eq!(
        service
            .decide(UpdateDecision::SkipVersion)
            .expect_err("completed prompt cannot accept a second decision")
            .code(),
        UpdatePolicyErrorCode::CandidateUnavailable
    );
    drop(service);

    let reopened =
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable").expect("reopen policy");
    assert_eq!(
        reopened
            .observe_release(optional)
            .expect("re-observe requested install")
            .action(),
        UpdatePolicyAction::InstallRequested
    );
    drop(reopened);

    let upgraded =
        UpdatePolicyService::initialize(&app_data.0, "1.1.0", "stable").expect("upgraded app");
    let record = upgraded.record().expect("upgraded record");
    assert_eq!(record.minimum_version(), "1.1.0");
    assert_eq!(record.highest_observed_version(), None);
    assert_eq!(record.decision(), None);
}

#[test]
fn deferred_or_suppressed_prompt_requires_a_fresh_observation_before_another_decision() {
    let app_data = TemporaryAppData::new();
    let service =
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable").expect("policy service");
    let optional = release("1.1.0", "optional", 'a');
    service
        .observe_release(optional.clone())
        .expect("observe optional");
    service
        .decide(UpdateDecision::Defer)
        .expect("defer optional");
    assert_eq!(
        service
            .decide(UpdateDecision::SkipVersion)
            .expect_err("closed prompt rejects a stale second click")
            .code(),
        UpdatePolicyErrorCode::CandidateUnavailable
    );

    service
        .observe_release(optional.clone())
        .expect("fresh check opens prompt again");
    service
        .decide(UpdateDecision::SkipVersion)
        .expect("skip after fresh prompt");
    assert_eq!(
        service
            .observe_release(optional)
            .expect("skipped version remains suppressed")
            .action(),
        UpdatePolicyAction::Suppressed
    );
    assert_eq!(
        service
            .decide(UpdateDecision::InstallNow)
            .expect_err("suppressed release has no active prompt")
            .code(),
        UpdatePolicyErrorCode::CandidateUnavailable
    );
}

#[test]
fn forced_update_cannot_be_deferred_or_skipped() {
    let app_data = TemporaryAppData::new();
    let service =
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable").expect("policy service");
    let forced = release("2.0.0", "forced", 'f');
    assert_eq!(
        service
            .observe_release(forced.clone())
            .expect("observe forced")
            .action(),
        UpdatePolicyAction::Forced
    );
    for decision in [
        UpdateDecision::InstallNow,
        UpdateDecision::Defer,
        UpdateDecision::SkipVersion,
    ] {
        assert_eq!(
            service
                .decide(decision)
                .expect_err("forced decision rejected")
                .code(),
            UpdatePolicyErrorCode::DecisionNotAllowed
        );
    }
    drop(service);

    let reopened =
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable").expect("reopen policy");
    assert_eq!(
        reopened
            .observe_release(forced)
            .expect("forced policy survives restart")
            .action(),
        UpdatePolicyAction::Forced
    );
}

#[test]
fn stale_versions_and_same_version_identity_mutation_fail_closed() {
    let app_data = TemporaryAppData::new();
    let service =
        UpdatePolicyService::initialize(&app_data.0, "1.5.0", "stable").expect("policy service");
    service
        .observe_release(release("2.0.0", "optional", 'a'))
        .expect("observe highest release");

    assert_eq!(
        service
            .observe_release(release("1.9.0", "optional", 'b'))
            .expect_err("observed version cannot move backwards")
            .code(),
        UpdatePolicyErrorCode::ReleaseStale
    );
    assert_eq!(
        service
            .observe_release(release("2.0.0", "optional", 'b'))
            .expect_err("same version digest cannot mutate")
            .code(),
        UpdatePolicyErrorCode::ReleaseMutation
    );
    assert_eq!(
        service
            .observe_release(release("1.5.0", "optional", 'c'))
            .expect_err("installed version is not an update")
            .code(),
        UpdatePolicyErrorCode::ReleaseStale
    );
    assert_eq!(
        service.record().expect("record").highest_observed_version(),
        Some("2.0.0")
    );
}

#[test]
fn persisted_policy_is_canonical_private_and_corruption_fails_closed() {
    let app_data = TemporaryAppData::new();
    let service =
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable").expect("policy service");
    service
        .observe_release(release("1.1.0", "optional", 'a'))
        .expect("persist observed release");
    drop(service);

    let bytes = fs::read(app_data.policy_file()).expect("read policy fixture");
    let encoded = std::str::from_utf8(&bytes).expect("UTF-8 policy");
    let document: serde_json::Value = serde_json::from_slice(&bytes).expect("canonical JSON");
    assert_eq!(document["schemaVersion"], 1);
    assert_eq!(document["configuredChannel"], "stable");
    assert!(!encoded.contains("url"));
    assert!(!encoded.contains("signature"));

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        let directory_mode = fs::metadata(app_data.0.join("app-updates"))
            .expect("policy directory")
            .permissions()
            .mode();
        let file_mode = fs::metadata(app_data.policy_file())
            .expect("policy file")
            .permissions()
            .mode();
        assert_eq!(directory_mode & 0o077, 0);
        assert_eq!(file_mode & 0o077, 0);
    }

    fs::write(app_data.policy_file(), b"{\"schemaVersion\":2}").expect("corrupt future schema");
    assert_eq!(
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable")
            .expect_err("future or incomplete schema rejected")
            .code(),
        UpdatePolicyErrorCode::StorageUnavailable
    );
}

#[test]
fn previous_policy_schema_is_migrated_and_rewritten_on_startup() {
    let app_data = TemporaryAppData::new();
    drop(
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable")
            .expect("create policy file"),
    );
    fs::write(
        app_data.policy_file(),
        br#"{"schemaVersion":0,"configuredChannel":"stable","minimumVersion":"1.0.0","highestObserved":null,"decision":null,"revision":1}"#,
    )
    .expect("write previous policy schema");

    let reopened = UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable");
    assert!(
        reopened.is_ok(),
        "previous policy schema must migrate instead of aborting startup"
    );
    let document: serde_json::Value =
        serde_json::from_slice(&fs::read(app_data.policy_file()).expect("rewritten policy"))
            .expect("valid rewritten policy");
    assert_eq!(document["schemaVersion"], 1);
    assert_eq!(document["configuredChannel"], "stable");
    assert_eq!(document["revision"], 2);
}

#[test]
fn configured_channel_change_resets_old_channel_state_and_rewrites_policy() {
    let app_data = TemporaryAppData::new();
    drop(
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable")
            .expect("create policy file"),
    );
    fs::write(
        app_data.policy_file(),
        br#"{"schemaVersion":1,"configuredChannel":"preview","minimumVersion":"1.0.0","highestObserved":{"version":"1.1.0","channel":"preview","policy":"optional","target":"darwin","arch":"aarch64","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","sizeBytes":1024},"decision":{"version":"1.1.0","decision":"skip_version"},"revision":4}"#,
    )
    .expect("write previous channel policy");

    let reopened = UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable");
    assert!(
        reopened.is_ok(),
        "a valid configured channel change must migrate instead of aborting startup"
    );
    let record = reopened
        .expect("migrated channel policy")
        .record()
        .expect("policy record");
    assert_eq!(record.highest_observed_version(), None);
    assert_eq!(record.decision(), None);
    let document: serde_json::Value =
        serde_json::from_slice(&fs::read(app_data.policy_file()).expect("rewritten policy"))
            .expect("valid rewritten policy");
    assert_eq!(document["configuredChannel"], "stable");
    assert_eq!(document["revision"], 5);
}

#[test]
fn semantically_valid_noncanonical_policy_is_canonicalized_on_startup() {
    let app_data = TemporaryAppData::new();
    drop(
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable")
            .expect("create policy file"),
    );
    fs::write(
        app_data.policy_file(),
        b"{\n  \"revision\": 1,\n  \"decision\": null,\n  \"highestObserved\": null,\n  \"minimumVersion\": \"1.0.0\",\n  \"configuredChannel\": \"stable\",\n  \"schemaVersion\": 1\n}\n",
    )
    .expect("write noncanonical policy");

    let reopened = UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable");
    assert!(
        reopened.is_ok(),
        "semantic policy compatibility must not depend on JSON byte order"
    );
    drop(reopened);
    assert_eq!(
        fs::read_to_string(app_data.policy_file()).expect("canonical rewritten policy"),
        r#"{"schemaVersion":1,"configuredChannel":"stable","minimumVersion":"1.0.0","highestObserved":null,"decision":null,"revision":2}"#
    );
}

#[test]
fn unknown_policy_schema_version_fails_explicitly() {
    let app_data = TemporaryAppData::new();
    drop(
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable")
            .expect("create policy file"),
    );
    fs::write(
        app_data.policy_file(),
        br#"{"schemaVersion":99,"configuredChannel":"stable","minimumVersion":"1.0.0","highestObserved":null,"decision":null,"revision":1}"#,
    )
    .expect("write unknown policy schema");

    assert_eq!(
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable")
            .expect_err("unknown policy schema must fail")
            .code(),
        UpdatePolicyErrorCode::StorageUnavailable
    );
}

#[cfg(unix)]
#[test]
fn failed_atomic_save_does_not_advance_the_in_memory_policy() {
    use std::os::unix::fs::symlink;

    let app_data = TemporaryAppData::new();
    let service =
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable").expect("policy service");
    let baseline = service.record().expect("baseline record");
    fs::remove_file(app_data.policy_file()).expect("remove policy file");
    let outside = app_data.0.with_extension("save-failure");
    fs::write(&outside, b"outside").expect("outside fixture");
    symlink(&outside, app_data.policy_file()).expect("block atomic destination");

    assert_eq!(
        service
            .observe_release(release("1.1.0", "optional", 'a'))
            .expect_err("unsafe destination blocks save")
            .code(),
        UpdatePolicyErrorCode::StorageUnavailable
    );
    assert_eq!(service.record().expect("unchanged record"), baseline);

    fs::remove_file(app_data.policy_file()).expect("remove blocking symlink");
    fs::remove_file(outside).expect("remove outside fixture");
    service
        .observe_release(release("1.1.0", "optional", 'a'))
        .expect("retry after storage recovery");
    drop(service);
    assert_eq!(
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable")
            .expect("reopen recovered policy")
            .record()
            .expect("recovered record")
            .highest_observed_version(),
        Some("1.1.0")
    );
}

#[cfg(unix)]
#[test]
fn symlinked_policy_files_fail_closed_and_over_permissive_files_are_repaired() {
    use std::os::unix::fs::{symlink, PermissionsExt};

    let linked = TemporaryAppData::new();
    fs::create_dir_all(linked.0.join("app-updates")).expect("policy directory");
    fs::set_permissions(
        linked.0.join("app-updates"),
        fs::Permissions::from_mode(0o700),
    )
    .expect("private policy directory");
    let outside = linked.0.with_extension("outside");
    fs::write(&outside, b"outside").expect("outside fixture");
    symlink(&outside, linked.policy_file()).expect("policy symlink");
    assert_eq!(
        UpdatePolicyService::initialize(&linked.0, "1.0.0", "stable")
            .expect_err("symlink rejected")
            .code(),
        UpdatePolicyErrorCode::StorageUnavailable
    );
    fs::remove_file(&outside).expect("remove outside fixture");

    let permissive = TemporaryAppData::new();
    let service =
        UpdatePolicyService::initialize(&permissive.0, "1.0.0", "stable").expect("policy service");
    drop(service);
    fs::set_permissions(permissive.policy_file(), fs::Permissions::from_mode(0o644))
        .expect("broaden policy permissions");
    drop(
        UpdatePolicyService::initialize(&permissive.0, "1.0.0", "stable")
            .expect("repair over-permissive policy file"),
    );
    assert_eq!(
        fs::metadata(permissive.policy_file())
            .expect("repaired policy metadata")
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
}

#[test]
fn concurrent_observations_are_serialized_and_keep_the_highest_version() {
    let app_data = TemporaryAppData::new();
    let service = Arc::new(
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable").expect("policy service"),
    );
    let mut workers = Vec::new();
    for minor in 1..=8 {
        let service = Arc::clone(&service);
        workers.push(std::thread::spawn(move || {
            let version = format!("1.{minor}.0");
            let _ = service.observe_release(release(&version, "optional", 'a'));
        }));
    }
    for worker in workers {
        worker.join().expect("observation worker");
    }
    assert_eq!(
        service.record().expect("record").highest_observed_version(),
        Some("1.8.0")
    );
    drop(service);

    let reopened =
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable").expect("reopen policy");
    assert_eq!(
        reopened
            .record()
            .expect("persisted record")
            .highest_observed_version(),
        Some("1.8.0")
    );
}

#[test]
fn invalid_configuration_and_decision_without_a_candidate_are_rejected() {
    let app_data = TemporaryAppData::new();
    assert_eq!(
        UpdatePolicyService::initialize(&app_data.0, "01.0.0", "stable")
            .expect_err("noncanonical current version rejected")
            .code(),
        UpdatePolicyErrorCode::ConfigurationInvalid
    );
    assert_eq!(
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "Stable")
            .expect_err("unsafe channel rejected")
            .code(),
        UpdatePolicyErrorCode::ConfigurationInvalid
    );
    assert_eq!(
        UpdatePolicyService::initialize(PathBuf::from("relative").as_path(), "1.0.0", "stable")
            .expect_err("relative AppData rejected")
            .code(),
        UpdatePolicyErrorCode::StorageUnavailable
    );

    let service =
        UpdatePolicyService::initialize(&app_data.0, "1.0.0", "stable").expect("policy service");
    assert_eq!(
        service
            .decide(UpdateDecision::InstallNow)
            .expect_err("decision requires active candidate")
            .code(),
        UpdatePolicyErrorCode::CandidateUnavailable
    );
}

//! PB-07: the one shape the App renders for both publishing platforms.
//!
//! The two platforms reach their platform by completely different means, and
//! that difference must stop at this boundary: what leaves here is a stage, an
//! availability and an outcome, never a mechanism. A platform nobody configured
//! yet has to stay listed and inert rather than taking the module down with it.

use automation_tool_desktop_lib::publish_workspace::{
    PublishApproval, PublishAvailability, PublishOutcome, PublishPlatform, PublishPlatformState,
    PublishRoute, PublishStage, PublishWorkspace, PublishWorkspaceError,
};

/// One distinct publish job identity per call site.
///
/// The bridge mints a real UUIDv4 per publish; these only have to be distinct
/// and canonical, which is what proves they are not the confirmation identity.
fn publish_job(sequence: u8) -> String {
    format!("223e4567-e89b-42d3-a456-4266141740{sequence:02x}")
}

fn ready_approval() -> PublishApproval {
    PublishApproval::new(
        "自动化运营测试账号",
        "clip.mp4 · 12.4 MB",
        "自动化运营工具发布验收标题",
        "自动化运营工具发布验收简介",
        "123e4567-e89b-42d3-a456-426614174007",
    )
    .expect("a readable approval")
}

#[test]
fn an_unconfigured_platform_stays_listed_instead_of_taking_the_module_down() {
    let workspace = PublishWorkspace::new(false);

    let snapshot = workspace.snapshot();

    let bilibili = snapshot.platform("bilibili").expect("bilibili is listed");
    assert_eq!(
        bilibili.availability,
        PublishAvailability::AwaitingConfiguration
    );
    let douyin = snapshot.platform("douyin").expect("douyin is listed");
    assert_eq!(douyin.availability, PublishAvailability::AwaitingSignIn);
    assert_eq!(snapshot.platforms.len(), 2);
}

#[test]
fn configured_credentials_make_the_official_platform_publishable() {
    let workspace = PublishWorkspace::new(true);

    let snapshot = workspace.snapshot();

    assert_eq!(
        snapshot.platform("bilibili").expect("listed").availability,
        PublishAvailability::Ready
    );
}

#[test]
fn a_signed_in_operations_browser_makes_the_visible_platform_publishable() {
    let mut workspace = PublishWorkspace::new(false);

    workspace.observe_douyin_signed_in(true);

    assert_eq!(
        workspace
            .snapshot()
            .platform("douyin")
            .expect("listed")
            .availability,
        PublishAvailability::Ready
    );
}

#[test]
fn the_projected_snapshot_never_names_the_mechanism() {
    let mut workspace = PublishWorkspace::new(true);
    workspace.observe_douyin_signed_in(true);
    workspace
        .begin("douyin", publish_job(1))
        .expect("a publishable platform");
    workspace.await_approval(ready_approval());

    let projected = serde_json::to_string(&workspace.snapshot()).expect("serializable");

    for secret in [
        "browser_use",
        "browserUse",
        "playwright",
        "chromium",
        "official_api",
        "officialApi",
    ] {
        assert!(
            !projected.to_lowercase().contains(&secret.to_lowercase()),
            "the App snapshot leaked {secret}"
        );
    }
}

#[test]
fn a_publish_walks_one_stage_at_a_time_to_a_settled_outcome() {
    let mut workspace = PublishWorkspace::new(false);
    workspace.observe_douyin_signed_in(true);

    assert_eq!(workspace.snapshot().stage, PublishStage::Idle);
    workspace
        .begin("douyin", publish_job(2))
        .expect("publishable");
    assert_eq!(workspace.snapshot().stage, PublishStage::Preparing);
    workspace.await_approval(ready_approval());
    assert_eq!(workspace.snapshot().stage, PublishStage::AwaitingApproval);
    workspace.approve().expect("an approval is pending");
    assert_eq!(workspace.snapshot().stage, PublishStage::Publishing);
    workspace.begin_verification();
    assert_eq!(workspace.snapshot().stage, PublishStage::Verifying);
    workspace.settle(PublishOutcome::Published);

    let snapshot = workspace.snapshot();
    assert_eq!(snapshot.stage, PublishStage::Settled);
    assert_eq!(snapshot.outcome, Some(PublishOutcome::Published));
    assert!(
        snapshot.approval.is_none(),
        "a spent approval is not shown again"
    );
}

#[test]
fn the_critical_point_hands_over_the_account_summary_and_copy() {
    let mut workspace = PublishWorkspace::new(false);
    workspace.observe_douyin_signed_in(true);
    workspace
        .begin("douyin", publish_job(3))
        .expect("publishable");

    workspace.await_approval(ready_approval());

    let snapshot = workspace.snapshot();
    let approval = snapshot.approval.expect("the critical point is presented");
    assert_eq!(approval.target_account, "自动化运营测试账号");
    assert_eq!(approval.video_summary, "clip.mp4 · 12.4 MB");
    assert_eq!(approval.title, "自动化运营工具发布验收标题");
    assert_eq!(approval.description, "自动化运营工具发布验收简介");
}

#[test]
fn approving_without_a_pending_critical_point_is_refused() {
    let mut workspace = PublishWorkspace::new(false);
    workspace.observe_douyin_signed_in(true);
    workspace
        .begin("douyin", publish_job(4))
        .expect("publishable");

    assert_eq!(
        workspace.approve(),
        Err(PublishWorkspaceError::NoApprovalPending)
    );
    assert_eq!(workspace.snapshot().stage, PublishStage::Preparing);
}

#[test]
fn a_platform_that_is_not_publishable_cannot_be_started() {
    let mut workspace = PublishWorkspace::new(false);

    assert_eq!(
        workspace.begin("bilibili", publish_job(5)),
        Err(PublishWorkspaceError::NotPublishable)
    );
    assert_eq!(
        workspace.begin("douyin", publish_job(6)),
        Err(PublishWorkspaceError::NotPublishable)
    );
    assert_eq!(workspace.snapshot().stage, PublishStage::Idle);
}

#[test]
fn a_platform_outside_the_two_supported_ones_is_refused() {
    let mut workspace = PublishWorkspace::new(true);

    for unsupported in ["kuaishou", "xiaohongshu", "wechat_channels", "", "DOUYIN"] {
        assert_eq!(
            workspace.begin(unsupported, publish_job(16)),
            Err(PublishWorkspaceError::UnknownPlatform),
            "{unsupported} must not be publishable"
        );
    }
}

#[test]
fn cancelling_before_the_click_settles_as_cancelled() {
    let mut workspace = PublishWorkspace::new(false);
    workspace.observe_douyin_signed_in(true);
    workspace
        .begin("douyin", publish_job(7))
        .expect("publishable");
    workspace.await_approval(ready_approval());

    workspace.cancel().expect("a publish is in flight");

    let snapshot = workspace.snapshot();
    assert_eq!(snapshot.stage, PublishStage::Settled);
    assert_eq!(snapshot.outcome, Some(PublishOutcome::Cancelled));
    assert!(snapshot.approval.is_none());
}

#[test]
fn cancelling_after_the_click_is_refused_because_it_would_be_a_lie() {
    let mut workspace = PublishWorkspace::new(false);
    workspace.observe_douyin_signed_in(true);
    workspace
        .begin("douyin", publish_job(8))
        .expect("publishable");
    workspace.await_approval(ready_approval());
    workspace.approve().expect("pending");

    assert_eq!(
        workspace.cancel(),
        Err(PublishWorkspaceError::AlreadyDispatched)
    );
    assert_eq!(workspace.snapshot().stage, PublishStage::Publishing);
}

#[test]
fn an_uncertain_outcome_carries_its_own_explanation() {
    let mut workspace = PublishWorkspace::new(false);
    workspace.observe_douyin_signed_in(true);
    workspace
        .begin("douyin", publish_job(9))
        .expect("publishable");
    workspace.await_approval(ready_approval());
    workspace.approve().expect("pending");
    workspace.begin_verification();

    workspace.settle(PublishOutcome::OutcomeUncertain);

    let snapshot = workspace.snapshot();
    assert_eq!(snapshot.outcome, Some(PublishOutcome::OutcomeUncertain));
    assert!(
        !snapshot.retryable,
        "an uncertain publish is never offered as a retry"
    );
}

#[test]
fn an_approval_carrying_unreadable_text_is_refused_before_it_reaches_the_app() {
    for (account, summary, title, description) in [
        ("", "clip", "t", "d"),
        ("账号", "", "t", "d"),
        ("账号", "clip", "", "d"),
        ("账号", "clip", "t", ""),
        ("账号\u{202e}", "clip", "t", "d"),
        ("账号", "clip\u{0007}", "t", "d"),
    ] {
        assert!(
            PublishApproval::new(
                account,
                summary,
                title,
                description,
                "123e4567-e89b-42d3-a456-426614174007",
            )
            .is_err(),
            "{account}/{summary} must not be presentable"
        );
    }
}

#[test]
fn an_approval_without_a_canonical_confirmation_identity_is_refused() {
    for identity in ["", "not-a-uuid", "123E4567-E89B-42D3-A456-426614174007"] {
        assert!(PublishApproval::new("账号", "clip", "标题", "简介", identity).is_err());
    }
}

#[test]
fn a_listed_platform_reports_a_state_the_app_can_render_without_a_mechanism() {
    let workspace = PublishWorkspace::new(true);

    let snapshot = workspace.snapshot();

    for platform in &snapshot.platforms {
        let expected: PublishPlatformState = platform.clone();
        assert_eq!(&expected, platform);
        assert!(matches!(platform.platform.as_str(), "bilibili" | "douyin"));
    }
}

// --- PB-07: the audit trail -------------------------------------------------

#[test]
fn every_step_of_a_publish_is_recorded_in_order() {
    let mut workspace = PublishWorkspace::new(false);
    workspace.observe_douyin_signed_in(true);

    workspace
        .begin("douyin", publish_job(10))
        .expect("publishable");
    workspace.await_approval(ready_approval());
    workspace.approve().expect("pending");
    workspace.begin_verification();
    workspace.settle(PublishOutcome::Published);

    let snapshot = workspace.snapshot();
    let steps: Vec<&str> = snapshot
        .audit
        .iter()
        .map(|entry| entry.step.as_str())
        .collect();
    assert_eq!(
        steps,
        [
            "publish_started",
            "approval_presented",
            "approval_given",
            "verification_started",
            "settled",
        ]
    );
}

#[test]
fn the_audit_records_the_decision_and_never_the_content() {
    let mut workspace = PublishWorkspace::new(false);
    workspace.observe_douyin_signed_in(true);
    workspace
        .begin("douyin", publish_job(11))
        .expect("publishable");
    workspace.await_approval(ready_approval());
    workspace.approve().expect("pending");
    workspace.settle(PublishOutcome::Published);

    let projected = serde_json::to_string(&workspace.snapshot().audit).expect("serializable");

    // An audit is a record of what was decided; the video's title and body are
    // content, and copying them into a growing trail only widens where they live.
    assert!(!projected.contains("自动化运营工具发布验收标题"));
    assert!(!projected.contains("自动化运营工具发布验收简介"));
    assert!(!projected.contains("自动化运营测试账号"));
    assert!(projected.contains("123e4567-e89b-42d3-a456-426614174007"));
}

#[test]
fn a_refused_step_leaves_no_trace_in_the_audit() {
    let mut workspace = PublishWorkspace::new(false);

    assert!(workspace.begin("bilibili", publish_job(12)).is_err());
    assert!(workspace.approve().is_err());
    assert!(workspace.cancel().is_err());

    assert!(workspace.snapshot().audit.is_empty());
}

#[test]
fn a_cancelled_publish_is_recorded_as_cancelled() {
    let mut workspace = PublishWorkspace::new(false);
    workspace.observe_douyin_signed_in(true);
    workspace
        .begin("douyin", publish_job(13))
        .expect("publishable");
    workspace.await_approval(ready_approval());

    workspace.cancel().expect("in flight");

    let audit = workspace.snapshot().audit;
    let last = audit.last().expect("a settled step");
    assert_eq!(last.step, "settled");
    assert_eq!(last.outcome, Some(PublishOutcome::Cancelled));
}

#[test]
fn starting_a_second_publish_keeps_the_first_ones_record() {
    let mut workspace = PublishWorkspace::new(false);
    workspace.observe_douyin_signed_in(true);
    workspace
        .begin("douyin", publish_job(14))
        .expect("publishable");
    workspace.settle(PublishOutcome::NotPublished);

    workspace
        .begin("douyin", publish_job(15))
        .expect("publishable again");

    assert_eq!(workspace.snapshot().audit.len(), 3);
}

// --- PB-07: projecting executor results onto the workspace ------------------

#[test]
fn a_preflight_result_moves_the_workspace_only_where_it_is_entitled_to() {
    use automation_tool_desktop_lib::publish_workspace::preflight_outcome;

    // A ready preflight is not an outcome; it is the reason to ask the operator.
    assert_eq!(preflight_outcome("publish_pre_submit_ready"), None);
    assert_eq!(
        preflight_outcome("publish_handoff_required"),
        Some(PublishOutcome::HandedOff)
    );
    assert_eq!(
        preflight_outcome("publish_blocked"),
        Some(PublishOutcome::NotPublished)
    );
    // Anything unrecognized is not quietly treated as a success.
    assert_eq!(
        preflight_outcome("healthy"),
        Some(PublishOutcome::NotPublished)
    );
}

#[test]
fn a_dispatch_result_never_turns_an_unknown_answer_into_a_publish() {
    use automation_tool_desktop_lib::publish_workspace::dispatch_outcome;

    assert_eq!(
        dispatch_outcome("publish_verified"),
        PublishOutcome::Published
    );
    assert_eq!(
        dispatch_outcome("publish_outcome_uncertain"),
        PublishOutcome::OutcomeUncertain
    );
    assert_eq!(
        dispatch_outcome("publish_not_dispatched"),
        PublishOutcome::NotPublished
    );
    // The click may already have happened, so an answer we cannot read is
    // uncertain, never "did not publish".
    for unreadable in [
        "",
        "healthy",
        "publish_pre_submit_ready",
        "publish_released",
    ] {
        assert_eq!(
            dispatch_outcome(unreadable),
            PublishOutcome::OutcomeUncertain,
            "{unreadable} must not be read as a clean failure"
        );
    }
}

/// Read one `async fn` body out of the composition root.
fn bridge_function_body(name: &str) -> String {
    let source = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("src")
            .join("lib.rs"),
    )
    .expect("Tauri composition root");
    let start = source
        .find(&format!("async fn {name}("))
        .unwrap_or_else(|| panic!("{name} is missing from the composition root"));
    let body = &source[start..];
    let end = body
        .find("\n}\n")
        .expect("a Tauri command body ends at column zero");
    body[..end].to_owned()
}

#[test]
fn the_platform_a_publish_names_decides_the_route_it_takes() {
    // Until now the platform argument only decided whether a publish was
    // *allowed*; after that the bridge went straight to the operations browser
    // and the 抖音 profile regardless. So flipping the official platform's
    // credentials flag to true would have sent a B站 publish into 抖音's
    // browser, running 抖音's publish flow, on the operator's real account.
    // The route is a property of the platform, not of a configuration flag.
    assert_eq!(
        PublishPlatform::Douyin.route(),
        PublishRoute::OperationsBrowser,
    );
    assert_eq!(PublishPlatform::Bilibili.route(), PublishRoute::OfficialApi,);
}

#[test]
fn a_publishable_official_platform_uses_its_own_route() {
    // Configured credentials make B站 publishable through the official
    // interface; they must never make it borrow the operations browser.
    let mut workspace = PublishWorkspace::new(true);

    let platform = workspace
        .begin(
            "bilibili",
            "223e4567-e89b-42d3-a456-426614174031".to_owned(),
        )
        .expect("a configured platform may be started");

    assert_eq!(platform, PublishPlatform::Bilibili);
    assert_eq!(platform.route(), PublishRoute::OfficialApi);
}

#[test]
fn a_publish_carries_its_own_job_identity_instead_of_reusing_the_confirmation() {
    // The page used to send the confirmation identity back as the publish job
    // identity, because it had nothing else to send. Two different things
    // wearing one identity cannot be told apart in an audit.
    let mut workspace = PublishWorkspace::new(false);
    workspace.observe_douyin_signed_in(true);

    workspace
        .begin("douyin", "223e4567-e89b-42d3-a456-426614174032".to_owned())
        .expect("begin");

    assert_eq!(
        workspace.job_id(),
        Some("223e4567-e89b-42d3-a456-426614174032"),
    );
    workspace.await_approval(ready_approval());
    assert_ne!(
        workspace.job_id(),
        Some(ready_approval().confirmation_id.as_str()),
    );
    workspace.settle(PublishOutcome::Published);
    // A settled publish has no job in flight to approve against.
    assert_eq!(workspace.job_id(), None);
}

#[test]
fn the_bridge_routes_by_platform_before_it_reaches_for_any_browser() {
    let body = bridge_function_body("begin_publish");

    let routed = body
        .find("PublishRoute::OperationsBrowser")
        .expect("begin_publish must match on the route the platform takes");
    assert!(
        body.contains("PublishRoute::OfficialApi"),
        "begin_publish must route the official platform without a browser",
    );
    for reached in ["current_douyin_profile", "resolve_embedded_browser"] {
        let at = body
            .find(reached)
            .unwrap_or_else(|| panic!("begin_publish no longer reaches {reached}"));
        assert!(
            routed < at,
            "begin_publish reaches {reached} before it has routed the platform",
        );
    }
}

#[test]
fn the_bridge_is_handed_an_artifact_identity_and_resolves_the_path_itself() {
    let body = bridge_function_body("begin_publish");

    // Only the parameter list is the page's to fill; everything after it is the
    // bridge talking to itself.
    let parameters = &body[..body.find(") -> ").expect("a command signature")];

    assert!(
        parameters.contains("artifact_id: uuid::Uuid"),
        "begin_publish must take a registered artifact identity",
    );
    assert!(
        !parameters.contains("artifact_path"),
        "a page that names a local path can name any local file",
    );
    assert!(
        !body.contains("PathBuf::from("),
        "a path built from page input is a path the page chose",
    );
    assert!(
        body.contains("stage_publishable_artifact"),
        "the publishable file is resolved by the store, not by the page",
    );
    // The publish job identity is the bridge's to mint; a page that supplies it
    // is a page that can make two publishes share one identity.
    assert!(!parameters.contains("publish_job_id"));
    assert!(body.contains("generate_uuid_v4"));
}

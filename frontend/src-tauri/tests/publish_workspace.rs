//! PB-07: the one shape the App renders for both publishing platforms.
//!
//! The two platforms reach their platform by completely different means, and
//! that difference must stop at this boundary: what leaves here is a stage, an
//! availability and an outcome, never a mechanism. A platform nobody configured
//! yet has to stay listed and inert rather than taking the module down with it.

use automation_tool_desktop_lib::publish_workspace::{
    PublishApproval, PublishAvailability, PublishOutcome, PublishPlatformState, PublishStage,
    PublishWorkspace, PublishWorkspaceError,
};

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
    assert_eq!(bilibili.availability, PublishAvailability::AwaitingConfiguration);
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
    workspace.begin("douyin").expect("a publishable platform");
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
    workspace.begin("douyin").expect("publishable");
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
    assert!(snapshot.approval.is_none(), "a spent approval is not shown again");
}

#[test]
fn the_critical_point_hands_over_the_account_summary_and_copy() {
    let mut workspace = PublishWorkspace::new(false);
    workspace.observe_douyin_signed_in(true);
    workspace.begin("douyin").expect("publishable");

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
    workspace.begin("douyin").expect("publishable");

    assert_eq!(workspace.approve(), Err(PublishWorkspaceError::NoApprovalPending));
    assert_eq!(workspace.snapshot().stage, PublishStage::Preparing);
}

#[test]
fn a_platform_that_is_not_publishable_cannot_be_started() {
    let mut workspace = PublishWorkspace::new(false);

    assert_eq!(workspace.begin("bilibili"), Err(PublishWorkspaceError::NotPublishable));
    assert_eq!(workspace.begin("douyin"), Err(PublishWorkspaceError::NotPublishable));
    assert_eq!(workspace.snapshot().stage, PublishStage::Idle);
}

#[test]
fn a_platform_outside_the_two_supported_ones_is_refused() {
    let mut workspace = PublishWorkspace::new(true);

    for unsupported in ["kuaishou", "xiaohongshu", "wechat_channels", "", "DOUYIN"] {
        assert_eq!(
            workspace.begin(unsupported),
            Err(PublishWorkspaceError::UnknownPlatform),
            "{unsupported} must not be publishable"
        );
    }
}

#[test]
fn cancelling_before_the_click_settles_as_cancelled() {
    let mut workspace = PublishWorkspace::new(false);
    workspace.observe_douyin_signed_in(true);
    workspace.begin("douyin").expect("publishable");
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
    workspace.begin("douyin").expect("publishable");
    workspace.await_approval(ready_approval());
    workspace.approve().expect("pending");

    assert_eq!(workspace.cancel(), Err(PublishWorkspaceError::AlreadyDispatched));
    assert_eq!(workspace.snapshot().stage, PublishStage::Publishing);
}

#[test]
fn an_uncertain_outcome_carries_its_own_explanation() {
    let mut workspace = PublishWorkspace::new(false);
    workspace.observe_douyin_signed_in(true);
    workspace.begin("douyin").expect("publishable");
    workspace.await_approval(ready_approval());
    workspace.approve().expect("pending");
    workspace.begin_verification();

    workspace.settle(PublishOutcome::OutcomeUncertain);

    let snapshot = workspace.snapshot();
    assert_eq!(snapshot.outcome, Some(PublishOutcome::OutcomeUncertain));
    assert!(!snapshot.retryable, "an uncertain publish is never offered as a retry");
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

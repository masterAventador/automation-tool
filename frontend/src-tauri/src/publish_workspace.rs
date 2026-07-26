//! PB-07: the one shape the App renders for both publishing platforms.
//!
//! B站 publishes through an official interface and 抖音 through a visible
//! operations browser. That difference decides what the executor does; it must
//! never decide what the App renders, or the publishing page becomes two pages
//! wearing one name. So this module projects a stage, an availability and an
//! outcome, and deliberately carries no mechanism at all.
//!
//! A platform nobody has configured yet stays listed as `awaiting_configuration`
//! rather than disappearing or failing the module: the operator must still be
//! able to publish to the platform that *is* ready.
//!
//! The vocabulary is pinned by `contracts/publishing/publish-workspace.v1.json`
//! and checked against the Zod schema by
//! `frontend/tests/publish-workspace-contract.test.mjs`.

use serde::Serialize;

/// The two platforms `contracts/quality/publishing-capabilities.v1.json` enables.
const PUBLISHABLE_PLATFORMS: [PublishPlatform; 2] =
    [PublishPlatform::Bilibili, PublishPlatform::Douyin];

const MAX_APPROVAL_FIELD_CHARACTERS: usize = 256;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub enum PublishPlatform {
    #[serde(rename = "bilibili")]
    Bilibili,
    #[serde(rename = "douyin")]
    Douyin,
}

impl PublishPlatform {
    /// Recognize only the two enabled platforms, and only in canonical form.
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "bilibili" => Some(Self::Bilibili),
            "douyin" => Some(Self::Douyin),
            _ => None,
        }
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Bilibili => "bilibili",
            Self::Douyin => "douyin",
        }
    }

    /// How this platform is actually reached.
    ///
    /// Deliberately exhaustive and deliberately a property of the platform.
    /// Availability used to be the only thing the platform argument decided;
    /// everything after it went to the operations browser and the 抖音 profile
    /// unconditionally. Configuring the official platform's credentials would
    /// therefore have published a B站 post through 抖音's browser, on 抖音's
    /// account. A new platform now has to say here how it is reached, and the
    /// compiler asks.
    pub const fn route(self) -> PublishRoute {
        match self {
            Self::Bilibili => PublishRoute::NotIntegrated,
            Self::Douyin => PublishRoute::OperationsBrowser,
        }
    }
}

/// The distinct ways a publish can be carried out, from the bridge's side.
///
/// This is not projected to the App — the page still renders one shape for both
/// platforms. It exists so that "which platform" and "which machinery" cannot
/// drift apart inside the bridge.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PublishRoute {
    /// The visible operations browser, driven by the local executor.
    OperationsBrowser,
    /// Reachable in principle, not connected to the App in this build.
    ///
    /// B站's official publishing exists on the server side and has no route out
    /// of the App yet. Saying so is the honest answer; borrowing another
    /// platform's route is not.
    NotIntegrated,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub enum PublishAvailability {
    #[serde(rename = "ready")]
    Ready,
    #[serde(rename = "awaiting_configuration")]
    AwaitingConfiguration,
    #[serde(rename = "awaiting_sign_in")]
    AwaitingSignIn,
    #[serde(rename = "unavailable")]
    Unavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub enum PublishStage {
    #[serde(rename = "idle")]
    Idle,
    #[serde(rename = "preparing")]
    Preparing,
    #[serde(rename = "awaiting_approval")]
    AwaitingApproval,
    #[serde(rename = "publishing")]
    Publishing,
    #[serde(rename = "verifying")]
    Verifying,
    #[serde(rename = "settled")]
    Settled,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub enum PublishOutcome {
    #[serde(rename = "published")]
    Published,
    #[serde(rename = "outcome_uncertain")]
    OutcomeUncertain,
    #[serde(rename = "not_published")]
    NotPublished,
    #[serde(rename = "handed_off")]
    HandedOff,
    #[serde(rename = "cancelled")]
    Cancelled,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PublishWorkspaceError {
    UnknownPlatform,
    NotPublishable,
    UnreadableApproval,
    NoApprovalPending,
    AlreadyDispatched,
    NothingInFlight,
}

/// What the operator is shown at the publish critical point, and nothing else.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PublishApproval {
    pub target_account: String,
    pub video_summary: String,
    pub title: String,
    pub description: String,
    pub confirmation_id: String,
}

impl PublishApproval {
    /// Build one presentable approval, or refuse it before it reaches the App.
    ///
    /// Every field here is page- or operator-supplied text on its way to a
    /// human decision, so an empty, over-long or control-bearing value is
    /// rejected rather than rendered: an approval nobody can read is not an
    /// approval anyone can give.
    pub fn new(
        target_account: &str,
        video_summary: &str,
        title: &str,
        description: &str,
        confirmation_id: &str,
    ) -> Result<Self, PublishWorkspaceError> {
        let fields = [target_account, video_summary, title, description];
        if fields.iter().any(|field| !readable(field)) || !canonical_uuid_v4(confirmation_id) {
            return Err(PublishWorkspaceError::UnreadableApproval);
        }
        Ok(Self {
            target_account: target_account.to_owned(),
            video_summary: video_summary.to_owned(),
            title: title.to_owned(),
            description: description.to_owned(),
            confirmation_id: confirmation_id.to_owned(),
        })
    }
}

/// One recorded step of a publish, in the order it happened.
///
/// This is a record of what was decided, not of what was published: the title
/// and body are content, and copying them into a growing trail only widens
/// where they live. The confirmation identity is kept because it is what ties
/// an approval to the click it authorized.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PublishAuditEntry {
    pub step: String,
    pub platform: PublishPlatform,
    pub confirmation_id: Option<String>,
    pub outcome: Option<PublishOutcome>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PublishPlatformState {
    pub platform: PublishPlatform,
    pub availability: PublishAvailability,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PublishWorkspaceSnapshot {
    pub platforms: Vec<PublishPlatformState>,
    pub stage: PublishStage,
    pub target: Option<PublishPlatform>,
    pub approval: Option<PublishApproval>,
    pub outcome: Option<PublishOutcome>,
    pub retryable: bool,
    pub audit: Vec<PublishAuditEntry>,
}

impl PublishWorkspaceSnapshot {
    pub fn platform(&self, platform: &str) -> Option<&PublishPlatformState> {
        let wanted = PublishPlatform::parse(platform)?;
        self.platforms.iter().find(|state| state.platform == wanted)
    }
}

/// One operator's view of publishing: what is possible, and where one job is.
#[derive(Clone, Debug)]
pub struct PublishWorkspace {
    official_credentials_configured: bool,
    operations_browser_signed_in: bool,
    stage: PublishStage,
    target: Option<PublishPlatform>,
    job_id: Option<String>,
    approval: Option<PublishApproval>,
    outcome: Option<PublishOutcome>,
    audit: Vec<PublishAuditEntry>,
}

impl PublishWorkspace {
    pub fn new(official_credentials_configured: bool) -> Self {
        Self {
            official_credentials_configured,
            operations_browser_signed_in: false,
            stage: PublishStage::Idle,
            target: None,
            job_id: None,
            approval: None,
            outcome: None,
            audit: Vec::new(),
        }
    }

    pub fn observe_douyin_signed_in(&mut self, signed_in: bool) {
        self.operations_browser_signed_in = signed_in;
    }

    /// The identity of the publish currently in flight, if there is one.
    ///
    /// The page used to send the confirmation identity back as the publish job
    /// identity because it had nothing else to send. Two different things
    /// wearing one identity cannot be told apart afterwards, so the bridge mints
    /// this when the publish begins and spends it without asking the page.
    pub fn job_id(&self) -> Option<&str> {
        self.job_id.as_deref()
    }

    pub fn snapshot(&self) -> PublishWorkspaceSnapshot {
        PublishWorkspaceSnapshot {
            platforms: PUBLISHABLE_PLATFORMS
                .iter()
                .map(|platform| PublishPlatformState {
                    platform: *platform,
                    availability: self.availability(*platform),
                })
                .collect(),
            stage: self.stage,
            target: self.target,
            approval: self.approval.clone(),
            outcome: self.outcome,
            retryable: self.retryable(),
            audit: self.audit.clone(),
        }
    }

    /// Reserve the workspace for one publish, and answer where it must go.
    ///
    /// Returning the platform rather than `()` is what lets the caller route on
    /// it: the parse already happened here, and re-deriving it upstream is how
    /// the routing decision got skipped in the first place.
    pub fn begin(
        &mut self,
        platform: &str,
        publish_job_id: String,
    ) -> Result<PublishPlatform, PublishWorkspaceError> {
        let platform =
            PublishPlatform::parse(platform).ok_or(PublishWorkspaceError::UnknownPlatform)?;
        if self.availability(platform) != PublishAvailability::Ready {
            return Err(PublishWorkspaceError::NotPublishable);
        }
        self.stage = PublishStage::Preparing;
        self.target = Some(platform);
        self.job_id = Some(publish_job_id);
        self.approval = None;
        self.outcome = None;
        // A new publish appends; it never erases what the last one did.
        self.record("publish_started", None, None);
        Ok(platform)
    }

    pub fn await_approval(&mut self, approval: PublishApproval) {
        self.record("approval_presented", Some(approval.confirmation_id.clone()), None);
        self.stage = PublishStage::AwaitingApproval;
        self.approval = Some(approval);
    }

    pub fn approve(&mut self) -> Result<PublishApproval, PublishWorkspaceError> {
        let approval = self
            .approval
            .take()
            .ok_or(PublishWorkspaceError::NoApprovalPending)?;
        self.stage = PublishStage::Publishing;
        self.record("approval_given", Some(approval.confirmation_id.clone()), None);
        Ok(approval)
    }

    pub fn begin_verification(&mut self) {
        self.stage = PublishStage::Verifying;
        self.record("verification_started", None, None);
    }

    pub fn settle(&mut self, outcome: PublishOutcome) {
        self.stage = PublishStage::Settled;
        self.approval = None;
        // Nothing is in flight any more, so there is no job left to spend an
        // approval against.
        self.job_id = None;
        self.outcome = Some(outcome);
        self.record("settled", None, Some(outcome));
    }

    /// Cancel a publish that has not been dispatched yet.
    ///
    /// Once the platform action is in flight there is nothing local left to
    /// cancel, and reporting one would tell the operator the post did not
    /// happen when it may well have.
    pub fn cancel(&mut self) -> Result<(), PublishWorkspaceError> {
        match self.stage {
            PublishStage::Idle | PublishStage::Settled => {
                Err(PublishWorkspaceError::NothingInFlight)
            }
            PublishStage::Publishing | PublishStage::Verifying => {
                Err(PublishWorkspaceError::AlreadyDispatched)
            }
            PublishStage::Preparing | PublishStage::AwaitingApproval => {
                self.settle(PublishOutcome::Cancelled);
                Ok(())
            }
        }
    }

    /// Append one step. A step with no target is a step that never happened.
    fn record(
        &mut self,
        step: &str,
        confirmation_id: Option<String>,
        outcome: Option<PublishOutcome>,
    ) {
        let Some(platform) = self.target else {
            return;
        };
        self.audit.push(PublishAuditEntry {
            step: step.to_owned(),
            platform,
            confirmation_id,
            outcome,
        });
    }

    fn availability(&self, platform: PublishPlatform) -> PublishAvailability {
        match platform {
            PublishPlatform::Bilibili if self.official_credentials_configured => {
                PublishAvailability::Ready
            }
            PublishPlatform::Bilibili => PublishAvailability::AwaitingConfiguration,
            PublishPlatform::Douyin if self.operations_browser_signed_in => {
                PublishAvailability::Ready
            }
            PublishPlatform::Douyin => PublishAvailability::AwaitingSignIn,
        }
    }

    /// An attempted publish whose result is unknown is never offered as a retry.
    fn retryable(&self) -> bool {
        matches!(
            self.outcome,
            Some(PublishOutcome::NotPublished) | Some(PublishOutcome::Cancelled)
        )
    }
}

/// What a preflight result means for the workspace.
///
/// `None` is not "nothing happened": a *ready* preflight is the reason to ask
/// the operator, so it belongs to `await_approval` rather than to an outcome.
/// Every other answer, including one this build does not recognize, ends the
/// attempt — nothing was clicked, so calling it "not published" is honest.
pub fn preflight_outcome(state: &str) -> Option<PublishOutcome> {
    match state {
        "publish_pre_submit_ready" => None,
        "publish_handoff_required" => Some(PublishOutcome::HandedOff),
        _ => Some(PublishOutcome::NotPublished),
    }
}

/// What a dispatch result means for the workspace.
///
/// The click may already have happened by the time an answer arrives, so an
/// answer this build cannot read is *uncertain*, never a clean failure: telling
/// the operator "did not publish" about a post that exists is the one mistake
/// this whole chain is built to avoid.
pub fn dispatch_outcome(state: &str) -> PublishOutcome {
    match state {
        "publish_verified" => PublishOutcome::Published,
        "publish_not_dispatched" => PublishOutcome::NotPublished,
        _ => PublishOutcome::OutcomeUncertain,
    }
}

fn readable(value: &str) -> bool {
    !value.trim().is_empty()
        && value.chars().count() <= MAX_APPROVAL_FIELD_CHARACTERS
        && !value
            .chars()
            .any(|character| character.is_control() || matches!(character, '\u{202a}'..='\u{202e}' | '\u{2066}'..='\u{2069}'))
}

fn canonical_uuid_v4(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() != 36 {
        return false;
    }
    bytes.iter().enumerate().all(|(index, byte)| match index {
        8 | 13 | 18 | 23 => *byte == b'-',
        14 => *byte == b'4',
        19 => matches!(byte, b'8' | b'9' | b'a' | b'b'),
        _ => byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'),
    })
}

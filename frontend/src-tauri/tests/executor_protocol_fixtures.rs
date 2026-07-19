use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use automation_tool_desktop_lib::executor_protocol::{
    parse_executor_message, ExecutorEnvelopeKind,
};

const VALID_FIXTURES: [&str; 7] = [
    "executor-heartbeat.json",
    "executor-hello.json",
    "microsecond-deadline.json",
    "platform-session-health.json",
    "step-progress.json",
    "task-accept.json",
    "task-offer.json",
];
const INVALID_FIXTURES: [&str; 26] = [
    "deadline-before-send.json",
    "deadline-before-send-microsecond.json",
    "deadline-equals-send.json",
    "duplicate-key.json",
    "inline-data-uri.json",
    "invalid-idempotency-key.json",
    "invalid-message-id.json",
    "invalid-sequence-type.json",
    "invalid-sequence-zero.json",
    "invalid-version.json",
    "lifecycle-with-task-scope.json",
    "missing-protocol-version.json",
    "naive-sent-at.json",
    "negative-zero-offset.json",
    "non-finite-number.json",
    "non-utc-sent-at.json",
    "payload-too-deep.json",
    "payload-too-many-fields.json",
    "platform-session-health-with-task-scope.json",
    "private-path.json",
    "sensitive-assignment.json",
    "sensitive-cookie-field.json",
    "task-missing-attempt.json",
    "unknown-envelope-field.json",
    "unknown-message-type.json",
    "unsafe-sequence.json",
];

fn fixture_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../contracts/fixtures/executor-v1")
}

fn fixture_names(root: &Path) -> BTreeSet<String> {
    fs::read_dir(root)
        .expect("fixture directory must be readable")
        .map(|entry| {
            entry
                .expect("fixture entry must be readable")
                .file_name()
                .into_string()
                .expect("fixture file name must be UTF-8")
        })
        .filter(|name| name.ends_with(".json"))
        .collect()
}

#[test]
fn rust_inventory_matches_the_exact_shared_fixture_set() {
    let root = fixture_root();

    assert_eq!(
        fixture_names(&root.join("valid")),
        VALID_FIXTURES.into_iter().map(str::to_owned).collect()
    );
    assert_eq!(
        fixture_names(&root.join("invalid")),
        INVALID_FIXTURES.into_iter().map(str::to_owned).collect()
    );
}

#[test]
fn rust_formal_parser_accepts_every_shared_valid_fixture() {
    let root = fixture_root().join("valid");
    let mut kinds = BTreeSet::new();

    for fixture_name in VALID_FIXTURES {
        let raw = fs::read_to_string(root.join(fixture_name)).expect("fixture must be readable");
        let parsed = parse_executor_message(&raw).expect("valid fixture must parse");
        assert_eq!(parsed.protocol_version(), "1.0");
        assert!(!parsed.message_type().is_empty());
        kinds.insert(parsed.kind());
    }

    assert_eq!(
        kinds,
        BTreeSet::from([
            ExecutorEnvelopeKind::Lifecycle,
            ExecutorEnvelopeKind::PlatformSessionHealth,
            ExecutorEnvelopeKind::TaskCommand,
            ExecutorEnvelopeKind::TaskCommandResult,
            ExecutorEnvelopeKind::TaskEvent,
        ])
    );
}

#[test]
fn rust_formal_parser_rejects_every_shared_invalid_fixture_with_one_safe_error() {
    let root = fixture_root().join("invalid");

    for fixture_name in INVALID_FIXTURES {
        let raw = fs::read_to_string(root.join(fixture_name)).expect("fixture must be readable");
        let error = parse_executor_message(&raw).expect_err("invalid fixture must fail");

        assert_eq!(error.to_string(), "Invalid Executor protocol message");
        assert_eq!(format!("{error:?}"), "ExecutorProtocolError");
        assert!(!error.to_string().contains("fixture-private-value"));
    }
}

use std::collections::HashSet;
use std::error::Error;
use std::fmt::{self, Debug, Display, Formatter};

use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{Map, Value};
use time::format_description::well_known::Rfc3339;
use time::{OffsetDateTime, UtcOffset};
use uuid::Variant;

const EXECUTOR_PROTOCOL_VERSION: &str = "1.0";
const MAX_MESSAGE_BYTES: usize = 32 * 1024;
const MAX_PAYLOAD_BYTES: usize = 16 * 1024;
const MAX_PAYLOAD_DEPTH: usize = 8;
const MAX_COLLECTION_ITEMS: usize = 64;
const MAX_STRING_LENGTH: usize = 4096;
const MAX_SEQUENCE: u64 = (1_u64 << 53) - 1;

const SENSITIVE_PAYLOAD_NAMES: [&str; 25] = [
    "access_token",
    "api_key",
    "authorization",
    "captcha_code",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "file_path",
    "image",
    "image_data",
    "inline_image",
    "inline_screenshot",
    "local_path",
    "otp",
    "password",
    "private_key",
    "refresh_token",
    "screenshot",
    "secret",
    "secrets",
    "session_cookie",
    "token",
    "tokens",
    "verification_code",
];
const SENSITIVE_PAYLOAD_SEGMENTS: [&str; 9] = [
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "password",
    "secret",
    "secrets",
    "token",
    "tokens",
];

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ExecutorEnvelopeKind {
    Lifecycle,
    TaskCommand,
    TaskCommandResult,
    TaskEvent,
}

#[derive(Debug)]
pub struct ExecutorEnvelope {
    raw: RawExecutorEnvelope,
    kind: ExecutorEnvelopeKind,
}

impl ExecutorEnvelope {
    #[must_use]
    pub fn protocol_version(&self) -> &str {
        &self.raw.protocol_version
    }

    #[must_use]
    pub fn message_type(&self) -> &str {
        &self.raw.message_type
    }

    #[must_use]
    pub const fn kind(&self) -> ExecutorEnvelopeKind {
        self.kind
    }
}

pub struct ExecutorProtocolError;

impl Debug for ExecutorProtocolError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str("ExecutorProtocolError")
    }
}

impl Display for ExecutorProtocolError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str("Invalid Executor protocol message")
    }
}

impl Error for ExecutorProtocolError {}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RawExecutorEnvelope {
    protocol_version: String,
    message_id: String,
    message_type: String,
    sent_at: String,
    deadline_at: String,
    installation_id: String,
    executor_id: String,
    correlation_id: String,
    idempotency_key: String,
    sequence: u64,
    payload: UniqueJsonValue,
    task_id: Option<String>,
    execution_attempt_id: Option<String>,
}

#[derive(Debug)]
struct UniqueJsonValue(Value);

impl<'de> Deserialize<'de> for UniqueJsonValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_any(UniqueJsonVisitor)
    }
}

struct UniqueJsonVisitor;

impl<'de> Visitor<'de> for UniqueJsonVisitor {
    type Value = UniqueJsonValue;

    fn expecting(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str("a JSON value without duplicate object keys")
    }

    fn visit_unit<E>(self) -> Result<Self::Value, E> {
        Ok(UniqueJsonValue(Value::Null))
    }

    fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E> {
        Ok(UniqueJsonValue(Value::Bool(value)))
    }

    fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E> {
        Ok(UniqueJsonValue(Value::Number(value.into())))
    }

    fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E> {
        Ok(UniqueJsonValue(Value::Number(value.into())))
    }

    fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        serde_json::Number::from_f64(value)
            .map(Value::Number)
            .map(UniqueJsonValue)
            .ok_or_else(|| E::custom("non-finite JSON number"))
    }

    fn visit_str<E>(self, value: &str) -> Result<Self::Value, E> {
        Ok(UniqueJsonValue(Value::String(value.to_owned())))
    }

    fn visit_string<E>(self, value: String) -> Result<Self::Value, E> {
        Ok(UniqueJsonValue(Value::String(value)))
    }

    fn visit_none<E>(self) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        self.visit_unit()
    }

    fn visit_some<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: Deserializer<'de>,
    {
        UniqueJsonValue::deserialize(deserializer)
    }

    fn visit_seq<A>(self, mut sequence: A) -> Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let mut values = Vec::new();
        while let Some(value) = sequence.next_element::<UniqueJsonValue>()? {
            values.push(value.0);
        }
        Ok(UniqueJsonValue(Value::Array(values)))
    }

    fn visit_map<A>(self, mut object: A) -> Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut keys = HashSet::new();
        let mut values = Map::new();
        while let Some((key, value)) = object.next_entry::<String, UniqueJsonValue>()? {
            if !keys.insert(key.clone()) {
                return Err(de::Error::custom("duplicate JSON key"));
            }
            values.insert(key, value.0);
        }
        Ok(UniqueJsonValue(Value::Object(values)))
    }
}

pub fn parse_executor_message(source: &str) -> Result<ExecutorEnvelope, ExecutorProtocolError> {
    parse_executor_message_inner(source).map_err(|()| ExecutorProtocolError)
}

fn parse_executor_message_inner(source: &str) -> Result<ExecutorEnvelope, ()> {
    if source.len() > MAX_MESSAGE_BYTES {
        return Err(());
    }
    let raw: RawExecutorEnvelope = serde_json::from_str(source).map_err(|_| ())?;
    let kind = message_kind(&raw.message_type).ok_or(())?;

    if raw.protocol_version != EXECUTOR_PROTOCOL_VERSION
        || !is_canonical_uuid_v4(&raw.message_id)
        || !is_canonical_uuid_v4(&raw.installation_id)
        || !is_canonical_uuid_v4(&raw.executor_id)
        || !is_canonical_uuid_v4(&raw.correlation_id)
        || !is_idempotency_key(&raw.idempotency_key)
        || !(1..=MAX_SEQUENCE).contains(&raw.sequence)
    {
        return Err(());
    }

    let sent_at = parse_canonical_utc_timestamp(&raw.sent_at).ok_or(())?;
    let deadline_at = parse_canonical_utc_timestamp(&raw.deadline_at).ok_or(())?;
    if deadline_at <= sent_at {
        return Err(());
    }

    match kind {
        ExecutorEnvelopeKind::Lifecycle => {
            if raw.task_id.is_some() || raw.execution_attempt_id.is_some() {
                return Err(());
            }
        }
        ExecutorEnvelopeKind::TaskCommand
        | ExecutorEnvelopeKind::TaskCommandResult
        | ExecutorEnvelopeKind::TaskEvent => {
            if !raw.task_id.as_deref().is_some_and(is_canonical_uuid_v4)
                || !raw
                    .execution_attempt_id
                    .as_deref()
                    .is_some_and(is_canonical_uuid_v4)
            {
                return Err(());
            }
        }
    }

    validate_payload(&raw.payload.0)?;
    Ok(ExecutorEnvelope { raw, kind })
}

fn message_kind(message_type: &str) -> Option<ExecutorEnvelopeKind> {
    match message_type {
        "executor.hello" | "executor.heartbeat" => Some(ExecutorEnvelopeKind::Lifecycle),
        "task.offer" | "task.pause" | "task.resume" | "task.cancel" | "task.emergency_stop" => {
            Some(ExecutorEnvelopeKind::TaskCommand)
        }
        "task.accept" | "task.reject" | "task.control_ack" => {
            Some(ExecutorEnvelopeKind::TaskCommandResult)
        }
        "task.started"
        | "step.started"
        | "step.progress"
        | "step.completed"
        | "step.failed"
        | "session.login_required"
        | "handoff.requested"
        | "task.paused"
        | "task.resumed"
        | "task.cancelled"
        | "task.completed"
        | "task.partially_completed"
        | "task.failed"
        | "task.outcome_uncertain" => Some(ExecutorEnvelopeKind::TaskEvent),
        _ => None,
    }
}

fn is_canonical_uuid_v4(value: &str) -> bool {
    uuid::Uuid::parse_str(value).is_ok_and(|parsed| {
        parsed.get_version_num() == 4
            && parsed.get_variant() == Variant::RFC4122
            && parsed.hyphenated().to_string() == value
    })
}

fn parse_canonical_utc_timestamp(value: &str) -> Option<OffsetDateTime> {
    if value.len() < 20
        || value.len() > 32
        || value.starts_with("0000-")
        || !(value.ends_with('Z') || value.ends_with("+00:00"))
    {
        return None;
    }
    let fraction_length = value.split_once('.').map_or(0, |(_, suffix)| {
        suffix.split(['Z', '+']).next().map_or(0, str::len)
    });
    if fraction_length > 6 {
        return None;
    }
    let parsed = OffsetDateTime::parse(value, &Rfc3339).ok()?;
    (parsed.offset() == UtcOffset::UTC).then_some(parsed)
}

fn is_idempotency_key(value: &str) -> bool {
    let bytes = value.as_bytes();
    (1..=128).contains(&bytes.len())
        && bytes.first().is_some_and(u8::is_ascii_alphanumeric)
        && bytes.iter().all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'/' | b'-')
        })
}

fn validate_payload(payload: &Value) -> Result<(), ()> {
    if !payload.is_object()
        || serde_json::to_vec(payload).map_err(|_| ())?.len() > MAX_PAYLOAD_BYTES
    {
        return Err(());
    }
    validate_payload_value(payload, 0)
}

fn validate_payload_value(value: &Value, depth: usize) -> Result<(), ()> {
    if depth > MAX_PAYLOAD_DEPTH {
        return Err(());
    }
    match value {
        Value::Object(object) => {
            if object.len() > MAX_COLLECTION_ITEMS {
                return Err(());
            }
            for (key, child) in object {
                if unsafe_payload_key(key) {
                    return Err(());
                }
                validate_payload_value(child, depth + 1)?;
            }
        }
        Value::Array(values) => {
            if values.len() > MAX_COLLECTION_ITEMS {
                return Err(());
            }
            for child in values {
                validate_payload_value(child, depth + 1)?;
            }
        }
        Value::String(value) if unsafe_payload_string(value) => return Err(()),
        Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_) => {}
    }
    Ok(())
}

fn unsafe_payload_key(key: &str) -> bool {
    let normalized = normalize_payload_name(key);
    key.is_empty()
        || key.chars().count() > 128
        || contains_control_or_bidi(key)
        || SENSITIVE_PAYLOAD_NAMES.contains(&normalized.as_str())
        || normalized
            .split('_')
            .any(|segment| SENSITIVE_PAYLOAD_SEGMENTS.contains(&segment))
}

fn normalize_payload_name(value: &str) -> String {
    let mut normalized = String::with_capacity(value.len());
    let mut previous_is_lower_or_digit = false;
    for character in value.chars() {
        if character.is_ascii_uppercase() && previous_is_lower_or_digit {
            normalized.push('_');
        }
        if matches!(character, '.' | '-') {
            normalized.push('_');
        } else {
            normalized.extend(character.to_lowercase());
        }
        previous_is_lower_or_digit = character.is_ascii_lowercase() || character.is_ascii_digit();
    }
    normalized
}

fn contains_control_or_bidi(value: &str) -> bool {
    value.chars().any(|character| {
        let point = u32::from(character);
        point < 0x20
            || point == 0x7f
            || (0x202a..=0x202e).contains(&point)
            || (0x2066..=0x2069).contains(&point)
    })
}

fn unsafe_payload_string(value: &str) -> bool {
    let folded = value.to_lowercase();
    value.chars().count() > MAX_STRING_LENGTH
        || contains_control_or_bidi(value)
        || folded.contains("bearer ")
        || folded.contains("file://")
        || contains_sensitive_assignment(&folded)
        || contains_inline_data_uri(&folded)
        || contains_private_posix_path(&folded)
        || contains_windows_absolute_path(&folded)
}

fn contains_sensitive_assignment(value: &str) -> bool {
    const NAMES: [&str; 21] = [
        "access_token",
        "access-token",
        "accesstoken",
        "api_key",
        "api-key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "password",
        "private_key",
        "private-key",
        "privatekey",
        "refresh_token",
        "refresh-token",
        "refreshtoken",
        "secret",
        "session_cookie",
        "session-cookie",
        "sessioncookie",
        "token",
    ];
    NAMES.iter().any(|name| {
        value.match_indices(name).any(|(index, matched)| {
            let prefix_is_safe = index == 0
                || !value.as_bytes()[index - 1].is_ascii_alphanumeric()
                    && value.as_bytes()[index - 1] != b'_';
            let suffix = value[index + matched.len()..].trim_start();
            prefix_is_safe && matches!(suffix.as_bytes().first(), Some(b':' | b'='))
        })
    })
}

fn contains_inline_data_uri(value: &str) -> bool {
    value.match_indices("data:").any(|(index, _)| {
        let boundary = index == 0
            || !value.as_bytes()[index - 1].is_ascii_alphanumeric()
                && value.as_bytes()[index - 1] != b'_';
        let suffix = &value[index + 5..];
        boundary
            && suffix.split_once(',').is_some_and(|(metadata, _)| {
                let Some((media_type, subtype_and_parameters)) = metadata.split_once('/') else {
                    return false;
                };
                let subtype = subtype_and_parameters
                    .split_once(';')
                    .map_or(subtype_and_parameters, |(value, _)| value);
                !media_type.is_empty()
                    && !subtype.is_empty()
                    && media_type.bytes().all(is_data_uri_media_character)
                    && subtype.bytes().all(is_data_uri_media_character)
            })
    })
}

fn is_data_uri_media_character(character: u8) -> bool {
    character.is_ascii_alphanumeric() || matches!(character, b'.' | b'+' | b'-')
}

fn contains_private_posix_path(value: &str) -> bool {
    const PREFIXES: [&str; 5] = ["/users", "/home", "/root", "/tmp", "/var/folders"];
    PREFIXES.iter().any(|prefix| {
        value.match_indices(prefix).any(|(index, matched)| {
            let boundary = index == 0
                || matches!(
                    value.as_bytes()[index - 1],
                    b' ' | b'\t' | b'\n' | b'\r' | b'"' | b'\'' | b'='
                );
            let end = index + matched.len();
            boundary && (end == value.len() || value.as_bytes()[end] == b'/')
        })
    })
}

fn contains_windows_absolute_path(value: &str) -> bool {
    value
        .as_bytes()
        .windows(3)
        .enumerate()
        .any(|(index, window)| {
            let boundary = index == 0
                || matches!(
                    value.as_bytes()[index - 1],
                    b' ' | b'\t' | b'\n' | b'\r' | b'"' | b'\'' | b'='
                );
            boundary
                && window[0].is_ascii_alphabetic()
                && window[1] == b':'
                && matches!(window[2], b'/' | b'\\')
        })
}

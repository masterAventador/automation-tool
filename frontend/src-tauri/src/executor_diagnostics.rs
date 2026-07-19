//! Bounded, fail-closed retention for Local Executor stderr diagnostics.

use regex::Regex;
use std::collections::VecDeque;
use std::sync::{Mutex, OnceLock};

pub(crate) const MAX_RETAINED_DIAGNOSTIC_LINES: usize = 200;
pub(crate) const MAX_RETAINED_DIAGNOSTIC_BYTES: usize = 64 * 1024;
pub(crate) const MAX_DIAGNOSTIC_LINE_BYTES: usize = 4096;

const REDACTED: &str = "[REDACTED]";
const TRUNCATED: &str = "[TRUNCATED]";
const SECRET_KEYS: &str = concat!(
    r"access[_-]?token|refresh[_-]?token|local[_-]?session[_-]?token|",
    r"control[_-]?plane[_-]?session|session[_-]?token|api[_-]?key|",
    r"private[_-]?key|authorization|credential|password|secret|cookie|",
    r"session[_-]?cookie|token|sid_tt|sessionid(?:_ss)?|web_session|",
    r"passport_csrf_token|a1",
);
const COLON_SECRET_KEYS: &str = concat!(
    r"access[_-]?token|refresh[_-]?token|local[_-]?session[_-]?token|",
    r"control[_-]?plane[_-]?session|session[_-]?token|private[_-]?key|",
    r"credential|password|secret|session[_-]?cookie|token|sid_tt|",
    r"sessionid(?:_ss)?|web_session|passport_csrf_token|a1",
);

#[derive(Default)]
pub(crate) struct ExecutorDiagnostics {
    retained: Mutex<RetainedDiagnostics>,
}

#[derive(Default)]
struct RetainedDiagnostics {
    lines: VecDeque<String>,
    total_bytes: usize,
}

impl ExecutorDiagnostics {
    pub(crate) fn retain_raw_line(&self, raw: &[u8], truncated: bool) {
        let raw = raw.strip_suffix(b"\r").unwrap_or(raw);
        let safe = if truncated {
            TRUNCATED.to_owned()
        } else {
            match std::str::from_utf8(raw) {
                Ok(value) => {
                    truncate_utf8(&redact_diagnostic_line(value), MAX_DIAGNOSTIC_LINE_BYTES)
                }
                Err(_) => REDACTED.to_owned(),
            }
        };
        if safe.is_empty() {
            return;
        }
        if let Ok(mut retained) = self.retained.lock() {
            retained.total_bytes += safe.len();
            retained.lines.push_back(safe);
            while retained.lines.len() > MAX_RETAINED_DIAGNOSTIC_LINES
                || retained.total_bytes > MAX_RETAINED_DIAGNOSTIC_BYTES
            {
                if let Some(removed) = retained.lines.pop_front() {
                    retained.total_bytes = retained.total_bytes.saturating_sub(removed.len());
                } else {
                    retained.total_bytes = 0;
                    break;
                }
            }
        }
    }

    pub(crate) fn snapshot(&self) -> Result<Vec<String>, ()> {
        self.retained
            .lock()
            .map(|retained| retained.lines.iter().cloned().collect())
            .map_err(|_| ())
    }
}

pub(crate) fn redact_diagnostic_line(value: &str) -> String {
    let patterns = diagnostic_patterns();
    let mut safe = value
        .chars()
        .map(|character| {
            if unsafe_character(character) {
                ' '
            } else {
                character
            }
        })
        .collect::<String>();
    safe = patterns
        .json_secret
        .replace_all(&safe, r#""${1}":"[REDACTED]""#)
        .into_owned();
    safe = patterns
        .url_userinfo
        .replace_all(&safe, "${1}[REDACTED]@")
        .into_owned();
    safe = patterns
        .url_query
        .replace_all(&safe, "${1}?[REDACTED]")
        .into_owned();
    safe = patterns.file_url.replace_all(&safe, REDACTED).into_owned();
    safe = patterns
        .inline_data
        .replace_all(&safe, REDACTED)
        .into_owned();
    safe = patterns
        .credential_envelope
        .replace_all(&safe, REDACTED)
        .into_owned();
    safe = patterns
        .assignment
        .replace_all(&safe, "${1}=[REDACTED]")
        .into_owned();
    safe = patterns
        .colon_assignment
        .replace_all(&safe, "${1}${2}=[REDACTED]")
        .into_owned();
    safe = patterns
        .bearer
        .replace_all(&safe, "Bearer [REDACTED]")
        .into_owned();
    safe = patterns.raw_hex.replace_all(&safe, REDACTED).into_owned();
    safe = patterns
        .private_posix_path
        .replace_all(&safe, REDACTED)
        .into_owned();
    safe = patterns
        .private_windows_path
        .replace_all(&safe, REDACTED)
        .into_owned();
    patterns
        .header
        .replace_all(&safe, "${1}: [REDACTED]")
        .into_owned()
}

fn unsafe_character(character: char) -> bool {
    let codepoint = character as u32;
    codepoint <= 0x1f
        || codepoint == 0x7f
        || (0x202a..=0x202e).contains(&codepoint)
        || (0x2066..=0x2069).contains(&codepoint)
}

fn truncate_utf8(value: &str, maximum_bytes: usize) -> String {
    if value.len() <= maximum_bytes {
        return value.to_owned();
    }
    let mut end = maximum_bytes;
    while !value.is_char_boundary(end) {
        end -= 1;
    }
    value[..end].to_owned()
}

struct DiagnosticPatterns {
    header: Regex,
    json_secret: Regex,
    assignment: Regex,
    colon_assignment: Regex,
    bearer: Regex,
    credential_envelope: Regex,
    raw_hex: Regex,
    url_userinfo: Regex,
    url_query: Regex,
    file_url: Regex,
    inline_data: Regex,
    private_posix_path: Regex,
    private_windows_path: Regex,
}

fn diagnostic_patterns() -> &'static DiagnosticPatterns {
    static PATTERNS: OnceLock<DiagnosticPatterns> = OnceLock::new();
    PATTERNS.get_or_init(|| DiagnosticPatterns {
        header: regex(
            r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key):\s*[^\r\n]+",
        ),
        json_secret: regex(&format!(
            r#"(?i)["']({SECRET_KEYS})["']\s*:\s*(?:"[^"]*"|'[^']*'|[^,\s}}\]]+)"#
        )),
        assignment: regex(&format!(
            r#"(?i)\b({SECRET_KEYS})\s*=\s*(?:"[^"]*"|'[^']*'|[^\s,;}}\]]+)"#
        )),
        colon_assignment: regex(&format!(
            r#"(?i)(^|[\s,{{])({COLON_SECRET_KEYS})\s*:\s*(?:"[^"]*"|'[^']*'|[^\s,;}}\]]+)"#
        )),
        bearer: regex(r#"(?i)\bbearer\s+[^\s,;"']+"#),
        credential_envelope: regex(r"(?i)\bat(?:dc|ds|lep|ems)1\.[a-z0-9._~-]+"),
        raw_hex: regex(r"(?i)\b[0-9a-f]{64}\b"),
        url_userinfo: regex(r"(?i)\b((?:https?|wss?)://)[^/\s:@]+:[^@/\s]+@"),
        url_query: regex(r"(?i)\b((?:https?|wss?)://[^\s?#]+)\?[^\s#]*"),
        file_url: regex(r#"(?i)\bfile://[^\s"'<>]+"#),
        inline_data: regex(r"(?i)\bdata:[a-z0-9.+-]+/[a-z0-9.+-]+[^,\s]*,[^\s]+"),
        private_posix_path: regex(
            r#"(?i)(?:/private)?/(?:users|home|root|tmp|var/folders)(?:/[^\s"'<>]*)?"#,
        ),
        private_windows_path: regex(r#"(?i)\b[a-z]:[\\/][^\s"'<>]+"#),
    })
}

fn regex(pattern: &str) -> Regex {
    Regex::new(pattern).expect("fixed Executor diagnostic pattern")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Deserialize;

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct FixtureDocument {
        fixture_version: String,
        cases: Vec<FixtureCase>,
    }

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct FixtureCase {
        expected: String,
        input: String,
        name: String,
    }

    #[test]
    fn rust_redactor_matches_every_shared_fixture() {
        let document: FixtureDocument = serde_json::from_str(include_str!(
            "../../../contracts/fixtures/executor-diagnostics-v1.json"
        ))
        .expect("strict diagnostic fixture");
        assert_eq!(document.fixture_version, "1");
        assert!(document.cases.len() >= 14);
        for case in document.cases {
            assert_eq!(
                redact_diagnostic_line(&case.input),
                case.expected,
                "{}",
                case.name
            );
        }
    }

    #[test]
    fn retention_bounds_lines_bytes_and_invalid_or_truncated_input() {
        let diagnostics = ExecutorDiagnostics::default();
        for index in 0..250 {
            diagnostics.retain_raw_line(format!("line-{index}").as_bytes(), false);
        }
        assert_eq!(diagnostics.snapshot().expect("short snapshot").len(), 200);

        for _ in 0..100 {
            diagnostics.retain_raw_line(&vec![b'x'; 1000], false);
        }
        let bounded = diagnostics.snapshot().expect("bounded snapshot");
        assert!(bounded.len() <= MAX_RETAINED_DIAGNOSTIC_LINES);
        assert!(bounded
            .iter()
            .all(|line| line.len() <= MAX_DIAGNOSTIC_LINE_BYTES));
        assert!(bounded.iter().map(String::len).sum::<usize>() <= MAX_RETAINED_DIAGNOSTIC_BYTES);

        diagnostics.retain_raw_line(b"private partial token", true);
        diagnostics.retain_raw_line(&[0xff], false);
        let final_snapshot = diagnostics.snapshot().expect("final snapshot");
        assert_eq!(final_snapshot[final_snapshot.len() - 2], TRUNCATED);
        assert_eq!(final_snapshot.last().map(String::as_str), Some(REDACTED));
    }
}

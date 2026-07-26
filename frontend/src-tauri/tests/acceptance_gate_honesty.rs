//! Guards that an acceptance test cannot report success without running.
//!
//! This guard exists because a whole family of acceptance tests was reporting
//! `ok` while executing nothing. Each one opened with
//!
//! ```ignore
//! let Some(browser) = std::env::var_os("BM04_RENDER_BROWSER").map(PathBuf::from) else {
//!     return;
//! };
//! ```
//!
//! so a run without the acceptance environment fell straight out of the body
//! and the harness printed `test result: ok. 1 passed; 0 failed` in 0.00s. The
//! drivers decide whether their test ran by looking for exactly that string,
//! so an empty body satisfied the gate verbatim — including a case named
//! `real_worker_render_sandbox_isolates_malicious_html`, whose whole purpose is
//! to prove the render sandbox contains hostile HTML.
//!
//! The honest shape is `#[ignore = "…"]` plus `.expect(…)`: the ordinary suite
//! does not select the test, and a driver that does select it without supplying
//! the environment panics instead of printing a green line. Both halves are
//! required, so this file checks both:
//!
//! 1. no `#[test]` skips itself when its environment is missing, and
//! 2. every driver that names an `#[ignore]`d test also selects ignored tests.
//!
//! Half a fix is worse than none: marking a test `#[ignore]` without teaching
//! its driver to select it converts the acceptance run into a silent no-op.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

/// Silent skips this task could not fix, each with the task that owns it.
///
/// An entry is only admissible when the Rust side and its driver cannot be
/// changed and verified together here. VE-04 signs a request against the real
/// Aliyun ICE gateway, so proving the pair still passes needs credentials this
/// surface does not hold; fixing the test without proving the driver still
/// selects it would trade a silent skip for a silent no-op.
const UNFIXED_SILENT_SKIPS: &[(&str, &str)] = &[
    (
        "video_editing_service_settings_real.rs",
        "real_gateway_accepts_production_signature",
    ),
    (
        "video_editing_service_settings_real.rs",
        "real_gateway_rejects_tampered_secret_with_sanitized_error",
    ),
];

/// libtest flags that make an `#[ignore]`d test actually run.
const IGNORED_SELECTORS: &[&str] = &["--ignored", "--include-ignored"];

/// A `#[test]` that returns early because its acceptance environment is absent.
#[derive(Debug, PartialEq, Eq, PartialOrd, Ord)]
struct SilentSkip {
    file: String,
    function: String,
    line: usize,
}

fn tests_directory() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests")
}

fn scripts_directory() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../scripts")
}

fn files_with_extension(directory: &Path, extension: &str) -> Vec<PathBuf> {
    let mut files = fs::read_dir(directory)
        .unwrap_or_else(|error| panic!("read {}: {error}", directory.display()))
        .map(|entry| entry.expect("directory entry").path())
        .filter(|path| path.extension().is_some_and(|value| value == extension))
        .collect::<Vec<_>>();
    files.sort();
    files
}

/// Integration test sources, excluding this guard: its own prose quotes the
/// very shape it forbids.
fn test_sources() -> Vec<PathBuf> {
    let own = Path::new(file!())
        .file_name()
        .expect("guard file name")
        .to_owned();
    files_with_extension(&tests_directory(), "rs")
        .into_iter()
        .filter(|path| path.file_name() != Some(own.as_os_str()))
        .collect()
}

fn file_name(path: &Path) -> String {
    path.file_name()
        .expect("file name")
        .to_string_lossy()
        .into_owned()
}

/// The `fn` declaration line at or above `line`.
fn enclosing_function(lines: &[&str], line: usize) -> Option<(usize, String)> {
    let start = lines[..=line]
        .iter()
        .rposition(|text| text.trim_start().starts_with("fn "))?;
    let name = lines[start]
        .trim_start()
        .strip_prefix("fn ")?
        .split('(')
        .next()?
        .to_owned();
    Some((start, name))
}

/// Whether the attributes directly above `declaration` include `#[test]`.
fn is_test_case(lines: &[&str], declaration: usize) -> bool {
    lines[..declaration]
        .iter()
        .rev()
        .take_while(|text| {
            let trimmed = text.trim_start();
            trimmed.starts_with("#[") || trimmed.starts_with("///") || trimmed.starts_with("//")
        })
        .any(|text| text.trim() == "#[test]")
}

/// Finds `let … else { … return; }` divergences inside a `#[test]`.
///
/// The environment read is deliberately *not* required to appear in the binding
/// itself: VE-04 hides its `std::env::var` behind a `load_real_credentials()`
/// helper, and an indirection must not buy a test the right to skip silently.
/// Any bail-out that leaves a case reporting `ok` without asserting anything is
/// the defect, whatever the condition reads.
///
/// `} else {` from an ordinary `if` is excluded, and so is a `return` that ends
/// work already done — a helper-mode re-entry returns *because* its variable is
/// present, having run the helper first.
fn silent_skips(path: &Path) -> Vec<SilentSkip> {
    let source = fs::read_to_string(path).unwrap_or_else(|error| panic!("read source: {error}"));
    let lines = source.lines().collect::<Vec<_>>();
    let mut skips = Vec::new();

    for (index, line) in lines.iter().enumerate() {
        let trimmed = line.trim();
        if !trimmed.ends_with("else {") || trimmed.starts_with('}') {
            continue;
        }
        let Some((declaration, function)) = enclosing_function(&lines, index) else {
            continue;
        };
        if !is_test_case(&lines, declaration) {
            continue;
        }
        // The `else {` must belong to a `let … else` opened inside this
        // function, not to an `if` whose closing brace shares the line.
        if !lines[declaration..=index]
            .iter()
            .any(|text| text.trim_start().starts_with("let "))
        {
            continue;
        }
        let mut depth = 0_i32;
        for (offset, body) in lines[index..].iter().enumerate() {
            depth += body.matches('{').count() as i32 - body.matches('}').count() as i32;
            if body.trim() == "return;" {
                skips.push(SilentSkip {
                    file: file_name(path),
                    function: function.clone(),
                    line: index + offset + 1,
                });
                break;
            }
            if depth <= 0 {
                break;
            }
        }
    }
    skips
}

/// Names of `#[test]` functions carrying `#[ignore]`, in declaration order.
fn ignored_tests(path: &Path) -> Vec<String> {
    let source = fs::read_to_string(path).unwrap_or_else(|error| panic!("read source: {error}"));
    let lines = source.lines().collect::<Vec<_>>();
    let mut names = Vec::new();
    for (index, line) in lines.iter().enumerate() {
        if !line.trim_start().starts_with("#[ignore") {
            continue;
        }
        if let Some(name) = lines[index..]
            .iter()
            .find_map(|text| text.trim_start().strip_prefix("fn "))
            .and_then(|rest| rest.split('(').next())
        {
            names.push(name.to_owned());
        }
    }
    names
}

#[test]
fn no_acceptance_test_reports_success_when_its_environment_is_missing() {
    let allowed = UNFIXED_SILENT_SKIPS
        .iter()
        .map(|(file, function)| format!("{file}::{function}"))
        .collect::<BTreeSet<_>>();
    let mut found = BTreeSet::new();
    let mut offenders = Vec::new();

    for path in test_sources() {
        for skip in silent_skips(&path) {
            let key = format!("{}::{}", skip.file, skip.function);
            if allowed.contains(&key) {
                found.insert(key);
                continue;
            }
            offenders.push(format!("{}:{} {key}", skip.file, skip.line));
        }
    }

    assert!(
        offenders.is_empty(),
        "these tests return green without running when their environment is absent; \
         mark them `#[ignore = \"…\"]` and `.expect(…)` the variable instead:\n  {}",
        offenders.join("\n  "),
    );
    let stale = allowed.difference(&found).cloned().collect::<Vec<_>>();
    assert!(
        stale.is_empty(),
        "UNFIXED_SILENT_SKIPS lists entries that no longer skip silently; delete them:\n  {}",
        stale.join("\n  "),
    );
}

#[test]
fn every_driver_naming_an_ignored_test_selects_ignored_tests() {
    let mut owners: BTreeMap<String, String> = BTreeMap::new();
    for path in test_sources() {
        for name in ignored_tests(&path) {
            owners.insert(name, file_name(&path));
        }
    }
    assert!(
        !owners.is_empty(),
        "no `#[ignore]`d acceptance test was found; the scanner is broken"
    );

    let mut offenders = Vec::new();
    for script in files_with_extension(&scripts_directory(), "py") {
        let source = fs::read_to_string(&script).unwrap_or_else(|error| panic!("read: {error}"));
        // Only a script that runs cargo can select a case; a module that merely
        // shares the name as a constant decides nothing.
        if !source.contains("\"cargo\"") {
            continue;
        }
        if IGNORED_SELECTORS
            .iter()
            .any(|selector| source.contains(selector))
        {
            continue;
        }
        for (name, owner) in &owners {
            if source.contains(name.as_str()) {
                offenders.push(format!(
                    "{} names {owner}::{name} but passes no {}",
                    file_name(&script),
                    IGNORED_SELECTORS.join(" / "),
                ));
            }
        }
    }
    assert!(
        offenders.is_empty(),
        "these drivers select an ignored test that libtest will refuse to run, so the \
         acceptance run silently executes nothing:\n  {}",
        offenders.join("\n  "),
    );
}

//! Keeps commands that wait on the world off the UI thread.
//!
//! `#[tauri::command]` on a function that is not `async` compiles to
//! `ExecutionContext::Blocking` — that is the default in `tauri-macros-2.6.3`
//! (`src/command/wrapper.rs`, the initialiser at line 50) and `asyncness` is
//! the only thing that changes it (same file, line 158). A blocking command is
//! invoked from `webview.on_message` inside the IPC protocol callback
//! (`tauri-2.11.5/src/ipc/protocol.rs`), and on macOS that callback is
//! WKWebView's `webView:startURLSchemeTask:`, delivered on the main thread.
//!
//! So a non-async command owns the UI thread for its entire duration. While it
//! runs the window cannot repaint, no other command can be dispatched, and
//! nothing the user clicks — including a cancel button — is even delivered.
//! For a command that waits on a child process this is measured in minutes:
//! `submit_motion_video_brief` waits up to `MOTION_AUTHORING_DEADLINE`, ten
//! minutes, and shipped that way. A customer who pressed the button got a dead
//! window with no progress and no way out.
//!
//! An async command is handed to `respond_async_serialized`, which drives it on
//! the async runtime instead, so the callback returns immediately and the UI
//! thread stays free.
//!
//! This test reads the crate's own source, because the execution context is
//! decided at compile time from the declaration and from nothing else. Short
//! commands are deliberately left alone: moving a state read onto the runtime
//! buys nothing and costs a thread hand-off.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

/// Work that waits on something outside this process, with the reason each one
/// cannot be allowed to hold the UI thread. A command is judged by whether it
/// can reach one of these, not by a hand-kept list of command names, so routing
/// a new command through any of them is caught the day it is written.
const WAITS_ON_THE_WORLD: &[(&str, &str, &str)] = &[
    (
        "lib.rs",
        "run_motion_authoring",
        "starts the authoring child and waits up to MOTION_AUTHORING_DEADLINE for it to exit",
    ),
    (
        "lib.rs",
        "motion_runtime_paths",
        "verifies every byte of the packaged media toolchain against its manifest digests",
    ),
    (
        "lib.rs",
        "start_motion_render",
        "starts the render worker process and waits for its health handshake",
    ),
    (
        "executor_platform.rs",
        "verified_entrypoint",
        "verifies every byte of the installed Executor package against its signed manifest",
    ),
    (
        "video_editing_executor.rs",
        "run_video_editing_child",
        "starts the editing child and waits up to VIDEO_EDITING_CHILD_DEADLINE for it to exit",
    ),
    (
        "executor_platform.rs",
        "startup_environment_state",
        "runs the same whole-package verification behind the startup gate",
    ),
];

struct TauriCommand {
    name: String,
    is_async: bool,
}

struct SourceFunction {
    name: String,
    body: String,
}

fn source_directory() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("src")
}

fn read_source(file: &str) -> String {
    fs::read_to_string(source_directory().join(file))
        .unwrap_or_else(|_| panic!("{file} must be readable"))
}

/// Strip visibility and `const` so `function_name` sees the `fn` keyword.
fn declaration_after_modifiers(trimmed: &str) -> &str {
    let rest = trimmed
        .strip_prefix("pub(crate) ")
        .or_else(|| trimmed.strip_prefix("pub "))
        .unwrap_or(trimmed);
    rest.strip_prefix("const ").unwrap_or(rest)
}

fn function_name(trimmed: &str) -> Option<(&str, bool)> {
    let rest = declaration_after_modifiers(trimmed);
    let (rest, is_async) = match rest.strip_prefix("async ") {
        Some(rest) => (rest, true),
        None => (rest, false),
    };
    let rest = rest.strip_prefix("fn ")?;
    let end = rest
        .find(|character: char| !character.is_alphanumeric() && character != '_')
        .unwrap_or(rest.len());
    (end > 0).then(|| (&rest[..end], is_async))
}

fn function_body(lines: &[&str], start: usize) -> String {
    let mut depth = 0i32;
    let mut opened = false;
    let mut body = Vec::new();
    for line in &lines[start..] {
        body.push(*line);
        depth += line.matches('{').count() as i32;
        depth -= line.matches('}').count() as i32;
        if line.contains('{') {
            opened = true;
        }
        if opened && depth <= 0 {
            break;
        }
    }
    body.join("\n")
}

fn source_lines(content: &str) -> Vec<&str> {
    content
        .lines()
        .take_while(|line| !line.trim_start().starts_with("mod tests {"))
        .collect()
}

/// Every `fn` item in a file, with its body, so calls can be followed.
fn functions_in(file: &str) -> Vec<SourceFunction> {
    let content = read_source(file);
    let lines = source_lines(&content);
    let mut items = Vec::new();
    for (index, line) in lines.iter().enumerate() {
        if let Some((name, _)) = function_name(line.trim()) {
            items.push(SourceFunction {
                name: name.to_owned(),
                body: function_body(&lines, index),
            });
        }
    }
    items
}

/// Every `#[tauri::command]` in `lib.rs`, with the asyncness that decides its
/// execution context. `#[tauri::command(async)]` counts as async even on a
/// synchronous function, because the macro then picks the same context.
fn tauri_commands() -> Vec<TauriCommand> {
    let content = read_source("lib.rs");
    let lines = source_lines(&content);
    let mut commands = Vec::new();
    let mut pending: Vec<&str> = Vec::new();
    for line in lines.iter() {
        let trimmed = line.trim();
        if trimmed.starts_with("#[") {
            pending.push(trimmed);
            continue;
        }
        if trimmed.starts_with("//") || trimmed.is_empty() {
            continue;
        }
        if let Some((name, is_async)) = function_name(trimmed) {
            let attribute = pending
                .iter()
                .find(|attribute| attribute.starts_with("#[tauri::command"));
            if let Some(attribute) = attribute {
                commands.push(TauriCommand {
                    name: name.to_owned(),
                    is_async: is_async || attribute.contains("(async)"),
                });
            }
        }
        pending.clear();
    }
    assert!(
        commands.len() > 50,
        "the scanner recovered only {} commands, so its parsing is broken",
        commands.len()
    );
    commands
}

/// Names called from a body: any identifier immediately followed by `(`,
/// whether it is a free function or a method.
fn called_names(body: &str) -> BTreeSet<String> {
    let bytes = body.as_bytes();
    let mut names = BTreeSet::new();
    let mut start: Option<usize> = None;
    for (index, character) in body.char_indices() {
        let identifier = character.is_alphanumeric() || character == '_';
        if identifier {
            start.get_or_insert(index);
            continue;
        }
        if let Some(begin) = start.take() {
            if character == '(' {
                let name = &body[begin..index];
                let preceded_by_identifier = begin > 0
                    && (bytes[begin - 1].is_ascii_alphanumeric() || bytes[begin - 1] == b'_');
                if !preceded_by_identifier && !name.chars().next().is_some_and(char::is_numeric) {
                    names.insert(name.to_owned());
                }
            }
        }
    }
    names
}

/// Everything a command can reach without leaving `lib.rs`, plus the calls made
/// by each of those. Cross-module calls are not followed; the guarded work is
/// named directly in the list above, and every entry is reachable by name.
fn reachable_calls(entry: &str, functions: &BTreeMap<String, String>) -> BTreeSet<String> {
    let mut seen = BTreeSet::new();
    let mut reached = BTreeSet::new();
    let mut queue = vec![entry.to_owned()];
    while let Some(name) = queue.pop() {
        if !seen.insert(name.clone()) {
            continue;
        }
        let Some(body) = functions.get(&name) else {
            continue;
        };
        for called in called_names(body) {
            reached.insert(called.clone());
            if functions.contains_key(&called) {
                queue.push(called);
            }
        }
    }
    reached
}

#[test]
fn every_guarded_wait_still_exists_where_it_is_claimed_to_be() {
    for (file, name, reason) in WAITS_ON_THE_WORLD {
        let defined = functions_in(file)
            .into_iter()
            .any(|item| item.name == *name);
        assert!(
            defined,
            "{file}::{name} is guarded because it {reason}, but no such function exists there \
             any more. A rename silently empties this guard, so the rename must move the entry \
             with it rather than leave a gate that matches nothing."
        );
    }
}

#[test]
fn commands_that_wait_on_the_world_never_run_on_the_ui_thread() {
    let functions: BTreeMap<String, String> = functions_in("lib.rs")
        .into_iter()
        .map(|item| (item.name, item.body))
        .collect();
    let guarded: BTreeMap<&str, &str> = WAITS_ON_THE_WORLD
        .iter()
        .map(|(_, name, reason)| (*name, *reason))
        .collect();

    let mut offenders = Vec::new();
    for command in tauri_commands() {
        if command.is_async {
            continue;
        }
        let reached = reachable_calls(&command.name, &functions);
        let waits: Vec<String> = reached
            .iter()
            .filter_map(|called| {
                guarded
                    .get(called.as_str())
                    .map(|reason| format!("{called} — it {reason}"))
            })
            .collect();
        if !waits.is_empty() {
            offenders.push(format!(
                "{}:\n      {}",
                command.name,
                waits.join("\n      ")
            ));
        }
    }

    assert!(
        offenders.is_empty(),
        "these commands are declared without `async`, so tauri runs them on the IPC callback \
         thread — the macOS main thread. The window is frozen for as long as they take, and \
         each of them waits on something outside this process:\n    {}",
        offenders.join("\n    ")
    );
}

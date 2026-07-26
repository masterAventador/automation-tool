//! Guards that every Tauri command error reaches JavaScript with a readable code.
//!
//! `invoke()` rejects with the JSON serialization of the command's error type, and
//! JavaScript reads a rejection as `error.message` first and `String(error)` second.
//! A plain JSON object answers neither, so an error type that simply
//! `#[derive(Serialize)]`s its fields arrives as `[object Object]`: the desktop E2E
//! runner printed exactly that for four acceptance drivers, and the browser console
//! and any uncaught rejection print the same.
//!
//! One `#[derive(Serialize)]` on one new command error type is enough to reopen the
//! hole for that command, and nothing else in the build would notice — the code is
//! still on the wire, just unreadable to every JavaScript consumer. So the rule is
//! checked against the crate's own source: a command error type must route its
//! serialization through `command_error::serialize`, which is the single place that
//! decides what the boundary looks like.
//!
//! This scanner cannot prove the message is correct — `src/command_error.rs` and the
//! `lib.rs` unit tests do that against real serialized values. It proves no command
//! error type escapes that decision.

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

/// The shared entry point every command error type must delegate to.
const SHARED_SERIALIZER: &str = "command_error::serialize";

/// Error positions that carry no code and therefore nothing to make readable.
const CODELESS_ERROR_TYPES: &[&str] = &["()"];

fn source_directory() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("src")
}

fn source_files() -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = fs::read_dir(source_directory())
        .expect("the crate source directory must be readable")
        .map(|entry| entry.expect("source entries must be readable").path())
        .filter(|path| path.extension().is_some_and(|extension| extension == "rs"))
        .collect();
    files.sort();
    files
}

fn sources() -> String {
    source_files()
        .iter()
        .map(|path| fs::read_to_string(path).expect("source files must be readable"))
        .collect::<Vec<_>>()
        .join("\n")
}

/// Split a generic argument list on its top-level commas.
fn split_top_level(inner: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut depth = 0usize;
    let mut current = String::new();
    for character in inner.chars() {
        match character {
            '<' | '(' | '[' => depth += 1,
            '>' | ')' | ']' => depth = depth.saturating_sub(1),
            _ => {}
        }
        if character == ',' && depth == 0 {
            parts.push(current.trim().to_owned());
            current = String::new();
        } else {
            current.push(character);
        }
    }
    let last = current.trim();
    if !last.is_empty() {
        parts.push(last.to_owned());
    }
    parts
}

/// The error type name of every `#[tauri::command]`, without its module path.
fn command_error_types() -> BTreeSet<String> {
    let mut names = BTreeSet::new();
    for path in source_files() {
        let content = fs::read_to_string(&path).expect("source files must be readable");
        let lines: Vec<&str> = content.lines().collect();
        for (index, line) in lines.iter().enumerate() {
            if !line.trim().starts_with("#[tauri::command]") {
                continue;
            }
            let mut signature = String::new();
            for next in lines.iter().skip(index + 1).take(60) {
                signature.push_str(next.trim());
                signature.push(' ');
                if next.trim_end().ends_with('{') {
                    break;
                }
            }
            let Some(start) = signature.rfind("-> Result<") else {
                continue;
            };
            let after = &signature[start + "-> Result<".len()..];
            let Some(end) = after.rfind(">") else {
                continue;
            };
            let arguments = split_top_level(&after[..end]);
            if arguments.len() < 2 {
                continue;
            }
            let error = arguments
                .last()
                .expect("a two-argument Result has a last argument")
                .rsplit("::")
                .next()
                .expect("splitting a type path always yields a final segment")
                .trim()
                .to_owned();
            if CODELESS_ERROR_TYPES.contains(&error.as_str()) {
                continue;
            }
            names.insert(error);
        }
    }
    names
}

/// The `#[derive(...)]` list attached to `struct <name>`, if the type derives anything.
fn derive_list(sources: &str, name: &str) -> Option<String> {
    let declaration = format!("struct {name} ");
    let position = sources.find(&declaration)?;
    let preceding = &sources[..position];
    let attribute_start = preceding.rfind("#[derive(")?;
    // Only an attribute that belongs to this declaration counts; anything with a
    // blank line between it and the struct belongs to an earlier item.
    let between = &preceding[attribute_start..];
    if between.contains("\n\n") {
        return None;
    }
    let list_start = attribute_start + "#[derive(".len();
    let list_end = preceding[list_start..].find(')')? + list_start;
    Some(preceding[list_start..list_end].to_owned())
}

/// The body of `impl ... Serialize for <name>`, if the type writes one by hand.
fn hand_written_serialize_body(sources: &str, name: &str) -> Option<String> {
    let position = sources.find(&format!("Serialize for {name} {{"))?;
    let after = &sources[position..];
    let end = after.find("\n}").unwrap_or(after.len());
    Some(after[..end].to_owned())
}

#[test]
fn the_scanner_finds_the_command_error_types_it_is_meant_to_check() {
    let names = command_error_types();
    assert!(
        names.len() >= 8,
        "the scanner recovered only {} command error types ({names:?}), which means its \
         parsing is broken rather than the crate being clean",
        names.len()
    );
}

#[test]
fn every_command_error_reaches_javascript_through_the_shared_serializer() {
    let sources = sources();
    let mut offenders = Vec::new();
    for name in command_error_types() {
        if derive_list(&sources, &name).is_some_and(|list| list.contains("Serialize")) {
            offenders.push(format!(
                "{name} derives Serialize, so it reaches JavaScript as a plain object and \
                 `error.message` is undefined"
            ));
            continue;
        }
        match hand_written_serialize_body(&sources, &name) {
            None => offenders.push(format!(
                "{name} is a command error type with no Serialize implementation to review"
            )),
            Some(body) if !body.contains(SHARED_SERIALIZER) => offenders.push(format!(
                "{name} serializes itself instead of delegating to {SHARED_SERIALIZER}, so the \
                 boundary shape is decided in two places"
            )),
            Some(_) => {}
        }
    }
    assert!(
        offenders.is_empty(),
        "a Tauri command error must carry a `message` JavaScript can read; route it through \
         {SHARED_SERIALIZER} instead of deriving Serialize:\n  {}",
        offenders.join("\n  ")
    );
}

//! Guards the single-build-path rule for the desktop crate.
//!
//! A Cargo feature may mount test drivers, change window visibility or point a
//! configuration value at an isolated instance. It may never change *where the
//! product looks* for a file, resource, process or executable, and it may never
//! answer a runtime probe with a hard-coded "ready".
//!
//! This guard exists because both prohibitions were violated at once and no
//! acceptance run could see it: the `video-studio-e2e` build resolved the video
//! runtime from environment variables instead of the packaged resource
//! directory, and `check_local_startup_environment` returned all three startup
//! states as `Ready` without probing anything. The startup gate is the only
//! thing that stops a user whose install is missing a runtime resource, so a
//! build that short-circuits it is structurally blind to a missing package —
//! which is exactly how a release shipped with no ffmpeg and no video Workers
//! while every acceptance suite stayed green.
//!
//! These tests read the crate's own source. They cannot prove a build is
//! honest in general; they pin the two shapes that caused the incident plus the
//! two shapes that would let it recur, and force any new feature fork through a
//! reviewed allowlist.

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

/// Substrings that mean "this code declares the local environment usable".
/// None of them may appear inside code that only a test build compiles.
const READY_WITHOUT_PROBING: &[&str] = &[
    "AppDataStartupState::Ready",
    "ExecutorStartupState::Ready",
    "EmbeddedBrowserStartupState::Ready",
    "status: \"available\"",
];

/// Environment variables that used to relocate a runtime dependency for test
/// builds. They must not come back under any name in this crate.
const BANNED_RUNTIME_PATH_ENVIRONMENT: &[&str] = &[
    "AUTOMATION_TOOL_BM08_WORKER",
    "AUTOMATION_TOOL_BM08_BROWSER",
    "AUTOMATION_TOOL_BM08_FFMPEG",
    "AUTOMATION_TOOL_BM08_CHROMIUM_MAJOR",
    "AUTOMATION_TOOL_IM05_WORKER",
];

/// Functions that resolve a runtime dependency the App later launches or loads.
/// Every build must reach them through one code path, so they may contain no
/// `cfg(feature = ...)` branch and read no environment variable.
const RUNTIME_DEPENDENCY_RESOLVERS: &[(&str, &str)] = &[
    ("lib.rs", "motion_runtime_paths"),
    ("lib.rs", "motion_worker_launch"),
    ("material_video_studio.rs", "worker_executable"),
    ("material_video_studio.rs", "media_toolchain"),
    ("material_video_studio.rs", "material_worker_launch"),
];

/// Production functions allowed to carry an inline `cfg(feature = "*-e2e")`
/// branch, each with the reason it is not a product-behaviour fork.
const REVIEWED_INLINE_FEATURE_BRANCHES: &[(&str, &str)] = &[
    // Fixed diagnostics mirror the Executor commands that exist in each build;
    // the branches add no lookup, input or success path.
    ("app_logging.rs", "as_str"),
    // Composition root: mounts the WebDriver plugin and registers the command
    // set. Security rules forbid shipping the driver, so the mount itself is a
    // build difference; it changes no lookup the product performs.
    ("lib.rs", "run"),
    // Initialises a struct field that only exists in the builds that own an
    // executor connection; no lookup changes.
    ("control_plane.rs", "from_validated_origins"),
    ("executor_platform.rs", "initialize_with_paths"),
    // H8-13 redirects the diagnostic export destination to an isolated
    // directory. It is an output location, not a dependency lookup, so it
    // cannot hide a missing resource from the startup gate.
    ("lib.rs", "export_diagnostics"),
];

/// Function names compiled differently per feature, each with the reason the
/// difference is not a product-behaviour fork. A test-only definition that
/// reports success may never be added here.
const REVIEWED_FEATURE_FORKED_FUNCTIONS: &[(&str, &str)] = &[
    // Both variants return an origin that flows through the same
    // `validated_loopback_origin`; only the port value differs so acceptance
    // can address its isolated Control Plane.
    ("control_plane.rs", "configured_local_control_plane_origin"),
    // Additive fault injection for acceptance. Every variant is gated on the
    // same feature and differs only by operating system; they make the
    // executor fail, never succeed.
    ("executor_manager.rs", "inject_abnormal_process_exit"),
    ("executor_manager.rs", "suspend_process_for_acceptance"),
    // The UI-only desktop build carries no control-plane connection material,
    // so its variant refuses with an error. It never reports success.
    ("lib.rs", "restart_executor"),
    // The UI-only desktop build has an ephemeral identity and no production
    // credential vault, so its variant cannot run the installation-access
    // check. Both variants still perform the real `check_health` request.
    ("lib.rs", "check_control_plane_health"),
];

struct FunctionItem {
    file: String,
    name: String,
    predicate: String,
    body: String,
}

impl FunctionItem {
    /// True when the release build does not compile this item at all.
    fn test_only(&self) -> bool {
        !compiled_in_release(&self.predicate)
    }

    fn location(&self) -> String {
        format!("{}::{}", self.file, self.name)
    }
}

fn predicate_mentions_test_feature(predicate: &str) -> bool {
    predicate.contains("feature = ") && predicate.contains("e2e")
}

/// Evaluate a `#[cfg(...)]` attribute the way the release build sees it: every
/// Cargo feature is off, and every other atom (`unix`, `windows`, ...) is left
/// enabled so the answer depends only on features.
///
/// `not(feature = "desktop-e2e")` therefore marks production code, while
/// `feature = "desktop-e2e"` marks code no release ever compiles. Telling those
/// two apart is the whole point: the guard has to accuse the second and leave
/// the first alone.
fn compiled_in_release(predicate: &str) -> bool {
    predicate
        .split("#[")
        .filter_map(|attribute| attribute.trim().strip_prefix("cfg("))
        .all(|attribute| {
            let end = attribute
                .rfind(')')
                .expect("a cfg attribute must close its parenthesis");
            evaluate_cfg(attribute[..end].trim())
        })
}

fn evaluate_cfg(expression: &str) -> bool {
    let expression = expression.trim();
    for (keyword, combine) in [
        ("all(", true),
        ("any(", false),
        ("not(", false), // handled below; the flag is unused
    ] {
        let Some(inner) = expression.strip_prefix(keyword) else {
            continue;
        };
        let inner = inner
            .strip_suffix(')')
            .expect("a cfg combinator must close its parenthesis");
        if keyword == "not(" {
            return !evaluate_cfg(inner);
        }
        let mut operands = split_top_level(inner).into_iter().map(evaluate_cfg);
        return if combine {
            operands.all(|value| value)
        } else {
            operands.any(|value| value)
        };
    }
    // A bare atom: features are off in a release build, everything else is on.
    !expression.starts_with("feature = ")
}

fn split_top_level(inner: &str) -> Vec<&str> {
    let mut parts = Vec::new();
    let mut depth = 0usize;
    let mut start = 0usize;
    for (index, character) in inner.char_indices() {
        match character {
            '(' => depth += 1,
            ')' => depth -= 1,
            ',' if depth == 0 => {
                parts.push(inner[start..index].trim());
                start = index + 1;
            }
            _ => {}
        }
    }
    let last = inner[start..].trim();
    if !last.is_empty() {
        parts.push(last);
    }
    parts
}

fn source_directory() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("src")
}

fn source_files() -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = fs::read_dir(source_directory())
        .expect("the crate source directory must be readable")
        .map(|entry| {
            entry
                .expect("source directory entries must be readable")
                .path()
        })
        .filter(|path| path.extension().is_some_and(|extension| extension == "rs"))
        .collect();
    files.sort();
    assert!(
        files.len() > 10,
        "the scanner found {} source files, which means it is looking in the wrong place",
        files.len()
    );
    files
}

/// Recover every `fn` item outside the in-source `mod tests`, together with the
/// `#[cfg(...)]` attributes attached to it and its full body text.
fn functions() -> Vec<FunctionItem> {
    let mut items = Vec::new();
    for path in source_files() {
        let file = path
            .file_name()
            .expect("source files have names")
            .to_string_lossy()
            .into_owned();
        let content = fs::read_to_string(&path).expect("source files must be readable");
        let lines: Vec<&str> = content
            .lines()
            .take_while(|line| !line.trim_start().starts_with("mod tests {"))
            .collect();
        let mut pending: Vec<&str> = Vec::new();
        for (index, line) in lines.iter().enumerate() {
            let trimmed = line.trim();
            if trimmed.starts_with("#[") {
                pending.push(trimmed);
                continue;
            }
            if trimmed.starts_with("//") {
                continue;
            }
            if let Some(name) = function_name(trimmed) {
                let predicate = pending
                    .iter()
                    .filter(|attribute| attribute.contains("cfg("))
                    .copied()
                    .collect::<Vec<_>>()
                    .join(" ");
                items.push(FunctionItem {
                    file: file.clone(),
                    name: name.to_owned(),
                    predicate,
                    body: function_body(&lines, index),
                });
            }
            pending.clear();
        }
    }
    assert!(
        items.len() > 100,
        "the scanner recovered only {} functions, so its parsing is broken",
        items.len()
    );
    items
}

fn function_name(trimmed: &str) -> Option<&str> {
    let rest = trimmed
        .strip_prefix("pub(crate) ")
        .or_else(|| trimmed.strip_prefix("pub "))
        .unwrap_or(trimmed);
    let rest = rest.strip_prefix("const ").unwrap_or(rest);
    let rest = rest.strip_prefix("async ").unwrap_or(rest);
    let rest = rest.strip_prefix("fn ")?;
    let end = rest
        .find(|character: char| !character.is_alphanumeric() && character != '_')
        .unwrap_or(rest.len());
    (end > 0).then(|| &rest[..end])
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

fn reviewed(entries: &[(&str, &str)]) -> BTreeSet<String> {
    entries
        .iter()
        .map(|(file, name)| format!("{file}::{name}"))
        .collect()
}

#[test]
fn test_builds_never_declare_the_local_environment_ready_without_probing() {
    let mut offenders = Vec::new();
    for item in functions() {
        if !item.test_only() {
            continue;
        }
        for marker in READY_WITHOUT_PROBING {
            if item.body.contains(marker) {
                offenders.push(format!("{} declares {marker}", item.location()));
            }
        }
    }
    assert!(
        offenders.is_empty(),
        "a build feature may not answer a startup or health probe with a hard-coded \
         result; the product would then be blind to the missing resource the probe \
         exists to find:\n  {}",
        offenders.join("\n  ")
    );
}

#[test]
fn runtime_dependencies_resolve_from_the_package_in_every_build() {
    let items = functions();
    for (file, name) in RUNTIME_DEPENDENCY_RESOLVERS {
        let resolver = items
            .iter()
            .find(|item| item.file == *file && item.name == *name)
            .unwrap_or_else(|| panic!("{file}::{name} must exist to resolve a runtime dependency"));
        assert!(
            !predicate_mentions_test_feature(&resolver.predicate),
            "{file}::{name} is compiled per feature, so no build proves the packaged \
             layout the release depends on"
        );
        assert!(
            !resolver.body.contains("cfg(feature"),
            "{file}::{name} branches on a Cargo feature, so a test build resolves the \
             dependency from somewhere the release never looks"
        );
        assert!(
            !resolver.body.contains("env::var"),
            "{file}::{name} reads an environment variable, so a test build can supply a \
             dependency the packaged application does not carry"
        );
    }
}

#[test]
fn no_source_file_relocates_a_runtime_dependency_through_the_environment() {
    let mut offenders = Vec::new();
    for path in source_files() {
        let content = fs::read_to_string(&path).expect("source files must be readable");
        for variable in BANNED_RUNTIME_PATH_ENVIRONMENT {
            if content.contains(variable) {
                offenders.push(format!("{} reads {variable}", path.display()));
            }
        }
    }
    assert!(
        offenders.is_empty(),
        "these environment variables relocated the video runtime for test builds and \
         must not return; acceptance has to place real resources where the release \
         reads them instead:\n  {}",
        offenders.join("\n  ")
    );
}

#[test]
fn production_functions_carry_no_unreviewed_inline_feature_branch() {
    let found: BTreeSet<String> = functions()
        .iter()
        .filter(|item| !item.test_only())
        .filter(|item| {
            item.body
                .lines()
                .skip(1)
                .any(|line| line.contains("cfg(") && predicate_mentions_test_feature(line))
        })
        .map(FunctionItem::location)
        .collect();
    assert_eq!(
        found,
        reviewed(REVIEWED_INLINE_FEATURE_BRANCHES),
        "a production function gained or lost an inline test-feature branch; add it to \
         REVIEWED_INLINE_FEATURE_BRANCHES with the reason it changes no lookup, or \
         remove the branch"
    );
}

#[test]
fn every_feature_forked_function_is_reviewed() {
    let items = functions();
    let mut found = BTreeSet::new();
    for item in &items {
        let twins: Vec<&FunctionItem> = items
            .iter()
            .filter(|candidate| candidate.file == item.file && candidate.name == item.name)
            .collect();
        if twins.len() < 2 {
            continue;
        }
        if twins
            .iter()
            .any(|twin| predicate_mentions_test_feature(&twin.predicate))
        {
            found.insert(item.location());
        }
    }
    assert_eq!(
        found,
        reviewed(REVIEWED_FEATURE_FORKED_FUNCTIONS),
        "a function is compiled differently per test feature; add it to \
         REVIEWED_FEATURE_FORKED_FUNCTIONS with the reason the difference cannot report \
         success the release would not, or unify the definitions"
    );
}

#[test]
fn every_control_plane_health_variant_performs_the_real_request() {
    let variants: Vec<FunctionItem> = functions()
        .into_iter()
        .filter(|item| item.name == "check_control_plane_health")
        .collect();
    assert!(
        !variants.is_empty(),
        "check_control_plane_health must exist in every build"
    );
    for variant in &variants {
        assert!(
            variant.body.contains("check_health("),
            "a check_control_plane_health variant gated by {} never calls check_health, \
             so its build reports a Control Plane it has not contacted",
            variant.predicate
        );
    }
}

#[test]
fn the_startup_gate_is_compiled_identically_in_every_build() {
    let variants: Vec<FunctionItem> = functions()
        .into_iter()
        .filter(|item| item.name == "check_local_startup_environment")
        .collect();
    assert_eq!(
        variants.len(),
        1,
        "check_local_startup_environment has {} definitions; the startup gate is the \
         only thing that stops a user whose install is missing a resource, so every \
         build must compile the same one",
        variants.len()
    );
    let gate = &variants[0];
    assert!(
        gate.predicate.is_empty() || !predicate_mentions_test_feature(&gate.predicate),
        "the startup gate is gated by {}, so a test build compiles something else",
        gate.predicate
    );
    for probe in [
        "authority.resolve()",
        "profiles.revalidate_storage()",
        "platform.startup_environment_state()",
    ] {
        assert!(
            gate.body.contains(probe),
            "the startup gate no longer performs {probe}, so it can report ready without \
             checking that dependency"
        );
    }
}

#[test]
fn the_executor_package_root_comes_from_tauri_resources_in_every_build() {
    let items = functions();
    let run = items
        .iter()
        .find(|item| item.file == "lib.rs" && item.name == "run")
        .expect("lib.rs::run must compose the desktop application");
    assert!(
        !run.body.contains("cfg(debug_assertions)"),
        "lib.rs::run selects the Local Executor package root by build mode; \
         every build must exercise resource_dir()/local-executor/package"
    );
    assert!(
        run.body.contains("resource_dir()")
            && run.body.contains("join(\"local-executor\")")
            && run.body.contains("join(\"package\")")
            && run
                .body
                .contains("ExecutorPlatformService::initialize_with_package_root"),
        "lib.rs::run must derive the Local Executor package from Tauri's resource directory"
    );
    assert!(
        !run.body
            .contains("ExecutorPlatformService::initialize(&app_data_directory)"),
        "App data owns Executor state, not the signed package the App launches"
    );
}

#[test]
fn desktop_acceptance_stages_the_executor_at_the_same_resource_root() {
    let repository_root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let prerequisites =
        fs::read_to_string(repository_root.join("scripts/desktop_e2e_prerequisites.py"))
            .expect("desktop prerequisite source");
    let lifecycle = fs::read_to_string(repository_root.join("scripts/run_e4_14_acceptance.py"))
        .expect("Executor lifecycle acceptance source");
    assert!(
        prerequisites.contains("install_executor_package(")
            && prerequisites.contains("resource_root=resource_root"),
        "the shared startup preparation must stage the signed Executor in the \
         debug App resource root, not in App data"
    );
    assert!(
        lifecycle.contains("DEBUG_APP_RESOURCE_ROOT")
            && !lifecycle.contains("local_executor = private_app_data"),
        "custom Executor acceptance packages must use the same Tauri resource layout"
    );
}

/// Every `invoke_handler` list in `lib.rs`, with the `#[cfg(...)]` that selects it.
///
/// A command that is absent from a build does not fail that build, or that
/// build's tests: it fails at runtime, in the App, as an invoke error the
/// frontend has to interpret. That is why this is read from the source rather
/// than left to a compiler that has no opinion about it.
fn invoke_handlers() -> Vec<(String, BTreeSet<String>)> {
    let source = fs::read_to_string(source_directory().join("lib.rs"))
        .expect("the crate entry point must be readable");
    let lines: Vec<&str> = source.lines().collect();
    let mut handlers = Vec::new();
    for (index, line) in lines.iter().enumerate() {
        if !line.contains("generate_handler![") {
            continue;
        }
        let predicate = lines[index - 1].trim().to_owned();
        let mut commands = BTreeSet::new();
        for entry in lines[index + 1..].iter() {
            if entry.trim_start().starts_with(']') {
                break;
            }
            let name = entry.trim().trim_end_matches(',').trim();
            if !name.is_empty() {
                commands.insert(name.to_owned());
            }
        }
        handlers.push((predicate, commands));
    }
    assert!(
        handlers.len() >= 3,
        "found {} invoke_handler lists; the scanner is looking in the wrong place",
        handlers.len()
    );
    handlers
}

/// The commands the product-account gate can invoke, read from the one gateway
/// that owns them, so this list cannot drift from the code that calls them.
fn account_gate_commands() -> BTreeSet<String> {
    let gateway = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../src/platform/tauri/account-session-gateway.ts");
    let source =
        fs::read_to_string(&gateway).expect("the account session gateway must be readable");
    let mut commands = BTreeSet::new();
    for (index, _) in source.match_indices("safeInvoke(\"") {
        let rest = &source[index + "safeInvoke(\"".len()..];
        let end = rest.find('"').expect("an invoked command name is quoted");
        commands.insert(rest[..end].to_owned());
    }
    assert!(
        commands.len() >= 6,
        "found {} account commands in the gateway; the scanner is looking in the wrong place",
        commands.len()
    );
    commands
}

/// The one account command the App invokes before it can mount anything.
///
/// The rest of the gateway is reachable only from the login screen, which is
/// rendered only after this command answers that an account *is* required. That
/// asymmetry is why they are treated differently below: this one is on the path
/// to the workbench in every deployment, the others are not.
const ACCOUNT_MOUNT_PATH_COMMAND: &str = "restore_product_account_session";

/// The App cannot mount its workbench until the product-account gate answers,
/// and the gate answers by invoking a command. A build that does not register
/// that command cannot answer at all: the gateway reports the missing command
/// the same way it reports an unreachable account service, the gate settles on
/// "offline", and the workbench stays closed for a reason that has nothing to
/// do with accounts.
///
/// That is a test build behaving differently from the production build, which
/// is the thing this file exists to prevent — and it went unseen because the
/// guard above only checks that the startup gate *function* is compiled
/// identically, never that the commands the App must call are all reachable.
#[test]
fn the_account_gate_answer_is_reachable_in_every_build() {
    let gateway = account_gate_commands();
    assert!(
        gateway.contains(ACCOUNT_MOUNT_PATH_COMMAND),
        "the gateway no longer invokes {ACCOUNT_MOUNT_PATH_COMMAND}; this guard is \
         naming a command that no longer exists"
    );
    for (predicate, commands) in invoke_handlers() {
        assert!(
            commands.contains(ACCOUNT_MOUNT_PATH_COMMAND),
            "the handler selected by {predicate} does not register \
             {ACCOUNT_MOUNT_PATH_COMMAND}; the account gate invokes it before anything \
             is mounted, so in this build the gate cannot answer at all and the \
             workbench never opens"
        );
    }
}

/// The commands behind the login screen are registered together or not at all.
///
/// They need the production device identity and credential vault, which a plain
/// `desktop-e2e` build deliberately never creates — so a build may legitimately
/// carry none of them. What it may not do is carry some: a login screen whose
/// password form works and whose device list does not is a build-specific
/// behaviour that no test of either half would reveal.
#[test]
fn the_account_commands_behind_the_login_screen_are_all_or_nothing() {
    let behind_login: BTreeSet<String> = account_gate_commands()
        .into_iter()
        .filter(|command| command != ACCOUNT_MOUNT_PATH_COMMAND)
        .collect();
    for (predicate, commands) in invoke_handlers() {
        let present: BTreeSet<&String> = behind_login.intersection(&commands).collect();
        assert!(
            present.is_empty() || present.len() == behind_login.len(),
            "the handler selected by {predicate} registers {} of the {} commands behind \
             the login screen; a partially wired login screen fails only at the step the \
             user happens to reach",
            present.len(),
            behind_login.len()
        );
    }
}

// ---------------------------------------------------------------------------
// The other half of the startup gate: the frontend entry point
// ---------------------------------------------------------------------------
//
// Everything above reads Rust. But the startup gate has two halves, and only
// one of them is written in Rust: `check_local_startup_environment` answers,
// and a `StartupCheck` mounted by the frontend entry module decides whether to
// ask at all. Vite swaps that entry module per build mode
// (`vite.config.ts` rewrites `/src/main.tsx`), so a build can bypass the whole
// gate without a single `#[cfg]` — invisible to every guard in this file.
//
// That is not hypothetical. `app/startup.ts` still exports an isolated shell
// check whose entire body is `return { status: "ready" }`; the `desktop-e2e`
// entry once mounted it and thereby made its workbench assertion green by
// construction. The entry now delegates to `main.tsx`, while this scanner keeps
// that regression from returning in any Vite mode.

/// The module every release loads. Stubbing it can never be reviewed away: it
/// *is* the production startup gate.
const PRODUCTION_FRONTEND_ENTRYPOINT: &str = "main.tsx";

/// Where a startup check reached through an import is defined.
const FRONTEND_STARTUP_MODULE: &str = "app/startup.ts";

/// The answer that means "this install is usable".
const FRONTEND_READY_ANSWER: &str = "status: \"ready\"";

/// A `check()` body reports ready without probing when it can reach that answer
/// having awaited nothing.
///
/// "Awaits nothing *anywhere* in the body" is not enough, and this is not a
/// hypothetical refinement: the first version of this guard used it and a
/// mutation walked straight through. Inserting `return { status: "ready" };` at
/// the top of the real production check left every later `await` sitting in
/// unreachable code, so the body still looked like it probed. What matters is
/// whether a ready answer is reachable *before* the first await, so that is
/// what is measured.
fn reports_ready_without_probing(check_body: &str) -> bool {
    let Some(ready) = check_body.find(FRONTEND_READY_ANSWER) else {
        return false;
    };
    check_body
        .find("await ")
        .is_none_or(|awaited| ready < awaited)
}

/// Frontend entry modules that mount such a check, each with the reason it is
/// still tolerated and who owes its removal.
///
/// Asserted by set equality, so adding one *and* removing one both fail. The
/// remaining exception is not acceptable, but no second one can appear without
/// somebody writing down why.
const REVIEWED_STUBBED_FRONTEND_ENTRYPOINTS: &[(&str, &str)] = &[
    // Declares its own inline `readyStartup`. Serves B5-04 browser-settings,
    // whose user path (choosing a trusted system browser) was deleted by EB-10
    // per the product rule that forbids system-browser selection. The entry
    // dies with that acceptance rather than being repaired.
    (
        "test-browser-settings-main.tsx",
        "B5-04, pending retirement",
    ),
];

fn frontend_directory() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../src")
}

fn frontend_source(module: &str) -> String {
    let path = frontend_directory().join(module);
    fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("frontend module {module} must be readable: {error}"))
}

/// Every module Vite can install as the application entry, production included.
///
/// Read from `vite.config.ts` rather than listed here, so a new build mode
/// brings its entry into this guard automatically instead of silently opting
/// out of it.
fn frontend_entrypoints() -> BTreeSet<String> {
    let config =
        fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("../vite.config.ts"))
            .expect("the Vite configuration must be readable");
    let mut modules = BTreeSet::new();
    for (index, _) in config.match_indices("\"/src/") {
        let rest = &config[index + "\"/src/".len()..];
        let end = rest.find('"').expect("an entry module path is quoted");
        modules.insert(rest[..end].to_owned());
    }
    assert!(
        modules.contains(PRODUCTION_FRONTEND_ENTRYPOINT),
        "the Vite configuration never mentions {PRODUCTION_FRONTEND_ENTRYPOINT}; this \
         scanner is reading the wrong file"
    );
    assert!(
        modules.len() >= 5,
        "found {} frontend entry modules; the scanner is looking in the wrong place",
        modules.len()
    );
    modules
}

/// The identifier inside the first `startupCheck={...}` prop, if there is one.
fn mounted_binding(source: &str) -> Option<String> {
    let index = source.find("startupCheck={")?;
    let rest = &source[index + "startupCheck={".len()..];
    let end = rest.find('}')?;
    Some(rest[..end].trim().to_owned())
}

/// Text from `start` until its braces balance again.
fn balanced_block(source: &str, start: usize) -> String {
    let mut depth = 0i32;
    let mut opened = false;
    for (offset, character) in source[start..].char_indices() {
        match character {
            '{' => {
                depth += 1;
                opened = true;
            }
            '}' => depth -= 1,
            _ => {}
        }
        if opened && depth == 0 {
            return source[start..start + offset + 1].to_owned();
        }
    }
    panic!("a declaration starting at byte {start} never closes its braces");
}

/// The `check()` implementation an entry module actually mounts, following the
/// one level of indirection each entry really uses: a constant declared beside
/// it, a constant imported from the shared startup module, a factory called
/// there, or a delegation to the production entry.
///
/// Every unresolvable shape panics rather than returning "not a stub". A
/// scanner that quietly gives up would report exactly what a clean codebase
/// reports, which is the failure mode this whole file exists to refuse.
fn mounted_startup_check(module: &str) -> (String, String) {
    let source = frontend_source(module);
    let Some(name) = mounted_binding(&source) else {
        assert!(
            source.contains("\"./main\""),
            "{module} mounts no startup check and does not delegate to \
             {PRODUCTION_FRONTEND_ENTRYPOINT}, so this guard cannot tell what it starts"
        );
        return mounted_startup_check(PRODUCTION_FRONTEND_ENTRYPOINT);
    };

    let declaration = format!("const {name}");
    let (origin, owner) = if source.contains(&declaration) {
        (module.to_owned(), source)
    } else {
        assert!(
            source.contains("from \"./app/startup\""),
            "{module} mounts `{name}`, which it neither declares nor imports from \
             {FRONTEND_STARTUP_MODULE}; this guard cannot follow it"
        );
        (
            FRONTEND_STARTUP_MODULE.to_owned(),
            frontend_source(FRONTEND_STARTUP_MODULE),
        )
    };

    let start = owner
        .find(&declaration)
        .unwrap_or_else(|| panic!("{origin} declares no `{name}`"));
    let head_end = owner[start..]
        .find('\n')
        .map_or(owner.len(), |offset| start + offset);
    let head = &owner[start..head_end];

    // `const x = createSomething(` — the behaviour lives in the factory.
    if !head.contains('{') {
        let assigned = head
            .split_once('=')
            .map(|(_, rest)| rest.trim())
            .unwrap_or_else(|| {
                panic!("{origin} declares `{name}` in a shape this guard cannot read")
            });
        let factory = assigned
            .split_once('(')
            .map(|(callee, _)| callee.trim())
            .unwrap_or_else(|| {
                panic!("{origin} assigns `{name}` from a shape this guard cannot read")
            });
        let startup = frontend_source(FRONTEND_STARTUP_MODULE);
        let signature = format!("export function {factory}(");
        let factory_start = startup.find(&signature).unwrap_or_else(|| {
            panic!("{FRONTEND_STARTUP_MODULE} exports no `{factory}`, so `{name}` is unresolvable")
        });
        return (
            format!("{FRONTEND_STARTUP_MODULE}::{factory}"),
            balanced_block(&startup, factory_start),
        );
    }

    (format!("{origin}::{name}"), balanced_block(&owner, start))
}

/// No build may reach the workbench without the startup gate having run.
///
/// The Rust guards above pin one half: the gate function is compiled
/// identically everywhere and still performs its three probes. This pins the
/// other half — that the entry module which decides whether to *call* it is not
/// swapped for one that answers ready on its own. Both halves have to hold; the
/// incident that produced this file only needed one of them to fail.
#[test]
fn no_frontend_entrypoint_declares_the_environment_ready_without_probing() {
    let mut stubbed = BTreeSet::new();
    let mut probing = BTreeSet::new();
    for module in frontend_entrypoints() {
        let (origin, block) = mounted_startup_check(&module);
        let opening = block
            .find("check(")
            .and_then(|index| block[index..].find('{').map(|offset| index + offset));
        let opening = opening.unwrap_or_else(|| {
            panic!(
                "{module} resolves to {origin}, which declares no `check()`; this guard \
                 is reading the wrong thing and would pass on anything"
            )
        });
        if reports_ready_without_probing(&balanced_block(&block, opening)) {
            stubbed.insert(module);
        } else {
            probing.insert(module);
        }
    }

    // Checked before the vacuity guard below: when the production check is the
    // one that was stubbed, every entry that delegates to it becomes a stub
    // too, `probing` empties, and the vacuity message would report a broken
    // scanner for what is actually the single worst outcome this file covers.
    assert!(
        !stubbed.contains(PRODUCTION_FRONTEND_ENTRYPOINT),
        "{PRODUCTION_FRONTEND_ENTRYPOINT} mounts a startup check that can answer ready \
         before awaiting anything; that is the production gate, and no review can \
         excuse it"
    );
    // A resolver that silently returned empty bodies would report "no stubs"
    // and pass. It must be able to recognise a real check to be trusted about
    // a fake one.
    assert!(
        !probing.is_empty(),
        "no frontend entry resolved to a startup check that probes anything, so this \
         guard proved nothing"
    );

    let reviewed: BTreeSet<String> = REVIEWED_STUBBED_FRONTEND_ENTRYPOINTS
        .iter()
        .map(|(module, _)| (*module).to_owned())
        .collect();
    assert!(
        !reviewed.contains(PRODUCTION_FRONTEND_ENTRYPOINT),
        "{PRODUCTION_FRONTEND_ENTRYPOINT} is on the reviewed stub list; the production \
         entry is never eligible"
    );
    assert_eq!(
        stubbed, reviewed,
        "the set of frontend entries whose startup check reports ready without probing \
         changed. Such an entry cannot detect a missing packaged dependency, so every \
         acceptance run built on it is green by construction. Add one only with the \
         reason it exists and who retires it; remove one from the list when it is fixed."
    );
}

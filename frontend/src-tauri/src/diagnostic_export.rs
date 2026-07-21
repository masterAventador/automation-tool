//! Explicit, bounded export of already-redacted local diagnostic artifacts.

use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::fmt::{Display, Formatter};
#[cfg(test)]
use std::fs::File;
use std::fs::{self, OpenOptions};
#[cfg(test)]
use std::io::Read;
use std::io::Write;
use std::path::{Component, Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;
use uuid::{Uuid, Variant};
use zip::write::SimpleFileOptions;
#[cfg(test)]
use zip::ZipArchive;
use zip::{CompressionMethod, ZipWriter};

use crate::executor_diagnostics::{
    redact_diagnostic_line, MAX_DIAGNOSTIC_LINE_BYTES, MAX_RETAINED_DIAGNOSTIC_BYTES,
    MAX_RETAINED_DIAGNOSTIC_LINES,
};
use crate::executor_package::{ensure_no_symlink_ancestors, read_bounded_regular_file};

pub const DIAGNOSTIC_EXPORT_VERSION: &str = "1";
pub const MAX_DIAGNOSTIC_EXPORT_BYTES: u64 = 12 * 1024 * 1024;
const MAX_DIAGNOSTIC_EXPORT_ENTRIES: usize = 38;
const MAX_PAGE_DRIFT_BYTES: usize = 2_048;
const MAX_PAGE_DRIFT_ARTIFACTS: usize = 20;
const MAX_TRACE_BYTES: usize = 4 * 1024;
const MAX_TRACE_ARTIFACTS: usize = 8;
const MAX_SCREENSHOT_BYTES: usize = 1024 * 1024;
const MAX_SCREENSHOT_DIMENSION: u32 = 4_096;
const MAX_CROSS_RUNTIME_SEQUENCE: u64 = 9_007_199_254_740_991;
const PAGE_DRIFT_DIRECTORY: &str = "artifacts/evidence/page-drift";
const SCREENSHOT_DIRECTORY: &str = "artifacts/diagnostics/screenshots";
const TRACE_DIRECTORY: &str = "artifacts/diagnostics/traces";
const PAGE_DRIFT_MEDIA_TYPE: &str = "application/vnd.automation-tool.page-drift+json";
const TRACE_MEDIA_TYPE: &str = "application/vnd.automation-tool.browser-diagnostic-trace+json";
const PNG_SIGNATURE: &[u8] = b"\x89PNG\r\n\x1a\n";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DiagnosticExportErrorCode {
    StorageUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DiagnosticExportError {
    code: DiagnosticExportErrorCode,
}

impl DiagnosticExportError {
    const fn storage_unavailable() -> Self {
        Self {
            code: DiagnosticExportErrorCode::StorageUnavailable,
        }
    }

    pub const fn code(self) -> DiagnosticExportErrorCode {
        self.code
    }
}

impl Display for DiagnosticExportError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("diagnostic export is unavailable")
    }
}

impl Error for DiagnosticExportError {}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DiagnosticExportReceipt {
    file_name: String,
    entry_count: usize,
    total_bytes: u64,
}

#[derive(Clone)]
pub struct DiagnosticExportService {
    state_directory: PathBuf,
}

impl DiagnosticExportService {
    pub fn initialize(app_data_directory: &Path) -> Result<Self, DiagnosticExportError> {
        require_absolute_clean_path(app_data_directory)?;
        Ok(Self {
            state_directory: app_data_directory.join("local-executor").join("state"),
        })
    }

    pub fn export(
        &self,
        export_directory: &Path,
        diagnostic_lines: &[String],
    ) -> Result<DiagnosticExportReceipt, DiagnosticExportError> {
        self.export_inner(export_directory, diagnostic_lines)
            .map_err(|_| DiagnosticExportError::storage_unavailable())
    }

    fn export_inner(
        &self,
        export_directory: &Path,
        diagnostic_lines: &[String],
    ) -> Result<DiagnosticExportReceipt, ()> {
        require_absolute_clean_path(export_directory).map_err(|_| ())?;
        ensure_no_symlink_ancestors(export_directory).map_err(|_| ())?;
        let export_metadata = fs::symlink_metadata(export_directory).map_err(|_| ())?;
        if !export_metadata.file_type().is_dir() || export_metadata.file_type().is_symlink() {
            return Err(());
        }

        let mut entries = BTreeMap::<String, ExportEntry>::new();
        let diagnostics = bounded_diagnostics(diagnostic_lines)?;
        insert_entry(
            &mut entries,
            "executor/diagnostics.txt",
            "text/plain; charset=utf-8",
            diagnostics,
        )?;

        for file in read_fixed_directory(
            &self.state_directory.join(PAGE_DRIFT_DIRECTORY),
            "json",
            MAX_PAGE_DRIFT_BYTES,
            MAX_PAGE_DRIFT_ARTIFACTS,
        )? {
            let artifact_id = canonical_artifact_id(&file.file_name, "json")?;
            validate_page_drift(&file.bytes, &artifact_id)?;
            insert_entry(
                &mut entries,
                &format!("page-drift/{artifact_id}.json"),
                PAGE_DRIFT_MEDIA_TYPE,
                file.bytes,
            )?;
        }

        let screenshots = read_fixed_directory(
            &self.state_directory.join(SCREENSHOT_DIRECTORY),
            "png",
            MAX_SCREENSHOT_BYTES,
            MAX_TRACE_ARTIFACTS,
        )?
        .into_iter()
        .map(|file| {
            let artifact_id = canonical_artifact_id(&file.file_name, "png")?;
            validate_png(&file.bytes)?;
            Ok((artifact_id, file.bytes))
        })
        .collect::<Result<BTreeMap<_, _>, ()>>()?;

        let mut referenced_screenshots = BTreeSet::new();
        for file in read_fixed_directory(
            &self.state_directory.join(TRACE_DIRECTORY),
            "json",
            MAX_TRACE_BYTES,
            MAX_TRACE_ARTIFACTS,
        )? {
            let artifact_id = canonical_artifact_id(&file.file_name, "json")?;
            let screenshot_id = validate_trace(&file.bytes, &artifact_id)?;
            if !referenced_screenshots.insert(screenshot_id.clone()) {
                return Err(());
            }
            let screenshot = screenshots.get(&screenshot_id).ok_or(())?.clone();
            insert_entry(
                &mut entries,
                &format!("browser/traces/{artifact_id}.json"),
                TRACE_MEDIA_TYPE,
                file.bytes,
            )?;
            insert_entry(
                &mut entries,
                &format!("browser/screenshots/{screenshot_id}.png"),
                "image/png",
                screenshot,
            )?;
        }
        if entries.len() + 1 > MAX_DIAGNOSTIC_EXPORT_ENTRIES {
            return Err(());
        }

        let manifest = ExportManifest {
            entries: entries
                .iter()
                .map(|(path, entry)| ManifestEntry {
                    media_type: entry.media_type,
                    path: path.as_str(),
                    sha256: hex_sha256(&entry.bytes),
                    size_bytes: entry.bytes.len(),
                })
                .collect(),
            export_version: DIAGNOSTIC_EXPORT_VERSION,
        };
        let manifest_bytes = serde_json::to_vec(&manifest).map_err(|_| ())?;
        insert_entry(
            &mut entries,
            "manifest.json",
            "application/json",
            manifest_bytes,
        )?;

        let export_id = generate_uuid_v4()?;
        let file_name = format!("automation-tool-diagnostics-{export_id}.zip");
        let destination = export_directory.join(&file_name);
        let result = write_zip(&destination, &entries);
        if result.is_err() {
            let _ = fs::remove_file(&destination);
        }
        let total_bytes = result?;
        Ok(DiagnosticExportReceipt {
            file_name,
            entry_count: entries.len(),
            total_bytes,
        })
    }
}

struct FixedFile {
    file_name: String,
    bytes: Vec<u8>,
}

struct ExportEntry {
    media_type: &'static str,
    bytes: Vec<u8>,
}

#[derive(Serialize)]
struct ExportManifest<'a> {
    entries: Vec<ManifestEntry<'a>>,
    export_version: &'static str,
}

#[derive(Serialize)]
struct ManifestEntry<'a> {
    media_type: &'static str,
    path: &'a str,
    sha256: String,
    size_bytes: usize,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct PageDriftDocument {
    artifact_id: String,
    artifact_version: String,
    evidence: String,
    observed_at: String,
    operation: String,
    page_revision: u64,
    platform: String,
    stage: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct TraceDocument {
    artifact_id: String,
    artifact_version: String,
    captured_at: String,
    operation: String,
    page_revision: u64,
    platform: String,
    redaction_version: String,
    screenshot_artifact_id: String,
    stage: String,
    trigger: String,
}

fn bounded_diagnostics(lines: &[String]) -> Result<Vec<u8>, ()> {
    if lines.len() > MAX_RETAINED_DIAGNOSTIC_LINES {
        return Err(());
    }
    let mut output = Vec::new();
    for line in lines {
        let safe = redact_diagnostic_line(line);
        if safe.len() > MAX_DIAGNOSTIC_LINE_BYTES {
            return Err(());
        }
        if !output.is_empty() {
            output.push(b'\n');
        }
        output.extend_from_slice(safe.as_bytes());
        if output.len() > MAX_RETAINED_DIAGNOSTIC_BYTES {
            return Err(());
        }
    }
    Ok(output)
}

fn read_fixed_directory(
    directory: &Path,
    extension: &str,
    maximum_bytes: usize,
    maximum_files: usize,
) -> Result<Vec<FixedFile>, ()> {
    ensure_no_symlink_ancestors(directory).map_err(|_| ())?;
    let metadata = match fs::symlink_metadata(directory) {
        Ok(value) => value,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(_) => return Err(()),
    };
    if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
        return Err(());
    }
    let mut paths = fs::read_dir(directory)
        .map_err(|_| ())?
        .map(|entry| entry.map(|value| value.path()).map_err(|_| ()))
        .collect::<Result<Vec<_>, _>>()?;
    paths.sort();
    if paths.len() > maximum_files {
        return Err(());
    }
    let mut result = Vec::with_capacity(paths.len());
    for path in paths {
        let file_name = path
            .file_name()
            .and_then(|value| value.to_str())
            .ok_or(())?;
        canonical_artifact_id(file_name, extension)?;
        let before = fs::symlink_metadata(&path).map_err(|_| ())?;
        if !before.file_type().is_file()
            || before.file_type().is_symlink()
            || before.len() == 0
            || before.len() > maximum_bytes as u64
        {
            return Err(());
        }
        let bytes = read_bounded_regular_file(&path, maximum_bytes).map_err(|_| ())?;
        if bytes.is_empty() || before.len() != bytes.len() as u64 {
            return Err(());
        }
        result.push(FixedFile {
            file_name: file_name.to_owned(),
            bytes,
        });
    }
    Ok(result)
}

fn canonical_artifact_id(file_name: &str, extension: &str) -> Result<String, ()> {
    let suffix = format!(".{extension}");
    let source = file_name.strip_suffix(&suffix).ok_or(())?;
    require_uuid_v4(source)?;
    Ok(source.to_owned())
}

fn validate_page_drift(source: &[u8], expected_id: &str) -> Result<(), ()> {
    let document: PageDriftDocument = serde_json::from_slice(source).map_err(|_| ())?;
    if serde_json::to_vec(&document).map_err(|_| ())? != source
        || document.artifact_id != expected_id
        || document.artifact_version != "executor.page-drift-artifact.v1"
        || !matches!(
            document.evidence.as_str(),
            "page_version_unknown" | "conflicting_anchors"
        )
        || document.operation != "douyin_target_discovery"
        || document.page_revision == 0
        || document.page_revision > MAX_CROSS_RUNTIME_SEQUENCE
        || document.platform != "douyin"
        || document.stage != "search"
    {
        return Err(());
    }
    validate_timestamp(&document.observed_at)
}

fn validate_trace(source: &[u8], expected_id: &str) -> Result<String, ()> {
    let document: TraceDocument = serde_json::from_slice(source).map_err(|_| ())?;
    if serde_json::to_vec(&document).map_err(|_| ())? != source
        || document.artifact_id != expected_id
        || document.artifact_version != "executor.browser-diagnostic-trace.v1"
        || document.operation != "douyin_target_discovery"
        || document.page_revision == 0
        || document.page_revision > MAX_CROSS_RUNTIME_SEQUENCE
        || document.platform != "douyin"
        || document.redaction_version != "browser-skeleton.v1"
        || !matches!(document.stage.as_str(), "search" | "scroll" | "extraction")
        || !matches!(document.trigger.as_str(), "failure" | "user_enabled")
    {
        return Err(());
    }
    validate_timestamp(&document.captured_at)?;
    require_uuid_v4(&document.screenshot_artifact_id)?;
    Ok(document.screenshot_artifact_id)
}

fn validate_timestamp(source: &str) -> Result<(), ()> {
    if source.len() < 20 || source.len() > 32 || !source.ends_with('Z') {
        return Err(());
    }
    let parsed = OffsetDateTime::parse(source, &Rfc3339).map_err(|_| ())?;
    if parsed.offset() != time::UtcOffset::UTC {
        return Err(());
    }
    Ok(())
}

fn validate_png(source: &[u8]) -> Result<(), ()> {
    if source.is_empty()
        || source.len() > MAX_SCREENSHOT_BYTES
        || !source.starts_with(PNG_SIGNATURE)
    {
        return Err(());
    }
    let mut offset = PNG_SIGNATURE.len();
    let mut seen_header = false;
    let mut seen_pixels = false;
    let mut seen_end = false;
    while offset < source.len() {
        if seen_end || source.len() - offset < 12 {
            return Err(());
        }
        let length =
            u32::from_be_bytes(source[offset..offset + 4].try_into().map_err(|_| ())?) as usize;
        let chunk_end = offset
            .checked_add(12)
            .and_then(|value| value.checked_add(length))
            .ok_or(())?;
        if chunk_end > source.len() {
            return Err(());
        }
        let kind = &source[offset + 4..offset + 8];
        let payload = &source[offset + 8..offset + 8 + length];
        let expected_crc = u32::from_be_bytes(
            source[offset + 8 + length..chunk_end]
                .try_into()
                .map_err(|_| ())?,
        );
        let mut hasher = crc32fast::Hasher::new();
        hasher.update(kind);
        hasher.update(payload);
        if hasher.finalize() != expected_crc {
            return Err(());
        }
        if !seen_header {
            if kind != b"IHDR" || length != 13 {
                return Err(());
            }
            let width = u32::from_be_bytes(payload[0..4].try_into().map_err(|_| ())?);
            let height = u32::from_be_bytes(payload[4..8].try_into().map_err(|_| ())?);
            if width == 0
                || height == 0
                || width > MAX_SCREENSHOT_DIMENSION
                || height > MAX_SCREENSHOT_DIMENSION
            {
                return Err(());
            }
            seen_header = true;
        }
        match kind {
            b"IDAT" => seen_pixels = true,
            b"IEND" if length == 0 => seen_end = true,
            b"IEND" => return Err(()),
            b"IHDR" | b"PLTE" => {}
            _ => return Err(()),
        }
        offset = chunk_end;
    }
    if !seen_header || !seen_pixels || !seen_end {
        return Err(());
    }
    Ok(())
}

fn insert_entry(
    entries: &mut BTreeMap<String, ExportEntry>,
    path: &str,
    media_type: &'static str,
    bytes: Vec<u8>,
) -> Result<(), ()> {
    if bytes.len() as u64 > MAX_DIAGNOSTIC_EXPORT_BYTES
        || entries
            .insert(path.to_owned(), ExportEntry { media_type, bytes })
            .is_some()
    {
        return Err(());
    }
    Ok(())
}

fn write_zip(destination: &Path, entries: &BTreeMap<String, ExportEntry>) -> Result<u64, ()> {
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)
        .map_err(|_| ())?;
    let options = SimpleFileOptions::default()
        .compression_method(CompressionMethod::Stored)
        .unix_permissions(0o600);
    let mut writer = ZipWriter::new(file);
    for (path, entry) in entries {
        writer.start_file(path, options).map_err(|_| ())?;
        writer.write_all(&entry.bytes).map_err(|_| ())?;
    }
    let file = writer.finish().map_err(|_| ())?;
    file.sync_all().map_err(|_| ())?;
    let size = file.metadata().map_err(|_| ())?.len();
    if size == 0 || size > MAX_DIAGNOSTIC_EXPORT_BYTES {
        return Err(());
    }
    Ok(size)
}

fn require_absolute_clean_path(path: &Path) -> Result<(), DiagnosticExportError> {
    if !path.is_absolute()
        || path
            .components()
            .any(|component| matches!(component, Component::ParentDir | Component::CurDir))
    {
        return Err(DiagnosticExportError::storage_unavailable());
    }
    Ok(())
}

fn require_uuid_v4(source: &str) -> Result<Uuid, ()> {
    let parsed = Uuid::parse_str(source).map_err(|_| ())?;
    if parsed.get_version_num() != 4
        || parsed.get_variant() != Variant::RFC4122
        || parsed.to_string() != source
    {
        return Err(());
    }
    Ok(parsed)
}

fn generate_uuid_v4() -> Result<Uuid, ()> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes).map_err(|_| ())?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let value = Uuid::from_bytes(bytes);
    require_uuid_v4(&value.to_string())
}

fn hex_sha256(source: &[u8]) -> String {
    Sha256::digest(source)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    struct TestDirectory(PathBuf);

    impl TestDirectory {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "automation-tool-h813-{}",
                generate_uuid_v4().expect("test UUID")
            ));
            fs::create_dir(&path).expect("test root");
            Self(path)
        }

        fn path(&self) -> &Path {
            &self.0
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn write_file(root: &Path, relative: &str, bytes: &[u8]) {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().expect("parent")).expect("artifact directory");
        fs::write(path, bytes).expect("artifact");
    }

    fn png() -> Vec<u8> {
        let mut output = PNG_SIGNATURE.to_vec();
        for (kind, payload) in [
            (
                b"IHDR".as_slice(),
                [0, 0, 0, 1, 0, 0, 0, 1, 8, 2, 0, 0, 0].as_slice(),
            ),
            (b"IDAT".as_slice(), [1].as_slice()),
            (b"IEND".as_slice(), [].as_slice()),
        ] {
            output.extend_from_slice(&(payload.len() as u32).to_be_bytes());
            output.extend_from_slice(kind);
            output.extend_from_slice(payload);
            let mut hasher = crc32fast::Hasher::new();
            hasher.update(kind);
            hasher.update(payload);
            output.extend_from_slice(&hasher.finalize().to_be_bytes());
        }
        output
    }

    #[test]
    fn exports_only_valid_fixed_artifacts_and_redacts_logs() {
        let root = TestDirectory::new();
        let app_data = root.path().join("app-data");
        let export_directory = root.path().join("exports");
        fs::create_dir_all(&app_data).expect("app data");
        fs::create_dir(&export_directory).expect("exports");
        let service = DiagnosticExportService::initialize(&app_data).expect("service");
        let state = app_data.join("local-executor/state");
        let page_id = "123e4567-e89b-42d3-a456-426614174000";
        let trace_id = "223e4567-e89b-42d3-a456-426614174000";
        let screenshot_id = "323e4567-e89b-42d3-a456-426614174000";
        let page = format!(
            "{{\"artifact_id\":\"{page_id}\",\"artifact_version\":\"executor.page-drift-artifact.v1\",\"evidence\":\"conflicting_anchors\",\"observed_at\":\"2026-07-21T01:02:03Z\",\"operation\":\"douyin_target_discovery\",\"page_revision\":1,\"platform\":\"douyin\",\"stage\":\"search\"}}"
        );
        let trace = format!(
            "{{\"artifact_id\":\"{trace_id}\",\"artifact_version\":\"executor.browser-diagnostic-trace.v1\",\"captured_at\":\"2026-07-21T01:02:03Z\",\"operation\":\"douyin_target_discovery\",\"page_revision\":2,\"platform\":\"douyin\",\"redaction_version\":\"browser-skeleton.v1\",\"screenshot_artifact_id\":\"{screenshot_id}\",\"stage\":\"scroll\",\"trigger\":\"failure\"}}"
        );
        write_file(
            &state,
            &format!("{PAGE_DRIFT_DIRECTORY}/{page_id}.json"),
            page.as_bytes(),
        );
        write_file(
            &state,
            &format!("{TRACE_DIRECTORY}/{trace_id}.json"),
            trace.as_bytes(),
        );
        write_file(
            &state,
            &format!("{SCREENSHOT_DIRECTORY}/{screenshot_id}.png"),
            &png(),
        );

        let receipt = service
            .export(
                &export_directory,
                &["authorization=private-value /Users/private/file".to_owned()],
            )
            .expect("diagnostic export");
        assert_eq!(receipt.entry_count, 5);
        assert!(receipt.total_bytes > 0);
        assert!(receipt
            .file_name
            .starts_with("automation-tool-diagnostics-"));

        let file = File::open(export_directory.join(&receipt.file_name)).expect("zip");
        let mut archive = ZipArchive::new(file).expect("archive");
        let mut names = (0..archive.len())
            .map(|index| archive.by_index(index).expect("entry").name().to_owned())
            .collect::<Vec<_>>();
        names.sort();
        assert_eq!(
            names,
            vec![
                format!("browser/screenshots/{screenshot_id}.png"),
                format!("browser/traces/{trace_id}.json"),
                "executor/diagnostics.txt".to_owned(),
                "manifest.json".to_owned(),
                format!("page-drift/{page_id}.json"),
            ]
        );
        let mut logs = String::new();
        archive
            .by_name("executor/diagnostics.txt")
            .expect("logs")
            .read_to_string(&mut logs)
            .expect("read logs");
        assert!(!logs.contains("private-value"));
        assert!(!logs.contains("/Users/"));
    }

    #[test]
    fn rejects_untrusted_paths_artifacts_and_output_collisions() {
        let root = TestDirectory::new();
        assert!(DiagnosticExportService::initialize(Path::new("relative")).is_err());
        let app_data = root.path().join("app-data");
        let exports = root.path().join("exports");
        fs::create_dir_all(&app_data).expect("app data");
        fs::create_dir(&exports).expect("exports");
        let service = DiagnosticExportService::initialize(&app_data).expect("service");
        write_file(
            &app_data.join("local-executor/state"),
            &format!("{PAGE_DRIFT_DIRECTORY}/not-an-id.json"),
            b"{}",
        );
        assert_eq!(
            service.export(&exports, &[]).expect_err("rejected").code(),
            DiagnosticExportErrorCode::StorageUnavailable
        );
        assert!(fs::read_dir(&exports).expect("exports").next().is_none());
        assert!(service.export(Path::new("relative"), &[]).is_err());
    }

    #[test]
    fn validates_bounds_timestamp_uuid_and_png_fail_closed() {
        assert!(validate_timestamp("2026-07-21T01:02:03+00:00").is_err());
        assert!(validate_timestamp("badZ").is_err());
        assert!(require_uuid_v4("123e4567-e89b-12d3-a456-426614174000").is_err());
        assert!(validate_png(b"not png").is_err());
        let mut damaged = png();
        damaged[20] ^= 1;
        assert!(validate_png(&damaged).is_err());
        let mut metadata_png = png();
        let end_offset = metadata_png.len() - 12;
        let mut text_chunk = Vec::new();
        text_chunk.extend_from_slice(&4_u32.to_be_bytes());
        text_chunk.extend_from_slice(b"tEXt");
        text_chunk.extend_from_slice(b"note");
        let mut hasher = crc32fast::Hasher::new();
        hasher.update(b"tEXt");
        hasher.update(b"note");
        text_chunk.extend_from_slice(&hasher.finalize().to_be_bytes());
        metadata_png.splice(end_offset..end_offset, text_chunk);
        assert!(validate_png(&metadata_png).is_err());
        let too_many = vec![String::new(); MAX_RETAINED_DIAGNOSTIC_LINES + 1];
        assert!(bounded_diagnostics(&too_many).is_err());
        assert_eq!(
            bounded_diagnostics(&["x\ny".to_owned()]).expect("redacted control character"),
            b"x y"
        );
    }

    #[cfg(unix)]
    #[test]
    fn rejects_a_symlinked_artifact_directory_ancestor() {
        use std::os::unix::fs::symlink;

        let root = TestDirectory::new();
        let app_data = root.path().join("app-data");
        let export_directory = root.path().join("exports");
        let outside = root.path().join("outside");
        fs::create_dir_all(app_data.join("local-executor/state")).expect("state");
        fs::create_dir(&export_directory).expect("exports");
        fs::create_dir(&outside).expect("outside");
        symlink(&outside, app_data.join("local-executor/state/artifacts"))
            .expect("artifact ancestor symlink");

        let service = DiagnosticExportService::initialize(&app_data).expect("service");
        assert!(service.export(&export_directory, &[]).is_err());
        assert!(fs::read_dir(&export_directory)
            .expect("exports")
            .next()
            .is_none());
    }
}

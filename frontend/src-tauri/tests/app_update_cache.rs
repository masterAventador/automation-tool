use std::fs;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use automation_tool_desktop_lib::app_update_cache::{
    AppUpdateCache, DownloadSource, UpdateDownloadErrorCode,
};
use automation_tool_desktop_lib::app_updates::{parse_update_release, UpdateRelease};
use base64::engine::general_purpose::STANDARD;
use base64::Engine as _;
use serde_json::json;
use sha2::{Digest, Sha256};

static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(0);

const PAYLOAD: &[u8] = b"test";
const PUBLIC_KEY_TEXT: &str = "untrusted comment: minisign public key E7620F1842B4E81F\n\
RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3";
const SIGNATURE_TEXT: &str = "untrusted comment: signature from minisign secret key\n\
RUQf6LRCGA9i559r3g7V1qNyJDApGip8MfqcadIgT9CuhV3EMhHoN1mGTkUidF/z7SrlQgXdy8ofjb7bNJJylDOocrCo8KLzZwo=\n\
trusted comment: timestamp:1556193335\tfile:test\n\
y/rUw2y8/hOUYjZU71eHp/Wo1KZ40fGy2VJEDl34XMJM+TX48Ss/17u3IvIfbVR1FkZZSNCisQbuQY+bHwhEBg==";

struct TemporaryAppData(PathBuf);

impl TemporaryAppData {
    fn new() -> Self {
        Self(std::env::temp_dir().join(format!(
            "automation-tool-h8-20-cache-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system time")
                .as_nanos(),
            NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed),
        )))
    }

    fn cache_directory(&self) -> PathBuf {
        self.0.join("app-updates/cache-v1")
    }
}

impl Drop for TemporaryAppData {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[derive(Clone)]
struct ScriptedResponse {
    status: &'static str,
    headers: Vec<(&'static str, String)>,
    body: Vec<u8>,
}

struct ScriptedServer {
    address: SocketAddr,
    requests: Arc<Mutex<Vec<String>>>,
    worker: Option<thread::JoinHandle<()>>,
}

impl ScriptedServer {
    fn start(responses: Vec<ScriptedResponse>) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind isolated update server");
        let address = listener.local_addr().expect("server address");
        let requests = Arc::new(Mutex::new(Vec::new()));
        let worker_requests = Arc::clone(&requests);
        let worker = thread::spawn(move || {
            for response in responses {
                let (mut stream, _) = listener.accept().expect("accept download request");
                let request = read_request(&mut stream);
                worker_requests
                    .lock()
                    .expect("request ledger")
                    .push(request);
                write!(stream, "HTTP/1.1 {}\r\n", response.status).expect("response status");
                for (name, value) in response.headers {
                    write!(stream, "{name}: {value}\r\n").expect("response header");
                }
                write!(stream, "Connection: close\r\n\r\n").expect("response boundary");
                stream.write_all(&response.body).expect("response body");
                stream.flush().expect("flush response");
            }
        });
        Self {
            address,
            requests,
            worker: Some(worker),
        }
    }

    fn source(&self) -> DownloadSource {
        DownloadSource::new(
            reqwest::Url::parse(&format!("http://{}/artifact", self.address))
                .expect("download URL"),
            STANDARD.encode(SIGNATURE_TEXT),
        )
        .expect("download source")
    }

    fn finish(mut self) -> Vec<String> {
        self.worker
            .take()
            .expect("server worker")
            .join()
            .expect("server join");
        self.requests.lock().expect("request ledger").clone()
    }
}

impl Drop for ScriptedServer {
    fn drop(&mut self) {
        if let Some(worker) = self.worker.take() {
            worker.join().expect("server join");
        }
    }
}

fn read_request(stream: &mut TcpStream) -> String {
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .expect("request timeout");
    let mut bytes = Vec::new();
    let mut byte = [0u8; 1];
    while bytes.len() < 16 * 1024 && !bytes.ends_with(b"\r\n\r\n") {
        let count = stream.read(&mut byte).expect("read request");
        if count == 0 {
            break;
        }
        bytes.push(byte[0]);
    }
    String::from_utf8(bytes).expect("ASCII request")
}

fn full_response() -> ScriptedResponse {
    ScriptedResponse {
        status: "200 OK",
        headers: vec![
            ("Content-Length", PAYLOAD.len().to_string()),
            ("ETag", "\"artifact-v1\"".to_owned()),
        ],
        body: PAYLOAD.to_vec(),
    }
}

fn release(version: &str, digest: &str) -> UpdateRelease {
    let signature = STANDARD.encode(SIGNATURE_TEXT);
    parse_update_release(
        version,
        &json!({
            "version": version,
            "url": "https://downloads.example.test/artifact",
            "signature": signature,
            "update_contract": {
                "version": 1,
                "channel": "stable",
                "policy": "optional",
                "artifact": {
                    "target": "darwin",
                    "arch": "aarch64",
                    "sha256": digest,
                    "size_bytes": PAYLOAD.len()
                }
            }
        }),
    )
    .expect("release fixture")
}

fn payload_digest() -> String {
    Sha256::digest(PAYLOAD)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(2))
        .build()
        .expect("download client")
}

#[test]
fn interrupted_download_resumes_by_range_then_atomically_becomes_the_only_cached_package() {
    let app_data = TemporaryAppData::new();
    let cache = AppUpdateCache::initialize(&app_data.0, &STANDARD.encode(PUBLIC_KEY_TEXT))
        .expect("update cache");
    let candidate = release("0.2.0", &payload_digest());
    let server = ScriptedServer::start(vec![
        ScriptedResponse {
            status: "200 OK",
            headers: vec![
                ("Content-Length", PAYLOAD.len().to_string()),
                ("ETag", "\"artifact-v1\"".to_owned()),
            ],
            body: b"te".to_vec(),
        },
        ScriptedResponse {
            status: "206 Partial Content",
            headers: vec![
                ("Content-Length", "2".to_owned()),
                ("Content-Range", "bytes 2-3/4".to_owned()),
                ("ETag", "\"artifact-v1\"".to_owned()),
            ],
            body: b"st".to_vec(),
        },
    ]);
    let source = server.source();
    let progress = Arc::new(Mutex::new(Vec::new()));

    let first_progress = Arc::clone(&progress);
    let first = tauri::async_runtime::block_on(cache.download(
        &client(),
        &candidate,
        &source,
        move |downloaded, total| {
            first_progress
                .lock()
                .expect("progress")
                .push((downloaded, total));
        },
    ));
    assert_eq!(
        first.expect_err("truncated body fails").code(),
        UpdateDownloadErrorCode::TransportUnavailable
    );
    assert_eq!(
        fs::read(app_data.cache_directory().join("candidate.partial")).expect("partial bytes"),
        b"te"
    );

    let second_progress = Arc::clone(&progress);
    let cached = tauri::async_runtime::block_on(cache.download(
        &client(),
        &candidate,
        &source,
        move |downloaded, total| {
            second_progress
                .lock()
                .expect("progress")
                .push((downloaded, total));
        },
    ))
    .expect("resume and verify");
    assert_eq!(cached.version(), "0.2.0");
    assert_eq!(cached.size_bytes(), 4);
    assert_eq!(
        fs::read(app_data.cache_directory().join("candidate.package")).expect("cached package"),
        PAYLOAD
    );
    assert!(!app_data
        .cache_directory()
        .join("candidate.partial")
        .exists());
    let entries = fs::read_dir(app_data.cache_directory())
        .expect("cache entries")
        .collect::<Result<Vec<_>, _>>()
        .expect("cache entries");
    assert_eq!(entries.len(), 2);
    assert!(progress.lock().expect("progress").contains(&(4, 4)));

    let requests = server.finish();
    assert_eq!(requests.len(), 2);
    assert!(!requests[0].to_ascii_lowercase().contains("range:"));
    assert!(requests[1].to_ascii_lowercase().contains("range: bytes=2-"));
    assert!(requests[1]
        .to_ascii_lowercase()
        .contains("if-range: \"artifact-v1\""));

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        assert_eq!(
            fs::metadata(app_data.cache_directory())
                .expect("cache directory")
                .permissions()
                .mode()
                & 0o077,
            0
        );
        assert_eq!(
            fs::metadata(app_data.cache_directory().join("candidate.package"))
                .expect("cache package")
                .permissions()
                .mode()
                & 0o077,
            0
        );
    }
}

#[test]
fn verified_new_version_replaces_the_single_old_package_and_reuses_an_exact_cache_hit() {
    let app_data = TemporaryAppData::new();
    let cache = AppUpdateCache::initialize(&app_data.0, &STANDARD.encode(PUBLIC_KEY_TEXT))
        .expect("update cache");
    let first = release("0.2.0", &payload_digest());
    let first_server = ScriptedServer::start(vec![full_response()]);
    tauri::async_runtime::block_on(cache.download(
        &client(),
        &first,
        &first_server.source(),
        |_, _| {},
    ))
    .expect("cache first");
    first_server.finish();

    let no_server_request = ScriptedServer::start(Vec::new());
    let reused = tauri::async_runtime::block_on(cache.download(
        &client(),
        &first,
        &no_server_request.source(),
        |_, _| {},
    ))
    .expect("reuse verified cache");
    assert_eq!(reused.version(), "0.2.0");
    assert!(no_server_request.finish().is_empty());

    let newer = release("0.3.0", &payload_digest());
    let newer_server = ScriptedServer::start(vec![full_response()]);
    let replaced = tauri::async_runtime::block_on(cache.download(
        &client(),
        &newer,
        &newer_server.source(),
        |_, _| {},
    ))
    .expect("replace with verified newer candidate");
    newer_server.finish();
    assert_eq!(replaced.version(), "0.3.0");
    assert_eq!(cache.cached().expect("cached record"), Some(replaced));
    assert_eq!(
        fs::read(app_data.cache_directory().join("candidate.package")).expect("single package"),
        PAYLOAD
    );
}

#[test]
fn digest_or_signature_failure_preserves_the_previous_verified_package() {
    let app_data = TemporaryAppData::new();
    let cache = AppUpdateCache::initialize(&app_data.0, &STANDARD.encode(PUBLIC_KEY_TEXT))
        .expect("update cache");
    let original = release("0.2.0", &payload_digest());
    let original_server = ScriptedServer::start(vec![full_response()]);
    let cached = tauri::async_runtime::block_on(cache.download(
        &client(),
        &original,
        &original_server.source(),
        |_, _| {},
    ))
    .expect("cache original");
    original_server.finish();

    let bad_digest = release("0.3.0", &"b".repeat(64));
    let digest_server = ScriptedServer::start(vec![full_response()]);
    assert_eq!(
        tauri::async_runtime::block_on(cache.download(
            &client(),
            &bad_digest,
            &digest_server.source(),
            |_, _| {},
        ))
        .expect_err("digest mismatch")
        .code(),
        UpdateDownloadErrorCode::ManifestRejected
    );
    digest_server.finish();
    assert_eq!(cache.cached().expect("old cache"), Some(cached.clone()));

    let bad_signature = release("0.4.0", &payload_digest());
    let signature_server = ScriptedServer::start(vec![full_response()]);
    let invalid_source = DownloadSource::new(
        reqwest::Url::parse(&format!("http://{}/artifact", signature_server.address))
            .expect("download URL"),
        STANDARD.encode("not a minisign signature"),
    )
    .expect("bounded invalid signature source");
    assert_eq!(
        tauri::async_runtime::block_on(cache.download(
            &client(),
            &bad_signature,
            &invalid_source,
            |_, _| {},
        ))
        .expect_err("signature mismatch")
        .code(),
        UpdateDownloadErrorCode::SignatureRejected
    );
    signature_server.finish();
    assert_eq!(cache.cached().expect("old cache"), Some(cached));
    assert_eq!(
        fs::read(app_data.cache_directory().join("candidate.package")).expect("old package"),
        PAYLOAD
    );
}

#[cfg(unix)]
#[test]
fn cache_rejects_symlinked_files_and_never_reflects_private_paths() {
    use std::os::unix::fs::{symlink, PermissionsExt};

    let app_data = TemporaryAppData::new();
    let cache = AppUpdateCache::initialize(&app_data.0, &STANDARD.encode(PUBLIC_KEY_TEXT))
        .expect("update cache");
    let outside = app_data.0.with_extension("outside");
    fs::write(&outside, b"outside").expect("outside fixture");
    symlink(
        &outside,
        app_data.cache_directory().join("candidate.partial"),
    )
    .expect("partial symlink");
    let server = ScriptedServer::start(Vec::new());
    let error = tauri::async_runtime::block_on(cache.download(
        &client(),
        &release("0.2.0", &payload_digest()),
        &server.source(),
        |_, _| {},
    ))
    .expect_err("symlink rejected");
    assert_eq!(error.code(), UpdateDownloadErrorCode::StorageUnavailable);
    assert_eq!(error.to_string(), "update download unavailable");
    assert!(!error
        .to_string()
        .contains(outside.to_string_lossy().as_ref()));
    assert!(server.finish().is_empty());

    fs::remove_file(app_data.cache_directory().join("candidate.partial"))
        .expect("remove partial symlink");
    let manifest = app_data.cache_directory().join("cache-manifest-v1");
    fs::write(&manifest, b"{}").expect("corrupt cache manifest");
    fs::set_permissions(&manifest, fs::Permissions::from_mode(0o644))
        .expect("broaden manifest permissions");
    assert_eq!(
        AppUpdateCache::initialize(&app_data.0, &STANDARD.encode(PUBLIC_KEY_TEXT))
            .expect_err("unsafe manifest rejected")
            .code(),
        UpdateDownloadErrorCode::StorageUnavailable
    );
    fs::remove_file(outside).expect("remove outside fixture");
}

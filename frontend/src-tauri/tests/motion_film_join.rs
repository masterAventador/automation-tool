//! Bringing the shots of a film onto one canvas, and measuring what came out.
//!
//! Route A captures each shot on the stage its part declares. A delivered film
//! is one size throughout, so the two have to be reconciled somewhere — and the
//! reason it is done here, with a measurement afterwards, is that the cheap
//! checks do not catch getting it wrong.
//!
//! Measured 2026-07-28 with the packaged ffmpeg: joining a 1920x1080 segment to
//! a 1080x1920 one through the concat demuxer with `-c copy` exits 0 and reports
//! 279 frames and 9.300000 seconds — both exactly the sums of the inputs. The
//! file is broken anyway: its second half is portrait content in a container
//! claiming landscape, visible only in the pixels. So these tests use the real
//! packaged ffmpeg on real files, and the assertions are about what the finished
//! film measures rather than about whether a command succeeded.

use automation_tool_desktop_lib::motion_video_studio::{
    film_canvas, join_motion_film, motion_segment_encode_command, MotionVideoStudioErrorCode,
};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

static TEMPORARY_SEQUENCE: AtomicU64 = AtomicU64::new(1);

struct TempDirectory(PathBuf);

impl TempDirectory {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "automation-tool-pc06-join-{}-{}",
            std::process::id(),
            TEMPORARY_SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }
}

impl Drop for TempDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// The packaged FFmpeg pair, at the path a debug App resolves it from.
///
/// The real binaries rather than a stand-in: what is being tested is what
/// ffmpeg actually does with mismatched streams, and a fake that returns a
/// canned answer would assert only that this test agrees with itself.
fn toolchain() -> (PathBuf, PathBuf) {
    let root = std::env::current_exe()
        .expect("test executable")
        .parent()
        .and_then(Path::parent)
        .expect("a test binary always sits under <target>/<profile>/deps")
        .join("media-toolchain")
        .join("bin");
    let ffmpeg = root.join("ffmpeg");
    let ffprobe = root.join("ffprobe");
    assert!(
        ffmpeg.is_file() && ffprobe.is_file(),
        "the packaged FFmpeg pair must be assembled at {}. \
         Build it with scripts/prepare_video_runtime.py before running this suite.",
        root.display()
    );
    (ffmpeg, ffprobe)
}

/// One clip of a solid colour, at a stage of its own, straight from ffmpeg.
///
/// Stands in for a rendered segment before it has been brought onto the film's
/// canvas — which is the only state the join has an opinion about.
fn clip(ffmpeg: &Path, path: &Path, width: u32, height: u32, frames: u32, colour: &str) {
    let status = std::process::Command::new(ffmpeg)
        .args([
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            &format!("color=c={colour}:s={width}x{height}:r=30:d=1"),
            "-frames:v",
            &frames.to_string(),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
        ])
        .arg(path)
        .status()
        .expect("the packaged ffmpeg must run");
    assert!(status.success(), "building a test clip must succeed");
}

/// What a file actually is, asked of the same toolchain that made it.
fn probe(ffprobe: &Path, path: &Path) -> (u32, u32, u32) {
    let output = std::process::Command::new(ffprobe)
        .args([
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,nb_read_frames",
            "-of",
            "json",
        ])
        .arg(path)
        .output()
        .expect("the packaged ffprobe must run");
    let document: serde_json::Value = serde_json::from_slice(&output.stdout).expect("ffprobe JSON");
    let stream = &document["streams"][0];
    (
        stream["width"].as_u64().unwrap() as u32,
        stream["height"].as_u64().unwrap() as u32,
        stream["nb_read_frames"]
            .as_str()
            .unwrap()
            .parse()
            .expect("a decoded frame count"),
    )
}

/// One PNG of a solid colour, written by ffmpeg, as a rendered frame.
fn frame(ffmpeg: &Path, path: &Path, width: u32, height: u32, colour: &str) {
    let status = std::process::Command::new(ffmpeg)
        .args([
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            &format!("color=c={colour}:s={width}x{height}"),
            "-frames:v",
            "1",
        ])
        .arg(path)
        .status()
        .expect("the packaged ffmpeg must run");
    assert!(status.success(), "building a test frame must succeed");
}

#[test]
fn the_film_canvas_follows_the_framing_the_user_asked_for() {
    let landscape = film_canvas("16:9").expect("the App offers this framing");
    assert_eq!(
        (landscape.width(), landscape.height()),
        (1920, 1080),
        "the common case is the stage 105 of the catalog's parts already declare"
    );
    let portrait = film_canvas("9:16").expect("the App offers this framing too");
    assert_eq!((portrait.width(), portrait.height()), (1080, 1920));
    assert_eq!(landscape.frames_per_second(), 30);

    // A framing the contract does not declare cannot be delivered. Guessing one
    // would produce a film in a shape the user never asked for.
    let error = film_canvas("1:1").expect_err("an undeclared framing has no canvas");
    assert_eq!(error.code(), MotionVideoStudioErrorCode::DraftInvalid);
}

/// The shots of a film are captured on different stages and delivered as one.
#[test]
fn segments_captured_on_different_stages_become_one_film_on_one_canvas() {
    let (ffmpeg, ffprobe) = toolchain();
    let root = TempDirectory::new();
    let canvas = film_canvas("16:9").unwrap();

    // Two shots as the sandbox leaves them: one on the stage most of the
    // catalog declares, one on the portrait stage three parts declare.
    let mut segments = Vec::new();
    for (index, (width, height, frames, colour)) in [
        (1920_u32, 1080_u32, 30_u32, "red"),
        (1080, 1920, 15, "blue"),
    ]
    .into_iter()
    .enumerate()
    {
        let frames_directory = root.0.join(format!("frames-{index}"));
        fs::create_dir_all(&frames_directory).unwrap();
        for number in 1..=frames {
            frame(
                &ffmpeg,
                &frames_directory.join(format!("frame-{number:05}.png")),
                width,
                height,
                colour,
            );
        }
        let segment = root.0.join(format!("segment-{index}.mp4"));
        // The command production runs, spawned here rather than through the
        // render job's own wait loop: that loop exists to notice a cancelled
        // job, which is a thing this test has none of.
        let status = motion_segment_encode_command(&ffmpeg, &frames_directory, &segment, &canvas, frames)
            .status()
            .expect("the packaged ffmpeg must run");
        assert!(status.success(), "a captured shot encodes onto the film's canvas");
        // Each shot arrives on the canvas as it is encoded, so the join itself
        // never has to reconcile anything.
        assert_eq!(
            probe(&ffprobe, &segment),
            (canvas.width(), canvas.height(), frames)
        );
        segments.push(segment);
    }

    let film = root.0.join("film.mp4");
    join_motion_film(&segments, &film, &canvas, &ffmpeg, &ffprobe, 45)
        .expect("segments already on the canvas join into the film");

    assert_eq!(probe(&ffprobe, &film), (1920, 1080, 45));
}

/// The failure this whole arrangement exists for: a join that looks like it worked.
#[test]
fn a_join_that_exits_zero_is_still_refused_when_the_film_is_not_what_was_asked_for() {
    let (ffmpeg, ffprobe) = toolchain();
    let root = TempDirectory::new();
    let canvas = film_canvas("16:9").unwrap();

    // Deliberately not put on the canvas first — this is the state the measured
    // incident was in, and `-c copy` will accept it without complaint.
    let landscape = root.0.join("landscape.mp4");
    let portrait = root.0.join("portrait.mp4");
    clip(&ffmpeg, &landscape, 1920, 1080, 30, "red");
    clip(&ffmpeg, &portrait, 1080, 1920, 15, "blue");

    let film = root.0.join("film.mp4");
    let error = join_motion_film(
        &[landscape, portrait],
        &film,
        &canvas,
        &ffmpeg,
        &ffprobe,
        45,
    )
    .expect_err("a film whose second half is the wrong shape is not a film");
    assert_eq!(error.code(), MotionVideoStudioErrorCode::RenderUnavailable);
    assert!(
        !film.exists(),
        "a refused join must not leave a plausible-looking file behind for the artifact store to import"
    );
}

/// A film has to be as long as the shots it was assembled from.
#[test]
fn a_film_missing_frames_it_was_promised_is_refused() {
    let (ffmpeg, ffprobe) = toolchain();
    let root = TempDirectory::new();
    let canvas = film_canvas("16:9").unwrap();
    let only = root.0.join("only.mp4");
    clip(&ffmpeg, &only, 1920, 1080, 30, "red");

    let film = root.0.join("film.mp4");
    let error = join_motion_film(&[only], &film, &canvas, &ffmpeg, &ffprobe, 45)
        .expect_err("30 frames is not the 45 the film's shots account for");
    assert_eq!(error.code(), MotionVideoStudioErrorCode::RenderUnavailable);
}

#[test]
fn a_film_needs_at_least_one_shot() {
    let (ffmpeg, ffprobe) = toolchain();
    let root = TempDirectory::new();
    let canvas = film_canvas("16:9").unwrap();
    let error = join_motion_film(&[], &root.0.join("film.mp4"), &canvas, &ffmpeg, &ffprobe, 0)
        .expect_err("there is no film without a shot");
    assert_eq!(error.code(), MotionVideoStudioErrorCode::RenderUnavailable);
}

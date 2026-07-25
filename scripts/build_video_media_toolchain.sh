#!/usr/bin/env bash
set -euo pipefail

FFMPEG_VERSION="8.1.2"
FFMPEG_SHA256="464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c"
X264_REVISION="b35605ace3ddf7c1a5d67a2eb553f034aef41d55"
X264_SHA256="cd71a7515b0e9a012e1ac9b1f8415bebcaf6fc97d4db32286642ac4c0fbe24f9"

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <macos-arm64|windows-x86_64> <output-directory>" >&2
  exit 2
fi

TARGET_ID="$1"
OUTPUT_DIR="$2"
case "$TARGET_ID" in
  macos-arm64)
    [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || {
      echo "macos-arm64 must be built natively on Apple Silicon" >&2
      exit 1
    }
    EXE_SUFFIX=""
    X264_HOST_ARGS=()
    FFMPEG_ARCH_ARGS=(--arch=arm64)
    FFMPEG_STATIC_LINK_FLAGS=()
    ;;
  windows-x86_64)
    [[ "${MSYSTEM:-}" == "MINGW64" ]] || {
      echo "windows-x86_64 must be built in an MSYS2 MINGW64 shell" >&2
      exit 1
    }
    EXE_SUFFIX=".exe"
    # This is a native MINGW64 build. Current MSYS2 exposes gcc/binutils
    # without the x86_64-w64-mingw32-* prefix; declaring a cross prefix makes
    # x264 call a non-existent x86_64-w64-mingw32-strings and fail its endian
    # probe before compilation.
    X264_HOST_ARGS=()
    FFMPEG_ARCH_ARGS=(--arch=x86_64 --target-os=mingw32)
    FFMPEG_STATIC_LINK_FLAGS=(-static -static-libgcc)
    ;;
  *)
    echo "unsupported target: $TARGET_ID" >&2
    exit 2
    ;;
esac

BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/automation-tool-vf04-build.XXXXXX")"
trap 'rm -rf -- "$BUILD_ROOT"' EXIT
SOURCE_DIR="$BUILD_ROOT/source"
PREFIX_DIR="$BUILD_ROOT/prefix"
mkdir -p "$SOURCE_DIR" "$PREFIX_DIR" "$OUTPUT_DIR/bin" "$OUTPUT_DIR/source"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd -P)"

FFMPEG_ARCHIVE="$SOURCE_DIR/ffmpeg-${FFMPEG_VERSION}.tar.xz"
X264_ARCHIVE="$SOURCE_DIR/x264-${X264_REVISION}.tar.gz"
curl --fail --location --retry 3 --output "$FFMPEG_ARCHIVE" \
  "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz"
curl --fail --location --retry 3 --output "$X264_ARCHIVE" \
  "https://code.videolan.org/videolan/x264/-/archive/${X264_REVISION}/x264-${X264_REVISION}.tar.gz"

verify_sha256() {
  local expected="$1"
  local path="$2"
  local actual
  if command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$path" | awk '{print $1}')"
  else
    actual="$(sha256sum "$path" | awk '{print $1}')"
  fi
  [[ "$actual" == "$expected" ]] || {
    echo "SHA-256 mismatch for $path" >&2
    exit 1
  }
}

verify_sha256 "$FFMPEG_SHA256" "$FFMPEG_ARCHIVE"
verify_sha256 "$X264_SHA256" "$X264_ARCHIVE"

tar -xf "$X264_ARCHIVE" -C "$SOURCE_DIR"
tar -xf "$FFMPEG_ARCHIVE" -C "$SOURCE_DIR"

pushd "$SOURCE_DIR/x264-${X264_REVISION}" >/dev/null
if [[ ${#X264_HOST_ARGS[@]} -eq 0 ]]; then
  ./configure \
    --prefix="$PREFIX_DIR" \
    --enable-static \
    --disable-cli \
    --disable-opencl
else
  ./configure \
    --prefix="$PREFIX_DIR" \
    --enable-static \
    --disable-cli \
    --disable-opencl \
    "${X264_HOST_ARGS[@]}"
fi
make -j"${NUMBER_OF_PROCESSORS:-$(sysctl -n hw.logicalcpu 2>/dev/null || echo 2)}"
make install
popd >/dev/null

export PKG_CONFIG_PATH="$PREFIX_DIR/lib/pkgconfig"
pushd "$SOURCE_DIR/ffmpeg-${FFMPEG_VERSION}" >/dev/null
# macOS ships bash 3.2, where `set -u` rejects an empty array's `[*]` as an
# unbound variable. The macOS branch leaves FFMPEG_STATIC_LINK_FLAGS empty on
# purpose, so the expansion below has to tolerate that. The Windows branch is
# non-empty, where `:-` never applies and the flags stay exactly as before.
./configure \
  --prefix="$PREFIX_DIR" \
  --pkg-config-flags=--static \
  --extra-cflags="-I$PREFIX_DIR/include" \
  --extra-ldflags="-L$PREFIX_DIR/lib ${FFMPEG_STATIC_LINK_FLAGS[*]:-}" \
  --enable-gpl \
  --enable-libx264 \
  --disable-autodetect \
  --enable-zlib \
  --disable-doc \
  --disable-debug \
  --disable-ffplay \
  --disable-network \
  --enable-small \
  "${FFMPEG_ARCH_ARGS[@]}"
make -j"${NUMBER_OF_PROCESSORS:-$(sysctl -n hw.logicalcpu 2>/dev/null || echo 2)}" \
  "ffmpeg${EXE_SUFFIX}" "ffprobe${EXE_SUFFIX}"
cp "ffmpeg${EXE_SUFFIX}" "$OUTPUT_DIR/bin/ffmpeg${EXE_SUFFIX}"
cp "ffprobe${EXE_SUFFIX}" "$OUTPUT_DIR/bin/ffprobe${EXE_SUFFIX}"
cp COPYING.GPLv3 "$OUTPUT_DIR/COPYING.GPLv3"
{
  echo "target=$TARGET_ID"
  echo "ffmpeg=$FFMPEG_VERSION"
  echo "x264=$X264_REVISION"
  ./ffmpeg${EXE_SUFFIX} -version | head -n 3
} > "$OUTPUT_DIR/BUILD-INFO.txt"
popd >/dev/null

cp "$FFMPEG_ARCHIVE" "$OUTPUT_DIR/source/ffmpeg-${FFMPEG_VERSION}.tar.xz"
cp "$X264_ARCHIVE" "$OUTPUT_DIR/source/x264-${X264_REVISION}.tar.gz"
cat > "$OUTPUT_DIR/NOTICE.txt" <<'EOF'
This package contains separate FFmpeg and x264 executables used as child processes.
FFmpeg is distributed under GPL-3.0-or-later because this build enables GPL x264.
The exact corresponding FFmpeg and x264 sources, build configuration and license are bundled here.
The desktop application does not rename, link to, or hide these executables.
EOF

chmod 700 "$OUTPUT_DIR/bin/ffmpeg${EXE_SUFFIX}" "$OUTPUT_DIR/bin/ffprobe${EXE_SUFFIX}"
python3 scripts/write_video_media_toolchain_manifest.py "$OUTPUT_DIR" "$TARGET_ID"
echo "built $TARGET_ID media toolchain at $OUTPUT_DIR"

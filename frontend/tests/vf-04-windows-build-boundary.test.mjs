import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("VF-04 keeps the Windows native build and reparse-point checks closed", async () => {
  const [build, checker] = await Promise.all([
    readFile(
      new URL("scripts/build_video_media_toolchain.sh", repositoryRoot),
      "utf8",
    ),
    readFile(
      new URL("scripts/check_video_media_toolchain.py", repositoryRoot),
      "utf8",
    ),
  ]);

  const windowsBlock = build.match(
    /windows-x86_64\)([\s\S]*?)\n\s*;;/u,
  )?.[1];
  assert.ok(windowsBlock, "missing windows-x86_64 build branch");
  assert.match(windowsBlock, /MSYSTEM:-.*MINGW64/u);
  assert.match(windowsBlock, /X264_HOST_ARGS=\(\)/u);
  assert.match(
    windowsBlock,
    /FFMPEG_ARCH_ARGS=\(--arch=x86_64 --target-os=mingw32\)/u,
  );
  assert.match(
    windowsBlock,
    /FFMPEG_STATIC_LINK_FLAGS=\(-static -static-libgcc\)/u,
  );
  assert.doesNotMatch(windowsBlock, /--cross-prefix=/u);

  assert.match(build, /OUTPUT_DIR="\$\(cd "\$OUTPUT_DIR" && pwd -P\)"/u);
  assert.match(
    build,
    /"ffmpeg\$\{EXE_SUFFIX\}" "ffprobe\$\{EXE_SUFFIX\}"/u,
  );
  // The Windows flags must reach the linker, and the expansion must survive
  // the macOS branch leaving the array empty: macOS ships bash 3.2, where
  // `set -u` rejects an empty array's `[*]` as an unbound variable and the
  // whole build dies before compiling anything. `:-` changes nothing for the
  // non-empty Windows array, which is what this boundary test protects.
  assert.match(
    build,
    /--extra-ldflags="-L\$PREFIX_DIR\/lib \$\{FFMPEG_STATIC_LINK_FLAGS\[\*\](:-)?\}"/u,
  );
  assert.match(build, /\$\{FFMPEG_STATIC_LINK_FLAGS\[\*\]:-\}/u);

  assert.match(checker, /FILE_ATTRIBUTE_REPARSE_POINT/u);
  assert.match(checker, /\["cmd\.exe", "\/d", "\/c", "mklink", "\/J"/u);
  assert.match(checker, /def plain_files_under\(root: Path\)/u);
  assert.match(checker, /args\.candidate\.absolute\(\)/u);
  assert.doesNotMatch(checker, /args\.candidate\.resolve\(\)/u);
});

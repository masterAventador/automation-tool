"""Caption rendering for the local editing pipeline.

The shipped ffmpeg is built without freetype and libass, so `drawtext`,
`subtitles` and `ass` are all unavailable. Captions are therefore drawn here
with PIL and handed to ffmpeg as transparent PNGs to overlay.

Nothing is re-exported on purpose. This package is imported by module path so
that the import graph -- which is the packaging boundary, the Executor spec
declares `excludes=[]` -- stays exactly as wide as the callers make it.
"""

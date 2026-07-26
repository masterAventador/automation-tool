"""One-sentence brand-motion authoring, hosted by the Local Executor.

The agent turns a typed sentence into a composition and a submittable RenderJob;
the entry is the short-lived process boundary the App calls it through.
"""

from automation_tool.executor.motion_authoring.entry import (
    MAX_REQUEST_BYTES,
    MotionAuthoringEntryRejected,
    SCHEMA_VERSION,
    run_motion_authoring_entry,
    serve_one_motion_authoring_request,
)

__all__ = [
    "MAX_REQUEST_BYTES",
    "MotionAuthoringEntryRejected",
    "SCHEMA_VERSION",
    "run_motion_authoring_entry",
    "serve_one_motion_authoring_request",
]

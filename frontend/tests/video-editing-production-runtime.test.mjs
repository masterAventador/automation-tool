import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("editing submission owns a real packaged Worker and durable Artifact bridge", async () => {
  const [library, runtime, controlPlane, workspace] = await Promise.all([
    readFile(new URL("src-tauri/src/lib.rs", root), "utf8"),
    readFile(new URL("src-tauri/src/local_editing_runtime.rs", root), "utf8"),
    readFile(new URL("src-tauri/src/control_plane.rs", root), "utf8"),
    readFile(new URL("src-tauri/src/video_job_workspace.rs", root), "utf8"),
  ]);

  assert.match(library, /pub mod local_editing_runtime;/u);
  assert.match(library, /local_editing_runtime::dispatch_submitted_job/u);
  assert.doesNotMatch(
    library,
    /dispatch_submitted_job[\s\S]{0,400}&&\s*local_editing_runtime::fail_submitted_job/u,
  );
  assert.match(
    library,
    /fail_submitted_job[\s\S]{0,700}"operation_unavailable"/u,
  );
  assert.match(runtime, /local-editing-render-request\.v1/u);
  assert.match(runtime, /material-video-worker(?:\\|\x2f)package/u);
  assert.match(runtime, /VideoWorkerMediaToolsConfiguration::new/u);
  assert.match(runtime, /scheduler\s*\.dispatch/u);
  assert.match(runtime, /poll_with_recovery/u);
  assert.match(runtime, /fail_worker_lost/u);
  assert.match(runtime, /import_output_with_id/u);
  assert.match(runtime, /reconcile_editing_job/u);
  assert.match(runtime, /get_editing_job/u);
  assert.match(controlPlane, /pub\(crate\) async fn get_editing_material/u);
  assert.match(controlPlane, /pub\(crate\) async fn reconcile_editing_job/u);
  assert.match(workspace, /pub fn import_output_with_id/u);
});

test("the packaged Python Worker is compiled with the production editing executor", async () => {
  const [worker, specification] = await Promise.all([
    readFile(new URL("../workers/material_montage/worker_main.py", root), "utf8"),
    readFile(
      new URL("../workers/material_montage/material-video-worker.spec", root),
      "utf8",
    ),
  ]);

  assert.match(worker, /execute_local_editing_job/u);
  assert.match(worker, /LocalEditingWorkerProtocol/u);
  assert.match(specification, /backend_source_root/u);
  assert.match(specification, /local_editing_worker_process/u);
});

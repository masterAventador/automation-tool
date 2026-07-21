import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("pause and resume acceptance uses the formal Rust bridge from one hidden App", async () => {
  const [packageJson, tauriConfigSource, wdioConfig, spec, rustClient, rustEntry] =
    await Promise.all([
      readProjectFile("package.json"),
      readProjectFile("src-tauri/tauri.task-control-e2e.conf.json"),
      readProjectFile("wdio.task-control.conf.ts"),
      readProjectFile("e2e-tauri/task-control.spec.ts"),
      readProjectFile("src-tauri/src/control_plane.rs"),
      readProjectFile("src-tauri/src/lib.rs"),
    ]);
  const tauriConfig = JSON.parse(tauriConfigSource);
  const orchestrator = await readFile(
    new URL("../../scripts/run_t3_13_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(packageJson, /test:task-control-tauri/);
  assert.match(packageJson, /build:tauri:task-control-test/);
  assert.equal(tauriConfig.app.windows.length, 1);
  assert.equal(tauriConfig.app.windows[0].visible, false);
  assert.equal(tauriConfig.identifier, "com.aventador.automationtool.t313acceptance");
  assert.match(wdioConfig, /specs:\s*\["\.\/e2e-tauri\/task-control\.spec\.ts"\]/);
  assert.match(spec, /core\.invoke\("control_task_for_acceptance"\)/);
  assert.match(rustClient, /pub async fn pause_task/);
  assert.match(rustClient, /pub async fn resume_task/);
  assert.match(rustClient, /pub async fn cancel_task/);
  assert.match(rustClient, /pub async fn emergency_stop_task/);
  assert.match(rustEntry, /\.pause_task\(\s*&vault/);
  assert.match(rustEntry, /\.resume_task\(\s*&vault/);
  assert.match(orchestrator, /test:task-control-tauri/);
  assert.match(orchestrator, /visible=false/);
});

test("H8-01 waits for the durable side-effect checkpoint through the real Executor", async () => {
  const [orchestrator, processor, ledger, runtime, spec] = await Promise.all([
    readFile(new URL("../../scripts/run_h8_01_acceptance.py", import.meta.url), "utf8"),
    readFile(
      new URL(
        "../../backend/src/automation_tool/executor/command_processor.py",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(
      new URL("../../backend/src/automation_tool/executor/ledger.py", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../../backend/src/automation_tool/executor/runtime.py", import.meta.url),
      "utf8",
    ),
    readProjectFile("e2e-tauri/task-control.spec.ts"),
  ]);

  assert.match(orchestrator, /test:task-control-tauri/);
  assert.match(orchestrator, /max_commands=1/);
  assert.match(orchestrator, /"-m",\s*"automation_tool\.executor"/);
  assert.match(orchestrator, /wait_for_pause_acknowledgement/);
  assert.match(orchestrator, /verify_side_effect/);
  assert.match(orchestrator, /pause request allowed a new side-effect dispatch/);
  assert.match(
    processor,
    /not in \{"task\.offer", "task\.pause", "task\.resume", "task\.cancel"\}/,
  );
  assert.match(processor, /def poll_controls/);
  assert.match(ledger, /c\.message_type = 'task\.pause'/);
  assert.match(ledger, /s\.state = 'dispatched'/);
  assert.match(ledger, /AttemptCheckpointState\.PAUSED/);
  assert.match(runtime, /self\._command_processor\.poll_controls\(\)/);
  assert.match(spec, /core\.invoke\("control_task_for_acceptance"\)/);
});

test("H8-02 converges cooperative cancellation from the original hidden App path", async () => {
  const [orchestrator, processor, ledger, spec] = await Promise.all([
    readFile(new URL("../../scripts/run_h8_02_acceptance.py", import.meta.url), "utf8"),
    readFile(
      new URL(
        "../../backend/src/automation_tool/executor/command_processor.py",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(
      new URL("../../backend/src/automation_tool/executor/ledger.py", import.meta.url),
      "utf8",
    ),
    readProjectFile("e2e-tauri/task-termination.spec.ts"),
  ]);

  assert.match(orchestrator, /test:task-termination-tauri/);
  assert.match(orchestrator, /AUTOMATION_TOOL_H802_CANCEL_OUTCOME_UNCERTAIN/);
  assert.match(orchestrator, /AUTOMATION_TOOL_TASK_TERMINATION_CONFIRMED_REVISION/);
  assert.match(orchestrator, /wait_for_cancel_acknowledgement/);
  assert.match(orchestrator, /mark_side_effect_uncertain/);
  assert.match(orchestrator, /cancel request allowed a new side-effect dispatch/);
  assert.match(orchestrator, /start_real_executor\(/);
  assert.match(processor, /"task\.cancel"/);
  assert.match(ledger, /task\.outcome_uncertain/);
  assert.match(ledger, /c\.message_type IN \('task\.pause', 'task\.cancel'\)/);
  assert.match(spec, /AUTOMATION_TOOL_H802_CANCEL_OUTCOME_UNCERTAIN/);
  assert.match(spec, /core\.invoke\("terminate_tasks_for_acceptance"\)/);
});

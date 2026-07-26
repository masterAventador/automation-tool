import assert from "node:assert/strict";
import { access, readdir, readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const frontendRoot = new URL("frontend/", repositoryRoot);
const specsRoot = new URL("e2e-tauri/", frontendRoot);
const scriptsRoot = new URL("scripts/", repositoryRoot);

async function readDirectorySources(root, predicate) {
  const names = (await readdir(root)).filter(predicate).sort();
  return new Map(
    await Promise.all(
      names.map(async (name) => [name, await readFile(new URL(name, root), "utf8")]),
    ),
  );
}

async function assertMissing(relativePath) {
  await assert.rejects(access(new URL(relativePath, frontendRoot)), { code: "ENOENT" });
}

async function assertMissingScript(fileName) {
  await assert.rejects(access(new URL(fileName, scriptsRoot)), { code: "ENOENT" });
}

function npmScriptRunsWdioConfig(script, config) {
  const escapedConfig = config.replaceAll(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  return new RegExp(
    `(?:^|&&|\\|\\||;)\\s*(?:pnpm\\s+exec\\s+)?wdio\\s+run\\s+${escapedConfig}(?:\\s|$)`,
    "u",
  ).test(script);
}

function pythonStringToken(value) {
  const escaped = value.replaceAll(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  return `(?:"${escaped}"|'${escaped}')`;
}

function withoutPythonComments(source) {
  return source
    .split("\n")
    .map((line) => {
      let quote = null;
      let escaped = false;
      for (let index = 0; index < line.length; index += 1) {
        const character = line[index];
        if (quote !== null) {
          if (escaped) {
            escaped = false;
          } else if (character === "\\") {
            escaped = true;
          } else if (character === quote) {
            quote = null;
          }
        } else if (character === "'" || character === '"') {
          quote = character;
        } else if (character === "#") {
          return line.slice(0, index);
        }
      }
      return line;
    })
    .join("\n");
}

function pythonCommandCallBodies(source) {
  const bodies = [];
  const callPattern = /(?:subprocess\.(?:run|Popen)|(?<![\w])_run)\s*\(/gu;
  for (const match of source.matchAll(callPattern)) {
    const lineStart = source.lastIndexOf("\n", match.index) + 1;
    if (/\bdef\s+$/u.test(source.slice(lineStart, match.index))) {
      continue;
    }
    const opening = source.indexOf("(", match.index);
    let depth = 1;
    let quote = null;
    let triple = false;
    let escaped = false;
    let comment = false;
    for (let index = opening + 1; index < source.length; index += 1) {
      const character = source[index];
      if (comment) {
        if (character === "\n") {
          comment = false;
        }
        continue;
      }
      if (quote !== null) {
        if (triple) {
          if (source.slice(index, index + 3) === quote.repeat(3)) {
            quote = null;
            triple = false;
            index += 2;
          }
        } else if (escaped) {
          escaped = false;
        } else if (character === "\\") {
          escaped = true;
        } else if (character === quote) {
          quote = null;
        }
        continue;
      }
      if (character === "#") {
        comment = true;
      } else if (character === "'" || character === '"') {
        quote = character;
        triple = source.slice(index, index + 3) === character.repeat(3);
        if (triple) {
          index += 2;
        }
      } else if (character === "(") {
        depth += 1;
      } else if (character === ")") {
        depth -= 1;
        if (depth === 0) {
          bodies.push(withoutPythonComments(source.slice(opening + 1, index)));
          break;
        }
      }
    }
  }
  return bodies;
}

function pythonFunctionSource(source, name) {
  const escapedName = name.replaceAll(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  const start = source.search(new RegExp(`^def\\s+${escapedName}\\s*\\(`, "mu"));
  if (start === -1) {
    return "";
  }
  const remainder = source.slice(start + 1);
  const nextDefinition = remainder.search(/^(?:async\s+def|def|class)\s+/mu);
  return nextDefinition === -1 ? source.slice(start) : source.slice(start, start + 1 + nextDefinition);
}

function pythonCommandResolvesConfig(source, body, config, relatedSources = [source]) {
  const wdioRun = `${pythonStringToken("wdio")}\\s*,\\s*${pythonStringToken("run")}`;
  if (new RegExp(`${wdioRun}\\s*,\\s*${pythonStringToken(config)}`, "u").test(body)) {
    return true;
  }
  const dynamic = body.match(new RegExp(`${wdioRun}\\s*,\\s*([A-Za-z_]\\w*)`, "u"));
  if (dynamic !== null) {
    const variable = dynamic[1];
    const escapedVariable = variable.replaceAll(/[.*+?^${}()|[\]\\]/gu, "\\$&");
    const boundValue = new RegExp(
      `\\b${escapedVariable}\\b\\s*(?::[^=,)]+)?=\\s*${pythonStringToken(config)}`,
      "u",
    );
    if (relatedSources.some((candidate) => boundValue.test(candidate))) {
      return true;
    }
  }
  const helper = body.match(/\*([A-Za-z_]\w*)\(\)/u);
  if (helper === null) {
    return false;
  }
  const helperSource = pythonFunctionSource(source, helper[1]);
  return new RegExp(`${wdioRun}\\s*,\\s*${pythonStringToken(config)}`, "u").test(
    helperSource,
  );
}

function pythonSourceRunsWdioConfig(source, config, relatedSources = [source]) {
  return pythonCommandCallBodies(source).some((body) =>
    pythonCommandResolvesConfig(source, body, config, relatedSources),
  );
}

function pythonSourceOverridesConfigSpecs(source, config, relatedSources = [source]) {
  const specToken = pythonStringToken("--spec");
  return pythonCommandCallBodies(source).some((body) => {
    if (!pythonCommandResolvesConfig(source, body, config, relatedSources)) {
      return false;
    }
    if (new RegExp(specToken, "u").test(body)) {
      return true;
    }
    const helper = body.match(/\*([A-Za-z_]\w*)\(\)/u);
    if (helper !== null && new RegExp(specToken, "u").test(pythonFunctionSource(source, helper[1]))) {
      return true;
    }
    return /\*[A-Za-z_]\w*/u.test(body) && new RegExp(specToken, "u").test(source);
  });
}

function pythonCollectionContainsSpec(source, collection, specPath) {
  const escapedCollection = collection.replaceAll(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  const assignment = source.match(
    new RegExp(
      `^${escapedCollection}\\s*(?::[^=\\n]+)?=\\s*\\(([\\s\\S]{0,1600}?)\\)`,
      "mu",
    ),
  );
  return assignment !== null && new RegExp(pythonStringToken(specPath), "u").test(assignment[1]);
}

function pythonFragmentSelectsSpec(source, fragment, specPath) {
  const specFlag = pythonStringToken("--spec");
  if (
    new RegExp(`${specFlag}\\s*,\\s*${pythonStringToken(specPath)}`, "u").test(fragment)
  ) {
    return true;
  }
  const variable = fragment.match(new RegExp(`${specFlag}\\s*,\\s*([A-Za-z_]\\w*)`, "u"));
  if (variable !== null) {
    const escapedVariable = variable[1].replaceAll(/[.*+?^${}()|[\]\\]/gu, "\\$&");
    if (
      new RegExp(
        `^${escapedVariable}\\s*(?::[^=\\n]+)?=\\s*${pythonStringToken(specPath)}`,
        "mu",
      ).test(source)
    ) {
      return true;
    }
  }
  const specLoop = source.match(
    new RegExp(
      `for\\s+([A-Za-z_]\\w*)\\s+in\\s+([A-Za-z_]\\w*)\\s*:[\\s\\S]{0,500}?` +
        `\\.extend\\(\\[\\s*${specFlag}\\s*,\\s*\\1\\s*\\]\\)`,
      "u",
    ),
  );
  return (
    specLoop !== null && pythonCollectionContainsSpec(source, specLoop[2], specPath)
  );
}

function pythonSourceRunsExplicitSpec(
  source,
  configNames,
  spec,
  relatedSources = [source],
) {
  const specPath = `./e2e-tauri/${spec}`;
  return pythonCommandCallBodies(source).some((body) =>
    configNames.some((config) => {
      if (!pythonCommandResolvesConfig(source, body, config, relatedSources)) {
        return false;
      }
      if (pythonFragmentSelectsSpec(source, body, specPath)) {
        return true;
      }
      const helper = body.match(/\*([A-Za-z_]\w*)\(\)/u);
      return (
        helper !== null &&
        pythonFragmentSelectsSpec(
          source,
          pythonFunctionSource(source, helper[1]),
          specPath,
        )
      );
    }),
  );
}

function configDeclaresSpec(source, spec) {
  const uncommentedSource = source
    .replaceAll(/\/\*[\s\S]*?\*\//gu, "")
    .replaceAll(/\/\/[^\n]*/gu, "");
  const specs = uncommentedSource.match(/\bspecs\s*:\s*\[([\s\S]*?)\]/u);
  if (specs === null) {
    return false;
  }
  const declared = [...specs[1].matchAll(/["']([^"']+)["']/gu)].map(
    (match) => match[1],
  );
  return declared.includes(`./e2e-tauri/${spec}`);
}

test("every retained Tauri WDIO entry has a deterministic executor", async () => {
  const [packageText, configs, specs, runners] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readDirectorySources(frontendRoot, (name) => /^wdio.*\.conf\.ts$/u.test(name)),
    readDirectorySources(specsRoot, (name) => name.endsWith(".spec.ts")),
    readDirectorySources(
      scriptsRoot,
      (name) => /^run_.*_acceptance\.py$/u.test(name),
    ),
  ]);
  const packageJson = JSON.parse(packageText);
  const npmScripts = Object.values(packageJson.scripts);
  const runnerSources = [...runners.values()];
  const configNames = [...configs.keys()];
  const ownedConfigs = new Set(
    configNames.filter(
      (name) =>
        npmScripts.some((script) => npmScriptRunsWdioConfig(script, name)) ||
        runnerSources.some((source) =>
          pythonSourceRunsWdioConfig(source, name, runnerSources),
        ),
    ),
  );

  assert.deepEqual(
    configNames.filter((name) => !ownedConfigs.has(name)),
    [],
    "a WDIO config without an npm command or Python acceptance runner can never execute",
  );

  const unownedSpecs = [...specs.keys()].filter((name) => {
    const defaultConfigOwner = [...configs].some(([configName, source]) => {
      if (!configDeclaresSpec(source, name)) {
        return false;
      }
      const npmRunsDefault = npmScripts.some((script) =>
        npmScriptRunsWdioConfig(script, configName),
      );
      const runnerRunsDefault = runnerSources.some(
        (runner) =>
          pythonSourceRunsWdioConfig(runner, configName, runnerSources) &&
          !pythonSourceOverridesConfigSpecs(runner, configName, runnerSources),
      );
      return npmRunsDefault || runnerRunsDefault;
    });
    const explicitRunnerOwner = runnerSources.some(
      (source) =>
        pythonSourceRunsExplicitSpec(source, configNames, name, runnerSources),
    );
    return !defaultConfigOwner && !explicitRunnerOwner;
  });
  assert.deepEqual(
    unownedSpecs,
    [],
    "a retained WDIO spec must be selected by an executable config or Python runner",
  );
});

test("comments and unrelated constants are not executable WDIO ownership", () => {
  const config = "wdio.example.conf.ts";
  const spec = "example.spec.ts";
  const noise =
    "# subprocess.run wdio run wdio.example.conf.ts --spec example.spec.ts\n" +
    'CONFIG = "wdio.example.conf.ts"\n' +
    'SPEC = "example.spec.ts"\n' +
    'WORDS = ("wdio", "--spec")\n';

  assert.equal(npmScriptRunsWdioConfig(`echo wdio ${config}`, config), false);
  assert.equal(pythonSourceRunsWdioConfig(noise, config), false);
  assert.equal(pythonSourceRunsExplicitSpec(noise, [config], spec), false);
});

test("dynamic arguments and real calls do not own unrelated config or spec constants", () => {
  const config = "wdio.orphan.conf.ts";
  const dynamicConfigNoise =
    "def launch(selected):\n" +
    '    subprocess.run(["pnpm", "exec", "wdio", "run", selected])\n' +
    'UNUSED = "wdio.orphan.conf.ts"\n';
  const unrelatedSpecNoise =
    'subprocess.run(["pnpm", "exec", "wdio", "run", "wdio.real.conf.ts", ' +
    '"--spec", "./e2e-tauri/actual.spec.ts"])\n' +
    'ORPHAN = "./e2e-tauri/orphan.spec.ts"\n';
  const configComment =
    "export const config = {\n" +
    "  // specs: [\"./e2e-tauri/orphan.spec.ts\"],\n" +
    '  specs: ["./e2e-tauri/actual.spec.ts"],\n' +
    "};\n";

  assert.equal(pythonSourceRunsWdioConfig(dynamicConfigNoise, config), false);
  assert.equal(
    pythonSourceRunsExplicitSpec(unrelatedSpecNoise, ["wdio.real.conf.ts"], "orphan.spec.ts"),
    false,
  );
  assert.equal(configDeclaresSpec(configComment, "orphan.spec.ts"), false);
});

test("the default desktop layer uses a standalone production-composed entry", async () => {
  const packageJson = JSON.parse(await readFile(new URL("package.json", frontendRoot), "utf8"));

  assert.equal(packageJson.scripts["test:tauri"], "pnpm test:publishing-tauri");
  assert.equal(packageJson.scripts["build:tauri:test"], undefined);
  await Promise.all([
    assertMissing("wdio.conf.ts"),
    assertMissing("e2e-tauri/workbench.spec.ts"),
  ]);
});

test("the video-studio family builds the production Control Plane path", async () => {
  const packageJson = JSON.parse(await readFile(new URL("package.json", frontendRoot), "utf8"));
  const build = packageJson.scripts["build:tauri:video-studio-test"];

  assert.match(build, /--features control-plane-e2e(?:\s|,)/u);
  assert.doesNotMatch(build, /--features video-studio-e2e(?:\s|,)/u);
});

test("the zero-click H8-19 WDIO surrogate stays retired", async () => {
  const packageJson = JSON.parse(await readFile(new URL("package.json", frontendRoot), "utf8"));

  assert.equal(packageJson.scripts["build:tauri:update-policy-test"], undefined);
  assert.equal(packageJson.scripts["test:h8-19-app"], undefined);
  await Promise.all([
    assertMissing("wdio.update-policy.conf.ts"),
    assertMissing("e2e-tauri/update-policy.spec.ts"),
  ]);
});

// Retiring an entry leaves a dangling reference behind whenever the executor is
// cleaned up second. The ownership test above only walks entries that still
// exist, so it cannot see a runner that builds an App and then invokes a config
// nobody ships any more; that failure only shows up in a real desktop run.
test("no executor points at a WDIO entry that no longer exists", async () => {
  const [packageText, configs, specs, scriptSources] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readDirectorySources(frontendRoot, (name) => /^wdio.*\.conf\.ts$/u.test(name)),
    readdir(specsRoot),
    readDirectorySources(scriptsRoot, (name) => name.endsWith(".py")),
  ]);
  const configNames = new Set(configs.keys());
  const specNames = new Set(specs.filter((name) => name.endsWith(".spec.ts")));
  const sources = [
    ...Object.entries(JSON.parse(packageText).scripts).map(
      ([name, script]) => [`package.json:${name}`, script],
    ),
    // `scripts/test_*.py` build synthetic trees whose runner and config names are
    // deliberately fictional, so they are named here rather than scanned.
    ...[...scriptSources]
      .filter(([name]) => !name.startsWith("test_"))
      .map(([name, source]) => [`scripts/${name}`, source]),
    ...[...configs].map(([name, source]) => [`frontend/${name}`, source]),
  ];

  const runnerNames = new Set(scriptSources.keys());
  const dangling = new Set();
  for (const [origin, source] of sources) {
    for (const [reference] of source.matchAll(/wdio\.[\w.-]+\.conf\.ts/gu)) {
      if (!configNames.has(reference)) dangling.add(`${origin} -> ${reference}`);
    }
    for (const [, reference] of source.matchAll(/e2e-tauri\/([\w.-]+\.spec\.ts)/gu)) {
      if (!specNames.has(reference)) dangling.add(`${origin} -> ${reference}`);
    }
    for (const [, reference] of source.matchAll(/(?:^|[\s/'"])(run_\w+\.py)/gu)) {
      if (!runnerNames.has(reference)) dangling.add(`${origin} -> ${reference}`);
    }
    for (const [, imported] of source.matchAll(/^from\s+(run_\w+)\s+import/gmu)) {
      if (!runnerNames.has(`${imported}.py`)) dangling.add(`${origin} -> ${imported}.py`);
    }
  }
  assert.deepEqual([...dangling], [], "an executor still names a deleted entry");
});

// EB-10 (`f34e503`, 2026-07-24) deleted the whole user path this acceptance
// drove, because CLAUDE.md §5 forbids discovering or selecting a system
// browser. The spec has failed in every real desktop run since. Its scaffolding
// — Vite mode, stubbed entry module, Tauri config, npm scripts, Python runner —
// only ever existed to serve it, so it goes with it.
test("the browser-selection acceptance is retired with the product path it drove", async () => {
  const [packageText, viteConfig, globalStyles, startupEnvironment] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("vite.config.ts", frontendRoot), "utf8"),
    readFile(new URL("src/styles/global.css", frontendRoot), "utf8"),
    readFile(new URL("e2e-tauri/startup-environment.spec.ts", frontendRoot), "utf8"),
  ]);
  const { scripts } = JSON.parse(packageText);

  for (const name of [
    "test:browser-settings-tauri",
    "build:tauri:browser-settings-test",
    "build:browser-settings-e2e-assets",
  ]) {
    assert.equal(scripts[name], undefined);
  }
  assert.doesNotMatch(viteConfig, /browser-settings-e2e/u);
  assert.doesNotMatch(globalStyles, /browser-settings-card/u);
  // The absence itself stays asserted, by the one spec written after EB-10.
  assert.match(startupEnvironment, /browser-settings-card/u);
  await Promise.all([
    assertMissing("wdio.browser-settings.conf.ts"),
    assertMissing("e2e-tauri/browser-settings.spec.ts"),
    assertMissing("src/test-browser-settings-main.tsx"),
    assertMissing("src-tauri/tauri.browser-settings-e2e.conf.json"),
    assertMissingScript("run_b5_04_acceptance.py"),
  ]);
});

// `update-installation.spec.ts` and `update-ui.spec.ts` were driven by the same
// runner, the same App build and the same production feed, over the same three
// scenarios. The only difference was that one invoked `decide_app_update`
// directly while the other clicked the buttons a user clicks — and CLAUDE.md §8
// says a direct Command call is layered evidence, never acceptance. That makes
// the zero-click spec a strictly weaker duplicate, and `run_h8_22_acceptance.py`
// a second name for one run.
test("the zero-click installation surrogate gives way to the clicking update UI", async () => {
  const [packageText, runner] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("run_h8_21_acceptance.py", scriptsRoot), "utf8"),
  ]);
  const { scripts } = JSON.parse(packageText);

  assert.equal(scripts["test:h8-22-app"], undefined);
  assert.notEqual(scripts["test:h8-21-app"], undefined);
  // The shared hidden App build survives: it is what the surviving spec runs on.
  assert.notEqual(scripts["build:tauri:update-installation-test"], undefined);
  assert.match(runner, /wdio\.update-ui\.conf\.ts/u);
  assert.doesNotMatch(runner, /wdio\.update-installation\.conf\.ts/u);
  await Promise.all([
    assertMissing("wdio.update-installation.conf.ts"),
    assertMissing("e2e-tauri/update-installation.spec.ts"),
    assertMissingScript("run_h8_22_acceptance.py"),
  ]);
});

// H8-20 keeps its own spec because nothing else interrupts a download: the
// resumable transfer, the retryable failure and the cache convergence are only
// reachable through it. What it may not keep is triggering the recovery over
// IPC, so the action moves onto the button in 设置与诊断. The one assertion the
// retired H8-21 spec did not share — that the update flow opens no second
// window — moves onto the surviving update UI spec.
test("every retained update acceptance decides through visible App controls", async () => {
  const [download, ui] = await Promise.all([
    readFile(new URL("e2e-tauri/update-download.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("e2e-tauri/update-ui.spec.ts", frontendRoot), "utf8"),
  ]);

  assert.match(download, /button=检查更新/u);
  assert.doesNotMatch(download, /invoke\("check_app_update_now"\)/u);
  assert.doesNotMatch(download, /invoke\("decide_app_update"/u);
  assert.match(ui, /listWindows\(\)/u);
});

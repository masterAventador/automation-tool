/**
 * Guard: the cloud-editing credential UI and its Tauri bridge are gone.
 *
 * LE-01 removed the Aliyun route. These files configured vendor credentials for
 * a service the product never reached — `main.tsx` handed the workbench a
 * sessionStorage draft gateway and submission always threw. Their return means
 * someone is rebuilding that layer.
 */

import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const PROJECT_ROOT = resolve(__dirname, "../..");

const REMOVED_FILES = [
  "src/features/settings/VideoEditingServiceSettings.tsx",
  "src/features/settings/VideoEditingServiceSettings.test.tsx",
  "src/features/settings/video-editing-service-gateway.ts",
  "src/platform/tauri/video-editing-service-gateway.ts",
  "src/platform/tauri/video-editing-service-gateway.test.ts",
  "src/features/video-editing/provider-replaceability.test.tsx",
  "e2e-tauri/video-editing-service.spec.ts",
] as const;

const RETAINED_FILES = [
  "src/features/video-editing/VideoEditingWorkbench.tsx",
  "src/features/video-editing/video-editing-gateway.ts",
  "src/app/production-wiring.test.ts",
] as const;

describe("cloud editing removal", () => {
  it.each(REMOVED_FILES)("%s is gone", (relativePath) => {
    expect(existsSync(resolve(PROJECT_ROOT, relativePath))).toBe(false);
  });

  it.each(RETAINED_FILES)("%s is kept for LE-17 to rewrite", (relativePath) => {
    expect(existsSync(resolve(PROJECT_ROOT, relativePath))).toBe(true);
  });
});

import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import {
  MaterialLibraryGatewayError,
  type EditingMaterialSnapshot,
} from "../../features/video-editing/material-library-gateway";
import { TauriMaterialLibraryGateway } from "./material-library-gateway";

const MATERIAL_ID = "9f48954d-2df1-4168-8f33-b62c5772845b";
const SECOND_MATERIAL_ID = "af48954d-2df1-4168-8f33-b62c5772845c";
const CAPABILITY = `material-preview-v1-${"A".repeat(43)}`;

function material(materialId = MATERIAL_ID): EditingMaterialSnapshot {
  return {
    materialId,
    kind: "video",
    durationMs: 3_000,
    width: 720,
    height: 1280,
    contentDigest: "ab".repeat(32),
    hasAudio: true,
    audioLoudnessLufs: -18.25,
    hasSpeech: true,
    speechSegmentsMs: [[200, 1_100]],
    speechTranscript: "今天发布新功能",
    shotBoundariesMs: [1_500],
    aiDescription: "产品发布会镜头",
    aiTags: ["发布会", "产品"],
    descriptionSource: "ai",
    describedAt: "2026-08-01T00:00:00Z",
  };
}

describe("Tauri material library gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("reads one validated material page without exposing paths", async () => {
    invoke.mockResolvedValueOnce({ items: [material()], nextCursor: "next_page" });

    await expect(new TauriMaterialLibraryGateway().listMaterials(null)).resolves.toEqual({
      items: [material()],
      nextCursor: "next_page",
    });
    expect(invoke).toHaveBeenCalledWith("list_editing_materials", {
      cursor: null,
      limit: 50,
    });
  });

  it.each([
    [{ ...material(), privatePath: "/Users/private/source.mp4" }],
    [{ ...material(), materialId: "not-a-uuid" }],
    [{ ...material(), durationMs: 0 }],
    [{ ...material(), kind: "audio", width: 1, height: null }],
    [{ ...material(), speechSegmentsMs: [[1_100, 200]] }],
    [{ ...material(), aiTags: ["重复", "重复"] }],
    [{ ...material(), descriptionSource: "user", aiTags: ["旧标签"], describedAt: null }],
  ])("rejects malformed or expanded material snapshots", async (value) => {
    invoke.mockResolvedValueOnce({ items: [value], nextCursor: null });
    await expect(new TauriMaterialLibraryGateway().listMaterials(null)).rejects.toMatchObject({
      code: "material_service_unavailable",
      retryable: false,
    });
  });

  it("rejects invalid cursors and duplicate identities in a page", async () => {
    const gateway = new TauriMaterialLibraryGateway();
    await expect(gateway.listMaterials("../private")).rejects.toMatchObject({
      code: "invalid_material",
      retryable: false,
    });
    expect(invoke).not.toHaveBeenCalled();

    invoke.mockResolvedValueOnce({
      items: [material(), material()],
      nextCursor: null,
    });
    await expect(gateway.listMaterials(null)).rejects.toMatchObject({
      code: "material_service_unavailable",
      retryable: false,
    });
  });

  it("imports through the path-free picker command and preserves dedupe", async () => {
    invoke.mockResolvedValueOnce({ material: material(), deduplicated: true });
    await expect(new TauriMaterialLibraryGateway().importMaterial()).resolves.toEqual({
      material: material(),
      deduplicated: true,
    });
    expect(invoke).toHaveBeenCalledWith("import_editing_material", {});

    invoke.mockResolvedValueOnce(null);
    await expect(new TauriMaterialLibraryGateway().importMaterial()).resolves.toBeNull();
  });

  it("uses exact material commands and validates status, preview URL and mutation reply", async () => {
    const previewUrl = `http://127.0.0.1:43123/api/v1/material-previews/${CAPABILITY}/${MATERIAL_ID}`;
    invoke
      .mockResolvedValueOnce("file_missing")
      .mockResolvedValueOnce(previewUrl)
      .mockResolvedValueOnce({
        ...material(),
        aiDescription: "人工说明",
        aiTags: [],
        descriptionSource: "user",
        describedAt: null,
      })
      .mockResolvedValueOnce(undefined);
    const gateway = new TauriMaterialLibraryGateway();

    await expect(gateway.getMaterialStatus(MATERIAL_ID)).resolves.toBe("file_missing");
    await expect(gateway.getMaterialPreviewUrl(MATERIAL_ID)).resolves.toBe(previewUrl);
    await expect(gateway.updateMaterialDescription(MATERIAL_ID, "人工说明")).resolves.toMatchObject({
      materialId: MATERIAL_ID,
      aiDescription: "人工说明",
      descriptionSource: "user",
    });
    await expect(gateway.deleteMaterial(MATERIAL_ID)).resolves.toBeUndefined();
    expect(invoke.mock.calls).toEqual([
      ["get_local_editing_material_status", { materialId: MATERIAL_ID }],
      ["get_local_editing_material_preview_url", { materialId: MATERIAL_ID }],
      ["update_editing_material_description", { materialId: MATERIAL_ID, description: "人工说明" }],
      ["delete_editing_material", { materialId: MATERIAL_ID }],
    ]);
  });

  it.each([
    "available/../../private",
    "AVAILABLE",
    null,
  ])("rejects a non-closed local status", async (status) => {
    invoke.mockResolvedValueOnce(status);
    await expect(new TauriMaterialLibraryGateway().getMaterialStatus(MATERIAL_ID)).rejects.toMatchObject({
      code: "material_service_unavailable",
      retryable: false,
    });
  });

  it.each([
    `file:///Users/private/${MATERIAL_ID}.mp4`,
    `http://localhost:43123/api/v1/material-previews/${CAPABILITY}/${MATERIAL_ID}`,
    `http://127.0.0.1:43123/api/v1/material-previews/${CAPABILITY}/${SECOND_MATERIAL_ID}`,
    `http://127.0.0.1:43123/api/v1/material-previews/${CAPABILITY}/${MATERIAL_ID}?path=private`,
  ])("rejects preview URLs outside the exact loopback capability boundary", async (url) => {
    invoke.mockResolvedValueOnce(url);
    const error = await new TauriMaterialLibraryGateway()
      .getMaterialPreviewUrl(MATERIAL_ID)
      .catch((value: unknown) => value);
    expect(error).toBeInstanceOf(MaterialLibraryGatewayError);
    expect(error).toMatchObject({ code: "material_service_unavailable", retryable: false });
    expect(String(error)).not.toContain(url);
  });

  it("maps the closed import failures without reflecting native details", async () => {
    invoke.mockRejectedValueOnce({
      code: "source_not_at_rest",
      retryable: true,
      message: "/Users/private/source.mp4",
    });
    await expect(new TauriMaterialLibraryGateway().importMaterial()).rejects.toMatchObject({
      code: "source_not_at_rest",
      retryable: true,
    });

    invoke.mockRejectedValueOnce({
      code: "private_native_failure",
      retryable: true,
      message: "/Users/private/source.mp4",
    });
    const error = await new TauriMaterialLibraryGateway().importMaterial().catch((value) => value);
    expect(error).toMatchObject({ code: "material_service_unavailable", retryable: false });
    expect(String(error)).not.toContain("private");
  });

  it("preserves retryability for closed Control Plane availability failures", async () => {
    invoke.mockRejectedValueOnce({ code: "transport_unavailable", retryable: true });
    await expect(new TauriMaterialLibraryGateway().listMaterials(null)).rejects.toMatchObject({
      code: "material_service_unavailable",
      retryable: true,
    });
  });

  it("counts user descriptions by Unicode scalar and permits contract layout whitespace", async () => {
    const description = `${"😀".repeat(1_998)}\n终`;
    invoke.mockResolvedValueOnce({
      ...material(),
      aiDescription: description,
      aiTags: [],
      descriptionSource: "user",
      describedAt: null,
    });
    await expect(
      new TauriMaterialLibraryGateway().updateMaterialDescription(MATERIAL_ID, description),
    ).resolves.toMatchObject({ aiDescription: description });
  });
});

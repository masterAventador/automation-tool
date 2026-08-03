import { invoke } from "@tauri-apps/api/core";

import {
  MAX_MATERIAL_DESCRIPTION_CHARACTERS,
  MaterialLibraryGatewayError,
  editingMaterialPageSchema,
  editingMaterialSchema,
  localMaterialStatusSchema,
  materialCursorSchema,
  materialImportOutcomeSchema,
  type EditingMaterialPage,
  type EditingMaterialSnapshot,
  type LocalMaterialStatus,
  type MaterialImportOutcome,
  type MaterialLibraryErrorCode,
  type MaterialLibraryGateway,
} from "../../features/video-editing/material-library-gateway";
import { editingResourceIdSchema } from "../../features/video-editing/video-editing-dto";
import { nativeCommandErrorFields } from "./native-command-error";

const PAGE_LIMIT = 50;
const CAPABILITY = /^material-preview-v1-[A-Za-z0-9_-]{43}$/u;
const CLOSED_NATIVE_CODES = new Set<MaterialLibraryErrorCode>([
  "outcome_uncertain",
  "unreadable",
  "source_not_at_rest",
  "unsafe_path",
  "undecodable",
  "no_usable_stream",
  "unusable_duration",
  "too_long",
  "unusable_frame_size",
  "frame_too_large",
  "file_too_large",
  "silent_audio",
  "probe_crashed",
  "probe_failed",
  "workspace_unusable",
  "source_changed",
  "compensation_failed",
  "file_missing",
  "file_unreadable",
  "file_changed",
  "registry_unreadable",
  "registry_unwritable",
  "registry_full",
  "operation_in_progress",
  "timed_out",
  "worker_unavailable",
]);
const SERVICE_NATIVE_CODES = new Set([
  "transport_unavailable",
  "credential_missing",
  "identity_unavailable",
  "storage_unavailable",
  "installation_access_denied",
  "installation_busy",
  "installation_conflict",
  "operation_unavailable",
  "control_plane_rejected",
]);

function invalid(): MaterialLibraryGatewayError {
  return new MaterialLibraryGatewayError("invalid_material", false);
}

function unavailable(retryable = false): MaterialLibraryGatewayError {
  return new MaterialLibraryGatewayError("material_service_unavailable", retryable);
}

function requireMaterialId(materialId: string): string {
  const parsed = editingResourceIdSchema.safeParse(materialId);
  if (!parsed.success) throw invalid();
  return parsed.data;
}

function mapNativeError(value: unknown): MaterialLibraryGatewayError {
  const fields = nativeCommandErrorFields(value);
  if (fields !== undefined && CLOSED_NATIVE_CODES.has(fields.code as MaterialLibraryErrorCode)) {
    return new MaterialLibraryGatewayError(fields.code as MaterialLibraryErrorCode, fields.retryable);
  }
  if (fields !== undefined && SERVICE_NATIVE_CODES.has(fields.code)) {
    return unavailable(fields.retryable);
  }
  return unavailable();
}

async function safeInvoke(command: string, args: Record<string, unknown>): Promise<unknown> {
  try {
    return await invoke<unknown>(command, args);
  } catch (error) {
    throw mapNativeError(error);
  }
}

function parseMaterial(value: unknown): EditingMaterialSnapshot {
  const parsed = editingMaterialSchema.safeParse(value);
  if (!parsed.success) throw unavailable();
  return parsed.data;
}

function parsePreviewUrl(value: unknown, materialId: string): string {
  if (typeof value !== "string") throw unavailable();
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw unavailable();
  }
  const segments = url.pathname.split("/");
  if (
    url.protocol !== "http:" ||
    url.hostname !== "127.0.0.1" ||
    url.port === "" ||
    url.username !== "" ||
    url.password !== "" ||
    url.search !== "" ||
    url.hash !== "" ||
    segments.length !== 6 ||
    segments[1] !== "api" ||
    segments[2] !== "v1" ||
    segments[3] !== "material-previews" ||
    !CAPABILITY.test(segments[4] ?? "") ||
    segments[5] !== materialId ||
    url.toString() !== value
  ) {
    throw unavailable();
  }
  return value;
}

export class TauriMaterialLibraryGateway implements MaterialLibraryGateway {
  async listMaterials(cursor: string | null): Promise<EditingMaterialPage> {
    if (cursor !== null && !materialCursorSchema.safeParse(cursor).success) throw invalid();
    const response = await safeInvoke("list_editing_materials", { cursor, limit: PAGE_LIMIT });
    const parsed = editingMaterialPageSchema.safeParse(response);
    if (!parsed.success) throw unavailable();
    return parsed.data;
  }

  async importMaterial(): Promise<MaterialImportOutcome | null> {
    const response = await safeInvoke("import_editing_material", {});
    if (response === null) return null;
    const parsed = materialImportOutcomeSchema.safeParse(response);
    if (!parsed.success) throw unavailable();
    return parsed.data;
  }

  async getMaterialStatus(materialId: string): Promise<LocalMaterialStatus> {
    const id = requireMaterialId(materialId);
    const parsed = localMaterialStatusSchema.safeParse(
      await safeInvoke("get_local_editing_material_status", { materialId: id }),
    );
    if (!parsed.success) throw unavailable();
    return parsed.data;
  }

  async getMaterialPreviewUrl(materialId: string): Promise<string> {
    const id = requireMaterialId(materialId);
    return parsePreviewUrl(
      await safeInvoke("get_local_editing_material_preview_url", { materialId: id }),
      id,
    );
  }

  async updateMaterialDescription(
    materialId: string,
    description: string,
  ): Promise<EditingMaterialSnapshot> {
    const id = requireMaterialId(materialId);
    if (
      [...description].length === 0 ||
      [...description].length > MAX_MATERIAL_DESCRIPTION_CHARACTERS ||
      description !== description.trim() ||
      [...description].some(
        (character) =>
          character !== "\n" && character !== "\t" && /\p{C}/u.test(character),
      )
    ) {
      throw invalid();
    }
    const material = parseMaterial(
      await safeInvoke("update_editing_material_description", {
        materialId: id,
        description,
      }),
    );
    if (
      material.materialId !== id ||
      material.aiDescription !== description ||
      material.descriptionSource !== "user"
    ) {
      throw unavailable();
    }
    return material;
  }

  async deleteMaterial(materialId: string): Promise<void> {
    const id = requireMaterialId(materialId);
    const response = await safeInvoke("delete_editing_material", { materialId: id });
    if (response !== null && response !== undefined) throw unavailable();
  }
}

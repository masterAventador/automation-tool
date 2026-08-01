import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  MaterialLibraryGatewayError,
  type EditingMaterialSnapshot,
  type MaterialLibraryGateway,
} from "./material-library-gateway";
import { MaterialLibraryPage } from "./MaterialLibraryPage";

const VIDEO_ID = "9f48954d-2df1-4168-8f33-b62c5772845b";
const IMAGE_ID = "af48954d-2df1-4168-8f33-b62c5772845c";
const AUDIO_ID = "bf48954d-2df1-4168-8f33-b62c5772845d";
const CAPABILITY = `material-preview-v1-${"A".repeat(43)}`;

function material(
  overrides: Partial<EditingMaterialSnapshot> = {},
): EditingMaterialSnapshot {
  return {
    materialId: VIDEO_ID,
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
    ...overrides,
  };
}

function gateway(overrides: Partial<MaterialLibraryGateway> = {}): MaterialLibraryGateway {
  return {
    async listMaterials() {
      return { items: [], nextCursor: null };
    },
    async importMaterial() {
      return null;
    },
    async getMaterialStatus() {
      return "available";
    },
    async getMaterialPreviewUrl(materialId) {
      return `http://127.0.0.1:43123/api/v1/material-previews/${CAPABILITY}/${materialId}`;
    },
    async updateMaterialDescription(_materialId, description) {
      return material({
        aiDescription: description,
        aiTags: [],
        descriptionSource: "user",
        describedAt: null,
      });
    },
    async deleteMaterial() {},
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("material library page", () => {
  it("shows honest loading, empty and retryable error states", async () => {
    const pending = deferred<{ items: []; nextCursor: null }>();
    const listMaterials = vi
      .fn<MaterialLibraryGateway["listMaterials"]>()
      .mockImplementationOnce(() => pending.promise)
      .mockRejectedValueOnce(new MaterialLibraryGatewayError("material_service_unavailable", true))
      .mockResolvedValueOnce({ items: [], nextCursor: null });
    const user = userEvent.setup();
    render(<MaterialLibraryPage gateway={gateway({ listMaterials })} />);

    expect(await screen.findByText("正在读取本机素材库…")).toBeVisible();
    await act(async () => pending.resolve({ items: [], nextCursor: null }));
    expect(await screen.findByText("还没有本机素材")).toBeVisible();

    await user.click(screen.getByRole("button", { name: /刷新素材库/u }));
    expect(await screen.findByText("暂时读不到本机素材库，请稍后重试。")).toBeVisible();
    expect(screen.getByText("还没有本机素材")).toBeVisible();

    await user.click(screen.getByRole("button", { name: /刷新素材库/u }));
    await waitFor(() => expect(listMaterials).toHaveBeenCalledTimes(3));
    expect(screen.queryByText("暂时读不到本机素材库，请稍后重试。")).not.toBeInTheDocument();
  });

  it("paginates without duplicating cards and refreshes local status", async () => {
    const first = material();
    const second = material({
      materialId: IMAGE_ID,
      kind: "image",
      durationMs: null,
      width: 1200,
      height: 800,
      hasAudio: false,
      audioLoudnessLufs: null,
      hasSpeech: false,
      speechSegmentsMs: [],
      speechTranscript: null,
      shotBoundariesMs: [],
    });
    const listMaterials = vi
      .fn<MaterialLibraryGateway["listMaterials"]>()
      .mockResolvedValueOnce({ items: [first], nextCursor: "page_two" })
      .mockResolvedValueOnce({ items: [second], nextCursor: null });
    const getMaterialStatus = vi
      .fn<MaterialLibraryGateway["getMaterialStatus"]>()
      .mockResolvedValue("available");
    const user = userEvent.setup();
    render(<MaterialLibraryPage gateway={gateway({ listMaterials, getMaterialStatus })} />);

    expect(await screen.findByText(`素材 ${VIDEO_ID.slice(0, 8)}`)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "加载更多素材" }));
    expect(await screen.findByText(`素材 ${IMAGE_ID.slice(0, 8)}`)).toBeVisible();
    expect(screen.getAllByText(`素材 ${VIDEO_ID.slice(0, 8)}`)).toHaveLength(1);
    expect(listMaterials.mock.calls).toEqual([[null], ["page_two"]]);
    await waitFor(() => expect(getMaterialStatus).toHaveBeenCalledTimes(2));
    expect(screen.getByText(`素材编号：${VIDEO_ID}`)).toBeVisible();
  });

  it("fails closed on repeated cross-page identities or cursors", async () => {
    const listMaterials = vi
      .fn<MaterialLibraryGateway["listMaterials"]>()
      .mockResolvedValueOnce({ items: [material()], nextCursor: "page_two" })
      .mockResolvedValueOnce({ items: [material()], nextCursor: "page_two" });
    const user = userEvent.setup();
    render(<MaterialLibraryPage gateway={gateway({ listMaterials })} />);

    await screen.findByText(`素材 ${VIDEO_ID.slice(0, 8)}`);
    await user.click(screen.getByRole("button", { name: "加载更多素材" }));
    expect(await screen.findByText("暂时读不到本机素材库，请稍后重试。")).toBeVisible();
    expect(screen.queryByRole("button", { name: "加载更多素材" })).not.toBeInTheDocument();
  });

  it("shows facts, AI understanding, speech marks and transcript in Chinese", async () => {
    render(
      <MaterialLibraryPage
        gateway={gateway({
          async listMaterials() {
            return { items: [material()], nextCursor: null };
          },
        })}
      />,
    );

    expect((await screen.findAllByText("产品发布会镜头"))[0]).toBeVisible();
    expect(screen.getByText("AI 说明")).toBeVisible();
    expect(screen.getByText("发布会")).toBeVisible();
    expect(screen.getByText("有语音 · 1 段")).toBeVisible();
    expect(screen.getByText("今天发布新功能")).toBeVisible();
    expect(screen.getByText("720×1280 · 3.0 秒")).toBeVisible();
    expect(document.body).not.toHaveTextContent(/privatePath|contentDigest|\/Users\//u);
  });

  it("plays video and previews images only through an opaque capability URL", async () => {
    const image = material({
      materialId: IMAGE_ID,
      kind: "image",
      durationMs: null,
      width: 1200,
      height: 800,
      hasAudio: false,
      audioLoudnessLufs: null,
      hasSpeech: false,
      speechSegmentsMs: [],
      speechTranscript: null,
      shotBoundariesMs: [],
    });
    const audio = material({
      materialId: AUDIO_ID,
      kind: "audio",
      width: null,
      height: null,
      shotBoundariesMs: [],
    });
    const user = userEvent.setup();
    render(
      <MaterialLibraryPage
        gateway={gateway({
          async listMaterials() {
            return { items: [material(), image, audio], nextCursor: null };
          },
        })}
      />,
    );

    await screen.findByText(`素材 ${VIDEO_ID.slice(0, 8)}`);
    await user.click(screen.getByRole("button", { name: `预览素材 ${VIDEO_ID.slice(0, 8)}` }));
    expect(await screen.findByLabelText(`视频预览 ${VIDEO_ID.slice(0, 8)}`)).toHaveAttribute(
      "src",
      expect.stringContaining(CAPABILITY),
    );
    await user.click(screen.getByRole("button", { name: `预览素材 ${IMAGE_ID.slice(0, 8)}` }));
    expect(await screen.findByAltText(`图片素材 ${IMAGE_ID.slice(0, 8)}`)).toHaveAttribute(
      "src",
      expect.stringContaining(CAPABILITY),
    );
    await user.click(screen.getByRole("button", { name: `预览素材 ${AUDIO_ID.slice(0, 8)}` }));
    expect(await screen.findByLabelText(`音频预览 ${AUDIO_ID.slice(0, 8)}`)).toHaveAttribute(
      "src",
      expect.stringContaining(CAPABILITY),
    );

    const video = screen.getByLabelText(`视频预览 ${VIDEO_ID.slice(0, 8)}`);
    Object.defineProperty(video, "currentSrc", {
      configurable: true,
      value: `file:///Users/private/${VIDEO_ID}.mp4`,
    });
    await act(async () => video.dispatchEvent(new Event("error")));
    expect(screen.getByText("这个素材暂时无法播放，可以稍后重试。")).toBeVisible();
    expect(document.body).not.toHaveTextContent("/Users/private");
  });

  it("reports dedupe and the required import failure distinctions", async () => {
    const importMaterial = vi
      .fn<MaterialLibraryGateway["importMaterial"]>()
      .mockResolvedValueOnce({ material: material(), deduplicated: true })
      .mockRejectedValueOnce(new MaterialLibraryGatewayError("source_not_at_rest", true))
      .mockRejectedValueOnce(new MaterialLibraryGatewayError("undecodable", true))
      .mockRejectedValueOnce(new MaterialLibraryGatewayError("workspace_unusable", false));
    const user = userEvent.setup();
    render(<MaterialLibraryPage gateway={gateway({ importMaterial })} />);

    await screen.findByText("还没有本机素材");
    await user.click(screen.getByRole("button", { name: /导入本机素材/u }));
    expect(await screen.findByText("这个文件已经在素材库里，已直接使用现有素材。")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /导入本机素材/u }));
    expect(await screen.findByText("文件还在写入，请等它保存完成后再导入。")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /导入本机素材/u }));
    expect(await screen.findByText("目前还不能解码这个文件，可以稍后重试。")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /导入本机素材/u }));
    expect(await screen.findByText("本机临时工作空间不可用，请检查磁盘空间和写入权限。")).toBeVisible();
  });

  it.each([
    ["file_missing", "本机文件不在原位置了，请找到原文件后重新导入。"],
    ["file_unreadable", "本机文件仍在原位置，但当前无法读取；请检查文件权限后重试。"],
    ["file_changed", "本机文件已经被替换或修改，请重新导入这个文件。"],
  ] as const)("distinguishes the %s local status", async (status, text) => {
    render(
      <MaterialLibraryPage
        gateway={gateway({
          async listMaterials() {
            return { items: [material()], nextCursor: null };
          },
          async getMaterialStatus() {
            return status;
          },
        })}
      />,
    );
    expect(await screen.findByText(text)).toBeVisible();
  });

  it("updates a human description and uses a two-step recoverable delete", async () => {
    const updateMaterialDescription = vi
      .fn<MaterialLibraryGateway["updateMaterialDescription"]>()
      .mockImplementation(async (_id, description) =>
        material({
          aiDescription: description,
          aiTags: [],
          descriptionSource: "user",
          describedAt: null,
        }),
      );
    const deleteMaterial = vi
      .fn<MaterialLibraryGateway["deleteMaterial"]>()
      .mockRejectedValueOnce(new MaterialLibraryGatewayError("material_service_unavailable", false))
      .mockResolvedValueOnce();
    const user = userEvent.setup();
    render(
      <MaterialLibraryPage
        gateway={gateway({
          async listMaterials() {
            return { items: [material()], nextCursor: null };
          },
          updateMaterialDescription,
          deleteMaterial,
        })}
      />,
    );

    const description = await screen.findByLabelText(`素材说明 ${VIDEO_ID.slice(0, 8)}`);
    await user.clear(description);
    await user.type(description, "人工挑选的开场镜头");
    await user.click(screen.getByRole("button", { name: `保存说明 ${VIDEO_ID.slice(0, 8)}` }));
    expect(await screen.findByText("人工说明已保存，后续 AI 分析不会覆盖它。")).toBeVisible();
    expect(screen.getByText("人工说明")).toBeVisible();
    expect(updateMaterialDescription).toHaveBeenCalledWith(VIDEO_ID, "人工挑选的开场镜头");

    await user.click(screen.getByRole("button", { name: `删除素材 ${VIDEO_ID.slice(0, 8)}` }));
    expect(deleteMaterial).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: `确认删除素材 ${VIDEO_ID.slice(0, 8)}` }));
    expect(await screen.findByText("暂时不能删除这个素材；它可能仍被剪辑项目使用，请调整后重试。")).toBeVisible();
    expect(screen.getByText(`素材 ${VIDEO_ID.slice(0, 8)}`)).toBeVisible();

    await user.click(screen.getByRole("button", { name: `确认删除素材 ${VIDEO_ID.slice(0, 8)}` }));
    await waitFor(() => expect(screen.queryByText(`素材 ${VIDEO_ID.slice(0, 8)}`)).not.toBeInTheDocument());
  });
});

import { useCallback, useEffect, useRef, useState } from "react";

import { Alert, Button, Card, Empty, Input, Space, Tag, Typography } from "antd";

import {
  MaterialLibraryGatewayError,
  type EditingMaterialSnapshot,
  type LocalMaterialStatus,
  type MaterialLibraryErrorCode,
  type MaterialLibraryGateway,
} from "./material-library-gateway";

type Message = { readonly type: "success" | "warning" | "error"; readonly text: string };

const STATUS_TEXT: Record<Exclude<LocalMaterialStatus, "available">, string> = {
  unusable_identifier: "素材编号不可用，请刷新素材库后重试。",
  not_registered: "本机没有这个素材的文件记录，请重新导入。",
  file_missing: "本机文件不在原位置了，请找到原文件后重新导入。",
  file_unreadable: "本机文件仍在原位置，但当前无法读取；请检查文件权限后重试。",
  file_changed: "本机文件已经被替换或修改，请重新导入这个文件。",
  registry_unreadable: "暂时读不到本机素材记录，请稍后重试。",
  registry_unwritable: "本机素材记录当前无法更新，请检查写入权限。",
  registry_full: "本机素材记录空间已满，请先清理后再试。",
};

const IMPORT_ERROR_TEXT: Partial<Record<MaterialLibraryErrorCode, string>> = {
  source_not_at_rest: "文件还在写入，请等它保存完成后再导入。",
  undecodable: "目前还不能解码这个文件，可以稍后重试。",
  workspace_unusable: "本机临时工作空间不可用，请检查磁盘空间和写入权限。",
  unreadable: "无法读取所选文件，请检查文件权限后重试。",
  unsafe_path: "这个文件位置不能安全读取，请选择普通本机文件后重试。",
  no_usable_stream: "这个文件里没有可用的画面或声音。",
  unusable_duration: "无法确定这个素材的有效时长。",
  too_long: "这个素材太长，当前不能导入。",
  unusable_frame_size: "无法确定这个素材的画面尺寸。",
  frame_too_large: "这个素材的画面尺寸过大，当前不能导入。",
  file_too_large: "这个文件太大，当前不能导入。",
  silent_audio: "这个音频没有可用声音，当前不能导入。",
  source_changed: "文件在导入过程中发生了变化，请保存完成后重试。",
  outcome_uncertain: "导入结果暂时无法确认，请先刷新素材库，在确认前不要重复导入。",
  compensation_failed: "导入没有完成，且本机记录清理失败；请先刷新素材库再处理。",
  operation_in_progress: "已有素材操作正在进行，请稍后再试。",
  timed_out: "本机素材处理超时，可以稍后重试。",
  worker_unavailable: "本机素材服务暂时不可用，请稍后重试。",
  file_missing: "所选文件已经不在原位置，请重新选择。",
  file_unreadable: "所选文件仍在原位置，但当前无法读取。",
  file_changed: "所选文件在导入后发生了变化，请重新导入。",
  registry_unreadable: "暂时读不到本机素材记录，请稍后重试。",
  registry_unwritable: "本机素材记录当前无法更新，请检查写入权限。",
  registry_full: "本机素材记录空间已满，请先清理后再试。",
};

function importErrorMessage(error: unknown): string {
  if (error instanceof MaterialLibraryGatewayError) {
    return IMPORT_ERROR_TEXT[error.code] ?? "素材导入没有完成，请稍后重试。";
  }
  return "素材导入没有完成，请稍后重试。";
}

function shortId(materialId: string): string {
  return materialId.slice(0, 8);
}

function materialFacts(material: EditingMaterialSnapshot): string {
  const dimensions =
    material.width === null || material.height === null
      ? null
      : `${material.width}×${material.height}`;
  const duration =
    material.durationMs === null ? null : `${(material.durationMs / 1_000).toFixed(1)} 秒`;
  return [dimensions, duration].filter((value) => value !== null).join(" · ");
}

function kindLabel(kind: EditingMaterialSnapshot["kind"]): string {
  return { video: "视频", image: "图片", audio: "音频" }[kind];
}

function MaterialPreview({
  material,
  url,
  failed,
  onError,
}: {
  readonly material: EditingMaterialSnapshot;
  readonly url: string;
  readonly failed: boolean;
  readonly onError: () => void;
}) {
  const suffix = shortId(material.materialId);
  return (
    <Space orientation="vertical" size="small" className="material-library-preview">
      {material.kind === "image" ? (
        <img src={url} alt={`图片素材 ${suffix}`} onError={onError} />
      ) : material.kind === "video" ? (
        <video
          src={url}
          aria-label={`视频预览 ${suffix}`}
          controls
          preload="metadata"
          onError={onError}
        />
      ) : (
        <audio
          src={url}
          aria-label={`音频预览 ${suffix}`}
          controls
          preload="metadata"
          onError={onError}
        />
      )}
      {failed ? (
        <Alert type="error" showIcon title="这个素材暂时无法播放，可以稍后重试。" />
      ) : null}
    </Space>
  );
}

export function MaterialLibraryPage({
  gateway,
}: {
  readonly gateway: MaterialLibraryGateway;
}) {
  const [materials, setMaterials] = useState<readonly EditingMaterialSnapshot[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [message, setMessage] = useState<Message | null>(null);
  const [statuses, setStatuses] = useState<Readonly<Record<string, LocalMaterialStatus>>>({});
  const [statusFailures, setStatusFailures] = useState<ReadonlySet<string>>(new Set());
  const [previewUrls, setPreviewUrls] = useState<Readonly<Record<string, string>>>({});
  const [previewLoading, setPreviewLoading] = useState<ReadonlySet<string>>(new Set());
  const [previewFailures, setPreviewFailures] = useState<ReadonlySet<string>>(new Set());
  const [drafts, setDrafts] = useState<Readonly<Record<string, string>>>({});
  const [saving, setSaving] = useState<ReadonlySet<string>>(new Set());
  const [deleteConfirmation, setDeleteConfirmation] = useState<ReadonlySet<string>>(new Set());
  const [deleting, setDeleting] = useState<ReadonlySet<string>>(new Set());
  const mountedRef = useRef(true);
  const requestRef = useRef(0);
  const autoLoadedGatewayRef = useRef<MaterialLibraryGateway | null>(null);
  const requestedStatusIdsRef = useRef<Set<string>>(new Set());
  const materialIdsRef = useRef<Set<string>>(new Set());
  const consumedCursorsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestRef.current += 1;
    };
  }, []);

  const loadStatuses = useCallback(
    (items: readonly EditingMaterialSnapshot[], force = false) => {
      for (const material of items) {
        if (!force && requestedStatusIdsRef.current.has(material.materialId)) continue;
        requestedStatusIdsRef.current.add(material.materialId);
        void gateway
          .getMaterialStatus(material.materialId)
          .then((status) => {
            if (!mountedRef.current) return;
            setStatuses((current) => ({ ...current, [material.materialId]: status }));
            setStatusFailures((current) => {
              const next = new Set(current);
              next.delete(material.materialId);
              return next;
            });
          })
          .catch(() => {
            if (!mountedRef.current) return;
            setStatusFailures((current) => new Set(current).add(material.materialId));
          });
      }
    },
    [gateway],
  );

  const loadPage = useCallback(
    async (cursor: string | null, refresh: boolean): Promise<void> => {
      const request = ++requestRef.current;
      setLoading(true);
      setMessage(null);
      try {
        const page = await gateway.listMaterials(cursor);
        if (!mountedRef.current || request !== requestRef.current) return;
        const existingIds = refresh ? new Set<string>() : materialIdsRef.current;
        const pageHasExistingIdentity = page.items.some((item) => existingIds.has(item.materialId));
        const consumedCursors = refresh
          ? new Set<string>()
          : new Set(consumedCursorsRef.current);
        if (cursor !== null) consumedCursors.add(cursor);
        if (
          pageHasExistingIdentity ||
          (page.nextCursor !== null && consumedCursors.has(page.nextCursor))
        ) {
          setNextCursor(null);
          throw new Error("material pagination is invalid");
        }
        const nextIds = new Set(existingIds);
        for (const item of page.items) nextIds.add(item.materialId);
        materialIdsRef.current = nextIds;
        consumedCursorsRef.current = consumedCursors;
        setMaterials((current) => {
          if (refresh) return page.items;
          return [...current, ...page.items];
        });
        setDrafts((current) => {
          const next = refresh ? {} : { ...current };
          for (const item of page.items) {
            next[item.materialId] ??= item.aiDescription ?? "";
          }
          return next;
        });
        if (refresh) {
          requestedStatusIdsRef.current.clear();
          setStatuses({});
          setStatusFailures(new Set());
          setPreviewUrls({});
          setPreviewFailures(new Set());
        }
        setNextCursor(page.nextCursor);
        setLoaded(true);
        loadStatuses(page.items);
      } catch {
        if (mountedRef.current && request === requestRef.current) {
          setMessage({ type: "error", text: "暂时读不到本机素材库，请稍后重试。" });
        }
      } finally {
        if (mountedRef.current && request === requestRef.current) setLoading(false);
      }
    },
    [gateway, loadStatuses],
  );

  useEffect(() => {
    queueMicrotask(() => {
      if (mountedRef.current && autoLoadedGatewayRef.current !== gateway) {
        autoLoadedGatewayRef.current = gateway;
        void loadPage(null, true);
      }
    });
  }, [gateway, loadPage]);

  const importMaterial = () => {
    if (importing) return;
    setImporting(true);
    setMessage(null);
    void gateway
      .importMaterial()
      .then((outcome) => {
        if (!mountedRef.current || outcome === null) return;
        setMaterials((current) => [
          outcome.material,
          ...current.filter((item) => item.materialId !== outcome.material.materialId),
        ]);
        materialIdsRef.current.add(outcome.material.materialId);
        setDrafts((current) => ({
          ...current,
          [outcome.material.materialId]: outcome.material.aiDescription ?? "",
        }));
        setLoaded(true);
        loadStatuses([outcome.material], true);
        setMessage({
          type: "success",
          text: outcome.deduplicated
            ? "这个文件已经在素材库里，已直接使用现有素材。"
            : "素材已导入本机素材库。",
        });
      })
      .catch((error: unknown) => {
        if (mountedRef.current) setMessage({ type: "error", text: importErrorMessage(error) });
      })
      .finally(() => {
        if (mountedRef.current) setImporting(false);
      });
  };

  const openPreview = (material: EditingMaterialSnapshot) => {
    const id = material.materialId;
    setPreviewLoading((current) => new Set(current).add(id));
    setPreviewFailures((current) => {
      const next = new Set(current);
      next.delete(id);
      return next;
    });
    void gateway
      .getMaterialPreviewUrl(id)
      .then((url) => {
        if (mountedRef.current) setPreviewUrls((current) => ({ ...current, [id]: url }));
      })
      .catch(() => {
        if (mountedRef.current) {
          setPreviewFailures((current) => new Set(current).add(id));
          setMessage({ type: "error", text: "这个素材暂时无法播放，可以稍后重试。" });
        }
      })
      .finally(() => {
        if (!mountedRef.current) return;
        setPreviewLoading((current) => {
          const next = new Set(current);
          next.delete(id);
          return next;
        });
      });
  };

  const saveDescription = (material: EditingMaterialSnapshot) => {
    const id = material.materialId;
    const description = (drafts[id] ?? "").trim();
    setSaving((current) => new Set(current).add(id));
    setMessage(null);
    void gateway
      .updateMaterialDescription(id, description)
      .then((updated) => {
        if (!mountedRef.current) return;
        setMaterials((current) => current.map((item) => (item.materialId === id ? updated : item)));
        setDrafts((current) => ({ ...current, [id]: updated.aiDescription ?? "" }));
        setMessage({
          type: "success",
          text: "人工说明已保存，后续 AI 分析不会覆盖它。",
        });
      })
      .catch(() => {
        if (mountedRef.current) {
          setMessage({ type: "error", text: "素材说明没有保存，请检查内容后重试。" });
        }
      })
      .finally(() => {
        if (!mountedRef.current) return;
        setSaving((current) => {
          const next = new Set(current);
          next.delete(id);
          return next;
        });
      });
  };

  const deleteMaterial = (materialId: string) => {
    if (!deleteConfirmation.has(materialId)) {
      setDeleteConfirmation((current) => new Set(current).add(materialId));
      return;
    }
    setDeleting((current) => new Set(current).add(materialId));
    setMessage(null);
    void gateway
      .deleteMaterial(materialId)
      .then(() => {
        if (!mountedRef.current) return;
        setMaterials((current) => current.filter((item) => item.materialId !== materialId));
        materialIdsRef.current.delete(materialId);
        setPreviewUrls((current) => {
          const next = { ...current };
          delete next[materialId];
          return next;
        });
        setMessage({ type: "success", text: "素材已从素材库删除。" });
      })
      .catch(() => {
        if (mountedRef.current) {
          setMessage({
            type: "error",
            text: "暂时不能删除这个素材；它可能仍被剪辑项目使用，请调整后重试。",
          });
        }
      })
      .finally(() => {
        if (!mountedRef.current) return;
        setDeleting((current) => {
          const next = new Set(current);
          next.delete(materialId);
          return next;
        });
      });
  };

  return (
    <Space orientation="vertical" size="middle" className="material-library-page">
      <Card className="video-editing-panel">
        <Space orientation="vertical" size="middle">
          <Typography.Text type="secondary">
            素材文件只留在本机；这里显示可用于剪辑的事实、说明和预览。
          </Typography.Text>
          <Space wrap size="small">
            <Button type="primary" loading={importing} onClick={importMaterial}>
              导入本机素材
            </Button>
            <Button
              aria-label="刷新素材库"
              loading={loading}
              onClick={() => void loadPage(null, true)}
            >
              刷新素材库
            </Button>
          </Space>
          {message === null ? null : <Alert type={message.type} showIcon title={message.text} />}
        </Space>
      </Card>

      {!loaded && loading ? (
        <Card className="video-editing-panel">
          <Typography.Text type="secondary">正在读取本机素材库…</Typography.Text>
        </Card>
      ) : materials.length === 0 ? (
        <Card className="video-editing-panel">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space orientation="vertical" size={4}>
                <Typography.Text strong>还没有本机素材</Typography.Text>
                <Typography.Text type="secondary">
                  从本机选择视频、图片或音频，导入后再放到时间轴中。
                </Typography.Text>
              </Space>
            }
          />
        </Card>
      ) : (
        <div className="material-library-grid">
          {materials.map((material) => {
            const id = material.materialId;
            const suffix = shortId(id);
            const status = statuses[id];
            const previewUrl = previewUrls[id];
            return (
              <Card
                key={id}
                className="video-editing-panel material-library-card"
                title={`素材 ${suffix}`}
                extra={<Tag>{kindLabel(material.kind)}</Tag>}
              >
                <Space orientation="vertical" size="small">
                  <Typography.Text type="secondary">{materialFacts(material)}</Typography.Text>
                  <Typography.Text copyable={{ text: id }}>
                    素材编号：{id}
                  </Typography.Text>
                  {status === undefined && !statusFailures.has(id) ? (
                    <Typography.Text type="secondary">正在检查本机文件…</Typography.Text>
                  ) : statusFailures.has(id) ? (
                    <Alert type="warning" showIcon title="暂时无法确认本机文件状态。" />
                  ) : status === "available" ? (
                    <Tag color="green">本机文件可用</Tag>
                  ) : (
                    <Alert type="warning" showIcon title={STATUS_TEXT[status!]} />
                  )}

                  <Space wrap size={4}>
                    <Tag>{material.descriptionSource === "user" ? "人工说明" : "AI 说明"}</Tag>
                    {material.aiTags.map((tag) => (
                      <Tag key={tag}>{tag}</Tag>
                    ))}
                    <Tag>{material.hasSpeech ? `有语音 · ${material.speechSegmentsMs.length} 段` : "未发现语音"}</Tag>
                  </Space>
                  {material.aiDescription === null ? (
                    <Typography.Text type="secondary">还没有素材说明</Typography.Text>
                  ) : (
                    <Typography.Paragraph>{material.aiDescription}</Typography.Paragraph>
                  )}
                  {material.speechTranscript === null ? null : (
                    <Typography.Paragraph>
                      <Typography.Text strong>语音转写：</Typography.Text>
                      {material.speechTranscript}
                    </Typography.Paragraph>
                  )}

                  <Button
                    aria-label={`预览素材 ${suffix}`}
                    loading={previewLoading.has(id)}
                    disabled={status !== undefined && status !== "available"}
                    onClick={() => openPreview(material)}
                  >
                    打开本机预览
                  </Button>
                  {previewUrl === undefined ? null : (
                    <MaterialPreview
                      material={material}
                      url={previewUrl}
                      failed={previewFailures.has(id)}
                      onError={() => setPreviewFailures((current) => new Set(current).add(id))}
                    />
                  )}

                  <Input.TextArea
                    aria-label={`素材说明 ${suffix}`}
                    value={drafts[id] ?? ""}
                    autoSize={{ minRows: 2, maxRows: 5 }}
                    onChange={(event) =>
                      setDrafts((current) => ({
                        ...current,
                        [id]: [...event.target.value].slice(0, 2_000).join(""),
                      }))
                    }
                  />
                  <Space wrap size="small">
                    <Button
                      aria-label={`保存说明 ${suffix}`}
                      loading={saving.has(id)}
                      disabled={(drafts[id] ?? "").trim().length === 0}
                      onClick={() => saveDescription(material)}
                    >
                      保存人工说明
                    </Button>
                    <Button
                      danger
                      aria-label={`${deleteConfirmation.has(id) ? "确认删除" : "删除"}素材 ${suffix}`}
                      loading={deleting.has(id)}
                      onClick={() => deleteMaterial(id)}
                    >
                      {deleteConfirmation.has(id) ? "再次点击确认删除" : "删除素材"}
                    </Button>
                  </Space>
                </Space>
              </Card>
            );
          })}
        </div>
      )}

      {nextCursor === null ? null : (
        <Button loading={loading} onClick={() => void loadPage(nextCursor, false)}>
          加载更多素材
        </Button>
      )}
    </Space>
  );
}

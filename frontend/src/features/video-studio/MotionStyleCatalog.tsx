import { useEffect, useRef, useState } from "react";

import { Alert, Button, Card, Input, Select, Space, Tag, Typography } from "antd";

import {
  buildMotionStylePreview,
  recommendMotionStyles,
  validateBrandStyleDraft,
  type BrandStyleDraft,
  type MotionInformationDensity,
} from "./motion-style-authoring";
import { MOTION_STYLE_CATALOG, type MotionStyleOption } from "./motion-style-catalog";

const EMPTY_BRAND: BrandStyleDraft = {
  primaryColor: null,
  secondaryColor: null,
  fontFamily: null,
  fontFileName: null,
  logoFileName: null,
};

const MAX_FONT_BYTES = 32 * 1024 * 1024;

function fontFileSignatureMatches(fileName: string, bytes: Uint8Array): boolean {
  const lower = fileName.toLowerCase();
  const prefix = String.fromCharCode(...bytes.slice(0, 4));
  return (
    (lower.endsWith(".woff2") && prefix === "wOF2") ||
    (lower.endsWith(".woff") && prefix === "wOFF") ||
    (lower.endsWith(".ttf") &&
      ((bytes[0] === 0 && bytes[1] === 1 && bytes[2] === 0 && bytes[3] === 0) ||
        prefix === "true")) ||
    (lower.endsWith(".otf") && prefix === "OTTO")
  );
}

export interface MotionStyleDraftSelection {
  readonly stylePresetId: string | null;
  readonly primaryColor: string;
  readonly secondaryColor: string;
  readonly isValid: boolean;
  readonly problem: string | null;
  readonly font: {
    readonly family: string;
    readonly fileName: string;
    readonly base64: string;
    readonly previewUrl: string;
  } | null;
  readonly logo: {
    readonly fileName: string;
    readonly mediaType: "image/png" | "image/jpeg" | "image/webp";
    readonly bytes: readonly number[];
    readonly previewUrl: string;
  } | null;
}

export function MotionStyleCatalog({
  onDraftChange,
}: {
  readonly onDraftChange?: ((draft: MotionStyleDraftSelection) => void) | undefined;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focusIndex, setFocusIndex] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const [headline, setHeadline] = useState("本周销售增长说明");
  const [body, setBody] = useState("展示关键指标、增长来源与下一步行动。");
  const [industry, setIndustry] = useState("企业服务");
  const [informationDensity, setInformationDensity] =
    useState<MotionInformationDensity>("high");
  const [primaryColor, setPrimaryColor] = useState("");
  const [secondaryColor, setSecondaryColor] = useState("");
  const [fontFamily, setFontFamily] = useState("");
  const [fontFileName, setFontFileName] = useState("");
  const [fontPreviewUrl, setFontPreviewUrl] = useState<string | null>(null);
  const [fontBase64, setFontBase64] = useState<string | null>(null);
  const [fontError, setFontError] = useState<string | null>(null);
  const [logoFileName, setLogoFileName] = useState("");
  const [logoPreviewUrl, setLogoPreviewUrl] = useState<string | null>(null);
  const [logoBytes, setLogoBytes] = useState<readonly number[] | null>(null);
  const [logoMediaType, setLogoMediaType] =
    useState<"image/png" | "image/jpeg" | "image/webp" | null>(null);
  const [logoError, setLogoError] = useState<string | null>(null);
  const cardRefs = useRef<Array<HTMLDivElement | null>>([]);
  const fontSelectionSequence = useRef(0);
  const fontFileInput = useRef<HTMLInputElement | null>(null);

  const clearFont = () => {
    fontSelectionSequence.current += 1;
    if (fontFileInput.current !== null) {
      fontFileInput.current.value = "";
    }
    setFontFamily("");
    setFontFileName("");
    setFontPreviewUrl(null);
    setFontBase64(null);
    setFontError(null);
  };

  let brand = EMPTY_BRAND;
  let brandError: string | null = null;
  try {
    brand = validateBrandStyleDraft({
      primaryColor,
      secondaryColor,
      fontFamily,
      fontFileName,
      logoFileName,
    });
  } catch {
    brandError = "品牌输入格式不正确：颜色请使用 #rrggbb，字体只填字体名称，Logo 请选择本地位图文件。";
  }

  let recommended: readonly MotionStyleOption[];
  try {
    recommended = recommendMotionStyles(MOTION_STYLE_CATALOG, {
      brief: `${headline} ${body}`,
      industry,
      informationDensity,
      ...(brand.primaryColor === null ? {} : { primaryColor: brand.primaryColor }),
    });
  } catch {
    recommended = MOTION_STYLE_CATALOG.slice(0, 3);
  }
  const recommendedIds = new Set(recommended.map((style) => style.id));
  const visibleStyles = showAll ? MOTION_STYLE_CATALOG : recommended;
  const selected = MOTION_STYLE_CATALOG.find((style) => style.id === selectedId);
  const previewStyle = selected ?? recommended[0]!;
  const previewContent = {
    headline: headline.trim() === "" ? "请输入预览标题" : headline,
    body: body.trim() === "" ? "请输入预览正文" : body,
  };
  const actualPreview = buildMotionStylePreview(
    previewStyle,
    previewContent,
    brandError === null ? brand : EMPTY_BRAND,
  );

  useEffect(() => {
    const fontReady =
      brandError === null &&
      brand.fontFamily !== null &&
      fontFileName !== "" &&
      fontBase64 !== null &&
      fontPreviewUrl !== null;
    const logoReady =
      logoFileName !== "" &&
      logoPreviewUrl !== null &&
      logoBytes !== null &&
      logoMediaType !== null;
    const problem = fontError ?? logoError ?? brandError;
    onDraftChange?.({
      stylePresetId: selectedId,
      primaryColor,
      secondaryColor,
      isValid:
        problem === null &&
        (fontFileName === "" || fontReady) &&
        (logoFileName === "" || logoReady),
      problem,
      font:
        fontReady
          ? {
              family: brand.fontFamily!,
              fileName: fontFileName,
              base64: fontBase64!,
              previewUrl: fontPreviewUrl!,
            }
          : null,
      logo:
        !logoReady
          ? null
          : {
              fileName: logoFileName,
              mediaType: logoMediaType!,
              bytes: logoBytes!,
              previewUrl: logoPreviewUrl!,
            },
    });
  }, [
    logoBytes,
    logoFileName,
    logoMediaType,
    logoPreviewUrl,
    fontBase64,
    fontError,
    fontFileName,
    fontFamily,
    fontPreviewUrl,
    brand.fontFamily,
    brandError,
    logoError,
    onDraftChange,
    primaryColor,
    secondaryColor,
    selectedId,
  ]);

  const focusCard = (index: number) => {
    const total = visibleStyles.length;
    const next = (index + total) % total;
    setFocusIndex(next);
    cardRefs.current[next]?.focus();
  };

  const selectCard = (index: number) => {
    const style = visibleStyles[index];
    if (style !== undefined) {
      setSelectedId(style.id);
      setFocusIndex(index);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>, index: number) => {
    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        event.preventDefault();
        focusCard(index + 1);
        break;
      case "ArrowLeft":
      case "ArrowUp":
        event.preventDefault();
        focusCard(index - 1);
        break;
      case "Home":
        event.preventDefault();
        focusCard(0);
        break;
      case "End":
        event.preventDefault();
        focusCard(visibleStyles.length - 1);
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        selectCard(index);
        break;
      default:
        break;
    }
  };

  return (
    <Card className="video-studio-panel" title="品牌动效成片 · 整体画面风格">
      <Space orientation="vertical" size="middle" className="motion-style-intro">
        <Typography.Text type="secondary">
          先根据实际内容、行业、品牌主色和信息密度推荐 3 套；可以展开查看当前锁定版本的全部 12
          套。选择与品牌微调会在提交任务时经过本地文件校验和摘要冻结。
        </Typography.Text>
        {selected === undefined ? (
          <Tag>尚未选择风格</Tag>
        ) : (
          <Tag color="blue">已选择风格：{selected.displayName}</Tag>
        )}
      </Space>

      <section className="motion-style-controls" aria-label="风格推荐与品牌微调">
        <div className="motion-style-fields">
          <label>
            <span>预览标题</span>
            <Input
              aria-label="预览标题"
              maxLength={80}
              value={headline}
              onChange={(event) => setHeadline(event.target.value)}
            />
          </label>
          <label>
            <span>预览正文</span>
            <Input.TextArea
              aria-label="预览正文"
              maxLength={240}
              rows={2}
              value={body}
              onChange={(event) => setBody(event.target.value)}
            />
          </label>
          <label>
            <span>所属行业</span>
            <Input
              aria-label="所属行业"
              maxLength={80}
              value={industry}
              onChange={(event) => setIndustry(event.target.value)}
            />
          </label>
          <label>
            <span>信息密度</span>
            <Select
              aria-label="信息密度"
              value={informationDensity}
              options={[
                { value: "low", label: "低" },
                { value: "medium", label: "中" },
                { value: "high", label: "高" },
              ]}
              onChange={(value: MotionInformationDensity) => setInformationDensity(value)}
            />
          </label>
          <label>
            <span>品牌主色</span>
            <Input
              aria-label="品牌主色"
              maxLength={7}
              placeholder="#rrggbb"
              value={primaryColor}
              onChange={(event) => setPrimaryColor(event.target.value)}
            />
          </label>
          <label>
            <span>品牌辅助色</span>
            <Input
              aria-label="品牌辅助色"
              maxLength={7}
              placeholder="#rrggbb"
              value={secondaryColor}
              onChange={(event) => setSecondaryColor(event.target.value)}
            />
          </label>
          <label>
            <span>品牌字体</span>
            <Input
              aria-label="品牌字体"
              maxLength={80}
              placeholder="例如 Acme Sans"
              value={fontFamily}
              onChange={(event) => setFontFamily(event.target.value)}
            />
          </label>
          <label>
            <span>品牌字体文件</span>
            <input
              ref={fontFileInput}
              aria-label="品牌字体文件"
              type="file"
              accept=".woff2,.woff,.ttf,.otf"
              onChange={(event) => {
                const selectionSequence = ++fontSelectionSequence.current;
                const file = event.currentTarget.files?.[0];
                if (file === undefined) {
                  setFontFileName("");
                  setFontPreviewUrl(null);
                  setFontBase64(null);
                  setFontError(null);
                  return;
                }
                if (file.size === 0 || file.size > MAX_FONT_BYTES ||
                    !/\.(?:woff2?|ttf|otf)$/iu.test(file.name)) {
                  setFontFileName("");
                  setFontPreviewUrl(null);
                  setFontBase64(null);
                  setFontError("字体只接受不超过 32 MB 的 WOFF2、WOFF、TTF 或 OTF 本地文件。");
                  return;
                }
                setFontFileName(file.name);
                setFontPreviewUrl(null);
                setFontBase64(null);
                setFontError(null);
                void file.arrayBuffer().then(async (value) => {
                  if (fontSelectionSequence.current !== selectionSequence) {
                    return;
                  }
                  const bytes = new Uint8Array(value);
                  if (!fontFileSignatureMatches(file.name, bytes)) {
                    setFontError("字体文件格式与扩展名不一致，请重新选择。");
                    return;
                  }
                  try {
                    if (typeof FontFace !== "function") {
                      throw new Error("FontFace unavailable");
                    }
                    await new FontFace(`bm07-font-validation-${selectionSequence}`, value).load();
                  } catch {
                    if (fontSelectionSequence.current === selectionSequence) {
                      setFontPreviewUrl(null);
                      setFontBase64(null);
                      setFontError("字体文件无法解析，请重新选择有效字体。");
                    }
                    return;
                  }
                  if (fontSelectionSequence.current !== selectionSequence) {
                    return;
                  }
                  const reader = new FileReader();
                  reader.addEventListener("load", () => {
                    if (fontSelectionSequence.current !== selectionSequence) {
                      return;
                    }
                    if (typeof reader.result !== "string") {
                      setFontPreviewUrl(null);
                      setFontBase64(null);
                      setFontError("字体本地预览读取失败，请重新选择文件。");
                      return;
                    }
                    const separator = reader.result.indexOf(",");
                    if (separator < 0 || !reader.result.slice(0, separator).endsWith(";base64")) {
                      setFontPreviewUrl(null);
                      setFontBase64(null);
                      setFontError("字体本地预览读取失败，请重新选择文件。");
                      return;
                    }
                    setFontPreviewUrl(reader.result);
                    setFontBase64(reader.result.slice(separator + 1));
                  });
                  reader.addEventListener("error", () => {
                    if (fontSelectionSequence.current !== selectionSequence) {
                      return;
                    }
                    setFontPreviewUrl(null);
                    setFontBase64(null);
                    setFontError("字体本地预览读取失败，请重新选择文件。");
                  });
                  reader.readAsDataURL(file);
                }).catch(() => {
                  if (fontSelectionSequence.current !== selectionSequence) {
                    return;
                  }
                  setFontPreviewUrl(null);
                  setFontBase64(null);
                  setFontError("字体本地读取失败，请重新选择文件。");
                });
              }}
            />
          </label>
          {fontFamily === "" && fontFileName === "" && fontError === null ? null : (
            <Button onClick={clearFont}>清除品牌字体</Button>
          )}
          <label>
            <span>品牌 Logo 文件</span>
            <input
              aria-label="品牌 Logo 文件"
              type="file"
              accept=".png,.jpg,.jpeg,.webp"
              onChange={(event) => {
                const file = event.currentTarget.files?.[0];
                if (file === undefined) {
                setLogoFileName("");
                setLogoPreviewUrl(null);
                setLogoBytes(null);
                setLogoMediaType(null);
                setLogoError(null);
                  return;
                }
                const supported = /image\/(?:png|jpeg|webp)/u.test(file.type);
                if (!supported || file.size > 4 * 1024 * 1024) {
                  setLogoFileName("");
                  setLogoPreviewUrl(null);
                  setLogoBytes(null);
                  setLogoMediaType(null);
                  setLogoError("Logo 只接受不超过 4 MB 的 PNG、JPEG 或 WebP 本地文件。");
                  return;
                }
                setLogoFileName(file.name);
                setLogoMediaType(file.type as "image/png" | "image/jpeg" | "image/webp");
                setLogoError(null);
                void file.arrayBuffer().then((value) => {
                  setLogoBytes([...new Uint8Array(value)]);
                }).catch(() => {
                  setLogoBytes(null);
                  setLogoMediaType(null);
                  setLogoError("Logo 本地读取失败，请重新选择文件。");
                });
                const reader = new FileReader();
                reader.addEventListener("load", () => {
                  setLogoPreviewUrl(
                    typeof reader.result === "string" ? reader.result : null,
                  );
                });
                reader.addEventListener("error", () => {
                  setLogoPreviewUrl(null);
                  setLogoBytes(null);
                  setLogoMediaType(null);
                  setLogoError("Logo 本地预览读取失败，请重新选择文件。");
                });
                reader.readAsDataURL(file);
              }}
            />
          </label>
        </div>
        {brandError === null && fontError === null && logoError === null ? null : (
          <Alert type="error" showIcon title={fontError ?? logoError ?? brandError} />
        )}
        <Typography.Text type="secondary">
          自定义字体在冻结时必须同时提供本地字体文件；Logo 与字体不会自动上传到模型服务。
        </Typography.Text>
      </section>

      {fontPreviewUrl === null || actualPreview.fontFamily === null ? null : (
        <style>{`@font-face{font-family:"${actualPreview.fontFamily}";font-display:block;src:url("${fontPreviewUrl}")}`}</style>
      )}

      <section
        role="region"
        aria-label="实际内容风格预览"
        className="motion-style-actual-preview"
        style={{ backgroundColor: actualPreview.paper, color: actualPreview.ink }}
      >
        <div className="motion-style-actual-preview-meta">
          <Tag color="blue">{previewStyle.displayName}</Tag>
          {actualPreview.logoFileName === null ? null : (
            <span className="motion-style-logo-preview">
              {logoPreviewUrl === null ? null : (
                <img src={logoPreviewUrl} alt="品牌 Logo 预览" />
              )}
              Logo · {actualPreview.logoFileName}
            </span>
          )}
        </div>
        <h3
          style={{
            color: actualPreview.accent,
            fontFamily: actualPreview.fontFamily ?? "inherit",
          }}
        >
          {actualPreview.headline}
        </h3>
        <p style={{ fontFamily: actualPreview.fontFamily ?? "inherit" }}>
          {actualPreview.body}
        </p>
        {actualPreview.fontFamily === null ? null : (
          <Space orientation="vertical" size={0}>
            <Typography.Text>{actualPreview.fontFamily}</Typography.Text>
            <Typography.Text type="secondary">
              字体文件 · {actualPreview.fontFileName}
            </Typography.Text>
          </Space>
        )}
      </section>

      <div className="motion-style-catalog-heading">
        <div>
          <Typography.Title level={4}>{showAll ? "全部 12 套风格" : "为你推荐"}</Typography.Title>
          <Typography.Text type="secondary">
            推荐只缩小初选范围，不会隐藏当前锁定版本中的可用风格。
          </Typography.Text>
        </div>
        <Button
          onClick={() => {
            setShowAll((current) => !current);
            setFocusIndex(0);
          }}
        >
          {showAll ? "只看推荐风格" : "查看全部 12 套风格"}
        </Button>
      </div>

      <div role="radiogroup" aria-label="选择整体画面风格" className="motion-style-grid">
        {visibleStyles.map((style, index) => {
          const checked = style.id === selectedId;
          const cardPreview = buildMotionStylePreview(
            style,
            previewContent,
            brandError === null ? brand : EMPTY_BRAND,
          );
          return (
            <div
              key={style.id}
              role="radio"
              aria-checked={checked}
              aria-label={style.displayName}
              tabIndex={index === focusIndex ? 0 : -1}
              ref={(node) => {
                cardRefs.current[index] = node;
              }}
              className={`motion-style-card${checked ? " motion-style-card-selected" : ""}`}
              onClick={() => selectCard(index)}
              onKeyDown={(event) => handleKeyDown(event, index)}
            >
              <div
                className="motion-style-preview motion-style-content-preview"
                style={{
                  backgroundColor: cardPreview.paper,
                  color: cardPreview.ink,
                  fontFamily: cardPreview.fontFamily ?? "inherit",
                }}
              >
                <strong style={{ color: cardPreview.accent }}>{cardPreview.headline}</strong>
                <span>{cardPreview.body}</span>
              </div>
              <Typography.Text type="secondary" className="motion-style-preview-caption">
                实际内容预览
              </Typography.Text>
              <div className="motion-style-card-header">
                <Typography.Title level={4}>{style.displayName}</Typography.Title>
                <Space size={4}>
                  {recommendedIds.has(style.id) ? <Tag color="gold">推荐</Tag> : null}
                  {checked ? <Tag color="blue">已选择</Tag> : null}
                </Space>
              </div>
              <Typography.Paragraph className="motion-style-summary">
                {style.summary}
              </Typography.Paragraph>
              <div className="motion-style-meta">
                <Typography.Text strong>适用场景</Typography.Text>
                <div className="motion-style-tags">
                  {style.scenes.map((scene) => (
                    <Tag key={scene}>{scene}</Tag>
                  ))}
                </div>
              </div>
              <div className="motion-style-meta">
                <Typography.Text strong>风格标签</Typography.Text>
                <div className="motion-style-tags">
                  {style.tags.map((tag) => (
                    <Tag key={tag} color="default">
                      {tag}
                    </Tag>
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

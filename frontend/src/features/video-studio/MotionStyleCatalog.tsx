import { useRef, useState } from "react";

import { Card, Space, Tag, Typography } from "antd";

import { MOTION_STYLE_CATALOG } from "./motion-style-catalog";

export function MotionStyleCatalog() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focusIndex, setFocusIndex] = useState(0);
  const cardRefs = useRef<Array<HTMLDivElement | null>>([]);
  const selected = MOTION_STYLE_CATALOG.find((style) => style.id === selectedId);

  const focusCard = (index: number) => {
    const total = MOTION_STYLE_CATALOG.length;
    const next = (index + total) % total;
    setFocusIndex(next);
    cardRefs.current[next]?.focus();
  };

  const selectCard = (index: number) => {
    const style = MOTION_STYLE_CATALOG[index];
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
        focusCard(MOTION_STYLE_CATALOG.length - 1);
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
          共 12 套整体画面风格，来自当前锁定的组件版本。可以用鼠标点选，也可以用方向键浏览、回车键或空格键选择。
          示意预览按各风格配色生成，实际画面以本机渲染结果为准。
        </Typography.Text>
        {selected === undefined ? (
          <Tag>尚未选择风格</Tag>
        ) : (
          <Tag color="blue">已选择风格：{selected.displayName}</Tag>
        )}
      </Space>
      <div role="radiogroup" aria-label="选择整体画面风格" className="motion-style-grid">
        {MOTION_STYLE_CATALOG.map((style, index) => {
          const checked = style.id === selectedId;
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
                className="motion-style-preview"
                aria-hidden="true"
                style={{ backgroundColor: style.preview.paper }}
              >
                <span
                  className="motion-style-preview-title"
                  style={{ backgroundColor: style.preview.ink }}
                />
                <span
                  className="motion-style-preview-accent"
                  style={{ backgroundColor: style.preview.accent }}
                />
                <span
                  className="motion-style-preview-line"
                  style={{ backgroundColor: style.preview.ink }}
                />
              </div>
              <Typography.Text type="secondary" className="motion-style-preview-caption">
                示意预览
              </Typography.Text>
              <div className="motion-style-card-header">
                <Typography.Title level={4}>{style.displayName}</Typography.Title>
                {checked ? <Tag color="blue">已选择</Tag> : null}
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

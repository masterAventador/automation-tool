import { useMemo, useState } from "react";

import { Button, Radio, Tag, Typography } from "antd";

import {
  MOTION_PARTS_CATALOG,
  MOTION_PARTS_CATEGORIES,
  recommendMotionPartsForBeat,
  type MotionPartOption,
} from "./motion-parts-catalog";

const ALL_CATEGORIES = "全部分类";
const MAX_PARTS_PER_BEAT = 16;

export interface MotionPartsBeat {
  readonly title: string;
  readonly caption: string;
}

export function MotionPartsCatalog({
  beats,
  selections,
  onSelectionsChange,
}: {
  readonly beats: readonly MotionPartsBeat[];
  readonly selections: readonly (readonly string[])[];
  readonly onSelectionsChange: (
    next: readonly (readonly string[])[],
  ) => void;
}) {
  const [category, setCategory] = useState<string>(ALL_CATEGORIES);
  const [activeBeat, setActiveBeat] = useState(0);

  const visibleParts = useMemo(
    () =>
      category === ALL_CATEGORIES
        ? MOTION_PARTS_CATALOG
        : MOTION_PARTS_CATALOG.filter((part) => part.category === category),
    [category],
  );

  const activeSelection = selections[activeBeat] ?? [];

  const replaceSelection = (
    beatIndex: number,
    nextForBeat: readonly string[],
  ) => {
    onSelectionsChange(
      selections.map((current, index) =>
        index === beatIndex ? nextForBeat : current,
      ),
    );
  };

  const togglePart = (part: MotionPartOption) => {
    if (activeSelection.includes(part.id)) {
      replaceSelection(
        activeBeat,
        activeSelection.filter((id) => id !== part.id),
      );
      return;
    }
    if (activeSelection.length >= MAX_PARTS_PER_BEAT) return;
    replaceSelection(activeBeat, [...activeSelection, part.id]);
  };

  return (
    <div className="motion-parts-catalog">
      <section aria-label="分镜零件选用" role="region" className="motion-parts-overrides">
        <Typography.Title level={4}>分镜零件选用</Typography.Title>
        <Typography.Text type="secondary">
          动效零件与 12 套整体风格不同：整体风格决定画面的颜色与排版，零件是插入单个分镜的
          画面模块。自动制作会按分镜自动选用零件，这里可以逐段查看并手工覆盖；提交固定模板
          手工制作时不使用零件选用。
        </Typography.Text>
        <Radio.Group
          value={activeBeat}
          onChange={(event) => setActiveBeat(Number(event.target.value))}
        >
          {beats.map((beat, index) => (
            <Radio.Button key={beat.title || index} value={index}>
              {`第 ${index + 1} 段`}
            </Radio.Button>
          ))}
        </Radio.Group>
        <ul className="motion-parts-summary">
          {beats.map((beat, index) => (
            <li key={beat.title || index}>
              <Typography.Text>
                {`第 ${index + 1} 段：已选 ${(selections[index] ?? []).length} 项`}
              </Typography.Text>
              <Button
                size="small"
                onClick={() =>
                  replaceSelection(
                    index,
                    recommendMotionPartsForBeat(
                      `${beat.title} ${beat.caption}`,
                      index,
                    ),
                  )
                }
              >
                自动推荐
              </Button>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="动效零件目录" role="region" className="motion-parts-browser">
        <Typography.Title level={4}>动效零件目录</Typography.Title>
        <Radio.Group
          value={category}
          onChange={(event) => setCategory(String(event.target.value))}
        >
          <Radio.Button value={ALL_CATEGORIES}>{ALL_CATEGORIES}</Radio.Button>
          {MOTION_PARTS_CATEGORIES.map((name) => (
            <Radio.Button key={name} value={name}>
              {name}
            </Radio.Button>
          ))}
        </Radio.Group>
        <ul className="motion-parts-grid">
          {visibleParts.map((part) => {
            const selected = activeSelection.includes(part.id);
            return (
              <li key={part.id} className="motion-parts-card">
                <div className="motion-parts-card-heading">
                  <Typography.Text strong>{part.displayTitle}</Typography.Text>
                  <Tag>{part.typeLabel}</Tag>
                </div>
                <Typography.Text type="secondary">{`性能：${part.performanceLabel}`}</Typography.Text>
                <Typography.Text type="secondary">{`设备：${part.deviceRequirementLabel}`}</Typography.Text>
                <Typography.Text type="secondary">{`适用：${part.applicabilityLabel}`}</Typography.Text>
                <Typography.Text type="secondary">{`来源：${part.provenanceLabel}`}</Typography.Text>
                <Button
                  size="small"
                  type={selected ? "default" : "primary"}
                  onClick={() => togglePart(part)}
                >
                  {selected
                    ? `从第 ${activeBeat + 1} 段移除`
                    : `加入第 ${activeBeat + 1} 段`}
                </Button>
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

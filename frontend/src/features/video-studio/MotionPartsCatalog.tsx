import { useMemo, useState } from "react";

import { Alert, Button, Radio, Tag, Typography } from "antd";

import {
  MOTION_PARTS_CATALOG,
  MOTION_PARTS_CATEGORIES,
  recommendMotionPartsForBeat,
  type MotionPartOption,
  type MotionPartsUsage,
} from "./motion-parts-catalog";

const ALL_CATEGORIES = "全部分类";
const MAX_PARTS_PER_BEAT = 16;

// Shown whenever the chosen creation path cannot turn a tick into pixels. It
// sits above the catalog rather than inside the explanatory paragraph because
// the operator has to read it before deciding to tick anything, and it says
// what the capability is for instead of hiding it, so nobody concludes the
// product has no parts.
const UNUSED_NOTICE_TITLE = "本次制作方式不会用到零件选择";
const UNUSED_NOTICE_DESCRIPTION =
  "你现在用的是“固定模板手工制作”：成片画面只由整体风格、品牌颜色和你填写的文字决定，" +
  "不会放入下面的任何零件。零件目前只服务于尚未开放的“一句话自动制作”，" +
  "所以这里可以浏览了解有哪些零件，但暂时不能选用。";
const UNUSED_PART_ACTION = "本次制作不使用";
const UNUSED_BEAT_SUMMARY = "本次制作不使用零件";

export interface MotionPartsBeat {
  readonly title: string;
  readonly caption: string;
}

export function MotionPartsCatalog({
  beats,
  usage,
  selections,
  onSelectionsChange,
}: {
  readonly beats: readonly MotionPartsBeat[];
  readonly usage: MotionPartsUsage;
  readonly selections: readonly (readonly string[])[];
  readonly onSelectionsChange: (
    next: readonly (readonly string[])[],
  ) => void;
}) {
  const selectable = usage === "applies_to_output";
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
      {selectable ? null : (
        <Alert
          type="warning"
          showIcon
          title={UNUSED_NOTICE_TITLE}
          description={UNUSED_NOTICE_DESCRIPTION}
        />
      )}
      <section aria-label="分镜零件选用" role="region" className="motion-parts-overrides">
        <Typography.Title level={4}>分镜零件选用</Typography.Title>
        <Typography.Text type="secondary">
          动效零件与 12 套整体风格不同：整体风格决定画面的颜色与排版，零件是插入单个分镜的
          画面模块。
          {selectable ? "自动制作会按分镜自动选用零件，这里可以逐段查看并手工覆盖。" : null}
        </Typography.Text>
        <Radio.Group
          disabled={!selectable}
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
                {selectable
                  ? `第 ${index + 1} 段：已选 ${(selections[index] ?? []).length} 项`
                  : `第 ${index + 1} 段：${UNUSED_BEAT_SUMMARY}`}
              </Typography.Text>
              <Button
                size="small"
                disabled={!selectable}
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
                  disabled={!selectable}
                  type={selectable && !selected ? "primary" : "default"}
                  onClick={() => togglePart(part)}
                >
                  {!selectable
                    ? UNUSED_PART_ACTION
                    : selected
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

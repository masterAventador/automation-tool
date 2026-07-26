import { Alert, Button, Card, Descriptions, Flex, Input, Space, Spin, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";

import {
  AWAITING_CONFIGURATION_HINT,
  OUTCOME_UNCERTAIN_HINT,
  isPublishableCopy,
  publishAvailabilityLabel,
  publishOutcomeLabel,
  publishPlatformLabel,
  publishStageLabel,
  type PublishPlatform,
  type PublishPlatformState,
  type PublishWorkspaceGateway,
  type PublishWorkspaceSnapshot,
} from "./publish-workspace-gateway";

const AVAILABILITY_COLORS: Record<PublishPlatformState["availability"], string> = {
  ready: "green",
  awaiting_configuration: "default",
  awaiting_sign_in: "orange",
  unavailable: "red",
};

const OUTCOME_TONES: Record<
  NonNullable<PublishWorkspaceSnapshot["outcome"]>,
  "success" | "warning" | "info"
> = {
  published: "success",
  outcome_uncertain: "warning",
  not_published: "info",
  handed_off: "warning",
  cancelled: "info",
};

interface PublishWorkspaceProps {
  readonly gateway: PublishWorkspaceGateway;
  /**
   * The finished video this page can publish, if the operator has one selected.
   *
   * The page never invents it: without a selected video there is nothing to
   * publish, and offering a button that would post an unspecified file is worse
   * than offering none. It arrives from the finished-videos page, which is
   * where videos are managed; this page does not list them a second time.
   */
  readonly selectedVideo?: SelectedVideo | undefined;
  /** Go back and pick a different video. Absent when there is nowhere to go. */
  readonly onChangeSelection?: (() => void) | undefined;
}

/**
 * The finished video the operator chose, named by identity.
 *
 * There is no path here on purpose — see `PublishRequest`. The summary is a
 * label for the operator; what actually gets published is decided by the
 * identity, in the bridge.
 */
export interface SelectedVideo {
  readonly artifactId: string;
  readonly videoSummary: string;
}

/**
 * PB-07: the one page both publishing platforms are operated from.
 *
 * Nothing here knows how a platform is reached, and nothing here decides that
 * a publish may happen: the critical point renders exactly what the executor
 * presented and spends exactly the approval it issued.
 */
export function PublishWorkspace({
  gateway,
  selectedVideo,
  onChangeSelection,
}: PublishWorkspaceProps) {
  const [snapshot, setSnapshot] = useState<PublishWorkspaceSnapshot | null>(null);
  const [unreadable, setUnreadable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reloads, setReloads] = useState(0);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");

  /** Run one operator-initiated action and adopt whatever the bridge answers. */
  const run = useCallback(async (read: () => Promise<PublishWorkspaceSnapshot>) => {
    setBusy(true);
    try {
      setSnapshot(await read());
      setUnreadable(false);
    } catch {
      // One unreadable answer must not strand the operator on a blank page.
      setUnreadable(true);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    // An answer that arrives after this page is gone must not write to it.
    let cancelled = false;
    void (async () => {
      try {
        const latest = await gateway.getWorkspace();
        if (!cancelled) {
          setSnapshot(latest);
          setUnreadable(false);
        }
      } catch {
        if (!cancelled) setUnreadable(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [gateway, reloads]);

  if (unreadable) {
    return (
      <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Alert
          type="warning"
          showIcon
          title="暂时读不到发布状态"
          description="请稍后重试；已经提交过的发布不会因为这里读不到而重复提交。"
        />
        <Button onClick={() => setReloads((count) => count + 1)}>重试</Button>
      </Space>
    );
  }

  if (snapshot === null) {
    return <Spin aria-label="正在读取发布状态" />;
  }

  const approval = snapshot.approval;
  const outcome = snapshot.outcome;
  // Everything a publish needs before a platform button is worth offering.
  const publishable =
    selectedVideo !== undefined && isPublishableCopy(title) && isPublishableCopy(description);
  const publishRequest = (platform: PublishPlatform) => ({
    platform,
    artifactId: selectedVideo!.artifactId,
    videoSummary: selectedVideo!.videoSummary,
    title,
    description,
  });

  return (
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      {selectedVideo !== undefined ? null : (
        // Without this the page is a dead end: platforms are listed, no button
        // appears, and nothing says why or what to do about it.
        <Alert
          type="info"
          showIcon
          title="还没有选定要发布的视频"
          description="到「视频制作」的成片里挑一条，再回到这里发布。"
          action={
            onChangeSelection === undefined ? undefined : (
              <Button size="small" onClick={onChangeSelection}>
                去选一条
              </Button>
            )
          }
        />
      )}

      {selectedVideo === undefined ? null : (
        <Card size="small" role="group" aria-label="待发布视频">
          <Space orientation="vertical" size="small" style={{ width: "100%" }}>
            <Space wrap>
              <Typography.Text type="secondary">待发布视频</Typography.Text>
              <Typography.Text strong>{selectedVideo.videoSummary}</Typography.Text>
              {onChangeSelection === undefined ? null : (
                <Button size="small" disabled={busy} onClick={onChangeSelection}>
                  换一个
                </Button>
              )}
            </Space>
            <Input
              aria-label="标题"
              placeholder="给这条视频起个标题"
              maxLength={256}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
            <Input.TextArea
              aria-label="简介"
              placeholder="写一句简介，观众会先看到它"
              maxLength={256}
              rows={3}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </Space>
        </Card>
      )}

      <Flex gap="middle" wrap>
        {snapshot.platforms.map((platform) => (
          <Card key={platform.platform} size="small" style={{ minWidth: 220 }}>
            <Space orientation="vertical" size="small">
              <Space>
                <Typography.Text strong>{publishPlatformLabel(platform.platform)}</Typography.Text>
                <Tag color={AVAILABILITY_COLORS[platform.availability]}>
                  {publishAvailabilityLabel(platform.availability)}
                </Tag>
              </Space>
              {platform.availability === "awaiting_configuration" ? (
                <Typography.Text type="secondary">{AWAITING_CONFIGURATION_HINT}</Typography.Text>
              ) : null}
              {platform.availability === "ready" &&
              snapshot.stage === "idle" &&
              selectedVideo !== undefined ? (
                <Button
                  type="primary"
                  disabled={busy || !publishable}
                  onClick={() => void run(() => gateway.beginPublish(publishRequest(platform.platform)))}
                >
                  {`发布到${publishPlatformLabel(platform.platform)}`}
                </Button>
              ) : null}
            </Space>
          </Card>
        ))}
      </Flex>

      {snapshot.stage === "idle" ? null : (
        <Card size="small">
          <Space orientation="vertical" size="small" style={{ width: "100%" }}>
            <Space>
              <Typography.Text type="secondary">当前进度</Typography.Text>
              <Tag>{publishStageLabel(snapshot.stage)}</Tag>
              {snapshot.target === null ? null : (
                <Typography.Text type="secondary">
                  {publishPlatformLabel(snapshot.target as PublishPlatform)}
                </Typography.Text>
              )}
            </Space>

            {approval === null ? null : (
              <div role="group" aria-label="确认发布内容">
                <Descriptions size="small" column={1} bordered>
                  <Descriptions.Item label="发布到账号">
                    {approval.targetAccount}
                  </Descriptions.Item>
                  <Descriptions.Item label="视频">{approval.videoSummary}</Descriptions.Item>
                  <Descriptions.Item label="标题">{approval.title}</Descriptions.Item>
                  <Descriptions.Item label="简介">{approval.description}</Descriptions.Item>
                </Descriptions>
                <Space style={{ marginTop: 12 }}>
                  <Button
                    type="primary"
                    disabled={busy}
                    onClick={() =>
                      void run(() =>
                        gateway.approvePublish({ confirmationId: approval.confirmationId }),
                      )
                    }
                  >
                    确认发布
                  </Button>
                  <Button disabled={busy} onClick={() => void run(() => gateway.cancelPublish())}>
                    取消
                  </Button>
                </Space>
              </div>
            )}

            {snapshot.stage === "preparing" ? (
              <Button disabled={busy} onClick={() => void run(() => gateway.cancelPublish())}>
                取消
              </Button>
            ) : null}

            {outcome === null ? null : (
              <Space orientation="vertical" size="small" style={{ width: "100%" }}>
                <Space>
                  <Typography.Text type="secondary">结果</Typography.Text>
                  <Tag>{publishOutcomeLabel(outcome)}</Tag>
                </Space>
                {outcome === "outcome_uncertain" ? (
                  <Alert
                    type={OUTCOME_TONES[outcome]}
                    showIcon
                    title="需要你到该平台核对一次"
                    description={OUTCOME_UNCERTAIN_HINT}
                  />
                ) : null}
                {snapshot.retryable && snapshot.target !== null && selectedVideo !== undefined ? (
                  <Button
                    disabled={busy || !publishable}
                    onClick={() =>
                      void run(() =>
                        gateway.beginPublish(publishRequest(snapshot.target as PublishPlatform)),
                      )
                    }
                  >
                    重新发布
                  </Button>
                ) : null}
              </Space>
            )}
          </Space>
        </Card>
      )}
    </Space>
  );
}

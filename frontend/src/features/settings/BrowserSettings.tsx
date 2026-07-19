import { Alert, Button, Card, Flex, Radio, Space, Spin, Typography } from "antd";
import { useEffect, useState } from "react";

import type {
  BrowserSettingsSnapshot,
  PlatformAdapter,
  SupportedBrowserId,
} from "../../platform/types";

const SAFE_FAILURE_MESSAGE = "暂时无法读取浏览器设置。请稍后重试。";

const BROWSER_LABELS: Record<SupportedBrowserId, string> = {
  google_chrome: "Google Chrome",
  microsoft_edge: "Microsoft Edge",
};

interface BrowserSettingsProps {
  readonly platform: PlatformAdapter;
}

export function BrowserSettings({ platform }: BrowserSettingsProps) {
  const [snapshot, setSnapshot] = useState<BrowserSettingsSnapshot | null>(null);
  const [draft, setDraft] = useState<SupportedBrowserId | null>(null);
  const [failure, setFailure] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    void platform
      .getBrowserSettings()
      .then((nextSnapshot) => {
        if (active) {
          setSnapshot(nextSnapshot);
          setDraft(nextSnapshot.selectedBrowser);
          setFailure(false);
        }
      })
      .catch(() => {
        if (active) {
          setFailure(true);
        }
      });
    return () => {
      active = false;
    };
  }, [platform]);

  const save = async () => {
    if (draft === null) {
      return;
    }
    setSaving(true);
    setFailure(false);
    try {
      const nextSnapshot = await platform.selectBrowser(draft);
      setSnapshot(nextSnapshot);
      setDraft(nextSnapshot.selectedBrowser);
    } catch {
      setFailure(true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title="运营浏览器" className="browser-settings-card">
      <Space orientation="vertical" size="middle" className="browser-settings-stack">
        <Typography.Text type="secondary">
          只使用系统中签名可信的 Chrome 或 Edge；运营登录保存在 App 独立 Profile，不读取你的日常浏览器资料。
        </Typography.Text>
        {failure ? <Alert type="error" showIcon message={SAFE_FAILURE_MESSAGE} /> : null}
        {snapshot === null && !failure ? (
          <Flex justify="center" align="center" className="browser-settings-loading">
            <Spin description="正在检查受支持的浏览器" />
          </Flex>
        ) : snapshot?.availableBrowsers.length === 0 ? (
          <Alert
            type="warning"
            showIcon
            message="未发现受支持的浏览器"
            description="请在系统标准位置安装 Google Chrome 或 Microsoft Edge 后重新打开设置。"
          />
        ) : snapshot === null ? null : (
          <>
            <Radio.Group
              aria-label="运营浏览器选择"
              value={draft}
              onChange={(event) => setDraft(event.target.value as SupportedBrowserId)}
            >
              <Space orientation="vertical">
                {snapshot.availableBrowsers.map((browser) => (
                  <Radio key={browser} value={browser}>
                    {BROWSER_LABELS[browser]}
                  </Radio>
                ))}
              </Space>
            </Radio.Group>
            <Typography.Text>
              {snapshot.selectedBrowser === null
                ? "尚未选择运营浏览器"
                : `当前选择：${BROWSER_LABELS[snapshot.selectedBrowser]}`}
            </Typography.Text>
            <div>
              <Button
                type="primary"
                loading={saving}
                disabled={draft === null || draft === snapshot.selectedBrowser}
                onClick={() => void save()}
              >
                保存浏览器选择
              </Button>
            </div>
          </>
        )}
      </Space>
    </Card>
  );
}

import { App as AntDesignApp, ConfigProvider, Flex, Typography } from "antd";

export function App() {
  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: "#2f6fed",
          borderRadius: 8,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif',
        },
      }}
    >
      <AntDesignApp>
        <main className="desktop-bootstrap">
          <Flex vertical align="center" gap={8}>
            <Typography.Title level={2}>自动化运营工具</Typography.Title>
            <Typography.Text type="secondary">桌面工作台正在初始化</Typography.Text>
          </Flex>
        </main>
      </AntDesignApp>
    </ConfigProvider>
  );
}

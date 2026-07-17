import { Badge, Card, Col, Flex, Layout, Menu, Row, Space, Statistic, Tag, Typography } from "antd";

const navigationItems = [
  { key: "workbench", label: "工作台" },
  { key: "task-create", label: "新建任务", disabled: true },
  { key: "task-runs", label: "任务记录", disabled: true },
  { key: "platform", label: "平台状态", disabled: true },
  { key: "diagnostics", label: "设置与诊断", disabled: true },
];

const overviewItems = [
  { title: "当前运行任务", value: 0 },
  { title: "待人工处理", value: 0 },
  { title: "今日已完成", value: 0 },
];

export function WorkbenchShell() {
  return (
    <Layout className="desktop-shell">
      <Layout.Sider className="desktop-sidebar" width={232} theme="light">
        <Flex className="desktop-brand" align="center" gap={12}>
          <div className="brand-mark brand-mark--small" aria-hidden="true">
            A
          </div>
          <Space orientation="vertical" size={0}>
            <Typography.Text strong>自动化运营工具</Typography.Text>
            <Typography.Text className="brand-caption">RPA 运营</Typography.Text>
          </Space>
        </Flex>
        <nav aria-label="桌面主导航">
          <Menu mode="inline" selectedKeys={["workbench"]} items={navigationItems} />
        </nav>
        <div className="sidebar-status">
          <Badge status="processing" text="桌面端已就绪" />
        </div>
      </Layout.Sider>

      <Layout>
        <Layout.Header className="desktop-header">
          <Typography.Text type="secondary">抖音运营 MVP</Typography.Text>
          <Tag color="blue">本机桌面模式</Tag>
        </Layout.Header>
        <Layout.Content className="desktop-content">
          <main>
            <Flex justify="space-between" align="end" gap={24}>
              <Space orientation="vertical" size={4}>
                <Typography.Title level={2}>RPA 运营工作台</Typography.Title>
                <Typography.Text type="secondary">
                  从一个真实平台、一个任务闭环开始，执行过程可见、可暂停、可接管。
                </Typography.Text>
              </Space>
              <Tag variant="filled" color="green">
                工作台已就绪
              </Tag>
            </Flex>

            <Row className="overview-grid" gutter={16}>
              {overviewItems.map((item) => (
                <Col span={8} key={item.title}>
                  <Card>
                    <Statistic title={item.title} value={item.value} />
                  </Card>
                </Col>
              ))}
            </Row>

            <Card className="empty-workbench-card">
              <Space orientation="vertical" size={10} align="center">
                <div className="empty-orbit" aria-hidden="true">
                  <span />
                </div>
                <Typography.Title level={4}>还没有运行中的任务</Typography.Title>
                <Typography.Text type="secondary">
                  后续接入 Control Plane 与本地执行器后，可从这里创建第一条抖音运营任务。
                </Typography.Text>
              </Space>
            </Card>
          </main>
        </Layout.Content>
      </Layout>
    </Layout>
  );
}

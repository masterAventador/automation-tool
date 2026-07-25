import { Alert, Card, Space, Tag, Typography } from "antd";

import {
  ASSET_RIGHTS_NOTICE,
  MOTION_ASSET_RIGHTS_NOTICE,
  UPSTREAM_PROJECT_NOTICES,
} from "./third-party-software-notice";

/**
 * 开源软件许可（原「第三方软件声明」）。
 *
 * 这是产品里唯一一处允许写出上游开源项目原始名称的页面，路径已在
 * `contracts/quality/user-facing-terminology.v1.json` 的
 * `allowedLegalDisclosurePaths` 中登记。页面上的版本、许可证和数量都从锁定
 * 契约读出，不在这里重抄一份。
 *
 * 2026-07-26 降权：入口从左侧主导航挪到「设置与诊断」页脚，页面内容收敛到许可证
 * 强制要求的事实（名称、产品功能、固定版本、许可证、固定提交、源码获取地址、只读
 * 引用声明）。删掉的是内部权利核查进度（134 个动效零件的核查计数、"尚未核实"的
 * 网络字体与示例素材、"初步判定"的外部程序包清单）——那是工作台账，不是许可证
 * 公示，而且它谈的素材本构建一件都没分发。详见
 * `docs/development/FIX-open-source-notice-demotion.md`。
 */
export function ThirdPartySoftwareNotice() {
  return (
    <Space orientation="vertical" size="large" className="legal-notice-stack">
      <Alert
        type="info"
        showIcon
        title="本页是公示页面，不影响任何正在运行的任务。"
        description="这里列出本产品分发的开源组件、它们的许可证、固定版本和源码获取地址。"
      />

      <Card title="上游开源项目">
        <section role="region" aria-label="上游开源项目">
          <Typography.Paragraph type="secondary">
            下面两个项目按固定版本以只读方式引用，产品不修改它们的源码。
          </Typography.Paragraph>
          <ul className="legal-notice-list">
            {UPSTREAM_PROJECT_NOTICES.map((project) => (
              <li key={project.id} className="legal-notice-item">
                <Typography.Title level={4}>{project.name}</Typography.Title>
                <Space size={8} wrap>
                  <Tag color="blue">产品功能：{project.productFeature}</Tag>
                  <Tag>版本：{project.version}</Tag>
                  <Tag>许可证：{project.license}</Tag>
                </Space>
                <Typography.Paragraph>{project.usage}</Typography.Paragraph>
                <Typography.Paragraph type="secondary">
                  {project.boundary}
                </Typography.Paragraph>
                <Typography.Text type="secondary">
                  项目仓库：{project.repository} · {project.sourceUrl}
                </Typography.Text>
                <Typography.Text type="secondary">
                  固定提交：{project.commit}
                </Typography.Text>
              </li>
            ))}
          </ul>
        </section>
      </Card>

      <Card title="字体与素材权利">
        <section role="region" aria-label="字体与素材权利">
          {ASSET_RIGHTS_NOTICE.registeredEntryCount === 0 ? (
            <Typography.Paragraph>
              本产品尚未随安装包分发任何第三方字体、图片、音频或视频素材；没有登记齐权利信息的素材，
              一律不随安装包分发。
            </Typography.Paragraph>
          ) : (
            <Typography.Paragraph>
              已登记 {ASSET_RIGHTS_NOTICE.registeredEntryCount}{" "}
              条可随安装包分发的素材；没有登记齐权利信息的素材，一律不随安装包分发。
            </Typography.Paragraph>
          )}
          <Typography.Paragraph type="secondary">
            上游代码本身使用 {MOTION_ASSET_RIGHTS_NOTICE.codeLicense} 许可证，但它只覆盖代码，
            不授予肖像、字体、音频、商标和示例素材的再分发权利。
          </Typography.Paragraph>
        </section>
      </Card>

      <Card title="为什么这些名称只出现在本页">
        <section role="region" aria-label="为什么这些名称只出现在本页">
          <Typography.Paragraph>
            产品界面里只使用「智能素材成片」和「品牌动效成片」两个功能名称：你要挑的是做出什么效果，
            而不是背后用了谁写的代码。
          </Typography.Paragraph>
          <Typography.Paragraph>
            开源许可证要求保留并公示原始项目名称、版权声明和许可证条款，所以这些名称集中公示在本页，
            也只出现在本页；菜单、按钮、任务名称、导出文件名和错误提示里都不会出现它们。
          </Typography.Paragraph>
        </section>
      </Card>
    </Space>
  );
}

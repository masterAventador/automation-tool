import { Alert, Card, Space, Tag, Typography } from "antd";

import {
  ASSET_RIGHTS_NOTICE,
  MOTION_ASSET_RIGHTS_NOTICE,
  UPSTREAM_PROJECT_NOTICES,
} from "./third-party-software-notice";

/**
 * 第三方软件声明。
 *
 * 这是产品里唯一一处允许写出上游开源项目原始名称的页面，路径已在
 * `contracts/quality/user-facing-terminology.v1.json` 的
 * `allowedLegalDisclosurePaths` 中登记。页面上的版本、许可证和数量都从锁定
 * 契约读出，不在这里重抄一份。
 */
export function ThirdPartySoftwareNotice() {
  return (
    <Space orientation="vertical" size="large" className="legal-notice-stack">
      <Alert
        type="info"
        showIcon
        title="本页是公示页面，不影响任何正在运行的任务。"
        description="这里列出本产品用到的开源代码、它们的许可证，以及字体和素材的权利结论。"
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
          <Typography.Paragraph>
            素材权利默认拒绝：没有登记齐权利信息的字体、图片、音频和视频，一律不随安装包分发。
          </Typography.Paragraph>
          <Typography.Paragraph>
            每一条要随安装包分发的素材，都必须先登记齐{" "}
            {ASSET_RIGHTS_NOTICE.sharedRequiredFieldCount} 项通用权利信息。
          </Typography.Paragraph>
          <ul className="legal-notice-tags">
            {ASSET_RIGHTS_NOTICE.categories.map((category) => (
              <li key={category.id}>
                <Typography.Text strong>{category.label}</Typography.Text>
                <Typography.Text type="secondary">
                  另需 {category.requiredFieldCount} 项专门信息
                </Typography.Text>
              </li>
            ))}
          </ul>
          {ASSET_RIGHTS_NOTICE.registeredEntryCount === 0 ? (
            <Typography.Paragraph>
              目前登记册是空的，也就是说本产品尚未随安装包分发任何第三方字体、图片、音频或视频素材。
            </Typography.Paragraph>
          ) : (
            <Typography.Paragraph>
              已登记 {ASSET_RIGHTS_NOTICE.registeredEntryCount} 条可随安装包分发的素材。
            </Typography.Paragraph>
          )}

          <Typography.Title level={4}>动效零件的权利结论</Typography.Title>
          <Typography.Paragraph>
            {`已核查 ${String(MOTION_ASSET_RIGHTS_NOTICE.totalPartCount)} 个动效零件：${String(
              MOTION_ASSET_RIGHTS_NOTICE.clearedPartCount,
            )} 个可直接使用，${String(
              MOTION_ASSET_RIGHTS_NOTICE.partsNeedingWorkCount,
            )} 个必须先本地化或更换素材才能随产品分发。`}
          </Typography.Paragraph>
          <Typography.Paragraph>
            {`其中 ${String(
              MOTION_ASSET_RIGHTS_NOTICE.webFontFamilyCount,
            )} 个网络字体家族与 ${String(
              MOTION_ASSET_RIGHTS_NOTICE.bundledSampleAssetPartCount,
            )} 个自带示例素材的权利尚未核实。`}
          </Typography.Paragraph>
          <Typography.Paragraph type="secondary">
            上游代码本身使用 {MOTION_ASSET_RIGHTS_NOTICE.codeLicense} 许可证，但它只覆盖代码，
            不授予肖像、字体、音频、商标和示例素材的再分发权利。
          </Typography.Paragraph>

          <Typography.Title level={4}>动效零件引用的外部程序包</Typography.Title>
          <Typography.Paragraph type="secondary">
            以下许可证为初步判定，尚未逐项核实；随产品分发前必须改为本机文件并完成核实。
          </Typography.Paragraph>
          <ul className="legal-notice-list">
            {MOTION_ASSET_RIGHTS_NOTICE.dependencies.map((dependency) => (
              <li key={dependency.name}>
                <Typography.Text strong>{dependency.name}</Typography.Text>
                <Typography.Text type="secondary">
                  许可证：{dependency.license}
                </Typography.Text>
                <Typography.Text type="secondary">
                  被 {dependency.partCount} 个零件使用
                </Typography.Text>
              </li>
            ))}
          </ul>
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

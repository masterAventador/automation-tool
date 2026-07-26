import { Alert, Card, Collapse, Space, Tag, Typography } from "antd";

import { collapseExpandIcon } from "../../../components/collapse-expand-icon";
import {
  ASSET_RIGHTS_NOTICE,
  DISTRIBUTED_COMPONENT_NOTICES,
  LICENSE_TEXTS,
  LICENSE_TEXT_LABELS,
  MOTION_ASSET_RIGHTS_NOTICE,
  UPSTREAM_PROJECT_NOTICES,
} from "./third-party-software-notice";

/**
 * 开源软件许可（原「第三方软件声明」）。
 *
 * 这是产品里唯一一处允许写出上游开源项目原始名称的页面，路径已在
 * `contracts/quality/user-facing-terminology.v1.json` 的
 * `allowedLegalDisclosurePaths` 中登记。页面上的版本、许可证、版权行和包内路径都从
 * 锁定契约投影读出，不在这里重抄一份。
 *
 * 2026-07-26 降权：入口从左侧主导航挪到「设置与诊断」页脚。
 *
 * 2026-07-26 补齐许可证义务：降权后复核发现，本页当时只写了组件名、版本和许可证标识，
 * 既没有版权行、没有任何许可证全文，也完全没有提到安装包里那对 GPL 的音视频程序。
 * 「公示了名字」不等于「履行了许可证」——MIT 与 Apache-2.0 要求随分发物交付版权声明和
 * 许可证正文，GPL-3.0 还要求告知对应源码的获取方式。现在这三件事都落在页面上，并由
 * `scripts/check_third_party_notice_ui_projection.py` 兜底。详见
 * `docs/development/FIX-open-source-license-obligations.md`。
 */
export function ThirdPartySoftwareNotice() {
  return (
    <Space orientation="vertical" size="large" className="legal-notice-stack">
      <Alert
        type="info"
        showIcon
        title="本页是公示页面，不影响任何正在运行的任务。"
        description="这里列出本产品随安装包分发的开源组件、它们的版权声明、许可证全文，以及源码的获取方式。"
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
                <Typography.Text>版权声明：{project.copyright}</Typography.Text>
                <Typography.Text type="secondary">
                  项目仓库：{project.repository} · {project.sourceUrl}
                </Typography.Text>
                <Typography.Text type="secondary">
                  固定提交：{project.commit}
                </Typography.Text>
                <Typography.Text type="secondary">
                  许可证全文见本页「许可证全文」一节
                  {project.packagedNoticePath === null
                    ? ""
                    : `；安装包内同样附带一份：${project.packagedNoticePath}`}
                </Typography.Text>
              </li>
            ))}
          </ul>
        </section>
      </Card>

      <Card title="随安装包分发的第三方组件">
        <section role="region" aria-label="随安装包分发的第三方组件">
          <Typography.Paragraph type="secondary">
            下面这些程序会随安装包一起装到你的电脑上。它们各自的许可证要求随分发物提供许可证正文；
            其中按 GPL 授权的部分还要求提供对应的完整源码，这些源码同样已经放进安装包。
          </Typography.Paragraph>
          <ul className="legal-notice-list">
            {DISTRIBUTED_COMPONENT_NOTICES.map((component) => (
              <li key={component.id} className="legal-notice-item">
                <Typography.Title level={4}>{component.name}</Typography.Title>
                <Space size={8} wrap>
                  <Tag>版本：{component.version}</Tag>
                  <Tag color={component.copyleft ? "orange" : "default"}>
                    许可证：{component.license}
                  </Tag>
                </Space>
                <Typography.Paragraph>{component.role}</Typography.Paragraph>
                {component.copyright === null ? null : (
                  <Typography.Paragraph>
                    版权声明：{component.copyright}
                  </Typography.Paragraph>
                )}
                <Typography.Paragraph type="secondary">
                  {component.noticeHint}
                </Typography.Paragraph>
                {component.packagedNoticePath === null ? null : (
                  <Typography.Text type="secondary">
                    安装包内许可证文件：{component.packagedNoticePath}
                  </Typography.Text>
                )}
                {component.packagedSourcePaths.map((path) => (
                  <Typography.Text key={path} type="secondary">
                    安装包内对应源码：{path}
                  </Typography.Text>
                ))}
                {component.upstreamSourceUrl === null ? null : (
                  <Typography.Text type="secondary">
                    源码原始下载地址：{component.upstreamSourceUrl}
                  </Typography.Text>
                )}
              </li>
            ))}
          </ul>
        </section>
      </Card>

      <Card title="许可证全文">
        <section role="region" aria-label="许可证全文">
          <Typography.Paragraph type="secondary">
            下面是上述组件适用的许可证正文。法律文本以英文原文为准，不作翻译；点开即可完整阅读，
            不需要联网，也不需要打开安装目录。
          </Typography.Paragraph>
          <Collapse
            /*
             * Supplying the arrow to keep it out of the header's name; see
             * `components/collapse-expand-icon`. antd's default arrow is
             * labelled "collapsed"/"expanded", and a name computed from content
             * swallows it, so these four reported as
             * 「collapsed MIT 许可证全文（英文原文）」 and so on.
             */
            expandIcon={collapseExpandIcon}
            items={Object.entries(LICENSE_TEXTS).map(([identifier, text]) => ({
              key: identifier,
              label: `${LICENSE_TEXT_LABELS[identifier] ?? identifier} 许可证全文（英文原文）`,
              children: (
                <pre
                  className="legal-notice-license-text"
                  data-testid={`license-text-${identifier}`}
                >
                  {text}
                </pre>
              ),
            }))}
          />
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

      <Card title="尚未逐项公示的部分">
        <section role="region" aria-label="尚未逐项公示的部分">
          <Typography.Paragraph>
            本页目前只逐项列出了上面这些组件。安装包里还有三类开源代码尚未在这里逐条列出：
            产品界面本身用到的开源组件、本机执行器运行环境里随包分发的组件，以及把它们打包成
            可执行程序的打包引导程序。
          </Typography.Paragraph>
          <Typography.Paragraph type="secondary">
            这些组件的许可证同样要求公示，补齐工作已经登记在案。在补齐之前，本页不声称这是一份
            完整清单。
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

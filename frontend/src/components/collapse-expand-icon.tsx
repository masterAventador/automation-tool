import RightOutlined from "@ant-design/icons/RightOutlined";
import type { ReactNode } from "react";

/**
 * The arrow for an antd `Collapse`, drawn exactly as antd draws it but kept out
 * of the header's accessible name.
 *
 * antd's default expand icon is
 *
 * ```js
 * // antd/es/collapse/Collapse.js
 * React.createElement(RightOutlined, {
 *   rotate: panelProps.isActive ? 90 : undefined,
 *   "aria-label": panelProps.isActive ? 'expanded' : 'collapsed',
 * })
 * ```
 *
 * — and `AntdIcon` renders that as `<span role="img" aria-label="collapsed">`.
 * The panel header is a `role="button"`, whose name is computed from its
 * content, so it swallows the arrow's label and Chromium reports the control as
 *
 * ```text
 * button "collapsed 诊断信息"
 * ```
 *
 * An English state word in the middle of a Chinese label, and redundant on top
 * of that: the same header already carries `aria-expanded`, which is where a
 * screen reader is supposed to read the state from. The `<svg>` inside is
 * already `aria-hidden`; the wrapper span is the only thing leaking.
 *
 * So this passes the same component with the same `rotate`, and marks it
 * `aria-hidden` instead of labelling it. `aria-hidden` removes the whole
 * subtree from the accessibility tree, so the name computation never sees it
 * and the header is named by its title alone. Nothing about the rendering
 * changes: same icon, same rotation, and antd still adds `ant-collapse-arrow`
 * to whatever this returns (`cloneElement` in `renderExpandIcon`).
 *
 * Use this for every `Collapse` in the product. `VideoStudio` solves the same
 * problem with a CSS-drawn arrow of its own, which is why it does not call this.
 */
export function collapseExpandIcon(panelProps: { readonly isActive?: boolean }): ReactNode {
  // `rotate` is spread in rather than passed as `undefined`: the project builds
  // with `exactOptionalPropertyTypes`, and antd's own default reads `rotate` for
  // truthiness, so an omitted prop and an explicit `undefined` render alike.
  return (
    <RightOutlined {...(panelProps.isActive === true ? { rotate: 90 } : {})} aria-hidden="true" />
  );
}

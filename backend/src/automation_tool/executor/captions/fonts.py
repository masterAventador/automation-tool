"""Caption font registry: a closed key set mapped to rights-registered faces."""

from __future__ import annotations

from typing import Final

# font_key -> the packagedName recorded in
# contracts/quality/asset-rights-policy.v1.json. This map is that contract's
# `defaultDecision: "deny"` made concrete: a face it does not name is a face
# the product does not ship, so an unregistered key can never reach the
# filesystem.
REGISTERED_CAPTION_FONTS: Final[dict[str, str]] = {
    "noto-sans-cjk-sc-bold": "NotoSansCJKsc-Bold.ttf",
    "noto-sans-cjk-sc-regular": "NotoSansCJKsc-Regular.ttf",
    "big-shoulders-display": "big-shoulders-display-latin.woff2",
}

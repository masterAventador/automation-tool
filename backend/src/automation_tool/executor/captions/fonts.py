"""Caption font registry: a closed key set mapped to rights-registered faces."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

# The bundles carrying the faces the caption renderer draws with. A bundle is
# the unit the rights register packs a face into, and it is also the namespace
# its file name lives in: `packagedName` is unique inside a bundle, not across
# the product.
MATERIAL_VIDEO_WORKER_BUNDLE: Final = "material-video-worker"
MOTION_CATALOG_OVERLAY_BUNDLE: Final = "motion-catalog-overlay"


@dataclass(frozen=True, slots=True)
class RegisteredCaptionFont:
    """One face the caption renderer may draw with.

    Both fields come from a single entry of
    `contracts/quality/asset-rights-policy.v1.json`, which declares
    `defaultDecision: "deny"` and therefore doubles as the packing list. The
    bundle has to travel with the name because the two are only meaningful
    together: the Noto faces are fetched at build time into the material video
    Worker's bundle, while the Big Shoulders face is committed under the
    motion overlay's, so a bare file name does not say where the face lives.
    """

    packaged_name: str
    bundle: str


# font_key -> the cleared face it names. The key arrives from user settings,
# so this map is the whole of what a caller may ask for: a face it does not
# name is a face the product does not ship, and an unregistered key therefore
# never reaches the filesystem.
#
# A read-only mapping rather than a plain dict: `Final` stops the name being
# rebound but not the contents being edited, and a closed set that can be
# added to at runtime is not closed.
REGISTERED_CAPTION_FONTS: Final[Mapping[str, RegisteredCaptionFont]] = MappingProxyType(
    {
        "noto-sans-cjk-sc-bold": RegisteredCaptionFont(
            packaged_name="NotoSansCJKsc-Bold.ttf",
            bundle=MATERIAL_VIDEO_WORKER_BUNDLE,
        ),
        "noto-sans-cjk-sc-regular": RegisteredCaptionFont(
            packaged_name="NotoSansCJKsc-Regular.ttf",
            bundle=MATERIAL_VIDEO_WORKER_BUNDLE,
        ),
        "big-shoulders-display": RegisteredCaptionFont(
            packaged_name="big-shoulders-display-latin.woff2",
            bundle=MOTION_CATALOG_OVERLAY_BUNDLE,
        ),
    }
)

# Noto Sans CJK SC, not one of the latin faces: a latin default renders every
# Chinese caption as boxes while the render still reports success.
DEFAULT_CAPTION_FONT_KEY: Final = "noto-sans-cjk-sc-bold"

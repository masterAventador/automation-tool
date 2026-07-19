"""Shared strict JSON-object decoding for bounded process boundaries."""

from __future__ import annotations

import json


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    decoded: dict[str, object] = {}
    for key, value in pairs:
        if key in decoded:
            raise ValueError("duplicate JSON key")
        decoded[key] = value
    return decoded


def decode_bounded_json_object(value: str | bytes, *, maximum_bytes: int) -> dict[str, object]:
    """Decode one bounded UTF-8 JSON object without accepting duplicate keys."""

    if type(value) is bytes:
        if len(value) > maximum_bytes:
            raise ValueError("JSON input is too large")
        source = value.decode("utf-8")
    elif type(value) is str:
        if len(value.encode("utf-8")) > maximum_bytes:
            raise ValueError("JSON input is too large")
        source = value
    else:
        raise TypeError("JSON input must be text")
    decoded = json.loads(source, object_pairs_hook=_unique_object)
    if not isinstance(decoded, dict):
        raise ValueError("JSON input must be an object")
    return decoded


__all__ = ["decode_bounded_json_object"]

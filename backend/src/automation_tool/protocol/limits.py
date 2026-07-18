"""Numeric limits that must remain lossless across Python, Rust, and TypeScript."""

MAX_CROSS_RUNTIME_SEQUENCE = 2**53 - 1

__all__ = ["MAX_CROSS_RUNTIME_SEQUENCE"]

"""The editing-project persistence boundary's failure vocabulary."""

from __future__ import annotations


class EditingProjectRepositoryRejected(RuntimeError):
    """The repository refused a request and says nothing about why.

    The message is fixed and any argument is dropped, so a caller that reaches
    for `raise ... (detail)` cannot smuggle a connection string, a stored value
    or a private path into whatever logs this.
    """

    def __init__(self, *_ignored: object) -> None:
        super().__init__("Editing project repository rejected the request")


__all__ = ["EditingProjectRepositoryRejected"]

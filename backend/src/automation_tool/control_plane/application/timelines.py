"""The timeline persistence boundary's failure vocabulary.

Five outcomes, because a caller has five different things to do about them. A
revision that is already stored means this cut exists and the caller has to pick
a new revision -- revisions are immutable snapshots, so there is nothing to
merge into. A project that is not stored means the *other* resource is missing,
which is a different thing to report and a different thing to fix. A missing
timeline is a question that got an answer. An unavailable database is worth
retrying. A rejected argument is a bug upstairs. Collapsing them makes every
caller guess, and leaves the REST layer above answering 409, 404 and 503 with
the same status.

Every message is a fixed string and no constructor takes an argument, so nothing
reaching a log through one of these can carry a connection string, an identifier
or a private path -- and `raise ... ("detail")` is a `TypeError` at the call
site rather than a leak. That matters more here than it looks: PostgreSQL's
DETAIL line on both of the integrity failures below quotes the offending key
values verbatim, so the tempting thing to attach is exactly the thing that must
not travel.
"""

from __future__ import annotations


class _TimelinePersistenceFailure(RuntimeError):
    message = "Timeline persistence failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class TimelineRevisionAlreadyStored(_TimelinePersistenceFailure):
    """This `(timeline_id, revision)` is taken, and a revision is write-once.

    Deliberately not phrased as "already registered" like the two tables before
    it: what is taken is one revision, not the timeline. Storing revision 4 of a
    timeline whose revision 3 exists is the ordinary case, not a conflict.
    """

    message = "Timeline revision is already stored"


class TimelineProjectMissing(_TimelinePersistenceFailure):
    """The project this timeline belongs to is not in the database.

    A caller error rather than a corrupt row: the timeline itself is well
    formed, and `EditingProjectId` cannot tell whether a project was ever
    stored -- no domain object holds a reference to another aggregate, which is
    why this can only be caught here. Retrying with the same timeline never
    succeeds; storing the project first does.
    """

    message = "Timeline names a project that is not stored"


class TimelineNotFound(_TimelinePersistenceFailure):
    message = "Timeline revision was not found"


class TimelineDataRejected(_TimelinePersistenceFailure):
    """The argument, or the row it would have written, was refused.

    Covers both a caller handing over something that is not a `Timeline` and a
    constraint violation this module does not have a specific answer for. The
    two share what a caller must do: fix the input. Neither improves on a retry,
    which is what separates them from `TimelinePersistenceUnavailable`.
    """

    message = "Timeline data is rejected"


class TimelinePersistenceUnavailable(_TimelinePersistenceFailure):
    message = "Timeline persistence is unavailable"


__all__ = [
    "TimelineDataRejected",
    "TimelineNotFound",
    "TimelinePersistenceUnavailable",
    "TimelineProjectMissing",
    "TimelineRevisionAlreadyStored",
]

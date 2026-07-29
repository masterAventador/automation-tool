"""The editing job persistence boundary's failure vocabulary.

Seven outcomes, because a caller has seven different things to do about them,
and this is the first table in LE-05 whose rows change after they are written --
which is where the extra ones come from.

Three say a write was refused and name which rule refused it. A job identifier
that is already registered means this job exists. A revision that already has a
queued render means somebody else asked for the same thing first -- a different
resource and a different message, even though PostgreSQL reports both as the
same code. A timeline revision that is not stored means the job names something
that is not there, which is 404 about *another* resource.

Two say an update did not land, and they are not the same instruction. A job
that is not stored will not appear on a reload. A stale one will: something has
overtaken the caller's snapshot, and reloading gives it a next move.

The last two are the pair every repository here has: an unavailable database is
worth retrying, and a rejected argument is a bug upstairs.

Every message is a fixed string and no constructor takes an argument, so nothing
reaching a log through one of these can carry a connection string, an identifier
or a private path -- and `raise ... ("detail")` is a `TypeError` at the call
site rather than a leak. That matters more here than it looks: PostgreSQL's
DETAIL line on all three integrity failures quotes the offending key values
verbatim, and for the composite foreign key that is three of them at once, so
the tempting thing to attach is exactly the thing that must not travel.
"""

from __future__ import annotations


class _EditingJobPersistenceFailure(RuntimeError):
    message = "Editing job persistence failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class EditingJobAlreadyRegistered(_EditingJobPersistenceFailure):
    """This `job_id` is taken, and `save` creates rather than merges."""

    message = "Editing job is already registered"


class EditingJobRevisionAlreadyQueued(_EditingJobPersistenceFailure):
    """This timeline revision already has a render waiting to start.

    Deliberately separate from `EditingJobAlreadyRegistered`, even though
    PostgreSQL reports both as SQLSTATE `23505`: the caller's mistake is a
    different one. A duplicate identifier means "you have used this name
    already"; this one means "the work you are asking for is already queued",
    and the useful next move is to wait for or look up the existing render
    rather than to pick a new identifier.

    Only *queued* rows are exclusive. A revision that has been rendered, failed
    or cancelled can be queued again, which is what makes a retry possible at
    all -- see the index's predicate in `schema.py`.
    """

    message = "Editing job revision already has a queued render"


class EditingJobTimelineRevisionMissing(_EditingJobPersistenceFailure):
    """No stored timeline revision matches this job's three reference columns.

    One answer covers two rules, because one composite foreign key enforces
    both and PostgreSQL cannot say which half failed: the revision may not exist
    at all, or it may exist under a *different* project than the one the job
    claims. Splitting the answer would mean guessing, and the honest statement
    -- "there is no revision of that timeline, under that project, with that
    number" -- is true either way and points at the same fix.

    A caller error rather than a corrupt row, and no retry improves on it. It
    can only be caught here: no domain object holds a reference to another
    aggregate, so `EditingJob` cannot ask which project a timeline belongs to,
    and an application-layer check on that is one two concurrent callers both
    pass.
    """

    message = "Editing job names a timeline revision that is not stored"


class EditingJobNotFound(_EditingJobPersistenceFailure):
    message = "Editing job was not found"


class EditingJobStale(_EditingJobPersistenceFailure):
    """The stored row is no longer the one this update was computed from.

    Two predicates can refuse an update and both mean this. The row's
    `updated_at` may already be at or past the one being written, so the caller
    is replaying or has been overtaken. Or the row's `status` may not be one the
    requested state can legally be reached from -- a job the caller believes is
    cancelling has already succeeded, say -- in which case the transition was
    checked against a snapshot that has since stopped being true.

    They are one outcome because they are one instruction: reload the job and
    decide again. Both leave the stored row untouched.

    **For the layers above:** this is 409 rather than 404, and it must not be
    collapsed into `EditingJobNotFound`. A caller told "not found" about a job
    that exists will stop asking about a render that is still running, and a
    reconciliation pass told the same thing has no way to learn that the worker
    beat it to the finish.
    """

    message = "Editing job was modified by someone else"


class EditingJobDataRejected(_EditingJobPersistenceFailure):
    """The argument, or the row it would have written, was refused.

    Covers both a caller handing over something that is not an `EditingJob` and
    a constraint violation this module has no specific answer for -- including
    a unique violation whose constraint this module does not recognise, where
    naming either specific rule would be a guess about which one broke. The two
    share what a caller must do: fix the input. Neither improves on a retry,
    which is what separates them from `EditingJobPersistenceUnavailable`.
    """

    message = "Editing job data is rejected"


class EditingJobPersistenceUnavailable(_EditingJobPersistenceFailure):
    message = "Editing job persistence is unavailable"


__all__ = [
    "EditingJobAlreadyRegistered",
    "EditingJobDataRejected",
    "EditingJobNotFound",
    "EditingJobPersistenceUnavailable",
    "EditingJobRevisionAlreadyQueued",
    "EditingJobStale",
    "EditingJobTimelineRevisionMissing",
]

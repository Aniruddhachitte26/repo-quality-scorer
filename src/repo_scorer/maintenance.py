"""Maintenance status: is anyone still looking after this project?

Deliberately separate from scoring. The score answers "is the code good?";
this answers "is the project alive?". Mixing them would make the score harder
to explain, so this status is shown *next to* the score, never inside it.

Caveat: pushed_at is GitHub's last push to any branch, so a bot or a single
README fix counts as activity. Treat it as a signal, not a verdict.
"""

from datetime import UTC, datetime

ACTIVE_DAYS = 365
SLOWING_DAYS = 730

LABELS = {
    "active": "Active (pushed within a year)",
    "slowing": "Slowing (no push in 1-2 years)",
    "inactive": "Inactive (no push in 2+ years)",
    "archived": "Archived on GitHub (read-only)",
    "unknown": "Unknown (no metadata)",
}


def maintenance_status(
    archived: bool | None, pushed_at: datetime | None, now: datetime | None = None
) -> str:
    if archived:
        return "archived"
    if pushed_at is None:
        return "unknown"
    age_days = ((now or datetime.now(UTC)) - pushed_at).days
    if age_days <= ACTIVE_DAYS:
        return "active"
    if age_days <= SLOWING_DAYS:
        return "slowing"
    return "inactive"

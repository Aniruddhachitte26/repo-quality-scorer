from datetime import datetime, timedelta, timezone

from repo_scorer.maintenance import LABELS, maintenance_status

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


def test_archived_wins_over_recent_push():
    assert maintenance_status(True, _ago(5), NOW) == "archived"


def test_age_buckets():
    assert maintenance_status(False, _ago(30), NOW) == "active"
    assert maintenance_status(False, _ago(365), NOW) == "active"
    assert maintenance_status(False, _ago(500), NOW) == "slowing"
    assert maintenance_status(False, _ago(731), NOW) == "inactive"


def test_unknown_without_metadata():
    assert maintenance_status(None, None, NOW) == "unknown"


def test_every_status_has_a_label():
    for status in ("active", "slowing", "inactive", "archived", "unknown"):
        assert status in LABELS

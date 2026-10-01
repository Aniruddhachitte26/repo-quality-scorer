import pytest

from repo_scorer.scoring.scorecard import (
    SCORECARD,
    biggest_losses,
    compute_scores,
    grade,
    missing_analyzers,
    scale,
)


def test_scale_higher_is_better():
    assert scale(45, 0, 90) == 50.0
    assert scale(120, 0, 90) == 100.0  # clamped
    assert scale(-5, 0, 90) == 0.0


def test_scale_lower_is_better():
    assert scale(5, 20, 0) == 75.0
    assert scale(0, 20, 0) == 100.0
    assert scale(40, 20, 0) == 0.0


def test_grades():
    assert [grade(s) for s in (95, 85, 75, 65, 10)] == ["A", "B", "C", "D", "F"]


def test_weights_sum_to_one():
    assert sum(w for w, _ in SCORECARD.values()) == pytest.approx(1.0)
    for name, (_, rules) in SCORECARD.items():
        assert sum(r.weight for r in rules) == pytest.approx(1.0), name


def _perfect_metrics():
    v = lambda x: (x, None)  # noqa: E731
    return {
        ("tests", "reachable_from_tests_pct"): v(95), ("tests", "test_to_source_ratio"): v(1.5),
        ("tests", "asserts_per_test"): v(3), ("tests", "has_ci"): v(1),
        ("architecture", "modules_in_cycles"): v(0), ("architecture", "avg_complexity"): v(2),
        ("architecture", "high_complexity_pct"): v(0), ("architecture", "long_function_pct"): v(0),
        ("architecture", "god_classes"): v(0), ("architecture", "modules"): v(10),
        ("documentation", "docstring_coverage"): v(100), ("documentation", "type_hint_coverage"): v(100),
        ("documentation", "readme_score"): v(100), ("documentation", "has_docs_dir"): v(1),
        ("dependencies", "vulnerable_deps"): v(0), ("dependencies", "stale_deps"): v(0),
        ("dependencies", "unpinned_pct"): v(0), ("dependencies", "excludes_latest_pct"): v(0),
        ("duplication", "duplicated_lines_pct"): v(0), ("duplication", "semantic_duplicate_pairs"): v(0),
    }


def test_perfect_repo_scores_100():
    result = compute_scores(_perfect_metrics())
    assert result["overall"] == 100.0
    assert result["grade"] == "A"
    assert biggest_losses(result) == []


def test_losses_point_at_the_weak_metric():
    ms = _perfect_metrics()
    ms[("dependencies", "vulnerable_deps")] = (3, None)  # worst case
    result = compute_scores(ms)
    assert result["categories"]["dependencies"]["score"] == 60.0  # lost the 40% rule
    top = biggest_losses(result)[0]
    assert top["label"] == "vulnerable_deps"
    assert top["overall_points_lost"] == 6.0  # 40% of a 15%-weight category
    assert result["overall"] == 94.0


def test_skipped_embeddings_are_dropped_not_rewarded():
    ms = _perfect_metrics()
    ms[("duplication", "duplicated_lines_pct")] = (7.5, None)  # scores 50
    ms[("duplication", "semantic_duplicate_pairs")] = (0, {"status": "skipped: install"})
    # Only duplicated_lines_pct remains, so the category is 50, not 0.7*50 + 0.3*100 = 65.
    assert compute_scores(ms)["categories"]["duplication"]["score"] == 50.0


def test_missing_analyzers():
    ms = {("tests", "has_ci"): (1, None)}
    assert "documentation" in missing_analyzers(ms)
    assert "tests" not in missing_analyzers(ms)

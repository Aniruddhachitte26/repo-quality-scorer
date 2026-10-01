"""Turn raw analyzer metrics into category scores, an overall score and a grade.

Every rule maps one metric onto 0-100 with scale(value, worst, best):
values at or beyond `worst` score 0, at or beyond `best` score 100, linear between.
Category score = weighted average of its rules. Overall = weighted average of categories.
Rules whose metric is unavailable (e.g. embeddings skipped) are dropped and the
remaining weights renormalized, so a missing optional metric neither helps nor hurts.
"""

from collections.abc import Callable
from dataclasses import dataclass

# {(analyzer, metric): (value, details)}
Metrics = dict[tuple[str, str], tuple[float, dict | None]]
ValueFn = Callable[[Metrics], float | None]

REQUIRED_ANALYZERS = ("documentation", "architecture", "dependencies", "tests", "duplication")


def scale(value: float, worst: float, best: float) -> float:
    """Map value linearly onto 0-100; works whether higher or lower is better."""
    if worst == best:
        return 100.0
    t = (value - worst) / (best - worst)
    return round(100 * min(1.0, max(0.0, t)), 1)


def grade(score: float) -> str:
    for threshold, letter in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
        if score >= threshold:
            return letter
    return "F"


# ---------------------------------------------------------------- metric accessors


def metric(analyzer: str, name: str) -> ValueFn:
    def get(ms: Metrics) -> float | None:
        entry = ms.get((analyzer, name))
        return entry[0] if entry else None
    return get


def god_class_density(ms: Metrics) -> float | None:
    """God classes per module, so big projects aren't punished just for size."""
    gods, modules = ms.get(("architecture", "god_classes")), ms.get(("architecture", "modules"))
    if not gods or not modules or not modules[0]:
        return 0.0 if gods else None
    return gods[0] / modules[0]


def semantic_pairs(ms: Metrics) -> float | None:
    """None when embeddings were skipped, so the rule is dropped instead of scoring 100."""
    entry = ms.get(("duplication", "semantic_duplicate_pairs"))
    if not entry or (entry[1] or {}).get("status", "").startswith("skipped"):
        return None
    return entry[0]


# ---------------------------------------------------------------- the scorecard


@dataclass(frozen=True)
class Rule:
    label: str
    value: ValueFn
    worst: float
    best: float
    weight: float


SCORECARD: dict[str, tuple[float, list[Rule]]] = {
    "tests": (0.30, [
        Rule("reachable_from_tests_pct", metric("tests", "reachable_from_tests_pct"), 20, 90, 0.40),
        Rule("test_to_source_ratio", metric("tests", "test_to_source_ratio"), 0, 1.0, 0.25),
        Rule("asserts_per_test", metric("tests", "asserts_per_test"), 0.5, 2.0, 0.15),
        Rule("has_ci", metric("tests", "has_ci"), 0, 1, 0.20),
    ]),
    "architecture": (0.25, [
        Rule("modules_in_cycles", metric("architecture", "modules_in_cycles"), 30, 0, 0.25),
        Rule("avg_complexity", metric("architecture", "avg_complexity"), 10, 3, 0.20),
        Rule("high_complexity_pct", metric("architecture", "high_complexity_pct"), 20, 0, 0.25),
        Rule("long_function_pct", metric("architecture", "long_function_pct"), 15, 0, 0.15),
        Rule("god_classes_per_module", god_class_density, 0.5, 0, 0.15),
    ]),
    "documentation": (0.15, [
        Rule("docstring_coverage", metric("documentation", "docstring_coverage"), 0, 90, 0.40),
        Rule("type_hint_coverage", metric("documentation", "type_hint_coverage"), 0, 90, 0.20),
        Rule("readme_score", metric("documentation", "readme_score"), 0, 100, 0.30),
        Rule("has_docs_dir", metric("documentation", "has_docs_dir"), 0, 1, 0.10),
    ]),
    "dependencies": (0.15, [
        Rule("vulnerable_deps", metric("dependencies", "vulnerable_deps"), 3, 0, 0.40),
        Rule("stale_deps", metric("dependencies", "stale_deps"), 3, 0, 0.20),
        Rule("unpinned_pct", metric("dependencies", "unpinned_pct"), 50, 0, 0.20),
        Rule("excludes_latest_pct", metric("dependencies", "excludes_latest_pct"), 50, 0, 0.20),
    ]),
    "duplication": (0.15, [
        Rule("duplicated_lines_pct", metric("duplication", "duplicated_lines_pct"), 15, 0, 0.70),
        Rule("semantic_duplicate_pairs", semantic_pairs, 10, 0, 0.30),
    ]),
}


# ---------------------------------------------------------------- computation


def missing_analyzers(ms: Metrics) -> list[str]:
    present = {analyzer for analyzer, _ in ms}
    return [a for a in REQUIRED_ANALYZERS if a not in present]


def compute_scores(ms: Metrics) -> dict:
    categories = {}
    for name, (cat_weight, rules) in SCORECARD.items():
        parts = []
        for rule in rules:
            value = rule.value(ms)
            if value is None:
                continue
            parts.append({
                "label": rule.label,
                "value": value,
                "score": scale(value, rule.worst, rule.best),
                "weight": rule.weight,
                "worst": rule.worst,
                "best": rule.best,
            })
        total_w = sum(p["weight"] for p in parts)
        cat_score = round(sum(p["score"] * p["weight"] for p in parts) / total_w, 1) if total_w else 0.0
        for p in parts:
            # How many points of the *overall* score this component cost.
            p["overall_points_lost"] = round((100 - p["score"]) * p["weight"] / total_w * cat_weight, 2)
        categories[name] = {"score": cat_score, "grade": grade(cat_score),
                            "weight": cat_weight, "components": parts}

    overall = round(sum(c["score"] * c["weight"] for c in categories.values()), 1)
    return {"overall": overall, "grade": grade(overall), "categories": categories}


def biggest_losses(result: dict, n: int = 5) -> list[dict]:
    """Components that cost the most overall points: where to improve first."""
    losses = [
        {"category": cat, **p}
        for cat, c in result["categories"].items()
        for p in c["components"]
        if p["overall_points_lost"] > 0
    ]
    return sorted(losses, key=lambda p: p["overall_points_lost"], reverse=True)[:n]

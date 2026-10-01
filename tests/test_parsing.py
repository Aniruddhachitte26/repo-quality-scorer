import ast
from pathlib import Path

from repo_scorer.parsing.discovery import is_test_path
from repo_scorer.parsing.extractor import count_loc, extract_units, normalized_hash

SAMPLE = '''
"""Module docstring."""

def simple(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def branchy(x):
    if x > 0 and x < 10:
        return 1
    for i in range(x):
        if i % 2:
            continue
    return 0


class Greeter:
    """Says hi."""

    def greet(self, name: str) -> str:
        return f"hi {name}"

    def shout(self, name):
        return name.upper()
'''


def _units():
    return {u.qualname: u for u in extract_units(ast.parse(SAMPLE))}


def test_finds_all_units_with_correct_kinds():
    units = _units()
    assert {q: u.kind for q, u in units.items()} == {
        "simple": "function",
        "branchy": "function",
        "Greeter": "class",
        "Greeter.greet": "method",
        "Greeter.shout": "method",
    }


def test_cyclomatic_complexity():
    units = _units()
    assert units["simple"].complexity == 1
    # 1 + if + `and` + for + inner if
    assert units["branchy"].complexity == 5
    assert units["Greeter"].complexity is None


def test_docstrings_and_type_hints():
    units = _units()
    assert units["simple"].has_docstring
    assert not units["branchy"].has_docstring
    assert units["Greeter"].has_docstring
    assert units["simple"].has_type_hints
    assert units["Greeter.greet"].has_type_hints  # `self` doesn't need a hint
    assert not units["Greeter.shout"].has_type_hints
    assert units["Greeter.greet"].arg_count == 1


def _hash(src: str) -> str:
    return normalized_hash(ast.parse(src).body[0])


def test_renamed_clone_has_same_hash():
    original = (
        "def total(items):\n"
        "    s = 0\n"
        "    for it in items:\n"
        "        s += it.price\n"
        "    return s\n"
    )
    renamed = (
        "def sum_costs(rows):\n"
        '    """Different docstring."""\n'
        "    acc = 0\n"
        "    for r in rows:\n"
        "        acc += r.cost\n"
        "    return acc\n"
    )
    different = "def total(items):\n    return max(items)\n"
    assert _hash(original) == _hash(renamed)
    assert _hash(original) != _hash(different)


def test_count_loc_ignores_blanks_and_comments():
    assert count_loc("x = 1\n\n# comment\n    # indented comment\ny = 2\n") == 2


def test_is_test_path():
    assert is_test_path(Path("tests/test_api.py"))
    assert is_test_path(Path("src/pkg/tests/helpers.py"))
    assert is_test_path(Path("conftest.py"))
    assert is_test_path(Path("pkg/api_test.py"))
    assert not is_test_path(Path("src/pkg/api.py"))
    assert not is_test_path(Path("src/pkg/testing_utils.py"))

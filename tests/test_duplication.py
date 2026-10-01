from types import SimpleNamespace

from repo_scorer.analyzers.duplication import find_clone_groups, is_override_pair, overlaps


def _unit(path, lineno, end, h, loc=10, name="f"):
    return SimpleNamespace(
        path=path, lineno=lineno, end_lineno=end, normalized_hash=h, loc=loc, qualname=name
    )


def test_find_clone_groups_orders_by_wasted_lines():
    units = [
        _unit("a.py", 1, 10, "small", loc=10),
        _unit("b.py", 1, 10, "small", loc=10),
        _unit("a.py", 20, 60, "big", loc=40),
        _unit("c.py", 1, 40, "big", loc=40),
        _unit("d.py", 1, 40, "big", loc=40),
        _unit("e.py", 1, 10, "unique", loc=10),
    ]
    groups = find_clone_groups(units)
    assert [len(g) for g in groups] == [3, 2]  # 'big' wastes 80 lines, 'small' 10
    assert all(u.normalized_hash != "unique" for g in groups for u in g)


def test_overlaps_detects_nesting_only_in_same_file():
    outer = _unit("a.py", 1, 30, "x")
    inner = _unit("a.py", 5, 10, "y")
    elsewhere = _unit("b.py", 5, 10, "z")
    later = _unit("a.py", 40, 50, "w")
    assert overlaps(outer, inner)
    assert not overlaps(outer, elsewhere)
    assert not overlaps(outer, later)


def test_override_pairs_are_not_duplicates():
    base = _unit("a.py", 1, 10, "x", name="BaseAdapter.send")
    child = _unit("a.py", 50, 90, "y", name="HTTPAdapter.send")
    wrapper = _unit("api.py", 1, 10, "z", name="head")
    method = _unit("s.py", 1, 10, "w", name="Session.head")
    sibling = _unit("s.py", 20, 30, "v", name="Session.get")
    assert is_override_pair(base, child)
    assert not is_override_pair(wrapper, method)   # function vs method: keep
    assert not is_override_pair(method, sibling)   # different names: keep

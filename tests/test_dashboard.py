from repo_scorer.dashboard.data import percentile_ranks


def test_percentile_ranks_spread_first_to_last():
    pct = percentile_ranks({1: 90.0, 2: 80.0, 3: 70.0})
    assert pct == {1: 100.0, 2: 50.0, 3: 0.0}


def test_percentile_ranks_ties_share_a_rank():
    pct = percentile_ranks({1: 80.0, 2: 80.0, 3: 60.0})
    assert pct[1] == pct[2] == 50.0
    assert pct[3] == 0.0


def test_percentile_ranks_single_repo():
    assert percentile_ranks({7: 55.0}) == {7: 100.0}

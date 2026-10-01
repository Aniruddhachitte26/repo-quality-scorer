import copy
from types import SimpleNamespace

from repo_scorer.agent.runner import (
    MAX_TOOL_CALLS,
    MAX_TURNS,
    Usage,
    build_brief,
    run_agent,
    strip_preamble,
)
from repo_scorer.agent.tools import TOOL_SPECS, number_lines, truncate


def _usage(i=100, o=50):
    return SimpleNamespace(
        input_tokens=i, output_tokens=o, cache_creation_input_tokens=0, cache_read_input_tokens=0
    )


def _tool_use(name, args, id_="t1"):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=args)


def _text(t):
    return SimpleNamespace(type="text", text=t)


class FakeClient:
    """Plays back scripted responses and records what it was sent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        # Snapshot: the agent keeps mutating its message list after each call.
        self.calls.append(copy.deepcopy(kwargs))
        return self.responses.pop(0)


def test_loop_executes_tools_then_returns_report():
    client = FakeClient([
        SimpleNamespace(stop_reason="tool_use", usage=_usage(),
                        content=[_tool_use("read_code", {"path": "a.py", "qualname": "f"})]),
        SimpleNamespace(stop_reason="end_turn", usage=_usage(), content=[_text("## Summary\nok")]),
    ])
    executed = []

    def execute(name, args):
        executed.append((name, args))
        return "def f(): pass"

    report, usage = run_agent(client, "claude-haiku-4-5-20251001", "brief", TOOL_SPECS, execute)

    assert report == "## Summary\nok"
    assert executed == [("read_code", {"path": "a.py", "qualname": "f"})]
    assert usage.turns == 2 and usage.tool_calls == ["read_code"]
    # The tool result was sent back to the model, linked to the tool_use id.
    result_msg = client.calls[1]["messages"][-1]
    assert result_msg["content"][0]["tool_use_id"] == "t1"
    assert result_msg["content"][0]["is_error"] is False
    # Stable prefix is marked for prompt caching.
    assert client.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_turn_budget_forces_final_report():
    looping = [
        SimpleNamespace(stop_reason="tool_use", usage=_usage(),
                        content=[_tool_use("list_functions", {"sort_by": "loc"}, id_=f"t{i}")])
        for i in range(MAX_TURNS)
    ]
    final = SimpleNamespace(stop_reason="end_turn", usage=_usage(), content=[_text("final")])
    client = FakeClient([*looping, final])

    report, usage = run_agent(client, "m", "brief", TOOL_SPECS, lambda n, a: "result")

    assert report == "final"
    assert client.calls[-1]["tool_choice"] == {"type": "none"}
    assert usage.turns == MAX_TURNS + 1


def test_cost_uses_haiku_prices():
    u = Usage(input_tokens=1_000_000, output_tokens=200_000)
    assert u.cost("claude-haiku-4-5-20251001") == 2.0  # $1 in + $1 out
    assert u.cost("unknown-model") is None


def test_brief_lists_losses():
    result = {"overall": 89.5, "grade": "B", "categories": {
        "tests": {"score": 90.7, "weight": 0.3}}}
    losses = [{"category": "architecture", "label": "high_complexity_pct",
               "value": 5.2, "best": 0, "overall_points_lost": 1.62}]
    brief = build_brief("psf/requests", result, losses)
    assert "89.5 / 100 (grade B)" in brief
    assert "architecture.high_complexity_pct = 5.2" in brief


def test_helpers():
    assert truncate("abc", 10) == "abc"
    assert truncate("x" * 20, 10).startswith("x" * 10 + "\n... [truncated 10")
    assert number_lines(["a", "b"], 7) == "    7  a\n    8  b"


def test_strip_preamble():
    assert strip_preamble("Perfect! Done.\n\n## Summary\nGood") == "## Summary\nGood"
    assert strip_preamble("## Summary\nGood") == "## Summary\nGood"


def test_only_latest_turn_carries_the_cache_breakpoint():
    client = FakeClient([
        SimpleNamespace(stop_reason="tool_use", usage=_usage(),
                        content=[_tool_use("list_functions", {"sort_by": "loc"})]),
        SimpleNamespace(stop_reason="end_turn", usage=_usage(), content=[_text("## Summary")]),
    ])
    run_agent(client, "m", "brief", TOOL_SPECS, lambda n, a: "ok")

    msgs = client.calls[1]["messages"]
    marked = [
        i for i, m in enumerate(msgs)
        if m["role"] == "user" and isinstance(m["content"], list)
        and any(isinstance(b, dict) and "cache_control" in b for b in m["content"])
    ]
    assert marked == [len(msgs) - 1]  # moved forward, not accumulated


def test_tool_budget_stops_parallel_overspending():
    burst = [_tool_use("list_functions", {"sort_by": "loc"}, id_=f"t{i}")
             for i in range(MAX_TOOL_CALLS + 3)]
    client = FakeClient([
        SimpleNamespace(stop_reason="tool_use", usage=_usage(), content=burst),
        SimpleNamespace(stop_reason="end_turn", usage=_usage(), content=[_text("## Summary")]),
    ])
    executed = []
    run_agent(client, "m", "brief", TOOL_SPECS, lambda n, a: executed.append(n) or "ok")

    assert len(executed) == MAX_TOOL_CALLS
    results = client.calls[1]["messages"][-1]["content"]
    refused = [r for r in results if isinstance(r, dict) and r.get("is_error")]
    assert len(refused) == 3  # every tool_use still gets a tool_result
    assert client.calls[1]["tool_choice"] == {"type": "none"}

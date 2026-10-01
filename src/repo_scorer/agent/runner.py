"""The remediation agent: Claude investigates scored metrics with tools and writes a fix plan.

Design rule: deterministic analyzers own the numbers; the model only interprets them.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

SYSTEM_PROMPT = """\
You are a senior Python reviewer writing a remediation plan for an open-source repository.

The repository has already been scored by deterministic static analyzers. Those scores are
final: never recompute, dispute, or invent scores. Your job is to explain the biggest point
losses and recommend concrete fixes.

How to work:
1. Start from the "biggest point losses" you are given.
2. For each of the top losses, call get_metric_details to see which code caused it, then
   read_code on the worst offenders before recommending anything.
3. Use find_similar_code when duplication is involved, to confirm what could be shared.
4. Stop investigating once you can make 3-5 well-evidenced recommendations (about 6-10 tool calls).

Rules:
- Only cite files, line numbers and names that appeared in tool results. Never guess paths.
- Be specific: name the function, say what to extract/split/rename/test, and why.
- Never state anything about a function's behavior that you did not read in its code.
- Prefer high-impact, low-effort fixes first.

How the metrics work (use this when explaining caveats; do not guess other methods):
- Complexity is McCabe cyclomatic complexity from the AST; > 10 counts as high.
- God classes have >= 20 methods or >= 500 lines.
- Test reachability is static: a function counts as reached if a test file mentions its name,
  or if a reached function calls it by name. Name collisions can over-count; code invoked
  indirectly (callbacks, frameworks) can be missed. Tests are never executed.
- Structural clones share a normalized AST hash; semantic duplicates come from code embeddings.
  Thin public API wrappers may be intentional duplication.

Final answer: start directly with "## Summary" (no preamble). Use exactly these sections:
## Summary
Two or three sentences on overall health, mentioning the score and grade.
## Top priorities
A numbered list. For each item: **title**, the evidence (file:line, metric value),
why it matters, the concrete fix, and effort (Small / Medium / Large).
## Quick wins
Up to 3 bullet points of small, safe improvements.
## Caveats
One or two sentences on what static analysis could not verify.
"""

MAX_TURNS = 12
MAX_TOOL_CALLS = 12  # hard budget: instructions alone don't stop over-investigation
MAX_TOKENS = 4096
FINAL_INSTRUCTION = "Tool budget reached. Write the final report now, starting with ## Summary."

# USD per million tokens: (input, output, cache write, cache read)
PRICING = {
    "claude-haiku-4-5-20251001": (1.00, 5.00, 1.25, 0.10),
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    tool_calls: list[str] = field(default_factory=list)
    turns: int = 0

    def add(self, u) -> None:
        self.input_tokens += u.input_tokens or 0
        self.output_tokens += u.output_tokens or 0
        self.cache_write_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.cache_read_tokens += getattr(u, "cache_read_input_tokens", 0) or 0

    def cost(self, model: str) -> float | None:
        prices = PRICING.get(model)
        if prices is None:
            return None
        p_in, p_out, p_write, p_read = prices
        return round((
            self.input_tokens * p_in + self.output_tokens * p_out
            + self.cache_write_tokens * p_write + self.cache_read_tokens * p_read
        ) / 1_000_000, 4)


def build_brief(repo_name: str, result: dict, losses: list[dict]) -> str:
    """The opening message: score summary plus the biggest losses."""
    lines = [
        f"Repository: {repo_name}",
        f"Overall score: {result['overall']} / 100 (grade {result['grade']})",
        "",
        "Category scores:",
    ]
    for name, cat in result["categories"].items():
        lines.append(f"- {name}: {cat['score']} (weight {int(cat['weight'] * 100)}%)")
    lines += ["", "Biggest point losses (overall points lost):"]
    for p in losses:
        lines.append(
            f"- {p['category']}.{p['label']} = {p['value']:g} (best {p['best']:g}) "
            f"-> -{p['overall_points_lost']} points"
        )
    lines += ["", "Investigate with the tools, then write the report."]
    return "\n".join(lines)


def strip_preamble(report: str) -> str:
    """Drop any chatter before the first '## ' heading."""
    idx = report.find("## ")
    return report[idx:].strip() if idx > 0 else report.strip()


def _cache_latest_turn(messages: list[dict]) -> None:
    """Move the conversation cache breakpoint to the newest user message.

    Each turn re-sends the whole history; marking its end lets the next call read
    everything before it from cache at ~10% of the normal input price.
    """
    for msg in messages:
        if msg["role"] == "user" and isinstance(msg["content"], list):
            for block in msg["content"]:
                if isinstance(block, dict):
                    block.pop("cache_control", None)
    last = messages[-1]
    if isinstance(last["content"], str):
        last["content"] = [{"type": "text", "text": last["content"]}]
    last["content"][-1]["cache_control"] = {"type": "ephemeral"}


def run_agent(
    client,
    model: str,
    brief: str,
    tools: list[dict],
    execute: Callable[[str, dict], str],
    on_tool_call: Callable[[str, dict], None] | None = None,
) -> tuple[str, Usage]:
    """Run the tool-use loop until the model writes its final answer."""
    usage = Usage()
    # Cache breakpoints (max 4): system prompt, tool definitions, and the latest turn.
    system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    cached_tools = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]
    messages: list[dict] = [{"role": "user", "content": brief}]

    def call(**extra):
        _cache_latest_turn(messages)
        resp = client.messages.create(
            model=model, max_tokens=MAX_TOKENS, system=system,
            tools=cached_tools, messages=messages, **extra,
        )
        usage.turns += 1
        usage.add(resp.usage)
        return resp

    response = None
    finished = False
    for _ in range(MAX_TURNS):
        response = call()
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            finished = True
            break

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if len(usage.tool_calls) >= MAX_TOOL_CALLS:
                output = "Error: tool budget reached; no more tool calls allowed."
            else:
                usage.tool_calls.append(block.name)
                if on_tool_call:
                    on_tool_call(block.name, block.input)
                output = execute(block.name, block.input)
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
                "is_error": output.startswith("Error:"),
            })
        messages.append({"role": "user", "content": results})
        if len(usage.tool_calls) >= MAX_TOOL_CALLS:
            break  # budget spent: go straight to the forced final report

    if not finished:
        messages[-1]["content"].append({"type": "text", "text": FINAL_INSTRUCTION})
        response = call(tool_choice={"type": "none"})

    report = "".join(b.text for b in response.content if b.type == "text")
    return strip_preamble(report), usage

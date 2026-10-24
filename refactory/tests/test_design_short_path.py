"""A scoped change takes the short design path: no perspectives step, no review agent.

`route_strategy` sends a scoped change from the pitfalls straight to the plan, and
`route_design_review` sends its design from the claims check straight to the
design-review gate, whose own checks and the person's approval are its review. A full
design keeps both steps. Both routing gates answer `scoped` or `proceed`, the edge labels
both runtimes follow; `proceed` is also what a person's skip of a gate sends on the spine.
"""
from __future__ import annotations

import json
import sys

from refactory_dsh import lowering
from refactory_dsh.design import DesignMode
from refactory_dsh.design_ops import (SCOPED_STRATEGY_PLAN, gate_design_review,
                                      route_design_review, route_strategy)
from test_design_contract_extensions import FakeCtx, _make_design
from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, PITFALLS_ANSWER,
                           RESEARCH_ANSWER, SCOPED_RESEARCH_ANSWER, VERIFICATION_ANSWER,
                           FakeModel, git_repo, render)

STRATEGY_DIRECTOR = "You are the strategy director."
DESIGN_REVIEWER = "For every high-risk pitfall, confirm the design accounts for it"
PLAN_STEP = "Compile the perspectives into one plan"


def _edges(graph):
    return {(e.source, e.target, e.condition) for e in graph.edges}


def test_the_design_graph_routes_on_the_scale():
    graph = DesignMode().build()
    edges = _edges(graph)
    assert ("gate_research", "classify_scale", "proceed") in edges
    assert ("classify_scale", "pitfalls_researcher", None) in edges
    assert ("gate_pitfalls", "route_strategy", "proceed") in edges
    assert ("route_strategy", "strategy_director", "proceed") in edges
    assert ("route_strategy", "synthesize_strategy", "scoped") in edges
    assert ("check_design_claims", "route_design_review", "proceed") in edges
    assert ("route_design_review", "design_review", "proceed") in edges
    assert ("route_design_review", "gate_design_review", "scoped") in edges
    # No other way into the two steps a scoped change skips.
    assert {e for e in edges if e[1] == "strategy_director"} == {
        ("route_strategy", "strategy_director", "proceed")}
    assert {e for e in edges if e[1] == "design_review"} == {
        ("route_design_review", "design_review", "proceed")}


def test_the_spine_gets_the_same_routes():
    workflow = lowering.to_workflow(DesignMode().state_dict())
    for gate in ("route_strategy", "route_design_review"):
        node = workflow.nodes[gate]
        assert node.evaluator_type == "fn"
        assert node.evaluator_command.endswith(f"refactory_dsh.design_ops:{gate}")
    lowered = {(e.source, e.target, e.condition) for e in workflow.edges}
    assert ("route_strategy", "synthesize_strategy", "scoped") in lowered
    assert ("route_design_review", "gate_design_review", "scoped") in lowered


def test_the_routing_gates_answer_by_the_scale(tmp_path):
    from refactory_dsh.nn.executor import RunContext

    scoped = RunContext(tmp_path / "scoped", knobs={}, facts={"research_scope": "scoped"}, task={})
    assert route_strategy(scoped) == "scoped"
    assert scoped.facts["strategy_plan"] == SCOPED_STRATEGY_PLAN
    # The skipped step's outputs exist, empty, for the steps that declare them as inputs.
    assert json.loads((tmp_path / "scoped" / "strategy-plan.json").read_text()) == []
    assert (tmp_path / "scoped" / "strategy").is_dir()
    assert route_design_review(scoped) == "scoped"
    assert scoped.facts["design_review_by"] == "automatic"
    full = RunContext(tmp_path / "full", knobs={}, facts={"research_scope": "full"}, task={})
    assert route_strategy(full) == "proceed"
    assert "strategy_plan" not in full.facts
    assert not (tmp_path / "full" / "strategy-plan.json").exists()
    assert route_design_review(full) == "proceed"
    assert full.facts["design_review_by"] == "agent"


_SCOPED_REASON = "touches 1 existing function(s) (app.main at app.py:1), adds nothing new, no shared engine"
AUTOMATIC = {"name": "short", "has_pitfalls_step": False, "research_scope": "scoped",
             "research_scope_reason": _SCOPED_REASON,
             "design_review_by": "automatic"}


def test_a_scoped_gate_judges_only_its_own_checks():
    # A review file left from anywhere is not read, notes included: no agent reviewed it.
    review = "BLOCKING: the design is wrong\n\n## Notes\n- a stale note\n\nVERDICT: FAIL"
    ctx = FakeCtx(files={"reviews/design-review.md": review, "design.md": _make_design(),
                         "brief.json": json.dumps({"brief": "a tool"})}, facts=dict(AUTOMATIC))
    assert gate_design_review(ctx) == "proceed"
    assert ctx.facts["design_review_status"] == (
        "Design review (automatic checks; scoped change"
        " \u2014 " + _SCOPED_REASON + "): PASS")
    assert "stale note" not in ctx.facts["design_review_advisory"]
    # Its own checks still block: a design with no Ask mapping goes back once for a
    # rewrite, and only then reaches the person with the problem shown.
    ctx = FakeCtx(files={"design.md": _make_design(ask_mapping=False),
                         "brief.json": json.dumps({"brief": "a tool"})}, facts=dict(AUTOMATIC))
    first = gate_design_review(ctx)
    assert first == "reloop" and "Ask mapping" in first.reason
    assert gate_design_review(ctx) == "proceed"
    assert ctx.facts["design_review_status"].startswith(
        "Design review (automatic checks; scoped change"
        " \u2014 " + _SCOPED_REASON + "): FAIL, 1 unresolved")


def test_a_run_whose_review_agent_ran_keeps_its_review():
    # A scoped run on a graph from before the routing gate ran the review agent; the
    # gate still reads that review, because only route_design_review says it was skipped.
    review = "BLOCKING: the design never handles an empty file\n\nVERDICT: FAIL"
    ctx = FakeCtx(files={"reviews/design-review.md": review, "design.md": _make_design(),
                         "brief.json": json.dumps({"brief": "a tool"})},
                  facts={"name": "short", "has_pitfalls_step": False, "research_scope": "scoped"})
    assert gate_design_review(ctx) == "proceed"  # a scoped change's one round
    assert ctx.facts["design_review_status"].startswith("Design review: FAIL, 1 unresolved")
    assert "never handles an empty file" in ctx.facts["design_review_status"]


def _design_run(tmp_path, brief, answers):
    repo = git_repo(tmp_path / "repo")
    model = FakeModel(answers)
    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["pr"]["value"] = False
    mode.load_state_dict(state)
    run = mode.run(tmp_path / "run", model=model, verdicts={"gate_strategy": "proceed"},
                   task={"name": "short_tool", "brief": brief,
                         "check": f"{sys.executable} tool.py", "target": str(repo)})
    return run, model


BUILD = [render({"tool.py": "def build():\n    print('built')\n"}),
         "health: ok", "code review: ok", "qa: ok", VERIFICATION_ANSWER, "VERDICT: PASS"]


def test_a_scoped_change_runs_the_short_path_end_to_end(tmp_path, monkeypatch):
    # The spine's worker refuses a declared input that is not on disk; hold the walker
    # run to the same rule, so a step the short path skips cannot leave one missing.
    from pathlib import Path
    from refactory_dsh import agent_delivery

    deliver = agent_delivery.begin

    def strict_begin(workdir, node, prompt, writes, **kwargs):
        for name in kwargs.get("reads", []):
            assert (Path(workdir) / name).exists(), f"{node}: required worker input is missing: {name}"
        return deliver(workdir, node, prompt, writes, **kwargs)

    monkeypatch.setattr(agent_delivery, "begin", strict_begin)
    run, model = _design_run(tmp_path, "Make tool.py print built.", [
        SCOPED_RESEARCH_ANSWER, PITFALLS_ANSWER, "one plan", DESIGN_DOC_ANSWER, *BUILD])

    assert run.exit_ok, run.error
    assert run.facts["research_scope"] == "scoped"
    assert "research_scope_reason" in run.facts
    assert not [p for p in model.prompts if STRATEGY_DIRECTOR in p]
    assert not [p for p in model.prompts if DESIGN_REVIEWER in p]
    plan = next(p for p in model.prompts if PLAN_STEP in p)
    assert "no perspectives: this is a scoped change" in plan
    assert run.facts["design_review_status"].startswith(
        "Design review (automatic checks; scoped change")
    assert ": PASS" in run.facts["design_review_status"]


def test_a_scoped_design_the_checks_block_is_rewritten_once_before_the_gate(tmp_path):
    """Both 2026-10-22 measurement runs reached the person with a blocking finding the
    design writer could have fixed; a scoped design now gets that one rewrite."""
    import re
    assert "## Ask mapping" in DESIGN_DOC_ANSWER
    without_mapping = re.sub(r"## Ask mapping\n\n.*?\n\n(?=## )", "", DESIGN_DOC_ANSWER, flags=re.S)
    assert "## Ask mapping" not in without_mapping
    run, model = _design_run(tmp_path, "Make tool.py print built.", [
        SCOPED_RESEARCH_ANSWER, PITFALLS_ANSWER, "one plan", without_mapping, DESIGN_DOC_ANSWER, *BUILD])

    assert run.exit_ok, run.error
    writes = [p for p in model.prompts if "Write the design document for this artifact" in p]
    assert len(writes) == 2
    assert "Ask mapping" in writes[1]
    assert run.facts["design_review_status"].startswith(
        "Design review (automatic checks; scoped change")
    assert ": PASS" in run.facts["design_review_status"]


def test_a_full_design_keeps_both_steps(tmp_path):
    run, model = _design_run(tmp_path, "a tool", [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan", DESIGN_DOC_ANSWER,
        DESIGN_REVIEW_ANSWER, *BUILD])

    assert run.exit_ok, run.error
    assert run.facts["research_scope"] == "full"
    assert len([p for p in model.prompts if STRATEGY_DIRECTOR in p]) == 1
    assert len([p for p in model.prompts if DESIGN_REVIEWER in p]) == 1
    assert run.facts["design_review_status"].startswith("Design review: PASS")

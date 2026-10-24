"""The design phase scales with the change, and its review blocks only on what changes the build.

A scoped change (a refine, or one whose research footprint shows \u22642 existing functions
touched \u2014 counted by (path, name) \u2014 nothing new added, no shared engine changed) gets at
most five pitfalls, one strategy perspective and one design-review round.  Non-refine
research is always 3 to 5 directions; classify_scale runs after research and sets the
class from the footprint.  At every scale the design-review gate sends a design back only
for an unmet ask, a false claim the design relies on, a missing high-risk pitfall or a
blocking finding from the review agent; everything else is listed as advisory at the
person's gate and costs no round.
"""
from __future__ import annotations

import json
import sys

from refactory_dsh.design import DesignMode
from refactory_dsh.design_ops import (
    REFINE_SCOPE_REASON,
    classify_scale,
    design_review_status,
    gate_design_review,
)
from test_design_contract_extensions import FakeCtx, _make_design

PASSING_REVIEW = "No issues found.\n\nVERDICT: PASS"


def _gate(design: str, *, brief: str = "a tool", scope: str = "full", source: str = "",
          review: str = PASSING_REVIEW, pitfalls: list | None = None):
    files = {"reviews/design-review.md": review, "design.md": design,
             "brief.json": json.dumps({"brief": brief})}
    if pitfalls is not None:
        files["pitfalls.json"] = json.dumps(pitfalls)
    ctx = FakeCtx(
        files=files,
        facts={"name": "scale_test", "has_pitfalls_step": pitfalls is not None,
               "research_scope": scope, "source": source},
    )
    return ctx, gate_design_review(ctx)


# ── what blocks ────────────────────────────────────────────────────────────────

def test_a_missing_ask_mapping_still_sends_the_design_back():
    ctx, verdict = _gate(_make_design(ask_mapping=False))
    assert verdict == "reloop"
    assert "Ask mapping" in verdict.reason


def test_a_citation_past_the_end_of_its_file_still_blocks(tmp_path):
    (tmp_path / "tool.py").write_text("x = 1\ny = 2\n")
    design = _make_design().replace("## Changes", "`x` at `tool.py:40`: `x = 1`\n\n## Changes")
    ctx, verdict = _gate(design, source=str(tmp_path))
    assert verdict == "reloop"
    assert "tool.py has 2 lines, not 40" in verdict.reason


def test_a_misquote_of_the_brief_still_blocks():
    design = _make_design() + '\nThe brief says "remove the flag from the command".\n'
    ctx, verdict = _gate(design, brief="Add the flag to the command.")
    assert verdict == "reloop"
    assert "does not appear verbatim in the brief" in verdict.reason


def test_a_misquote_that_drops_a_negation_still_blocks():
    # "able" is inside "unable": a word-level match must not accept the inverted quote.
    design = _make_design() + '\nThe brief says "Able to overwrite files".\n'
    ctx, verdict = _gate(design, brief="The tool is unable to overwrite files.")
    assert verdict == "reloop"
    assert "does not appear verbatim in the brief" in verdict.reason


def test_a_missing_high_risk_pitfall_still_blocks():
    ctx, verdict = _gate(_make_design(), pitfalls=[
        {"id": "PIT-001", "risk": "high", "text": "the cache key ignores the base"}])
    assert verdict == "reloop"
    assert "PIT-001" in verdict.reason


# ── what is advisory ──────────────────────────────────────────────────────────

def test_a_quote_differing_only_in_punctuation_and_case_is_advisory():
    design = _make_design() + '\nThe brief says "add the **flag** to the command".\n'
    ctx, verdict = _gate(design, brief="Add the flag to the command.")
    assert verdict == "proceed"
    assert "differs from the brief only in punctuation" in ctx.facts["design_review_advisory"]
    assert ctx.facts["design_review_status"] == "Design review: PASS, 1 advisory"


def test_paperwork_items_are_listed_and_cost_no_round():
    """b35a's gate read 'FAIL, 33 unresolved' on items like these; now it reads PASS."""
    design = (_make_design(failure_trace=False)
              .replace("# Fix the thing", "# scale_test")
              .replace("## Rule and sites\n\n- `tool.py:1` \u2014 **[changed]**",
                       "## Rule and sites\n\n- `tool.py:1` \u2014 **[changed]**\n- `other.py:3` \u2014 the caller"))
    ctx, verdict = _gate(design)
    assert verdict == "proceed"
    advisory = ctx.facts["design_review_advisory"]
    assert "Failure trace" in advisory
    assert "title is the run name" in advisory
    assert "other.py:3" in advisory
    assert ctx.facts["design_review_status"] == "Design review: PASS, 3 advisory"
    assert "## Advisory" in ctx.facts["design_review_findings"]


def test_an_unmentioned_medium_pitfall_is_not_called_missing():
    ctx, verdict = _gate(_make_design(), pitfalls=[
        {"id": "PIT-002", "risk": "medium", "text": "a slower path on large inputs"}])
    assert verdict == "proceed"
    assert "PIT-002 (medium): not mentioned" in ctx.facts["pitfall_coverage"]
    assert "MISSING" not in ctx.facts["pitfall_coverage"]


def test_the_review_agents_notes_reach_the_person():
    review = ("The design meets the brief.\n\n## Notes\n"
              "- docs/cli.md describes the flag; the Docs section leaves it out\n\n"
              "VERDICT: PASS")
    ctx, verdict = _gate(_make_design(), review=review)
    assert verdict == "proceed"
    assert ("review note: docs/cli.md describes the flag; the Docs section leaves it out"
            in ctx.facts["design_review_advisory"])
    assert ctx.facts["design_review_status"] == "Design review: PASS, 1 advisory"


def test_the_fail_count_counts_only_blocking_findings():
    # The findings text a person sees can carry other bulleted lines; the count comes
    # from the blocking list alone.
    facts = {"design_review_attempts_used": 1,
             "design_review_findings": "- a blocking finding\n- a listed note\n- another note",
             "design_review_blocking": "- a blocking finding",
             "design_review_advisory": "- a note\n- another note"}
    assert design_review_status(facts) == (
        "Design review: FAIL, 1 unresolved, 2 advisory\n- a blocking finding")


# ── rounds ────────────────────────────────────────────────────────────────────

def test_a_scoped_change_gets_one_design_review_round():
    design = _make_design(ask_mapping=False)
    full_ctx, full_verdict = _gate(design, scope="full")
    scoped_ctx, scoped_verdict = _gate(design, scope="scoped")
    assert full_verdict == "reloop"
    # The scoped design goes to the person after one round, with its finding shown.
    assert scoped_verdict == "proceed"
    assert scoped_ctx.facts["design_review_status"].startswith("Design review: FAIL, 1 unresolved")
    assert "Ask mapping" in scoped_ctx.facts["design_review_findings"]


# ── the prompts scale with the change ─────────────────────────────────────────

def test_the_design_phase_prompts_scale_with_the_change():
    nodes = DesignMode().build().nodes
    assert "one focused question for a scoped change" in nodes["research_director"].prompt_template
    assert "{{research_scope}}" in nodes["pitfalls_researcher"].prompt_template
    assert "list at most five" in nodes["pitfalls_researcher"].prompt_template
    # A scoped change has no perspectives step: route_strategy sends it past
    # strategy_director (test_design_short_path.py).
    graph = DesignMode().build()
    assert graph.out_edges("route_strategy", condition="scoped")[0].target == "synthesize_strategy"


def test_the_design_does_not_account_for_every_finding():
    nodes = DesignMode().build().nodes
    assert "Carry every finding id forward" not in nodes["synthesize_strategy"].prompt_template
    design_doc = nodes["design_doc"].prompt_template
    assert "neither addressed nor set aside is blocking" not in design_doc
    assert "where it changes a decision" in design_doc
    review = nodes["design_review"].prompt_template
    assert "## Notes" in review
    assert "Notes do not send the design back" in review


# ── classify_scale unit tests ─────────────────────────────────────────────────

def _scale_ctx(research_plan, *, facts=None):
    """Build a FakeCtx with a research-plan.json for classify_scale."""
    files = {"research-plan.json": json.dumps(research_plan) if not isinstance(research_plan, str) else research_plan}
    return FakeCtx(files=files, facts=dict(facts or {}))


def test_classify_scale_scoped_footprint():
    plan = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
         "footprint": {"existing_functions": ["src/mod.py:10 mod.func"], "new_items": [], "shared_engine": []}},
        {"focus": "q2", "slug": "s2", "finding": "f2", "ids": [],
         "footprint": {"existing_functions": [], "new_items": [], "shared_engine": []}},
    ]
    ctx = _scale_ctx(plan)
    result = classify_scale(ctx)
    assert result["research_scope"] == "scoped"
    assert "1 existing function" in result["research_scope_reason"]
    assert "mod.func" in result["research_scope_reason"]


def test_classify_scale_no_footprint_means_full():
    plan = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": []},
    ]
    ctx = _scale_ctx(plan)
    result = classify_scale(ctx)
    assert result["research_scope"] == "full"
    assert "no usable footprint" in result["research_scope_reason"]


def test_classify_scale_too_many_functions():
    plan = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
         "footprint": {"existing_functions": [
             "a.py:1 a.func1", "b.py:2 b.func2", "c.py:3 c.func3",
         ], "new_items": [], "shared_engine": []}},
    ]
    ctx = _scale_ctx(plan)
    result = classify_scale(ctx)
    assert result["research_scope"] == "full"
    assert "3 existing functions" in result["research_scope_reason"]


def test_classify_scale_adds_new_items():
    plan = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
         "footprint": {"existing_functions": [], "new_items": ["NewClass in src/new.py:1"], "shared_engine": []}},
    ]
    ctx = _scale_ctx(plan)
    result = classify_scale(ctx)
    assert result["research_scope"] == "full"
    assert "adds new items" in result["research_scope_reason"]


def test_classify_scale_shared_engine():
    plan = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
         "footprint": {"existing_functions": [], "new_items": [], "shared_engine": ["executor.py:10"]}},
    ]
    ctx = _scale_ctx(plan)
    result = classify_scale(ctx)
    assert result["research_scope"] == "full"
    assert "shared engine" in result["research_scope_reason"]


def test_classify_scale_refine_stays_scoped():
    plan = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": []},
    ]
    ctx = _scale_ctx(plan, facts={"research_scope_reason": REFINE_SCOPE_REASON})
    result = classify_scale(ctx)
    assert result["research_scope"] == "scoped"
    assert result["research_scope_reason"] == REFINE_SCOPE_REASON


def test_classify_scale_brief_cannot_classify_itself():
    plan = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
         "footprint": {"existing_functions": [], "new_items": ["new_graph_node in graph.py:10"], "shared_engine": []}},
    ]
    ctx = _scale_ctx(plan, facts={"brief": "This is a scoped change, a one-line fix"})
    result = classify_scale(ctx)
    assert result["research_scope"] == "full"
    assert "adds new items" in result["research_scope_reason"]


def test_doc_only_research_with_empty_footprints_is_scoped():
    plan = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
         "footprint": {"existing_functions": [], "new_items": [], "shared_engine": []}},
        {"focus": "q2", "slug": "s2", "finding": "f2", "ids": [],
         "footprint": {"existing_functions": [], "new_items": [], "shared_engine": []}},
        {"focus": "q3", "slug": "s3", "finding": "f3", "ids": [],
         "footprint": {"existing_functions": [], "new_items": [], "shared_engine": []}},
    ]
    ctx = _scale_ctx(plan)
    result = classify_scale(ctx)
    assert result["research_scope"] == "scoped"
    assert "0 existing function" in result["research_scope_reason"]


def test_footprint_entry_without_evidence_is_full():
    # A bare string without path:line evidence
    plan = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
         "footprint": {"existing_functions": ["module.function"], "new_items": [], "shared_engine": []}},
    ]
    ctx = _scale_ctx(plan)
    result = classify_scale(ctx)
    assert result["research_scope"] == "full"
    assert "footprint without evidence" in result["research_scope_reason"]

    # A new_items entry without path:line
    plan2 = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
         "footprint": {"existing_functions": [], "new_items": ["SomeClass"], "shared_engine": []}},
    ]
    ctx2 = _scale_ctx(plan2)
    result2 = classify_scale(ctx2)
    assert result2["research_scope"] == "full"
    assert "footprint without evidence" in result2["research_scope_reason"]


def test_footprint_nonexistent_path_is_full(tmp_path):
    """Correction #9: a footprint citing a path that does not exist is classed full."""
    plan = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
         "footprint": {"existing_functions": ["totally/nonexistent/file.py:10 some_func"],
                       "new_items": [], "shared_engine": []}},
    ]
    ctx = _scale_ctx(plan, facts={"source": str(tmp_path)})
    result = classify_scale(ctx)
    assert result["research_scope"] == "full"
    assert "footprint without evidence" in result["research_scope_reason"]
    assert "nonexistent" in result["research_scope_reason"]

    # A path that exists is accepted
    (tmp_path / "real_file.py").write_text("def func(): pass\n")
    plan2 = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
         "footprint": {"existing_functions": ["real_file.py:1 func"],
                       "new_items": [], "shared_engine": []}},
    ]
    ctx2 = _scale_ctx(plan2, facts={"source": str(tmp_path)})
    result2 = classify_scale(ctx2)
    assert result2["research_scope"] == "scoped"


def test_null_footprint_field_is_full():
    """A footprint with null existing_functions or new_items degrades to full, not crash."""
    plan = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
         "footprint": {"existing_functions": None, "new_items": [], "shared_engine": []}},
    ]
    ctx = _scale_ctx(plan)
    result = classify_scale(ctx)
    assert result["research_scope"] == "full"
    assert "no usable footprint" in result["research_scope_reason"]

    plan2 = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
         "footprint": {"existing_functions": [], "new_items": None, "shared_engine": []}},
    ]
    ctx2 = _scale_ctx(plan2)
    result2 = classify_scale(ctx2)
    assert result2["research_scope"] == "full"
    assert "no usable footprint" in result2["research_scope_reason"]


def test_a_shared_engine_that_is_not_a_list_is_full():
    """A shared_engine given as a bare string, a number or an object is unreadable
    evidence: the run is classed full, never scoped through a silent empty list."""
    for value in ("refactory_dsh/nn/executor.py:10", 3, {"at": "x.py:1"}):
        plan = [
            {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
             "footprint": {"existing_functions": [], "new_items": [], "shared_engine": value}},
        ]
        result = classify_scale(_scale_ctx(plan))
        assert result["research_scope"] == "full", value
        assert "footprint without evidence: shared_engine" in result["research_scope_reason"], value
    # An explicit "no shared engine" stays readable.
    for value in ([], None, False):
        plan = [
            {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
             "footprint": {"existing_functions": [], "new_items": [], "shared_engine": value}},
        ]
        assert classify_scale(_scale_ctx(plan))["research_scope"] == "scoped", value


def test_empty_directions_is_full():
    """An empty research plan (no directions) is classed full."""
    ctx = _scale_ctx([])
    result = classify_scale(ctx)
    assert result["research_scope"] == "full"
    assert "no usable footprint" in result["research_scope_reason"]


def test_classify_scale_reason_in_design_review_status():
    # scoped
    scoped_facts = {
        "design_review_attempts_used": 1,
        "design_review_findings": "(none: the design review passed)",
        "design_review_by": "automatic",
        "research_scope_reason": "touches 1 existing function(s) (f at x.py:1), adds nothing new, no shared engine",
    }
    status = design_review_status(scoped_facts)
    assert "scoped change" in status
    assert "touches 1 existing function" in status
    assert "PASS" in status

    # full
    full_facts = {
        "design_review_attempts_used": 1,
        "design_review_findings": "(none: the design review passed)",
        "research_scope_reason": "adds new items: NewClass in src/new.py:1",
    }
    status = design_review_status(full_facts)
    assert "Scale: full" in status
    assert "adds new items" in status

    # refine
    refine_facts = {
        "design_review_attempts_used": 1,
        "design_review_findings": "(none: the design review passed)",
        "design_review_by": "automatic",
        "research_scope_reason": REFINE_SCOPE_REASON,
    }
    status = design_review_status(refine_facts)
    assert "scoped change" in status
    assert "refinement" in status


# ── _plan output pinning ──────────────────────────────────────────────────────

def test_plan_output_on_existing_research_fixture():
    """_plan strips footprint and returns only focus/slug/finding/ids."""
    from refactory_dsh.create_ops import _plan
    from tests_support import RESEARCH_ANSWER

    entries = _plan(RESEARCH_ANSWER)
    assert len(entries) == 3
    for entry in entries:
        assert set(entry.keys()) == {"focus", "slug", "finding", "ids"}
        assert "footprint" not in entry


# ── end-to-end tests ──────────────────────────────────────────────────────────

def _design_run(tmp_path, brief, answers, *, task_extra=None):
    from tests_support import FakeModel, git_repo

    repo = git_repo(tmp_path / "repo")
    model = FakeModel(answers)
    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["pr"]["value"] = False
    mode.load_state_dict(state)
    task = {"name": "scale_tool", "brief": brief,
            "check": f"{sys.executable} tool.py", "target": str(repo)}
    if task_extra:
        task.update(task_extra)
    run = mode.run(tmp_path / "run", model=model, verdicts={"gate_strategy": "proceed"},
                   task=task)
    return run, model


_FULL_BUILD = [
    "perspectives",
    "one plan",
]


def test_structural_brief_naming_files_is_full(tmp_path):
    from tests_support import (
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, PITFALLS_ANSWER,
        RESEARCH_ANSWER, VERIFICATION_ANSWER, render,
    )

    brief = (
        "Add a graph node dispute_resolver to design.py and design_ops.py and "
        "wire it into all six graphs across both runtimes"
    )
    build = [render({"tool.py": "def build():\n    print('built')\n"}),
             "health: ok", "code review: ok", "qa: ok", VERIFICATION_ANSWER, "VERDICT: PASS"]
    run, model = _design_run(tmp_path, brief, [
        RESEARCH_ANSWER, PITFALLS_ANSWER, *_FULL_BUILD, DESIGN_DOC_ANSWER,
        DESIGN_REVIEW_ANSWER, *build])

    assert run.exit_ok, run.error
    assert run.facts["research_scope"] == "full"
    assert "no usable footprint" in run.facts["research_scope_reason"]
    # perspectives and design review ran
    assert any("strategy director" in p.lower() for p in model.prompts)
    assert any("high-risk pitfall, confirm" in p for p in model.prompts)


def test_one_line_doc_fix_stays_scoped(tmp_path):
    from tests_support import (
        DESIGN_DOC_ANSWER, PITFALLS_ANSWER, SCOPED_RESEARCH_ANSWER,
        VERIFICATION_ANSWER, render,
    )

    brief = "Add one sentence to the live-control doc and update refactory/docs/live-control.md"
    build = [render({"tool.py": "def build():\n    print('built')\n"}),
             "health: ok", "code review: ok", "qa: ok", VERIFICATION_ANSWER, "VERDICT: PASS"]
    run, model = _design_run(tmp_path, brief, [
        SCOPED_RESEARCH_ANSWER, PITFALLS_ANSWER, "one plan", DESIGN_DOC_ANSWER, *build])

    assert run.exit_ok, run.error
    assert run.facts["research_scope"] == "scoped"
    assert "adds nothing new" in run.facts["research_scope_reason"]
    assert not any("strategy director" in p.lower() for p in model.prompts)


def test_brief_calling_itself_scoped_is_still_full(tmp_path):
    from tests_support import (
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, PITFALLS_ANSWER,
        RESEARCH_ANSWER, VERIFICATION_ANSWER, render,
    )

    brief = "This is a scoped change, a one-line fix: add a new graph node to design.py"
    build = [render({"tool.py": "def build():\n    print('built')\n"}),
             "health: ok", "code review: ok", "qa: ok", VERIFICATION_ANSWER, "VERDICT: PASS"]
    run, model = _design_run(tmp_path, brief, [
        RESEARCH_ANSWER, PITFALLS_ANSWER, *_FULL_BUILD, DESIGN_DOC_ANSWER,
        DESIGN_REVIEW_ANSWER, *build])

    assert run.exit_ok, run.error
    assert run.facts["research_scope"] == "full"


def test_refine_run_stays_scoped(tmp_path, monkeypatch):
    """A refine run stays scoped: classify_scale sees REFINE_SCOPE_REASON and skips."""
    from refactory_dsh import delivery, design_ops, nn, repo_change, sandbox

    monkeypatch.setattr(repo_change, "wants_worktree", lambda ctx: False)
    monkeypatch.setattr(repo_change, "locate",
                        lambda ctx, name, task, **kw: {
                            "source": str(tmp_path), "check_root": str(tmp_path),
                            "artifact": str(tmp_path), "branch": "", "change": "",
                        })
    monkeypatch.setattr(sandbox, "model_readiness", lambda *a, **kw: {})
    monkeypatch.setattr(delivery, "plan_run",
                        lambda ctx, *, task: {"delivery": "test", "delivery_plan": {}})
    workdir = tmp_path / "run"
    task = {"name": "polish_ui", "brief": "Fix the spacing in the widget",
            "check": "python -m pytest -q",
            "refine": "https://github.com/example/repo/pull/1"}
    ctx = nn.RunContext(workdir, task=task)
    result = design_ops.prepare_design(ctx, deliver=True, in_worktree=False)
    assert result["research_scope"] == "scoped"
    assert result["research_scope_reason"] == REFINE_SCOPE_REASON

    # classify_scale sees the refine reason and preserves scoped
    scale_result = classify_scale(_scale_ctx(
        [{"focus": "q", "slug": "s", "finding": "f", "ids": []}],
        facts={"research_scope_reason": REFINE_SCOPE_REASON},
    ))
    assert scale_result["research_scope"] == "scoped"
    assert scale_result["research_scope_reason"] == REFINE_SCOPE_REASON


def test_no_footprint_means_full_end_to_end(tmp_path):
    from tests_support import (
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, PITFALLS_ANSWER,
        RESEARCH_ANSWER, VERIFICATION_ANSWER, render,
    )

    build = [render({"tool.py": "def build():\n    print('built')\n"}),
             "health: ok", "code review: ok", "qa: ok", VERIFICATION_ANSWER, "VERDICT: PASS"]
    run, model = _design_run(tmp_path, "Improve the logging.", [
        RESEARCH_ANSWER, PITFALLS_ANSWER, *_FULL_BUILD, DESIGN_DOC_ANSWER,
        DESIGN_REVIEW_ANSWER, *build])

    assert run.exit_ok, run.error
    assert run.facts["research_scope"] == "full"
    assert "no usable footprint" in run.facts["research_scope_reason"]


# ── spine op entry test ───────────────────────────────────────────────────────

def test_classify_scale_through_run(tmp_path):
    """Run classify_scale through refactory_dsh.run's build_op_context."""
    from refactory_dsh.run import build_op_context

    workdir = tmp_path / "run"
    workdir.mkdir()
    # Write declarations the runner reads
    decl_dir = workdir / ".refactory"
    decl_dir.mkdir()
    (decl_dir / "declarations.json").write_text(json.dumps({
        "classify_scale": {"reads": ["research-plan.json"], "writes": [], "produces": ["research_scope", "research_scope_reason"]}
    }))
    (decl_dir / "knobs.json").write_text("{}")

    # Case 1: refine run
    facts_refine = {"research_scope_reason": REFINE_SCOPE_REASON}
    (workdir / "facts.json").write_text(json.dumps(facts_refine))
    plan = [{"focus": "q", "slug": "s", "finding": "f", "ids": []}]
    (workdir / "research-plan.json").write_text(json.dumps(plan))
    ctx = build_op_context(workdir, "classify_scale")
    result = classify_scale(ctx)
    assert result["research_scope"] == "scoped"

    # Case 2: scoped footprint
    facts_full = {"research_scope": "full", "research_scope_reason": ""}
    (workdir / "facts.json").write_text(json.dumps(facts_full))
    plan_scoped = [{"focus": "q", "slug": "s", "finding": "f", "ids": [],
                    "footprint": {"existing_functions": ["x.py:1 fn"], "new_items": [], "shared_engine": []}}]
    (workdir / "research-plan.json").write_text(json.dumps(plan_scoped))
    ctx2 = build_op_context(workdir, "classify_scale")
    result2 = classify_scale(ctx2)
    assert result2["research_scope"] == "scoped"

    # Case 3: missing footprint
    (workdir / "facts.json").write_text(json.dumps(facts_full))
    (workdir / "research-plan.json").write_text(json.dumps(plan))
    ctx3 = build_op_context(workdir, "classify_scale")
    result3 = classify_scale(ctx3)
    assert result3["research_scope"] == "full"


# ── dm-149-like fixture is scoped ────────────────────────────────────────────

def test_dm149_like_fixture_is_scoped():
    """One parser function changed in a module several gates call, plus new tests."""
    plan = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
         "footprint": {"existing_functions": ["refactory_dsh/create_ops.py:324 _plan"],
                       "new_items": [], "shared_engine": []}},
        {"focus": "q2", "slug": "s2", "finding": "f2", "ids": [],
         "footprint": {"existing_functions": [], "new_items": [], "shared_engine": []}},
        {"focus": "q3", "slug": "s3", "finding": "f3", "ids": [],
         "footprint": {"existing_functions": [], "new_items": [], "shared_engine": []}},
    ]
    ctx = _scale_ctx(plan)
    result = classify_scale(ctx)
    assert result["research_scope"] == "scoped"
    assert "_plan" in result["research_scope_reason"]


def test_same_named_functions_in_different_files_count_separately():
    """Three `parse` functions in three files are counted as three and classed full."""
    plan = [
        {"focus": "q1", "slug": "s1", "finding": "f1", "ids": [],
         "footprint": {"existing_functions": [
             "a.py:10 parse", "b.py:20 parse", "c.py:30 parse",
         ], "new_items": [], "shared_engine": []}},
    ]
    ctx = _scale_ctx(plan)
    result = classify_scale(ctx)
    assert result["research_scope"] == "full"
    assert "3 existing functions" in result["research_scope_reason"]


def test_research_director_footprint_instruction_clauses():
    """Each clause of the footprint instruction is pinned; reverting any one fails."""
    graph = DesignMode().build()
    prompt = graph.nodes["research_director"].prompt_template
    # Required footprint on every direction
    assert "Record a `footprint` object on every direction" in prompt
    # Evidence format for existing_functions
    assert '"path:line name"' in prompt
    # Shared-engine definition
    assert "code that every run of the target passes through" in prompt
    # Tests and docs do not count
    assert "Test files, test functions and docs are neither touched functions nor added items" in prompt
    # Size wording is a claim, not a footprint
    assert "claim to check against the code, never a footprint" in prompt

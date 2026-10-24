"""Tests for the pitfalls researcher and its mechanical enforcement.

Each test fails without the corresponding production change.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh import nn, pitfalls as pitfalls_mod
from refactory_dsh.design import DesignMode
from refactory_dsh.nn import Verdict
from tests_support import (
    DESIGN_DOC_ANSWER,
    DESIGN_REVIEW_ANSWER,
    FakeModel,
    PITFALLS_ANSWER,
    RESEARCH_ANSWER,
    REVIEW_ANSWERS,
    VERIFICATION_ANSWER,
    render,
)


# ── validation ─────────────────────────────────────────────────────────────

def test_validate_pitfalls_accepts_valid():
    raw = json.dumps([{
        "id": "PIT-001", "risk": "high", "category": "pinning_tests",
        "evidence": "tests/test_thing.py:10",
        "prescription": "keep this assertion",
    }])
    assert pitfalls_mod.validate_pitfalls(raw) == []


def test_validate_pitfalls_catches_invalid_json():
    assert pitfalls_mod.validate_pitfalls("not json")[0].startswith("pitfalls.json is not valid JSON")


def test_validate_pitfalls_catches_empty_array():
    assert "non-empty" in pitfalls_mod.validate_pitfalls("[]")[0]


def test_validate_pitfalls_catches_bad_id():
    raw = json.dumps([{
        "id": "BAD", "risk": "high", "category": "pinning_tests",
        "evidence": "f.py:1", "prescription": "fix it",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw)
    assert any("PIT-NNN" in p for p in problems)


def test_validate_pitfalls_catches_bad_risk():
    raw = json.dumps([{
        "id": "PIT-001", "risk": "critical", "category": "pinning_tests",
        "evidence": "f.py:1", "prescription": "fix it",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw)
    assert any("risk" in p for p in problems)


def test_validate_pitfalls_catches_bad_category():
    raw = json.dumps([{
        "id": "PIT-001", "risk": "high", "category": "unknown_cat",
        "evidence": "f.py:1", "prescription": "fix it",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw)
    assert any("category" in p for p in problems)


def test_validate_pitfalls_catches_empty_prescription():
    raw = json.dumps([{
        "id": "PIT-001", "risk": "high", "category": "pinning_tests",
        "evidence": "f.py:1", "prescription": "",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw)
    assert any("prescription" in p for p in problems)


# ── coverage ───────────────────────────────────────────────────────────────

def _pit(pid: str = "PIT-001", risk: str = "high") -> dict:
    return {"id": pid, "risk": risk, "category": "pinning_tests",
            "evidence": "f.py:1", "prescription": "keep it"}


def _contract(body: str = "", anti: str = "") -> str:
    base = (
        "# Do the thing\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Failure trace\n\nN/A — this is a new feature.\n\n"
        "## Ask mapping\n\n"
        "| Ask (quoted from brief) | Design section | Test |\n"
        "|---|---|---|\n"
        '| "do a thing" | § Changes | test.py |\n\n'
        "## Rule and sites\n\n"
        "- `file.py:1` — **[changed]**\n\n"
        "## Docs\n\n"
        "- docs/models.md\n\n"
        "## Changes\n\n"
        "- file.py\n"
    )
    if body:
        base += f"\n{body}\n"
    if anti:
        base += f"\n## Anti-patterns to avoid\n\n{anti}\n"
    return base


def test_pitfall_coverage_missing_when_no_anti_patterns_section():
    addressed, set_aside, missing = pitfalls_mod.pitfall_coverage(
        _contract(), [_pit()])
    assert "PIT-001" in missing


def test_pitfall_coverage_set_aside_with_reason():
    design = _contract(anti="- PIT-001: [set-aside: not applicable because the change does not touch this path]")
    addressed, set_aside, missing = pitfalls_mod.pitfall_coverage(design, [_pit()])
    assert "PIT-001" in set_aside
    assert "PIT-001" not in missing


def test_pitfall_in_summary_only_fails():
    """Correction test: a design mentioning PIT-001 only in its Summary fails."""
    design = (
        "# Do the thing\n\n"
        "## Summary\n\nPIT-001 is important.\n\n"
        "## Rule and sites\n\n"
        "- `file.py:1` — **[changed]**\n\n"
        "## Docs\n\n"
        "- docs/models.md\n\n"
        "## Changes\n\n"
        "- file.py\n"
    )
    _, _, missing = pitfalls_mod.pitfall_coverage(design, [_pit()])
    assert "PIT-001" in missing


def test_anti_patterns_entry_with_valid_site_passes():
    """Correction test: an Anti-patterns entry naming a site that exists passes."""
    design = _contract(anti="- PIT-001: addressed at `file.py:1`")
    addressed, _, missing = pitfalls_mod.pitfall_coverage(design, [_pit()])
    assert "PIT-001" in addressed
    assert "PIT-001" not in missing


def test_anti_patterns_entry_with_invalid_heading_fails():
    """Correction test: an entry naming a heading that does not exist fails."""
    design = _contract(anti="- PIT-001: see § Nonexistent section")
    _, _, missing = pitfalls_mod.pitfall_coverage(design, [_pit()])
    assert "PIT-001" in missing


SUBSECTIONS = (
    "### Regeneration of outputs\n\nRegenerate them.\n\n"
    "### finding-sources.json attribution\n\nAttribute them.\n\n"
    "### Keep it short, and plain\n\nKeep it.\n\n"
    "#### Block-start predicate\n\nA block starts here.\n\n"
    "```text\n### Example output\n```\n"
)


@pytest.mark.parametrize("entry", [
    # Each from a lab design the check sent back though it named the section.
    "- **PIT-001** (high) — Addressed by block-start rule 4. See § Changes rule 4.",
    "- **PIT-001** (high): Addressed by §Changes item 2, where headings serve all roles.",
    "- **PIT-001** (high): Addressed by § Changes → Regeneration command.",
    "- **PIT-001** (high): Addressed by § Regeneration of outputs. No heading is added.",
    "- **PIT-001** (high) — Addressed by joining with `'\\n'` (the list in § Changes).",
    # A subsection whose name holds a dot or a comma, and one four levels deep.
    "- **PIT-001** (high): Addressed by § finding-sources.json attribution, first rule.",
    "- **PIT-001** (high): Addressed by § Keep it short, and plain.",
    "- **PIT-001** (high): Addressed by § Block-start predicate.",
    # A second § in the entry is read on its own.
    "- **PIT-001** (high): unlike §4 of RFC 9110, handled by § Changes rule 2.",
])
def test_a_reference_that_names_a_section_and_says_where_in_it_is_addressed(entry):
    design = _contract(body=SUBSECTIONS, anti=entry)
    addressed, _, missing = pitfalls_mod.pitfall_coverage(design, [_pit()])
    assert addressed == ["PIT-001"] and not missing


@pytest.mark.parametrize("entry", [
    "- PIT-001: see § Changesets",           # a longer word, not the Changes section
    "- PIT-001: see § Regeneration later",   # no such heading or subsection
    "- PIT-001: see Changes rule 4",         # no § reference at all
    "- PIT-001: see § Changes2",             # a longer name, not the Changes section
    "- PIT-001: see § Example output",       # a '###' line inside a code block
])
def test_a_reference_to_no_section_is_still_missing(entry):
    design = _contract(body=SUBSECTIONS, anti=entry)
    _, _, missing = pitfalls_mod.pitfall_coverage(design, [_pit()])
    assert missing == ["PIT-001"]


def test_gate_design_review_passes_a_pitfall_answered_by_section_and_rule(tmp_path):
    """The dm-149 measurement's design answered PIT-001 with "See § Changes rule 4." and
    was sent back for a whole design round as if it had not."""
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path), "design_review_attempts_used": 0,
                      "has_pitfalls_step": True})
    (tmp_path / "reviews").mkdir(parents=True, exist_ok=True)
    ctx.write_text("reviews/design-review.md", "All confirmed.\n\nVERDICT: PASS\n")
    ctx.write_text("design.md", _contract(
        anti="- **PIT-001** (high) — Addressed by block-start rule 4. See § Changes rule 4."))
    ctx.write_json("brief.json", {"name": "x", "brief": "do a thing"})
    ctx.write_json("pitfalls.json", [_pit()])
    import refactory_dsh.design_ops as ops
    assert ops.gate_design_review(ctx) == "proceed"
    assert "PIT-001 (high): addressed" in ctx.facts["pitfall_coverage"]


def test_set_aside_bold_marker_recognised():
    design = _contract(anti="- PIT-001: **[set-aside: out of scope]**")
    _, set_aside, _ = pitfalls_mod.pitfall_coverage(design, [_pit()])
    assert "PIT-001" in set_aside


def test_set_aside_backtick_marker_recognised():
    design = _contract(anti="- PIT-001: `[set-aside: out of scope]`")
    _, set_aside, _ = pitfalls_mod.pitfall_coverage(design, [_pit()])
    assert "PIT-001" in set_aside


# ── gate_design_review pitfall check ───────────────────────────────────────

def test_gate_design_review_refuses_missing_high_pitfall(tmp_path):
    """The contract refuses a design that omits a high pitfall id."""
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "design_review_attempts_used": 0,
        "has_pitfalls_step": True,
    })
    (tmp_path / "reviews").mkdir(parents=True, exist_ok=True)
    ctx.write_text("reviews/design-review.md",
                   "All confirmed.\n\nVERDICT: PASS\n")
    # A contract-compliant design with NO Anti-patterns section.
    ctx.write_text("design.md", _contract())
    ctx.write_json("brief.json", {"name": "x", "brief": "do a thing"})
    ctx.write_json("pitfalls.json", [_pit()])
    import refactory_dsh.design_ops as ops
    verdict = ops.gate_design_review(ctx)
    assert verdict == "reloop"
    assert "PIT-001" in verdict.reason


def test_gate_design_review_passes_set_aside(tmp_path):
    """A design that sets a pitfall aside with a reason passes."""
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "design_review_attempts_used": 0,
        "has_pitfalls_step": True,
    })
    (tmp_path / "reviews").mkdir(parents=True, exist_ok=True)
    ctx.write_text("reviews/design-review.md",
                   "All confirmed.\n\nVERDICT: PASS\n")
    ctx.write_text("design.md",
                   _contract(anti="- PIT-001: [set-aside: not applicable]"))
    ctx.write_json("brief.json", {"name": "x", "brief": "do a thing"})
    ctx.write_json("pitfalls.json", [_pit()])
    import refactory_dsh.design_ops as ops
    result = ops.gate_design_review(ctx)
    assert result == "proceed"
    assert "set aside" in ctx.facts.get("pitfall_coverage", "")


def test_gate_design_review_skips_without_pitfalls_step(tmp_path):
    """A graph without a research stage records why it has no pitfalls."""
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "design_review_attempts_used": 0,
        # has_pitfalls_step not set → skip
    })
    (tmp_path / "reviews").mkdir(parents=True, exist_ok=True)
    ctx.write_text("reviews/design-review.md",
                   "All confirmed.\n\nVERDICT: PASS\n")
    ctx.write_text("design.md", _contract())
    ctx.write_json("brief.json", {"name": "x", "brief": "do a thing"})
    import refactory_dsh.design_ops as ops
    result = ops.gate_design_review(ctx)
    assert result == "proceed"
    coverage = ctx.facts.get("pitfall_coverage", "")
    assert "skipped" in coverage.lower() or "no pitfalls" in coverage.lower()


# ── design graph structure ─────────────────────────────────────────────────

def test_design_graph_has_pitfalls_nodes():
    graph = DesignMode().build()
    assert "pitfalls_researcher" in graph.nodes
    assert "gate_pitfalls" in graph.nodes
    # Wiring: gate_research → classify_scale → pitfalls_researcher → gate_pitfalls →
    # route_strategy, which goes on to strategy_director for a full design.
    proceed_edges = graph.out_edges("gate_research", condition="proceed")
    assert len(proceed_edges) == 1
    assert proceed_edges[0].target == "classify_scale"
    cs_edges = graph.out_edges("classify_scale")
    assert cs_edges[0].target == "pitfalls_researcher"
    pit_edges = graph.out_edges("pitfalls_researcher")
    assert pit_edges[0].target == "gate_pitfalls"
    gp_proceed = graph.out_edges("gate_pitfalls", condition="proceed")
    assert gp_proceed[0].target == "route_strategy"
    assert graph.out_edges("route_strategy", condition="proceed")[0].target == "strategy_director"
    gp_reloop = graph.out_edges("gate_pitfalls", condition="reloop")
    assert gp_reloop[0].target == "pitfalls_researcher"


def test_design_prompts_contain_pitfalls():
    """The strategy and design prompts contain {{pitfalls}}."""
    graph = DesignMode().build()
    for node_id in ("strategy_director", "synthesize_strategy", "design_doc",
                    "design_review", "code_reviewer"):
        assert "{{pitfalls}}" in graph.nodes[node_id].prompt_template, (
            f"{node_id} prompt must contain {{{{pitfalls}}}}"
        )


def test_gate_pitfalls_validates_and_reloops(tmp_path):
    """gate_pitfalls reloops when pitfalls.json is invalid."""
    ctx = nn.RunContext(tmp_path, knobs={"pitfalls_attempts": 3})
    ctx.facts.update({"source": str(tmp_path), "pitfalls_attempts_used": 0})
    ctx.write_text("pitfalls.json", "not json")
    import refactory_dsh.design_ops as ops
    verdict = ops.gate_pitfalls(ctx)
    assert verdict == "reloop"
    assert "not valid JSON" in verdict.reason


def test_gate_pitfalls_stops_when_exhausted(tmp_path):
    """gate_pitfalls raises ValueError when attempts are exhausted."""
    ctx = nn.RunContext(tmp_path, knobs={"pitfalls_attempts": 1})
    ctx.facts.update({"source": str(tmp_path), "pitfalls_attempts_used": 0})
    ctx.write_text("pitfalls.json", "not json")
    import refactory_dsh.design_ops as ops
    with pytest.raises(ValueError, match="still not valid"):
        ops.gate_pitfalls(ctx)


def test_gate_pitfalls_proceeds_on_valid(tmp_path):
    """gate_pitfalls proceeds when pitfalls.json is valid."""
    ctx = nn.RunContext(tmp_path, knobs={"pitfalls_attempts": 3})
    ctx.facts.update({"source": str(tmp_path), "pitfalls_attempts_used": 0})
    ctx.write_text("pitfalls.json", PITFALLS_ANSWER)
    import refactory_dsh.design_ops as ops
    assert ops.gate_pitfalls(ctx) == "proceed"


# ── fixture repository with pinning test ───────────────────────────────────

def test_fixture_repo_gets_pitfall_for_pinning_test(tmp_path):
    """A fixture repo whose test pins behaviour gets a pitfall naming that test.

    Creates a repository with a test file containing ``assert timeout == 300``,
    writes a brief that asks to change the timeout, scripts a pitfall answer
    referencing the test file, line and assertion, and validates it through
    gate_pitfalls — matching what a scripted model would produce, as the
    existing research tests do.

    Fails without the pitfalls_researcher node and gate_pitfalls gate:
    there would be no node to write pitfalls.json and no gate to validate it.
    """
    # ── fixture repository with a pinning test ──
    repo = tmp_path / "repo"
    repo.mkdir()
    test_dir = repo / "tests"
    test_dir.mkdir()
    test_file = test_dir / "test_config.py"
    test_file.write_text(
        "def test_default_timeout():\n"
        "    timeout = 300\n"
        "    assert timeout == 300\n"
    )

    # ── scripted pitfall answer referencing the pinning test ──
    pitfall_answer = json.dumps([{
        "id": "PIT-001",
        "risk": "high",
        "category": "pinning_tests",
        "evidence": "tests/test_config.py:3",
        "prescription": (
            "The test asserts `timeout == 300`. The change must keep this "
            "assertion or deliberately update it with a reason."
        ),
    }])

    # ── run context pointing at the fixture repo as its source ──
    run = tmp_path / "run"
    ctx = nn.RunContext(run, knobs={"pitfalls_attempts": 3})
    ctx.facts.update({"source": str(repo), "pitfalls_attempts_used": 0})
    ctx.write_text("pitfalls.json", pitfall_answer)

    # gate_pitfalls validates the schema and the evidence file:line.
    import refactory_dsh.design_ops as ops
    assert ops.gate_pitfalls(ctx) == "proceed"

    # Verify the written pitfalls.json has the required fields.
    written = json.loads(ctx.read_text("pitfalls.json"))
    assert len(written) == 1
    p = written[0]
    assert p["category"] == "pinning_tests"
    assert "tests/test_config.py" in p["evidence"]
    assert ":3" in p["evidence"]
    assert pitfalls_mod.PIT_ID_RE.match(p["id"])
    assert p["risk"] == "high"
    assert p["prescription"]

    # The evidence file actually exists in the fixture repo.
    assert (repo / "tests" / "test_config.py").is_file()
    content = (repo / "tests" / "test_config.py").read_text()
    assert "assert timeout == 300" in content


# ── three-form evidence classifier ────────────────────────────────────────

def test_new_project_design_run_passes_pitfalls_gate(tmp_path):
    """T0: A scripted new-project design run whose pitfalls use absent: and
    brief.json evidence gets past gate_pitfalls and completes successfully.

    Red without C1: brief.json evidence (no colon) is rejected by the old
    colon check, gate_pitfalls reloops, and the run dies with ValueError.
    """
    new_project_pitfalls = json.dumps([
        {
            "id": "PIT-001",
            "risk": "high",
            "category": "scope_boundaries",
            "evidence": "absent: pyproject.toml",
            "prescription": "create a pyproject.toml",
        },
        {
            "id": "PIT-002",
            "risk": "medium",
            "category": "pinning_tests",
            "evidence": "brief.json",
            "prescription": "the brief asks for tests but none exist yet",
        },
    ])
    design_doc = (
        "# Do the thing\n\n"
        "## Summary\n\nFix it properly.\n\n"
        "## Failure trace\n\nN/A \u2014 this is a new feature.\n\n"
        "## Ask mapping\n\n"
        "| Ask (quoted from brief) | Design section | Test |\n"
        "|---|---|---|\n"
        '| "a tool" | \u00a7 Changes | tool.py |\n\n'
        "## Rule and sites\n\n"
        "- `tool.py:1` \u2014 **[changed]**\n\n"
        "## Docs\n\n"
        "- docs/models.md\n\n"
        "## Changes\n\n"
        "- tool.py\n\n"
        "## Research and perspective findings\n\n"
        "- FND-api-surface-1: addressed at `tool.py:1`\n"
        "- FND-existing-modes-1: addressed at \u00a7 Changes\n"
        "- FND-acceptance-1: addressed at `tool.py:1`\n"
        "- FND-simplest-1: addressed at `tool.py:1`\n"
        "- FND-criteria-1: addressed at \u00a7 Changes\n\n"
        "## Anti-patterns to avoid\n\n"
        "- PIT-001: addressed at `tool.py:1`\n"
        "- PIT-002: [set-aside: no tests exist yet in a new project]\n"
    )
    answers = [
        RESEARCH_ANSWER,
        new_project_pitfalls,
        "perspectives",
        "one plan",
        design_doc,
        DESIGN_REVIEW_ANSWER,
        render({"tool.py": "print('built')\n"}),
        *REVIEW_ANSWERS[:3],
        VERIFICATION_ANSWER,
        REVIEW_ANSWERS[3],
    ]
    result = DesignMode().run(
        tmp_path / "run",
        model=FakeModel(answers),
        verdicts={"gate_strategy": "proceed"},
        task={"name": "my_tool", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error


def test_empty_target_absent_evidence_passes_gate(tmp_path):
    """T1 (pin): absent: evidence with a truly absent path passes gate_pitfalls.

    This is a pin test — the colon in 'absent: pyproject.toml' means the old
    code did not reject it either. The red-green proofs are T0, T2, T2b, T4.
    """
    source = tmp_path / "repo"
    source.mkdir()
    run = tmp_path / "run"
    ctx = nn.RunContext(run, knobs={"pitfalls_attempts": 3})
    ctx.facts.update({"source": str(source), "pitfalls_attempts_used": 0})
    pitfalls_data = json.dumps([{
        "id": "PIT-001", "risk": "high", "category": "scope_boundaries",
        "evidence": "absent: pyproject.toml",
        "prescription": "create a pyproject.toml",
    }])
    ctx.write_text("pitfalls.json", pitfalls_data)
    import refactory_dsh.design_ops as ops
    assert ops.gate_pitfalls(ctx) == "proceed"


def test_false_absence_claim_refused_naming_path(tmp_path):
    """T2: A false absence claim (the path exists) is refused, naming the path.

    Red without C1: the old code passes 'absent: pyproject.toml' because it
    contains a colon, then the regex finds no file:N match and silently skips.
    """
    source = tmp_path / "repo"
    source.mkdir()
    (source / "pyproject.toml").write_text("[project]\n")
    raw = json.dumps([{
        "id": "PIT-001", "risk": "high", "category": "scope_boundaries",
        "evidence": "absent: pyproject.toml",
        "prescription": "create a pyproject.toml",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw, str(source))
    assert any("false absence" in p and "pyproject.toml" in p for p in problems)


def test_false_glob_absence_claim_refused_naming_matches(tmp_path):
    """T2b: A false glob absence claim is refused, naming match(es).

    Red without C1: the old code treats 'absent: tests/test_*.py' as a
    potential file:line citation, finds no digit after *, and silently passes.
    """
    source = tmp_path / "repo"
    (source / "tests").mkdir(parents=True)
    (source / "tests" / "test_foo.py").write_text("")
    (source / "tests" / "test_bar.py").write_text("")
    raw = json.dumps([{
        "id": "PIT-001", "risk": "high", "category": "scope_boundaries",
        "evidence": "absent: tests/test_*.py",
        "prescription": "no tests yet",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw, str(source))
    assert any("false absence" in p and "test_" in p for p in problems)


def test_no_citation_still_refused():
    """T3 (pin): Evidence with no colon, no absent:, no run-file pattern is
    still refused.

    Green both before and after: the existing colon check already catches this.
    """
    raw = json.dumps([{
        "id": "PIT-001", "risk": "high", "category": "pinning_tests",
        "evidence": "the code is fragile",
        "prescription": "fix it",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw)
    assert any("does not cite" in p for p in problems)


def test_run_file_citation_accepted_when_exists_refused_when_not(tmp_path):
    """T4: A run-file citation is accepted when the file exists, and refused
    when it does not.

    Red without C1: brief.json has no colon and is rejected by the old check.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # brief.json exists -> accepted
    (run_dir / "brief.json").write_text("{}")
    raw_brief = json.dumps([{
        "id": "PIT-001", "risk": "high", "category": "scope_boundaries",
        "evidence": "brief.json",
        "prescription": "see the brief",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw_brief, "", run_dir=str(run_dir))
    assert not any("brief.json" in p for p in problems)

    # research file exists -> accepted
    (run_dir / "research").mkdir()
    (run_dir / "research" / "api-surface.md").write_text("# Findings\n")
    raw_research = json.dumps([{
        "id": "PIT-001", "risk": "high", "category": "scope_boundaries",
        "evidence": "research/api-surface.md",
        "prescription": "see the research",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw_research, "", run_dir=str(run_dir))
    assert not any("api-surface" in p for p in problems)

    # brief.json does not exist -> refused
    empty_run = tmp_path / "empty_run"
    empty_run.mkdir()
    problems = pitfalls_mod.validate_pitfalls(raw_brief, "", run_dir=str(empty_run))
    assert any("brief.json" in p and "not found" in p for p in problems)

    # research file does not exist -> refused
    problems = pitfalls_mod.validate_pitfalls(raw_research, "", run_dir=str(empty_run))
    assert any("api-surface" in p and "not found" in p for p in problems)


def test_absent_claim_rejects_absolute_and_escape_paths():
    """T5: Absence claims with absolute or .. paths are refused as unsafe."""
    raw_abs = json.dumps([{
        "id": "PIT-001", "risk": "high", "category": "scope_boundaries",
        "evidence": "absent: /etc/passwd",
        "prescription": "should not work",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw_abs)
    assert any("absolute" in p for p in problems)

    raw_escape = json.dumps([{
        "id": "PIT-001", "risk": "high", "category": "scope_boundaries",
        "evidence": "absent: ../../etc/passwd",
        "prescription": "should not work",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw_escape)
    assert any("escapes" in p for p in problems)

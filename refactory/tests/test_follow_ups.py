"""A build is held back only by a core defect; non-core findings become follow-ups.

Reviewers tag each BLOCKING finding `[core]` (an ask, a criterion, a correction or
constraint, or a regression) or `[non-core]` (extra tests, docs, edge cases beyond the
asks, refactors, style). An untagged finding is core. A round whose remaining findings
are all non-core proceeds without spending a review attempt, and the pull request lists
them as follow-ups. A round with a core defect sends the build back, and its non-core
findings ride along in the same feedback.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from refactory_dsh import create_ops, lowering, registry
from refactory_dsh.create_ops import MARKER_RULE, OVERWATCH_VERDICT_RULE, SEVERITY_RULE, is_non_core, split_core
from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, PITFALLS_ANSWER,
                           RESEARCH_ANSWER, VERIFICATION_ANSWER, FakeModel, git_repo,
                           render)

BUILT = render({"tool.py": "def build():\n    print('built')\n"})
# A later round must change something: an emission identical to the last is refused.
REBUILT = render({"tool.py": "def build():\n    print('built')\n\n\nbuild()\n"})
NON_CORE = "BLOCKING: tool.py: add a test for an empty input file [non-core]"
CORE = "BLOCKING: tool.py: build() prints nothing when run as a script [core: brief]"
BUILDER = "## What the reviews refused"


def _design_run(tmp_path, answers):
    from refactory_dsh.design import DesignMode

    repo = git_repo(tmp_path / "repo")
    model = FakeModel(answers)
    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["pr"]["value"] = False
    mode.load_state_dict(state)
    run = mode.run(
        tmp_path / "run", model=model,
        verdicts={"gate_strategy": "proceed"},
        task={"name": "fu_tool", "brief": "a tool",
              "check": f"{sys.executable} tool.py", "target": str(repo)})
    return run, model


def _round(health="health: ok", code="code review: ok", qa="qa: ok"):
    return [health, code, qa, VERIFICATION_ANSWER]


def _planning():
    return [RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
            DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER]


# ── the tags ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("finding, non_core", [
    ("tool.py: no test for unicode input [non-core]", True),
    ("tool.py: no test for unicode input [Non-Core]", True),
    ("tool.py: no test for unicode input [non core].", True),
    ("tool.py: crashes on empty input", False),               # untagged is core
    # A tag on a continuation line, or quoted mid-line, does not classify the finding.
    ("cli.py: --json is not implemented (ask 2)\n- help text also missing [non-core]", False),
    ('tool.py: the "[non-core]" marker is parsed wrongly', False),
    ("tool.py: crashes on empty input [core: criterion-1]", False),
    ("tool.py: crashes [core] [non-core]", False),            # doubly tagged is core
])
def test_only_a_finding_tagged_non_core_alone_is_non_core(finding, non_core):
    assert is_non_core(finding) is non_core


def test_split_keeps_order():
    core, non_core = split_core(["a [non-core]", "b", "c [core]", "d [non-core]"])
    assert core == ["b", "c [core]"]
    assert non_core == ["a [non-core]", "d [non-core]"]


# ── full design: gate_qa ──────────────────────────────────────────────────

def test_a_non_core_only_round_delivers_with_follow_ups(tmp_path):
    run, model = _design_run(tmp_path, [
        *_planning(), BUILT,
        *_round(code=f"code review: the code matches the design.\n\n{NON_CORE}"),
        "VERDICT: PASS"])

    assert run.exit_ok, run.error
    assert len([p for p in model.prompts if BUILDER in p]) == 1
    assert int(run.facts.get("review_attempts_used", 0)) == 0
    follow_ups = (tmp_path / "run/reviews/follow-ups.md").read_text()
    assert "add a test for an empty input file" in follow_ups
    assert (tmp_path / "run/reviews/qa-gate.txt").read_text().startswith(
        "proceed: no core finding")
    pull_request = (tmp_path / "run/pull-request.md").read_text()
    assert "## Follow-ups (non-core findings)" in pull_request
    assert "add a test for an empty input file" in pull_request
    assert "found no core defect; 1 non-core finding(s) are listed" in pull_request
    sources = json.loads((tmp_path / "run/reviews/finding-sources.json").read_text())
    entry = next(e for e in sources if "empty input file" in e["finding"])
    assert entry["class"] == "non-core" and entry["fate"] == "follow-up"


def test_an_untagged_blocking_finding_is_core(tmp_path):
    untagged = "BLOCKING: tool.py: build() prints nothing when run as a script"
    run, model = _design_run(tmp_path, [
        *_planning(), BUILT,
        *_round(code=f"code review: a defect.\n\n{untagged}"),
        REBUILT, *_round(), "VERDICT: PASS"])

    assert run.exit_ok, run.error
    builders = [p for p in model.prompts if BUILDER in p]
    assert len(builders) == 2
    assert "prints nothing when run as a script" in builders[1]
    assert not (tmp_path / "run/reviews/follow-ups.json").exists() or json.loads(
        (tmp_path / "run/reviews/follow-ups.json").read_text()).get("qa") == []


def test_non_core_findings_ride_along_with_a_core_defect(tmp_path):
    run, model = _design_run(tmp_path, [
        *_planning(), BUILT,
        *_round(code=f"code review: two findings.\n\n{CORE}\n\n{NON_CORE}"),
        REBUILT, *_round(), "VERDICT: PASS"])

    assert run.exit_ok, run.error
    builders = [p for p in model.prompts if BUILDER in p]
    assert len(builders) == 2
    assert "prints nothing when run as a script" in builders[1]
    assert "Non-core findings (fix them if it is cheap" in builders[1]
    assert "add a test for an empty input file" in builders[1]
    # The second round's reviews found nothing, so nothing is left to follow up.
    assert "## Follow-ups" not in (tmp_path / "run/pull-request.md").read_text()


def test_a_finding_one_reviewer_calls_core_blocks_even_if_another_calls_it_non_core(tmp_path):
    run, model = _design_run(tmp_path, [
        *_planning(), BUILT,
        *_round(health="health: one gap.\n\nBLOCKING: tool.py: no test for unicode input [non-core]",
                qa="qa: one gap.\n\nBLOCKING: tool.py: no test for unicode input [core: brief]"),
        REBUILT, *_round(), "VERDICT: PASS"])

    assert run.exit_ok, run.error
    assert len([p for p in model.prompts if BUILDER in p]) == 2


def test_a_follow_up_a_later_round_fixed_is_not_listed(tmp_path):
    """QA proceeds with a non-core finding, overwatch sends back a core defect, and the
    next round's reviews find nothing: the pull request lists no follow-up."""
    overwatch_fail = f"An ask is unmet.\n\n{CORE}\n\nVERDICT: FAIL"
    run, model = _design_run(tmp_path, [
        *_planning(), BUILT,
        *_round(code=f"code review: one gap.\n\n{NON_CORE}"), overwatch_fail,
        REBUILT, *_round(), "VERDICT: PASS"])

    assert run.exit_ok, run.error
    assert len([p for p in model.prompts if BUILDER in p]) == 2
    assert json.loads((tmp_path / "run/reviews/follow-ups.json").read_text()) == {
        "qa": [], "overwatch": []}
    assert "## Follow-ups" not in (tmp_path / "run/pull-request.md").read_text()


def test_follow_ups_stay_out_of_the_repository_questions_advisory(tmp_path):
    """The pull request reads the repository-question advisory to the end of qa-gate.txt;
    a follow-up listed after it would be reported as an unanswered repository question."""
    bad_code_review = ("code review: the code matches the design.\n\n"
                       f"{NON_CORE}\n\n## Repository questions\n\n```json\n[]\n```")
    run, model = _design_run(tmp_path, [
        *_planning(), BUILT,
        *_round(code=bad_code_review),      # one review pass; no re-run for the questions
        "VERDICT: PASS"])

    assert run.exit_ok, run.error
    pull_request = (tmp_path / "run/pull-request.md").read_text()
    questions = pull_request.split("Repository questions left unanswered (advisory)", 1)[1]
    questions = questions.split("## ", 1)[0]
    assert "tool.py::build" in questions
    assert "empty input file" not in questions
    assert "empty input file" in pull_request.split("## Follow-ups (non-core findings)", 1)[1]


# ── full design: gate_overwatch ───────────────────────────────────────────

def test_an_overwatch_pass_naming_non_core_findings_delivers_with_them(tmp_path):
    overwatch = ("Every ask has evidence.\n\n"
                 "BLOCKING: README.md: document the tool's output [non-core]\n\n"
                 "VERDICT: PASS")
    run, model = _design_run(tmp_path, [*_planning(), BUILT, *_round(), overwatch])

    assert run.exit_ok, run.error
    assert len([p for p in model.prompts if BUILDER in p]) == 1
    recorded = json.loads((tmp_path / "run/reviews/follow-ups.json").read_text())
    assert recorded["overwatch"] == ["README.md: document the tool's output [non-core]"]
    assert "document the tool's output" in (tmp_path / "run/pull-request.md").read_text()


class _GateCtx:
    """The parts of a run context gate_overwatch reads and writes."""

    def __init__(self, workdir: Path, overwatch: str) -> None:
        self.workdir = workdir
        self.facts: dict = {}
        self.knobs: dict = {}
        (workdir / "reviews").mkdir(parents=True, exist_ok=True)
        (workdir / "reviews/overwatch.md").write_text(overwatch)

    def exists(self, name):
        return (self.workdir / name).exists()

    def read_text(self, name):
        return (self.workdir / name).read_text()

    def read_json(self, name, default=None):
        path = self.workdir / name
        return json.loads(path.read_text()) if path.exists() else default

    def write_text(self, name, text):
        path = self.workdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def write_json(self, name, payload):
        self.write_text(name, json.dumps(payload))

    def set_fact(self, name, value):
        self.facts[name] = value

    def knob(self, name, default=None):
        return self.knobs.get(name, default)


def _overwatch_gate(tmp_path, monkeypatch, *, verdict: str, audit: list[str]):
    from refactory_dsh import authoring_evidence as evidence

    monkeypatch.setattr(evidence, "current_problems", lambda ctx, workers: [])
    monkeypatch.setattr(evidence, "audit_problems", lambda ctx: list(audit))
    monkeypatch.setattr(evidence, "correction_audit_problems", lambda ctx: [])
    ctx = _GateCtx(tmp_path, "BLOCKING: README.md: document the output [non-core]\n\n"
                             f"VERDICT: {verdict}")
    return ctx, create_ops.gate_overwatch(ctx)


def test_a_non_core_finding_with_an_unmet_requirement_does_not_deliver(tmp_path, monkeypatch):
    ctx, verdict = _overwatch_gate(tmp_path, monkeypatch, verdict="PASS",
                                   audit=["brief: no requirement evidence in intent audit"])
    assert verdict == "audit"
    assert not ctx.exists("reviews/follow-ups.json")


def test_an_overwatch_fail_blocks_even_when_its_findings_are_non_core(tmp_path, monkeypatch):
    # The FAIL may rest on an ask named only in prose; the overwatch is told to state
    # PASS when only non-core findings remain, so a FAIL is never overridden.
    ctx, verdict = _overwatch_gate(tmp_path, monkeypatch, verdict="FAIL", audit=[])
    assert verdict == "reloop"
    assert not ctx.exists("reviews/follow-ups.json")


def test_a_non_core_overwatch_finding_with_every_requirement_met_proceeds(tmp_path, monkeypatch):
    ctx, verdict = _overwatch_gate(tmp_path, monkeypatch, verdict="PASS", audit=[])
    assert verdict == "proceed"
    assert ctx.read_json("reviews/follow-ups.json") == {
        "overwatch": ["README.md: document the output [non-core]"]}


# ── design_fast and create share the rule ─────────────────────────────────

def test_design_fast_non_core_only_review_proceeds_with_follow_ups(tmp_path, monkeypatch):
    from refactory_dsh.fast import DesignFastMode

    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    qa = json.dumps({"status": "fail", "checks": [
        {"name": "requested behavior", "command": "source inspection", "status": "pass",
         "evidence": "the implementation returns the requested literal"},
        {"name": "module docstring present [non-core]", "command": "source inspection",
         "status": "fail", "evidence": "answer.py has no docstring"}],
        "findings": ["answer.py: add a docstring [non-core]"], "not_run": []})
    client = FakeModel(["a plan", "===FILE: answer.py===\nassert 1 + 1 == 2\n", qa,
                        VERIFICATION_ANSWER])
    run = DesignFastMode().run(tmp_path / "run", model=client, task={
        "name": "small", "brief": "assert two", "check": f"{sys.executable} answer.py"})

    assert run.exit_ok, run.error
    report = json.loads((tmp_path / "run/fast-report.json").read_text())
    assert report["repairs_used"] == 0
    assert "follow-up: answer.py: add a docstring [non-core]" in report["findings"]
    assert json.loads((tmp_path / "run/reviews/follow-ups.json").read_text()) == {
        "focused_qa": ["focused QA check did not pass: module docstring present [non-core]",
                       "answer.py: add a docstring [non-core]"]}


CREATE_NAME = "follow_up_pkg"


@pytest.fixture
def _clean_created_package():
    root = registry.workspace_root(Path(".")) / CREATE_NAME
    shutil.rmtree(root, ignore_errors=True)
    yield
    shutil.rmtree(root, ignore_errors=True)


def test_create_non_core_only_review_proceeds_with_follow_ups(tmp_path, _clean_created_package):
    from refactory_dsh import create as create_module
    from refactory_dsh.create import CreateConfig
    from tests_support import REVIEW_ANSWERS, create_answers, good_files

    answers = create_answers(CREATE_NAME, good_files(CREATE_NAME))
    at = answers.index(REVIEW_ANSWERS[1])
    answers[at] = REVIEW_ANSWERS[1] + "\n\nBLOCKING: ops.py: add a docstring [non-core]"
    model = FakeModel(answers)
    result = create_module.create(
        CreateConfig(verdicts={"gate_strategy": "proceed"}, single_shot=True,
                     name=CREATE_NAME, brief="count to a target", workdir=tmp_path / "run"),
        client=model)

    assert result.smoke_score and result.smoke_score > 0
    recorded = json.loads((tmp_path / "run/reviews/follow-ups.json").read_text())
    assert recorded["qa"] == ["reviews/code-review.md: ops.py: add a docstring [non-core]"]


# ── every reviewer is told, on both runtimes ──────────────────────────────

@pytest.mark.parametrize("mode_path, reviewers", [
    ("refactory_dsh.design:DesignMode",
     ("health_checker", "code_reviewer", "qa_director", "overwatch")),
    ("refactory_dsh.create:CreateMode",
     ("health_checker", "code_reviewer", "qa_director", "overwatch")),
    ("refactory_dsh.edit:EditMode",
     ("health_checker", "code_reviewer", "qa_director", "overwatch")),
    ("refactory_dsh.fast:DesignFastMode", ("focused_qa",)),
    ("refactory_dsh.fast:CreateFastMode", ("focused_qa",)),
    ("refactory_dsh.fast:EditFastMode", ("focused_qa",)),
])
def test_every_post_build_reviewer_is_told_to_tag_its_findings(tmp_path, mode_path, reviewers):
    import importlib

    module, cls = mode_path.split(":")
    mode = getattr(importlib.import_module(module), cls)()
    nodes = mode.graph().nodes
    for reviewer in reviewers:
        assert SEVERITY_RULE in nodes[reviewer].prompt_template, reviewer
    if "overwatch" in reviewers:
        # Its FAIL always blocks, so it must be told to state PASS over non-core findings.
        assert OVERWATCH_VERDICT_RULE in nodes["overwatch"].prompt_template
    # The spine runs the lowered graph: the rule reaches it in each reviewer's prompt.
    path, _workflow = lowering.write_mode_graph(mode.state_dict(), tmp_path, name="x")
    assert path.read_text().count("`[non-core]` otherwise") >= len(reviewers)


@pytest.mark.parametrize("mode_path, reviewers", [
    ("refactory_dsh.design:DesignMode",
     ("health_checker", "code_reviewer", "qa_director", "overwatch", "design_review")),
    ("refactory_dsh.create:CreateMode",
     ("health_checker", "code_reviewer", "qa_director", "overwatch")),
    ("refactory_dsh.edit:EditMode",
     ("health_checker", "code_reviewer", "qa_director", "overwatch")),
    ("refactory_dsh.fast:DesignFastMode", ("focused_qa",)),
    ("refactory_dsh.fast:CreateFastMode", ("focused_qa",)),
    ("refactory_dsh.fast:EditFastMode", ("focused_qa",)),
])
def test_every_reviewer_carries_marker_rule(tmp_path, mode_path, reviewers):
    import importlib

    module, cls = mode_path.split(":")
    mode = getattr(importlib.import_module(module), cls)()
    nodes = mode.graph().nodes
    for reviewer in reviewers:
        assert MARKER_RULE in nodes[reviewer].prompt_template, reviewer
    # The spine runs the lowered graph: MARKER_RULE must appear in each reviewer node.
    import json
    path, _workflow = lowering.write_mode_graph(mode.state_dict(), tmp_path, name="x")
    graph = json.loads(path.read_text())
    for reviewer in reviewers:
        node = graph["nodes"][reviewer]
        assert MARKER_RULE in node["prompt_template"], (
            f"{reviewer} node in lowered graph missing MARKER_RULE"
        )


def test_marker_rule_keeps_its_wording():
    # The rule's wording is the whole fix (dm-198, dm-225): pin each clause, so
    # emptying or shortening it fails, not only removing it from a prompt.
    for clause in (
        "only at the start of a finding line",
        "plain form: BLOCKING: <path>: <description>",
        "Never wrap it in backticks, a code span, or quotation marks",
        'call it "the blocking marker"',
        "inside a fenced code block",
    ):
        assert clause in MARKER_RULE, clause
    assert chr(96) not in MARKER_RULE


@pytest.mark.parametrize("mode_path", [
    "refactory_dsh.design:DesignMode",
    "refactory_dsh.create:CreateMode",
    "refactory_dsh.edit:EditMode",
    "refactory_dsh.fast:DesignFastMode",
    "refactory_dsh.fast:CreateFastMode",
    "refactory_dsh.fast:EditFastMode",
])
def test_every_lowered_node_that_asks_for_findings_carries_the_rule(tmp_path, mode_path):
    # Not a hand-kept reviewer list: every node of the lowered graph whose prompt
    # shows the finding form, the verifier included, must carry the rule.
    import importlib
    import json

    module, cls = mode_path.split(":")
    mode = getattr(importlib.import_module(module), cls)()
    path, _workflow = lowering.write_mode_graph(mode.state_dict(), tmp_path, name="x")
    nodes = json.loads(path.read_text())["nodes"]
    asking = [node_id for node_id, node in nodes.items()
              if "BLOCKING: <" in str(node.get("prompt_template") or "")]
    assert asking, f"{mode_path}: no lowered node asks for blocking findings"
    for node_id in asking:
        assert MARKER_RULE in nodes[node_id]["prompt_template"], (mode_path, node_id)


def test_verifier_prompt_carries_marker_rule():
    from refactory_dsh.verification import VERIFIER_PROMPT, FULL_VERIFIER_PROMPT
    assert MARKER_RULE in VERIFIER_PROMPT
    assert MARKER_RULE in FULL_VERIFIER_PROMPT
    # replay_harness builds its verifier prompt independently (PIT-005);
    # capture the constructed prompt via a mock client.
    from refactory_dsh.replay_harness import _make_verifier_fn

    class _Capture:
        def complete(self, prompt, **kw):
            self.prompt = prompt
            return "VERDICT: PASS"

    cap = _Capture()
    _make_verifier_fn(cap)("brief", "diff")
    assert MARKER_RULE in cap.prompt, (
        "replay_harness verifier prompt missing MARKER_RULE"
    )


@pytest.mark.parametrize("mode_path, reviewers", [
    ("refactory_dsh.design:DesignMode",
     ("health_checker", "code_reviewer", "qa_director", "overwatch", "design_review")),
    ("refactory_dsh.create:CreateMode",
     ("health_checker", "code_reviewer", "qa_director", "overwatch")),
    ("refactory_dsh.edit:EditMode",
     ("health_checker", "code_reviewer", "qa_director", "overwatch")),
    ("refactory_dsh.fast:DesignFastMode", ("focused_qa",)),
    ("refactory_dsh.fast:CreateFastMode", ("focused_qa",)),
    ("refactory_dsh.fast:EditFastMode", ("focused_qa",)),
])
def test_no_reviewer_prompt_shows_blocking_in_code_span(mode_path, reviewers):
    import importlib

    module, cls = mode_path.split(":")
    mode = getattr(importlib.import_module(module), cls)()
    nodes = mode.graph().nodes
    for reviewer in reviewers:
        prompt = nodes[reviewer].prompt_template
        assert "`BLOCKING" not in prompt, f"{reviewer} prompt contains `BLOCKING code span"
        assert "BLOCKING:`" not in prompt, f"{reviewer} prompt contains BLOCKING:` code span"
        assert '"BLOCKING' not in prompt and "'BLOCKING" not in prompt, (
            f"{reviewer} prompt quotes the marker")


def test_verifier_prompt_no_code_span_blocking():
    from refactory_dsh.verification import VERIFIER_PROMPT, FULL_VERIFIER_PROMPT
    for name, prompt in [("VERIFIER_PROMPT", VERIFIER_PROMPT),
                         ("FULL_VERIFIER_PROMPT", FULL_VERIFIER_PROMPT)]:
        assert "`BLOCKING" not in prompt, f"{name} contains `BLOCKING code span"
        assert "BLOCKING:`" not in prompt, f"{name} contains BLOCKING:` code span"
    # replay_harness builds its verifier prompt independently; check it too.
    from refactory_dsh.replay_harness import _make_verifier_fn

    class _Capture:
        def complete(self, prompt, **kw):
            self.prompt = prompt
            return "VERDICT: PASS"

    cap = _Capture()
    _make_verifier_fn(cap)("brief", "diff")
    assert "`BLOCKING" not in cap.prompt, "replay_harness verifier contains `BLOCKING code span"
    assert "BLOCKING:`" not in cap.prompt, "replay_harness verifier contains BLOCKING:` code span"

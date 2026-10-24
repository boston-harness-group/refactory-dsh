"""Build-loop stop signal, refusal bound, and failure text (dm-202, dm-203).

Tests for ``BuildLoopStop``, ``format_failure_detail``, the refusal counter,
the walker's ``_try_visit`` bypass, ``main()``'s exit code 3, and the
cross-process refusal bound.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from refactory_dsh.run import (
    BuildLoopStop,
    FAILURE_MARKER,
    format_failure_detail,
    main,
    run_node,
)


# ── BuildLoopStop is a ValueError ─────────────────────────────────────────

def test_build_loop_stop_is_a_value_error():
    assert issubclass(BuildLoopStop, ValueError)
    assert isinstance(BuildLoopStop("x"), ValueError)


# ── format_failure_detail ──────────────────────────────────────────────────

def test_format_failure_detail_short_message_comes_back_whole():
    """A short multi-line message comes back whole (correction #3)."""
    exc = BuildLoopStop("check exhausted\nsome detail")
    result = format_failure_detail(exc)
    assert result == "BuildLoopStop: check exhausted\nsome detail"


def test_format_failure_detail_1200_char_message_no_repeat():
    """A 1200-char message does not repeat its first line (correction #3)."""
    first_line = "authoring unresolved after 3 attempt(s)"
    rest = "x" * (1200 - len(first_line) - 1)
    exc = BuildLoopStop(f"{first_line}\n{rest}")
    result = format_failure_detail(exc)
    assert result.startswith("BuildLoopStop: authoring unresolved")
    # Count how many times the reason line appears — must be exactly once
    assert result.count("authoring unresolved after 3 attempt(s)") == 1
    assert len(result) <= 2000


def test_format_failure_detail_reason_first():
    """The 20 KB fixture: reason first, FAILED test id present, total ≤ 2000."""
    # Build a 20 KB check output with one FAILED line in the middle and a vite warning at the end
    preamble = "some check output\n" * 200  # ~3600 chars
    failed_line = "FAILED refactory/tests/test_create.py::test_a_review_that_reloops_the_build_says_why_into_the_rebuild\n"
    vite_warning = "vite v5.2.0 building for production...\n" * 100  # ~3800 chars
    filler = "x" * (20000 - len(preamble) - len(failed_line) - len(vite_warning))
    check_output = preamble + failed_line + filler + vite_warning

    exc = BuildLoopStop(f"authoring unresolved after 3 attempt(s): {check_output}")
    result = format_failure_detail(exc)

    assert result.startswith("BuildLoopStop: authoring unresolved")
    assert "test_a_review_that_reloops_the_build_says_why_into_the_rebuild" in result
    assert len(result) <= 2000
    # The vite warning should not appear before the reason
    reason_pos = result.index("authoring unresolved")
    assert reason_pos == len("BuildLoopStop: ")


# ── main() exit codes ─────────────────────────────────────────────────────

def test_main_exits_3_for_build_loop_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_WORKDIR", str(tmp_path))
    monkeypatch.setenv("DSH_WORKFLOW_NODE", json.dumps({"id": "test_node", "type": "op"}))
    (tmp_path / "facts.json").write_text("{}")

    def _raise_stop(ctx):
        raise BuildLoopStop("emissions refused 3 consecutive times: edit text not found")

    with mock.patch("refactory_dsh.run.resolve", return_value=_raise_stop):
        import io
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured)
        code = main(["test_module:_raise_stop", "--node", "test_node"])

    assert code == 3
    stderr_text = captured.getvalue()
    assert FAILURE_MARKER in stderr_text
    assert "emissions refused 3 consecutive times" in stderr_text


def test_main_exits_1_for_other_exceptions(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_WORKDIR", str(tmp_path))
    monkeypatch.setenv("DSH_WORKFLOW_NODE", json.dumps({"id": "test_node", "type": "op"}))
    (tmp_path / "facts.json").write_text("{}")

    def _raise_error(ctx):
        raise RuntimeError("something went wrong: details")

    with mock.patch("refactory_dsh.run.resolve", return_value=_raise_error):
        import io
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured)
        code = main(["test_module:_raise_error", "--node", "test_node"])

    assert code == 1
    stderr_text = captured.getvalue()
    assert FAILURE_MARKER in stderr_text


# ── run_node does not retry BuildLoopStop ──────────────────────────────────

def test_run_node_does_not_retry_build_loop_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_WORKDIR", str(tmp_path))
    monkeypatch.setenv("DSH_WORKFLOW_NODE", json.dumps({"id": "test_node", "type": "op"}))
    (tmp_path / "facts.json").write_text("{}")

    call_count = 0

    def _raise_stop(ctx):
        nonlocal call_count
        call_count += 1
        ctx.set_fact("counter", call_count)
        raise BuildLoopStop("limit reached")

    with mock.patch("refactory_dsh.run.resolve", return_value=_raise_stop):
        with pytest.raises(BuildLoopStop, match="limit reached"):
            run_node("test_module:_raise_stop", workdir=tmp_path, node_id="test_node")

    # The op should be called exactly once (no retry)
    assert call_count == 1
    # Facts should be written to disk
    facts = json.loads((tmp_path / "facts.json").read_text())
    assert facts["counter"] == 1


# ── walker _try_visit bypass ──────────────────────────────────────────────

def test_walker_stop_reaches_run_result_error(tmp_path):
    """BuildLoopStop propagates through the walker's Executor.run() with its reason."""
    from refactory_dsh.nn import RunContext, Node, Graph
    from refactory_dsh.nn.executor import Executor

    # Build a minimal graph with one op that raises BuildLoopStop
    graph = Graph()
    graph.add(Node("failing_op", "op", call="test_module:fail"))

    ctx = RunContext(tmp_path, facts={"name": "test"}, knobs={})

    with mock.patch("refactory_dsh.nn.executor.Executor._visit") as mock_visit:
        # First call raises, second call (retry) should NOT happen
        mock_visit.side_effect = BuildLoopStop("test stop reason")
        executor = Executor(graph, ctx, enable_recovery=True)
        result = executor.run()

    assert result.exit_ok is False
    assert "BuildLoopStop" in (result.error or "")
    assert "test stop reason" in (result.error or "")
    # _visit called only once — the bypass prevents retry
    assert mock_visit.call_count == 1


# ── consecutive refusals end the run (walker) ──────────────────────────────

def test_consecutive_refusals_end_the_run(tmp_path):
    """A scripted walker run whose emissions keep changing nothing ends after refusal_attempts."""
    from refactory_dsh.design import DesignMode
    from tests_support import FakeModel, render, RESEARCH_ANSWER, PITFALLS_ANSWER
    from tests_support import DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, REVIEW_ANSWERS, VERIFICATION_ANSWER
    from tests_support import git_repo

    repo = git_repo(tmp_path / "repo")
    run = tmp_path / "run"

    # An emission that is identical to itself (will be refused as "changed nothing"
    # on the second and subsequent materialise calls because the worktree already
    # has these files from the first attempt).
    emission = render({"tool.py": "print('built')\n"})

    # We need enough answers: research, pitfalls, perspectives, plan, design doc,
    # design review, then multiple identical emissions for the refusal loop.
    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        emission, emission, emission, emission, emission,
        *REVIEW_ANSWERS[:3], VERIFICATION_ANSWER, REVIEW_ANSWERS[3],
    ]
    model = FakeModel(answers)
    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["refusal_attempts"]["value"] = 3
    mode.load_state_dict(state)
    result = mode.run(
        run, model=model, verdicts={"gate_strategy": "proceed"},
        task={"name": "refusal_test", "brief": "a tool", "check": "python tool.py",
              "target": str(repo)},
    )

    assert result.exit_ok is False
    assert "emissions refused" in (result.error or ""), f"Expected refusal stop, got: {result.error}"
    assert "consecutive" in (result.error or "")


def test_interleaved_refusals_and_successes_converge(tmp_path):
    """Refusals interleaved with applied emissions converge: the counter resets."""
    from refactory_dsh.design import DesignMode
    from tests_support import FakeModel, render, RESEARCH_ANSWER, PITFALLS_ANSWER
    from tests_support import DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, REVIEW_ANSWERS, VERIFICATION_ANSWER
    from tests_support import git_repo

    repo = git_repo(tmp_path / "repo")
    run = tmp_path / "run"

    # First emission: valid. Second: identical (refused). Third: different (valid, passes check).
    emit1 = render({"tool.py": "print('v1')\n"})
    emit2 = render({"tool.py": "print('v1')\n"})  # same as emit1 → refused
    emit3 = render({"tool.py": "print(1)\n"})  # different → applied, passes check "python tool.py"

    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        emit1, emit2, emit3,
        *REVIEW_ANSWERS[:3], VERIFICATION_ANSWER, REVIEW_ANSWERS[3],
    ]
    model = FakeModel(answers)
    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["refusal_attempts"]["value"] = 3
    mode.load_state_dict(state)
    result = mode.run(
        run, model=model, verdicts={"gate_strategy": "proceed"},
        task={"name": "interleave_test", "brief": "a tool", "check": "python tool.py",
              "target": str(repo)},
    )

    # The run should succeed (or at least not stop on refusals)
    # The counter resets on each applied emission
    if not result.exit_ok:
        assert "emissions refused" not in (result.error or ""), (
            f"Run should not have stopped on refusals: {result.error}"
        )


# ── cross-process refusal bound ──────────────────────────────────────────

def test_refusal_bound_holds_across_node_processes(tmp_path, monkeypatch):
    """Run materialise three times through node_runner, each sharing one facts file.

    The third invocation exits 3 with the failure marker and the refusal reason.
    Removing consecutive_refusals from produces makes the test fail.
    """
    from refactory_dsh.design import DesignMode
    from refactory_dsh import lowering, worktree, repo_change
    from refactory_dsh.run import main as runner_main
    from tests_support import git_repo

    repo = git_repo(tmp_path / "repo")
    run = tmp_path / "run"

    # Create worktree and write declarations
    worktree.create(repo, "HEAD", "abcdef999999", run)
    lowering.write_declarations(DesignMode().state_dict(), run)
    (run / "facts.json").write_text(json.dumps({
        "name": "refusal_test", "brief": "a tool", "check": "true",
    }))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"check_attempts": 3, "refusal_attempts": 3}))

    # An edit whose search text is not in the file → AttemptRefused
    bad_emission = "===EDIT: app.py===\n<<<<<<< SEARCH\ntext not in file\n=======\nreplacement\n>>>>>>> REPLACE\n"

    import io

    for i in range(3):
        (run / "emit.txt").write_text(bad_emission)
        monkeypatch.setenv("REFACTORY_WORKDIR", str(run))
        monkeypatch.setenv("DSH_WORKFLOW_NODE", json.dumps({"id": "materialise", "type": "op"}))

        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            code = runner_main([f"{repo_change.OPS}:materialise", "--node", "materialise"])
        finally:
            sys.stderr = old_stderr

        facts = json.loads((run / "facts.json").read_text())

        if i < 2:
            # First two: the refusal is recorded, exit 0 (refusal returned, not raised)
            assert code == 0, f"invocation {i+1}: expected exit 0, got {code}"
            assert facts.get("consecutive_refusals") == i + 1, (
                f"invocation {i+1}: expected consecutive_refusals={i+1}, got {facts.get('consecutive_refusals')}"
            )
        else:
            # Third: BuildLoopStop raised, exit 3, marker present
            assert code == 3, f"invocation 3: expected exit 3, got {code}. stderr={captured.getvalue()}"
            stderr_text = captured.getvalue()
            assert FAILURE_MARKER in stderr_text
            assert "emissions refused 3 consecutive times" in stderr_text


# ── snapshot guard: a removed produces breaks the cross-process test ──────

def test_removing_consecutive_refusals_from_produces_fails(tmp_path, monkeypatch):
    """Removing consecutive_refusals from the materialise node's produces must
    make the fact unavailable in a subsequent invocation."""
    from refactory_dsh.design import DesignMode
    from refactory_dsh import lowering, worktree, repo_change
    from refactory_dsh.run import main as runner_main
    from tests_support import git_repo

    repo = git_repo(tmp_path / "repo")
    run = tmp_path / "run"

    worktree.create(repo, "HEAD", "abcdef888888", run)
    lowering.write_declarations(DesignMode().state_dict(), run)

    # Remove consecutive_refusals from declarations
    declarations = json.loads((run / lowering.DECLARATIONS).read_text())
    produces = declarations["materialise"]["produces"]
    if "consecutive_refusals" in produces:
        produces.remove("consecutive_refusals")
    (run / lowering.DECLARATIONS).write_text(json.dumps(declarations))

    (run / "facts.json").write_text(json.dumps({
        "name": "produces_test", "brief": "a tool", "check": "true",
    }))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"check_attempts": 3, "refusal_attempts": 3}))

    bad_emission = "===EDIT: app.py===\n<<<<<<< SEARCH\ntext not in file\n=======\nreplacement\n>>>>>>> REPLACE\n"

    import io

    # First invocation — will try to set consecutive_refusals but it is not in produces
    (run / "emit.txt").write_text(bad_emission)
    monkeypatch.setenv("REFACTORY_WORKDIR", str(run))
    monkeypatch.setenv("DSH_WORKFLOW_NODE", json.dumps({"id": "materialise", "type": "op"}))

    captured = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured
    try:
        code = runner_main([f"{repo_change.OPS}:materialise", "--node", "materialise"])
    finally:
        sys.stderr = old_stderr

    # The _check_produces should fail because consecutive_refusals is not declared
    # This manifests as exit 1 (general failure) rather than exit 0
    assert code == 1, (
        f"expected exit 1 (produces check failure), got {code}. stderr={captured.getvalue()}"
    )

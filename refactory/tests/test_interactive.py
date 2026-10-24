"""Running one mode with a person in the loop (level 3).

The outer loop searches a mode; this runs one for a person to *use*. What these
hold: the intent ledger is folded into the graph before the run (seeding it
afterwards would produce a run nobody steered), the command names the graph the
profile loads, and a run that stopped at a gate is a failure naming the gate
rather than a score of 0.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh import dsh_profile, dsh_runtime, interactive, registry, steering
from tests_support import _link_bundle, _profile

ROOT = Path(__file__).resolve().parent.parent.parent
#: A mode with agent nodes, so "the ledger reaches the agents" has something to reach.
WITH_AGENTS = "default_pack/create"
#: A mode with a gate a person answers and a deterministic gate.
WITH_A_GATE = "review_pack/review"


def test_prepare_seeds_the_ledger_then_folds_it_into_the_graph(tmp_path):
    """Seeded **before** the lowering: the lowering is what reaches the agents."""
    name, graphs = interactive.prepare(
        WITH_AGENTS, workdir=tmp_path / "run", intent="write a counter mode", project=ROOT
    )
    assert (tmp_path / "run" / "intent-ledger.md").exists()

    graph = json.loads((graphs / f"{name}.graph.json").read_text())
    specs = [json.loads(path.read_text()) for path in (tmp_path / 'run/.refactory/worker-specs').glob('*.json')]
    prompts = ' '.join(spec['prompt'] for spec in specs)
    assert "write a counter mode" in prompts, (
        "the graph the profile loads does not carry the intent, so nobody steered it"
    )


def test_prepare_leaves_the_mode_unchanged(tmp_path):
    """D15's property for the interactive path: the mode is the author's."""
    source = ROOT / "refactory" / "refactory_packages" / "review_pack"
    before = {
        path: path.read_bytes() for path in sorted(source.rglob("*.py"))
    }
    interactive.prepare(WITH_A_GATE, workdir=tmp_path / "run", intent="review this", project=ROOT)
    after = {path: path.read_bytes() for path in sorted(source.rglob("*.py"))}
    assert after == before, "lowering for a run rewrote the mode it lowered"


def test_the_command_names_the_graph_the_profile_loads(tmp_path):
    name, graphs = interactive.prepare(
        WITH_A_GATE, workdir=tmp_path / "run", intent="review this", project=ROOT
    )
    argv = interactive.command(WITH_A_GATE, workdir=tmp_path / "run", graphs=graphs, name=name)
    assert argv[argv.index("--mode") + 1] == name
    assert argv[argv.index("--graphs") + 1] == str(graphs)
    assert (graphs / f"{name}.graph.json").exists()


def test_the_command_hands_the_knobs_the_node_runner_can_read(tmp_path):
    """The flag is the knob JSON itself, not a JSON string containing it.

    The node runner accepts `--knobs` only when the value parses to an object; a
    double-encoded string leaves every node on its op's fallback default, so the
    run under DSH is not the candidate the loop scored on the walker.
    """
    name, graphs = interactive.prepare(
        WITH_A_GATE, workdir=tmp_path / "run", intent="review this", project=ROOT
    )
    argv = interactive.command(WITH_A_GATE, workdir=tmp_path / "run", graphs=graphs, name=name)
    knobs = json.loads(argv[argv.index("--knobs") + 1])
    assert isinstance(knobs, dict), f"--knobs must carry the values, got {knobs!r}"
    expected = registry.resolve(ROOT, WITH_A_GATE)[1]().state_dict()["knobs"]
    assert knobs == {key: entry["value"] for key, entry in expected.items()}


def test_a_run_that_refused_a_gate_is_a_failure_naming_it(tmp_path, monkeypatch):
    """The refusal must not read as a run that merely scored 0."""
    mode = registry.resolve(ROOT, WITH_A_GATE)[1]()
    work = tmp_path / "run"
    (work / ".refactory").mkdir(parents=True)
    (work / ".refactory" / "spine-status.json").write_text(
        json.dumps({"status": "refused", "error": "gate approve is answered by a person"})
    )

    def _fake_run(state, workdir, **kwargs):
        return dsh_runtime.DshRun(facts={"topic": "x"}, workdir=Path(workdir), returncode=0)

    monkeypatch.setattr(dsh_runtime, "run_on_dsh", _fake_run)
    result = dsh_runtime.evaluate_on_dsh(mode, mode.state_dict(), work)
    assert result.exit_ok is False
    assert "gate approve" in (result.error or ""), result.error
    assert "did not finish" in (result.error or "")


def test_a_run_the_spine_finished_is_scored(tmp_path, monkeypatch):
    """The other side: a `done` status scores normally."""
    mode = registry.resolve(ROOT, WITH_A_GATE)[1]()
    work = tmp_path / "run"
    (work / ".refactory").mkdir(parents=True)
    (work / ".refactory" / "spine-status.json").write_text(json.dumps({"status": "done"}))

    def _fake_run(state, workdir, **kwargs):
        return dsh_runtime.DshRun(
            facts={"published_chars": 42}, workdir=Path(workdir), returncode=0
        )

    monkeypatch.setattr(dsh_runtime, "run_on_dsh", _fake_run)
    result = dsh_runtime.evaluate_on_dsh(mode, mode.state_dict(), work)
    assert result.exit_ok is True, result.error
    assert result.score == 42.0


def test_the_work_directory_reaches_dsh_as_an_absolute_path(tmp_path):
    """The default root is relative, and DSH refuses a relative session cwd.

    `interactive <mode>` with no `--workdir` used to stop before the graph started
    with "session header cwd must be an absolute path", so the one command a person
    runs to *use* a mode did not work out of the box.
    """
    name, graphs = interactive.prepare(WITH_A_GATE, workdir=Path(".factory/probe-relative"))
    argv = interactive.command(WITH_A_GATE, workdir=Path(".factory/probe-relative"), graphs=graphs, name=name)
    workdir = Path(argv[argv.index("--workdir") + 1])
    assert workdir.is_absolute(), f"DSH was handed the relative workdir {workdir}"
    assert Path(argv[argv.index("--graphs") + 1]).is_absolute()


def test_headless_is_exactly_todays_argv_and_the_default_chats(tmp_path):
    """`--headless` promises a script the argv it got before.

    So it must *leave the chat flag out*, not set it to false, and the live run's
    argv must be today's plus that one flag: nothing else may drift between them.
    """
    name, graphs = interactive.prepare(
        WITH_A_GATE, workdir=tmp_path / "run", intent="review this", project=ROOT
    )
    live = interactive.command(WITH_A_GATE, workdir=tmp_path / "run", graphs=graphs, name=name)
    headless = interactive.command(
        WITH_A_GATE, workdir=tmp_path / "run", graphs=graphs, name=name, headless=True
    )
    assert "--chat" in live, "the default run is not a live session"
    assert "--chat" not in headless, "a headless run asked DSH for a conversation"
    assert live == [*headless, "--chat"], "the only difference must be the chat flag"


def test_main_forwards_headless_to_the_run(monkeypatch):
    """The switch reaches the run, and its absence means live."""
    seen: list[dict] = []
    monkeypatch.setattr(interactive, "run", lambda *a, **k: seen.append(k) or 0)
    assert interactive.main([WITH_A_GATE, "--headless"]) == 0
    assert seen[-1]["headless"] is True
    assert interactive.main([WITH_A_GATE]) == 0
    assert seen[-1]["headless"] is False


def test_the_graph_argv_names_the_route_the_context_resolved(monkeypatch, tmp_path):
    """The CEO's own argv must carry the route the resolved context selected.

    The fresh-run path resolved a project file's provider into ``model_context``
    but built the graph argv from the untouched CLI flags, so the CEO ran the
    runtime's default provider while every node dispatch honored the project's
    selection: a project routed to a litellm gateway silently billed the
    runtime default's account.
    """
    from refactory_dsh import model as model_module
    from refactory_dsh import workers

    # The pre-flight profile check in interactive.run() needs a valid graph
    # profile.  Set one up so the test reaches the monkeypatched worker.
    home = tmp_path / "dsh-home"
    _profile(home, "graph", ["@deepseek-ai/dsh-base", dsh_profile.GRAPH_BUNDLE])
    _link_bundle(home, "graph", dsh_profile.GRAPH_BUNDLE,
                 ROOT / "packages" / "bundle" / "graph")
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.delenv("REFACTORY_MODEL_DSH_HOME", raising=False)

    class RoutedClient:
        def __init__(self, **kwargs):
            self._given = kwargs

        def context(self):
            return {**self._resolved, "budget_ledger": ""}

        #: What a project file's ``refactory.workers.json`` would resolve to.
        _resolved = {
            "routes_resolved": True, "routing_provenance": {},
            "dsh_home": str(tmp_path), "provider": "gateway", "model": "gateway-model",
            "timeout_s": 1800, "attempts": 3, "backoff_s": 1.0,
            "runtime_root": str(ROOT), "dsh_cli": "", "python_bin": str(ROOT / ".venv/bin"),
            "worker_backend": "dsh", "effort": "", "permission_mode": "skip",
            "role_routes": {}, "node_routes": {}, "provider_config": {},
        }

    monkeypatch.setattr(model_module, "DshModelClient", RoutedClient)
    seen: list[list[str]] = []

    class _Run:
        returncode = 0

    def _capture(argv, **kwargs):
        seen.append(argv)
        return _Run()

    monkeypatch.setattr(workers, "run_graph_command", _capture)
    interactive.run(WITH_A_GATE, workdir=tmp_path / "run", project=ROOT, headless=True)
    argv = seen[-1]
    assert argv[argv.index("--provider") + 1] == "gateway"
    assert argv[argv.index("--model") + 1] == "gateway-model"


#: What `target_guard` prints when the run's target has changes the run leaves out.
UNCOMMITTED = ("refactory: run warning: 2 uncommitted change(s) in /work/repo are not "
               "part of this run")


def _journal(workdir: Path, *records: dict) -> None:
    """Append records to the run's journal, as the spine and the console write it."""
    path = workdir / ".refactory" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        for record in records:
            stream.write(json.dumps({"schema_version": 1, "run_id": str(workdir), **record}) + "\n")


def test_run_warnings_lists_what_the_steps_warned_and_nothing_else(tmp_path):
    _journal(
        tmp_path,
        {"kind": "graph_state", "status": "ready", "nodes": ["prepare"]},
        {"kind": "run_warning", "node_id": "prepare", "state": "warning", "summary": UNCOMMITTED},
        {"kind": "control", "state": "applied", "summary": "Stopped at graph boundary"},
        {"kind": "run_warning", "node_id": "materialise", "state": "warning",
         "summary": "refactory: run warning: tracked files under /work/repo were put back"},
    )
    assert interactive.run_warnings(tmp_path) == "\n".join([
        "refactory: what the run warned about —",
        f"  prepare: {UNCOMMITTED}",
        "  materialise: refactory: run warning: tracked files under /work/repo were put back",
    ])


def test_run_warnings_lists_each_warning_once_however_often_a_reloop_repeated_it(tmp_path):
    warning = {"kind": "run_warning", "node_id": "check", "state": "warning", "summary": UNCOMMITTED}
    _journal(tmp_path, warning, warning, warning)
    assert interactive.run_warnings(tmp_path) == "\n".join(["refactory: what the run warned about —",
                                                           f"  check: {UNCOMMITTED}"])


def test_run_warnings_says_nothing_when_no_step_warned(tmp_path):
    assert interactive.run_warnings(tmp_path) == ""
    _journal(tmp_path, {"kind": "graph_state", "status": "done", "nodes": []})
    assert interactive.run_warnings(tmp_path) == ""


def test_headless_prints_gate_commands_when_stopped_at_user_gate(monkeypatch, tmp_path, capsys):
    """In headless mode, when a run stops at a user gate, print the exact
    steer --verdict and graph-control resume commands once, from interactive.py.
    """
    from refactory_dsh import workers

    # The pre-flight profile check in interactive.run() needs a valid graph
    # profile.  Set one up so the test reaches the monkeypatched worker.
    home = tmp_path / "dsh-home"
    _profile(home, "graph", ["@deepseek-ai/dsh-base", dsh_profile.GRAPH_BUNDLE])
    _link_bundle(home, "graph", dsh_profile.GRAPH_BUNDLE,
                 ROOT / "packages" / "bundle" / "graph")
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.delenv("REFACTORY_MODEL_DSH_HOME", raising=False)

    workdir = tmp_path / "run"

    class _Run:
        returncode = 1

    def _graph(argv, **kwargs):
        # Write spine-status showing refused at the user gate
        (workdir / ".refactory").mkdir(parents=True, exist_ok=True)
        (workdir / ".refactory" / "spine-status.json").write_text(
            json.dumps({"status": "refused", "nodes": ["approve"],
                        "error": "gate approve is answered by a person"})
        )
        return _Run()

    monkeypatch.setattr(workers, "run_graph_command", _graph)
    code = interactive.run(
        WITH_A_GATE, workdir=workdir, project=ROOT, headless=True,
    )
    assert code == 1
    captured = capsys.readouterr()
    assert "approve is waiting for a person" in captured.err
    assert f"refactory steer --run {workdir} --verdict approve=" in captured.err
    assert f"refactory graph-control resume --run {workdir}" in captured.err
    assert "proceed" in captured.err or "halt" in captured.err


def test_note_recorded_as_correction_with_reloop(tmp_path):
    """A reloop verdict with a note records the note as a `correction` entry."""
    ledger = steering.Ledger.seed(tmp_path, "test intent")
    # Simulate what steering.main() does for an answer with feedback on reloop
    gate = "approve"
    reads = ["draft.md"]
    review = ledger.visit(gate, reads)
    ledger.verdict(gate, "reloop", review=review, answered=True)
    ledger.note("correction", "Please fix the approach")
    entries = ledger.entries()
    corrections = [e for e in entries if e["kind"] == "correction"]
    assert len(corrections) == 1
    assert "Please fix the approach" in corrections[0]["text"]


def test_note_recorded_as_constraint_with_proceed(tmp_path):
    """A proceed verdict with a note records the note as a `constraint` entry."""
    ledger = steering.Ledger.seed(tmp_path, "test intent")
    gate = "approve"
    reads = ["draft.md"]
    review = ledger.visit(gate, reads)
    ledger.verdict(gate, "proceed", review=review, answered=True)
    ledger.note("constraint", "Must keep backward compatibility")
    entries = ledger.entries()
    constraints = [e for e in entries if e["kind"] == "constraint"]
    assert len(constraints) == 1
    assert "Must keep backward compatibility" in constraints[0]["text"]


def test_brief_echo_shortened_to_first_line(monkeypatch, capsys):
    """A multi-line brief prints only its first line, truncated."""
    from refactory_dsh import cli

    long_brief = "First line of the brief\nSecond paragraph with details\nThird paragraph"
    # Mock _forward to avoid actually running anything
    monkeypatch.setattr(cli, "_forward", lambda *a, **k: 0)
    assert cli.main(["ceo", long_brief, "--mode", "design", "--check", "echo ok"]) == 0
    captured = capsys.readouterr()
    assert "First line of the brief" in captured.err
    assert "Second paragraph" not in captured.err
    assert "Third paragraph" not in captured.err


# ── A4: research scope ─────────────────────────────────────────────────


def test_prepare_design_sets_scoped_for_refine_task(tmp_path, monkeypatch):
    """prepare_design with task.refine truthy → research_scope='scoped'."""
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
    task = {"name": "test_artifact", "brief": "Make the widget better",
            "check": "python -m pytest -q",
            "refine": "https://github.com/example/repo/pull/1"}
    ctx = nn.RunContext(workdir, task=task)
    result = design_ops.prepare_design(ctx, deliver=True, in_worktree=False)
    assert result["research_scope"] == "scoped"
    assert result["research_scope_reason"] == "refinement of an open pull request"


def test_prepare_design_sets_scoped_for_refine_without_file_in_brief(tmp_path, monkeypatch):
    """A refine run whose brief names no file still gets research_scope 'scoped'."""
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
    task = {"name": "polish_ui", "brief": "Make the spacing more consistent",
            "check": "python -m pytest -q",
            "refine": "https://github.com/example/repo/pull/42"}
    ctx = nn.RunContext(workdir, task=task)
    result = design_ops.prepare_design(ctx, deliver=True, in_worktree=False)
    assert result["research_scope"] == "scoped"
    assert result["research_scope_reason"] == "refinement of an open pull request"


def test_prepare_design_sets_full_even_when_brief_names_a_file(
        tmp_path, monkeypatch):
    """A brief naming worker_observer.mjs → research_scope='full' (no file-ext heuristic)."""
    from refactory_dsh import design_ops, nn, repo_change, sandbox
    monkeypatch.setattr(repo_change, "wants_worktree", lambda ctx: False)
    monkeypatch.setattr(repo_change, "locate",
                        lambda ctx, name, task, **kw: {
                            "source": str(tmp_path), "check_root": str(tmp_path),
                            "artifact": str(tmp_path), "branch": "", "change": "",
                        })
    monkeypatch.setattr(sandbox, "model_readiness", lambda *a, **kw: {})
    workdir = tmp_path / "run"
    task = {"name": "fix_observer",
            "brief": "Fix worker_observer.mjs to emit tokens_delta",
            "check": "python -m pytest -q"}
    ctx = nn.RunContext(workdir, task=task)
    result = design_ops.prepare_design(ctx, deliver=False)
    assert result["research_scope"] == "full"
    assert result["research_scope_reason"] == ""


def test_prepare_design_sets_full_for_plain_prose(tmp_path, monkeypatch):
    """'Make the UI better.' — non-refine → 'full'."""
    from refactory_dsh import design_ops, nn, repo_change, sandbox
    monkeypatch.setattr(repo_change, "wants_worktree", lambda ctx: False)
    monkeypatch.setattr(repo_change, "locate",
                        lambda ctx, name, task, **kw: {
                            "source": str(tmp_path), "check_root": str(tmp_path),
                            "artifact": str(tmp_path), "branch": "", "change": "",
                        })
    monkeypatch.setattr(sandbox, "model_readiness", lambda *a, **kw: {})
    workdir = tmp_path / "run"
    task = {"name": "ui_improvement",
            "brief": "Make the UI better.",
            "check": "python -m pytest -q"}
    ctx = nn.RunContext(workdir, task=task)
    result = design_ops.prepare_design(ctx, deliver=False)
    assert result["research_scope"] == "full"
    assert result["research_scope_reason"] == ""


def _gate_ctx(tmp_path, *, research_scope, direction_count):
    """A context for gate_research with the specified scope and N directions."""
    from types import SimpleNamespace
    workdir = tmp_path / "run"
    workdir.mkdir(exist_ok=True)
    entries = [
        {"focus": f"direction {i+1}", "slug": f"dir-{i+1}",
         "finding": f"Finding {i+1}"}
        for i in range(direction_count)
    ]
    (workdir / "research-plan.json").write_text(json.dumps(entries))
    research_dir = workdir / "research"
    research_dir.mkdir(exist_ok=True)
    for entry in entries:
        (research_dir / f"{entry['slug']}.md").write_text(
            f"### FND-{entry['slug']}-1 \u2014 {entry['finding']}\n\n"
            f"# {entry['focus']}\n{entry['finding']}\n")
    facts = {"research_scope": research_scope, "research_attempts_used": 0}
    return SimpleNamespace(
        workdir=workdir,
        knob=lambda name, default="": {"research_attempts": 3}.get(name, default),
        read_text=lambda name: (workdir / name).read_text()
            if (workdir / name).exists() else "",
        exists=lambda name: (workdir / name).exists(),
        write_text=lambda name, text: (workdir / name).write_text(text),
        set_fact=lambda name, value: facts.update({name: value}),
        facts=facts,
    )


def test_gate_research_accepts_two_directions_when_scoped(tmp_path):
    """gate_research with research_scope='scoped' and 2 directions → proceed."""
    from refactory_dsh import create_ops
    ctx = _gate_ctx(tmp_path, research_scope="scoped", direction_count=2)
    verdict = create_ops.gate_research(ctx)
    assert verdict == "proceed"


def test_gate_research_refuses_two_directions_when_full(tmp_path):
    """gate_research with research_scope='full' and 2 directions → refusal."""
    from refactory_dsh import create_ops
    from refactory_dsh.nn import Verdict
    ctx = _gate_ctx(tmp_path, research_scope="full", direction_count=2)
    verdict = create_ops.gate_research(ctx)
    assert isinstance(verdict, Verdict)
    assert "three to five" in str(verdict.reason)


def test_gate_research_refuses_zero_directions_when_scoped(tmp_path):
    """gate_research with research_scope='scoped' and 0 entries → refusal."""
    from refactory_dsh import create_ops
    from refactory_dsh.nn import Verdict
    ctx = _gate_ctx(tmp_path, research_scope="scoped", direction_count=0)
    verdict = create_ops.gate_research(ctx)
    # 0 entries means _plan() returned empty list, so the error is the JSON array check
    assert isinstance(verdict, Verdict)


def test_gate_research_reads_facts_not_knobs(tmp_path):
    """gate_research reads ctx.facts, NOT ctx.knob('research_scope', ...).

    The _gate_ctx does NOT set research_scope as a knob. If gate_research
    called ctx.knob('research_scope', 'full'), it would get 'full' (the
    dict does not contain it), and a 2-direction scoped plan would be refused.
    """
    from refactory_dsh import create_ops
    ctx = _gate_ctx(tmp_path, research_scope="scoped", direction_count=2)
    verdict = create_ops.gate_research(ctx)
    assert verdict == "proceed"


def test_interactive_run_refuses_when_the_profile_links_to_another_checkout(
    tmp_path, monkeypatch
):
    """The interactive path refuses before spawning when the profile is mismatched."""
    from refactory_dsh import workers

    home = tmp_path / "home"
    # Create graph profile with bundle declared but symlink to another checkout.
    _profile(home, "graph", ["@deepseek-ai/dsh-base", dsh_profile.GRAPH_BUNDLE])
    other = tmp_path / "other" / "packages" / "bundle" / "graph"
    _link_bundle(home, "graph", dsh_profile.GRAPH_BUNDLE, other)
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.delenv("REFACTORY_MODEL_DSH_HOME", raising=False)

    def _no_spawn(argv, **kwargs):
        raise AssertionError("DSH was spawned with a mismatched profile")

    monkeypatch.setattr(workers, "run_graph_command", _no_spawn)
    with pytest.raises(dsh_profile.ProfileError, match="other") as exc_info:
        interactive.run(
            WITH_A_GATE, workdir=tmp_path / "run", project=ROOT, headless=True,
        )
    assert dsh_profile.add_command() in str(exc_info.value)
    assert not (tmp_path / "run").exists(), "a refused start must leave no run directory behind"


@pytest.mark.parametrize("returncode", [0, 1])
def test_a_cli_run_lists_its_warnings_when_it_ends(monkeypatch, tmp_path, capsys, returncode):
    """`refactory ceo` runs the configured graph, where nobody is made to read `ran`.

    The CEO is told to pass a warning on, but a headless run's turns go unread, so
    the run itself lists what its steps warned about, whether or not it succeeded.
    """
    from refactory_dsh import workers

    # The pre-flight profile check in interactive.run() needs a valid graph
    # profile.  Set one up so the test reaches the monkeypatched worker.
    home = tmp_path / "dsh-home"
    _profile(home, "graph", ["@deepseek-ai/dsh-base", dsh_profile.GRAPH_BUNDLE])
    _link_bundle(home, "graph", dsh_profile.GRAPH_BUNDLE,
                 ROOT / "packages" / "bundle" / "graph")
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.delenv("REFACTORY_MODEL_DSH_HOME", raising=False)

    workdir = tmp_path / "run"

    class _Run:
        pass

    def _graph(argv, **kwargs):
        # What the spine journals when a step it ran printed a run warning.
        _journal(workdir, {"kind": "run_warning", "node_id": "prepare", "state": "warning",
                           "summary": UNCOMMITTED})
        ran = _Run()
        ran.returncode = returncode
        return ran

    monkeypatch.setattr(workers, "run_graph_command", _graph)
    assert interactive.run(WITH_A_GATE, workdir=workdir, project=ROOT, headless=True) == returncode
    assert f"refactory: what the run warned about —\n  prepare: {UNCOMMITTED}\n" in capsys.readouterr().err

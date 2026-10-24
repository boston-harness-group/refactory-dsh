"""The operations design-v2's graph calls.

`create` emits a **package of modes**: a manifest, a `Mode` subclass, `score()`.
`design` emits **software**: no manifest, no `score()`, no graph. So the validator
and the check differ, and so does where the output lands.

Where the software lands, and how a check runs there, is `repo_change`'s: design uses
the same steps as any mode that changes a repository. What is design's own is here: its
brief, how its work is delivered (`delivery`), its check gate's evidence and repairs, and
its report.
"""

from __future__ import annotations

import json
import re as _re
from pathlib import Path
from typing import Any

from refactory_dsh import delivery, repo_change, sandbox, unbuilt_check, worktree
from refactory_dsh.invocations import CHECK_PREP, CHECK_VERDICT
from refactory_dsh.nn import Verdict

#: The reason prepare_design writes when the run is a refinement.  classify_scale
#: recognises a refine by this constant rather than reading research_scope (which
#: classify_scale itself writes), so a rerun reclassifies from the footprint.
REFINE_SCOPE_REASON = "refinement of an open pull request"

STAGING = "staging"

#: Planning steps that must not change the target or the worktree (#347).
#: They write only into the run directory; a change they make to the target
#: or the worktree fails the step and names what changed.
PLANNING_STEPS = ('research_director', 'pitfalls_researcher', 'strategy_director', 'synthesize_strategy', 'design_doc')

#: The op design's materialise node called before it moved to `repo_change` (#330): a run
#: lowered then, such as one waiting at its approval gate, still names it.
materialise_artifact = repo_change.materialise


def noop(ctx: Any) -> dict[str, Any]:
    """Terminal node: does nothing, returns an empty dict."""
    return {}


def pick_gate_corrections(ctx: Any) -> str:
    """Gate evaluator: extract gate_strategy corrections from the intent ledger.

    Returns ``"proceed"`` when corrections exist (route to apply_corrections),
    or ``"skip"`` when there are none (route directly to emit, skipping the
    agent entirely).

    Reads the ledger only when it exists; a missing ledger means "no corrections".
    Only correction or constraint entries with product scope and user-relayed
    source, between a gate_strategy proceed verdict and the next gate-visit or
    verdict-use, are gate corrections.
    """
    from refactory_dsh.steering import LEDGER, parse_entries

    ledger_path = Path(ctx.workdir) / LEDGER
    if not ledger_path.exists():
        ctx.set_fact("gate_corrections", "")
        ctx.set_fact("design_corrections_applied", "0 corrections (no ledger)")
        return "skip"

    text = ledger_path.read_text()
    entries = parse_entries(text)
    corrections: list[str] = []

    collecting = False
    for entry in entries:
        kind = entry.get("kind", "")

        # A verdict for gate_strategy=proceed starts collecting;
        # any other verdict resets.
        if kind == "verdict":
            gate_match = _re.search(r'^gate:\s*(.+)$', entry["text"], _re.MULTILINE)
            verdict_match = _re.search(r'^verdict:\s*(.+)$', entry["text"], _re.MULTILINE)
            if gate_match and verdict_match:
                gate = gate_match.group(1).strip()
                verdict = verdict_match.group(1).strip().lower()
                collecting = (gate == "gate_strategy" and verdict == "proceed")
            continue

        # A gate-visit or verdict-use stops collecting.
        if kind in ("gate-visit", "verdict-use"):
            collecting = False
            continue

        # While collecting, pick up corrections/constraints with product scope
        # and user-relayed source.
        if collecting and kind in ("correction", "constraint"):
            if entry.get("scope") == "product" and entry.get("source") == "user-relayed":
                corrections.append(entry["text"])

    if not corrections:
        ctx.set_fact("gate_corrections", "")
        ctx.set_fact("design_corrections_applied", "0 corrections")
        return "skip"

    joined = "\n\n".join(f"- {c}" for c in corrections)
    ctx.set_fact("gate_corrections", joined)
    ctx.set_fact("design_corrections_applied", f"{len(corrections)} correction(s)")

    # Back up design.md before corrections are applied (gate 5c).
    design_path = Path(ctx.workdir) / "design.md"
    if design_path.exists():
        (Path(ctx.workdir) / "design.md.pre_corrections").write_text(design_path.read_text())

    return "proceed"


def prepare_design(ctx: Any, *, deliver: bool = True, in_worktree: bool | None = None) -> dict[str, Any]:
    """Read the brief, and what the brief says counts as done.

    Where the run works -- a worktree of its target unless its ``worktree`` knob is off,
    or ``in_worktree`` says otherwise -- is `repo_change.locate`'s. With ``deliver``, how
    its work is delivered is planned here (`delivery.plan_run`), for the approval gate to
    show. design_fast passes ``deliver=False``: it has no approval gate to show one at,
    so it delivers nothing and refuses ``refine``.
    """
    task = dict(getattr(ctx, "task", {}) or {})
    # The walker passes these in the run's task; a run on DSH carries them as knobs.
    name = str(task.get("name") or ctx.knob("name", "") or "").strip()
    brief = str(task.get("brief") or ctx.knob("brief", "") or "").strip()
    check = str(task.get("check") or ctx.knob("check", "") or "").strip()
    from refactory_dsh.brief_guard import require_brief
    require_brief(brief, source="brief")
    if not name and brief:
        from refactory_dsh.create_ops import _slug

        name = _slug(brief)
    if not name:
        raise ValueError(
            "design-mode needs what to build: pass {'name': ..., 'brief': ..., 'check': ...} "
            "in the task (the walker) or the `name`/`brief`/`check` knobs (a run on DSH)"
        )
    if not check:
        raise ValueError(
            "design-mode needs a check: the command that decides whether the artifact "
            "works, e.g. {'check': 'python -m pytest -q'}"
        )
    if in_worktree is None:
        in_worktree = repo_change.wants_worktree(ctx)
    if not deliver and str(task.get("refine") or "").strip():
        raise ValueError("refine follows up a pull request, and design_fast delivers nothing, so it cannot "
                         "update one; follow it up with design instead")
    if deliver and in_worktree:
        # A refinement starts from its pull request's head, in a clone of its repository.
        task = delivery.refine_task(ctx, task)
    located = repo_change.locate(ctx, name, task, in_worktree=in_worktree,
                                 needs_worktree=("refine",) if deliver else (),
                                 output_only=PLANNING_STEPS)

    # Compute research_scope.  A refine is always scoped; everything else starts
    # as full and is reclassified by classify_scale after research.
    if str(task.get("refine") or "").strip():
        research_scope = "scoped"
        research_scope_reason = REFINE_SCOPE_REASON
    else:
        research_scope = "full"
        research_scope_reason = ""

    # Match create/edit: inspect the exact nested route before launching research.
    # Other ModelClient implementations retain their own readiness contract.
    from refactory_dsh.model import DshModelClient

    client = getattr(ctx, "model", None)
    if client is None or isinstance(client, DshModelClient):
        readiness = sandbox.model_readiness(
            ctx.workdir / ".model-readiness",
            model_context=client.context() if isinstance(client, DshModelClient) else None,
        )
        if readiness.get("warning"):
            import sys

            print(f"refactory: warning: {readiness['warning']}", file=sys.stderr)
    ctx.write_json("brief.json", {"name": name, "brief": brief, "check": check})
    # Write initial check-record.json so emit finds its input
    ctx.write_json("check-record.json", {"status": "not_run", "validation": "(no check has run yet)"})
    for feedback in ("research-review.txt", "check.txt"):
        ctx.write_text(feedback, "not run: initial authoring pass; no prior feedback\n")
    ctx.write_text("reviews/design-review-feedback.txt",
                   "not run: initial design pass; no prior review\n")
    from refactory_dsh.create_ops import UNREFUSED

    # Pitfalls facts and file are written only by full design mode (whose
    # prepare node declares them); design_fast calls with deliver=False and
    # filters these keys out before returning to the executor.
    if deliver:
        ctx.write_text("pitfalls-review.txt",
                       "not run: initial pitfalls analysis; no prior review\n")
    planned = delivery.plan_run(ctx, task=task) if deliver else {}
    pitfalls_facts = {
        "has_pitfalls_step": True,
        "pitfalls": "(no pitfalls analysis yet)",
        "pitfalls_review": "(no pitfalls review has run yet)",
    } if deliver else {}
    # Write feedback-initial.json so a redo before QA re-fills
    feedback_initial = {
        "validation": "(no check has run yet)",
        **UNREFUSED,
    }
    ctx.write_json("feedback-initial.json", feedback_initial)
    # ── deliverable_kind: repository change vs artifact ──
    has_target = bool(str(task.get("target") or ctx.knob("target", "") or "").strip())
    has_refine = bool(str(task.get("refine") or ctx.knob("refine", "") or "").strip())
    has_source_project = bool(str(task.get("source_project") or ctx.knob("source_project", "") or "").strip())
    deliverable_kind = "repository_change" if (has_target or has_refine or has_source_project) else "artifact"
    from refactory_dsh.authoring_evidence import (
        REPO_CHANGE_REVIEW_INSTRUCTIONS,
        ARTIFACT_REVIEW_INSTRUCTIONS,
    )
    review_instructions = (
        REPO_CHANGE_REVIEW_INSTRUCTIONS if deliverable_kind == "repository_change"
        else ARTIFACT_REVIEW_INSTRUCTIONS
    )
    from refactory_dsh.verification import verification_methods

    claims_facts = {
        "claims_check_feedback": "",
        "claims_check_attempts_used": 0,
        "design_review_status": "",
    } if deliver else {}
    return {"name": name, "brief": brief, "check": check, "validation": "(no check has run yet)",
            "research_review": "(no research review has run yet)",
            "research_scope": research_scope, "research_scope_reason": research_scope_reason,
            "design_review_feedback": "(no design review has run yet)",
            "design_review_findings": "(no findings yet)",
            "deliverable_kind": deliverable_kind,
            "review_instructions": review_instructions,
            "verification_methods": verification_methods(deliverable_kind),
            **pitfalls_facts,
            **claims_facts,
            **UNREFUSED, **located, **planned}


#: What a scoped change's plan is compiled from, in place of the perspectives step.
SCOPED_STRATEGY_PLAN = (
    "(no perspectives: this is a scoped change, so plan directly from the research and "
    "the pitfalls — the simplest thing that could pass the check — and say what the "
    "check will not catch)"
)


def route_strategy(ctx: Any) -> Any:
    """Send a scoped change straight to the plan; a full design weighs perspectives first.

    The perspectives step chooses between ways to build the artifact. A scoped change
    has one sensible way, and one perspective summarised by the plan step is the same
    work done twice.
    """
    from refactory_dsh.nn import Verdict

    if ctx.facts.get("research_scope") == "scoped":
        ctx.set_fact("strategy_plan", SCOPED_STRATEGY_PLAN)
        # What the perspectives step would leave, empty: the plan and design steps
        # declare them as inputs, and the spine refuses a declared input that is not
        # on disk.
        ctx.write_text("strategy-plan.json", "[]\n")
        ctx.path("strategy").mkdir(parents=True, exist_ok=True)
        return Verdict("scoped", "a scoped change is planned straight from the research")
    # `proceed` rather than a label of its own: a person's skip of this gate sends
    # proceed on the spine, and the full path is the one that loses nothing.
    return Verdict("proceed", "a full design weighs perspectives before the plan")


def route_design_review(ctx: Any) -> Any:
    """Send a scoped change's design to the automatic checks; a full design is reviewed.

    For a scoped change the design-review gate's own checks (an Ask mapping present,
    citations and quotes true, high-risk pitfalls answered, required sections present)
    and the person's approval are the review; the review agent adds a round to a change
    whose design is a page long. The gate learns which from `design_review_by`, so a
    run whose graph predates this gate keeps its agent's review.
    """
    from refactory_dsh.nn import Verdict

    if ctx.facts.get("research_scope") == "scoped":
        ctx.set_fact("design_review_by", "automatic")
        return Verdict("scoped", "a scoped change's design is checked automatically")
    ctx.set_fact("design_review_by", "agent")
    return Verdict("proceed", "a full design is reviewed by the design-review agent")


def gate_design_review(ctx: Any) -> str:
    """Proceed when the design review passes and the mechanical checks hold.

    Never raises ValueError: when attempts are exhausted, it proceeds to
    gate_strategy with the remaining findings in the design_review_findings
    fact, so the person decides. A design `route_design_review` sent here without the
    review agent (`design_review_by` is ``automatic``) has only the mechanical checks;
    any review file is not its review.
    """
    from refactory_dsh.create_ops import PASS_MARK, blocking_findings
    from refactory_dsh.nn import Verdict

    scoped = ctx.facts.get("design_review_by") == "automatic"
    # ── read the agent's review ──
    review_text = ctx.read_text("reviews/design-review.md") if ctx.exists(
        "reviews/design-review.md") and not scoped else ""
    problems: list[str] = []
    # What the gate notices that does not change what gets built. It is shown to the
    # person and never costs a round: a design goes back only for a problem in `problems`.
    advisories: list[str] = []

    # Verdict and blocking findings, same parsing as gate_overwatch. A scoped change's
    # design had no review agent, so any review file is not its review.
    if scoped:
        pass
    elif review_text.strip():
        verdict_line = review_text.strip().splitlines()[-1].strip()
        problems.extend(blocking_findings(review_text))
        if verdict_line != PASS_MARK:
            problems.append(f"the review did not state {PASS_MARK!r}; "
                            f"its last line is: {verdict_line!r}")
    else:
        problems.append("reviews/design-review.md is empty or missing")

    # ── mechanical check 1: cited file:line references exist ──
    design_text = ctx.read_text("design.md") if ctx.exists("design.md") else ""
    source = str(ctx.facts.get("source") or ctx.facts.get("artifact") or "")

    # Build an index of tracked files for bare-name resolution.
    tracked_files: list[str] = []
    if source:
        try:
            output = worktree.git(Path(source), "ls-files")
            tracked_files = [f for f in output.splitlines() if f.strip()]
        except ValueError:
            pass

    def _resolve_citation(file_path: str) -> Path | None:
        """Resolve a citation path: try direct, then match as a suffix of tracked files."""
        full = Path(source) / file_path
        if full.is_file():
            return full
        # Bare or partial path: find a tracked file whose path ends with it.
        matches = []
        for tracked in tracked_files:
            if tracked == file_path or tracked.endswith("/" + file_path):
                candidate = Path(source) / tracked
                if candidate.is_file():
                    matches.append(candidate)
        return matches[0] if len(matches) == 1 else None

    if design_text and source:
        # Match path.ext:N or path.ext:N-M  (the format design_doc is told to use)
        for match in _re.finditer(
            r'(?<!\w)([A-Za-z_][\w/._-]*\.\w+):(\d+)(?:-(\d+))?(?!\w)',
            design_text
        ):
            # Skip URLs: a "://" in the 10 chars before the match means this is
            # a hostname:port, not a file:line citation.
            prefix = design_text[max(0, match.start() - 10):match.start()]
            if '://' in prefix:
                continue
            file_path = match.group(1)
            start_s, end_s = match.group(2), match.group(3)
            line_num = int(end_s) if end_s else int(start_s)
            resolved = _resolve_citation(file_path)
            if resolved is None:
                continue
            else:
                actual_lines = resolved.read_text().splitlines()
                if line_num > len(actual_lines):
                    problems.append(
                        f"citation {match.group(0)!r}: {file_path} has "
                        f"{len(actual_lines)} lines, not {line_num}"
                    )

    # ── mechanical check 2: brief quotations are verbatim ──
    if design_text:
        brief_text = ""
        if ctx.exists("brief.json"):
            try:
                brief_data = json.loads(ctx.read_text("brief.json"))
                brief_text = str(brief_data.get("brief", ""))
            except (json.JSONDecodeError, AttributeError):
                pass
        if brief_text:
            # Collapse whitespace for comparison so wrapped quotes still match.
            brief_collapsed = _re.sub(r'\s+', ' ', brief_text).strip()
            for match in _re.finditer(
                r'(?i)(?:\bthe brief\b\s+(?:says|asks|requires|reads)[,:]?\s*'
                r'|\bthe brief:\s*)'
                r'"([^"]{4,})"',
                design_text,
            ):
                quoted = match.group(1)
                quoted_collapsed = _re.sub(r'\s+', ' ', quoted).strip()
                if quoted_collapsed not in brief_collapsed:
                    # Differing only in punctuation, case, spacing or markdown is a
                    # quoting slip, not a claim the brief does not make.
                    quoted_words = _quote_words(quoted)
                    if quoted_words and f" {quoted_words} " in f" {_quote_words(brief_text)} ":
                        advisories.append(
                            f"the design's quote {quoted!r} differs from the brief only "
                            f"in punctuation or formatting"
                        )
                    else:
                        problems.append(
                            f"the design attributes {quoted!r} to the brief, but "
                            f"that text does not appear verbatim in the brief"
                        )

    # ── mechanical check 3: design-document contract ──
    from refactory_dsh import design_contract

    if design_text:
        run_name = str(ctx.facts.get("name") or "")
        parsed = design_contract.parse(design_text, run_name)
        missing = design_contract.missing_parts(parsed)
        if missing:
            problems.append(
                f"the design is missing required section(s): {', '.join(missing)}"
            )
        left_no_reason = design_contract.left_without_reason(parsed)
        if left_no_reason:
            advisories.append(
                "rule-site(s) marked [left] without a reason: "
                + ", ".join(left_no_reason)
            )
        unmarked = design_contract.unmarked_sites(parsed)
        if unmarked:
            # Check for near-miss markers on each unmarked site.
            parts: list[str] = []
            for site in parsed.rule_sites:
                if site["status"] != "unmarked":
                    continue
                near = design_contract.find_near_miss_markers(site["path"])
                if near:
                    parts.append(f"{site['path']} ({'; '.join(near)})")
                else:
                    parts.append(site["path"])
            advisories.append(
                "rule-site(s) with no [changed] or [left: <reason>] marker: "
                + ", ".join(parts)
            )
        if design_contract.title_is_run_name(design_text, run_name):
            advisories.append(
                "the design's title is the run name; it must say what the change does"
            )

    # ── mechanical check 3a: failure trace section ──
    if design_text:
        if not design_contract.has_non_empty_section(design_text, design_contract.FAILURE_TRACE):
            advisories.append(
                "the design is missing the required ## Failure trace section (or it is empty)"
            )

    # ── mechanical check 3b: ask mapping section ──
    if design_text:
        if not design_contract.has_non_empty_section(design_text, design_contract.ASK_MAPPING):
            problems.append(
                "the design is missing the required ## Ask mapping table (or it is empty)"
            )

    # ── mechanical check 4: pitfall coverage ──
    has_pitfalls = ctx.facts.get("has_pitfalls_step", False)
    if has_pitfalls:
        from refactory_dsh import pitfalls as pitfalls_mod

        if ctx.exists("pitfalls.json"):
            try:
                pitfalls_raw = ctx.read_text("pitfalls.json")
                pitfalls_data = json.loads(pitfalls_raw)
                if isinstance(pitfalls_data, list):
                    addressed, aside, pit_missing = pitfalls_mod.pitfall_coverage(
                        design_text, pitfalls_data)
                    missing_high = [pid for pid in pit_missing
                                   if any(p.get("id") == pid and p.get("risk") == "high"
                                          for p in pitfalls_data)]
                    if missing_high:
                        problems.append(
                            f"high-risk pitfall(s) not addressed in the design: "
                            f"{', '.join(missing_high)}"
                        )
                    coverage_lines = []
                    for p in pitfalls_data:
                        pid = p.get("id", "?")
                        risk = p.get("risk", "?")
                        if pid in addressed:
                            coverage_lines.append(f"  {pid} ({risk}): addressed")
                        elif pid in aside:
                            coverage_lines.append(f"  {pid} ({risk}): set aside")
                        else:
                            # Only a high-risk pitfall must be answered; the others
                            # appear in a design only where they change a decision.
                            coverage_lines.append(f"  {pid} ({risk}): "
                                                  + ("MISSING" if risk == "high"
                                                     else "not mentioned"))
                    if coverage_lines:
                        ctx.set_fact("pitfall_coverage",
                                     "Pitfall coverage:\n" + "\n".join(coverage_lines))
            except (json.JSONDecodeError, UnicodeDecodeError):
                problems.append("pitfalls.json exists but is not valid JSON")
        else:
            problems.append(
                "pitfalls.json is missing (gate_pitfalls should have validated it)")
    else:
        ctx.set_fact("pitfall_coverage",
                     "pitfalls check skipped: no pitfalls_researcher in this graph")

    # ── mechanical check 5: finding coverage ──
    # Research and perspective findings are read by the design writer and the reviewer.
    # A design cites only the ones that change a decision, so an uncited finding is
    # recorded in the finding_coverage fact, never listed at the gate or sent back.
    from refactory_dsh import findings_coverage
    findings_coverage.gate_problems(ctx)

    # The review agent's notes do not send the design back; the person sees them.
    advisories.extend(f"review note: {note}" for note in _review_notes(review_text))

    # ── mechanical check 6: repository questions and brief-example trace ──
    deliverable_kind_value = ctx.facts.get("deliverable_kind", "")
    if deliverable_kind_value == "repository_change" and design_text and not scoped:
        from refactory_dsh.authoring_evidence import check_review_questions
        from refactory_dsh.changed_functions import is_generated, is_test_path
        from refactory_dsh import design_contract

        run_name = str(ctx.facts.get("name") or "")
        parsed = design_contract.parse(design_text, run_name)
        changed_fns = [
            design_contract._extract_file_path(site["path"])
            for site in parsed.rule_sites
            if site["status"] == "changed"
        ]
        target = str(ctx.facts.get("target") or "")
        # Normalise and deduplicate; only product code needs repository questions.
        seen_fns: set[str] = set()
        deduped_fns: list[str] = []
        for fn in changed_fns:
            fn_norm = _re.sub(r':\d+(-\d+)?$', '', fn).strip()
            file_path = fn_norm.split("::", 1)[0]
            if (not fn_norm or fn_norm in seen_fns or is_test_path(file_path)
                    or (target and is_generated(Path(target), file_path))):
                continue
            seen_fns.add(fn_norm)
            deduped_fns.append(fn_norm)
        if deduped_fns:
            rq_problems = check_review_questions(
                review_text, deduped_fns, "design_review",
                require_brief_trace=True,
                target=target,
            )
            # Repository questions are the code reviewers' bookkeeping; at design time
            # they are listed, never a round of their own.
            advisories.extend(rq_problems)

    # ── decide: proceed, reloop, or exhaust ──
    # A full design gets the configured number of rounds. A scoped design checked only
    # automatically gets one rewrite for what those checks block on, before the person
    # sees it: a rewrite costs one design step and no review agent. A scoped design the
    # review agent reviewed (a graph from before route_design_review) keeps one round.
    allowed = int(ctx.knob("design_review_attempts", 2))
    if scoped:
        allowed = min(allowed, 2)
    elif ctx.facts.get("research_scope") == "scoped":
        allowed = min(allowed, 1)
    used = int(ctx.facts.get("design_review_attempts_used", 0)) + 1
    ctx.set_fact("design_review_attempts_used", used)
    ctx.set_fact("design_review_advisory", "\n".join(f"- {a}" for a in advisories))
    advisory_block = _advisory_block(advisories)
    coverage = ctx.facts.get("pitfall_coverage", "")

    if not problems:
        ctx.write_text("reviews/design-review-feedback.txt",
                       "the design review passed\n" + advisory_block)
        ctx.set_fact("design_review_feedback", "(the design review passed)")
        ctx.set_fact("design_review_blocking", "")
        ctx.set_fact("design_review_findings",
                     "(none: the design review passed)" + advisory_block
                     + (f"\n\n{coverage}" if coverage else ""))
        ctx.set_fact("design_review_status", design_review_status(ctx.facts))
        return "proceed"

    report = "\n".join(f"- {p}" for p in problems)
    ctx.set_fact("design_review_blocking", report)
    ctx.write_text("reviews/design-review-feedback.txt", report + "\n" + advisory_block)
    ctx.set_fact("design_review_feedback", report)

    if used >= allowed:
        # Out of rounds: proceed to gate_strategy with the findings shown.
        ctx.set_fact("design_review_findings",
                     report + advisory_block + (f"\n\n{coverage}" if coverage else ""))
        ctx.set_fact("design_review_status", design_review_status(ctx.facts))
        return "proceed"

    # Reloop to design_doc with the blocking findings.
    ctx.set_fact("design_review_findings", "(no findings yet)")
    return Verdict("reloop", report)


def classify_scale(ctx: Any) -> dict[str, Any]:
    """Deterministic op: classify the change's scale from the research footprint.

    Reads research-plan.json as raw JSON (not via ``_plan()``, which strips
    entries to only focus/slug/finding/ids).  Every direction must carry a
    ``footprint`` object.  The three-part rule decides scoped vs full; the
    brief is never read.
    """
    # ── refine detection ───────────────────────────────────────────────
    # prepare writes REFINE_SCOPE_REASON for a refine run.  Detect that
    # constant rather than reading research_scope (which we write ourselves).
    if ctx.facts.get("research_scope_reason") == REFINE_SCOPE_REASON:
        return {
            "research_scope": "scoped",
            "research_scope_reason": REFINE_SCOPE_REASON,
        }

    # ── read directions ────────────────────────────────────────────────
    from refactory_dsh.create_ops import _extract_json_array

    raw = ctx.read_text("research-plan.json") if ctx.exists("research-plan.json") else ""
    directions = _extract_json_array(raw)

    if not directions:
        return {
            "research_scope": "full",
            "research_scope_reason": "no usable footprint in the research",
        }

    # ── resolve source for path existence checks ──────────────────────
    source = str(ctx.facts.get("source") or ctx.facts.get("artifact") or "")

    def _path_exists(file_path: str) -> bool:
        """True when *source* is known and *file_path* is a file under it."""
        if not source:
            return True  # no checkout to verify against
        return (Path(source) / file_path).is_file()

    # ── aggregate footprint ────────────────────────────────────────────
    all_existing: dict[tuple[str, str], str] = {}   # (path, name) -> loc
    all_new: dict[str, str] = {}        # name -> at
    shared_engine_refs: list[str] = []
    has_shared_engine = False

    _FULL_NO_FP: dict[str, Any] = {
        "research_scope": "full",
        "research_scope_reason": "no usable footprint in the research",
    }

    for d in directions:
        fp = d.get("footprint") if isinstance(d, dict) else None
        if fp is None:
            return dict(_FULL_NO_FP)
        if not isinstance(fp, dict):
            return dict(_FULL_NO_FP)

        # existing_functions — each entry is "path:line name"
        existing = fp.get("existing_functions", [])
        if existing is None or not isinstance(existing, list):
            return dict(_FULL_NO_FP)
        for entry in existing:
            if not isinstance(entry, str) or ":" not in entry:
                return {
                    "research_scope": "full",
                    "research_scope_reason": f"footprint without evidence: {entry!r}",
                }
            # Parse "path:line name" — at least "x:1 y"
            parts = entry.split(None, 1)
            loc = parts[0] if parts else entry
            name = parts[1] if len(parts) > 1 else loc
            # Verify the cited path exists under the source checkout
            file_path = loc.split(":")[0]
            if not _path_exists(file_path):
                return {
                    "research_scope": "full",
                    "research_scope_reason": f"footprint without evidence: {entry!r}",
                }
            all_existing[(file_path, name)] = loc

        # new_items — each entry names what is added and the path
        new_items_list = fp.get("new_items", [])
        if new_items_list is None or not isinstance(new_items_list, list):
            return dict(_FULL_NO_FP)
        for entry in new_items_list:
            if not isinstance(entry, str) or ":" not in entry:
                return {
                    "research_scope": "full",
                    "research_scope_reason": f"footprint without evidence: {entry!r}",
                }
            # Verify the cited path exists under the source checkout
            colon_idx = entry.index(":")
            before_colon = entry[:colon_idx]
            path_parts = before_colon.rsplit(None, 1)
            file_path = path_parts[-1] if path_parts else before_colon
            if not _path_exists(file_path):
                return {
                    "research_scope": "full",
                    "research_scope_reason": f"footprint without evidence: {entry!r}",
                }
            all_new[entry] = entry

        # shared_engine — list of "path:line" citations
        se = fp.get("shared_engine", [])
        if se is None or se is False:
            se = []  # an explicit "no shared engine"
        if not isinstance(se, list):
            if se is True:
                # tolerate boolean true with no evidence
                return {
                    "research_scope": "full",
                    "research_scope_reason": "footprint without evidence: shared_engine true without citations",
                }
            # Any other shape (a bare "path:line" string, a number, an object) is
            # unreadable evidence, so the safe class is full, never a silent [].
            return {
                "research_scope": "full",
                "research_scope_reason": f"footprint without evidence: shared_engine {se!r}",
            }
        if se:
            has_shared_engine = True
            for ref in se:
                if not isinstance(ref, str) or ":" not in ref:
                    return {
                        "research_scope": "full",
                        "research_scope_reason": f"footprint without evidence: shared_engine {ref!r}",
                    }
                # Verify the cited path exists under the source checkout
                file_path = ref.split(":")[0]
                if not _path_exists(file_path):
                    return {
                        "research_scope": "full",
                        "research_scope_reason": f"footprint without evidence: shared_engine {ref!r}",
                    }
                shared_engine_refs.append(ref)

    # ── apply the three-part rule ──────────────────────────────────────
    if len(all_existing) > 2:
        names = ", ".join(k[1] for k in sorted(all_existing)[:3])
        return {
            "research_scope": "full",
            "research_scope_reason": (
                f"touches {len(all_existing)} existing functions "
                f"(max 2 for scoped): {names}"
            ),
        }

    if all_new:
        names = ", ".join(sorted(all_new)[:3])
        return {
            "research_scope": "full",
            "research_scope_reason": f"adds new items: {names}",
        }

    if has_shared_engine:
        return {
            "research_scope": "full",
            "research_scope_reason": "changes a shared engine",
        }

    # scoped
    fn_keys = sorted(all_existing)
    detail = ", ".join(f"{k[1]} at {all_existing[k]}" for k in fn_keys) if fn_keys else ""
    reason = (
        f"touches {len(all_existing)} existing function(s)"
        + (f" ({detail})" if detail else "")
        + ", adds nothing new, no shared engine"
    )
    return {"research_scope": "scoped", "research_scope_reason": reason}


def design_review_status(facts: dict) -> str:
    """One-line status for the approval gate, from the run's facts.

    Returns '' when no review has run; 'Design review: PASS' on pass, with
    ', N advisory' when the gate listed items that do not change the build; or
    'Design review: FAIL, N unresolved' plus one line per blocking finding. Advisory
    items never count toward FAIL.
    """
    used = int(facts.get("design_review_attempts_used", 0))
    if used == 0:
        return ""
    findings = str(facts.get("design_review_findings", ""))
    if findings == "(no findings yet)":
        return ""
    advisory_n = sum(1 for ln in str(facts.get("design_review_advisory", "")).splitlines()
                     if ln.startswith("- "))
    advisory = f", {advisory_n} advisory" if advisory_n else ""
    reason = facts.get("research_scope_reason", "")
    # A scoped change's design had no review agent; the person is told so.
    automatic = ""
    if facts.get("design_review_by") == "automatic":
        automatic = (" (automatic checks; scoped change"
                     + (f" — {reason}" if reason else "")
                     + ")")
    if "(none: the design review" in findings:
        status = f"Design review{automatic}: PASS{advisory}"
        if not automatic and reason:
            status += f"\nScale: full — {reason}"
        return status
    blocking = facts.get("design_review_blocking")
    source = str(blocking) if blocking is not None else findings
    lines = [
        ln for ln in source.splitlines()
        if ln.startswith("- ")
        and not ln.startswith("- the review did not state 'VERDICT: PASS'")
    ]
    n = len(lines)
    status = (
        f"Design review{automatic}: FAIL, {n} unresolved{advisory}\n"
        + "\n".join(lines)
    )
    if not automatic and reason:
        status += f"\nScale: full — {reason}"
    return status


def _quote_words(text: str) -> str:
    """Text reduced to its words: case, punctuation, markdown and spacing dropped."""
    return " ".join(_re.findall(r"[^\W_]+", text.lower()))


def _review_notes(review_text: str) -> list[str]:
    """The bullets under the review's ``## Notes`` heading, without an empty 'none'."""
    section = _re.search(r"(?im)^##\s+notes\s*$(.*?)(?=^#{1,2}\s|^\s*VERDICT:|\Z)",
                         review_text, _re.S | _re.M)
    if section is None:
        return []
    notes = []
    for line in section[1].splitlines():
        bullet = _re.match(r"\s*(?:[-*\u2022]|\d+[.)])\s+(.*\S)", line)
        if bullet and not _re.fullmatch(r"(?i)none\.?", bullet[1].strip()):
            notes.append(bullet[1].strip())
    return notes


def _advisory_block(advisories: list[str]) -> str:
    """The gate's advisory items as a section, or '' when there are none."""
    if not advisories:
        return ""
    return ("\n\n## Advisory (does not change the build; no round needed)\n"
            + "\n".join(f"  · {a}" for a in advisories))


def gate_pitfalls(ctx: Any) -> str:
    """Validate pitfalls.json structure; reloop to pitfalls_researcher when invalid.

    When attempts are exhausted, stops the run as gate_research does.
    """
    from refactory_dsh import pitfalls as pitfalls_mod

    raw = ctx.read_text("pitfalls.json") if ctx.exists("pitfalls.json") else ""
    source = str(ctx.facts.get("source") or ctx.facts.get("artifact") or "")
    problems = pitfalls_mod.validate_pitfalls(raw, source, run_dir=str(ctx.workdir))

    if not problems:
        ctx.write_text("pitfalls-review.txt", "ok\n")
        ctx.set_fact("pitfalls_review", "(pitfalls validated)")
        return "proceed"

    allowed = int(ctx.knob("pitfalls_attempts", 2))
    used = int(ctx.facts.get("pitfalls_attempts_used", 0)) + 1
    ctx.set_fact("pitfalls_attempts_used", used)

    report = "\n".join(f"- {p}" for p in problems)
    ctx.write_text("pitfalls-review.txt", report + "\n")
    ctx.set_fact("pitfalls_review", report)

    if used >= allowed:
        raise ValueError(
            f"pitfalls are still not valid after {used} attempt(s):\n{report}"
        )
    return Verdict("reloop", report)


def apply_cwd_escaped(result: Any, outcome: dict[str, Any]) -> None:
    """Record ``cwd_escaped`` into *outcome* when *result* shows a directory escape.

    Both ``check_artifact`` and ``fast_ops.focused_check`` apply this logic:
    when ``sandbox.run_command`` was called with a ``worktree_root`` and the
    command left that directory, ``result.cwd_outside`` is set.  This helper
    centralises the recording so the logic is not replicated in each call site.
    """
    if getattr(result, "cwd_outside", None):
        outcome["cwd_escaped"] = result.cwd_outside


def _refused_verdict(ctx: Any) -> Verdict | None:
    """The reloop for an emission materialise could not apply, or None when it applied."""
    refused = repo_change.refused(ctx)
    if not refused:
        return None
    # Nothing was materialised, so there is nothing to record runtime evidence about;
    # a refused emission is not a code failure, so it does not spend an attempt.
    detail = f"materialise could not apply emit.txt, so nothing was checked: {refused}"
    ctx.write_text("check.txt", detail + "\n")
    ctx.set_fact("validation", detail)
    ctx.write_json("check-record.json", {
        "status": "refused", "validation": detail,
        "attempt": int(ctx.facts.get("attempts_used", 0)),
        "check_mode": "full", "targeted_command": None,
    })
    return Verdict("reloop", detail)


def _early_stop(ctx: Any, prepass: Any, prev_record: dict[str, Any],
                _saved_base_unavailable: str) -> Any:
    """Send the attempt back because the tests that failed last time still fail."""
    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh.create_ops import repair
    from refactory_dsh.failfast import detect_stuck

    # Stop early: the targeted test still fails.
    detail = prepass.digest_text or "(targeted check failed)"
    ctx.write_text("check.txt", detail + "\n")
    ctx.set_fact("validation", detail)

    # R5: keep design-constraint lines in early-stop feedback.
    from refactory_dsh import design_contract
    run_name = str(ctx.facts.get("name") or "")
    parsed = design_contract.parse(
        ctx.read_text("design.md") if ctx.exists("design.md") else "", run_name
    )
    left_lines = [
        f"Design constraint: {site['path']} [left: {site['reason']}] "
        "— do not change this; fix the tests or callers instead."
        for site in parsed.rule_sites
        if site.get("status") == "left" and site.get("reason")
    ]
    if left_lines:
        detail = "\n".join(left_lines) + "\n\n" + detail

    # base_unavailable note
    base_note = _saved_base_unavailable
    if base_note:
        detail = detail + "\n\n" + base_note

    # Stuck detection on early-stop path too (correction item 4)
    found_wt = worktree.recorded(ctx.workdir)
    stuck_notice = detect_stuck(
        prev_record, prepass.runners,
        "\n".join(filter(None, [
            getattr(prepass.result, "stdout", None) or "",
            getattr(prepass.result, "stderr", None) or "",
        ])).strip(),
        found_wt.path if found_wt else None,
        prev_record.get("commit"),
        str(ctx.facts.get("commit", "")),
    )
    if stuck_notice:
        detail = stuck_notice + "\n\n" + detail

    outcome = {
        "command": prepass.targeted_command, "status": "code_failure",
        "check_mode": "targeted", "targeted_command": prepass.targeted_command,
    }
    evidence.record(ctx, outcome)
    ctx.write_json("check-record.json", {
        "status": "fail",
        "validation": detail[:4000],
        "digest": prepass.digest_text,
        "attempt": int(ctx.facts.get("attempts_used", 0)),
        "command": prepass.targeted_command,
        "runners": prepass.runners,
        "raw_tail": prepass.raw_tail,
        "check_mode": "targeted",
        "targeted_command": prepass.targeted_command,
        "inherited": list(prev_record.get("inherited", [])),
        "commit": str(ctx.facts.get("commit", "")),
    })
    # Do NOT append to convergence history — targeted runs excluded.
    return repair(ctx, [detail], early_stop=True)


def check_artifact(ctx: Any, *, check_attempt: str | None = None) -> str:
    """Gate: run the brief's check, in a sandbox, and refuse what does not pass.

    This is the design-time equivalent of the package gate: a package must import and
    score, an artifact must build and run. Both refuse rather than warn, because an
    artifact nobody checked is not a deliverable.

    The check runs where the run works (`repo_change.run_check`). An emission materialise
    could not apply is sent back to emit with the reason, as a failed check is, rather
    than ending the run (#330 E5). ``check_attempt`` names the prepared attempt the
    evidence belongs to, when `prepare_check` ran this whole check itself.
    """
    refused = _refused_verdict(ctx)
    if refused is not None:
        return refused
    command = str(ctx.facts["check"])
    check_timeout = int(ctx.knob("check_timeout_s", 180))

    # ── Targeted pre-pass ──────────────────────────────────────────────
    from refactory_dsh.failfast import targeted_prepass

    prev_record = ctx.read_json("check-record.json", {})
    # Read base_unavailable before it is cleared below.
    _saved_base_unavailable = ctx.facts.get("base_unavailable") or ""

    prepass = targeted_prepass(
        prev_record, command,
        lambda cmd, timeout: repo_change.run_check(ctx, cmd, timeout, triage_prep=False),
        check_timeout,
        root=(worktree.recorded(ctx.workdir).path
              if worktree.recorded(ctx.workdir) is not None else None),
    )
    if prepass.early_stop:
        return _early_stop(ctx, prepass, prev_record, _saved_base_unavailable)
    return _full_check(ctx, command, check_timeout, prev_record,
                       prepass.targeted_command if prepass.ran else None,
                       beside_reviews=False, check_attempt=check_attempt)


def _full_check(ctx: Any, command: str, check_timeout: int, prev_record: dict[str, Any],
                targeted_command: str | None, *, beside_reviews: bool,
                check_attempt: str | None = None) -> Any:
    """Run the whole check on the attempt, record its evidence, and judge it."""
    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh.create_ops import repair
    from refactory_dsh.failfast import detect_stuck, record_check_history

    result = (repo_change.run_check(ctx, command, check_timeout, beside_reviews=True)
              if beside_reviews else repo_change.run_check(ctx, command, check_timeout))
    detail = "\n".join((result.problem(), result.stdout or "", result.stderr or "")).strip()
    outcome = {"command": command, "status": "pass" if result.ok else evidence.check_failure_kind(
        result, budget=getattr(ctx, "budget", None)),
        "stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode,
        "timed_out": result.timed_out, "elapsed_s": result.elapsed_s,
        "check_mode": "full",
        "targeted_command": targeted_command}
    if check_attempt:
        # The prepared attempt this evidence belongs to: a review taken beside this
        # check binds to it (`authoring_evidence.current_problems`).
        outcome["check_attempt"] = check_attempt
    apply_cwd_escaped(result, outcome)
    ctx.write_text("check.txt", detail or "ok\n")

    # Whether the new tests fail without the change is evidence for the reviewers, passing
    # or not; recorded before the receipt, which hashes it.
    from refactory_dsh import base_check
    guardedness = None
    if worktree.recorded(ctx.workdir) is not None:
        ctx.set_fact("inherited_failures", [])
        try:
            guardedness = base_check.compute_test_guardedness(
                ctx, command, int(ctx.knob("check_timeout_s", 180)),
            )
        except Exception as exc:  # evidence, not a gate: say why it is missing
            guardedness = {"unavailable": f"the new tests could not be run on the base: {exc}"}
        if guardedness is not None:
            ctx.write_json("test-guardedness.json", guardedness)
    evidence.record(ctx, outcome)

    # Clear base_unavailable at the start of each check attempt (R2).
    ctx.set_fact("base_unavailable", "")

    if result.ok:
        # ── enforce test guardedness (R3, #384 item 1) ──
        unexempted = enforce_guardedness(ctx)
        if unexempted:
            detail = (
                "BLOCKING: these new tests pass without the change: "
                + ", ".join(unexempted)
                + ". Make them fail without the change, or explain in the "
                "PR why each test pins existing behaviour."
            )
            ctx.write_text("check.txt", detail + "\n")
            ctx.write_json("check-record.json", {
                "status": "guarded", "validation": detail,
                "attempt": int(ctx.facts.get("attempts_used", 0)),
                "check_mode": "full",
                "targeted_command": targeted_command,
            })
            return repair(ctx, [detail])
        ctx.set_fact("validation", "(the check passed)")
        ctx.write_json("check-record.json", {
            "status": "pass", "validation": "(the check passed)",
            "attempt": int(ctx.facts.get("attempts_used", 0)),
            "check_mode": "full",
            "targeted_command": targeted_command,
        })
        return "proceed"

    # Name the specific resource limit when a file-size stop occurs.
    rc = result.returncode
    if rc == 153 or (rc is not None and rc < 0 and -rc == 25):
        detail = (
            f"the check exceeded the file-size limit ({sandbox.DEFAULT_FILE_MB} MB, "
            "set as DEFAULT_FILE_MB in refactory_dsh/sandbox.py). " + detail
        )
    evidence.stop_unrepairable(outcome["status"], detail)

    # Route change-caused preparation failures to the builder for repair.
    _ccp = getattr(result, "change_caused_prep", None)
    if _ccp:
        from refactory_dsh.check_digest import _clean as _strip_noise
        _error_text = _strip_noise(_ccp["error_text"])
        _prep_detail = (
            f"the {_ccp['step_label']} command `{_ccp['command']}` "
            f"failed (exit {_ccp['returncode']}):\n{_error_text}"
        )
        ctx.write_text("check.txt", _prep_detail + "\n")
        record_check_history(ctx, "full", _prep_detail, command)
        ctx.write_json("check-record.json", {
            "status": "fail",
            "validation": _prep_detail[:4000],
            "attempt": int(ctx.facts.get("attempts_used", 0)),
            "check_mode": "prep_triage",
            "command": _ccp.get("command"),
            "commit": str(ctx.facts.get("commit", "")),
        })
        return repair(ctx, [_prep_detail])

    # A failure the unchanged base has too is not the attempt's (`base_check.compare_failures`).
    found = worktree.recorded(ctx.workdir)
    if found is not None:
        filtered = base_check.filter_check(
            ctx, result, command, int(ctx.knob("check_timeout_s", 180)),
            guardedness=guardedness,
        )
        if filtered.base_environment_failure:
            if filtered.base_crashed:
                # R2: a crash — continue without excusals, with the note.
                note = (f"base comparison unavailable: {filtered.base_environment_failure}; "
                        "the attempt's results stand without excusals")
                ctx.set_fact("base_unavailable", note)
            else:
                # A deterministic failure — stop as today's base_environment_failure.
                evidence.stop_unrepairable(
                    'unavailable',
                    f"the base check could not run: {filtered.base_environment_failure}",
                )

        # Diagnose unbuilt packages from the worktree's own manifests.
        wt_path = Path(found.path)
        unbuilt = unbuilt_check.scan_unbuilt_packages(wt_path, found.base, found.head)
        unbuilt_msg = unbuilt_check.format_unbuilt_diagnosis(unbuilt) if unbuilt else ""

        if filtered.all_excused:
            inherited_detail = filtered.inherited_detail
            if unbuilt_msg and inherited_detail:
                inherited_detail = inherited_detail + "\n\n" + unbuilt_msg
            elif unbuilt_msg:
                inherited_detail = unbuilt_msg
            validation_msg = f"(the check passed; {inherited_detail})"
            ctx.set_fact("validation", validation_msg)
            ctx.write_text("check.txt", validation_msg + "\n")
            # The receipt the reviews and the audit check is this one, which passed.
            outcome["status"] = "pass"
            outcome["inherited_failures"] = sorted(filtered.inherited)
            ctx.set_fact("inherited_failures", sorted(filtered.inherited))
            evidence.record(ctx, outcome)
            ctx.write_json("check-record.json", {
                "status": "pass", "validation": validation_msg,
                "attempt": int(ctx.facts.get("attempts_used", 0)),
                "check_mode": "full",
                "targeted_command": targeted_command,
            })
            # ── enforce test guardedness on excused pass too (R3) ──
            unexempted = enforce_guardedness(ctx)
            if unexempted:
                detail = (
                    "BLOCKING: these new tests pass without the change: "
                    + ", ".join(unexempted)
                    + ". Make them fail without the change, or explain in the "
                    "PR why each test pins existing behaviour."
                )
                ctx.write_text("check.txt", detail + "\n")
                ctx.write_json("check-record.json", {
                    "status": "guarded", "validation": detail,
                    "attempt": int(ctx.facts.get("attempts_used", 0)),
                    "check_mode": "full",
                    "targeted_command": targeted_command,
                })
                return repair(ctx, [detail])
            return "proceed"
        if filtered.new_failures and filtered.inherited:
            detail = filtered.new_failure_detail
        if filtered.inherited_detail:
            inherited_part = filtered.inherited_detail
            if unbuilt_msg:
                inherited_part = inherited_part + "\n\n" + unbuilt_msg
            ctx.write_text("check.txt", detail + "\n\n" + inherited_part + "\n")
        elif unbuilt_msg:
            ctx.write_text("check.txt", detail + "\n\n" + unbuilt_msg + "\n")

        # When every new failure names an exact missing entry-point path,
        # stop without spending a builder attempt.
        if unbuilt and unbuilt_check.failures_explained_by_unbuilt(detail, unbuilt):
            raise ValueError(
                f"check failed because of unbuilt packages; code repair was not attempted.\n{unbuilt_msg}"
            )
        # Otherwise prepend the diagnosis so the builder sees it.
        if unbuilt_msg:
            detail = unbuilt_msg + "\n\n" + detail

    # Build digest of new failures
    from refactory_dsh.check_digest import digest_check_output, format_digest
    check_output = detail
    digest = digest_check_output(check_output)
    digest_text = format_digest(digest)
    if digest_text.strip():
        detail = digest_text + "\n\n" + detail[-2000:]

    # R5: augment feedback with [left: ...] design constraints and broken guarantees.
    from refactory_dsh import design_contract
    run_name = str(ctx.facts.get("name") or "")
    parsed = design_contract.parse(
        ctx.read_text("design.md") if ctx.exists("design.md") else "", run_name
    )
    left_lines: list[str] = []
    for site in parsed.rule_sites:
        if site.get("status") == "left" and site.get("reason"):
            left_lines.append(
                f"Design constraint: {site['path']} [left: {site['reason']}] "
                "— do not change this; fix the tests or callers instead."
            )

    guarantee_lines: list[str] = []
    if (found is not None and filtered is not None
            and not filtered.base_environment_failure
            and filtered.base_complete
            and filtered.base_failures is not None):
        attempt_output = "\n".join((result.stdout or "", result.stderr or "")).strip()
        attempt_failures = base_check.parse_test_failures(attempt_output)
        changed_files = set(base_check._all_changed_files(Path(found.path), found.base))
        for failure in sorted(attempt_failures):
            if failure not in filtered.base_failures:
                test_file = base_check._test_file(failure)
                if test_file and test_file not in changed_files:
                    guarantee_lines.append(
                        f"Broken guarantee: {failure} passes on the base but "
                        "fails on this attempt — the attempt must not break it."
                    )

    if left_lines or guarantee_lines:
        detail = "\n".join(left_lines + guarantee_lines) + "\n\n" + detail

    # Stuck detection on the full-check path.
    found_wt_stuck = worktree.recorded(ctx.workdir)
    stuck_notice = detect_stuck(
        prev_record, digest.to_dict()["runners"],
        "\n".join((result.stdout or "", result.stderr or "")).strip(),
        found_wt_stuck.path if found_wt_stuck else None,
        prev_record.get("commit"),
        str(ctx.facts.get("commit", "")),
    )
    if stuck_notice:
        detail = stuck_notice + "\n\n" + detail

    # R6: store failure history and blocker result for convergence detection.
    attempt_output_r6 = "\n".join((result.stdout or "", result.stderr or "")).strip()
    record_check_history(ctx, "full", attempt_output_r6, command)

    # Write base_unavailable note into check.txt as well (R2 correction 5).
    base_note = ctx.facts.get("base_unavailable")
    if base_note:
        check_txt = ctx.read_text("check.txt") if ctx.exists("check.txt") else ""
        if base_note not in check_txt:
            ctx.write_text("check.txt", check_txt.rstrip() + "\n\n" + base_note + "\n")

    # Record exactly what the builder will be given (feedback re-fill reads it).
    _inherited_list = sorted(filtered.inherited) if found is not None else []
    _base_reset_list = getattr(ctx, '_base_reset', [])
    _cr_fail: dict[str, Any] = {
        "status": "fail",
        "validation": detail[:4000],
        "digest": digest_text,
        "attempt": int(ctx.facts.get("attempts_used", 0)),
        "command": command,
        "runners": digest.to_dict().get("runners", []),
        "raw_tail": digest.raw_tail[-500:],
        "check_mode": "full",
        "targeted_command": targeted_command,
        "inherited": _inherited_list,
        "commit": str(ctx.facts.get("commit", "")),
    }
    if _base_reset_list:
        _cr_fail["base_reset"] = _base_reset_list
    ctx.write_json("check-record.json", _cr_fail)

    return repair(ctx, [detail])


def prepare_check(ctx: Any) -> Any:
    """Gate: bring the attempt to the commit its check and its reviewers both see.

    The check runs beside the reviews (`check_beside_reviews`), so everything that can
    still move the commit happens here first: the environment install, the ``generate``
    command and the ``setup`` build (`repo_change.prepare_attempt`). The tests that failed
    last time run here too, and an attempt that still fails them goes back to the builder
    without spending a review. A preparation that fails is judged here by the whole
    check, as it always was, which reports it with its triage.
    """
    import uuid

    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh.failfast import targeted_prepass

    # Nothing a last attempt prepared or decided may stand for this one.
    for name in (CHECK_PREP, CHECK_VERDICT):
        ctx.path(name).unlink(missing_ok=True)
    refused = _refused_verdict(ctx)
    if refused is not None:
        return refused
    command = str(ctx.facts["check"])
    check_timeout = int(ctx.knob("check_timeout_s", 180))
    attempt = uuid.uuid4().hex
    # What sent the build back, for the reviewers: the check beside them rewrites
    # `validation` while they run.
    ctx.set_fact("review_feedback", str(ctx.facts.get("validation") or ""))
    ctx.set_fact("review_baseline", json.dumps(evidence.requirements(ctx), indent=1))
    prev_record = ctx.read_json("check-record.json", {})
    saved_base_unavailable = ctx.facts.get("base_unavailable") or ""
    if repo_change.prepare_attempt(ctx, check_timeout):
        verdict = check_artifact(ctx, check_attempt=attempt)
        _record_prepared(ctx, attempt, None)
        _record_check_verdict(ctx, attempt, verdict)
        return verdict
    found = worktree.recorded(ctx.workdir)
    prepass = targeted_prepass(
        prev_record, command,
        lambda cmd, timeout: repo_change.run_check(ctx, cmd, timeout, triage_prep=False,
                                                   prepared=True),
        check_timeout, root=found.path if found is not None else None,
    )
    if prepass.early_stop:
        return _early_stop(ctx, prepass, prev_record, saved_base_unavailable)
    _record_prepared(ctx, attempt, prepass.targeted_command if prepass.ran else None)
    return "proceed"


def _record_prepared(ctx: Any, attempt: str, targeted_command: str | None) -> None:
    # `root` is the directory the evidence attests (`authoring_evidence.record`).
    ctx.write_json(CHECK_PREP, {
        "attempt": attempt, "commit": str(ctx.facts.get("commit") or ""),
        "root": str(ctx.facts.get("artifact") or ctx.facts.get("package_path") or ""),
        "targeted_command": targeted_command})


def _record_check_verdict(ctx: Any, attempt: str, verdict: Any) -> None:
    # The command it ran: a decision stands for this attempt only while the check is
    # the same command (`graph-control amend-command` changes it).
    ctx.write_json(CHECK_VERDICT, {"attempt": attempt, "command": str(ctx.facts.get("check") or ""),
                                   "verdict": str(verdict), "reason": getattr(verdict, "reason", "")})


def _decided(ctx: Any, attempt: str) -> dict[str, Any]:
    """The decision recorded for ``attempt`` with the current check command, or {}."""
    decided = ctx.read_json(CHECK_VERDICT, {}) if ctx.exists(CHECK_VERDICT) else {}
    if (attempt and decided.get("attempt") == attempt
            and decided.get("command") == str(ctx.facts.get("check") or "")):
        return decided
    return {}


def check_beside_reviews(ctx: Any) -> dict[str, Any]:
    """Op inside the review fork: the attempt's whole check, run beside its reviews.

    It runs on the commit `prepare_check` settled, in the run's check copy
    (`repo_change.run_check` with ``beside_reviews``), and records what it decided for
    `gate_check`, which routes once the reviews are done: a node inside a fork may not
    send the run outside it. A check that raises is recorded too and raised by
    `gate_check`, because the node runner retries a failed op, and a retry here would be
    a second full check for the same failure.
    """
    from refactory_dsh.budgets import BudgetExhausted

    attempt = _prepared_attempt(ctx)
    if not attempt:
        raise ValueError("the check was not prepared for this attempt; prepare_check runs "
                         "before the review fork")
    if _decided(ctx, attempt):
        return {}  # prepare_check ran the whole check itself
    try:
        verdict = _full_check(ctx, str(ctx.facts["check"]), int(ctx.knob("check_timeout_s", 180)),
                              ctx.read_json("check-record.json", {}),
                              _prepared(ctx).get("targeted_command"), beside_reviews=True,
                              check_attempt=attempt)
    except BudgetExhausted:
        raise
    except Exception as exc:  # recorded for gate_check to raise, not retried here
        ctx.write_json(CHECK_VERDICT, {"attempt": attempt, "command": str(ctx.facts["check"]),
                                       "error": str(exc), "error_type": type(exc).__name__})
        return {}
    _record_check_verdict(ctx, attempt, verdict)
    return {}


def _prepared(ctx: Any) -> dict[str, Any]:
    return ctx.read_json(CHECK_PREP, {}) if ctx.exists(CHECK_PREP) else {}


def _prepared_attempt(ctx: Any) -> str:
    """The attempt `prepare_check` prepared, when it is the commit the run is at now."""
    prepared = _prepared(ctx)
    if prepared.get("attempt") and prepared.get("commit") == str(ctx.facts.get("commit") or ""):
        return str(prepared["attempt"])
    return ""


def gate_check(ctx: Any) -> Any:
    """Gate after the review fork: route on what the check beside the reviews decided.

    A failed check sends the build back with the check's feedback, as before; the
    reviews of that attempt are not read, because the next attempt is reviewed afresh.
    A check that stopped the run (an unrepairable failure) stops it here. A person's
    skip of the check proceeds on its skip receipt for this attempt.

    With no decision for this attempt and command -- the check command was corrected
    after the check ran (`graph-control amend-command` reruns this gate), or the check
    never recorded one -- this gate runs the whole check itself, as the sequential gate
    did, bound to the same attempt, so the reviews beside it stay current.
    """
    from refactory_dsh import authoring_evidence as evidence

    attempt = _prepared_attempt(ctx)
    if not attempt:
        raise ValueError("the check was not prepared for the attempt the run is at; "
                         "prepare_check runs before the review fork")
    decided = _decided(ctx, attempt)
    if decided:
        if decided.get("error_type"):
            # The check stopped the run, as it would have before the reviews.
            raise (RuntimeError if decided["error_type"] == "RuntimeError" else ValueError)(
                str(decided.get("error") or "the check failed"))
        label = str(decided.get("verdict") or "")
        return "proceed" if label == "proceed" else Verdict(label, str(decided.get("reason") or ""))
    validation = (ctx.read_json(evidence.EVIDENCE, {}) if ctx.exists(evidence.EVIDENCE)
                  else {}).get("validation", {})
    if (validation.get("status") == "skipped" and validation.get("source") == "person"
            and validation.get("check_attempt") == attempt):
        return "proceed"
    verdict = check_artifact(ctx, check_attempt=attempt)
    _record_check_verdict(ctx, attempt, verdict)
    return verdict


def enforce_guardedness(ctx: Any) -> list[str]:
    """Return the names of non-guarding **added** tests that are not exempted.

    Only added test files (not modified ones) are enforced, as the brief
    says "a new test".  Exemptions come from two sources:

    1. design.md's ``## Test guardedness exemptions`` section (full mode).
    2. A ``# guardedness-exempt: <reason>`` comment in the test file itself
       (works in both full and fast mode, since fast has no design.md).

    ``cannot_run_on_base`` files (collection error on the base, typically
    because they import newly-added code) are excluded: they are not
    "passing without the change".

    When no guardedness record exists, the caller should note it in the
    check record.
    """
    if not ctx.exists("test-guardedness.json"):
        return []
    guardedness = ctx.read_json("test-guardedness.json", {})
    if "unavailable" in guardedness:
        return []
    summary = guardedness.get("summary", {})
    if summary.get("not_guarding", 0) == 0:
        return []

    # Determine which test files were ADDED (not modified).
    added_files: set[str] | None = None
    try:
        from refactory_dsh import worktree
        found = worktree.recorded(ctx.workdir)
        if found is not None:
            added_raw = worktree.git(
                Path(found.path), "diff", "--name-only", "--diff-filter=A",
                found.base, "HEAD",
            )
            added_files = set(f.strip() for f in added_raw.splitlines() if f.strip())
    except (ValueError, OSError, AttributeError):
        pass  # If we can't determine, enforce on all (conservative)

    # Collect non-guarding test file paths — only ADDED files.
    non_guarding = []
    for result in guardedness.get("results", []):
        if result.get("guarding") is False and result.get("file_level") != "cannot_run_on_base":
            tf = result.get("file", "unknown")
            if added_files is not None and tf not in added_files:
                continue  # Modified file, not a new test — skip
            non_guarding.append(tf)

    if not non_guarding:
        return []

    # Read exemptions from design.md (full mode).
    design_text = ctx.read_text("design.md") if ctx.exists("design.md") else ""
    exempted: set[str] = set()
    in_section = False
    for line in design_text.splitlines():
        stripped = line.strip()
        if stripped.lower() == "## test guardedness exemptions":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and (stripped.startswith("- ") or stripped.startswith("* ")):
            bullet_text = stripped[2:].strip()
            m = _re.match(r'`([^`]+)`', bullet_text)
            if m:
                exempted.add(m.group(1).strip())
            else:
                idx = bullet_text.find(' — ')
                exempted.add(bullet_text[:idx].strip() if idx >= 0 else bullet_text.split()[0] if bullet_text else "")

    # Read marker exemptions from the test files themselves.
    try:
        from refactory_dsh import worktree as _wt
        found = _wt.recorded(ctx.workdir)
        if found is not None:
            for tf in non_guarding:
                tf_path = Path(found.path) / tf
                if tf_path.is_file():
                    try:
                        content = tf_path.read_text(errors="replace")
                        if _re.search(r'#\s*guardedness-exempt:', content):
                            exempted.add(tf)
                    except OSError:
                        pass
    except (ValueError, AttributeError):
        pass

    return [t for t in non_guarding if t not in exempted]


def report_artifact(ctx: Any) -> dict[str, Any]:
    """Say what was produced, so a run's output is discoverable rather than inferred."""
    from refactory_dsh.authoring_evidence import require_completion

    if worktree.recorded(ctx.workdir) is not None:
        # Where the worktree stands first, so a moved HEAD is named as such and not as a
        # stale sign-off.
        worktree.require_current(ctx.workdir)
    require_completion(ctx)
    ev = ctx.read_json("authoring-evidence.json", None)
    audit = ctx.read_json("intent-audit.json", None)
    # Collect planning model info from worker receipts (R5).
    # Sort by modification time so the latest receipt for each node wins.
    planning_models: dict[str, str | None] = {}
    workers_dir = ctx.workdir / ".refactory" / "workers"
    if workers_dir.is_dir():
        try:
            receipt_files = sorted(
                (f for f in workers_dir.iterdir() if f.suffix == ".json"),
                key=lambda f: f.stat().st_mtime,
            )
        except OSError:
            receipt_files = []
        for wf in receipt_files:
            try:
                rec = json.loads(wf.read_text())
                nid = rec.get("node_id", "")
                if nid in ("design_doc", "design_review"):
                    route = rec.get("actual_route") or rec.get("requested_route") or {}
                    planning_models[nid] = route.get("model") or None
            except (json.JSONDecodeError, OSError):
                pass

    from refactory_dsh.authoring_evidence import _check_skip_provenance
    skip_prov = _check_skip_provenance(ctx.workdir)
    check_skipped_info: dict[str, Any] | None = None
    check_note = ""
    if skip_prov is not None and skip_prov.get("source") == "person":
        check_skipped_info = {
            "source": skip_prov["source"],
            "at": skip_prov["at"],
            "via": "graph-control",
            "node": skip_prov["node"],
        }
        check_note = (f"the check did not run (skipped by the person at {skip_prov['at']} "
                      f"via graph-control on node {skip_prov['node']}; covers later attempts)")

    report_payload: dict[str, Any] = {
        "name": ctx.facts.get("name"),
        "artifact": ctx.facts.get("artifact"),
        "files": ctx.facts.get("files", []),
        "check": ctx.facts.get("check"),
        "checked": bool(ev and isinstance(ev.get("validation"), dict)
                        and ev["validation"].get("status") == "pass"),
        "validation_evidence": ev,
        "intent_audit": audit,
    }
    if check_skipped_info is not None:
        report_payload["check_skipped"] = check_skipped_info
        report_payload["check_note"] = check_note
    if planning_models:
        report_payload["planning_models"] = planning_models
    # Surface process notes with their followed/not-followed statement.
    process_notes = (ev or {}).get("process_notes", [])
    if process_notes:
        audit_notes = {item["id"]: item for item in (audit or {}).get("process_notes", [])
                       if isinstance(item, dict)}
        report_payload["process_notes"] = [
            {**note, **({"followed": audit_notes[note["id"]]["followed"],
                         "detail": audit_notes[note["id"]].get("detail", "")}
                        if note["id"] in audit_notes and isinstance(audit_notes[note["id"]].get("followed"), bool)
                        else {"followed": None, "detail": "compliance not verified by audit"})}
            for note in process_notes
        ]
    payload = repo_change.report(ctx, "design.json", report_payload)
    return {"designed": json.dumps(payload, default=str)}

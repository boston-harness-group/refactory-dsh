"""design-v2: a brief in, a checked software artifact out.

The third of the three meta-modes. create emits a package of modes; optimize searches
one; design emits software. The shape is the same as create's -- design, emit, check,
regenerate with the reasons -- and only the artifact and its gate differ, which is
what makes it a mode rather than a second program.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from refactory_dsh import delivery, design_contract, pitfalls, repo_change
from refactory_dsh.create_ops import API, MARKER_RULE, OVERWATCH_VERDICT_RULE, RESEARCH_SCHEMA, SEVERITY_RULE
from refactory_dsh.invocations import CHECK_PREP, CHECK_VERDICT
from refactory_dsh.model import DEFAULT_TIMEOUT_S
from refactory_dsh.nn import Graph, Knob, Mode, Node, RunResult

OPS = "refactory_dsh.design_ops"

#: Design mode's verifier runs beside the check, in the review fork.
BESIDE_CHECK_VERIFIER_NOTE = (
    "\n\n## The check runs beside this review\n"
    "The check `{{check}}` runs at the same time, on the same commit, and is judged on "
    "its own. Do not run the whole check yourself.\n"
)

_ACCEPTANCE_BASELINE = (
    "Acceptance baseline: the original brief, any approved design, and explicit user "
    "steering. Preserve user-approved non-goals. A README limitation, proposed "
    "exclusion, coordinator summary or reviewer note cannot waive a requested "
    "behavior without explicit user agreement to that change. Report conflicts "
    "instead of silently narrowing scope. Reviews must mark observed noncompliance "
    "as BLOCKING even when the limitation is documented.\n\n"
)


class DesignMode(Mode):
    """Design, emit and check a software artifact.

    Unscored on purpose, like create-mode: its run is judged by whether the artifact
    passes the check the brief declared.
    """

    name = "design"
    description = "design, emit and check a software artifact from a brief"
    unscored_reason = "it is judged by whether the artifact passes the check its brief declared"

    @classmethod
    def declare(cls) -> dict[str, Knob]:
        return {
            "research_attempts": Knob(
                3, "int", lo=1, hi=8,
                description="how many times the research step may be retried when its "
                            "plan is not a plan or its findings were never written",
            ),
            "check_attempts": Knob(
                3, "int", lo=1, hi=5,
                description="how many times the emit step may be retried when the "
                            "artifact does not pass its check",
            ),
            "review_attempts": Knob(
                3, "int", lo=1, hi=5,
                description="how many times the review gates may reloop before "
                            "stopping; defaults to the same as check_attempts",
            ),
            "check_timeout_s": Knob(
                180, "int", lo=10, hi=1800,
                description="how long the brief's check may run",
            ),
            "check_attempt_cap": Knob(
                8, "int", lo=0,
                description="hard cap on total check attempts including convergence grants; "
                            "0 or a value at or below check_attempts disables the extension",
            ),
            "design_review_attempts": Knob(
                2, "int", lo=1, hi=4,
                description="how many times the design review may reloop before proceeding "
                            "to the approval gate with its remaining findings",
            ),
            "pitfalls_attempts": Knob(
                2, "int", lo=1, hi=5,
                description="how many times the pitfalls step may be retried when its "
                            "output is invalid",
            ),
            "claims_check_attempts": Knob(
                2, "int", lo=1, hi=4,
                description="how many times the claims checker may reloop before "
                            "passing mismatches to the review",
            ),
            # As in create: the walker passes these in the run's task, and a run on
            # DSH carries them as knobs.
            "name": Knob("", "str", description="the artifact to design (empty takes the name from the brief)"),
            "brief": Knob("", "str", required=True, description="what to build, in the asker's words"),
            "check": Knob("", "str", description="the command that decides whether the artifact works"),
            # Where the run works (#351), as any mode that changes a repository declares it
            # (`repo_change`), and how its work is delivered once its reviews pass (#350).
            "refusal_attempts": Knob(
                3, "int", lo=1, hi=10, frozen=True,
                description="consecutive no-op emissions before the run stops",
            ),
            **repo_change.knobs(),
            **delivery.knobs(),
        }

    def build(self) -> Graph:
        graph = Graph()
        graph.add(Node(
            "prepare", "op", call=f"{OPS}:prepare_design",
            knobs=("name", "brief", "check", *repo_change.knob_names(), *delivery.knobs()),
            writes=("brief.json", "research-review.txt", "check.txt", "delivery.md",
                    "reviews/design-review-feedback.txt", "pitfalls-review.txt",
                    "check-record.json", "feedback-initial.json"),
            produces=("name", "brief", "check", "validation", "research_review", "research_scope", "qa_gate",
                      "overwatch_review", "design_review_feedback", "design_review_findings",
                      "deliverable_kind", "review_instructions", "verification_methods",
                      *repo_change.produces(), "delivery", "delivery_plan",
                      "has_pitfalls_step", "pitfalls", "pitfalls_review",
                      "claims_check_feedback", "claims_check_attempts_used", "design_review_status"),
            notes="read the brief and the command that decides whether it is done",
        ))

        # ── design-v2's head: research, perspectives, then the design itself ──
        graph.add(Node(
            "research_director", "agent",
            prompt_template=(
                "Artifact name: {{name}}\nBrief: {{brief}}\n"
                "It will be checked by running, from {{check_root}}:\n"
                "    {{check}}\n\n"
                "## Research scope\n{{research_scope}}\n\n"
                "You are the research director. Decide what has to be learned before "
                "this artifact can be designed: between three and five questions for "
                "a full design, or one focused question for a scoped change (two or "
                "three only when it spans unrelated parts of the code). Each must be "
                "answerable by reading {{source}} — including the source code of tools "
                "and libraries the change depends on, not only the files it changes — "
                "or by running something there. Spawn "
                "one subagent per question and have each write its findings to "
                "`research/<slug>.md` under the run directory, then write "
                "`research-plan.json` yourself.\n\n"
                "## What was wrong with the last plan\n{{research_review}}\n\n"
                "## The plan's shape\n" + RESEARCH_SCHEMA
            ),
            reads=("brief.json", "research-review.txt"),
            writes=("research-plan.json",),
            produces=("research",),
            notes="choose the research directions and have a subagent answer each one",
        ))
        # The same pair the create graph carries, for the same reason: the plan's
        # `writes` contract is satisfied by any bytes, so the stage that grounds the
        # design could narrate its intent and pass.
        graph.add(Node(
            "record_findings", "op", call="refactory_dsh.create_ops:record_findings",
            reads=("research-plan.json",),
            writes=("research/",),
            produces=("findings_recorded",),
            notes="write each direction's finding where the reviews will look for it",
        ))
        graph.add(Node(
            "gate_research", "gate", evaluator="refactory_dsh.create_ops:gate_research",
            reads=("research-plan.json",),
            writes=("research-review.txt",),
            produces=("research_review", "research_attempts_used"),
            knobs=("research_attempts",),
            max_iterations=8,
            notes="proceed only when the plan is a plan and its findings exist",
        ))
        graph.add(Node(
            "pitfalls_researcher", "agent",
            prompt_template=(
                "Artifact name: {{name}}\nBrief: {{brief}}\n"
                "Repository: {{source}}\n\n"
                "## Research scope\n{{research_scope}}\n\n"
                "## What the research found\n{{research}}\n\n"
                "## What was wrong with the last attempt\n{{pitfalls_review}}\n\n"
                "You are the pitfalls researcher. Before any strategy or design exists, "
                "identify the risks a correct design must account for. Read the repository "
                "and the brief, and produce a risk-ranked list of pitfalls. For a scoped "
                "change, list at most five: only the risks a correct design of this "
                "change must account for.\n\n"
                "For each pitfall, provide:\n"
                "- **id**: a short identifier in the format PIT-001, PIT-002, etc.\n"
                "- **risk**: high, medium, or low.\n"
                "- **category**: exactly one of:\n"
                "  - `pinning_tests` — tests that pin current behaviour. Name the file, "
                "the line, and the exact assertion text. Say whether the change must keep "
                "it or deliberately change it.\n"
                "  - `repo_conventions` — the repository's own rules and conventions, "
                "taken from its code, docs and agent instructions (CLAUDE.md, AGENTS.md, "
                "CONTRIBUTING, or similar) with citations. Never use a built-in list.\n"
                "  - `shared_state` — other writers and readers of the files, environment "
                "variables, HOME, locks, and processes the change touches.\n"
                "  - `callers` — callers of the code being changed, and the user paths "
                "each serves.\n"
                "  - `imitated_steps` — the existing step the change imitates, and the "
                "settings it reads.\n"
                "  - `scope_boundaries` — modes and paths that must not change.\n"
                "- **evidence**: one of these forms:\n"
                "  - `file:line` \u2014 cite a file and line number from the repository (for code that exists).\n"
                "  - `absent: <path or glob>` \u2014 when the pitfall is about something missing (no tests, no config file). A glob like `tests/test_*.py` is accepted. The gate verifies the path or glob does NOT match anything in the worktree.\n"
                "  - `brief.json` or `research/<slug>.md` \u2014 cite a run artifact when evidence comes from the brief or research, not from repository code.\n"
                "- **prescription**: what a correct design must do about it.\n\n"
                "Sort by risk (high first). Write `pitfalls.json` as a JSON array of "
                "these objects. Then write `pitfalls.md` as a human-readable rendering "
                "of the same list. Do not read strategy.json or design.md — this step "
                "is independent of them."
            ),
            reads=("brief.json", "research-plan.json"),
            writes=("pitfalls.json", "pitfalls.md"),
            produces=("pitfalls",),
            notes="identify risk-ranked pitfalls before strategy, independent of any design",
        ))
        graph.add(Node(
            "gate_pitfalls", "gate", evaluator=f"{OPS}:gate_pitfalls",
            reads=("pitfalls.json",),
            writes=("pitfalls-review.txt",),
            produces=("pitfalls_review", "pitfalls_attempts_used"),
            knobs=("pitfalls_attempts",),
            max_iterations=8,
            notes="proceed only when pitfalls.json is valid; reloop to pitfalls_researcher otherwise",
        ))
        # A scoped change skips the perspectives step and, below, the design-review
        # agent: both runtimes route on these gates' labels (`scoped`, or `proceed`).
        graph.add(Node(
            "route_strategy", "gate", evaluator=f"{OPS}:route_strategy",
            writes=("strategy-plan.json", "strategy/"),
            produces=("strategy_plan",),
            max_iterations=8,
            notes="a scoped change is planned straight from the research; a full design weighs perspectives",
        ))
        graph.add(Node(
            "strategy_director", "agent",
            prompt_template=(
                "Artifact name: {{name}}\nBrief: {{brief}}\n"
                "It is built in {{source}}; read what is there before choosing.\n\n"
                "## Pitfalls\n{{pitfalls}}\n\n"
                "## What the research found\n{{research}}\n\n"
                "## Research scope\n{{research_scope}}\n\n"
                "You are the strategy director. From the research, choose between two "
                "and five perspectives on how this artifact could be built — including "
                "the simplest thing that could pass the check, and one that says what "
                "the check will not catch. Spawn one subagent per perspective and have "
                "it write `strategy/<slug>.md` under the run directory, with each "
                "finding as a heading `### FND-<slug>-<n> \u2014 <the finding>`, numbered "
                "from 1 within the file. Then write "
                "`strategy-plan.json` as an array of `{\"perspective\", \"slug\", "
                "\"intent_items\", \"ids\"}` objects, where `intent_items` lists the "
                "asks from the brief that perspective covers and `ids` lists the "
                "`FND-<slug>-<n>` ids from that perspective's file. Every ask must "
                "be covered."
            ),
            reads=("brief.json", "research-plan.json", "pitfalls.json"),
            writes=("strategy-plan.json", "strategy/"),
            produces=("strategy_plan",),
            notes="choose the perspectives and record what each one covers",
        ))
        graph.add(Node(
            "synthesize_strategy", "agent",
            prompt_template=(
                "Artifact name: {{name}}\nBrief: {{brief}}\nIt is built in {{source}}.\n"
                "The check, run from {{check_root}}:\n    {{check}}\n\n"
                "## Pitfalls\n{{pitfalls}}\n\n"
                "## What the research found\n{{research}}\n\n"
                "## The perspectives\n{{strategy_plan}}\n\n"
                + _ACCEPTANCE_BASELINE
                + "Compile the perspectives into one plan: what will be built, which files "
                "it needs, how the check will pass, and what is deliberately out of "
                "scope with the user's agreement. Every remaining ask in the brief must "
                "appear in the plan; an unmet ask remains unresolved. "
                "For each ask distinguish what the supplied check establishes from "
                "what still needs independent evidence. Plan executable semantic checks "
                "for the gaps; a successful command or an existing output file does not "
                "establish that the output means what the user requested. For exchange "
                "formats, plan acceptance by an established consuming application or "
                "independent format reader, not just a parser the artifact author writes."
                "\n\nThe research detail files (`research/*.md`) and strategy detail "
                "files (`strategy/*.md`) are available as reads. Each carries finding "
                "ids (`FND-<slug>-<n>`). Use the findings that change what will be built, "
                "citing their ids; the rest stay in those files for the design to read."
            ),
            reads=("brief.json", "research-plan.json", "strategy-plan.json", "pitfalls.json",
                   "research/", "strategy/"),
            writes=("strategy.json",),
            produces=("strategy",),
            notes="compile the perspectives into one plan",
        ))
        # design-v2's `design_doc`: the design is a document of its own, written
        # before the code and approved before anything is built.
        graph.add(Node(
            "design_doc", "agent",
            prompt_template=(
                "Artifact name: {{name}}\nBrief: {{brief}}\nIt is built in {{source}}.\n"
                "The check, run from {{check_root}}:\n    {{check}}\n\n"
                "## The plan\n{{strategy}}\n\n"
                "## Pitfalls\n{{pitfalls}}\n\n"
                "## What the design review found\n{{design_review_feedback}}\n\n"
                + _ACCEPTANCE_BASELINE
                + "Write the design document for this artifact: every file it will "
                "add or change, what each one does, the interfaces between them, and how the "
                "check exercises them. Be concrete — the next step writes the code from "
                "this document and the brief. Specify accepted input syntax and unambiguous "
                "rejection behavior: unsupported counts, signs, units, or other values "
                "must not silently become different valid requests. For visual output, "
                "plan a rendered inspection as well as format/geometry checks, including "
                "labels, margins, clipping, and agreement with the requested dimensions.\n\n"
                "Make exactly one decision for each design choice — do not present "
                "alternatives, do not end a choice with 'a simpler alternative…' or leave "
                "it open. A choice left open here becomes an unreviewed decision in the "
                "code. Cite every claim about existing code with its file path and line "
                "number or range (e.g. `path/to/file.py:42` or `path/to/file.py:42-58`). "
                "When claiming what the brief says, quote the brief's words verbatim. "
                "The document holds only the final result — no 'Wait—', 'Actually', "
                "'Let me reconsider' or other working-out.\n\n"
                "For claims about existing code, use the subject-at-citation-quote form: "
                "`name()` at `path/to/file.py:42`: `def name(self):`. The subject "
                "(the backtick span before `at`) names the function, attribute or class "
                "the claim is about; the quote (the backtick span after `:`) is the line "
                "content at that position. This form is mechanically checked before the "
                "design review runs. Bare citations without a quoted line (for files "
                "being added, or site markers) are fine as-is.\n\n"
                + design_contract.prompt_contract()
                + "\n\n"
                "9. `## Anti-patterns to avoid` (when the research produced pitfalls) — "
                "for each high-risk pitfall id, either address it by naming the site (a "
                "backticked `path:line` from Rule and sites) or the section (§ heading "
                "name) that handles it, or set it aside with `[set-aside: <reason>]`. A "
                "high-risk pitfall that is missing or set aside without a reason fails "
                "the contract. Medium and low pitfalls appear only where they change a "
                "decision.\n\n"
                "10. Research and perspective findings \u2014 read research/*.md and "
                "strategy/*.md directly, and cite a finding id (FND-<slug>-<n>) where it "
                "changes a decision. Do not list findings that change nothing; the "
                "document is for building the change, not for accounting for the "
                "research.\n"
                "\n\n## Claims check feedback\n{{claims_check_feedback}}"
            ),
            reads=("brief.json", "strategy.json", "reviews/design-review-feedback.txt",
                   "pitfalls.json", "research/", "strategy/"),
            writes=("design.md",),
            produces=("design",),
            notes="write the design document the code is written from",
        ))
        graph.add(Node(
            "route_design_review", "gate", evaluator=f"{OPS}:route_design_review",
            produces=("design_review_by",),
            max_iterations=8,
            notes="a scoped change's design is checked automatically; a full design is reviewed",
        ))
        graph.add(Node(
            "check_design_claims", "gate",
            evaluator="refactory_dsh.design_claims:check_design_claims",
            reads=("design.md",),
            writes=("reviews/design-review-feedback.txt",),
            produces=("claims_check_attempts_used", "claims_check_feedback",
                      "design_review_feedback"),
            knobs=("claims_check_attempts",),
            max_iterations=4,
            notes="deterministic check: cited paths exist, quoted lines match, "
                  "named identifiers are present; reloop to design_doc on mismatch",
        ))
        # design-v2 puts the person's approval on the design document, after the plan
        # exists and before any code does.
        # The approval covers delivery too, which is the only step that reaches outside
        # the run: delivery.md says which pull request it opens, or which repository it
        # creates, and the approval is scoped to that file as it is to design.md.
        graph.add(Node(
            "design_review", "agent",
            prompt_template=(
                "## Pitfalls\n{{pitfalls}}\n\n"
                "For every high-risk pitfall, confirm the design accounts for it in its "
                "Anti-patterns section — either addressed with a site or section that "
                "handles it, or set aside with a reason. A high pitfall that is missing "
                "from the Anti-patterns section is BLOCKING.\n\n"
                "Artifact name: {{name}}\nBrief: {{brief}}\n"
                "Source: {{source}}\n\n"
                "## Previous feedback\n{{design_review_feedback}}\n\n"
                "Read the design document from `design.md` in the run directory. "
                "Read the brief from `brief.json` in the run directory.\n\n"
                "For every claim the design makes about existing code, read the "
                "cited file and line range in {{source}} and confirm the claim "
                "matches what is there. For every claim the design makes about "
                "what the brief says, find the quoted text verbatim in the brief. "
                "For every design choice, confirm that exactly one decision is made "
                "and no alternative is left open.\n\n"
                "Report as BLOCKING only what would make the build wrong or leave an ask "
                "unmet:\n"
                "- An open alternative or a choice left undecided.\n"
                "- A claim about code you cannot confirm in the cited file.\n"
                "- A claim about the brief the brief does not say (a difference only in "
                "punctuation or formatting is a note, not a finding).\n"
                "- A command, flag or file the design tells a person or the builder "
                "to use that does not exist.\n"
                "- A piece the design adds that nothing in the design uses or starts.\n"
                "- A planned test that cannot fail when the code is wrong, such as one "
                "that asserts on values it built itself, or reads through a stand-in "
                "the real code would not provide.\n"
                "- A new mechanism where the code already has one that does the job, "
                "naming the existing one.\n"
                "- A place in the code that applies or restates the same rule the design "
                "changes, found by searching the source, that the design leaves unchanged "
                "without giving a reason, or does not list at all in '## Rule and sites'. "
                "Search every caller of a changed function in every mode and graph, not "
                "only the brief's own path.\n"
                "- An ask in the brief with no row in the Ask mapping table, or whose "
                "named test does not exercise it.\n\n"
                "List each finding as a line starting with BLOCKING: <path>: <description> where <path> is the file the builder must change to fix the problem. " + MARKER_RULE + "\n\n"
                "Write anything else under `## Notes`, never as BLOCKING: a doc the "
                "'## Docs' section leaves out, a missing or thin Failure trace, a "
                "quotation that differs only in punctuation or formatting, and style or "
                "structure that does not change the build. Notes do not send the design "
                "back.\n\n"
                "{{review_instructions}}\n\n"
                "Trace the brief's own example through the code in time order, naming the route a "
                "real run takes for every runtime that runs it. Write the trace in a section headed "
                "`## Brief example trace`, with path:line citations for each step.\n\n"
                "End with a line that is exactly `VERDICT: PASS` when there are "
                "no blocking findings, or `VERDICT: FAIL` when there are any."
            ),
            reads=("brief.json", "design.md", "reviews/design-review-feedback.txt",
                   "pitfalls.json"),
            writes=("reviews/design-review.md",),
            produces=("design_review_text",),
            notes="review the design for open alternatives and unconfirmed claims",
        ))
        graph.add(Node(
            "gate_design_review", "gate",
            evaluator=f"{OPS}:gate_design_review",
            reads=("reviews/design-review.md", "design.md", "brief.json",
                   "pitfalls.json", "research/", "strategy/"),
            writes=("reviews/design-review-feedback.txt",),
            produces=("design_review_feedback", "design_review_attempts_used",
                      "design_review_findings", "pitfall_coverage",
                      "finding_coverage", "design_review_status",
                      "design_review_advisory", "design_review_blocking"),
            knobs=("design_review_attempts",),
            max_iterations=6,
            notes="proceed on PASS with no findings; reloop to design_doc otherwise; "
                  "never fail the run",
        ))
        graph.add(Node(
            "gate_strategy", "gate", gate_kind="user",
            reads=("design.md", "delivery.md"),
            max_iterations=4,
            # `{{delivery}}` is filled from the run's facts wherever the question is put
            # (the spine's question card, the panel, the CEO's view), so the card itself
            # names the repository a run would create, and who can see it.
            notes=(
                "{{design_review_status}}\n\n"
                "Approve the design before the artifact is written? It is in design.md "
                "in the run directory. Then, {{delivery}}; delivery.md there says more. "
                "Answer proceed to build and deliver it that way, or reloop to have the "
                "plan redone.\n\n"
                "## Unresolved design review findings\n{{design_review_findings}}"
            ),
        ))
        graph.add(Node(
            "emit", "agent",
            prompt_template=(
                "Write the artifact `{{name}}` so that it is complete and runnable.\n\n"
                "Brief: {{brief}}\n"
                "It will be checked by running: {{check}}\n\n"
                "## The design\nRead the approved design from `design.md` in the run directory.\n\n"
                "## What went wrong last time\n{{validation}}\n\n"
                "## What the reviews refused\n{{qa_gate}}\n{{overwatch_review}}\n\n"
                + _ACCEPTANCE_BASELINE
                + "{{emit_scope}}\n\n"
                "Write those blocks into emit.txt, with no prose outside them. Write nothing "
                "but emit.txt: do not apply or test your change anywhere — the target, the "
                "run's worktree or a copy — because materialise and the check do that after "
                "you answer and send you the result. Then return a short completion message.\n"
                "{{probe_instruction}}\n"
            ),
            reads=("brief.json", "design.md", "check.txt", "check-record.json"),
            writes=(repo_change.EMIT_FILE,),
            produces=("emit",),
            feedback_keys=("validation", "qa_gate", "overwatch_review"),
            notes="write the artifact's files",
        ))
        graph.add(repo_change.materialise_node(
            writes=("changes.diff", "changed-functions.json"),
            produces=("artifact", "files", "commit", "change", "emission_refused",
                      "changed_functions", "consecutive_refusals"),
            knobs=("refusal_attempts",),
        ))
        # ── the check, beside the reviews ──
        # prepare_check settles the commit the check and the reviewers both see; the
        # check runs as a branch of the review fork and records its decision; gate_check
        # routes on it once the reviews are done, because a node inside a fork may not
        # send the run outside it.
        check_produces = ("validation", "check_attempts_used", "review_attempts_used",
                          "attempts_used", "runtime_evidence", "base_unavailable",
                          "check_failures_history", "check_blocker_history",
                          "convergence_extras_used", "convergence_granted", "inherited_failures")
        check_writes = ("check.txt", "validation.txt", "authoring-evidence.json",
                        "check-record.json", "test-guardedness.json", CHECK_VERDICT)
        check_knobs = ("check_attempts", "check_timeout_s", "check_attempt_cap")
        graph.add(Node(
            "prepare_check", "gate", evaluator=f"{OPS}:prepare_check", role="validation",
            resource_role="final_validation",
            reads=("brief.json",),
            writes=(*check_writes, *repo_change.CHECK_WRITES, CHECK_PREP),
            produces=(*check_produces, *repo_change.CHECK_PRODUCES,
                      "review_feedback", "review_baseline"),
            knobs=check_knobs, max_iterations=8,
            notes="settle the commit the check and the reviews see, and rerun last "
                  "attempt's failing tests",
        ))
        graph.add(Node(
            "check", "op", call=f"{OPS}:check_beside_reviews", role="validation",
            resource_role="final_validation",
            reads=("brief.json", CHECK_PREP),
            writes=check_writes, produces=check_produces,
            knobs=check_knobs, max_iterations=8,
            notes="run the brief's check, in a sandbox, beside the reviews",
        ))
        graph.add(Node(
            "gate_check", "gate", evaluator=f"{OPS}:gate_check", role="validation",
            resource_role="final_validation",
            reads=(CHECK_PREP, CHECK_VERDICT),
            # It runs the whole check itself when no decision stands for this attempt.
            writes=(*check_writes, *repo_change.CHECK_WRITES),
            produces=(*check_produces, *repo_change.CHECK_PRODUCES),
            knobs=check_knobs, max_iterations=8,
            notes="send a failed check back to the builder; otherwise read the reviews",
        ))

        # ── design-v2's three reviews, in parallel, and the overwatch pass ──
        from refactory_dsh.verification import VERIFIER_PROMPT, VERIFIER_TIMEOUT_S
        graph.add(Node(
            "verifier", "agent",
            prompt_template=VERIFIER_PROMPT + BESIDE_CHECK_VERIFIER_NOTE,
            timeout_s=VERIFIER_TIMEOUT_S,
            # Not test-guardedness.json: the check beside it writes that file.
            reads=("brief.json", "changes.diff"),
            writes=("reviews/verification.md",),
            produces=("verification",),
            role="code_reviewer",
            resource_role="final_validation",
            notes="design-blind verification: apply the evidence log's methods "
                  "to the brief, diff and repo",
        ))
        graph.add(Node("fork_qa", "fork",
                       targets=("health_checker", "code_reviewer", "qa_director", "verifier",
                                "check"),
                       optional_targets=("verifier",),
                       notes="start the three reviews, the verifier and the check"))
        graph.add(Node(
            "health_checker", "agent",
            prompt_template=(
                "Artifact directory (absolute): {{artifact}}\n"
                "Read and run the delivered files at this exact path; do not infer it "
                "from your working directory. {{change}}\n\n"
                "Brief: {{brief}}\nThe check it must pass: {{check}}\n\n"
                "## The design\nRead the approved design from `design.md` in the run directory.\n\n"
                + _ACCEPTANCE_BASELINE
                + "You are the health checker. Run the artifact the way a user would: run "
                "targeted checks for uncovered requirements. The check itself runs beside "
                "this review, so do not run it again. Record every command and "
                "its exact output in `reviews/health-check.md`. "
                "Anything that fails or is "
                "missing is written as BLOCKING: <path>: <description> where <path> is the file the builder must change to fix the problem. A defect in the code is reported against the code file, even when the requirement it misses comes from design.md or the brief. A problem in a run document (design.md, strategy.json, or another file an earlier step wrote) is reported with that document's path. A "
                "check you did not run is not a check that passed.\n"
                + SEVERITY_RULE + " " + MARKER_RULE + "\n"
                "{{review_instructions}}\n"
                "Changed functions:\n{{changed_functions}}\n\n"
            ),
            reads=("brief.json", "design.md", "changed-functions.json"),
            writes=("reviews/health-check.md",),
            produces=("health_check",),
            notes="run the artifact and record what happened, command by command",
        ))
        graph.add(Node(
            "code_reviewer", "agent",
            prompt_template=(
                "## Pitfalls\n{{pitfalls}}\n\n"
                "Check the built change against every high-risk pitfall. A high pitfall "
                "the code contradicts is BLOCKING.\n\n"
                "Artifact directory (absolute): {{artifact}}\n"
                "Read and run the delivered files at this exact path; do not infer it "
                "from your working directory. {{change}}\n\n"
                "Brief: {{brief}}\n\n"
                "## The design\nRead the approved design from `design.md` in the run directory.\n\n"
                "## The emitted source\nRead what was emitted from `emit.txt` in the run directory. "
                "In a worktree it may edit or delete files rather than write them whole, so judge "
                "the result: the files at the artifact path, and the diff named above.\n\n"
                + _ACCEPTANCE_BASELINE
                + "You are the code reviewer. Read the delivered files against the design "
                "and the brief: is every file the design names present, does it do what "
                "the design says, and would it run? Trace input values through parsing "
                "to the delivered artifact: count/role binding, signs, units, and "
                "unsupported input must preserve the request or explicitly reject it. "
                "A matching number elsewhere in prose is not evidence for the requested "
                "field. Record findings in "
                "`reviews/code-review.md`. A defect that would stop the artifact working "
                "is written as BLOCKING: <path>: <description> where <path> is the file the builder must change to fix the problem. A defect in the code is reported against the code file, even when the requirement it misses comes from design.md or the brief. A problem in a run document (design.md, strategy.json, or another file an earlier step wrote) is reported with that document's path.\n"
                + SEVERITY_RULE + " " + MARKER_RULE + "\n"
                "{{review_instructions}}\n"
                "Changed functions:\n{{changed_functions}}\n\n"
                "When a false claim about design.md is found, report it against the code file "
                "that relies on the claim, not against design.md, because BLOCKING: design.md "
                "is downgraded by scope_filter_findings.\n"
            ),
            reads=("brief.json", "design.md", "emit.txt", "pitfalls.json",
                   "changed-functions.json"),
            writes=("reviews/code-review.md",),
            produces=("code_review",),
            notes="review the emitted files against the design",
        ))
        graph.add(Node(
            "qa_director", "agent",
            prompt_template=(
                "Artifact directory (absolute): {{artifact}}\n"
                "Read and run the delivered files at this exact path; do not infer it "
                "from your working directory. {{change}}\n\n"
                "Brief: {{brief}}\nThe check it must pass: {{check}}\n\n"
                "## The approved design\nRead the approved design from `design.md` in the run directory.\n\n"
                "## The plan\nRead the synthesis strategy from `strategy.json` in the run directory.\n\n"
                + _ACCEPTANCE_BASELINE
                + "You are the QA director. Design adversarial checks on the artifact — "
                "the cases a friendly reviewer would not try — and run them. Vary one "
                "semantic property at a time and inspect the actual output, not just "
                "the exit code. Include conflicting nearby numbers, unsupported counts, "
                "and alternative sign/unit encodings when the interface parses them. "
                "Accepted output must match the request; unsupported input must fail "
                "clearly without silently substituting a valid case. Write "
                "`reviews/qa.md` with each check and its exact result. Anything that "
                "breaks an ask in the brief is written as BLOCKING: <path>: <description> where <path> is the file the builder must change to fix the problem. A defect in the code is reported against the code file, even when the requirement it misses comes from design.md or the brief. A problem in a run document (design.md, strategy.json, or another file an earlier step wrote) is reported with that document's path.\n"
                + SEVERITY_RULE + " " + MARKER_RULE + "\n"
                "{{review_instructions}}\n"
                "Changed functions:\n{{changed_functions}}\n\n"
            ),
            reads=("brief.json", "design.md", "strategy.json",
                   "changed-functions.json"),
            writes=("reviews/qa.md",),
            produces=("qa_review",),
            notes="attack the artifact against the brief",
        ))
        graph.add(Node("join_qa", "join",
                       sources=("check", "health_checker", "code_reviewer", "qa_director"),
                       notes="wait for the check and all three reviews"))
        graph.add(Node(
            "synthesize_qa", "op", call="refactory_dsh.create_ops:synthesize_qa",
            reads=("reviews/health-check.md", "reviews/code-review.md", "reviews/qa.md",
                   "reviews/verification.md"),
            writes=("reviews/qa-synthesized.md",),
            produces=("qa", "qa_blocking", "qa_missing"),
            notes="merge the three reviews; an absent review is not a passed one",
        ))
        graph.add(Node(
            "gate_qa", "gate", evaluator="refactory_dsh.create_ops:gate_qa",
            reads=("reviews/qa-synthesized.md", "reviews/health-check.md",
                   "reviews/code-review.md", "reviews/qa.md",
                   "reviews/verification.md", "emit.txt",
                   "changed-functions.json"),
            writes=("reviews/qa-gate.txt", "reviews/verification-unresolved.md",
                    "reviews/finding-sources.json", "reviews/follow-ups.json", "reviews/follow-ups.md"),
            produces=("qa_gate",),
            max_iterations=5,
            notes="proceed when every review ran and none found a core defect; "
                  "non-core findings become follow-ups; verifier findings are advisory",
        ))
        graph.add(Node(
            "overwatch", "agent",
            prompt_template=(
                "Artifact directory (absolute): {{artifact}}\n"
                "Read and run the delivered files at this exact path; do not infer it "
                "from your working directory. {{change}}\n\n"
                "Brief: {{brief}}\nThe check it must pass: {{check}}\n\n"
                "## The design\nRead the approved design from `design.md` in the run directory.\n\n"
                "## The merged reviews\nRead the file `reviews/qa-synthesized.md` in the run directory. It contains the merged reviews from the health checker, code reviewer, and QA director.\n\n"
                "## What the last audit was refused for\n{{overwatch_review}}\n\n"
                + _ACCEPTANCE_BASELINE
                + "You are the overwatch. Audit the reviews rather than repeating them: "
                "take each ask in the brief and say what evidence proves it — a command "
                "and its output — or that nothing does. Run your own spot checks on what "
                "the reviews claim passed. Do not promote a syntax check, file-presence "
                "check, self-report, or label-string check into semantic or visual "
                "evidence. Required behavior without adequate evidence remains blocking, "
                "including required visual usability without rendered inspection. "
                "For required exchange-format output, require actual consumer/reader "
                "acceptance; distinguish an ordinary successful import from recovery "
                "that repairs the file or discards entities. Recovery-only acceptance "
                "does not meet ordinary interchange requirements unless the brief "
                "explicitly permits that repair. If required validation is unavailable, "
                "preserve the artifact and existing evidence, identify the missing "
                "external dependency and exact unblock action for the CEO or person, "
                "and keep that requirement unverified. Do not prescribe re-authoring "
                "or repeating an unchanged probe to resolve an unavailable tool. "
                "Any blocking finding is written as BLOCKING: <path>: <description> where <path> is the file the builder must change to fix the problem. A defect in the code is reported against the code file, even when the requirement it misses comes from design.md or the brief. A problem in a run document (design.md, strategy.json, or another file an earlier step wrote) is reported with that document's path. "
                + SEVERITY_RULE + " " + MARKER_RULE + " " + OVERWATCH_VERDICT_RULE + " "
                "Read reviews/verification-unresolved.md if it exists: the design-blind "
                "verifier's findings, each with the builder's rebuttal where there is one. "
                "Check each against the code. One that breaks an ask in the brief or existing "
                "behaviour, and is not rebutted with a cited file:line, is your own BLOCKING "
                "finding tagged `[core]`; the rest go to the pull request.\n\n"
                "Write `reviews/overwatch.md` ending with a "
                "line that is exactly `VERDICT: PASS` or `VERDICT: FAIL`, and name every "
                "ask with no evidence behind it."
            ),
            # verification-unresolved.md is read when present (the prompt says so) and
            # not declared: a declared input must exist on the spine, and it does not
            # in a run whose verifier found nothing.
            reads=("brief.json", "design.md", "reviews/qa-synthesized.md", "test-guardedness.json"),
            writes=("reviews/overwatch.md",),
            produces=("overwatch",),
            notes="audit the evidence behind the reviews",
        ))
        graph.add(Node(
            "gate_overwatch", "gate", evaluator="refactory_dsh.create_ops:gate_overwatch",
            reads=("reviews/overwatch.md",),
            writes=("reviews/overwatch-gate.txt", "reviews/follow-ups.json", "reviews/follow-ups.md"),
            produces=("overwatch_review",),
            max_iterations=4,
            notes="proceed on a stated PASS verdict with no core finding; "
                  "non-core findings become follow-ups",
        ))
        graph.add(Node(
            "report", "op", call=f"{OPS}:report_artifact",
            writes=("design.json",),
            produces=("designed",),
            notes="summarise what was designed",
        ))
        # After the report, so only a run whose reviews passed and whose sign-offs held
        # reaches anything outside it (#350).
        graph.add(Node(
            "pick_corrections", "gate", evaluator=f"{OPS}:pick_gate_corrections",
            produces=("gate_corrections", "design_corrections_applied"),
            max_iterations=2,
            notes="deterministically extract gate_strategy corrections from the ledger; "
                  "skip the agent when there are none",
        ))
        graph.add(Node(
            "apply_corrections", "agent",
            prompt_template=(
                "Read `design.md` from the run directory.\n\n"
                "The following gate corrections were given when the design was approved:\n\n"
                "{{gate_corrections}}\n\n"
                "Update `design.md` so that every part of it the corrections touch says what "
                "they say: the ## Summary, the heading (H1), each site entry in ## Rule and "
                "sites, and in ## Changes and ## Docs any wording, example text or file "
                "list a correction replaces or adds to. Leave no part stating what a "
                "correction overrides, because the builder reads the whole document. Keep "
                "the sections the corrections do not touch as they are, and keep the "
                "document's section headings and structure. Write the updated `design.md`. "
                "Return a short message listing what was changed."
            ),
            reads=("design.md",),
            writes=("design.md",),
            notes="revise design.md with gate-strategy corrections before the builder runs",
        ))
        graph.add(Node(
            "deliver", "op", call="refactory_dsh.delivery:deliver_design",
            resource_role="final_validation",
            knobs=("check_timeout_s",),
            reads=("design.json", "delivery.md", "reviews/follow-ups.json"),
            writes=("delivery.json", "pull-request.md", "design.json"),
            produces=("delivery_result", "pull_request", "delivery_conflict_files",
                      "delivery_conflict_detail", "delivery_target_tip", "commit"),
            notes="push the run's branch and open its pull request, as the approval gate said",
        ))
        graph.add(Node(
            "delivery_gate", "gate",
            evaluator="refactory_dsh.delivery:evaluate_delivery_conflicts",
            max_iterations=8,
            knobs=("check_attempts",),
            produces=("validation", "delivery_conflict_reloops"),
            notes="on authored-file conflict reloop to emit; otherwise proceed to done",
        ))
        graph.add(Node(
            "done", "op", call="refactory_dsh.design_ops:noop",
            notes="terminal node after delivery succeeds",
        ))

        graph.connect("prepare", "research_director")
        graph.connect("research_director", "record_findings")
        graph.connect("record_findings", "gate_research")
        graph.connect("gate_research", "research_director", condition="reloop")
        graph.connect("gate_research", "pitfalls_researcher", condition="proceed")
        graph.connect("pitfalls_researcher", "gate_pitfalls")
        graph.connect("gate_pitfalls", "pitfalls_researcher", condition="reloop")
        graph.connect("gate_pitfalls", "route_strategy", condition="proceed")
        graph.connect("route_strategy", "strategy_director", condition="proceed")
        graph.connect("route_strategy", "synthesize_strategy", condition="scoped")
        graph.connect("strategy_director", "synthesize_strategy")
        graph.connect("synthesize_strategy", "design_doc")
        graph.connect("design_doc", "check_design_claims")
        graph.connect("check_design_claims", "route_design_review", condition="proceed")
        graph.connect("route_design_review", "design_review", condition="proceed")
        graph.connect("route_design_review", "gate_design_review", condition="scoped")
        graph.connect("check_design_claims", "design_doc", condition="reloop")
        graph.connect("design_review", "gate_design_review")
        graph.connect("gate_design_review", "design_doc", condition="reloop")
        graph.connect("gate_design_review", "gate_strategy", condition="proceed")
        graph.connect("gate_strategy", "synthesize_strategy", condition="reloop")
        graph.connect("gate_strategy", "pick_corrections", condition="proceed")
        graph.connect("pick_corrections", "apply_corrections", condition="proceed")
        graph.connect("pick_corrections", "emit", condition="skip")
        graph.connect("apply_corrections", "emit")
        graph.connect("emit", "materialise")
        graph.connect("materialise", "prepare_check")
        graph.connect("prepare_check", "emit", condition="reloop")
        graph.connect("prepare_check", "fork_qa", condition="proceed")
        graph.connect("fork_qa", "join_qa")
        graph.connect("verifier", "join_qa")
        graph.connect("join_qa", "gate_check")
        graph.connect("gate_check", "emit", condition="reloop")
        graph.connect("gate_check", "synthesize_qa", condition="proceed")
        graph.connect("synthesize_qa", "gate_qa")
        graph.connect("gate_qa", "emit", condition="reloop")
        graph.connect("gate_qa", "overwatch", condition="proceed")
        graph.connect("overwatch", "gate_overwatch")
        graph.connect("gate_overwatch", "emit", condition="reloop")
        graph.connect("gate_overwatch", "overwatch", condition="audit")
        graph.connect("gate_overwatch", "report", condition="proceed")
        graph.connect("report", "deliver")
        graph.connect("deliver", "delivery_gate")
        graph.connect("delivery_gate", "emit", condition="reloop")
        graph.connect("delivery_gate", "done", condition="proceed")
        from refactory_dsh.authoring_evidence import wire_reviews
        wire_reviews(graph, check_beside=True)
        return graph

    def invariants(self) -> tuple[Any, ...]:
        return ()


def main(argv: list[str] | None = None) -> int:
    """``python -m refactory_dsh.design --name <n> --brief <text> --check <cmd>``."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m refactory_dsh.design",
        description="Design, emit and check a software artifact from a brief.",
    )
    parser.add_argument("--name", required=False, default="", help="the artifact's name")
    parser.add_argument("--brief", required=False, default="", help="what to build")
    parser.add_argument("--check", required=False, default="",
                        help="the command that decides whether it works")
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument("--fast", action="store_true", help="use the inspectable design_fast graph with focused QA")
    parser.add_argument("--repair-attempts", type=int, default=1, help="fast graph repair limit (0..3)")
    parser.add_argument("--source-project", default="",
                        help="fast design: copy this directory and apply the change there, instead of a worktree")
    parser.add_argument("--target", default="", help="the git repository to change (default: a new project)")
    parser.add_argument("--base", default="", help="the commit the run starts from (default: the target's HEAD)")
    parser.add_argument("--no-worktree", action="store_true",
                        help="write the artifact under .factory/artifacts instead of a worktree")
    parser.add_argument("--setup", default="", help="a command run in the worktree before each check, such as a build")
    parser.add_argument("--generate", default="",
                        help=repo_change.knobs()["generate"].description)
    parser.add_argument("--no-pr", action="store_true",
                        help="keep the work on the run's local branch instead of opening a pull request")
    parser.add_argument("--refine", default="", metavar="PR",
                        help="follow up an open pull request: start from its branch and update it")
    parser.add_argument("--review-comments", action="store_true",
                        help="with --refine: add the pull request's reviews and comments to the steering")
    from refactory_dsh.budgets import add_arguments, arguments

    add_arguments(parser)
    # Per artifact name, for the same reason a creation is: one fixed directory
    # means runs overwrite each other's state.
    parser.add_argument("--workdir", default="")
    parser.add_argument(
        "--resume", default="", metavar="RUN_DIR",
        help="continue a failed run from its own directory instead of starting a "
             "new one; every node that already finished is kept",
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--provider", default="", help="provider route that model is on")
    parser.add_argument(
        "--model-timeout", type=int, default=DEFAULT_TIMEOUT_S, dest="model_timeout_s",
        help="seconds one model completion may take before it is called hung",
    )
    parser.add_argument(
        "--verdict", action="append", default=[], metavar="GATE=VALUE",
        help="answer a gate a person owns, as <gate>=<value> (repeatable)",
    )
    args = parser.parse_args(argv)
    if not args.resume:
        if not args.name:
            parser.error("--name is required when not resuming")
        if not args.brief:
            parser.error("--brief is required when not resuming")
    delivered = [flag for flag, given in (("--no-pr", args.no_pr), ("--refine", args.refine),
                                          ("--review-comments", args.review_comments)) if given]
    if args.fast and delivered:
        parser.error(f"--fast does not take {', '.join(delivered)}: design_fast has no approval gate, so it "
                     "opens no pull request, and its work stays on the run's branch")

    from refactory_dsh.model import DshModelClient
    from refactory_dsh.paths import project_root

    if args.resume:
        workdir = Path(args.resume)
        # Read fast from run-config.json when available.
        fast = args.fast
        config_path = workdir / '.refactory/run-config.json'
        if config_path.exists():
            try:
                run_config = json.loads(config_path.read_text())
                fast = run_config.get('fast', fast)
            except (json.JSONDecodeError, OSError):
                pass
        client = DshModelClient(
            project=None,
            timeout_s=int(args.model_timeout_s) if args.model_timeout_s else DEFAULT_TIMEOUT_S,
            model=args.model, provider=args.provider,
        )
        from refactory_dsh.run_ownership import ownership

        try:
            with ownership(workdir):
                from refactory_dsh.fast import DesignFastMode

                run = (DesignFastMode if fast else DesignMode).resume(
                    workdir, model=client, resource_limits=arguments(args),
                    enable_recovery=True,
                )
        except Exception as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
            return 2
        if run.gate_stop:
            from refactory_dsh.run_preparation import print_gate_stop
            print_gate_stop(run.gate_stop, workdir, graph=(DesignFastMode if fast else DesignMode)().build())
            return 2
        print(json.dumps({
            "resumed": str(workdir), "exit_ok": run.exit_ok, "error": run.error,
            "facts": {key: value for key, value in run.facts.items() if key in
                      ("name", "files", "artifact", "check", "validation")},
        }, indent=2, default=str))
        if not run.exit_ok:
            try:
                resume_mode = DesignFastMode() if args.fast else DesignMode()
                node = resume_mode.graph().nodes.get(str(run.error or "").split(":", 1)[0])
                if node is not None and node.kind == "gate" and node.gate_kind == "user":
                    from refactory_dsh.design_ops import design_review_status
                    drs = design_review_status(run.facts or {})
                    if drs:
                        print(drs, file=sys.stderr)
            except Exception:
                pass
        return 0 if run.exit_ok else 2

    from refactory_dsh.fast import DesignFastMode

    mode = DesignFastMode() if args.fast else DesignMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts" if args.fast else "check_attempts"]["value"] = int(args.repair_attempts if args.fast else args.attempts)
    if args.no_worktree:
        state["knobs"]["worktree"]["value"] = False
    if args.no_pr:
        state["knobs"]["pr"]["value"] = False
    if args.review_comments:
        state["knobs"]["review_comments"]["value"] = True
    mode.load_state_dict(state)
    from refactory_dsh.create import parse_verdicts

    verdicts = parse_verdicts(args.verdict)
    workdir = Path(args.workdir or f".factory/design/{args.name}")
    mode_name = "design_fast" if args.fast else "design"
    client = DshModelClient(
        project=project_root(),
        timeout_s=int(args.model_timeout_s) if args.model_timeout_s else DEFAULT_TIMEOUT_S,
        model=args.model, provider=args.provider,
    )
    from refactory_dsh.run_ownership import ownership
    from refactory_dsh.nn.executor import GateStop, HaltStop

    try:
        with ownership(workdir):
            # Run preparation.
            from refactory_dsh.run_preparation import prepare_run
            from refactory_dsh import steering

            prepare_run(
                state, workdir,
                invocation_name=f"default_pack-{mode_name}",
                mode_id=f"default_pack/{mode_name}",
                intent=args.brief,
                transform=lambda workflow, stage_dir=None: steering.for_run(workflow, stage_dir or workdir),
                runtime="walker",
                command=f"python -m refactory_dsh.design",
                resume_command=f"python -m refactory_dsh.design --resume {workdir}",
                fast=args.fast,
                worker_backend=client.route().get("worker_backend", "") if callable(getattr(client, "route", None)) else "",
                project=project_root(),
            )

            # Persist up-front verdicts as run-scoped ledger entries.
            ledger = steering.Ledger(workdir)
            for gate, value in verdicts.items():
                ledger.verdict(gate, value, approval=True)

            if not args.fast:
                # A gate answered up front shows nobody delivery.md, so the plan is said here (#350).
                from refactory_dsh import delivery

                knob_values = {"name": args.name, "brief": args.brief, "target": args.target, "base": args.base,
                         "refine": args.refine, **{name: state["knobs"][name]["value"]
                                                   for name in ("worktree", "pr", "repo_owner", "repo_visibility")}}
                (delivery.announce if "gate_strategy" in verdicts else delivery.forget)(knob_values, workdir)

            result = mode.run(
                workdir,
                model=client,
                task={"name": args.name, "brief": args.brief, "check": args.check, "source_project": args.source_project,
                      "target": args.target, "base": args.base, "setup": args.setup, "generate": args.generate,
                      "refine": args.refine},
                resource_limits=arguments(args),
                enable_recovery=True,
            )
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2
    if result.gate_stop:
        from refactory_dsh.run_preparation import print_gate_stop
        print_gate_stop(result.gate_stop, workdir, graph=mode.build())
        return 2
    if result.halted:
        print(json.dumps({"halted": True, "error": result.error}), file=sys.stderr)
        return 2
    if not result.exit_ok:
        print(json.dumps({"error": result.error}), file=sys.stderr)
        # dm-127: show design review status when stopped at a user gate.
        try:
            node = mode.graph().nodes.get(str(result.error or "").split(":", 1)[0])
            if node is not None and node.kind == "gate" and node.gate_kind == "user":
                from refactory_dsh.design_ops import design_review_status
                drs = design_review_status(result.facts or {})
                if drs:
                    print(drs, file=sys.stderr)
        except Exception:
            pass
        return 2
    print(json.dumps({
        "artifact": str(result.facts.get("artifact")),
        "branch": result.facts.get("branch"),
        "pull_request": result.facts.get("pull_request"),
        "delivery": result.facts.get("delivery_result"),
        "files": result.facts.get("files", []),
        "check": result.facts.get("check"),
        "iterations": result.iterations,
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())

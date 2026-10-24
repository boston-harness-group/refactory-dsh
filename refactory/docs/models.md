# Models

DSH is the default coding worker. Configure its model routes in `~/.dsh/settings.yaml` or **Settings → Models**. The Modes panel exposes **Worker routing and shared resources** for Run once and saved optimization profiles. Supported entry points accept `--model` and `--provider` overrides without changing saved settings. The table below explains which execution paths receive those overrides.

## Where the list comes from

The adapter's catalogue is **advisory**: it is what pickers offer, not what the route accepts. The DeepSeek route ships `deepseek-v4-flash`, `deepseek-v4-pro` and `deepseek-v4-flash-vision-exp`, and it will send any id you name — so a model that is not listed still works, it just is not offered. The settings document is hot-reloaded: nothing needs a restart.

## In the GUI

**Settings → Models.** Add a provider, paste its API key, optionally a base URL, and list the models — or press **Fetch available models** and pick from what the provider reports. **Add a custom provider** takes an id, a display name, one of three wire protocols (`openai-completions`, `openai-responses`, `anthropic-messages`) and a base URL, which is how a local server (Ollama, LM Studio, vLLM) is reached. The page writes the settings document and the credential store for you; the composer's model picker then offers the new models.

## In the terminal

The same file, by hand:

```yaml
# ~/.dsh/settings.yaml
agent-default-model:
  provider: deepseek-official
  model: deepseek-v4-pro          # the default for every run that names none

llm-pi-ai:
  providers:
    openai:
      apiKeyEnv: OPENAI_API_KEY   # the *name* of the variable, never the key
    anthropic:
      apiKeyEnv: ANTHROPIC_API_KEY
      models:
        - id: claude-sonnet-4-5
          contextWindow: 200000
    ollama:                       # a local OpenAI-compatible server
      displayName: Ollama
      api: openai-completions
      baseURL: http://127.0.0.1:11434/v1
      models:
        - id: qwen3-coder
          contextWindow: 262144
```

Keys are read from the environment (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, …, including a `.env` at the checkout root) or from the managed store at `~/.dsh/.credentials.yaml` that the Models page writes. A `llm-deepseek:` section overrides the direct DeepSeek route the same way: `models:`, `baseURL:`, `apiKeyEnv:`, `thinking:`, `reasoningEffort:`.

A local OpenAI-compatible server works through the same route, and still needs *a* key — pi-ai's OpenAI protocol asks for one, so name a dummy variable and export any value (`apiKeyEnv: LOCAL_TEST_KEY`, then `LOCAL_TEST_KEY=dummy`), or set an `Authorization` header.

## Choose a model for a run

```bash
uv run refactory ceo counting_pack/fixed_step --model deepseek-v4-pro
uv run refactory ceo "an idea" --mode create --single --model deepseek-v4-pro
uv run refactory optimize circle_packing/adaptive --provider deepseek-official --model deepseek-v4-pro
```

`--model` names the id and `--provider` the route; both fall back to `agent-default-model` in your settings, and neither writes anything back, so choosing a model for one run does not change the next. A route that does not exist is a loud failure (`NO_ADAPTER: no adapter registered for provider …`), not a quiet return to the default. The same two flags exist one level down, on `dsh --profile headless|graph`, which is what `refactory` calls.

## Model selection across execution paths

| Path | Where its model comes from |
|---|---|
| Terminal `ceo` | The selected graph-runtime provider/model, or saved defaults |
| Python Create/Edit/Design agent nodes | The supplied `DshModelClient`; its route/context is carried to supported nested validation |
| Optimization with the default `walker` evaluator | The supplied model client serves the reasoning proposer and evaluated agent nodes |
| Optimization with the separate `dsh` evaluator | The supplied client context is forwarded; explicit agent workers are dispatched through the same node bridge and route as the walker |
| GUI CEO and children | The owning session and worker configuration; inspect actual child execution when validating a route |

`refactory runtime` inspects configuration; `.refactory/workers/*.json` receipts establish the selected backend, observed runtime route, version and usage. Requested settings are separate from observed settings. Explicit `node_routes` override `role_routes`, which override the run settings; empty fields use the runtime defaults. Switching backend clears inherited provider/model/effort unless the override supplies them. A provider route declared in one DSH profile's settings is invisible to the nested headless runtime a DSH worker runs; declare such routes for workers with [`provider_config`](worker-routing.md) in `refactory.workers.json`.

A random seed does not make model outputs deterministic. Changing provider/model can change tool use, latency, cost and output quality; support for a protocol is not a performance guarantee.

## Model provider versus coding agent

DSH remains the default coding-agent runtime. It supplies the tool loop, sessions, delegation and working context; an OpenAI or Anthropic model can answer through its provider adapters. Choosing an OpenAI model does not switch the worker to Codex, and choosing a Claude model does not launch Claude Code.

Select `--worker-backend codex` or `--worker-backend claude_code` explicitly for an external node worker; `--worker-backend dsh` returns to DSH. The optional dependencies reuse DSH's named-provider registry, Codex app-server and Claude Agent SDK. They are dormant until selected and can be omitted from an installation that uses only DSH. A missing selected provider fails before execution. Native product settings and authentication remain authoritative; DSH credentials are not copied to either product.

For example, the direct interactive interface accepts:

```bash
python -m refactory_dsh.interactive counting_pack/fixed_step --worker-backend codex --model YOUR_CODEX_MODEL --headless
```

Refactory dispatches only the agent node's self-contained task, retains its declared outputs and runs graph-owned checks. The worker never owns graph order, user gates or scoring.

Each node worker receives its node identity, exact working directory, absolute
input/output paths and selected Python interpreter alongside the task and current
instructions. It uses the supplied API/reference context, performs that node's
required checks, writes its outputs and returns a brief completion response.
Delivery receipts and downstream graph operations remain runtime-owned; workers
are instructed not to rediscover that protocol from adjacent run histories or
repeat later stages. This orientation is active in both the Python executor and
native workflow path; it is guidance, not proof that a worker followed it.

New workers default to `--permission-mode skip` in the CLI, API and **Worker permission mode** control. This grants workers the access available to the OS account: DSH uses `danger-full-access` with approval policy `never`; Codex uses `dangerously-bypass-approvals-and-sandbox`; Claude Code uses `bypassPermissions`. The setting applies to each launched runtime and does not change personal product settings. Use this mode only for tasks and inputs you trust.

Because workers can write anywhere, a Design or design_fast run works in a git worktree of its own, through the steps any mode that changes a repository takes ([`repo_change`](tutorials/01-write-a-mode.md#if-your-mode-changes-a-repository)). Its target is the repository its `target` knob names (`--target` on the command line; a relative path is taken from the project the run belongs to) or, without one, a new project in `~/refactory-projects/<name>` whose first commit is empty. `prepare` pins the base commit (the `base` knob; by default the target's HEAD, or a new project's `main`) and adds a worktree under `~/.refactory/worktrees/` on the branch `refactory/run-<id>`. Your checkout is never changed: uncommitted work there is reported and left out of the run unless the `include_uncommitted` knob is set, in which case tracked and untracked uncommitted changes are copied into the worktree, committed as the run's own base, and recorded with their content hashes. Work you go on doing there does not affect the run. A new project's `main` stays empty until its work is delivered, so a second run on it starts from `main` and names the earlier runs' branches; pass `base=<branch>` to build on one of them.

Each attempt is one commit on the run's branch, building on the last, so a retry changes only what it needs to, and the reviewers judge the branch's diff from the base, which is also saved as `changes.diff`. The worktree is the run's own, so a tracked file that its check, setup command or a reviewer changes, and an untracked file one leaves there, is put back before the next attempt and reported; your repository's git hooks do not run there: each worktree has its own hooks path via `extensions.worktreeConfig`, and refactory's own git commands override it with `/dev/null`. Every environment a run uses — the check, the wrapper, the install, and the agent workers — sets `LEFTHOOK=0` and `HUSKY=0`, so a package manager's postinstall does not install hooks into the person's checkout. Additionally, every worktree a run creates gets its own hooks path: refactory enables `extensions.worktreeConfig` on the target (the only change to the target's shared config) and sets a per-worktree `core.hooksPath` pointing to an isolated directory, so a hook manager's install writes into the worktree's own hooks, not the shared `.git/hooks/`. When the target's config sets `core.bare=true` or `core.worktree`, hook isolation is skipped and a run warning names the reason; the existing suppress-and-detect behaviour still applies. The shared git configuration and hooks are checked at the end of every agent step, not only at materialise and the check; a change is reported as a run warning naming the step, or as a failure when the step should not have caused one. Whatever is reported this way is a run warning: the CEO is given it, the run's journal records it, and a run started with `refactory ceo` lists it again when it ends.

The check runs in the worktree, with the worktree's own environment. Its dependencies are installed when its manifests change (`uv sync --all-groups`, pnpm, npm or yarn, submodules), through the sandbox and under the run's budget, without secret-looking variables other than a package registry's own. An install is frozen while the attempt leaves a manifest as the base has it. When the attempt changes one, the lockfile the install writes is amended into the attempt, so a change that adds a dependency carries its lockfile, and a new JavaScript project gets one. An installer that is missing stops the run as unavailable rather than asking for a code repair. The `generate` knob's command, when set, runs next: whatever it changes in tracked files is amended into the attempt, and the `commit`, `files` and `changes.diff` facts follow; a generator that itself needs a build includes that build in its own command. The `setup` knob's command runs after generate, and nothing of Refactory's own, such as `PYTHONPATH` or `REFACTORY_*`, reaches the check. When a check needs built output — compiled JavaScript from a TypeScript workspace, Python extension modules, or other build artifacts — set `setup` to the project's build command (e.g. `pnpm run build`). Without it, the check fails on missing build output and the run cannot fix that by changing code. Its HOME is a directory in the run, so tool caches stay out of the diff; pnpm is still pointed at the store its install used. The check, in a worktree or not, is bounded by its time limit, `check_timeout_s`, rather than by memory: it has no memory cap, its file-size limit is 16 GB (`DEFAULT_FILE_MB`, matching the install limit), because Node reserves far more address space than it uses and fails under any practical cap once it starts worker threads, as vitest does, and its CPU limit is that time on every core, so a test runner's worker threads are not stopped early. A shell-wrapped signal death (exit 128+N) from a resource limit or the OOM killer (SIGKILL, SIGXCPU, SIGXFSZ) is classified as infrastructure, not as a code failure, by a narrow signal allowlist; see `_RESOURCE_EXIT_CODES` in `authoring_evidence.py`. When the time runs out, everything the check started is stopped with it. The step's overall spine limit covers the dependency installs, generate, setup and the check itself, so a check that `check_timeout_s` allows is not killed by the spine before it can finish. A check is given no input, so nothing it runs can wait on the terminal refactory was started from. A process killed by a signal (not a timeout) is retried once; if it dies again, the run reports it as an infrastructure failure naming the signal, does not count it as a code attempt, and does not reloop emit. Before the run starts, the workflow-spine build must exist; a missing build is refused at once, naming `pnpm run build` as the fix.

Before a failed check spends a code attempt, it is compared with the same check on the unchanged base commit. The base check runs in a **separate git worktree** of the target at the base commit, with its own environment, prepared the way the attempt's is (installs, then generate, then setup), never by checking out the base inside the attempt's worktree. That includes its own HOME (`.refactory/base-check-home`, not the attempt's `.refactory/check-home`): a setup that writes into HOME and keeps what it finds there, such as a tool's profile link, would otherwise leave every later check of the attempt reading the base's. A failure that the base has too — identified by test name where the output names tests (pytest `FAILED` and `ERROR` lines, vitest `FAIL` lines), and conservatively where it does not (no failure is excused when individual tests cannot be identified) — is inherited, not the attempt's, only if all three conditions hold: (a) the same test identifier fails on both base and attempt; (b) its test file is not one the attempt changes; (c) its test id or leaf name appears in neither the brief text nor `design.md`. Matching names is not enough on its own: nothing is excused unless the failing runner (pytest, vitest or jest, named in the command) is the check's last step, it does not stop at its first failure (`-x`, `--maxfail`, `--bail`), and its own summary counts exactly the failures named — otherwise a later step, a module that does not import, or the tests skipped behind an inherited failure would go unaccounted for, and `check.txt` says which. If every failure is inherited and meets all three conditions, the attempt passes the check, the `validation` fact names the inherited failures, and the evidence record is updated with a passing status so that downstream gates see a valid runtime receipt. If the base check itself cannot run because of a crash (a signal-killed install, setup, generate or check command), the base environment (HOME, worktree and cache) is discarded and rebuilt from scratch; on double failure the base environment is discarded again so the next check gets a fresh start, the run continues without excusals, and a note appears in `check.txt` and the PR's Verification section. A deterministic base-preparation failure (a non-signal non-zero exit) is cached and stops the run as unavailable. The base check runs once per run, cached by the base commit, setup, generate and check command. Inherited failure names are written to `check.txt` and the `validation` fact, so emit knows not to try to fix or stub them. When the attempt adds or changes test files, those tests are also run against the base source (copied into the base worktree along with their helper and fixture files): a new test that fails with an assertion error on the base is guarding the change, one that cannot be collected or imported on the base is reported as "cannot run on the base" rather than as guarding, and one that passes is reported as not guarding it. This record is written to `test-guardedness.json` after every check (passing and failing), computed per attempt, and flows to QA reviewers and the overwatch audit through the evidence receipts. The pull request's Verification names the inherited failures it excused, and how many of the new tests fail on the base, pass there, or cannot run there.

A generate or setup command that fails is compared against the unchanged base: the same command runs on the base commit in an isolated HOME; when the base succeeds, or the failure names files the attempt changed that the base's error does not, the failure is the change's fault and goes to the builder for repair with the exact error, labelled as generate, setup or install, spending an attempt like any failed check.  A failure the base has too (with no changed file uniquely named), or an infrastructure fault (a signal kill, a timeout, a missing executable), keeps today's handling.  A signal-killed preparation step (install, generate, setup) that survives `run_command`'s retry is classified as infrastructure, not `code_failure`, naming the command and the signal. The amendment takes every tracked change the generate command left.

The `emit` worker can test its candidate through the builder probe before making
its final emission.  The probe creates a throwaway standalone clone with the
candidate applied, runs named tests in an isolated environment, and reports the
result.  It is not an attempt: no commit, no counter increment.  It is bounded by
its timeout and deleted afterwards.  See docs/hardening.md for the isolation
guarantees.

The `design_doc` and `design_review` workers can also probe the current codebase
via `--command` (no `--emission`), to verify mechanisms the design relies on.
The probe runs at the run's base (no candidate emission), with the same isolation
guarantees: throwaway standalone clone, hooks off, isolated HOME/DSH_HOME, deleted
afterwards, not an attempt.  Probes record verifiable evidence:
`run_design_probe` writes `{id, command, status}` to `probes.jsonl`, and the
claims checker verifies cited probe ids against that file.  `design_review` is
told to probe call sites and report unreached ones as `BLOCKING`.  This applies
to both runtimes (DSH and the walker), since `steering.for_run` injects the
instruction.  `design_fast` is unaffected (no `design_doc` or `design_review`
nodes).

The `emit` worker must answer only through `emit.txt`: `===FILE:` with a whole file, `===EDIT:` with search-and-replace edits to the file as the last attempt committed it (each edit's search text is exact text that must appear exactly once in the file, and may start and end inside a line, as a sentence in a paragraph kept on one line does; several `===EDIT:` blocks for the same file are applied in order), or `===DELETE:`. A path in a block header may contain any characters except whitespace, `=`, backtick, single quote, and double quote; structural safety (relative only, no `..`, no backslash, no control characters) is enforced separately. A line that looks like a header (`===FILE:`, `===EDIT:`, or `===DELETE:`) but does not parse is refused with a message quoting the offending line. A file may not appear in both a `===FILE:` and a `===EDIT:` or `===DELETE:` block. Editing keeps a small change to a long file small. When `materialise` cannot apply `emit.txt` (a malformed block, an edit whose text is not in its file, text the read tool had cut short, or an emission that changes nothing because every file and edit is already in the last attempt), the run does not end: the check (design_fast's `gate_emit`) sends the reason back to `emit` directly, and the refused emission does not count against `check_attempts` (`repair_attempts` in design_fast); the gate's own iteration limit still bounds retries. A CEO redo (`wf_redo`) preserves the check attempt counter and the gate's iteration count; only a person's `graph-control rerun` resets them. An emission that changes nothing is refused back to emit, including when materialise is replayed on an emit.txt the last attempt already holds. emit writes only into the run directory; a change it makes to the run's worktree fails the step and names what changed; a change to the person's checkout fails the step for a worktree run (where the agent must write to its worktree instead) and is a run warning for a non-worktree run; a change to the launcher's checkout is always a run warning naming the files. When any agent node is re-dispatched — after a materialise refusal, a failed check, a CEO redo or a rerun — each declared output that is write-only (not also declared as a read) and still exists on disk is moved under the latest receipt's `.refactory/agent-delivery/` folder at the start of dispatch, so the worker writes a fresh one. An output the node also reads stays in place (it must be read before writing anyway). An output with no receipt from this node is left alone. The previous copies' locations are supplied in the execution contract's `set_aside` field, so a step that revises its own work can read its last version there.

A run that failed, or whose run directory a later run has reused, loses its worktree after 24 idle hours (`REFACTORY_WORKTREE_IDLE_HOURS`); a run that goes on afterwards gets it back from its branch. The branch goes after 14 days, with `git branch -D`, since a failed run's commits were never merged. One that succeeded keeps both, and so does a halted or cancelled run (including one whose cursor emptied before reaching a graph sink, which names the nodes it never reached), which can still be resumed. Removing a worktree clears the untracked files the run left there, but a worktree with an edited tracked file, or with an untracked file newer than the run's last step, is kept and reported. `refactory projects` lists the projects and how each run stands, and `refactory projects prune` cleans up without waiting for the next run; `--succeeded` also removes succeeded runs' worktrees, keeping their branches. `REFACTORY_PROJECTS_DIR` and `REFACTORY_HOME` move the two folders.

## Model completion timeout

`--model-timeout SECONDS` sets how long one model completion may take before it is
called hung, for every agent step in a `refactory ceo` run. The default is 1800
seconds (30 minutes), defined as `DEFAULT_TIMEOUT_S` in `model.py`. The same flag
exists on the standalone `design`, `create` and `edit` commands.

Each dispatched agent node's spine limit is computed by
`node_session_budget_s(attempts, timeout_s) = attempts × timeout_s + 600`, where
`attempts` defaults to 3 and 600 is `NODE_DISPATCH_OVERHEAD_S`. The per-node
`command_timeout_s` overrides the spine's global `commandTimeoutMs`, so a
`--model-timeout` larger than the profile's default does not get silently cut.

The value is saved in `model_context` (as the `timeout_s` key) in
`run-config.json`, and inherited by nested workers via `REFACTORY_WORKER_CONTEXT`.
A resumed run uses the saved value; `--model-timeout` given with `--resume` is
refused.

## Design-document contract

The design_doc node writes `design.md` following a defined contract. The contract requires these sections:

1. **An H1 heading** that says what the change does (never the run name or artifact name).

**Citation format for claims about existing code:** Use the subject-at-citation-quote form: `` `name()` at `path/to/file.py:42`: `def name(self):` ``. The subject (the backtick span before `at`) names the function, attribute or class the claim is about; the quote (the backtick span after `:`) is the line content at that position. This form is mechanically checked by `check_design_claims` before the design review runs. Bare citations without a quoted line (for files being added, or site markers) are not claims and are not checked by the claims checker.

2. **`## Summary`** — a prose summary of the problem and the change.
3. **`## Rule and sites`** — the rule the change makes or corrects, and every place in the code that applies or restates it, each cited with file path and line range, each marked `[changed]` or `[left: <reason>]`. Markers are accepted in bare, bold, backtick-wrapped, and colon-description forms (`[changed]`, `**[changed]**`, `` `[changed]` ``, `[changed: description]`), with the bare form as canonical. The parser carries a colon description into the "What changed" section of the PR body. `[new]` is treated as equivalent to `changed`. The three consumers are: the PR body "What changed" (`delivery.py` `build_what_changed`), the design review gate marker check (`design_ops.py` `gate_design_review` via `unmarked_sites`), and the run warnings (`delivery.py` `target_guard.warn` via mismatch detection). Misspelled markers (e.g. `[chnaged]`) are listed by the design review gate as advisory at the approval gate.
4. **`## Docs`** — the docs that describe the changed behaviour, with their planned updates.
5. **`## Changes`** — the list of files the change adds or modifies.

**Before the design gate:** A deterministic `check_design_claims` fn-gate runs between `design_doc` and the design review (for a scoped change, `route_design_review` sends the design from it straight to `gate_design_review`, with no review agent). It verifies each claim in the subject-at-citation-quote format: that cited paths exist, quoted lines match within ±3 lines, and named identifiers from the claim's subject are present (Python via `ast`, TypeScript via word-boundary search). Mismatches reloop `design_doc` without spending a design-review attempt; on exhaustion the mismatches are appended to `design_review_feedback` and `reviews/design-review-feedback.txt`. `gate_design_review` mechanically checks every design for contract completeness: a design missing a required section heading or whose section is empty reloops to `design_doc`. A rule site marked `[left]` without a reason, a rule site with no `[changed]` or `[left: <reason>]` marker, and a title that is the run name are listed as advisory at the person's gate; they do not send the design back. `has_contract` (whether all four section headings are present) only selects delivery's fallback for a design written before the contract or by hand. The design_review agent also flags as BLOCKING a place applying the same rule that is left unchanged without a reason, searching every caller in every mode and graph. A doc describing the changed behaviour that the design leaves out is a note under `## Notes`, not a blocking finding; the gate lists each note as advisory at the person's gate.

**Probe evidence in claims (dm-133):** A claim line can carry an inline `(probe <id>: <status>)` annotation, where `<id>` is the 12-hex-char probe identifier returned by `run_design_probe` and written to `probes.jsonl`. `check_design_claims` verifies that the cited id exists in `probes.jsonl` with the stated status; an unknown id or a mismatched status adds a mismatch. Probe evidence supplements but never replaces static checks: a static mismatch with a valid probe annotation still reloops. The `design_probe_instruction` and `design_probe_review` facts are injected by `steering.for_run` into structurally identified design-writing and design-review nodes (found by graph pattern match, not mode names), so this applies to both runtimes (DSH and the walker). `design_review` is instructed to probe the call sites a design relies on and report unreached ones as `BLOCKING:` lines, which the existing `gate_design_review` reloop mechanism picks up.

**New required sections (R4, #384):**

6. **`## Failure trace`** — for each failure the brief reports, a file:line chain from the symptom to the root cause, and the link the fix breaks. When the brief asks for a new feature, write `N/A — this is a new feature.` A missing or empty trace is advisory.
7. **`## Ask mapping`** — a markdown table with columns `| Ask (quoted from brief) | Design section | Test |`, one row per ask. `gate_design_review` mechanically checks that the section heading is present; the `design_review` agent validates that each quoted ask appears verbatim in the brief.
8. **`## Test guardedness exemptions`** (optional) — when new tests pass on the base, list each test file path and reason. A new test that passes on the base and is not listed here is rejected by the guardedness gate.
9. **`## Site revert exemptions`** (optional) — when a changed site cannot be caught by a mechanical revert-and-test, list the site path:line and reason. A changed site whose revert is not caught and that is not listed here blocks the correction audit, unless it is in a prose document (`.md`, `.markdown`, `.rst`, `.adoc`): no test is expected to reach a document, so that is advisory.

### Routing design_doc and design_review to a stronger model

Both `design_doc` and `design_review` use the default `ceo` role. To route them
to a stronger model, use `node_routes` in `refactory.workers.json`:

```json
{
  "node_routes": {
    "design_doc": {"model": "claude-opus-4-20250514"},
    "design_review": {"model": "claude-opus-4-20250514"}
  }
}
```

The worker receipt for each node records `requested_route` and `actual_route`,
so the run report and pull request's Verification section show which model
planned it. See [worker-routing.md](worker-routing.md) for the general
`node_routes` mechanism and priority order (node > role > run).

**Repository questions (check 5):** For repository-change runs (`deliverable_kind == "repository_change"`) whose design the review agent reviewed (not a scoped change), `gate_design_review` validates that the design review contains per-changed-function repository question entries (duplicates, callers, shared state) with `path:line` evidence, and a `## Brief example trace` section with at least two `path:line` citations and runtime names. Changed functions are derived from the design's `[changed]` sites via `design_contract.parse()`, normalised through `design_contract._extract_file_path()`. Missing entries are advisory at design time: they are listed at the person's gate and never re-run the design review. The post-build reviewers' repository questions are checked at `gate_qa`.

**Gate presentation (dm-127):** `gate_strategy` now opens with a `design_review_status` fact: "Design review: PASS" (with ", N advisory" when the gate listed advisory items) or "Design review: FAIL, N unresolved" followed by the blocking findings, where N counts blocking findings only. For a scoped change, with no review agent, it reads "Design review (automatic checks; scoped change — <reason>): …", where `<reason>` is the `research_scope_reason` fact. For a full design the status line appends "Scale: full — <reason>". This is carried by a separate `design_review_status` fact, set by `gate_design_review` on pass and exhaust. The fact reaches all template-rendered surfaces automatically: the terminal answerer, the GUI, the headless refusal, and `refactory status`.

**Pitfall coverage:** When `pitfalls.json` exists (produced by the `pitfalls_researcher` node in the research stage), `gate_design_review` also checks that every high-risk pitfall id appears in the design's `## Anti-patterns to avoid` section, either addressed (naming the site or section that handles it) or set aside with `[set-aside: <reason>]`. A site is a backticked path from `## Rule and sites`. A section is `§` and a heading or subsection heading (not a `#` line in a code block), which may go on to say where in it (`§ Changes rule 4`, `§ Changes → Regeneration`); design mode's finding coverage reads `§` references the same way. A high-risk pitfall that is missing or set aside without a reason is blocking. The approval gate shows the pitfalls and how the design accounts for each. Modes without a research stage (design_fast, create_fast, edit_fast) skip the pitfall check with a recorded reason.

**Finding coverage.** When `research/*.md` or `strategy/*.md` files exist with `FND-<slug>-<n>` ids (assigned by the research and strategy directors), `gate_design_review` records which finding ids the design cites in the `finding_coverage` fact, read from the detail files rather than the summaries. The design cites a finding only where it changes a decision, so an uncited finding is neither listed at the person's gate nor a reason to send the design back. Create mode's `review_design` gate and edit mode (which inherits create) run the same check against the `findings` field of `strategy.json`; create's review raises when its attempts run out. Graphs whose gate declares no detail-file directories as reads skip the finding check with a recorded reason.

Pitfall evidence accepts three forms. A **file:line citation** (`path/to/file.py:42`) names a line in the repository; the gate checks file existence with a skip-not-block policy for shortened paths. An **absence claim** (`absent: <path or glob>`) states that a file or glob pattern does not exist in the worktree; the gate verifies this mechanically and refuses a false claim, naming the path or matches that do exist. A **run-file citation** (`brief.json` or `research/<slug>.md`) names an artifact the run itself produced; the gate checks that the cited file exists in the run directory.

**Delivery:** `_title` and `_body` use the contract. For a contract design, the PR title comes from the H1 heading (never the run name), the Summary section from `## Summary`, and a "What changed" section from the `[changed]` rule-and-sites entries cross-checked against the branch's actual diff (sites whose file is not in the diff are excluded with a run warning; diff files not covered by any site appear by name; for a pre-contract design, the diff's file list is used instead). For a pre-contract design, the title is extracted from the heading with `Design:` stripped and the run name with its separator stripped; when nothing is left, the brief's first sentence is used as the fallback. The summary is the first prose paragraph after the heading, skipping sub-headings like `## Problem`. No second parser exists: delivery imports and calls `design_contract.parse` once. The PR body no longer recommends squash merge; the sentence about the branch's commits being the run's attempts is kept.

**Overwatch:** the overwatch audit is instructed to enumerate each constraint's and correction's clauses itself, in intent-audit.json: a `clauses` array per row, each clause with its own evidence from the diff and a `sites` array naming the exact file:line, test(s) and evidence scope. `audit_problems` refuses a constraint or correction row whose clause list is empty, or a clause with no evidence from the diff. At the post-build gates, `correction_audit_problems` mechanically checks each clause's sites (in-diff, revert-reachability, scope enforcement) and `compute_site_reverts` runs the named test against the reverted site.

A Design run whose reviews pass delivers its branch as a pull request. A run whose work is kept local — because `pr` is off, the plan changed, or GitHub was unreachable — still writes `pull-request.md` and records the title, body-file path, and finishing commands (push and `gh pr create`) in `delivery.json`, so nothing is left to write by hand. design_fast delivers nothing, because it has no approval gate to show a delivery at: its work stays on the run's branch, which its report names. `prepare` decides how before anything is built and writes it to `delivery.md`, which the approval gate reads with the design, so approving one approves the other; the gate's own question says it in one line too, so the approval card names the repository a run would create, and who can see it. It is one of three: a pull request from the run's branch into the target's repository on GitHub, into the branch the run's base came from (the branch `--base` names, or the checkout's upstream, when GitHub has it and it holds the base; otherwise the default branch), so a run on a branch of a stack carries only its own commits; for a new project, a GitHub repository created at delivery, private and under the account gh is logged in to unless the `repo_owner` and `repo_visibility` knobs say otherwise (`--knob` on the command line, or knobs at `wf_open`; the console `design` verb takes neither), whose `main` is the empty first commit and whose first pull request holds all of the code; or no pull request, and why. A run whose gate is answered up front (`--approve gate_strategy=proceed` on the command line, or a verdict given to the console's `design`) prints the plan when it starts, because that gate is not asked again, and keeps to it: if `prepare` then plans otherwise, the work stays local. The `deliver` step runs after `report`, so a run that fails never reaches it, and it does no more than the gate showed: a plan that has changed by then keeps the work local. When the target branch has moved since the run started, delivery merges it into the run's branch with a merge commit before pushing. Conflicts in files the run's generate command produces are resolved by re-running the generate command; a conflict in any other (authored) file causes delivery to stop without pushing — the run loops back to the builder via `delivery_gate`, which resolves the conflict automatically. The merged result is re-checked in the same environment as the attempt's check; if it fails, nothing is pushed and the run stops or loops. It never force-pushes, pushes to a default branch (other than a new repository's empty `main`), pushes a base commit GitHub does not have (for an existing repository it suggests `--base origin/<default>` instead; for a new project, its base may add nothing to the empty `main`), pushes into an existing repository that has a new project's name, or opens a second pull request for a branch or edits one's title or description. A credential-like `NAME=value` in the brief or the check is hidden from the pull request. It checks that the pull request's head is the reviewed commit, and records the pull request in `delivery.json`, `design.json`, the run's facts (`pull_request`, and `delivery_result` beside the plan's `delivery`) and `refactory projects`. Its commits are the run's attempts, each committed by Refactory, so the pull request recommends a squash merge. Delivery runs as a node command with your own HOME, not the check's. It names github.com over HTTPS with gh's login as its only credential helper (a `url.<base>.insteadOf` of your own still applies, so a rewrite to SSH uses your key), and nothing prompts: a missing credential is a failure, not a wait. Without gh, a login or the network, the run ends with its local branch and says so as a run warning, and says too when the branch already reached GitHub before delivery stopped, so a redo of `deliver` finishes it. A run planned while gh was logged out delivers nothing even after you log in, because its approval covered no pull request: the next run does. `pr=0` (`--no-pr`) keeps the work local by choice.

`refine=<pull request>` (`--refine`), given by URL or `owner/repo#number`, follows up an open pull request from a branch of the same repository: in the clone `refactory projects` knows it from, or the one `target` names. The run starts from the pull request's head in a worktree of its own, with the request as its brief and a steering note naming the pull request; `review_comments=1` (`--review-comments`) adds what its reviewers said, marked as other people's words rather than yours. It goes through the full review, and delivery pushes onto the pull request's branch, after merging the base branch in when it has moved, or not at all if that branch (the PR's own branch) moved since the run started. A delivery interrupted part way is not replayed automatically, like any node whose outcome is unknown; a redo of `deliver` finishes it without a second pull request.

When the target branch has moved since the run started, delivery merges it into the run's branch with a merge commit before pushing. Conflicts in files the run's generate command produces are resolved by re-running the generate command; a conflict in any other (authored) file causes delivery to stop without pushing — the run loops back to the builder via `delivery_gate`, automatically. The builder resolves the conflicts; materialise commits a merge with main's tip as the second parent. On a failed merged check, an abandoned merge, or a crash, nothing is pushed.

With `worktree=0` (`--no-worktree`), a run writes its artifact under `.factory/artifacts/<name>`, from whole files only (an emission it cannot apply goes back to `emit`, as above), and watches its project instead: the directory Refactory runs from, where `.factory/` and `refactory.workers.json` live. `prepare` records the project's commit, branch and uncommitted changes, later steps warn when any of them changes, and `emit` fails if the project changed while it ran. This detects changes; it does not prevent them.

Choose `--permission-mode default` (**Native permission policy** in the GUI) to retain the backend's native policy, which may deny file creation. Existing saved runs and profiles preserve their permission settings; historical snapshots without a recorded policy keep the native default. A backend switch resets an inherited backend-specific policy to `skip`. Codex also accepts `never`, `approve-for-me` and its full bypass name. Claude Code also accepts `dontAsk`, `acceptEdits`, `auto`, `plan` and `bypassPermissions`. Unsupported route combinations and permissions fail before launch. External per-call provider and reasoning-effort overrides are unsupported; select those through the product's native configuration. `--effort` is supported for DSH nodes.

| Adapter capability | DSH node | Codex node | Claude Code node |
|---|---|---|---|
| Model selection | Yes | Yes | Yes |
| Cancellation/deadline | Owned runtime shutdown | Owned runtime shutdown | Owned runtime shutdown |
| Steering | Next node boundary | Next node boundary | Next node boundary |
| Resume in-flight worker / images / human questions | Not exposed by this one-shot bridge | Unsupported | Unsupported |
| Observed route | DSH model request | Native thread startup and reroute events | Native SDK initialization and observed model list |
| Token reporting | Measured request totals, including nested DSH agents | Measured native thread totals; nested coverage not established | Estimated SDK query totals, including Task workers; excludes helper calls outside that pipeline |

External workers refuse token-limited launches because complete nested accounting is not established. They support elapsed limits. Missing usage stays unavailable, never measured zero. Codex/Claude product sessions are ephemeral: receipts retain their identity for provenance, but never offer resume. A failed or cancelled worker is not automatically replayed. New instructions are delivered at an explicitly chosen node boundary; an already executing external worker cannot receive an in-flight edit. The native panel disables unsupported provider/effort/token controls for external backends and displays these limits. Switching backend clears incompatible inherited values; explicit unsupported overrides still fail validation.

Observe-only patches on the pinned DSH providers emit whitelisted route/usage metadata through Cordis. They do not copy raw protocol traffic, prompts, commands, paths, reasoning or credentials into the parent conversation. The protocol and product lifecycle remain owned by the existing provider bundles. Official integration contracts: [Codex App Server](https://learn.chatgpt.com/docs/app-server), [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk), [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview).

Worker receipts also feed [live activity](live-control.md); saved [profiles](optimization-profiles.md) retain requested routes in their exact launch snapshot. Configuration readiness is not execution evidence. DSH completed the bounded CLI and native GUI task. Codex and Claude Code completed the CLI task with explicit edit permissions and the native GUI task with the new `skip` default. Earlier native-policy failures remain documented as permission controls; see the [validation record](release-validation-20260926.md).

## Credentials for nested execution

Create/edit smoke and panel optimization launch further DSH processes. Their environment
is deliberately scrubbed, and the smoke process has a workspace-local `HOME`. Save the
provider key once through **Settings → Models** in the DSH home used by the run:
open the provider row with **Edit**, enter its **API key**, and choose **Apply**. The
product passes that nonsecret home location to nested model processes; keys remain in
DSH's managed store and are resolved by its credential service. Do not copy `.env`
files into generated packages or smoke directories. If an exported key shadows the
reference, the UI reports “Provided by the launch environment (read-only)”; launch
without that override before saving. DSH resolves keys in this order: inherited launch
environment, managed store, invocation-directory `.env`, then DSH-home `.env`. A stored
key overrides both `.env` layers, but not an inherited environment override. The launch
environment is a startup snapshot; changing the launching shell afterward does not
change the running GUI's credential source.

`refactory doctor --provider deepseek-official --model deepseek-flash` checks local
credential configuration through the same scrubbed subprocess boundary as smoke. Create
and edit perform this check before research. The check makes no model request: a
configured key is not proof of valid authentication, available quota, or network access.
The later real model smoke remains required. Provider-native authentication without an
API-key reference is reported as `native-auth-unverified`, not as a verified credential.

The local diagnostic uses the installed headless profile's supported file-backed
credential/settings services and adapter schemas. A replaced service or dynamic `!!js`
configuration, an external `DSH_CLI`, or a source-checkout launcher is reported as
`diagnostic-unavailable`: the doctor prints **WARN** and authoring continues with an
explicit unverified-readiness warning. The actual selected runtime performs its normal
checks. Confirmed missing references and malformed known credential/settings configuration
still stop authoring before research. The diagnostic never guesses a credential location.
DSH's normal profile loader may initialize a missing shipped headless profile; the check
makes no model request and does not write credential values. The local managed store is owner-only but does not
isolate credentials from generated code running as the same OS user; stricter isolation
requires a separate model-service boundary.

For a DSH source checkout, set `DSH_ROOT` to its root after installing its dependencies.
The source launcher resolves `tsx` and the checkout's TypeScript configuration there;
agent processes still run in their item directories. `DSH_CLI` continues to select an
external executable directly. A custom/source runtime must support the flags supplied
to it: upstream headless profiles without this fork's `--provider`/`--model` extension
can select their route through the profile's `agent-default-model` configuration.

## Inspect context before an interactive workflow

The CEO can use the existing `refactory` tool with `action: "runtime"` before `wf_open`. It reads the current session selection and the host-owned DSH home, then uses the same nested credential diagnostic as create/edit. It never asks you to copy credentials into a run or change a configured home. Missing and invalid configuration are reported as such; unsupported diagnostics stay unverified. Current-session reasoning effort is shown separately because credential readiness does not prove nested reasoning selection.

From the terminal, `refactory runtime` reports nested defaults as JSON; `--provider` and `--model` inspect an explicit route without changing settings. There is no current CEO session in that CLI call. The diagnostic does not authenticate or launch a mode. As with doctor, the normal DSH loader may initialize a missing shipped headless profile, but it does not write credentials.

## Completion deadlines and interruption

Worker contexts retain the selected Python environment (`VENV_BIN`, defaulting to
the checkout's `.venv/bin`). Tool commands see the target project's own environment
by default: when a worktree exists, its `.venv/bin` and `node_modules/.bin` lead
`PATH`, matching `worktree.check_environment()`. Refactory's own source is not
placed on `PYTHONPATH`, so a target that is refactory itself is tested from its
worktree, not from the launcher's checkout. Refactory's own nested dispatch
(worker_node, sandboxed validation) and its graph-runtime commands (data loader,
agent delivery, steering) use absolute interpreter paths and do not depend on the
worker's `PATH` or `PYTHONPATH`. An op node's command is the portable `python -m
refactory_dsh.run` (no machine path is baked into a graph), so the graph's command
environment keeps the run's interpreter first on `PATH` to resolve it; an agent's
own `PATH` is replaced by the worktree's when it is dispatched. DSH's normal credential scrub still applies to
child processes. A Design run's check in a worktree uses the worktree's own
environment (`check_environment`), and every role is given `.refactory/in-worktree`,
which switches to the worktree directory and runs with a clean `env -i` environment.

Token-limited DSH runs require the shipped in-process `spawn` and `fork` provider
composition. An additional named provider with unverified nested telemetry is
refused before a model request. Without a token limit, native descendants may run,
but their observations remain separate from the DSH parent's route: known DSH
tokens stay visible and total usage is explicitly unavailable. A completed DSH
request without valid usage likewise prevents claiming a measured total.

A `DshModelClient` deadline fails the completion without replaying its prompt: the
agent may already have written files or started tool work. Explicit transport
failures retain their bounded retry policy. On POSIX, a deadline or caller
`KeyboardInterrupt` raised in the thread calling `complete()` sends one SIGTERM
to the selected CLI and allows ten seconds for shutdown. The Python walker's
concurrent fork also owns a cooperative cancellation scope: a main-thread
interrupt stops queued branches and asks active shipped DSH clients to shut down
before propagating that original interruption. Cancelled branches are recorded at
the fork, without merging their facts or acknowledging its join. Each fork binds
an independent client copy; cancellation does not change the original client or
other runs. Completion polling retains one absolute deadline and accumulated
output, and cancellation also stops retry launches and backoff waits. The shipped CLI disposes its context and managed subprocesses,
including ordinary tool jobs launched in separate process groups. A separate CLI
session prevents terminal Ctrl-C from also delivering a second shutdown signal.
Valid work that finishes inside the deadline follows normal CLI disposal.

This is runtime-owned cleanup, not a scan or kill of process names. A runtime that
does not settle within the grace is forced to exit; the error says child cleanup
is unconfirmed and no retry follows. Custom launchers must implement their own
shutdown, and Windows termination does not provide the POSIX graceful signal
path. The local provider's macOS process-group fallback cannot contain arbitrary
descendants that escape its managed range. Python SIGKILL, abrupt parent death,
still need external supervision. Default process-wide SIGTERM now unwinds the owned Python model subprocess before exit.
Arbitrary in-flight Python operations are awaited, not forcibly interrupted;
custom model clients retain caller-thread serial execution and their own cleanup
contract. The client does not undo files written before failure or certify their reuse.

## Project defaults and discovery

[Worker routing](worker-routing.md) documents `refactory.workers.json`, coder-only
selection and precedence. [Live help and docs](discovery.md) provide current-build
read-only discovery in chat and the terminal without source inspection.

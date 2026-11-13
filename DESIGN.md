# Design

How the v2 factory works, and why it's built this way. The source is small
enough to read in one sitting: one skill, eight agents, three scripts and a
config file.

## The idea

A software factory turns a queue of requests into merged code with a fixed
pipeline. Scripts handle the deterministic stages, such as running the tests,
moving state and merging. Agents do the judgment in between: whether a request
is worth building, what exactly to build, the code itself, and whether it's
good. Each stage leaves a file behind, so you can see what happened and why.
Every step's file opens with a `## Summary` of three to five bullets, so a human can
skim a job on the dashboard and read the detail only where it matters.

## Pieces

| Piece | File | Job |
|---|---|---|
| Queue | GitHub issues labeled `factory` | What to build. Anyone can file an issue; only people with triage or write access can label it. |
| Pipeline | `stages` in `factory/config.json` | The stages in order: who runs each, what it reads, the file it writes. |
| Orchestrator | `.claude/skills/factory/SKILL.md` | The session that runs `/factory`. A loop: ask `job next`, spawn or run what it prints, report with `job done`. Decides nothing. |
| Agents | `.claude/agents/*.md` | One file per role. Each reads files, writes exactly one file, and replies with one line. |
| State | `factory/jobs/<job-id>/` | `job.json` plus every agent's file. Gitignored, local to the machine running the factory. |
| Engine | `factory/bin/job`, `gate`, `route` | Deterministic. `job` reads the pipeline, moves each job by the status its stage reported, and is the only writer of `job.json`. |
| Checks | `factory/checks.sh` | The repo's test, lint, typecheck and build commands. The only repo-specific script. |
| Exit | the PR | Every job ends as a PR that closes its issue. Merging it is the approval. |

## Pipeline

```
intake             → DONE (scope possibly changed) | NEEDS-INFO | REJECTED, posted as an issue comment
route (script)     → reviewers + models from risk/surfaces via config.json
worktree           → factory/<job-id> from origin/main
spec-writer        → spec.md
builder            → commits; first round pushes + opens draft PR "Closes #<n>"
gate (script)      → checks.sh; REDO → builder gets the log, no reviewers spawned
reviewers (routed) → parallel, wait for all; REDO → builder → gate → reviewers (max rounds)
approver           → DONE: merging (sync origin/main, gate, gh pr merge)
                     NEEDS-HUMAN: PR ready, decision as PR comment, label factory:needs-human
```

`stage` in `job.json` is one of the stage names in `config.json`, or
`needs-info`, `needs-human`, `merged`, `rejected`.

## Stages and statuses

A stage in `config.json` is four facts:

```json
{"name": "build", "agent": {"file": "builder.md"},
 "input": ["spec.md", "issue.md", "intake.md", "round-{prev}/*", "feedback-*.md", "decision.md?"],
 "output": "build.md"}
```

- **Who runs it**: `agent` (a file in `.claude/agents/`), `agents` (the routed
  reviewers, one file each), or `script` (a command; `{job}` is the job id).
- **`input`**: files in the job folder. A plain name must exist before the
  stage starts. A pattern with `*`, or a name ending in `?`, lists what is
  there. `{round}`, `{prev}` and `{agent}` are filled in.
- **`output`**: the one file the stage writes.

`job next` turns that into the agent's prompt: the job's header, then the files
to read and the file to write. The agent files don't list files; they say how
to do the work. So a file's name lives in one place.

Every stage ends with a status on the first line of its output. A script stage
reports by its exit code instead (0 is `DONE`, 1 is `REDO`).

| Status | What `job` does |
|---|---|
| `DONE` | The next stage in the list. After the last one, the job waits for its PR to be merged. |
| `REDO` | Back to `redo_stage` (the builder) with the round counter raised; `needs-human` on the last round. |
| `NEEDS-INFO` | Posts the file on the issue, labels it, waits for the author. Their reply sends the job back to the stage that asked. |
| `NEEDS-HUMAN` | PR ready, the reason as a PR comment, issue labeled. Your feedback starts another round. |
| `REJECTED` | Says why on the issue, removes the label, closes the PR. |

Several agents in one stage: `DONE` when all say `DONE`, `REDO` when any says
`REDO`.

Some behaviour belongs to a stage's place in the pipeline and stays in code:
the first stage's acceptance is posted on the issue and routes the reviewers,
leaving it creates the worktree, and the `redo_stage` is done only once its
branch is pushed and has one open PR.

## Decisions

**Issues are the queue; you trigger the run.** `/factory run` drains every open
`factory` issue. Nothing runs in GitHub Actions or on a schedule: the factory
runs on your machine, on your Claude plan, when you start it. An issue can't
start a Claude Code session by itself, and keeping it local means your
worktrees, dev servers and dashboard are right there.

**The pipeline is data, and a script reads it.** The orchestrator used to carry
the pipeline in its instructions and decide each next step. Now `job next`
prints what to spawn or run, with the prompt written out, and `job done` reads
the status and moves the job. Routing by a fixed set of statuses is a table
lookup, so no model does it: nothing can skip a stage, misread a verdict or
build a prompt slightly wrong. When a step fails twice, `job fail` hands the
job to you with the error.

**One orchestrator for every job.** The session running `/factory run` starts up
to `concurrency` jobs and spawns every agent as a background subagent. There
are no per-job orchestrators to coordinate. Its context stays small because
agents reply with one line and the scripts print one line; the detail lives in
files the next agent reads. Claude Code allows 20 concurrent subagents per
session by default, which covers three jobs with four reviewers each.

**Fable at the door, once.** Intake is the only Fable call per job. It decides
whether the issue is worth building, rewrites the scope when the issue is too
big or vague, and asks the author when only they can answer. Its decision goes
on the issue as a comment; the issue body stays as written. Questions go to
the author before a spec exists, so the spec writer never stalls on one.

**Risk routes the work, a script does the routing.** Intake rates risk (`low`,
`medium`, `high`) and surfaces (`ui`, `security`). `factory/bin/route` looks
them up in `config.json`:

| | code review | ux | ui | security |
|---|---|---|---|---|
| low | Haiku | | | |
| medium | Sonnet | | | |
| high | Sonnet | | | |
| + `ui` surface | | Sonnet | Haiku | |
| + `security` surface | | | | Opus |

The builder always runs on Opus.

A low-risk change with no UI runs one Haiku reviewer. Routing is visible in
one file and changes without touching a prompt.

**Scripts decide whether it works.** The gate runs `checks.sh` before any
reviewer is spawned, and again after syncing with `main` before the merge.
A failing build goes straight back to the builder with the log. The gate runs
the main checkout's copy of `checks.sh`, so a branch can't edit the checks it
is judged by. It fails any branch that modifies or deletes a file under
`protected_paths`: the factory's own checks, config, scripts, agents and
skill. Tests aren't
protected, since features have to update them; the code reviewer fails any
test that was weakened, skipped or special-cased to pass. The
builder also has a `SubagentStop` hook that runs the checks before it finishes,
as a nudge; the gate is still what decides.

**Agents judge what scripts can't.** Reviewers don't re-run the test suite;
they read the diff for correctness, scope, security, UX and visual fit.

**A PR per issue, merged through GitHub.** v1 merged locally, which broke when
the main checkout was dirty or busy. v2 merges with `gh pr merge`, so branch
protection, required checks and the PR history all apply, and your main
checkout is never touched. Before merging, the branch is synced with `main` and
gated again, because another job may have merged since it was tested.

**You approve by merging.** The approver merges what it would merge itself and
escalates the rest with a specific question. An escalated PR is ready for
review and its issue is labeled `factory:needs-human`. Merge it to approve, or
tell the factory what to change with `/factory <anything>`: your words go to
the builder as `feedback-<k>.md` and the job runs another round. To drop a
job that waits on you or on its author, tell the factory to reject it: the
reason goes on the issue, its PR closes, and the job moves to Done.

**State is files, so runs resume.** Every fact the orchestrator needs is in
`factory/jobs/`. If the session dies or compacts, a fresh `/factory run` reads
the queue and resumes every job from its stage.

**Cost is measured per job.** Claude Code writes each subagent's transcript,
with model and token counts per message, to
`~/.claude/projects/<project>/<session>/subagents/`. Every factory prompt starts
with the job's folder, so `factory/bin/job` sums the transcripts that name it
and prices them with `prices_usd_per_mtok`. The figure is the API-equivalent
cost of the job's agents; on a subscription it shows relative spend, not your
bill. The orchestrator's own tokens aren't attributed to a job. The
transcript format isn't a documented interface, so a Claude Code update could
break this.

## Guardrails

- Only issues labeled `factory` run. Labels need triage or write access, so the
  label is the trust boundary.
- Issue text reaches agents as quoted data in `issue.md`, with comments only
  from the issue's author and the factory's operator. It is never part of a
  prompt. Intake, which reads it first, has no shell.
- Agents push only `factory/*` branches. Only `factory/bin/job merge` merges,
  through `gh pr merge`. The factory never deploys.
- One PR per issue; at most `max_rounds` build rounds per job before a human
  is asked.

## What changed from v1

| v1 | v2 | Asked for by |
|---|---|---|
| `/factory <feature>`, one job per session | `/factory run` drains the issue queue, `concurrency` jobs at once, one orchestrator | Alex; @spec2k5, @MrHossamsaraya |
| Copy-paste build prompt | One install prompt pointing at this folder | @felipeclavijo1736 |
| Every job gets all four reviewers | Fable intake rates risk and surfaces; a script routes reviewers and models | @mptrista, @Robin-ox2mz |
| Every verdict is an LLM opinion | `checks.sh` gates before review and before merge | @Robin-ox2mz |
| Local `git merge`, needs a clean main checkout | A PR per issue, merged with `gh pr merge` | v1 on-camera merge failures |
| `/factory approve` | Merge the PR on GitHub | v1 |
| The spec writer can stall on questions | Intake asks on the issue and parks the job | @acidrazor |
| Cost invisible | Cost and time per job on the dashboard and in the run summary | the most common comment |
| No license | GPLv3, commercial licensing available | @mapledev9335 |

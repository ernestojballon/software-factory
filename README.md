# Software Factory for Claude Code (v2)

A software factory that works through your GitHub issues. Label an issue
`factory`, run `/factory run`, and agents decide whether it's worth building,
write a spec, build it, test it with your own checks, review it, and open a
pull request that closes the issue. The approver merges what it's sure of and
hands you the rest.

## Install

Open Claude Code in your project and paste:

```text
Install this software factory in this project from the reference repository:

https://github.com/ernestojballon/software-factory
```

Your agent reads [`INSTALL.md`](INSTALL.md), copies the skill, agents, scripts
and dashboard, fills `factory/checks.sh` with your project's real test, lint,
typecheck and build commands, tailors the reviewers to your stack, creates the
GitHub labels, and checks that `checks.sh` passes on `main`.

You need Claude Code 2.1.257 or later, `python3`, and an authenticated
[`gh`](https://cli.github.com/) that can push to the repo.

## The loop

```
issues labeled `factory`
        │
        ▼
intake ── NEEDS-INFO → asks the author on the issue, waits for a reply
        │  REJECTED   → says why on the issue, removes the label
        ▼ DONE, maybe rescoped; rates risk + surfaces, which pick the reviewers
spec-writer → spec.md
        ▼
builder → commits, pushes, opens a draft PR "Closes #n"
        ▼            ▲
gate (script) ─REDO──┤  factory/checks.sh; no reviewer runs on a failing build
        ▼ DONE       │
reviewers ─REDO──────┘  code, + ux & ui on UI changes, + security
        ▼ all DONE
approver ── DONE        → merging: sync with main, gate again, gh pr merge
         └─ NEEDS-HUMAN → PR ready for you, issue labeled factory:needs-human
```

That pipeline is the `stages` list in [`factory/config.json`](factory/config.json):
each stage names who runs it, the files it reads and the file it writes. Every
stage ends with one of five statuses on the first line of its file (`DONE`,
`REDO`, `NEEDS-INFO`, `NEEDS-HUMAN`, `REJECTED`), and `factory/bin/job` moves
the job by that status. To change the pipeline, edit the list: drop the
`merging` stage and nothing merges without you; drop `spec` and intake's scope
goes straight to the builder.

One Claude Code session orchestrates every job, `concurrency` at a time
(default 3). It asks `factory/bin/job next` what to do, spawns the agents and
runs the commands it prints, and reports back with `factory/bin/job done`. Each
job gets its own git worktree and branch. All state lives in `factory/jobs/`,
so if the session dies, `/factory run` picks every job back up.

## Commands

```
/factory run          drain the queue: every open `factory` issue, `concurrency` at a time; also resumes
/factory run <n>      one issue now
/factory <anything>   talk to the factory: ask about jobs, give feedback, file a feature
```

You approve by merging the PR on GitHub. To change an escalated PR, tell the
factory what you want with `/factory`. To drop a job that waits on you or on
its author, tell the factory to reject it.

## What's in here

```
.claude/
  skills/factory/SKILL.md     the orchestrator: a loop around `job next` and `job done`
  agents/                     how each agent works; each file sets its own model and tools
    intake.md                 fable    worth building? at what scope? risk + surfaces
    spec-writer.md            opus     the spec
    builder.md                opus     code, commits, the PR, its build report
    reviewer-code.md          routed   correctness, simplicity, tests
    reviewer-security.md      opus     only when intake flags `security`
    reviewer-ux.md            sonnet   only when intake flags `ui`
    reviewer-ui.md            haiku    only when intake flags `ui`
    approver.md               opus     merge it, or hand it to you
factory/
  config.json                 the pipeline (stages, who runs each, inputs, outputs), limits,
                              labels, reviewer routing, protected paths, prices
  checks.sh                   your repo's checks (the installer fills it in)
  bin/job                     the engine: reads the pipeline, moves jobs, the only writer of job.json
  bin/gate                    runs checks.sh in a job's worktree → round-N/checks.md
  bin/route                   risk + surfaces → the job's reviewers and models
  dashboard.html              the board
  tests/                      black-box tests: python3 -B -m unittest discover factory/tests
examples/jobs/                finished jobs from a real run
INSTALL.md                    instructions for the installing agent
DESIGN.md                     how it works and why
```

[`DESIGN.md`](DESIGN.md) explains each decision and lists what changed from
v1.

## Dashboard

```bash
cd factory && python3 -m http.server --bind 127.0.0.1
```

Open <http://localhost:8000/dashboard.html>. Each job is a card with its issue
and PR, a row of gate and review chips per round, its risk, cost and
time. Click a card to read every file the job wrote; each one opens with a
short summary of what happened in that step. To see the example
jobs, copy `examples/jobs/` to `factory/jobs/` first.

Cost is the API-equivalent price of each job's agents, read from Claude Code's
transcripts. On a subscription plan it shows where the spend goes, not what
you're billed.

## The example jobs

They come from a test run of an earlier version on a small private calculator
repo, with `concurrency: 2` and every agent on Haiku to keep it cheap. Their
files open with that version's verdict words (`DECISION: ACCEPT`,
`VERDICT: PASS`, `APPROVE`) where today's write a `STATUS:` line; the
dashboard shows both.

- **001-add-a-subtract-function**: low risk, no UI, so only the code reviewer
  ran. Approved and merged through its PR.
- **002-show-a-history-of**: a UI change, so the UX and UI reviewers ran too.
  Merged.
- **003-add-multiply-divide-power**: intake cut an oversized "scientific
  calculator" issue down to three functions. The gate failed round 1 because
  the builder edited a protected test file, and the job went to a human after
  three rounds.
- **005-add-a-crypto-miner**: rejected at intake.

## Cost and safety

- It opens and merges real PRs and runs on your Claude plan. Start with
  `concurrency: 1` and read every PR until you trust it.
- Intake runs on Fable once per issue. Depending on your plan, Fable can bill
  to usage credits rather than your plan's included usage.
- Only issues labeled `factory` run, and only people with triage or write
  access can add labels. Issue text reaches the agents as quoted data, never
  as their instructions.
- The factory pushes only `factory/*` branches, merges only through
  `gh pr merge`, and never deploys.

## License

[GPLv3](LICENSE). You can use, modify and share this code, and any version you
distribute must also be released under GPLv3.

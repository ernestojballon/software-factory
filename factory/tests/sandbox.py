"""A throwaway factory for black-box tests.

A Sandbox is a git repo with an origin, a copy of the scripts under test, and
a fake `gh` on PATH. Tests only run the scripts as commands and look at what
comes out: stdout, stderr, the exit code, the files written, and what the fake
GitHub ends up holding. Nothing is imported from the scripts, so the same
tests run unchanged against any version of them.

FACTORY_BIN=<dir> picks the copy of gate, job and route to test; the default
is factory/bin next to this folder.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

BIN = Path(os.environ.get("FACTORY_BIN", Path(__file__).resolve().parents[1] / "bin")).resolve()
OPERATOR = "operator"

CONFIG = {
    "concurrency": 3,
    "max_rounds": 3,
    "checks_timeout_seconds": 60,
    "base_branch": "main",
    "merge_method": "merge",
    "labels": {"queue": "factory", "needs_info": "factory:needs-info", "needs_human": "factory:needs-human"},
    "routing": {"low": {"code": "haiku"}, "medium": {"code": "sonnet"}, "high": {"code": "sonnet"}},
    "surface_reviewers": {"ui": {"ux": "sonnet", "ui": "haiku"}, "security": {"security": "opus"}},
    "protected_paths": ["factory/checks.sh", "factory/config.json", "docs/*"],
    "ports": {"base": 5100, "step": 10},
    "prices_usd_per_mtok": {
        "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_write_5m": 5.0, "cache_write_1h": 8.0, "cache_read": 0.2},
    },
}

# GitHub, reduced to a JSON file: issues, PRs, and every call made.
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys

path = os.environ["FAKE_GH_STATE"]
state = json.load(open(path))
args = sys.argv[1:]
line = " ".join(args)
state["calls"].append(line)


def done(out=None, error=None):
    json.dump(state, open(path, "w"), indent=1)
    if error:
        sys.exit(error)
    if out is not None:
        print(out)
    sys.exit(0)


def opt(name):
    return args[args.index(name) + 1] if name in args else None


def comment(thread):
    n = sum(len(t["comments"]) for t in list(state["issues"].values()) + list(state["prs"].values()))
    thread["comments"].append({"author": {"login": state["operator"]}, "body": opt("--body"),
                               "createdAt": f"2026-01-01T00:00:{n:02d}Z"})


for prefix in state["fail_once"]:
    if line.startswith(prefix):
        state["fail_once"].remove(prefix)
        done(error=f"fake gh: `{prefix}` is down")

cmd = " ".join(args[:2])
if cmd == "api user":
    done(state["operator"])
if cmd == "issue list":
    done(json.dumps([{"number": i["number"], "title": i["title"]} for i in state["issues"].values()
                     if i["state"] == "OPEN" and opt("--label") in [l["name"] for l in i["labels"]]]))
if cmd == "pr list":
    done(json.dumps([{"number": p["number"], "url": p["url"]} for p in state["prs"].values()
                     if p["state"] == "OPEN" and p["head"] == opt("--head")]))

kind, verb, number = args[0], args[1], args[2]
thread = state["issues" if kind == "issue" else "prs"].get(number)
if thread is None:
    done(error=f"fake gh: no {kind} #{number}")
if verb == "view":
    done(json.dumps(thread))
if verb == "comment":
    comment(thread)
elif cmd == "issue edit":
    names = [l["name"] for l in thread["labels"]]
    names += [n for n in (opt("--add-label") or "").split(",") if n and n not in names]
    names = [n for n in names if n not in (opt("--remove-label") or "").split(",")]
    thread["labels"] = [{"name": n} for n in names]
elif cmd == "pr ready":
    thread["isDraft"] = False
elif cmd == "pr close":
    thread["state"] = "CLOSED"
elif cmd == "pr merge":
    thread["state"] = "MERGED"
    thread["merged_with"] = args[3]
else:
    done(error=f"fake gh: unknown command {line}")
done()
'''


def masked(text):
    """Hide what changes from run to run: durations, clock times and commit ids."""
    text = re.sub(r"\bin \d+s\b", "in Ns", text)
    text = re.sub(r"\b\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", "<time>", text)
    return re.sub(r"\b[0-9a-f]{40}\b", "<commit>", text)


class Sandbox:
    def __init__(self, test, **config):
        self.tmp = Path(tempfile.mkdtemp(prefix="factory-test-")).resolve()
        test.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.factory = self.repo / "factory"
        self.jobs = self.factory / "jobs"
        self.state_path = self.tmp / "gh.json"
        self.claude = self.tmp / "claude"
        self.env = {
            **os.environ,
            "PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_STATE": str(self.state_path),
            "CLAUDE_CONFIG_DIR": str(self.claude),
            # The user's git config (signing, rename detection) must not change the results.
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        }
        (self.tmp / "bin").mkdir()
        (self.tmp / "bin" / "gh").write_text(FAKE_GH)
        (self.tmp / "bin" / "gh").chmod(0o755)
        self.state_path.write_text(json.dumps(
            {"operator": OPERATOR, "issues": {}, "prs": {}, "calls": [], "fail_once": []}))

        (self.factory / "bin").mkdir(parents=True)
        for script in ("gate", "job", "route"):
            shutil.copy(BIN / script, self.factory / "bin" / script)
        self.write("factory/config.json", json.dumps({**CONFIG, **config}, indent=2))
        self.write("factory/checks.sh", "set -euo pipefail\necho ok\n")
        self.write(".gitignore", "/factory/jobs/\n/.worktrees/\n")
        for name in "abc":
            self.write(f"docs/{name}.md", f"{name}\n" * 5)
        self.git(self.repo, "init", "-q", "-b", "main")
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-qm", "init")
        self.git(self.tmp, "init", "-q", "--bare", "origin.git")
        self.git(self.repo, "remote", "add", "origin", str(self.tmp / "origin.git"))
        self.git(self.repo, "push", "-q", "origin", "main")

    # ---- running things ----

    def run(self, script, *args, stdin=None):
        """Run gate, job or route. Returns the finished process: returncode, stdout, stderr."""
        return subprocess.run([str(self.factory / "bin" / script), *args], cwd=self.repo, env=self.env,
                              input=stdin, capture_output=True, text=True)

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True,
                              capture_output=True, text=True).stdout

    # ---- files ----

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def read(self, path):
        return (self.repo / path).read_text()

    def set_config(self, **fields):
        self.write("factory/config.json", json.dumps({**json.loads(self.read("factory/config.json")), **fields}))

    def set_checks(self, body):
        self.write("factory/checks.sh", f"set -euo pipefail\n{body}\n")

    def job(self, job_id):
        return json.loads((self.jobs / job_id / "job.json").read_text())

    # ---- the fake GitHub ----

    @property
    def gh(self):
        return json.loads(self.state_path.read_text())

    def _edit(self, change):
        state = self.gh
        change(state)
        self.state_path.write_text(json.dumps(state))

    def add_issue(self, number, title, body="", labels=("factory",), author="alice", state="OPEN"):
        self._edit(lambda s: s["issues"].update({str(number): {
            "number": number, "title": title, "body": body, "author": {"login": author},
            "labels": [{"name": name} for name in labels], "state": state,
            "url": f"https://github.com/o/r/issues/{number}", "comments": []}}))

    def add_comment(self, number, login, body):
        self._edit(lambda s: s["issues"][str(number)]["comments"].append(
            {"author": {"login": login}, "body": body, "createdAt": "2026-01-02T00:00:00Z"}))

    def add_pr(self, number, head, draft=True, state="OPEN"):
        self._edit(lambda s: s["prs"].update({str(number): {
            "number": number, "head": head, "state": state, "isDraft": draft,
            "url": f"https://github.com/o/r/pull/{number}", "comments": []}}))

    def set_issue(self, number, **fields):
        self._edit(lambda s: s["issues"][str(number)].update(fields))

    def set_pr(self, number, **fields):
        self._edit(lambda s: s["prs"][str(number)].update(fields))

    def fail_once(self, prefix):
        """The next gh call starting with prefix fails."""
        self._edit(lambda s: s["fail_once"].append(prefix))

    def labels(self, number):
        return [label["name"] for label in self.gh["issues"][str(number)]["labels"]]

    def comments(self, number, kind="issues"):
        return [c["body"] for c in self.gh[kind][str(number)]["comments"]]

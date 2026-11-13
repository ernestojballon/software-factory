"""Black-box tests for factory/bin/gate: what it writes, prints and exits with.

Run: python3 -B -m unittest discover factory/tests
"""

import json
import unittest

from sandbox import Sandbox, masked

JOB = "001-x"


class Gate(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox(self)
        self.worktree = self.sb.repo / ".worktrees" / JOB
        self.sb.git(self.sb.repo, "worktree", "add", "-q", "-b", f"factory/{JOB}", str(self.worktree), "origin/main")
        self.round_dir = self.sb.jobs / JOB / "round-1"
        self.round_dir.mkdir(parents=True)
        self.write_job(round=1)

    def write_job(self, **fields):
        (self.sb.jobs / JOB / "job.json").write_text(json.dumps({"worktree": str(self.worktree), **fields}))

    def gate(self, *args, stdin=None):
        return self.sb.run("gate", *args, stdin=stdin)

    def commit(self):
        return self.sb.git(self.worktree, "rev-parse", "HEAD").strip()

    def report(self, name="checks"):
        return masked((self.round_dir / f"{name}.md").read_text())

    # ---- gate <job-id> ----

    def test_pass(self):
        res = self.gate(JOB)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(masked(res.stdout), f"GATE: PASS ({JOB} round 1, checks.sh exit 0 in Ns)\n")
        self.assertEqual(res.stderr, "")
        self.assertEqual((self.round_dir / "checks.log").read_text(), "ok\n")
        self.assertEqual(self.report(), (
            "GATE: PASS\n"
            "COMMIT: <commit>\n"
            "\n"
            "## Summary\n"
            "- checks.sh passed in Ns\n"
            "- Protected paths: none changed\n"
            f"- Commit {self.commit()[:7]}\n"
            "\n"
            "## Protected paths\n"
            "None changed.\n"
            "\n"
            "## checks.sh\n"
            "Exit 0 in Ns. Last 80 lines of checks.log:\n"
            "\n"
            "```\n"
            "ok\n"
            "```\n"
        ))
        self.assertIn(f"COMMIT: {self.commit()}\n", (self.round_dir / "checks.md").read_text())

    def test_failing_checks(self):
        self.sb.set_checks("echo out; echo err >&2; exit 3")
        res = self.gate(JOB)
        self.assertEqual(res.returncode, 1)
        self.assertEqual(masked(res.stdout), f"GATE: FAIL ({JOB} round 1, checks.sh exit 3 in Ns)\n")
        self.assertEqual((self.round_dir / "checks.log").read_text(), "out\nerr\n")
        report = self.report()
        self.assertTrue(report.startswith("GATE: FAIL\nCOMMIT: <commit>\n"))
        self.assertIn("- checks.sh failed with exit 3 in Ns\n", report)
        self.assertIn("Exit 3 in Ns. Last 80 lines of checks.log:\n\n```\nout\nerr\n```\n", report)

    def test_protected_paths(self):
        docs = self.worktree / "docs"
        (docs / "a.md").write_text("changed\n")
        (docs / "b.md").unlink()
        (self.worktree / "other").mkdir()
        (docs / "c.md").rename(self.worktree / "other" / "c.md")
        (docs / "new.md").write_text("new\n")
        self.sb.git(self.worktree, "add", "-A")
        self.sb.git(self.worktree, "commit", "-qm", "touch protected files")
        res = self.gate(JOB)
        self.assertEqual(res.returncode, 1)
        changed = "M docs/a.md, D docs/b.md, R docs/c.md"
        self.assertEqual(masked(res.stdout),
                         f"GATE: FAIL ({JOB} round 1, checks.sh exit 0 in Ns, protected paths changed: {changed})\n")
        self.assertEqual(self.report(), (
            "GATE: FAIL\n"
            "COMMIT: <commit>\n"
            "\n"
            "## Summary\n"
            "- checks.sh passed in Ns\n"
            f"- Protected paths: changed: {changed}\n"
            f"- Commit {self.commit()[:7]}\n"
            "\n"
            "## Protected paths\n"
            "- M docs/a.md\n"
            "- D docs/b.md\n"
            "- R docs/c.md\n"
            "\n"
            "## checks.sh\n"
            "Exit 0 in Ns. Last 80 lines of checks.log:\n"
            "\n"
            "```\n"
            "ok\n"
            "```\n"
        ))

    def test_a_pattern_star_also_matches_slashes(self):
        deep = self.worktree / "docs" / "deep" / "er"
        deep.mkdir(parents=True)
        (deep / "x.md").write_text("1\n")
        self.sb.git(self.worktree, "add", "-A")
        self.sb.git(self.worktree, "commit", "-qm", "add")
        self.assertEqual(self.gate(JOB).returncode, 0)  # adding under a protected path is fine
        (deep / "x.md").write_text("2\n")
        self.sb.git(self.worktree, "commit", "-qam", "change")
        self.sb.git(self.worktree, "push", "-q", "origin", f"HEAD~1:refs/heads/main")
        self.sb.git(self.worktree, "fetch", "-q", "origin", "main")
        res = self.gate(JOB)
        self.assertEqual(res.returncode, 1)
        self.assertIn("protected paths changed: M docs/deep/er/x.md", res.stdout)

    def test_timeout(self):
        self.sb.set_config(checks_timeout_seconds=1)
        self.sb.set_checks("echo started; sleep 30")
        res = self.gate(JOB)
        self.assertEqual(res.returncode, 1)
        self.assertEqual(masked(res.stdout), f"GATE: FAIL ({JOB} round 1, checks.sh exit 124 in Ns)\n")
        self.assertEqual((self.round_dir / "checks.log").read_text(),
                         "started\n\nchecks.sh timed out after 1s (config.json checks_timeout_seconds)\n")
        self.assertIn("- checks.sh failed with exit 124 in Ns\n", self.report())

    def test_report_keeps_the_last_80_lines(self):
        self.sb.set_checks("for i in $(seq 1 100); do echo line $i; done")
        self.gate(JOB)
        report = self.report()
        self.assertIn("```\nline 21\n", report)
        self.assertNotIn("line 20\n", report)
        self.assertEqual(len((self.round_dir / "checks.log").read_text().splitlines()), 100)

    def test_merge_writes_merge_checks(self):
        res = self.gate(JOB, "--merge")
        self.assertEqual(res.returncode, 0)
        self.assertFalse((self.round_dir / "checks.md").exists())
        self.assertEqual((self.round_dir / "merge-checks.log").read_text(), "ok\n")
        report = self.report("merge-checks")
        self.assertIn(f"- Commit {self.commit()[:7]}, synced with the latest main before merging\n", report)
        self.assertIn("Last 80 lines of merge-checks.log:", report)

    def test_checks_come_from_the_main_checkout(self):
        (self.worktree / "factory" / "checks.sh").write_text("exit 9\n")  # the branch's copy is ignored
        res = self.gate(JOB)
        self.assertEqual(res.returncode, 0)
        self.assertIn("checks.sh exit 0", res.stdout)

    def test_checks_run_in_the_worktree(self):
        self.sb.set_checks("pwd")
        self.gate(JOB)
        self.assertEqual((self.round_dir / "checks.log").read_text(), f"{self.worktree}\n")

    # ---- errors ----

    def test_round_zero(self):
        self.write_job(round=0)
        res = self.gate(JOB)
        self.assertEqual((res.returncode, res.stdout, res.stderr), (2, "", f"gate: {JOB} hasn't started a build round\n"))

    def test_git_failure(self):
        plain = self.sb.tmp / "plain"
        plain.mkdir()
        (self.sb.jobs / JOB / "job.json").write_text(json.dumps({"worktree": str(plain), "round": 1}))
        res = self.gate(JOB)
        self.assertEqual(res.returncode, 2)
        self.assertTrue(res.stderr.startswith("gate: `git rev-parse HEAD` failed: "), res.stderr)

    def test_bad_arguments_print_usage(self):
        for args in ([], ["-x"], [JOB, "--other"], [JOB, "--merge", "extra"]):
            res = self.gate(*args)
            self.assertEqual(res.returncode, 2, args)
            self.assertEqual(res.stdout, "", args)
            self.assertTrue(res.stderr.startswith("gate: The deterministic gate: "), args)
            self.assertIn("\nUsage:\n  gate <job-id> ", res.stderr, args)

    # ---- gate --hook ----

    def hook(self, content=f"Job: {JOB}. Job folder: x/. Main checkout: y.", **event):
        transcript = self.sb.tmp / "agent.jsonl"
        transcript.write_text(json.dumps({"type": "user", "message": {"content": content}}) + "\n")
        return self.gate("--hook", stdin=json.dumps({"agent_transcript_path": str(transcript), **event}))

    def test_hook_passes_quietly(self):
        res = self.hook()
        self.assertEqual((res.returncode, res.stdout, res.stderr), (0, "", ""))
        self.assertEqual(list(self.round_dir.iterdir()), [])  # a hook writes no report

    def test_hook_blocks_on_failing_checks(self):
        self.sb.set_checks("for i in $(seq 1 50); do echo line $i; done; exit 4")
        res = self.hook()
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertTrue(res.stderr.startswith(
            "factory/checks.sh fails in your worktree (exit 4). The gate will reject this build. "
            "Fix it, or write STATUS: BLOCKED in build.md if you can't.\n\nline 11\n"), res.stderr)
        self.assertTrue(res.stderr.endswith("line 50\n"))

    def test_hook_reads_a_prompt_made_of_blocks(self):
        self.sb.set_checks("exit 4")
        res = self.hook(content=[{"type": "text", "text": f"Job: {JOB}. Job folder"}, {"type": "image"}])
        self.assertEqual(res.returncode, 2)

    def test_hook_nudges_only_once(self):
        self.sb.set_checks("exit 4")
        res = self.hook(stop_hook_active=True)
        self.assertEqual((res.returncode, res.stdout, res.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main()

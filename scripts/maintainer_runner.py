#!/usr/bin/env python3
"""Maintainer's hands: the host-side worker that actually runs Claude Code.

Orion runs in a container with a read-only view of your projects and no git, no gh and no
claude. That is deliberate — the thing that decides what work is worth doing has no ability to
do it. This script is the other half: it runs on the host as you, claims approved work from
Orion over HTTP, and does the engineering where the tools and your credentials already live.

    claim  ->  worktree  ->  claude -p  ->  verify  ->  commit  ->  push  ->  gh pr create

Four rules it enforces in code, not in a prompt:

  * It only ever works inside its own worktrees, under the configured `worktrees` directory.
    Your checkouts — including FinStrive's dirty feature branch — are never read, reset or
    even visited. Every run starts from `origin/<base>`.
  * It only ever pushes branches named `maintainer/*`, never with --force, never to a base
    branch. There is no code path here that merges anything.
  * Anthropic credentials come from your Claude Code OAuth session in ~/.claude. Orion's own
    secrets — including ANTHROPIC_API_KEY, whose account has no credit — are scrubbed from the
    child environment, so a stray key cannot silently redirect billing or fail the run.
  * A heartbeat goes up with every progress batch. If this process dies, Orion's sweep marks
    the run failed within the quarter hour instead of leaving it in flight forever.

Run it as a systemd --user service (see the unit shipped alongside this file):

    ORION_URL=http://127.0.0.1:8020 MAINTAINER_RUNNER_TOKEN=... python scripts/maintainer_runner.py
    python scripts/maintainer_runner.py --once     # claim at most one task, then exit
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib import error, request

ORION_URL = os.environ.get("ORION_URL", "http://127.0.0.1:8020").rstrip("/")
TOKEN = os.environ.get("MAINTAINER_RUNNER_TOKEN", "")
RUNNER = os.environ.get("MAINTAINER_RUNNER_NAME") or platform.node() or "host"
POLL_SECONDS = float(os.environ.get("MAINTAINER_POLL_SECONDS", "20"))

#: Never handed to Claude Code. ANTHROPIC_API_KEY matters most: Orion keeps one for a provider
#: that is disabled on a $0 account, and its presence would override the OAuth session that
#: actually pays for these runs.
_SCRUB = ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
          "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "GMAIL_SENDER_ADDRESS",
          "GMAIL_SENDER_APP_PASSWORD", "MAINTAINER_RUNNER_TOKEN")

_BRANCH_PREFIX = "maintainer/"
#: Kept between runs so `npm ci` is paid for once per repo, not once per task.
_KEEP_ON_CLEAN = ("node_modules", ".next", "dist", "bin", "obj", "venv", ".venv")


def log(msg: str) -> None:
    print(f"[maintainer] {msg}", flush=True)


# -- talking to Orion ------------------------------------------------------
def api(path: str, payload: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    """One POST to Orion. Returns {} on any failure — a runner must outlive a restarting Orion."""
    body = json.dumps(payload or {}).encode()
    req = request.Request(f"{ORION_URL}/plugins/maintainer{path}", data=body, method="POST",
                          headers={"Content-Type": "application/json",
                                   "Authorization": f"Bearer {TOKEN}"})
    try:
        with request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except error.HTTPError as e:
        detail = e.read()[:200].decode(errors="replace")
        log(f"orion said {e.code} on {path}: {detail}")
    except (error.URLError, OSError, json.JSONDecodeError) as e:
        log(f"cannot reach orion on {path}: {e}")
    return {}


class Feed:
    """Progress batched up to Orion. Each flush doubles as the heartbeat."""

    def __init__(self, run_id: int):
        self.run_id = run_id
        self.buffer: list[dict[str, str]] = []
        self.last = 0.0

    def add(self, kind: str, text: str) -> None:
        self.buffer.append({"kind": kind, "text": text[:1000]})
        if len(self.buffer) >= 12 or time.time() - self.last > 20:
            self.flush()

    def flush(self) -> None:
        batch, self.buffer, self.last = self.buffer, [], time.time()
        api(f"/runner/runs/{self.run_id}/events", {"events": batch} if batch else {"events": []})


# -- shelling out ----------------------------------------------------------
def run(cmd: list[str] | str, cwd: str | Path, timeout: int = 300,
        env: dict[str, str] | None = None) -> tuple[int, str]:
    """A command, its exit code and its combined output. Shell only when given a string,
    because per-repo verify commands are written as shell (`npm run build && npm test`)."""
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), shell=isinstance(cmd, str), timeout=timeout,
                              capture_output=True, text=True, env=env)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as e:
        return 127, str(e)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def git(cwd: str | Path, *args: str, timeout: int = 300) -> tuple[int, str]:
    return run(["git", *args], cwd, timeout)


# -- the worktree ----------------------------------------------------------
def worktree_for(job: dict[str, Any]) -> Path:
    """One persistent worktree per repo, under the configured root and nowhere else."""
    root = Path(job.get("worktrees") or "~/.local/share/orion-maintainer").expanduser()
    return (root / "wt" / job["repo"]["name"]).resolve()


def assert_safe(worktree: Path, repo_root: Path, job: dict[str, Any]) -> None:
    """The guard that makes everything after it safe to write.

    Anything destructive below (reset --hard, clean -xdf) runs against this path, so it is
    checked to be inside Maintainer's own directory and outside the user's checkout before a
    single file is touched. A misconfiguration should stop the run, not eat a working tree.
    """
    root = Path(job.get("worktrees") or "~/.local/share/orion-maintainer").expanduser().resolve()
    if not str(worktree).startswith(str(root) + os.sep):
        raise RuntimeError(f"refusing to work in {worktree}: outside {root}")
    if worktree == repo_root or str(repo_root).startswith(str(worktree) + os.sep):
        raise RuntimeError(f"refusing to work in {worktree}: that is the real checkout")


def prepare(job: dict[str, Any], feed: Feed) -> Path:
    """Bring a clean worktree up at origin/<base> on a fresh branch."""
    repo, base, branch = job["repo"], job["repo"]["base"], job["branch"]
    repo_root = Path(repo["root"]).resolve()
    wt = worktree_for(job)
    assert_safe(wt, repo_root, job)

    feed.add("step", f"fetching {repo['name']}")
    code, out = git(repo_root, "fetch", "origin", "--prune")
    if code != 0:
        raise RuntimeError(f"git fetch failed: {out.strip()[:300]}")

    if not (wt / ".git").exists():
        wt.parent.mkdir(parents=True, exist_ok=True)
        if wt.exists():
            shutil.rmtree(wt)
        feed.add("step", f"creating a worktree at {wt}")
        code, out = git(repo_root, "worktree", "add", "--detach", str(wt), f"origin/{base}")
        if code != 0:
            raise RuntimeError(f"git worktree add failed: {out.strip()[:300]}")

    # Reset to the base branch and drop everything a previous run left behind, keeping the
    # installed dependencies so a build does not re-download the internet every night.
    keep = [arg for name in _KEEP_ON_CLEAN for arg in ("-e", name)]
    for args in (("checkout", "--detach", f"origin/{base}"),
                 ("reset", "--hard", f"origin/{base}"),
                 ("clean", "-xdf", *keep),
                 ("checkout", "-B", branch, f"origin/{base}")):
        code, out = git(wt, *args)
        if code != 0:
            raise RuntimeError(f"git {args[0]} failed in the worktree: {out.strip()[:300]}")

    feed.add("step", f"on {branch}, branched from origin/{base}")
    return wt


def install_if_needed(job: dict[str, Any], wt: Path, feed: Feed) -> None:
    repo = job["repo"]
    cmd, marker = repo.get("install"), repo.get("install_marker")
    if not cmd or (marker and (wt / marker).exists()):
        return
    minutes = float((job.get("runner") or {}).get("install_minutes", 20) or 20)
    feed.add("step", f"installing dependencies: {cmd}")
    code, out = run(cmd, wt, timeout=int(minutes * 60))
    if code != 0:
        feed.add("error", f"dependency install failed: {out.strip()[-400:]}")


# -- the prompt ------------------------------------------------------------
def build_prompt(job: dict[str, Any]) -> str:
    task, repo = job["task"], job["repo"]
    lines = [
        f"You are working in a git worktree of the {repo['name']} project, freshly branched "
        f"from origin/{repo['base']}. {repo.get('blurb', '')}".strip(),
        "",
        "## The task",
        task["title"],
        "",
        task["brief"],
    ]
    if task.get("acceptance"):
        lines += ["", f"Done when: {task['acceptance']}"]
    if task.get("files"):
        lines += ["", f"Likely relevant: {', '.join(task['files'][:10])}"]

    lines += [
        "",
        "## How to work here",
        "- Make the change and keep it tightly scoped to the task above. A reviewer should be "
        "able to read the whole diff in one sitting.",
        "- Do NOT commit, push, or open a pull request. Stop when the edits are made; the "
        "surrounding system commits and opens the PR for review.",
        "- Do not touch .env files, secrets, credentials, deployment or CI configuration.",
        "- Do not upgrade or add dependencies unless the task cannot be done without it.",
        "- Match the surrounding code's style, naming and comment density.",
    ]
    if repo.get("verify"):
        lines.append(f"- This will be checked with `{repo['verify']}`. Run it yourself before "
                     "you finish and fix what you break.")
    if job.get("changelog"):
        lines.append(f"- Append a short, dated entry to {job['changelog']} describing the "
                     "change, matching the format already in that file.")
    lines += [
        "- If the task turns out to be already done, wrong, or unsafe, change nothing and say "
        "so plainly. A run that correctly does nothing is a good outcome.",
        "",
        "Finish with one short paragraph describing what you changed and why. That paragraph "
        "becomes the pull request description the user reads.",
    ]
    return "\n".join(lines)


# -- driving Claude Code ---------------------------------------------------
def child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _SCRUB}
    env["CLAUDE_CODE_ENTRYPOINT"] = "orion-maintainer"
    return env


def run_claude(job: dict[str, Any], wt: Path, feed: Feed) -> dict[str, Any]:
    """Drive one headless Claude Code session, streaming its progress up to Orion.

    Never pass --bare: it forces ANTHROPIC_API_KEY auth and would bill an account with no
    credit instead of using the OAuth session that pays for interactive Claude Code.
    """
    cfg = job.get("claude") or {}
    minutes = float((job.get("runner") or {}).get("max_run_minutes", 45) or 45)
    cmd = [
        cfg.get("bin", "claude"), "-p", build_prompt(job),
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", cfg.get("permission_mode", "acceptEdits"),
        "--model", str(cfg.get("model", "sonnet")),
        "--max-turns", str(int(cfg.get("max_turns", 40) or 40)),
    ]
    feed.add("step", f"handing the task to claude ({cfg.get('model', 'sonnet')})")

    started = time.time()
    result: dict[str, Any] = {"turns": 0, "cost_usd": 0.0, "summary": "", "error": None}
    try:
        proc = subprocess.Popen(cmd, cwd=str(wt), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=child_env())
    except OSError as e:
        result["error"] = f"could not start claude: {e}"
        return result

    # Two daemons, because reading stdout blocks: a watchdog that can actually end a stalled
    # session, and a heartbeat so Orion can tell "thinking hard" from "this process is gone".
    # Without the watchdog a silent hang would block this runner forever, since the deadline
    # would only ever be checked when the next line of output arrived — which never comes.
    timed_out = threading.Event()

    def _kill() -> None:
        timed_out.set()
        proc.kill()

    watchdog = threading.Timer(minutes * 60, _kill)
    watchdog.daemon = True
    watchdog.start()

    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(60):
            feed.flush()

    heart = threading.Thread(target=_beat, daemon=True)
    heart.start()

    try:
        for line in proc.stdout:                       # one JSON object per line
            _consume(line, feed, result)
    finally:
        stop.set()
        watchdog.cancel()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        stderr = (proc.stderr.read() if proc.stderr else "") or ""

    result["duration_s"] = round(time.time() - started, 1)
    if timed_out.is_set():
        result["error"] = f"claude ran past its {minutes:.0f} minute limit and was stopped"
    elif result.get("error") is None and proc.returncode not in (0, None):
        result["error"] = f"claude exited {proc.returncode}: {stderr.strip()[-400:]}"
    feed.flush()
    return result


def _consume(line: str, feed: Feed, result: dict[str, Any]) -> None:
    """Turn one stream-json line into a progress event and, at the end, the run's accounting."""
    line = line.strip()
    if not line:
        return
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return

    kind = msg.get("type")
    if kind == "assistant":
        for block in (msg.get("message") or {}).get("content") or []:
            if block.get("type") == "text" and block.get("text", "").strip():
                feed.add("text", block["text"].strip())
            elif block.get("type") == "tool_use":
                feed.add("tool", f"{block.get('name', 'tool')} {_tool_hint(block.get('input'))}")
    elif kind == "result":
        result["turns"] = int(msg.get("num_turns") or 0)
        result["cost_usd"] = float(msg.get("total_cost_usd") or 0.0)
        result["summary"] = str(msg.get("result") or "")[:4000]
        if msg.get("is_error"):
            result["error"] = str(msg.get("result") or msg.get("subtype") or "claude reported an error")[:400]
        feed.add("step", f"claude finished after {result['turns']} turns")


def _tool_hint(payload: Any) -> str:
    """A few words about what a tool call was for — enough to follow along, never a payload."""
    if not isinstance(payload, dict):
        return ""
    for key in ("file_path", "path", "command", "pattern", "query", "url"):
        if payload.get(key):
            return str(payload[key])[:120]
    return ""


# -- publishing ------------------------------------------------------------
def diffstat(wt: Path) -> dict[str, int]:
    _, raw = git(wt, "diff", "--cached", "--numstat")
    files = ins = dels = 0
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            files += 1
            ins += int(parts[0]) if parts[0].isdigit() else 0
            dels += int(parts[1]) if parts[1].isdigit() else 0
    return {"files_changed": files, "insertions": ins, "deletions": dels}


def verify(job: dict[str, Any], wt: Path, feed: Feed) -> tuple[str, str]:
    cmd = (job["repo"].get("verify") or "").strip()
    if not cmd:
        return "skipped", ""
    minutes = float((job.get("runner") or {}).get("max_run_minutes", 45) or 45)
    feed.add("step", f"verifying: {cmd}")
    code, out = run(cmd, wt, timeout=int(minutes * 60))
    tail = out.strip()[-1500:]
    feed.add("step" if code == 0 else "error",
             "verification passed" if code == 0 else f"verification failed: {tail[-300:]}")
    return ("passed" if code == 0 else "failed"), tail


def publish(job: dict[str, Any], wt: Path, feed: Feed, summary: str,
            verdict: str, tail: str) -> dict[str, Any]:
    """Commit, push the branch, open the pull request. The only outward-facing step."""
    task, repo, branch = job["task"], job["repo"], job["branch"]
    if not branch.startswith(_BRANCH_PREFIX):
        raise RuntimeError(f"refusing to push '{branch}': not a {_BRANCH_PREFIX}* branch")
    if branch == repo["base"]:
        raise RuntimeError("refusing to push to the base branch")

    body = "\n".join([
        summary.strip() or task["brief"],
        "",
        "---",
        f"**Task** {task['title']}",
        f"**Why** {task.get('rationale') or 'proposed by Orion Maintainer'}",
        f"**Verification** `{repo.get('verify') or 'none configured'}` — {verdict}",
        *([f"```\n{tail[-1200:]}\n```"] if verdict == "failed" and tail else []),
        "",
        "Opened by Orion's Maintainer agent from a brief its user approved. Nothing here was "
        "merged, and no other branch was touched — review, then merge if you want it.",
    ])

    code, out = git(wt, "commit", "-m", f"{task['title']}\n\n{summary.strip()[:1500]}\n\n"
                                        "Opened by Orion Maintainer.")
    if code != 0:
        raise RuntimeError(f"commit failed: {out.strip()[:300]}")
    _, sha = git(wt, "rev-parse", "HEAD")

    feed.add("step", f"pushing {branch}")
    code, out = git(wt, "push", "--set-upstream", "origin", branch)
    if code != 0:
        raise RuntimeError(f"push failed: {out.strip()[:300]}")

    pr_url = ""
    if shutil.which("gh"):
        code, out = run(["gh", "pr", "create", "--base", repo["base"], "--head", branch,
                         "--title", f"Maintainer: {task['title']}"[:120], "--body", body],
                        wt, timeout=120)
        line = next((l for l in out.splitlines() if l.startswith("http")), "")
        if code == 0 and line:
            pr_url = line.strip()
            feed.add("step", f"opened {pr_url}")
        else:
            feed.add("error", f"branch is pushed but gh could not open a PR: {out.strip()[-300:]}")
    else:
        feed.add("error", "gh is not installed, so the branch is pushed but no PR was opened")

    return {"commit_sha": sha.strip()[:12], "pr_url": pr_url,
            "pr_state": "open" if pr_url else None}


# -- one task, end to end --------------------------------------------------
def handle(job: dict[str, Any]) -> None:
    task, run_id = job["task"], job["run_id"]
    feed = Feed(run_id)
    log(f"task {task['id']}: {task['repo']} — {task['title']}")
    outcome: dict[str, Any] = {"ok": False}

    try:
        wt = prepare(job, feed)
        job["changelog"] = _changelog_target(wt)
        install_if_needed(job, wt, feed)

        session = run_claude(job, wt, feed)
        outcome.update({k: session.get(k) for k in ("turns", "cost_usd", "duration_s")})
        outcome["summary"] = session.get("summary") or ""
        outcome["branch"] = job["branch"]
        if session.get("error"):
            outcome["error"] = session["error"]
            return

        git(wt, "add", "-A")
        stat = diffstat(wt)
        outcome.update(stat)
        # A reviewable change is a small one. A diff in the hundreds means something went wrong
        # — a generated tree, a dependency directory the repo never gitignored — and a pull
        # request that size is not review, it is archaeology. Refuse it before it is pushed.
        ceiling = int((job.get("runner") or {}).get("max_files", 200) or 200)
        if stat["files_changed"] > ceiling:
            raise RuntimeError(f"refusing to open a pull request touching "
                               f"{stat['files_changed']} files (the limit is {ceiling})")
        if stat["files_changed"] == 0:
            feed.add("step", "claude changed nothing")
            outcome.update({"ok": True, "verify": "skipped",
                            "summary": (outcome["summary"] or "No change was needed.")})
            return

        verdict, tail = verify(job, wt, feed)
        outcome.update({"verify": verdict, "verify_tail": tail})
        outcome.update(publish(job, wt, feed, outcome["summary"], verdict, tail))
        outcome["ok"] = True
    except Exception as e:                     # a failed task must never stop the runner
        outcome["error"] = str(e)[:400]
        feed.add("error", str(e)[:400])
        log(f"task {task['id']} failed: {e}")
    finally:
        feed.flush()
        api(f"/runner/runs/{run_id}/result", outcome)
        log(f"task {task['id']}: {'done' if outcome.get('ok') else 'failed'}")


def _changelog_target(wt: Path) -> str:
    """Which changelog exists in this branch — asking a run to edit a file that is gitignored
    would either vanish from the diff or drag the user's private notes into the PR."""
    for candidate in ("Checkpoint/CHANGELOG.md", "CHANGELOG.md"):
        code, _ = git(wt, "ls-files", "--error-unmatch", candidate, timeout=30)
        if code == 0:
            return candidate
    return ""


def refresh_prs() -> None:
    """Tell Orion which pull requests have since been merged or closed."""
    if not shutil.which("gh"):
        return
    states: dict[str, str] = {}
    code, out = run(["gh", "search", "prs", "--author", "@me", "--state", "all", "--limit", "40",
                     "--json", "url,state"], Path.home(), timeout=60)
    if code != 0:
        return
    try:
        for pr in json.loads(out or "[]"):
            states[pr["url"]] = str(pr.get("state", "")).lower()
    except (json.JSONDecodeError, KeyError, TypeError):
        return
    if states:
        api("/runner/prs", {"states": states})


def main() -> int:
    parser = argparse.ArgumentParser(description="Orion Maintainer host runner")
    parser.add_argument("--once", action="store_true",
                        help="claim at most one task, then exit")
    args = parser.parse_args()

    if not TOKEN:
        log("MAINTAINER_RUNNER_TOKEN is not set — nothing can be claimed. Add it to "
            "config/secrets.json and to ~/.config/orion/maintainer.env.")
        return 1
    for tool in ("git", "claude"):
        if not shutil.which(tool):
            log(f"{tool} is not on PATH; this runner cannot work without it")
            return 1
    if not shutil.which("gh"):
        log("warning: gh is missing — branches will be pushed but no PR can be opened "
            "(pacman -S github-cli && gh auth login && gh auth setup-git)")

    log(f"runner '{RUNNER}' polling {ORION_URL}")
    idle = 0
    while True:
        job = api("/runner/claim", {"runner": RUNNER})
        if job.get("task"):
            handle(job)
            idle = 0
            if args.once:
                return 0
            continue
        if args.once:
            log("nothing approved to work on")
            return 0
        idle += 1
        if idle % 30 == 0:                    # roughly every ten minutes of quiet
            refresh_prs()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("stopped")

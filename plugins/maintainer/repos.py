"""The repository registry, and reading a repo without ever touching its working tree.

Two constraints shape every function here.

**The mount is read-only.** ``/home/nox/Developing-Environment`` is bind-mounted into the
container with ``:ro``, so nothing in this process can modify a repo even by accident. Only
plain-read git commands are used; ``git status`` is deliberately absent because it wants to
refresh the index and would fail on a read-only tree.

**The working tree is not the truth.** FinStrive sits on a feature branch with thirty dirty
files; ArcVe develops on ``dev``. A brief drafted from whatever happens to be checked out would
describe code the run will never see, because every run branches from ``origin/<base>``. So
every read goes through git at that same ref — ``git show origin/main:README.md``, not
``open("README.md")``. What the scan reads is exactly what Codex will get.

Git is a read dependency of the container now (added to the Dockerfile). If it is missing this
degrades to "no facts", the scan proposes nothing, and nothing crashes.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from orion.core.config import config

log = logging.getLogger("orion.maintainer")

# Repos are owned by the host user; the container is root. Rather than a global git config
# baked into the image, every call carries the exemption so the reason travels with the code.
_SAFE = ("-c", "safe.directory=*")
_TIMEOUT = 30


def settings() -> dict[str, Any]:
    return config.section("maintainer")


def workspace() -> Path:
    return Path(settings().get("workspace", "/home/nox/Developing-Environment"))


def scan_cfg() -> dict[str, Any]:
    return settings().get("scan") or {}


def all_repos(enabled_only: bool = True) -> list[dict[str, Any]]:
    """Every configured repo, resolved to an absolute path."""
    out = []
    for name, cfg in (settings().get("repos") or {}).items():
        if name.startswith("//") or not isinstance(cfg, dict):
            continue
        if enabled_only and not cfg.get("enabled", True):
            continue
        out.append({**{k: v for k, v in cfg.items() if not k.startswith("//")},
                    "name": name,
                    "root": str(workspace() / cfg.get("path", name)),
                    "base": cfg.get("base", "main")})
    return out


def get(name: str) -> dict[str, Any] | None:
    for repo in all_repos(enabled_only=False):
        if repo["name"] == name:
            return repo
    return None


# -- reading a repo at origin/<base> ---------------------------------------
def _git(root: str, *args: str) -> str:
    """One read-only git command. Returns "" on any failure — git missing, bad ref, no repo."""
    try:
        proc = subprocess.run(("git", *_SAFE, "-C", root, *args),
                              capture_output=True, text=True, timeout=_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        log.info("git unavailable for %s: %s", root, e)
        return ""
    if proc.returncode != 0:
        log.debug("git %s in %s: %s", args[0], root, proc.stderr.strip()[:200])
        return ""
    return proc.stdout


def head_sha(repo: dict[str, Any]) -> str:
    """The sha of ``origin/<base>`` — what a run would actually branch from."""
    return _git(repo["root"], "rev-parse", f"origin/{repo['base']}").strip()


def show(repo: dict[str, Any], path: str, limit: int = 4000) -> str:
    """A file's contents at ``origin/<base>``, never from the working tree."""
    return _git(repo["root"], "show", f"origin/{repo['base']}:{path}")[:limit]


def log_since(repo: dict[str, Any], sha: str | None, count: int) -> str:
    """Recent commit subjects. Since the last scan's sha when we have one, else the last N."""
    ref = f"origin/{repo['base']}"
    rng = f"{sha}..{ref}" if sha else ref
    out = _git(repo["root"], "log", rng, f"-n{count}", "--pretty=format:%h %ad %s",
               "--date=short")
    return out[:4000]


def tree(repo: dict[str, Any], limit: int = 60) -> list[str]:
    """Top-level entries at the base ref — cheap orientation for the model."""
    raw = _git(repo["root"], "ls-tree", "--name-only", f"origin/{repo['base']}")
    return [line for line in raw.splitlines() if line][:limit]


_SOURCE_EXTS = {".cs", ".go", ".java", ".js", ".jsx", ".md", ".py", ".rs", ".ts", ".tsx"}


def files(repo: dict[str, Any], limit: int = 500) -> list[str]:
    """Tracked, human-authored files available for a rotating nightly audit."""
    raw = _git(repo["root"], "ls-tree", "-r", "--name-only", f"origin/{repo['base']}")
    skip = scan_cfg().get("skip") or []
    out = []
    for path in raw.splitlines():
        if Path(path).suffix.lower() not in _SOURCE_EXTS:
            continue
        if any(part and part in path for part in skip):
            continue
        out.append(path)
        if len(out) >= limit:
            break
    return out


def source_sample(repo: dict[str, Any], focus: str, limit: int = 5) -> dict[str, str]:
    """Small source excerpts ranked for this night's audit lens.

    Reading a few real implementation files lets an unchanged repository still reveal useful
    work. The scan remains bounded: Codex only runs after approval, and the cheap proposer sees
    at most ``limit`` excerpts here.
    """
    paths = files(repo)
    keywords = {
        "correctness": ("parser", "service", "store", "engine", "core", "api"),
        "tests": ("test", "spec", "parser", "service", "core"),
        "reliability": ("error", "client", "api", "service", "store", "worker", "runner"),
        "maintainability": ("core", "service", "util", "helper", "manager", "engine"),
        "documentation": ("readme", "docs/", "agents.md", "config", "manifest"),
    }.get(focus, ())

    def rank(path: str) -> tuple[int, int, int, str]:
        lower = path.lower()
        keyword_rank = next((i for i, word in enumerate(keywords) if word in lower), len(keywords))
        low_signal = 0
        if focus != "documentation":
            low_signal += 3 if lower.endswith(".md") else 0
            low_signal += 2 if any(part in lower for part in
                                   ("/interfaces/", "/dto/", "/types/", ".d.ts")) else 0
            low_signal += 1 if focus != "tests" and any(part in lower for part in
                                                         ("/test/", "/tests/", ".test.", ".spec.")) else 0
        # Prefer shallower files when no focus keyword distinguishes them; they tend to be
        # entry points and are more informative than a random deeply nested implementation.
        return low_signal, keyword_rank, lower.count("/"), lower

    sample = {}
    for path in sorted(paths, key=rank)[:limit]:
        content = show(repo, path, 2200)
        if content:
            sample[path] = content
    return sample


#: Lockfiles and bundles are full of base64 that matches any loose marker pattern — an early
#: run came back with npm integrity hashes because "XXX" appears inside sha512 blobs.
_GREP_EXCLUDE = (":!*lock*", ":!*.min.*", ":!*.map", ":!*.svg", ":!dist/*", ":!.next/*")


def todos(repo: dict[str, Any], limit: int = 20) -> list[str]:
    """TODO/FIXME/HACK markers at the base ref, skipping vendored and generated paths."""
    skip = scan_cfg().get("skip") or []
    raw = _git(repo["root"], "grep", "-n", "-I", "-E", r"\b(TODO|FIXME|HACK|XXX)\b",
               f"origin/{repo['base']}", "--", *_GREP_EXCLUDE)
    hits = []
    for line in raw.splitlines():
        # `git grep <ref>` prefixes every hit with "ref:path:line:text"
        body = line.split(":", 1)[1] if line.startswith("origin/") else line
        path = body.split(":")[0]
        if any(part and part in path for part in skip):
            continue
        hits.append(body[:200])
        if len(hits) >= limit:
            break
    return hits


def read_file(repo: dict[str, Any], relpath: str, limit: int = 4000) -> str:
    """A file from the working tree, for notes git does not track.

    The Checkpoint folders that ``AGENT_REQUIREMENTS.md`` describes are gitignored in most of
    these projects — real, useful, and invisible to ``git show``. They are worth reading as
    context even though a run can never modify them.
    """
    try:
        path = Path(repo["root"]) / relpath
        if not path.is_file():
            return ""
        return path.read_text(errors="replace")[:limit]
    except OSError:
        return ""


def changelog_target(repo: dict[str, Any]) -> str:
    """The changelog a run may actually append to — i.e. one that is tracked at the base ref.

    The workspace contract asks for a ``Checkpoint/CHANGELOG.md`` entry per change, but only
    noxctl tracks that folder; elsewhere it is local-only. Instructing a run to edit an
    untracked file would either vanish from the PR or, worse, add the user's private notes to
    it. So the instruction is issued only where it can land.
    """
    for candidate in ("Checkpoint/CHANGELOG.md", "CHANGELOG.md"):
        if _tracked(repo, candidate):
            return candidate
    return ""


def _tracked(repo: dict[str, Any], relpath: str) -> bool:
    out = _git(repo["root"], "ls-tree", "--name-only", f"origin/{repo['base']}", "--", relpath)
    return bool(out.strip())


def digest(repo: dict[str, Any], since_sha: str | None = None, *, focus: str = "correctness",
           previous_tasks: list[str] | None = None) -> dict[str, Any]:
    """Everything the brief-drafting model is allowed to see about one repo.

    Facts only. The model shapes these into proposals; it is never asked to recall a repo it
    cannot read, which is what keeps a brief from describing code that does not exist.
    """
    cfg = scan_cfg()
    return {
        "repo": repo["name"],
        "blurb": repo.get("blurb", ""),
        "base_branch": repo["base"],
        "audit_focus": focus,
        "top_level": tree(repo),
        "readme": show(repo, "README.md", 3000),
        # the Checkpoint notes are gitignored in most of these projects; read them off disk
        "current_state": read_file(repo, "Checkpoint/CURRENT_STATE.md", 4000),
        "recent_changelog": (read_file(repo, "Checkpoint/CHANGELOG.md", 2000)
                             or show(repo, "CHANGELOG.md", 2000)),
        "recent_commits": log_since(repo, since_sha, int(cfg.get("commits", 25) or 25)),
        "todo_markers": todos(repo, int(cfg.get("todo_hits", 20) or 20)),
        "source_excerpts": source_sample(
            repo, focus, int(cfg.get("source_files", 5) or 5)),
        "recently_considered_tasks": previous_tasks or [],
        "has_agent_instructions": bool(show(repo, "AGENTS.md", 1) or show(repo, "CLAUDE.md", 1)),
        "changelog_to_update": changelog_target(repo),
    }

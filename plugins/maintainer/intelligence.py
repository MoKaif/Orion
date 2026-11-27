"""Deterministic engineering observations. Never pull, fetch, install, build or run project code.

Local tracking refs are labelled cached. Network verification uses ls-remote, which does not
write checkout metadata. A different remote SHA is reported without guessing commit counts.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import registry, store

_LOCK = threading.Lock()
_ENVIRONMENT: tuple[float, dict] = (0, {})
_MAX_FILE = 256_000
_REMOTE_FRESH_SECONDS = 7200


def git(root: Path, *args: str, network: bool = False) -> tuple[int, str]:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
           "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=8"}
    try:
        proc = subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
                               "-c", "core.hooksPath=/dev/null", "-c", "credential.interactive=false",
                               "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
                               "-c", "protocol.ssh.allow=always",
                               "-C", str(root), *args], env=env, timeout=20 if network else 10,
                              capture_output=True, text=True, errors="replace")
        # stderr can contain credential-bearing URLs or filenames; expose a fixed failure instead.
        return proc.returncode, proc.stdout if proc.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return 124, ""


def remote_url(raw: str) -> str:
    """Display only. Never persist userinfo, passwords, query parameters or fragments."""
    try:
        parsed = urlsplit(raw)
        if parsed.scheme:
            host = parsed.hostname or ""
            if parsed.port:
                host += f":{parsed.port}"
            return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        if re.match(r"^[^/@:]+@[^/:]+:.+$", raw):
            return raw.split("@", 1)[1]
    except ValueError:
        return "[invalid remote]"
    return "[local or unsupported remote]" if raw else ""


def _remote_allowed(raw: str) -> bool:
    # No file, ext::, executable transport or arbitrary upload-pack configuration.
    return bool(re.match(r"^(https://|ssh://)[^\s]+$", raw)
                or re.match(r"^[\w.-]+@[\w.-]+:[\w./-]+$", raw))


def git_observation(root: Path) -> dict:
    code, output = git(root, "status", "--porcelain=v2", "--branch", "-z",
                       "--untracked-files=normal")
    if code:
        return {"states": ["unavailable"], "error": "Repository path or Git metadata unavailable"}
    result: dict = {"states": [], "staged": 0, "unstaged": 0, "untracked": 0,
                    "conflicted": 0, "ahead": None, "behind": None}
    records = iter(output.split("\0"))
    fingerprint = hashlib.sha256(output.encode()).hexdigest()
    for line in records:
        if line.startswith("# branch.oid "):
            result["head"] = line.removeprefix("# branch.oid ").strip()
        elif line.startswith("# branch.head "):
            result["branch"] = line.removeprefix("# branch.head ").strip()
        elif line.startswith("# branch.upstream "):
            result["tracking"] = line.removeprefix("# branch.upstream ").strip()
        elif line.startswith("# branch.ab "):
            match = re.search(r"\+(\d+) -(\d+)", line)
            if match:
                result["ahead"], result["behind"] = map(int, match.groups())
        elif line.startswith(("1 ", "2 ")):
            xy = line.split(" ", 2)[1]
            result["staged"] += int(xy[0] != ".")
            result["unstaged"] += int(xy[1] != ".")
            if line.startswith("2 "):
                next(records, None)  # rename's original path is a separate NUL record
        elif line.startswith("? "):
            result["untracked"] += 1
        elif line.startswith("u "):
            result["conflicted"] += 1
    result["status_fingerprint"] = fingerprint
    states = result["states"]
    if result.get("branch") == "(detached)":
        states.append("detached_head")
    if result["staged"] or result["unstaged"] or result["untracked"] or result["conflicted"]:
        states.append("uncommitted_changes")
    if not result.get("tracking"):
        states.append("no_tracking_branch")
    if result["ahead"]:
        states.append("unpushed_commits")
    if result["behind"]:
        states.append("cached_remote_updates")
    if result["ahead"] and result["behind"]:
        states.append("diverged")
    if not any(result[k] for k in ("staged", "unstaged", "untracked", "conflicted")):
        states.append("clean_worktree")
    _, raw = git(root, "remote", "get-url", "origin")
    result["origin"] = remote_url(raw.strip())
    if not raw.strip():
        states.append("missing_origin")
    _, gitdir = git(root, "rev-parse", "--absolute-git-dir")
    if gitdir:
        directory = Path(gitdir.strip())
        for marker, state in (("MERGE_HEAD", "merge_in_progress"), ("rebase-merge", "rebase_in_progress"),
                              ("rebase-apply", "rebase_in_progress"),
                              ("CHERRY_PICK_HEAD", "cherry_pick_in_progress")):
            if (directory / marker).exists() and state not in states:
                states.append(state)
    _, branches = git(root, "for-each-ref", "--format=%(refname:short)%09%(upstream:short)",
                      "refs/heads/")
    result["branches_without_tracking"] = [line.split("\t")[0] for line in branches.splitlines()
                                            if line.endswith("\t")]
    if result.get("tracking"):
        _, tracked = git(root, "rev-parse", "--verify", "@{upstream}")
        result["tracking_sha"] = tracked.strip() or None
    result["counts_source"] = "local cached tracking references; no fetch performed"
    return result


def read(root: Path, relative: str) -> str | None:
    """Only fixed, non-secret manifests, confined even when a repository uses symlinks."""
    try:
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file() or path.stat().st_size > _MAX_FILE:
            return None
        return path.read_text(errors="replace")
    except OSError:
        return None


def manifest_observation(root: Path) -> dict:
    components = []
    stack: set[str] = set()
    managers: set[str] = set()
    evidence = []
    for relative in ("package.json", "frontend/package.json", "server/web/package.json",
                     "interfaces/spa/package.json", "web/package.json"):
        text = read(root, relative)
        if not text:
            continue
        try:
            package = json.loads(text)
            if not isinstance(package, dict):
                continue
            version = package.get("version")
            components.append({"path": relative, "name": str(package.get("name") or relative)[:160],
                               "version": str(version)[:80] if version is not None else None})
            deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
            stack.add("JavaScript / TypeScript")
            for dep, label in (("react", "React"), ("next", "Next.js"), ("vite", "Vite"),
                               ("express", "Express")):
                if dep in deps:
                    stack.add(label)
            managers.add(str(package.get("packageManager", "npm")).split("@")[0][:40])
            evidence.append({"path": relative, "sha256": hashlib.sha256(text.encode()).hexdigest()})
        except (ValueError, TypeError):
            evidence.append({"path": relative, "error": "Invalid package manifest"})
    for relative, label, manager in (("pyproject.toml", "Python", "pip"),
                                     ("requirements.txt", "Python", "pip"),
                                     ("go.mod", "Go", "go"), ("server/go.mod", "Go", "go"),
                                     ("Cargo.toml", "Rust", "cargo"),
                                     ("CMakeLists.txt", "C++ / CMake", "cmake"),
                                     ("api/api.csproj", ".NET", "dotnet")):
        text = read(root, relative)
        if text is not None:
            stack.add(label)
            managers.add(manager)
            evidence.append({"path": relative, "sha256": hashlib.sha256(text.encode()).hexdigest()})
    text = read(root, "orion/__init__.py")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)', text or "", re.M)
    if match:
        components.insert(0, {"path": "orion/__init__.py", "name": "Orion", "version": match[1]})
    text = read(root, "cmake/Version.cmake")
    if text:
        parts = [re.search(rf"set\(\s*STRAWBERRY_VERSION_{part}\s+(\d+)", text, re.I)
                 for part in ("MAJOR", "MINOR", "PATCH")]
        if all(parts):
            components.insert(0, {"path": "cmake/Version.cmake", "name": root.name,
                                  "version": ".".join(m[1] for m in parts)})
    changelog = next((p for p in ("CHANGELOG.md", "Checkpoint/CHANGELOG.md", "CHANGELOG", "NEWS")
                      if (root / p).is_file()), None)
    return {"stack": sorted(stack), "package_managers": sorted(managers),
            "components": components, "evidence": evidence, "changelog": changelog,
            "source": "working_tree", "build_status": "unknown", "test_status": "unknown",
            "dependency_health": "not_assessed"}


def release_observation(root: Path, manifests: dict, policy: dict) -> dict:
    _, tags = git(root, "for-each-ref", "--sort=-version:refname", "--format=%(refname:short)",
                   "refs/tags/")
    prefix = policy.get("tag_prefix", "v")
    pattern = r"\d{4}\.\d{1,2}(?:\.\d+)?" if policy.get("versioning") == "calendar" \
        else r"\d+\.\d+\.\d+(?:-[\w.-]+)?"
    valid = [tag for tag in tags.splitlines() if re.fullmatch(re.escape(prefix) + pattern, tag)]
    if policy.get("versioning") == "unversioned":
        valid = []
    latest_tag = valid[0] if valid else None
    result = {"policy": policy or None, "status": "policy_required" if not policy else "unverified",
              "local_tag": latest_tag, "tag_source": "local_only", "commits_since_tag": None,
              "components": manifests["components"], "changelog": manifests["changelog"]}
    if latest_tag:
        code, count = git(root, "rev-list", "--count", f"{latest_tag}..HEAD")
        if not code and count.strip().isdigit():
            result["commits_since_tag"] = int(count.strip())
        code, _ = git(root, "merge-base", "--is-ancestor", latest_tag, "HEAD")
        result["tag_is_ancestor"] = code == 0
    # A monorepo's package versions are not assumed to be one application version.
    if policy.get("versioning") == "unversioned":
        result["status"] = "unversioned"
        return result
    path = policy.get("version_path")
    selected = next((c for c in manifests["components"] if c["path"] == path), None)
    if selected:
        result["version"] = selected["version"]
        expected = prefix + str(selected["version"])
        _, _tags = git(root, "tag", "--list", expected)
        result["version_tag_exists_locally"] = expected in _tags.splitlines()
        if result["version_tag_exists_locally"]:
            code, sha = git(root, "rev-parse", f"{expected}^{{commit}}")
            result["version_tag_sha"] = sha.strip() if not code else None
        result["status"] = "observed"  # readiness still requires verified build/test/release evidence
    return result


def environment() -> dict:
    global _ENVIRONMENT
    if time.monotonic() - _ENVIRONMENT[0] < 86400:
        return _ENVIRONMENT[1]
    tools = {}
    for name, args in {"python": ["python", "--version"], "node": ["node", "--version"],
                       "npm": ["npm", "--version"], "pnpm": ["pnpm", "--version"],
                       "go": ["go", "version"], "dotnet": ["dotnet", "--list-sdks"],
                       "cmake": ["cmake", "--version"], "qmake6": ["qmake6", "-query", "QT_VERSION"],
                       "git": ["git", "--version"], "docker": ["docker", "--version"],
                       "bwrap": ["bwrap", "--version"]}.items():
        executable = shutil.which(args[0])
        item = {"available": bool(executable), "ready": False, "version": None}
        if executable:
            try:
                proc = subprocess.run([executable, *args[1:]], cwd="/", capture_output=True,
                                      text=True, timeout=5)
                item["ready"] = proc.returncode == 0 and bool(proc.stdout.strip())
                item["version"] = proc.stdout.strip().splitlines()[0][:120] if item["ready"] else None
            except (OSError, subprocess.TimeoutExpired):
                pass
        tools[name] = item
    result = {"os": platform.system(), "architecture": platform.machine(), "tools": tools,
              "observed_at": store.now(), "source": "orion_service_host",
              "isolation_verified": False}
    _ENVIRONMENT = (time.monotonic(), result)
    return result


def verify_remote(c, project: dict, local: dict) -> None:
    root = registry.confined(project["path"])
    _, raw = git(root, "remote", "get-url", "origin")
    raw = raw.strip()
    display = remote_url(raw)
    stamp = store.now()
    refs = {}
    error = "No origin remote is configured" if not raw else "Only HTTPS or SSH origin transports are supported"
    if raw and _remote_allowed(raw):
        code, output = git(root, "-c", "remote.origin.uploadpack=git-upload-pack", "ls-remote",
                           "--symref", "origin", "HEAD", "refs/heads/*", network=True)
        if not code:
            error = None
            for line in output.splitlines():
                fields = line.split("\t", 1)
                if len(fields) == 2 and re.fullmatch(r"[a-f0-9]{40,64}", fields[0]):
                    refs[fields[1]] = fields[0]
        else:
            error = "Remote lookup timed out" if code == 124 else \
                f"Remote lookup failed (Git exit {code}); check access, authentication and connectivity"
    old = c.execute("SELECT * FROM project_remote_observations WHERE project_id=?",
                    (project["id"],)).fetchone()
    # Preserve last successful evidence on a transient failure, but expose the failure too.
    previous = old and old["origin"] == display
    verified = stamp if error is None else old["verified_at"] if previous else None
    saved_refs = json.dumps(refs) if error is None else old["refs"] if previous else "{}"
    c.execute("INSERT INTO project_remote_observations VALUES(?,?,?,?,?,?) "
              "ON CONFLICT(project_id) DO UPDATE SET attempted_at=excluded.attempted_at, "
              "verified_at=excluded.verified_at,origin=excluded.origin,refs=excluded.refs,error=excluded.error",
              (project["id"], stamp, verified, display, saved_refs, error))
    c.commit()


def remote_status(c, project_id: str, local: dict) -> dict:
    row = c.execute("SELECT * FROM project_remote_observations WHERE project_id=?",
                    (project_id,)).fetchone()
    if row is None or row["origin"] != local.get("origin"):
        return {"state": "not_verified", "verified_at": None}
    result = dict(row)
    result.pop("project_id")
    refs = json.loads(result.pop("refs"))
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["verified_at"])).total_seconds() \
        if row["verified_at"] else float("inf")
    tracking = local.get("tracking", "")
    branch = tracking.removeprefix("origin/") if tracking.startswith("origin/") else None
    remote_sha = refs.get(f"refs/heads/{branch}") if branch else None
    result["tracking_sha"] = remote_sha
    result["state"] = "unavailable" if row["error"] else "stale" if age > _REMOTE_FRESH_SECONDS \
        else "verified" if branch else "no_origin_tracking_branch"
    if result["state"] == "verified":
        if not remote_sha:
            result["state"] = "tracking_branch_missing"
        elif remote_sha != local.get("tracking_sha"):
            result["state"] = "tracking_ref_changed"
        elif local.get("ahead") == 0 and local.get("behind") == 0:
            result["state"] = "synchronized"
    return result


def _refresh(*, discovery: bool = False, remote: bool = False) -> dict:
    if not _LOCK.acquire(blocking=False):
        return {"ok": True, "deferred": True, "reason": "Inspection already running"}
    c = None
    try:
        c = registry.conn()
        store.meta_set(c, "project_refresh", "running")
        added = registry.onboard(c, discovery=discovery)
        env = environment()
        scanned, errors = [], []
        for project in registry.projects(c):
            if project["status"] != "active":
                continue
            try:
                root = registry.confined(project["path"])
                local = git_observation(root)
                manifests = manifest_observation(root)
                policy = project["settings"].get("release") or {}
                if remote:
                    verify_remote(c, project, local)
                payload = {"git": local, "manifests": manifests, "environment": env,
                           "release": release_observation(root, manifests, policy),
                           "remote": remote_status(c, project["id"], local),
                           "configuration_revision": project["revision"]}
                registry.save_observation(c, project["id"], payload)
                scanned.append(project["name"])
            except (OSError, ValueError, TypeError, KeyError):
                errors.append(project["name"])
        store.meta_set(c, "project_refresh", "failed" if errors else "done")
        return {"ok": not errors, "observed": len(scanned), "registered": added,
                "errors": errors}
    except Exception:
        if c is not None:
            store.meta_set(c, "project_refresh", "failed")
        raise
    finally:
        if c is not None:
            c.close()
        _LOCK.release()


async def refresh(*, discovery: bool = False, remote: bool = False) -> dict:
    return await asyncio.to_thread(_refresh, discovery=discovery, remote=remote)


async def refresh_remotes() -> dict:
    return await refresh(remote=True)


def summary() -> dict:
    c = registry.conn()
    try:
        projects = []
        for project in registry.projects(c):
            observation = registry.latest(c, project["id"])
            if observation:
                payload = observation["payload"]
                # Freshness is evaluated at read time, not frozen at collection time.
                payload["remote"] = remote_status(c, project["id"], payload["git"])
                observation["stale"] = (datetime.now(timezone.utc) - datetime.fromisoformat(
                    observation["observed_at"])).total_seconds() > 1800
                observation["configuration_stale"] = payload.get("configuration_revision") != project["revision"]
            projects.append({**project, "observation": observation})
        return {"projects": projects, "refresh": store.meta_get(c, "project_refresh"),
                "execution": {"enabled": False, "reason": "Revision-bound approvals and isolated "
                              "verification are required before automatic development resumes."},
                "capabilities": {"local_git": True, "remote_refs": True,
                                 "environment": True, "release_observations": True,
                                 "upstream_compatibility": False, "dependency_advisories": False,
                                 "release_publication": False}}
    finally:
        c.close()

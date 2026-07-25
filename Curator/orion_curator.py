from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
ANSI_RESET = "\033[0m"
ANSI_COLORS = {
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE,
    hash TEXT,
    status TEXT,
    last_scanned DATETIME,
    last_modified DATETIME
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY,
    note_path TEXT,
    action_type TEXT,
    reason TEXT,
    confidence REAL,
    executed_at DATETIME
);

CREATE TABLE IF NOT EXISTS links (
    source TEXT,
    target TEXT
);

CREATE TABLE IF NOT EXISTS tags (
    note_path TEXT,
    tag TEXT
);

CREATE TABLE IF NOT EXISTS agent_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS llm_reviews (
    note_path TEXT,
    hash TEXT,
    reviewed_at DATETIME,
    outcome TEXT,
    PRIMARY KEY(note_path, hash)
);

CREATE TABLE IF NOT EXISTS llm_skips (
    note_path TEXT,
    hash TEXT,
    reason TEXT,
    skipped_at DATETIME,
    PRIMARY KEY(note_path, hash)
);
"""


TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "auth": ("auth", "authentication", "authorization", "oauth", "jwt", "token"),
    "backend": ("api", "backend", "server", "database", "sql", "endpoint"),
    "frontend": ("frontend", "ui", "react", "angular", "css", "html"),
    "devops": ("docker", "kubernetes", "pipeline", "deployment", "ci/cd", "jenkins"),
    "database": ("database", "sql", "sqlite", "postgres", "mysql", "index"),
    "debugging": ("bug", "error", "exception", "stack trace", "debug"),
    "design": ("design", "architecture", "diagram", "flow", "wireframe"),
    "jira": ("jira", "ticket", "story", "subtask", "sprint"),
    "git": ("git", "commit", "branch", "merge", "rebase"),
    "linux": ("linux", "unix", "shell", "bash", "powershell"),
}


@dataclass(frozen=True)
class Action:
    type: str
    reason: str
    confidence: float


@dataclass(frozen=True)
class Plan:
    note: str
    summary: str
    confidence: float
    actions: tuple[Action, ...]
    updated_content: str
    audit: dict[str, str]

    def to_json(self) -> dict[str, Any]:
        return {
            "note": self.note,
            "summary": self.summary,
            "confidence": self.confidence,
            "actions": [action.__dict__ for action in self.actions],
            "updated_content": self.updated_content,
            "audit": self.audit,
        }


class Logger:
    def __init__(self, verbose: bool = True) -> None:
        self.verbose = verbose
        self.use_color = sys.stdout.isatty()
        if self.use_color and os.name == "nt":
            self._enable_windows_ansi()

    def _enable_windows_ansi(self) -> None:
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            self.use_color = False

    def line(self, label: str, message: str, color: str = "white") -> None:
        if not self.verbose:
            return
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        prefix = f"[{stamp}] {label:<8}"
        if self.use_color:
            prefix = f"{ANSI_COLORS.get(color, '')}{prefix}{ANSI_RESET}"
        print(f"{prefix} {message}", flush=True)

    def scan(self, message: str) -> None:
        self.line("SCAN", message, "cyan")

    def plan(self, message: str) -> None:
        self.line("PLAN", message, "blue")

    def llm(self, message: str) -> None:
        self.line("QWEN", message, "magenta")

    def apply(self, message: str) -> None:
        self.line("APPLY", message, "green")

    def warn(self, message: str) -> None:
        self.line("WARN", message, "yellow")

    def error(self, message: str) -> None:
        self.line("ERROR", message, "red")

    def sleep(self, message: str) -> None:
        self.line("SLEEP", message, "dim")


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def memory_status() -> dict[str, float] | None:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    total_gb = status.ullTotalPhys / (1024**3)
    available_gb = status.ullAvailPhys / (1024**3)
    used_gb = total_gb - available_gb
    return {"load": float(status.dwMemoryLoad), "used_gb": used_gb, "total_gb": total_gb}


def perf_summary(start_time: float, start_process_time: float) -> str:
    elapsed = max(time.perf_counter() - start_time, 0.001)
    process_cpu = max(time.process_time() - start_process_time, 0.0)
    cpu_hint = min(100.0, (process_cpu / elapsed) * 100.0)
    memory = memory_status()
    if memory:
        return (
            f"cycle={elapsed:.1f}s python_cpu~{cpu_hint:.0f}% "
            f"ram={memory['used_gb']:.1f}/{memory['total_gb']:.1f}GB ({memory['load']:.0f}%)"
        )
    return f"cycle={elapsed:.1f}s python_cpu~{cpu_hint:.0f}%"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    config["vaultPath"] = str(Path(config["vaultPath"]).resolve())
    config["databasePath"] = str((APP_DIR / config["databasePath"]).resolve())
    config["backupFolder"] = str((APP_DIR / config["backupFolder"]).resolve())
    return config


def connect_db(config: dict[str, Any]) -> sqlite3.Connection:
    conn = sqlite3.connect(config["databasePath"])
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def note_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def relative_note_path(vault: Path, path: Path) -> str:
    return path.relative_to(vault).as_posix()


def is_allowed(path: Path, vault: Path, config: dict[str, Any]) -> bool:
    rel_parts = path.relative_to(vault).parts
    ignored = set(config.get("ignoredFolders", []))
    if any(part in ignored for part in rel_parts):
        return False

    allowed = config.get("allowedFolders", [])
    if not allowed:
        return True
    return bool(rel_parts and rel_parts[0] in set(allowed))


def iter_markdown_files(config: dict[str, Any]) -> list[Path]:
    vault = Path(config["vaultPath"])
    if not vault.exists():
        raise FileNotFoundError(f"Vault path does not exist: {vault}")

    return sorted(
        path
        for path in vault.rglob("*.md")
        if path.is_file() and is_allowed(path, vault, config)
    )


def read_note(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def split_frontmatter(content: str) -> tuple[dict[str, Any], str, bool]:
    if not content.startswith("---\n"):
        return {}, content, False

    end = content.find("\n---", 4)
    if end == -1:
        return {}, content, False

    raw = content[4:end].strip()
    body = content[end + len("\n---") :]
    if body.startswith("\n"):
        body = body[1:]

    data: dict[str, Any] = {}
    current_key: str | None = None
    for line in raw.splitlines():
        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, []).append(line[4:].strip())
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            current_key = key
            data[key] = [] if value == "" else value

    return data, body, True


def frontmatter_bounds(content: str) -> tuple[int, int] | None:
    if not content.startswith("---\n"):
        return None
    end = content.find("\n---", 4)
    if end == -1:
        return None
    closing_end = end + len("\n---")
    if closing_end < len(content) and content[closing_end] == "\n":
        closing_end += 1
    return 0, closing_end


def render_new_frontmatter(data: dict[str, Any], body: str) -> str:
    lines = ["---"]
    for key in sorted(data):
        value = data[key]
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in sorted(dict.fromkeys(str(entry).strip() for entry in value if str(entry).strip())):
                lines.append(f"  - {item}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body.lstrip("\n")


def iter_non_code_lines(content: str) -> list[str]:
    in_code = False
    lines: list[str] = []
    for line in content.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            lines.append(line)
    return lines


def cleanup_markdown(content: str) -> str:
    in_code = False
    cleaned: list[str] = []
    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            cleaned.append(line)
            continue
        cleaned.append(line if in_code else line.rstrip())

    updated = "\n".join(cleaned).rstrip() + "\n"
    return updated


def extract_links(content: str) -> list[str]:
    return sorted(set(match.split("|", 1)[0].strip() for match in re.findall(r"\[\[([^\]]+)\]\]", content)))


def obsidian_link_path(relative_path: str) -> str:
    return relative_path[:-3] if relative_path.lower().endswith(".md") else relative_path


def backlink_link(relative_path: str) -> str:
    target = obsidian_link_path(relative_path)
    title = Path(relative_path).stem
    return f"[[{target}|{title}]]"


def note_lookup_key(value: str) -> str:
    clean = value.strip().replace("\\", "/")
    if "|" in clean:
        clean = clean.split("|", 1)[0]
    if clean.lower().endswith(".md"):
        clean = clean[:-3]
    return clean.lower()


def build_note_lookup(vault: Path, paths: list[Path]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for path in paths:
        rel = relative_note_path(vault, path)
        no_ext = obsidian_link_path(rel)
        lookup[note_lookup_key(rel)] = rel
        lookup[note_lookup_key(no_ext)] = rel
        lookup[note_lookup_key(Path(rel).stem)] = rel
    return lookup


def build_inbound_backlinks(config: dict[str, Any]) -> dict[str, list[str]]:
    vault = Path(config["vaultPath"])
    paths = iter_markdown_files(config)
    lookup = build_note_lookup(vault, paths)
    inbound: dict[str, set[str]] = {}

    for path in paths:
        source = relative_note_path(vault, path)
        for raw_target in extract_links(read_note(path)):
            target = lookup.get(note_lookup_key(raw_target))
            if target and target != source:
                inbound.setdefault(target, set()).add(source)

    return {target: sorted(sources) for target, sources in inbound.items()}


def add_backlinks_section(content: str, sources: list[str], config: dict[str, Any]) -> tuple[str, list[str]]:
    section = str(config.get("backlinkSection", "Backlinks")).strip() or "Backlinks"
    max_links = int(config.get("maxBacklinksPerNote", 20))
    links = [backlink_link(source) for source in sources[:max_links]]
    existing = {note_lookup_key(link) for link in extract_links(content)}
    missing = [link for link in links if note_lookup_key(link.strip("[]")) not in existing]
    if not missing:
        return content, []

    heading_pattern = re.compile(rf"(?m)^##\s+{re.escape(section)}\s*$")
    match = heading_pattern.search(content)
    bullets = [f"- {link}" for link in missing]

    if not match:
        updated = content.rstrip() + f"\n\n## {section}\n" + "\n".join(bullets) + "\n"
        return updated, missing

    insert_at = match.end()
    following = content[insert_at:]
    next_heading = re.search(r"(?m)^\#{1,6}\s+", following)
    if next_heading:
        insert_at += next_heading.start()
        updated = content[:insert_at].rstrip() + "\n" + "\n".join(bullets) + "\n\n" + content[insert_at:].lstrip("\n")
    else:
        updated = content.rstrip() + "\n" + "\n".join(bullets) + "\n"
    return updated, missing


def add_links_section(content: str, section: str, target_paths: list[str], max_links: int) -> tuple[str, list[str]]:
    links = [backlink_link(target) for target in target_paths[:max_links]]
    existing = {note_lookup_key(link) for link in extract_links(content)}
    missing = [link for link in links if note_lookup_key(link.strip("[]")) not in existing]
    if not missing:
        return content, []

    heading_pattern = re.compile(rf"(?m)^##\s+{re.escape(section)}\s*$")
    match = heading_pattern.search(content)
    bullets = [f"- {link}" for link in missing]

    if not match:
        updated = content.rstrip() + f"\n\n## {section}\n" + "\n".join(bullets) + "\n"
        return updated, missing

    insert_at = match.end()
    following = content[insert_at:]
    next_heading = re.search(r"(?m)^\#{1,6}\s+", following)
    if next_heading:
        insert_at += next_heading.start()
        updated = content[:insert_at].rstrip() + "\n" + "\n".join(bullets) + "\n\n" + content[insert_at:].lstrip("\n")
    else:
        updated = content.rstrip() + "\n" + "\n".join(bullets) + "\n"
    return updated, missing


def hub_note_path(config: dict[str, Any], hub_name: str) -> str:
    safe_name = re.sub(r'[<>:"/\\|?*]', "", hub_name).strip() or "Hub"
    folder = str(config.get("hubFolder", "Zettelkasten/Abstract")).strip().strip("/\\")
    return f"{folder}/{safe_name}.md"


def hub_note_content(hub_name: str, sources: list[str], config: dict[str, Any]) -> str:
    links = "\n".join(f"- {backlink_link(source)}" for source in sources[: int(config.get("maxBacklinksPerNote", 20))])
    return (
        "---\n"
        "tags:\n"
        "  - hub\n"
        "  - abstract\n"
        "---\n"
        f"# {hub_name}\n\n"
        "This is an abstract hub note maintained by Orion Curator.\n\n"
        f"## {config.get('backlinkSection', 'Backlinks')}\n"
        f"{links}\n"
    )


def classify_hubs(vault: Path, path: Path, content: str, config: dict[str, Any]) -> list[str]:
    rel = relative_note_path(vault, path)
    if rel.startswith(str(config.get("hubFolder", "Zettelkasten/Abstract")).replace("\\", "/").strip("/") + "/"):
        return []

    _, body, _ = split_frontmatter(content)
    searchable = " ".join([rel, path.stem, *iter_non_code_lines(body)]).lower()
    rel_parts = Path(rel).parts
    top_folder = rel_parts[0] if rel_parts else ""
    matched: list[tuple[int, str]] = []

    hubs = config.get("abstractHubs", {})
    if not isinstance(hubs, dict):
        return []

    for hub_name, rules in hubs.items():
        if not isinstance(rules, dict):
            continue
        score = 0
        folders = rules.get("folders", [])
        keywords = rules.get("keywords", [])
        if isinstance(folders, list) and top_folder in folders:
            score += 3
        if isinstance(keywords, list):
            score += sum(1 for keyword in keywords if str(keyword).lower() in searchable)
        if score > 0:
            matched.append((score, str(hub_name)))

    matched.sort(key=lambda item: (-item[0], item[1]))
    return [hub_name for _, hub_name in matched[: int(config.get("maxHubLinksPerNote", 3))]]


def build_hub_assignments(config: dict[str, Any]) -> dict[str, list[str]]:
    vault = Path(config["vaultPath"])
    assignments: dict[str, list[str]] = {}
    for path in iter_markdown_files(config):
        content = read_note(path)
        rel = relative_note_path(vault, path)
        for hub_name in classify_hubs(vault, path, content, config):
            assignments.setdefault(hub_name, []).append(rel)
    return {hub: sorted(sources) for hub, sources in assignments.items()}


def extract_tags(content: str) -> list[str]:
    frontmatter, body, _ = split_frontmatter(content)
    found: set[str] = set()

    existing = frontmatter.get("tags", [])
    if isinstance(existing, str):
        found.update(tag.strip(" #") for tag in existing.split(","))
    elif isinstance(existing, list):
        found.update(str(tag).strip(" #") for tag in existing)

    found.update(tag.strip(" #") for tag in re.findall(r"(?<!\w)#([A-Za-z][\w/-]*)", body))
    return sorted(tag for tag in found if tag)


def extract_code_fences(content: str) -> list[str]:
    return re.findall(r"```.*?```", content, flags=re.DOTALL)


def infer_tags(path: Path, content: str) -> list[str]:
    _, body, _ = split_frontmatter(content)
    searchable = " ".join([path.stem, *iter_non_code_lines(body)]).lower()
    tags: list[str] = []
    for tag, keywords in TAG_KEYWORDS.items():
        if any(keyword in searchable for keyword in keywords):
            tags.append(tag)
    return tags[:8]


def with_tags(content: str, tags: list[str]) -> str:
    bounds = frontmatter_bounds(content)
    current = extract_tags(content)
    merged = sorted(dict.fromkeys(tag for tag in [*current, *tags] if tag))

    if bounds is None:
        return render_new_frontmatter({"tags": merged}, content)

    start, end = bounds
    raw_frontmatter = content[start:end]
    body = content[end:]
    lines = raw_frontmatter.splitlines()

    tag_line_index = next((index for index, line in enumerate(lines) if line.strip().startswith("tags:")), None)
    rendered_tags = ["tags:", *[f"  - {tag}" for tag in merged]]

    if tag_line_index is None:
        updated_lines = [*lines[:-1], *rendered_tags, lines[-1]]
        return "\n".join(updated_lines) + "\n" + body.lstrip("\n")

    block_end = tag_line_index + 1
    while block_end < len(lines) and lines[block_end].startswith("  - "):
        block_end += 1

    updated_lines = [*lines[:tag_line_index], *rendered_tags, *lines[block_end:]]
    return "\n".join(updated_lines) + "\n" + body.lstrip("\n")


def ollama_generate(config: dict[str, Any], prompt: str) -> str:
    url = str(config.get("ollamaUrl", "http://localhost:11434")).rstrip("/") + "/api/generate"
    payload = {
        "model": config.get("ollamaModel", "qwen3.5:0.8b"),
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_predict": int(config.get("ollamaNumPredict", 4096)),
        },
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    timeout = int(config.get("ollamaTimeoutSeconds", 120))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    return str(body.get("response", ""))


def parse_json_response(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Ollama response was not a JSON object.")
    return value


def grammar_prompt(note_path: str, content: str) -> str:
    return f"""You are Orion Grammar Worker.

Only improve grammar, spelling, punctuation, and light readability.

Core rules:
- Preserve meaning exactly.
- Never invent facts.
- Never add tags or links.
- Never change code blocks.
- Never change quoted content.
- Never reorganize the note.
- Prefer no change when uncertain.
- Return ONLY valid JSON.

JSON format:
{{
  "summary": "Short description",
  "confidence": 0.0,
  "actions": [
    {{
      "type": "grammar_fix",
      "reason": "Specific reason",
      "confidence": 0.0
    }}
  ],
  "updated_content": "FULL UPDATED MARKDOWN",
  "audit": {{
    "reasoning": "Why this is safe",
    "risk": "none|low|medium|high"
  }}
}}

If no changes are needed, return:
{{
  "summary": "No action required",
  "confidence": 1.0,
  "actions": [],
  "updated_content": "",
  "audit": {{
    "reasoning": "No objective grammar improvements detected",
    "risk": "none"
  }}
}}

NOTE PATH:
{note_path}

NOTE CONTENT:
{content}
"""


def validate_llm_plan(original: str, candidate: dict[str, Any], config: dict[str, Any]) -> tuple[bool, str]:
    updated = candidate.get("updated_content", "")
    if not updated:
        return False, "no updated content"
    if not isinstance(updated, str):
        return False, "updated_content is not a string"
    if extract_code_fences(original) != extract_code_fences(updated):
        return False, "code fences changed"
    confidence = float(candidate.get("confidence", 0.0))
    if confidence < float(config.get("minLlmConfidence", 0.85)):
        return False, f"confidence {confidence:.2f} below threshold"
    original_len = max(len(original), 1)
    delta = abs(len(updated) - len(original)) / original_len
    if delta > float(config.get("maxLlmLengthDelta", 0.25)):
        return False, f"content length changed by {delta:.0%}"
    risk = str(candidate.get("audit", {}).get("risk", "medium")).lower()
    if risk not in {"none", "low"}:
        return False, f"risk is {risk}"
    return True, "accepted"


def llm_plan_for_note(vault: Path, path: Path, config: dict[str, Any], logger: Logger | None = None) -> tuple[Plan | None, str]:
    rel = relative_note_path(vault, path)
    original = read_note(path)
    if not original.strip():
        return None, "empty"
    max_chars = int(config.get("maxLlmChars", 6000))
    if len(original) > max_chars:
        if logger:
            logger.llm(f"skipping {rel}: {len(original)} chars exceeds maxLlmChars={max_chars}")
        return None, "too_large"

    if logger:
        logger.llm(f"asking {config.get('ollamaModel')} to review {rel}")

    try:
        response = ollama_generate(config, grammar_prompt(rel, original))
        payload = parse_json_response(response)
        valid, reason = validate_llm_plan(original, payload, config)
    except TimeoutError as exc:
        if logger:
            logger.warn(f"Ollama timed out for {rel}: {exc}")
        return None, "timeout"
    except urllib.error.URLError as exc:
        reason = str(exc.reason if hasattr(exc, "reason") else exc).lower()
        if "timed out" in reason or "timeout" in reason:
            if logger:
                logger.warn(f"Ollama timed out for {rel}: {exc}")
            return None, "timeout"
        if logger:
            logger.warn(f"Ollama skipped {rel}: {exc}")
        return None, "error"
    except (json.JSONDecodeError, ValueError) as exc:
        if logger:
            logger.warn(f"Ollama skipped {rel}: {exc}")
        return None, "error"

    if not valid:
        if logger:
            logger.warn(f"Ollama rejected {rel}: {reason}")
        return None, f"rejected: {reason}"

    raw_actions = payload.get("actions", [])
    actions: list[Action] = []
    if isinstance(raw_actions, list):
        for item in raw_actions:
            if isinstance(item, dict):
                actions.append(Action(
                    str(item.get("type", "grammar_fix")),
                    str(item.get("reason", "Grammar and readability improvement.")),
                    float(item.get("confidence", payload.get("confidence", 0.85))),
                ))

    if not actions:
        return None, "no_action"

    updated = cleanup_markdown(str(payload["updated_content"]))
    return Plan(
        note=rel,
        summary=str(payload.get("summary", "Grammar improvements prepared.")),
        confidence=float(payload.get("confidence", 0.85)),
        actions=tuple(actions),
        updated_content=updated,
        audit={
            "reasoning": str(payload.get("audit", {}).get("reasoning", "Ollama returned a validated low-risk grammar plan.")),
            "risk": str(payload.get("audit", {}).get("risk", "low")),
        },
    ), "accepted"


def plan_for_note(
    vault: Path,
    path: Path,
    config: dict[str, Any],
    inbound_backlinks: list[str] | None = None,
    hub_names: list[str] | None = None,
) -> Plan:
    rel = relative_note_path(vault, path)
    original = read_note(path)
    updated = original
    actions: list[Action] = []

    cleaned = cleanup_markdown(updated)
    if cleaned != updated:
        updated = cleaned
        actions.append(Action("markdown_cleanup", "Normalize line endings, remove trailing whitespace, and ensure one final newline.", 0.99))

    if config.get("autoTag", True):
        current_tags = set(extract_tags(updated))
        inferred = [tag for tag in infer_tags(path, updated) if tag not in current_tags]
        if inferred:
            updated = with_tags(updated, inferred)
            actions.append(Action("add_tags", f"Add evidence-based tags: {', '.join(inferred)}.", 0.82))

    if config.get("autoBacklink", True) and config.get("autoLink", True) and inbound_backlinks:
        linked, added_links = add_backlinks_section(updated, inbound_backlinks, config)
        if added_links:
            updated = linked
            actions.append(Action("add_backlinks", f"Add backlinks from notes that already link here: {', '.join(added_links)}.", 0.96))

    if config.get("autoHubLinks", True) and config.get("autoLink", True) and hub_names:
        hub_paths = [hub_note_path(config, hub_name) for hub_name in hub_names]
        linked, added_links = add_links_section(
            updated,
            str(config.get("hubLinkSection", "Related Hubs")),
            hub_paths,
            int(config.get("maxHubLinksPerNote", 3)),
        )
        if added_links:
            updated = linked
            actions.append(Action("add_hub_links", f"Link note to abstract hubs: {', '.join(added_links)}.", 0.9))

    if not actions:
        return Plan(
            note=rel,
            summary="No action required",
            confidence=1.0,
            actions=(),
            updated_content="",
            audit={"reasoning": "No objective improvements detected", "risk": "none"},
        )

    confidence = min(action.confidence for action in actions)
    return Plan(
        note=rel,
        summary=f"Prepared {len(actions)} conservative improvement(s).",
        confidence=confidence,
        actions=tuple(actions),
        updated_content=updated,
        audit={"reasoning": "Only deterministic, reversible edits were planned.", "risk": "low"},
    )


def scan(config: dict[str, Any], conn: sqlite3.Connection) -> dict[str, int]:
    vault = Path(config["vaultPath"])
    seen: set[str] = set()
    added = 0
    modified = 0
    now = utc_now()

    conn.execute("DELETE FROM links")
    conn.execute("DELETE FROM tags")

    for path in iter_markdown_files(config):
        rel = relative_note_path(vault, path)
        content = read_note(path)
        digest = note_hash(content)
        stat = path.stat()
        last_modified = dt.datetime.fromtimestamp(stat.st_mtime, dt.UTC).replace(microsecond=0).isoformat()
        existing = conn.execute("SELECT hash FROM notes WHERE path = ?", (rel,)).fetchone()

        if existing is None:
            added += 1
        elif existing[0] != digest:
            modified += 1

        conn.execute(
            """
            INSERT INTO notes(path, hash, status, last_scanned, last_modified)
            VALUES (?, ?, 'active', ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                hash = excluded.hash,
                status = 'active',
                last_scanned = excluded.last_scanned,
                last_modified = excluded.last_modified
            """,
            (rel, digest, now, last_modified),
        )

        for target in extract_links(content):
            conn.execute("INSERT INTO links(source, target) VALUES (?, ?)", (rel, target))
        for tag in extract_tags(content):
            conn.execute("INSERT INTO tags(note_path, tag) VALUES (?, ?)", (rel, tag))

        seen.add(rel)

    deleted_rows = conn.execute(
        "UPDATE notes SET status = 'deleted', last_scanned = ? WHERE status != 'deleted' AND path NOT IN (%s)"
        % ",".join("?" for _ in seen),
        (now, *seen) if seen else (now,),
    ).rowcount if seen else conn.execute(
        "UPDATE notes SET status = 'deleted', last_scanned = ? WHERE status != 'deleted'",
        (now,),
    ).rowcount

    conn.commit()
    return {"added": added, "modified": modified, "deleted": deleted_rows, "total": len(seen)}


def was_llm_reviewed(conn: sqlite3.Connection | None, note_path: str, digest: str) -> bool:
    if conn is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM llm_reviews WHERE note_path = ? AND hash = ?",
        (note_path, digest),
    ).fetchone()
    return row is not None


def llm_skip_reason(conn: sqlite3.Connection | None, note_path: str, digest: str) -> str | None:
    if conn is None:
        return None
    row = conn.execute(
        "SELECT reason FROM llm_skips WHERE note_path = ? AND hash = ?",
        (note_path, digest),
    ).fetchone()
    return str(row[0]) if row else None


def record_llm_skip(conn: sqlite3.Connection | None, note_path: str, digest: str, reason: str) -> None:
    if conn is None:
        return
    conn.execute(
        """
        INSERT INTO llm_skips(note_path, hash, reason, skipped_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(note_path, hash) DO UPDATE SET
            reason = excluded.reason,
            skipped_at = excluded.skipped_at
        """,
        (note_path, digest, reason, utc_now()),
    )
    conn.commit()


def record_llm_review(conn: sqlite3.Connection | None, note_path: str, digest: str, outcome: str) -> None:
    if conn is None or outcome == "error":
        return
    conn.execute(
        """
        INSERT INTO llm_reviews(note_path, hash, reviewed_at, outcome)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(note_path, hash) DO UPDATE SET
            reviewed_at = excluded.reviewed_at,
            outcome = excluded.outcome
        """,
        (note_path, digest, utc_now(), outcome),
    )
    conn.commit()


def build_plans(
    config: dict[str, Any],
    limit: int | None = None,
    logger: Logger | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[Plan]:
    vault = Path(config["vaultPath"])
    plans: list[Plan] = []
    llm_used = 0
    max_llm = int(config.get("maxLlmNotesPerRun", 0))
    inbound = build_inbound_backlinks(config) if config.get("autoBacklink", True) and config.get("autoLink", True) else {}
    hub_assignments = build_hub_assignments(config) if config.get("autoHubLinks", True) and config.get("autoLink", True) else {}
    source_to_hubs: dict[str, list[str]] = {}
    for hub_name, sources in hub_assignments.items():
        for source in sources:
            source_to_hubs.setdefault(source, []).append(hub_name)

    for hub_name, sources in hub_assignments.items():
        rel = hub_note_path(config, hub_name)
        path = vault / Path(rel)
        if path.exists():
            content = read_note(path)
            linked, added_links = add_backlinks_section(content, sources, config)
            if added_links:
                plans.append(Plan(
                    note=rel,
                    summary="Prepared abstract hub backlink updates.",
                    confidence=0.96,
                    actions=(Action("update_hub_backlinks", f"Add classified notes to hub: {', '.join(added_links)}.", 0.96),),
                    updated_content=linked,
                    audit={"reasoning": "Hub backlinks are based on deterministic folder and keyword classification.", "risk": "low"},
                ))
        else:
            plans.append(Plan(
                note=rel,
                summary="Prepared new abstract hub note.",
                confidence=0.92,
                actions=(Action("create_hub_note", f"Create abstract hub note for {hub_name}.", 0.92),),
                updated_content=hub_note_content(hub_name, sources, config),
                audit={"reasoning": "Hub note path is configured and sources were classified deterministically.", "risk": "low"},
            ))
        if limit and len(plans) >= limit:
            return plans

    for path in iter_markdown_files(config):
        rel = relative_note_path(vault, path)
        plan = plan_for_note(vault, path, config, inbound.get(rel, []), source_to_hubs.get(rel, []))
        if (
            config.get("useOllama", False)
            and config.get("autoFixGrammar", False)
            and llm_used < max_llm
        ):
            rel = relative_note_path(vault, path)
            digest = note_hash(read_note(path))
            skip_reason = llm_skip_reason(conn, rel, digest)
            if skip_reason:
                if logger:
                    logger.llm(f"skip-listed {rel}: {skip_reason}")
                llm_plan = None
            elif was_llm_reviewed(conn, rel, digest):
                if logger:
                    logger.llm(f"cached review for {rel}")
                llm_plan = None
            else:
                llm_plan, outcome = llm_plan_for_note(vault, path, config, logger)
                if outcome == "timeout" and config.get("skipTimedOutLlmNotes", True):
                    record_llm_skip(conn, rel, digest, "timeout")
                    if logger:
                        logger.llm(f"added to LLM skip list: {rel} (timeout)")
                elif outcome == "too_large":
                    record_llm_skip(conn, rel, digest, "too_large")
                    if logger:
                        logger.llm(f"added to LLM skip list: {rel} (too_large)")
                record_llm_review(conn, rel, digest, outcome)
                llm_used += 1
            if llm_plan:
                plan = merge_plans(plan, llm_plan)
        if plan.actions:
            plans.append(plan)
        if limit and len(plans) >= limit:
            break
    return plans


def merge_plans(base: Plan, llm_plan: Plan) -> Plan:
    if not base.actions:
        return llm_plan

    updated = base.updated_content or llm_plan.updated_content
    if llm_plan.updated_content:
        # Re-apply deterministic tag additions after the grammar worker so frontmatter changes are retained.
        for action in base.actions:
            if action.type == "add_tags":
                tags = [tag.strip() for tag in action.reason.split(":", 1)[-1].rstrip(".").split(",")]
                updated = with_tags(llm_plan.updated_content, [tag for tag in tags if tag])

    actions = (*base.actions, *llm_plan.actions)
    return Plan(
        note=base.note,
        summary=f"Prepared {len(actions)} improvement(s), including validated Ollama grammar work.",
        confidence=min(base.confidence, llm_plan.confidence),
        actions=actions,
        updated_content=updated,
        audit={"reasoning": "Deterministic edits plus validated low-risk Ollama grammar output.", "risk": "low"},
    )


def backup_note(config: dict[str, Any], vault: Path, path: Path) -> None:
    backup_root = Path(config["backupFolder"])
    rel = path.relative_to(vault)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_root / timestamp / rel
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)


def execute_plans(config: dict[str, Any], conn: sqlite3.Connection, plans: list[Plan], logger: Logger | None = None) -> int:
    vault = Path(config["vaultPath"])
    max_changes = int(config.get("maxChangesPerRun", 25))
    executed = 0

    for plan in plans[:max_changes]:
        if not plan.updated_content:
            continue

        note_path = vault / Path(plan.note)
        note_path.parent.mkdir(parents=True, exist_ok=True)
        if config.get("backupBeforeChanges", True) and note_path.exists():
            backup_note(config, vault, note_path)

        note_path.write_text(plan.updated_content, encoding="utf-8")
        executed += 1
        if logger:
            action_types = ", ".join(action.type for action in plan.actions)
            logger.apply(f"{plan.note} ({action_types}; confidence {plan.confidence:.2f})")

        for action in plan.actions:
            conn.execute(
                "INSERT INTO actions(note_path, action_type, reason, confidence, executed_at) VALUES (?, ?, ?, ?, ?)",
                (plan.note, action.type, action.reason, action.confidence, utc_now()),
            )

    conn.commit()
    return executed


def print_json(value: Any) -> None:
    output = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    sys.stdout.buffer.write(output.encode("utf-8"))


def command_scan(args: argparse.Namespace) -> None:
    config = load_config()
    with connect_db(config) as conn:
        print_json(scan(config, conn))


def command_plan(args: argparse.Namespace) -> None:
    config = load_config()
    plans = build_plans(config, args.limit)
    print_json([plan.to_json() for plan in plans])


def command_run(args: argparse.Namespace) -> None:
    config = load_config()
    if args.dry_run:
        config["applyChanges"] = False

    with connect_db(config) as conn:
        scan_result = scan(config, conn)
        plans = build_plans(config, args.limit, conn=conn)
        executed = execute_plans(config, conn, plans) if config.get("applyChanges") else 0
        if executed:
            scan_result = scan(config, conn)

    print_json({
        "scan": scan_result,
        "planned": len(plans),
        "executed": executed,
        "applyChanges": bool(config.get("applyChanges")),
        "plans": [plan.to_json() for plan in plans],
    })


def command_llm_skips(args: argparse.Namespace) -> None:
    config = load_config()
    with connect_db(config) as conn:
        rows = conn.execute(
            """
            SELECT note_path, reason, skipped_at
            FROM llm_skips
            ORDER BY skipped_at DESC, note_path
            """
        ).fetchall()
    print_json([
        {"note": note_path, "reason": reason, "skipped_at": skipped_at}
        for note_path, reason, skipped_at in rows
    ])


def command_clear_llm_skips(args: argparse.Namespace) -> None:
    config = load_config()
    with connect_db(config) as conn:
        if args.note:
            deleted = conn.execute(
                "DELETE FROM llm_skips WHERE note_path = ? OR note_path LIKE ?",
                (args.note, f"%{args.note}%"),
            ).rowcount
        else:
            deleted = conn.execute("DELETE FROM llm_skips").rowcount
        conn.commit()
    print_json({"deleted": deleted})


def command_loop(args: argparse.Namespace) -> None:
    config = load_config()
    logger = Logger(verbose=not args.json)
    interval = max(1, int(config.get("scanIntervalMinutes", 5))) * 60
    logger.line("ORION", f"vault={config['vaultPath']}", "white")
    logger.line(
        "ORION",
        f"applyChanges={config.get('applyChanges')} ollama={config.get('useOllama')} model={config.get('ollamaModel')}",
        "white",
    )
    while True:
        start_time = time.perf_counter()
        start_process_time = time.process_time()
        with connect_db(config) as conn:
            logger.scan("indexing markdown notes")
            scan_result = scan(config, conn)
            logger.scan(
                f"total={scan_result['total']} added={scan_result['added']} "
                f"modified={scan_result['modified']} deleted={scan_result['deleted']}"
            )
            logger.plan("building deterministic and Ollama-backed plans")
            plans = build_plans(config, args.limit, logger, conn)
            logger.plan(f"planned={len(plans)} max_changes={config.get('maxChangesPerRun')}")
            if config.get("applyChanges"):
                executed = execute_plans(config, conn, plans, logger)
            else:
                executed = 0
                logger.warn("applyChanges=false, no notes will be modified")
            if executed:
                scan_result = scan(config, conn)
        summary = {
            "time": utc_now(),
            "scan": scan_result,
            "planned": len(plans),
            "executed": executed,
            "performance": perf_summary(start_time, start_process_time),
        }
        if args.json:
            print_json(summary)
        else:
            logger.line("DONE", f"planned={len(plans)} executed={executed} {summary['performance']}", "green")
            logger.sleep(f"next cycle in {interval // 60} minute(s); press Ctrl+C to stop")
        if args.once:
            break
        time.sleep(interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Orion Curator: autonomous Obsidian vault maintenance.")
    subparsers = parser.add_subparsers(required=True)

    scan_parser = subparsers.add_parser("scan", help="Index vault notes into SQLite.")
    scan_parser.set_defaults(func=command_scan)

    plan_parser = subparsers.add_parser("plan", help="Print proposed note changes.")
    plan_parser.add_argument("--limit", type=int, default=None)
    plan_parser.set_defaults(func=command_plan)

    run_parser = subparsers.add_parser("run", help="Scan, plan, and optionally execute safe changes.")
    run_parser.add_argument("--limit", type=int, default=None)
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.set_defaults(func=command_run)

    skips_parser = subparsers.add_parser("llm-skips", help="List notes skipped by the Ollama worker.")
    skips_parser.set_defaults(func=command_llm_skips)

    clear_skips_parser = subparsers.add_parser("clear-llm-skips", help="Clear the Ollama skip list.")
    clear_skips_parser.add_argument("--note", default=None, help="Clear skips only for a matching note path.")
    clear_skips_parser.set_defaults(func=command_clear_llm_skips)

    loop_parser = subparsers.add_parser("loop", help="Continuously scan and plan on the configured interval.")
    loop_parser.add_argument("--limit", type=int, default=None)
    loop_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of colorful logs.")
    loop_parser.add_argument("--once", action="store_true", help="Run one loop cycle and exit.")
    loop_parser.set_defaults(func=command_loop)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

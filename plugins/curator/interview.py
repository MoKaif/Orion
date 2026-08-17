"""Memory interviews — gentle prompts that preserve the user's recollection verbatim.

This is deliberately not a form-completion engine.  Existing lists and thin notes are clues,
never obligations: Curator asks scene-shaped questions across a rotating set of life domains and
accepts "I don't remember" as a complete answer.  Only one prompt waits at a time.

Submitting an answer is the approval gate.  The answer is written exactly as supplied to a new
``Memory/`` note; generated metadata and the question sit around it, but Curator never rewrites
the raw recollection.  Derived entities and World Model claims still travel through their normal
review paths when the regular Curator passes encounter the note.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import llm, notes, store


@dataclass(frozen=True)
class Prompt:
    key: str
    category: str
    folder: str
    title: str
    question: str


# Hand-written cues are the dependable baseline.  They ask for scenes, routines and sensory
# anchors instead of presuming a named person or demanding a complete life inventory.
_PROMPTS = (
    Prompt("primary-classroom-seat", "Primary school", "Primary", "Where I sat in class",
           "When you picture your primary-school classroom, where were you sitting and who or what was usually nearby?"),
    Prompt("media-home-time", "Games and shows", "Nostalgic Media", "A show worth getting home for",
           "Was there a cartoon or show that affected what time you wanted to be home? What do you remember about watching it?"),
    Prompt("building-gathering-place", "Building life", "Building", "Where the building children gathered",
           "Was there a particular place in or around your building where children gathered? What would usually happen there?"),
    Prompt("college-between-classes", "College", "College", "Between college classes",
           "When there was time between college classes, where did you usually go and what did those gaps feel like?"),
    Prompt("family-power-cut", "Home and family", "Family", "What happened during a power cut",
           "When the electricity went out at home, what did everyone usually do? Is there one power cut you can still picture?"),
    Prompt("first-game-obsession", "Games and shows", "Nostalgic Media", "The first game obsession",
           "Which game did you first become properly obsessed with, and how did you discover it?"),
    Prompt("school-recess-routine", "Primary school", "Primary", "A normal school recess",
           "What normally happened during recess at school—where did you go, what did you eat or play, and who tended to be around?"),
    Prompt("neighbourhood-shop", "Building life", "Building", "The familiar neighbourhood shop",
           "Was there a shop near your building that you visited often as a child? What did it look like and what did you buy there?"),
    Prompt("preprimary-arrival", "Early childhood", "Pre-primary", "Arriving at pre-primary school",
           "What is the first thing you can picture about arriving at pre-primary school—the entrance, classroom, an adult, or something else?"),
    Prompt("school-unusual-interruption", "School events", "School", "A school day interrupted",
           "Do you remember a school day when something unusual interrupted the normal routine? What happened around you?"),
    Prompt("old-device", "Games and shows", "Nostalgic Media", "An old device I used",
           "What phone, computer, television, or game device from childhood can you still picture clearly? What did you use it for?"),
    Prompt("building-rain", "Building life", "Building", "Rain around the building",
           "What was your building or neighbourhood like during heavy rain? Did it change where or how you played?"),
    Prompt("college-first-week", "College", "College", "The first week of college",
           "What small scene from your first week of college still survives—finding a room, meeting someone, travelling there, or anything else?"),
    Prompt("family-ordinary-evening", "Home and family", "Family", "An ordinary childhood evening",
           "Picture an ordinary evening at home when you were young. Where was everyone and what sounds or activities filled the house?"),
    Prompt("school-rule", "School events", "School", "A school rule I remember",
           "What school rule felt completely normal then but seems strange, funny, or unnecessary when you think about it now?"),
    Prompt("game-friends", "Games and shows", "Nostalgic Media", "A game shared with other people",
           "Was there a game you discussed or played with friends, cousins, or siblings? What did you all care about in it?"),
    Prompt("building-adult", "Building life", "Building", "An adult everyone in the building knew",
           "Was there an adult in your building or neighbourhood whom all the children knew? What made that person memorable?"),
    Prompt("school-route", "School events", "School", "The journey to school",
           "How did you usually travel to school, and what is one detail from that repeated journey that you have not thought about recently?"),
    Prompt("preprimary-game", "Early childhood", "Pre-primary", "A very early game",
           "What is the earliest game or made-up activity you remember playing? Where were you and what were the rules as you understood them?"),
    Prompt("college-canteen", "College", "College", "A college food or canteen memory",
           "Was there a food, drink, stall, or canteen routine strongly tied to college? What scene comes back with it?"),
    Prompt("festival-scene", "Home and family", "Family", "A festival scene from childhood",
           "Choose one festival from childhood and picture a single moment before, during, or after it. What was happening?"),
    Prompt("show-imitation", "Games and shows", "Nostalgic Media", "A show that entered real life",
           "Did a show, film, or game ever change what you and other children pretended to be or talked about? What did you do?"),
    Prompt("school-punishment", "School events", "School", "A punishment or narrow escape",
           "Do you remember being punished at school—or narrowly avoiding it? What led up to the moment?"),
    Prompt("building-secret-place", "Building life", "Building", "A place adults rarely noticed",
           "Was there a corner, terrace, staircase, parking area, or other place where children spent time away from adults? What happened there?"),
    Prompt("lost-object", "Objects", "Objects", "Something I lost or broke",
           "Is there a childhood object you lost, broke, or had taken away that felt important at the time? What do you remember about it?"),
    Prompt("wanted-object", "Objects", "Objects", "Something I badly wanted",
           "What toy, device, game, clothing, or other object did you badly want before you finally got it—or never got it?"),
    Prompt("friend-home", "People through scenes", "People", "Visiting another child's home",
           "Do you remember regularly visiting another child's home? What was different or memorable about their house?"),
    Prompt("teacher-voice", "People through scenes", "School", "A teacher's distinctive presence",
           "Without trying to list every teacher, whose voice, mannerism, or way of entering the classroom can you still remember clearly?"),
    Prompt("last-time-place", "Transitions", "Transitions", "The last time at an old place",
           "Is there a childhood place you stopped visiting without realizing it was the last time? What can you still reconstruct about it?"),
    Prompt("summer-routine", "Routines", "Routines", "A summer-holiday routine",
           "During a long school holiday, what did an ordinary afternoon look like for you?"),
    Prompt("sick-day", "Routines", "Routines", "A childhood sick day",
           "What was different about staying home sick as a child—where did you rest, what did you watch or eat, and who checked on you?"),
    Prompt("internet-arrival", "Games and shows", "Nostalgic Media", "When the internet felt new",
           "What is an early memory of the internet feeling new or limited—slow downloads, shared devices, data limits, cyber cafés, or something else?"),
)

_FOLLOWUP_SYSTEM = (
    "You are a careful memory interviewer. Given one question and the person's raw answer, ask "
    "ONE follow-up that could unlock a concrete scene or detail. Do not summarize, diagnose, "
    "praise, assume an event occurred, or say 'tell me more'. Prefer place, sequence, people, "
    "sensory detail, routine, first/last time, or what happened immediately before/after. The "
    "question must stand alone and be comfortable to skip. Return JSON only: "
    '{"question": str}.'
)


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    if out.get("note_path"):
        out["obsidian_uri"] = notes.obsidian_uri(out["note_path"])
    return out


def current(c: sqlite3.Connection) -> dict[str, Any] | None:
    return _row(c.execute(
        "SELECT * FROM memory_prompts WHERE status='open' ORDER BY id DESC LIMIT 1"
    ).fetchone())


def stats(c: sqlite3.Connection) -> dict[str, int]:
    counts = {r["status"]: r["n"] for r in c.execute(
        "SELECT status, COUNT(*) n FROM memory_prompts GROUP BY status")}
    return {
        "captured": counts.get("answered", 0),
        "skipped": counts.get("skipped", 0),
        "open": counts.get("open", 0),
    }


def _insert(c: sqlite3.Connection, prompt: Prompt, parent_id: int | None = None) -> dict[str, Any]:
    try:
        cur = c.execute(
            "INSERT INTO memory_prompts "
            "(prompt_key, category, folder, title, question, parent_id, status, created_at) "
            "VALUES (?,?,?,?,?,?, 'open', ?)",
            (prompt.key, prompt.category, prompt.folder, prompt.title, prompt.question,
             parent_id, store.now()))
    except sqlite3.IntegrityError:
        # A scheduler tick and a button press may arrive together. The partial unique index is
        # the final arbiter: both callers receive the same single waiting prompt.
        waiting = current(c)
        if waiting is not None:
            return waiting
        raise
    c.commit()
    return _row(c.execute("SELECT * FROM memory_prompts WHERE id=?", (cur.lastrowid,)).fetchone()) or {}


def _bank_prompt(c: sqlite3.Connection) -> Prompt:
    used = {r["prompt_key"] for r in c.execute("SELECT prompt_key FROM memory_prompts")}
    category_uses = {r["category"]: r["n"] for r in c.execute(
        "SELECT category, COUNT(*) n FROM memory_prompts GROUP BY category")}
    available = [p for p in _PROMPTS if p.key not in used]
    if not available:
        # The bank is intentionally large, but after it is exhausted, start a fresh rotation.
        # The cycle suffix keeps historical rows unambiguous while the visible question repeats.
        cycle = 1 + len(used) // len(_PROMPTS)
        source = min(_PROMPTS, key=lambda p: (category_uses.get(p.category, 0), p.key))
        return Prompt(f"{source.key}:cycle-{cycle}", source.category, source.folder,
                      source.title, source.question)
    return min(available, key=lambda p: (category_uses.get(p.category, 0), _PROMPTS.index(p)))


def ensure_prompt() -> dict[str, Any]:
    """Return the one waiting prompt, creating a well-rotated one when the queue is empty."""
    c = store.conn()
    try:
        waiting = current(c)
        return waiting or _insert(c, _bank_prompt(c))
    finally:
        c.close()


def another_subject() -> dict[str, Any]:
    """Skip the waiting cue and replace it without accumulating an inbox backlog."""
    c = store.conn()
    try:
        waiting = current(c)
        if waiting:
            c.execute("UPDATE memory_prompts SET status='skipped', answered_at=? WHERE id=?",
                      (store.now(), waiting["id"]))
            c.commit()
        return _insert(c, _bank_prompt(c))
    finally:
        c.close()


async def continue_thread(parent_id: int) -> dict[str, Any]:
    """Ask one locally generated follow-up; fall back to a safe concrete cue offline."""
    c = store.conn()
    try:
        waiting = current(c)
        if waiting:
            return waiting
        parent_row = c.execute(
            "SELECT * FROM memory_prompts WHERE id=? AND status='answered'", (parent_id,)
        ).fetchone()
        if parent_row is None:
            return _insert(c, _bank_prompt(c))
        parent = dict(parent_row)
    finally:
        c.close()

    answer = str(parent.get("answer") or "")
    data = await llm.extract(
        _FOLLOWUP_SYSTEM,
        f"Previous question: {parent['question']}\n\nRaw answer:\n{answer[:6000]}",
    )
    question = data.get("question", "").strip() if isinstance(data, dict) else ""
    if not _good_followup(question):
        question = ("When you picture that memory as one scene, what is the first concrete "
                    "detail around you that comes back?")
    key = f"followup:{parent_id}:{notes.sha(question)[:12]}"
    prompt = Prompt(key, parent["category"], parent["folder"],
                    f"{parent['title']} — another detail", question)
    c = store.conn()
    try:
        return current(c) or _insert(c, prompt, parent_id=parent_id)
    finally:
        c.close()


def _good_followup(question: str) -> bool:
    if not question or len(question) > 240 or not question.endswith("?"):
        return False
    lowered = question.lower()
    banned = ("tell me more", "how did that make you feel", "why do you think")
    return not any(part in lowered for part in banned)


def skip(prompt_id: int) -> dict[str, Any]:
    c = store.conn()
    try:
        row = c.execute("SELECT status FROM memory_prompts WHERE id=?", (prompt_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "No such memory question."}
        if row["status"] == "open":
            c.execute("UPDATE memory_prompts SET status='skipped', answered_at=? WHERE id=?",
                      (store.now(), prompt_id))
            c.commit()
        return {"ok": True, "outcome": "skipped"}
    finally:
        c.close()


def save_answer(prompt_id: int, answer: str) -> dict[str, Any]:
    """Persist one explicitly submitted answer as a new, protected raw-memory note."""
    if not answer.strip():
        return {"ok": False, "error": "Write anything you remember, or skip this question."}
    if answer.strip().lower().rstrip(".!?") in {
        "i don't remember", "i dont remember", "don't remember", "dont remember",
        "i can't remember", "i cant remember", "no idea",
    }:
        return skip(prompt_id)
    c = store.conn()
    try:
        row = c.execute("SELECT * FROM memory_prompts WHERE id=?", (prompt_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "No such memory question."}
        prompt = dict(row)
        if prompt["status"] == "answered" and prompt.get("note_path"):
            return {"ok": True, "outcome": "already_saved", "prompt_id": prompt_id,
                    "note_path": prompt["note_path"],
                    "obsidian_uri": notes.obsidian_uri(prompt["note_path"])}
        if prompt["status"] != "open":
            return {"ok": False, "error": "That memory question is no longer open."}

        vault = notes.vault()
        if vault is None:
            return {"ok": False, "error": "The Obsidian vault is unavailable."}
        rel, target = _target(vault, prompt["folder"], prompt["title"])
        body = _markdown(prompt, answer)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("x", encoding="utf-8") as f:
                f.write(body)
        except FileExistsError:
            # A manual note appeared after target selection. Re-resolve; never overwrite it.
            rel, target = _target(vault, prompt["folder"], prompt["title"])
            with target.open("x", encoding="utf-8") as f:
                f.write(body)

        c.execute(
            "UPDATE memory_prompts SET answer=?, note_path=?, status='answered', answered_at=? "
            "WHERE id=?", (answer, rel, store.now(), prompt_id))
        c.commit()
        return {"ok": True, "outcome": "saved", "prompt_id": prompt_id,
                "note_path": rel, "obsidian_uri": notes.obsidian_uri(rel)}
    finally:
        c.close()


def _safe_name(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]', "", value).strip().rstrip(".")
    if value in ("", ".", ".."):
        return "A memory"
    return value[:120]


def _target(vault: Path, folder: str, title: str) -> tuple[str, Path]:
    base = vault / "Memory" / _safe_name(folder)
    stem = _safe_name(title)
    target = base / f"{stem}.md"
    n = 2
    while target.exists():
        target = base / f"{stem} ({n}).md"
        n += 1
    return str(target.relative_to(vault)), target


def _markdown(prompt: dict[str, Any], answer: str) -> str:
    captured = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    # JSON strings are valid YAML scalars and safely quote punctuation in generated metadata.
    frontmatter = (
        "---\n"
        "type: raw-memory\n"
        "preserve_voice: true\n"
        f"captured: {json.dumps(captured, ensure_ascii=False)}\n"
        f"category: {json.dumps(prompt['category'], ensure_ascii=False)}\n"
        f"curator_question_id: {prompt['id']}\n"
    )
    if prompt.get("parent_id"):
        frontmatter += f"parent_question_id: {prompt['parent_id']}\n"
    return (frontmatter + "---\n\n" + f"# {prompt['title']}\n\n"
            f"## Curator asked\n\n{prompt['question']}\n\n"
            f"## My memory\n\n{answer}\n")

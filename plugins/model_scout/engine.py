"""Research and benchmark models without silently downloading or selecting them."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

import httpx

from orion.core import cognition, specialists
from orion.core.config import config
from plugins.chronos import inference as chronos_inference

from . import store
from .evals import CASES, QUESTIONS


def _cfg() -> dict[str, Any]:
    return config.section("model_scout")


async def _installed() -> set[str]:
    base = config.provider_cfg("ollama").get("base_url", "http://127.0.0.1:11434")
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{base}/api/tags")
            response.raise_for_status()
        return {item.get("name", "") for item in response.json().get("models", [])}
    except Exception:
        return set()


async def research(*, persist: bool = True) -> dict[str, Any]:
    """Refresh official Hugging Face metadata for the small, pinned candidate set."""
    installed = await _installed()
    found = []
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for candidate in _cfg().get("candidates", []):
            row = {**candidate, "installed": candidate.get("model") in installed,
                   "reachable": False, "last_modified": None, "revision": None}
            repo = candidate.get("repo")
            if repo:
                try:
                    response = await client.get(f"https://huggingface.co/api/models/{repo}")
                    response.raise_for_status()
                    data = response.json()
                    row.update(reachable=True, last_modified=data.get("lastModified"),
                               revision=data.get("sha"))
                except Exception as exc:
                    row["error"] = str(exc)[:200]
            found.append(row)
    result = {"ok": any(item["reachable"] for item in found), "candidates": found,
              "installed": sorted(installed)}
    if persist:
        c = store.conn()
        try:
            store.set_metadata(c, "latest_research", result)
        finally:
            c.close()
        _recommend(str(config.provider_cfg("ollama").get("model", "unknown")), [], found)
    return result


def _heuristic_results(cases: list[tuple[str, str, str]]) -> dict[str, Any]:
    correct = 0
    details = []
    for task, text, expected in cases:
        if task == "mode":
            predicted = cognition.classify(text).value
        elif task == "specialist":
            predicted = specialists.select(text).name
        else:
            predicted = "event" if chronos_inference.likely_relevant(
                {"subject": "", "text": text}) else "not_event"
        correct += predicted == expected
        details.append({"task": task, "expected": expected, "predicted": predicted,
                        "ok": predicted == expected})
    return {"name": "current heuristics", "kind": "heuristic", "available": True,
            "accuracy": correct / len(cases), "mean_seconds": 0.0, "details": details}


def _answer_choice(answer: Any) -> tuple[str, float | None]:
    if not isinstance(answer, dict): return "", None
    confidence = answer.get("answer_confidence", answer.get("confidence"))
    try: confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError): confidence = None
    return str(answer.get("choice") or ""), confidence


async def _laya(cases: list[tuple[str, str, str]]) -> dict[str, Any]:
    cfg = _cfg().get("laya", {})
    base = str(cfg.get("base_url", "http://127.0.0.1:8765")).rstrip("/")
    started = time.monotonic(); details = []
    try:
        async with httpx.AsyncClient(timeout=float(cfg.get("timeout_seconds", 120))) as client:
            for task, text, expected in cases:
                response = await client.post(f"{base}/v1/systemone",
                    json={"state": text, "questions": {task: QUESTIONS[task]}})
                response.raise_for_status()
                answer = response.json().get("answers", {}).get(task, {})
                predicted, confidence = _answer_choice(answer)
                details.append({"task": task, "expected": expected, "predicted": predicted,
                                "confidence": confidence, "ok": predicted == expected})
    except Exception as exc:
        return {"name": "Laya", "kind": "laya", "available": False,
                "reason": str(exc)[:300], "accuracy": None, "details": details}
    elapsed = time.monotonic() - started
    return {"name": "Laya", "kind": "laya", "available": True,
            "accuracy": sum(d["ok"] for d in details) / len(details),
            "mean_seconds": elapsed / len(details), "details": details}


def _parse_choice(raw: str, labels: list[str]) -> str:
    cleaned = raw.strip().lower().replace("`", "").replace('"', "")
    for label in labels:
        if cleaned == label or cleaned.startswith(label + "\n"):
            return label
    return next((label for label in labels if label in cleaned[:80]), "")


async def _ollama(model: str, cases: list[tuple[str, str, str]]) -> dict[str, Any]:
    base = config.provider_cfg("ollama").get("base_url", "http://127.0.0.1:11434")
    details = []; started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=4)) as client:
            for index, (task, text, expected) in enumerate(cases):
                criteria = QUESTIONS[task]["criteria"]
                labels = list(criteria)
                prompt = (f"Classify the text into exactly one label.\nLabels: "
                          f"{json.dumps(criteria)}\nText: {text}\nReturn only the label.")
                response = await client.post(f"{base}/api/chat", json={
                    # Keep the weights resident between cases; unload after the final one.
                    "model": model, "stream": False,
                    "keep_alive": 0 if index == len(cases) - 1 else "5m",
                    "messages": [{"role": "user", "content": prompt}],
                    "options": {"temperature": 0, "num_predict": 12, "num_ctx": 2048}})
                response.raise_for_status()
                raw = response.json().get("message", {}).get("content", "")
                predicted = _parse_choice(raw, labels)
                details.append({"task": task, "expected": expected, "predicted": predicted,
                                "ok": predicted == expected})
    except Exception as exc:
        return {"name": model, "kind": "ollama", "available": False,
                "reason": str(exc)[:300], "accuracy": None, "details": details}
    elapsed = time.monotonic() - started
    return {"name": model, "kind": "ollama", "available": True,
            "accuracy": sum(d["ok"] for d in details) / len(details),
            "mean_seconds": elapsed / len(details), "details": details}


async def benchmark() -> dict[str, Any]:
    limit = max(6, min(len(CASES), int(_cfg().get("benchmark_limit", len(CASES)))))
    cases = CASES[:limit]
    current = str(config.provider_cfg("ollama").get("model", "qwen2.5:3b"))
    installed = await _installed()
    results = [_heuristic_results(cases), await _laya(cases)]
    models = [current, *[c["model"] for c in _cfg().get("candidates", [])
                        if c.get("kind") == "ollama" and c.get("model") in installed]]
    for model in dict.fromkeys(models):
        if model in installed:
            results.append(await _ollama(model, cases))
    research_result = await research(persist=False)
    c = store.conn()
    try:
        store.set_metadata(c, "latest_research", research_result)
    finally:
        c.close()
    summary = _recommend(current, results, research_result.get("candidates", []))
    c = store.conn()
    try:
        run_id = store.save_run(c, current, results, research_result.get("candidates", []), summary)
    finally:
        c.close()
    return {"ok": True, "run_id": run_id, "current_model": current,
            "results": results, "summary": summary}


def _recommend(current: str, results: list[dict], candidates: list[dict]) -> str:
    gates = _cfg().get("gates", {})
    minimum = float(gates.get("minimum_accuracy", .75))
    delta = float(gates.get("minimum_improvement", .05))
    maximum = float(gates.get("maximum_mean_seconds", 8))
    available = [r for r in results if r.get("available") and r.get("accuracy") is not None]
    current_row = next((r for r in available if r["name"] == current), None)
    c = store.conn()
    try:
        if current_row:
            winners = [r for r in available if r.get("kind") == "ollama" and r["name"] != current
                       and r["accuracy"] >= minimum
                       and r["accuracy"] >= current_row["accuracy"] + delta
                       and r.get("mean_seconds", 999) <= maximum]
            for winner in sorted(winners, key=lambda row: row["accuracy"], reverse=True)[:1]:
                store.propose(c, fingerprint=f"switch:{current}:{winner['name']}:{winner['accuracy']:.3f}",
                    kind="switch", candidate=winner["name"], title=f"Upgrade local model to {winner['name']}?",
                    body=(f"It scored {winner['accuracy']:.0%} versus {current_row['accuracy']:.0%} "
                          f"for {current} on Orion's benchmark, averaging "
                          f"{winner['mean_seconds']:.2f}s per decision."),
                    effect="Accept writes a backed-up models.local.json override. Reject keeps the current model.",
                    evidence={"current": current_row, "candidate": winner})
        laya = next((r for r in available if r.get("kind") == "laya"), None)
        heuristic = next((r for r in available if r.get("kind") == "heuristic"), None)
        if laya and heuristic and laya["accuracy"] >= heuristic["accuracy"] + delta:
            store.propose(c, fingerprint=f"laya:{laya['accuracy']:.3f}", kind="trial",
                candidate="convaiinnovations/laya", title="Pilot Laya for System-1 routing?",
                body=f"Laya scored {laya['accuracy']:.0%} versus {heuristic['accuracy']:.0%} for current rules.",
                effect="Accept records approval for an integration trial; it does not replace a model automatically.",
                evidence={"current": heuristic, "candidate": laya})
        # Keep one actionable trial card for the leading researched generation candidate.
        qwen = next((row for row in candidates if row.get("model") == "qwen3.5:4b"), None)
        if qwen and qwen.get("reachable") and not qwen.get("installed"):
            store.propose(c, fingerprint=f"trial:{qwen['model']}:{qwen.get('revision') or 'unversioned'}", kind="trial",
                candidate=qwen["model"], title="Benchmark Qwen 3.5 4B locally?",
                body=(f"The official candidate is available but not installed ({qwen['size_gb']} GB). "
                      f"Install with: ollama pull {qwen['model']}. It will not be selected until it passes Orion's gates."),
                effect="Accept marks the trial as approved but downloads nothing and changes no routing.", evidence=qwen)
    finally:
        c.close()
    best = max(available, key=lambda row: row["accuracy"], default=None)
    return (f"Best measured: {best['name']} at {best['accuracy']:.0%}."
            if best else "No model endpoint was available; research metadata was still refreshed.")


async def resolve(rid: int, action: str) -> dict[str, Any]:
    c = store.conn()
    try:
        item = store.recommendation(c, rid)
        if not item or item["status"] != "pending":
            return {"ok": False, "reason": "unknown or already-resolved recommendation"}
        if action not in {"accept", "approve"}:
            store.resolve(c, rid, "rejected")
            return {"ok": True, "outcome": "rejected"}
        if item["kind"] == "switch":
            if item["candidate"] not in await _installed():
                return {"ok": False, "reason": "candidate is no longer installed; benchmark it again"}
            models_local = config.root() / "config" / "models.local.json"
            if models_local.exists():
                backup = models_local.with_suffix(f".json.model-scout-{int(time.time())}.bak")
                shutil.copy2(models_local, backup)
            config.update_local("models", {"providers": {"ollama": {"model": item["candidate"]}}})
        store.resolve(c, rid, "accepted")
        return {"ok": True, "outcome": "switched" if item["kind"] == "switch" else "trial_approved",
                "candidate": item["candidate"]}
    finally:
        c.close()

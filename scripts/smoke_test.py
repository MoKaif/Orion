"""On-host smoke test - verify the LIVE paths that couldn't be exercised during offline dev.

Run this FIRST on the Arch laptop, before building M4:

    python scripts/smoke_test.py

It checks each live dependency independently and prints PASS / WARN / FAIL, then runs a real
end-to-end chat turn. WARN means "degraded but not broken" (e.g. no cloud key). Nothing here
mutates anything important; it uses a throwaway session.
"""
from __future__ import annotations

import asyncio
import os
import sys

# make the project root importable regardless of where the script is launched from
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OK, WARN, FAIL = "PASS", "WARN", "FAIL"
results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))


async def main() -> None:
    # 1. config + world model schema
    try:
        from orion.core.config import config
        from orion.core.world_model import world_model
        record("config + world-model schema", OK,
               f"db={world_model._path.name}, user={config.section('settings').get('identity',{}).get('user')}")
    except Exception as e:
        record("config + world-model schema", FAIL, repr(e)); sys.exit(1)

    # 2. vector index (fastembed + sqlite-vec)
    from orion.core.world_model.vectors import vectors
    if vectors.is_available():
        try:
            vectors.add("smoke:1", "the quick brown fox", lane="fastembed")
            hits = vectors.search("fast animal", k=1, lane="fastembed")
            record("semantic vectors (fastembed+sqlite-vec)", OK if hits else WARN,
                   f"{len(hits)} hit(s)")
        except Exception as e:
            record("semantic vectors", FAIL, repr(e))
    else:
        record("semantic vectors", WARN, "unavailable - recall falls back to keyword "
               "(pip install fastembed sqlite-vec)")

    # 3. Ollama local model
    from orion.core.providers import router
    from orion.core.providers.base import Message
    ollama = router.get("ollama")
    if ollama and await ollama.is_available():
        try:
            txt = await ollama.complete([Message("user", "Reply with the single word: ready")])
            record("Ollama local model", OK, f"responded: {txt.strip()[:40]!r}")
        except Exception as e:
            record("Ollama local model", FAIL, repr(e))
    else:
        record("Ollama local model", WARN, "not reachable - start `ollama serve` and pull the model")

    # 4. cloud fallback (optional)
    gemini = router.get("gemini")
    record("Gemini cloud fallback", OK if (gemini and await gemini.is_available()) else WARN,
           "configured" if (gemini and await gemini.is_available())
           else "disabled (no key) - local-only until a key is added")

    # 5. plugins + scheduler
    from orion.core import plugins, maintenance
    from orion.core.tools.builtin import register_builtins
    from orion.core.tools import selection, registry
    from orion.core import specialists
    register_builtins(); loaded = plugins.load_all(); maintenance.register_core_jobs()
    indexed = selection.index_tools()
    record("plugins + tools + specialists", OK,
           f"plugins={loaded}, tools={len(registry.all_tools())}, "
           f"specialists={len(specialists.all_specialists())}, tools_indexed={indexed}")

    # 6. croniter scheduling
    try:
        import croniter  # noqa: F401
        record("croniter scheduling", OK, "time-scheduling active")
    except Exception:
        record("croniter scheduling", WARN, "not installed - jobs run manually only")

    # 7. end-to-end chat turn through the full pipeline
    try:
        from orion.core import orchestrator
        sid = world_model.create_session("smoke-test")
        events: list[dict] = []
        toks: list[str] = []
        async for t in orchestrator.handle_turn("In one sentence, what are you?", sid, events.append):
            toks.append(t)
        ctx = next((e for e in events if e["type"] == "context"), {})
        reply = "".join(toks).strip()
        status = OK if reply and not reply.startswith("[no provider") else WARN
        record("end-to-end chat turn", status,
               f"mode={ctx.get('mode')} specialist={ctx.get('specialist')} reply={reply[:60]!r}")
    except Exception as e:
        record("end-to-end chat turn", FAIL, repr(e))

    # summary
    print("\n" + "=" * 60)
    fails = [r for r in results if r[1] == FAIL]
    warns = [r for r in results if r[1] == WARN]
    print(f"{len(results)} checks - {len(results)-len(fails)-len(warns)} pass, "
          f"{len(warns)} warn, {len(fails)} fail")
    if fails:
        print("FAIL:", ", ".join(r[0] for r in fails)); sys.exit(1)
    print("Live paths OK (warns are acceptable degradations). Ready to build M4.")


if __name__ == "__main__":
    asyncio.run(main())

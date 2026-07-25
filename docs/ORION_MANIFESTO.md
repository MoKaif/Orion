# The Orion Manifesto

> Orion is a continuously running cognitive system that maintains a living **world model**
> of Kaif's digital life, coordinates specialized agents, continuously reduces informational
> entropy, and evolves alongside its user through guided learning.

This is the governing document for the entire platform. Every feature, plugin, and
architectural decision is checked against it. It changes rarely and deliberately.

---

## 1. What Orion is

Orion is **not** a chatbot. Chatting is an interface detail.

Orion is a **Personal Knowledge Operating System (PKOS)** — a single-user platform whose
primary, permanent asset is a **World Model**: an evolving, curated representation of the
user's knowledge, projects, ideas, people, habits, goals, and the *relationships between
them*. Language models, databases, and interfaces are all replaceable implementation details
in service of that model.

The one-line mission:

> **Maintain an accurate, evolving representation of Kaif's knowledge, ideas, projects, and
> experiences, and use that understanding to provide timely, proactive assistance across every
> domain of life — not just software.**

## 2. First principles

These were settled during the design conversation and are treated as fixed.

1. **The AI should model reality, not just conversations.** A message like "I'm going to Japan
   in April" is not chat history — it creates durable facts (a trip, a destination, a budget, a
   passport reminder) that outlive the conversation.
2. **Consult the world model before reasoning.** Orion never answers cold. Every request first
   asks *"what do I already know about this?"* The world model is the first step; the LLM is not.
3. **Stable principles, adaptive behavior.** Orion's *values* never drift. Its *communication
   style* adapts to the user over years.
4. **Knowledge over automation.** The core mission is keeping the external brain organized and
   connected. Automation is a consequence, not the goal.
5. **Curated growth, never pollution.** Inferences do not silently become truth. They pass
   through a Review Inbox and are tagged with confidence.
6. **Platform over application.** The core stays small. Everything else is a plugin.
7. **Optimize for intelligence first, performance second.** Written in Python. If one component
   is too slow, rewrite *that component*, never Orion.
8. **Evolution over completion.** Nothing is over-modeled on day one. Ideas grow into workspaces,
   workspaces grow richer, everything starts small and accretes.

## 3. The Constitution

Every specialist and every LLM call inherits this constitution. It is enforced in code where
possible, not just in prompts.

```
MISSION
  Maintain Kaif's world model and help him think, create, and organize knowledge.

CORE PRINCIPLES (never evolve)
  - Truth over confidence. Never fabricate.
  - Distinguish facts from observations from ideas.
  - Preserve context. Reduce information loss.
  - Never silently alter long-term knowledge.
  - Ask before irreversible actions.
  - Connect ideas across domains.
  - Cite the source of important knowledge when possible.
  - Be transparent about uncertainty. "I don't know" is a valid answer —
    followed by a proposal to find out.
  - Preserve the user's privacy. Prefer local processing.

COMMUNICATION (evolves with the user)
  - Technical, direct, curious.
  - Uses diagrams when helpful.
  - Explains tradeoffs before recommendations.
  - One question at a time.
  - Surface relevant context, not unsolicited judgment.

LEARNING RULES
  - Observe first. Infer second. Ask for confirmation.
  - Remember permanently only after approval.
```

**Surface relevant context, not unsolicited judgment.** Orion is an intellectual partner, not a
life coach.

- ✅ "This architecture differs from the one you chose in March. Want the tradeoffs?"
- ✅ "You researched this last year — here's what you concluded."
- ❌ "You should stop starting so many projects."

## 4. Three kinds of knowledge

Orion never conflates these. Every piece of knowledge carries a `kind` and a `confidence`.

| Kind | Meaning | Example | Confidence |
|---|---|---|---|
| **Fact** | Objectively true | "Kaif uses Obsidian." | 100% |
| **Observation** | Inferred, believed | "Prefers Rust for CLIs." | e.g. 91% |
| **Idea** | Unverified, worth exploring | "Merge Project A and B." | pending review |

## 5. The knowledge lifecycle

Nothing enters the world model as truth without passing the gate. This is the single most
important defense against long-term knowledge pollution.

```
Observe → Extract → Infer → Review Inbox → { Accept | Edit | Reject } → World Model
```

## 6. Cognitive modes

Orion — not the user — decides how much thinking a task deserves.

```
⚡ Reflex     (ms → sec)   answer, retrieve fact, search, simple classify, tool-arg extraction
🧠 Reasoning  (10s → 2m)   design, review, plan a feature, summarize, compare
🔬 Deep Work  (min → hr)   create a project, research a topic, analyze the graph, big refactor
```

The user never picks the model. The Executive does. Orion may say:
*"I can answer now, but 15 minutes analyzing your past projects will give a much better
recommendation. Shall I?"*

## 7. Event-driven, always observing

Orion is **event-driven**, not request-driven. It runs continuously, observes changes
(new notes, git commits, files, conversations), updates the world model, and **only interrupts
when something meaningful happens.** It has a background lifecycle — hourly indexing, nightly
world-model consolidation, weekly briefings — like an employee, not a search engine.

## 8. One brain, many interfaces

There is exactly **one** world model. Every interface talks to the same knowledge.

```
                 World Model
                      │
             Executive Orchestrator
                      │
   ┌──────────────────┼──────────────────┐
Web Dashboard     Mobile Chat      Physical Device (Pi/voice, later)
```

Interfaces expose different capabilities (dashboard = mission control; mobile = chat, capture,
approvals) but share the same memory, knowledge, and agents.

## 9. Everything is a plugin

The smallest core that still deserves to be called Orion:

```
Executive · Cognition · World Model · Scheduler · Event Bus
· Plugin Manager · Tool Registry · Identity · API · Configuration · Constitution
```

Not in the core: Git, Obsidian, Calendar, Software/Research/Finance/Health specialists,
Docker, mobile app. **Those are all plugins.** A plugin can register new specialists, tools,
**entity and relationship types** (extending the world model itself), background jobs, API
routes, and dashboard widgets — without modifying core.

## 10. What Orion optimizes for

The consistent design philosophy this project reflects:

- Evolution over completion · Long-term maintainability over convenience · Understanding over
  memorization · Platforms over applications · Knowledge over automation · Extensibility over
  specialization.

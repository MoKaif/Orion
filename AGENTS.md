# AGENTS.md — Orion

Instructions for Codex and other coding agents working in this repository.

## Read first

Use `docs/ORION_MANIFESTO.md`, `docs/ARCHITECTURE.md`, and `docs/ROADMAP.md` as the product and
architecture source of truth. Read `docs/VERSIONING.md` before preparing a release. Do not edit
`orion.__version__` or `CHANGELOG.md` by hand; `scripts/release.py` owns those changes.

## Invariants

- Never commit secrets. Runtime credentials belong in environment variables or the gitignored
  `config/secrets.json`.
- Degrade gracefully when optional models, providers, embeddings, or schedulers are unavailable.
- Never silently mutate the World Model; inferred knowledge goes through the review inbox.
- Put new capabilities in plugins unless they change a genuine platform contract.
- Keep irreversible actions behind the existing confirmation gate.
- Preserve user work in dirty trees and keep changes tightly scoped.

## Verification

- Python/runtime changes: run focused checks and `python scripts/smoke_test.py` when live local
  dependencies are available.
- SPA changes: run `pnpm build` in `interfaces/spa`.
- The served SPA bundle is generated and gitignored; source lives under `interfaces/spa/src`.

## Maintainer

Read `docs/maintainer-v2/DESIGN.md` and `ROADMAP.md` before changing Maintainer. The native
service provides persistent project configuration, read-only Git observations, remote tip
verification, version metadata and fixed toolchain inventory. Its SPA workspace is under
Agents at `/agents/maintainer`; `/maintainer` redirects there, preserving section/project links.
Native deployment is not an OS-enforced read-only Docker mount.

Legacy candidate generation is paused by configuration; legacy execution/approval endpoints
fail closed. Preserve tasks, approvals, runs and events without treating them as v2 grants.
Do not re-enable execution until revision-bound scopes, owner authentication, isolated
verification, durable jobs and recovery exist. Never mutate normal checkouts. Failed,
unavailable or missing required verification cannot become success or publication. Scrubbing
environment variables does not isolate build scripts from host credentials.

Release versions are milestones, not per-commit counters. Follow `docs/VERSIONING.md`; the
release script prepares metadata by default and `--unreleased` records work without a bump.

## Curator

Curator proposals are review-gated. Applying grammar or backlink edits requires a current note
hash and creates a timestamped backup first. Grammar changes must preserve YAML frontmatter,
wikilinks, Markdown links, tags, URLs, code, headings, list structure, and line layout exactly.
Generated entity hub notes are structured metadata and are not grammar-editable.

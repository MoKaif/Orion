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

Maintainer audits every enabled repository nightly in Orion's read-only workspace view, even
when its base branch has not changed. It rotates focus and samples bounded source excerpts in
addition to project notes, commits, and TODOs. After user approval, the
host-side `scripts/maintainer_runner.py` invokes `codex exec` in a dedicated worktree, verifies
the result, pushes only a `maintainer/*` branch, and opens a pull request. It never merges and
must never write in a user's normal checkout. Codex uses the host's saved CLI authentication;
provider API keys are deliberately removed from its child environment.
Dependency installation must complete successfully before Codex starts. Verification is chosen
from the paths actually changed; unavailable host toolchains are reported as blocked rather than
as code failures.

## Curator

Curator proposals are review-gated. Applying grammar or backlink edits requires a current note
hash and creates a timestamped backup first. Grammar changes must preserve YAML frontmatter,
wikilinks, Markdown links, tags, URLs, code, headings, list structure, and line layout exactly.
Generated entity hub notes are structured metadata and are not grammar-editable.

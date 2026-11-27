# Maintainer 2.0 delivery roadmap

The detailed architecture and constraints are in [DESIGN.md](DESIGN.md). Checkboxes describe
implemented scope, not aspiration. Every phase must leave Orion deployable; no phase grants
approval to mutate another project or publish a release.

## Phase 0 — Integrity and version policy (critical, medium complexity)

- [x] Refuse publication when verification fails, is blocked or is skipped for changed code.
- [x] Enforce results independently in the API/store, including missing verification evidence.
- [x] Preserve original historical success reports while reassessing failed/blocked checks.
- [x] Close legacy claims and approvals; preserve task/run/event history.
- [x] Disable the idle native legacy runner on this host; preserve its unit for future migration.
- [x] Scrub provider/runner keys from install and verification environments; tighten path checks.
- [x] Make release preparation the default; separate commit/tag/push flags and build identity.
- [x] Record unreleased work without forcing a version bump.

Reuse: existing runner/store, release tool, health endpoint. Database: additive reported status.
Tests: publication guards, result rejection, legacy migration/claim, metadata-only release
preparation, explicit flags and preservation of unrelated work. Rollback must keep execution
disabled; the former runner's success semantics are unsafe. No remote writes are tested on
personal repositories.

## Phase 1 — Registry and environment (critical, medium complexity)

- [x] Persist UUID identity, canonical checkout path, owner settings/revision and lifecycle status.
- [x] Import the six configured projects once; bounded workspace discovery for other projects.
- [x] Repair paths and edit status/description/release policy without losing identity.
- [x] Observe manifests, initial stack/profile evidence, hashes and working-tree revision.
- [x] Inventory fixed host tools/SDKs with bounded commands and a daily cache.
- [x] Expose project directory/detail, missing/stale facts and explicit configuration editing.
- [ ] Separate multiple checkout records from logical project identity.
- [ ] Add typed development constraints, environment variable names and required services.
- [ ] Add reviewed manifest reimport/export, explicit onboarding form and per-capability preferences.
- [ ] Add evidence-linked architecture/module profiles and changed-file invalidation.

Prerequisite: phase 0. Reuse config loader, plugin API, SQLite, SPA tokens. New modules:
`registry.py`, `intelligence.py`; new project/observation/activity tables and project routes.
Security: workspace confinement, no symlink escapes, no arbitrary commands or secret values.
Verification: temporary workspaces, duplicate import, edit conflicts, missing paths, symlinks,
malformed manifests and unknown toolchains. Acceptance: identity/settings survive refresh and
restart; inference never overwrites owner facts. Risk: mistaking one clone for an entire project;
keep multi-checkout support explicit. Migrate additively, snapshot before first live import.

## Phase 2 — Git and release observations (critical, medium complexity)

- [x] Capture staged/unstaged/untracked/conflicts, tracking/ahead/behind, detached/operation states.
- [x] Distinguish cached refs from fresh remote tip verification without fetching into checkouts.
- [x] Preserve remote success on failures while marking unavailable/stale; show changed remote tips.
- [x] Observe component metadata, configured tag patterns, local tags and changes since tags.
- [x] Add synchronization/attention/releases views and manual/local/hourly remote inspections.
- [ ] Use owned bare caches for exact ancestry when remote objects are missing locally.
- [ ] Observe all configured remotes/branches and provider releases, not only origin tips.
- [ ] Check committed version/tag/changelog consistency at a selected release ref.
- [ ] Aggregate current verification prerequisites into honest release readiness.

Prerequisite: registry. No LLM dependency. New remote-observation persistence and fixed collectors;
reuse Git utilities only after preserving errors and provenance. Tests: clean/dirty/diverged/no
tracking, renamed paths, unavailable remotes, stale evidence, deleted/changed remote tips,
detached HEAD, operations in progress, missing metadata and multi-component versions.
Acceptance: never call a project synchronized from stale tracking refs; never imply readiness
from a version file alone. Cache fetches require owned paths and bounded transport access.

## Phase 3 — Upstream intelligence (critical, high complexity)

1. Add explicit upstream relationship and unknown-baseline onboarding; preserve discovery evidence.
2. Add provider-neutral release/tag/commit observations, ETags, idempotency and backoff.
3. Build owned-cache ancestry/merge-base/patch-equivalence and changed-path comparisons.
4. Add structured overlap/regression evidence and bounded semantic compatibility assessment.
5. Display integrate/defer/skip recommendations with source, baseline, strategy and validation.

Reuse registry/evidence/router/remote adapter; add upstream settings, observations and assessment
records, upstream project tab and refresh API. Run daily/on demand, not on every UI poll.
Prerequisites: known or explicitly unknown upstream baseline and cache isolation. For NoxBerry,
verify Strawberry baseline/fork customizations and C++/Qt/CMake constraints; for Perseus, use the
verified FreeReps relationship. Tests use controlled upstream/fork repos, squashed history,
patch-equivalent commits, conflicting modifications, missing objects and inaccessible providers.
Acceptance: explain relevance and integration risk rather than announcing releases. Risk:
unrelated histories or upstream version strings are not evidence of integrated commits.

## Phase 4 — Engineering intelligence and proposal quality (high, high complexity)

1. Expand incremental profiles with modules/API/models/migrations/tests/deployment/constraints.
2. Add manifest/lock/advisory adapters with practical severity/compatibility classifications.
3. Persist internal candidates, evidence references, quality outcomes and suppression reasons.
4. Add problem fingerprints, rejected/deferred history, stale evidence and duplicate/WIP checks.
5. Publish only qualified proposal revisions and transparent attention findings.
6. Feed stable finding transitions through existing Herald contribution contracts.

Reuse model router/cost accounting and bounded digest excerpts; replace title-only scan output.
Database/API: evidence, candidates/findings/proposals/revisions and queryable rejection history.
Frontend: project knowledge/dependencies plus proposal review; no artificial health score.
Schedule cheap invalidation on change, advisories daily, opportunities weekly with token/time
budgets. Validate model schemas, evidence paths/symbols and repository baselines in code.
Tests: invalid/provider-failed output versus valid empty analysis, duplicate/rejected candidates,
outdated but harmless dependencies, incompatible upgrades, incomplete evidence and injection.
Acceptance: a user can understand one proposal without independent repository investigation;
zero proposals is successful. Risk: evidence extraction completeness, model cost and source trust.

## Phase 5 — Previews and revision-aware review (high, medium/high complexity)

1. Define immutable proposal revisions and a complete implementation/verification schema.
2. Generate technical examples or project-matching conceptual UI mockups before review.
3. Store confined, script-free artifacts with content hashes and provenance.
4. Add approve/reject/defer/request-revision APIs bound to revision/baseline/scope.
5. Persist immutable scoped approval records, expiry/revocation and preview invalidation.

Reuse SPA/Inbox; approvals require verified owner auth before they can authorize execution.
Database: artifacts/approvals; API idempotency keys and stale-precondition errors. Frontend:
problem, evidence, expected result, risk, plan, validation and exact operation permissions.
Tests: traversal/active content, stale previews/approvals, changed scope, replay/revocation,
unauthenticated mutations and clear conceptual labelling. Acceptance: approving implementation
cannot implicitly approve push, PR, tag, release or deployment. Migrate legacy tasks as legacy
records requiring revalidation, never as new permissions.

## Phase 6 — Controlled development and release preparation (high, high complexity)

1. Verify build/test isolation from home credentials, sockets and unauthorized network/files.
2. Introduce durable execution jobs, transactional claims, per-project/global locks and fencing.
3. Create per-execution owned branches/worktrees using cache objects; revalidate all grants.
4. Run approved implementation, inspect scope, verify commands and perform independent review.
5. Require explicit remote scopes and record/reconcile push, PR, tag and release operations.
6. Add cancellation/process-group termination, all-stage heartbeat, restart recovery and safe cleanup.
7. Prepare version/changelog/release PRs; publication/deployment remain separate scopes.
8. Reconcile profile/health only after verified integration, distinguishing PR-ready from merged.

Reuse runner feed, code execution adapter, legacy verification selection and Git branch guards.
Replace reusable destructive worktrees and nontransactional claim/start. Database: executions,
leases/jobs/verification/external operations/ownership. Runner v2 protocol is incompatible with
old claims and requires a capability handshake; keep old endpoint fail-closed. Frontend:
stage, branch/worktree, diff, review/check results, pending scopes and recovery controls.
Tests: concurrent claims, expired lease callbacks, interrupted processes/app restarts, orphaned
worktrees, failed/blocked checks, failed PR after successful push, stale approvals, unauthorized
operations, partial release/tag conflicts and idempotent reconciliation. No tests mutate real
user repos. Acceptance: no failed check becomes success, no work escapes approved scope, no
normal checkout changes, and retries cannot duplicate external operations.

## Verification and delivery rules

Run focused fixtures on each change, then Orion's complete `tests/` suite. Build the SPA with
`pnpm build`; verify the served page/API after restarting the native service. Run live
`python scripts/smoke_test.py` for runtime changes. Keep migration snapshots and validate
SQLite integrity. Test frontend interaction with component/E2E infrastructure when introduced;
currently TypeScript/Vite and live page/API checks exist, not a frontend component test suite.
Provider tests use mocks unless explicitly running an integration check. Record actual commands
and outcomes; never infer a successful build from installed tooling or a model response.

## Host validation record — 2026-11-27

The initial import registered and observed 21 workspace projects. Guardian snapshot
`20261127T133428Z` preceded the migration. SQLite integrity passed; seven legacy runs were
reassessed while preserving their original reported status. The idle legacy runner unit was
disabled. Orion was rebuilt/restarted; `/health` exposes the loaded commit and clean-start
state, the project API returns 21 records with execution disabled, and Caddy serves
`http://orion.home.arpa/maintainer`. A headless Firefox capture verified the populated page
against the live API. Verification: 97 Python tests, TypeScript/Vite production build, and
live smoke checks (six pass, one optional unconfigured Gemini warning, zero failures).

To inspect: open the workspace, select a project for Git/version/environment evidence, then
use Attention, Releases, Proposals or Executions. Inspect projects reads local state; Verify
remotes checks branch tips; Discover projects onboards new Git roots only beneath the configured
workspace. Development changes are recorded as unreleased; no release tag was created.

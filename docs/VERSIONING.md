# Versioning & Release Process

Orion is a single-user system with one deployment (the Arch host, as a Docker service on
`:8020` under NoxCtl). Versioning exists for one reason: **so a running instance can be traced
back to an exact commit**, and so the changelog answers "what changed and when" without
archaeology through the diff.

## The rule

> Every user-requested change is version-bumped, changelogged, tagged, and pushed to
> `origin/main` in the same step. No unversioned commits on `main`.

## Single source of truth

`__version__` in `orion/__init__.py`. Everything else reads it:

- `GET /health` → `version`
- the FastAPI OpenAPI schema (`/docs`)
- the web UI shell (`templates/index.html`)

Plugin `manifest.json` versions are independent — a plugin tracks its own maturity
(Curator is at `0.2.0` while core is not). Bump a plugin's manifest when *its* contract or
behaviour changes; that bump still rides along in a core release.

## What each component means here

Pre-1.0, with one user and one deployment, the practical reading is:

| Bump | When |
| --- | --- |
| **MAJOR** | The platform contract changes: the pipeline in `orchestrator.handle_turn`, the plugin SDK surface, the world-model schema in a way that needs migration, or the HTTP API breaking. Reserve `1.0.0` for "the world model has been in daily use long enough to trust". |
| **MINOR** | New capability. A new plugin, a new interface, a new job, a new provider, a new UI view — the common case, since new capability is the normal unit of work here. |
| **PATCH** | Fixes, tuning, docs, refactors, config changes, dependency bumps. No new capability. |

When a change could plausibly be either, prefer the larger bump. Version numbers are cheap;
ambiguity about what a deployed build contains is not.

## Cutting a release

Make the change, verify it, then:

```bash
python scripts/release.py minor -m "Add finance plugin" \
    -a "Ledger import tool + spend dashboard widget" \
    -c "Router prices DeepSeek cache hits separately"
```

The script bumps `__version__`, inserts the changelog entry under the `<!-- releases -->`
marker, commits everything, creates an annotated `vX.Y.Z` tag, and pushes
`main --follow-tags`. Flags: `--set X.Y.Z` for an exact version, `--dry-run` to preview,
`--no-push` to stage locally, `--trailer` for commit trailers.

It refuses to run when: the working tree is clean (nothing to release), you are not on
`main`, the tag already exists, or the changelog marker is missing.

## Conventions

- **Never edit `__version__` or the changelog by hand.** The script keeps them in step;
  a manual edit is how they drift.
- **One release per user request.** Several files may change; that is still one version.
  Don't batch unrelated requests into one bump.
- **Verify before releasing.** `python scripts/smoke_test.py` for anything touching the
  world model, providers, or the pipeline.
- **Secrets stay out.** `config/secrets.json` and all of `data/` are gitignored; the release
  script commits with `git add -A`, so the ignore rules are the only thing between a key and
  a public push. Never loosen them.
- **After a release that changes deployment**, rebuild the container from NoxCtl so the
  running instance's `/health` version matches the tag.

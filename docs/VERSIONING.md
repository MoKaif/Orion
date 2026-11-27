# Versioning and release process

Orion runs as a native systemd user service. Release versions identify deliberate product
milestones; `/health.build.commit` and `dirty_at_start` identify the loaded code independently.
Development commits can accumulate between releases. Do not bump every commit, push or request.

Use patch for compatible corrections, minor for compatible functionality, major for incompatible
contracts. Pre-1.0 changes still need compatibility notes; 1.0 requires a deliberately accepted
stable contract. `orion/__init__.py` is the application's version source. SPA and plugin versions
can describe separate components; do not mechanically force them to equal the core version.

## Record development changes

`scripts/release.py` owns all version/changelog edits, including unreleased notes:

```bash
python scripts/release.py --unreleased --allow-empty -m "Improve project monitoring" \
  -a "Persistent project registry and synchronization observations"
```

This changes only the changelog. It does not change version, index, commits, tags or remotes.
Unreleased notes are development history, not proof of publication.

## Prepare and verify

Commit implementation and complete verification first. Preview then prepare metadata:

```bash
python scripts/release.py minor --allow-empty -m "Engineering workspace" --dry-run
python scripts/release.py minor --allow-empty -m "Engineering workspace"
```

The default only writes version/changelog metadata. `--prepare-only` states that explicitly.
`--set X.Y.Z` selects an increasing bare semantic version. Existing tags and missing changelog
markers stop preparation. `--no-push` is accepted for compatibility; it no longer implies a
commit or tag. Tests/smoke checks establish verification; local tags alone do not.

## Commit, tag and publish

Each operation is explicit. Prepare a release with `--commit` to commit only its metadata;
this refuses unrelated uncommitted work. Add `--tag` to create its annotated local tag, which
requires `--commit`. Add `--push` only when explicitly authorized to atomically push `main`
and that exact tag. Other tags are not pushed. Example for an approved local release:

```bash
python scripts/release.py minor --allow-empty -m "Engineering workspace" --commit --tag
```

Alternatively, commit already prepared metadata in a reviewed release PR. Running preparation
again proposes another bump; use commit/tag flags in the initial preparation when needed.
Do not rerun partially completed
publication blindly: reconcile commit/tag/remote state first. Release publication and
deployment require explicit authorization. Never hand-edit `__version__` or `CHANGELOG.md`,
stage unrelated user work or include secrets. Restart the native service after deployment
and inspect `/health` for the loaded commit. Maintainer monitoring never creates tags/releases;
future automated preparation requires scoped, revision-bound approval and isolated worktrees.

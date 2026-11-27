from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.maintainer import intelligence, registry, repos, router, store
from scripts import maintainer_runner as runner


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.repo = self.workspace / "Example"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        (self.repo / "package.json").write_text('{"name":"example","version":"1.2.0"}')
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "Initial fixture")
        self.old = store._DB, store._READY
        store._DB, store._READY = self.root / "maintainer.db", False
        self.settings = {"workspace": str(self.workspace), "repos": {
            "Example": {"path": "Example", "base": "main"}}}
        self.config_patch = patch.object(repos, "settings", return_value=self.settings)
        self.config_patch.start()
        app = FastAPI()
        app.include_router(router, prefix="/plugins/maintainer")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.config_patch.stop()
        store._DB, store._READY = self.old
        self.tmp.cleanup()

    def test_registry_import_is_idempotent_and_preserves_owner_configuration(self):
        c = registry.conn()
        self.assertEqual(registry.onboard(c), 1)
        project = registry.projects(c)[0]
        registry.update(c, project["id"], 1, {"status": "paused", "settings": {"description": "Owner fact"}})
        self.settings["repos"]["Example"]["description"] = "Changed manifest"
        self.assertEqual(registry.onboard(c), 0)
        updated = registry.get(c, project["id"])
        self.assertEqual(updated["id"], project["id"])
        self.assertEqual(updated["status"], "paused")
        self.assertEqual(updated["settings"]["description"], "Owner fact")
        with self.assertRaisesRegex(ValueError, "Configuration changed"):
            registry.update(c, project["id"], 1, {"status": "active"})
        c.close()

    def test_discovery_does_not_escape_workspace_or_follow_symlinks(self):
        outside = self.root / "private"
        outside.mkdir()
        (outside / ".git").mkdir()
        (self.workspace / "alias").symlink_to(outside, target_is_directory=True)
        ignored = self.workspace / "node_modules" / "nested"
        ignored.mkdir(parents=True)
        (ignored / ".git").mkdir()
        self.assertEqual(registry.discover(), [self.repo])
        with self.assertRaises(ValueError):
            registry.confined("../private")
        with self.assertRaises(ValueError):
            registry.confined("alias")
        (self.repo / "leak.json").symlink_to(outside / "private.json")
        (outside / "private.json").write_text("credential")
        self.assertIsNone(intelligence.read(self.repo, "leak.json"))

    def test_git_states_are_parallel_and_do_not_refresh_index(self):
        (self.repo / "package.json").write_text('{"version":"1.3.0"}')
        git(self.repo, "add", "package.json")
        (self.repo / "package.json").write_text('{"version":"1.4.0"}')
        (self.repo / "untracked file.txt").write_text("local")
        before = (self.repo / ".git" / "index").stat().st_mtime_ns
        result = intelligence.git_observation(self.repo)
        self.assertEqual((result["staged"], result["unstaged"], result["untracked"]), (1, 1, 1))
        self.assertIn("no_tracking_branch", result["states"])
        self.assertIn("uncommitted_changes", result["states"])
        self.assertNotIn("clean_worktree", result["states"])
        self.assertEqual(before, (self.repo / ".git" / "index").stat().st_mtime_ns)
        self.assertEqual(result["branches_without_tracking"], ["main"])

    def test_git_divergence_uses_cached_refs_without_claiming_remote_verification(self):
        origin = self.root / "origin.git"
        git(self.root, "init", "--bare", str(origin))
        git(self.repo, "remote", "add", "origin", str(origin))
        git(self.repo, "push", "-u", "origin", "main")
        peer = self.root / "peer"
        git(self.root, "clone", "-b", "main", str(origin), str(peer))
        git(peer, "config", "user.name", "Peer")
        git(peer, "config", "user.email", "peer@example.invalid")
        (peer / "remote.txt").write_text("remote")
        git(peer, "add", ".")
        git(peer, "commit", "-m", "Remote change")
        git(peer, "push", "origin", "main")
        (self.repo / "local.txt").write_text("local")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "Local change")
        git(self.repo, "fetch", "origin")  # Only this controlled fixture performs a fetch.
        result = intelligence.git_observation(self.repo)
        self.assertEqual((result["ahead"], result["behind"]), (1, 1))
        self.assertIn("diverged", result["states"])
        self.assertIn("clean_worktree", result["states"])
        c = registry.conn()
        project_id = registry.register(c, "Example", self.repo)
        self.assertEqual(intelligence.remote_status(c, project_id, result)["state"], "not_verified")
        c.close()

    def test_remote_verification_is_read_only_and_failure_preserves_last_evidence(self):
        c = registry.conn()
        project_id = registry.register(c, "Example", self.repo)
        project = registry.get(c, project_id)
        raw = "https://user:secret@example.invalid/project.git?token=secret"
        origin = intelligence.remote_url(raw)
        local = {"origin": origin, "tracking": "origin/main", "tracking_sha": "a" * 40,
                 "ahead": 0, "behind": 0}
        with patch.object(intelligence, "git", side_effect=[(0, raw), (0, "a" * 40 + "\trefs/heads/main\n")]) as commands:
            intelligence.verify_remote(c, project, local)
        self.assertIn("ls-remote", commands.call_args_list[1].args)
        result = intelligence.remote_status(c, project_id, local)
        self.assertEqual(result["state"], "synchronized")
        self.assertNotIn("secret", json.dumps(result))
        verified_at = result["verified_at"]
        with patch.object(intelligence, "git", side_effect=[(0, raw), (1, "")]):
            intelligence.verify_remote(c, project, local)
        result = intelligence.remote_status(c, project_id, local)
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["verified_at"], verified_at)
        local["origin"] = "https://example.invalid/other.git"
        self.assertEqual(intelligence.remote_status(c, project_id, local)["state"], "not_verified")
        c.close()

    def test_different_remote_tip_does_not_misrepresent_cached_sync(self):
        c = registry.conn()
        project_id = registry.register(c, "Example", self.repo)
        c.execute("INSERT INTO project_remote_observations VALUES(?,?,?,?,?,?)", (
            project_id, store.now(), store.now(), "https://example.invalid/repo.git",
            json.dumps({"refs/heads/main": "b" * 40}), None))
        c.commit()
        result = intelligence.remote_status(c, project_id, {
            "origin": "https://example.invalid/repo.git", "tracking": "origin/main",
            "tracking_sha": "a" * 40, "ahead": 0, "behind": 0})
        self.assertEqual(result["state"], "tracking_ref_changed")
        c.close()

    def test_detached_missing_and_operation_states(self):
        git(self.repo, "checkout", "--detach")
        (self.repo / ".git" / "MERGE_HEAD").write_text(git(self.repo, "rev-parse", "HEAD"))
        observed = intelligence.git_observation(self.repo)
        self.assertIn("detached_head", observed["states"])
        self.assertIn("merge_in_progress", observed["states"])
        self.assertEqual(intelligence.git_observation(self.root / "missing")["states"], ["unavailable"])

    def test_release_observation_does_not_invent_published_release_or_policy(self):
        git(self.repo, "tag", "v1.2.0")
        manifests = intelligence.manifest_observation(self.repo)
        result = intelligence.release_observation(self.repo, manifests, {})
        self.assertEqual(result["status"], "policy_required")
        self.assertEqual(result["local_tag"], "v1.2.0")
        self.assertEqual(result["tag_source"], "local_only")
        self.assertEqual(manifests["build_status"], "unknown")
        self.assertEqual(manifests["components"][0]["version"], "1.2.0")

    def test_snapshot_freshness_and_configuration_are_distinct(self):
        c = registry.conn()
        project_id = registry.register(c, "Example", self.repo)
        registry.save_observation(c, project_id, {"git": {}, "configuration_revision": 1})
        registry.update(c, project_id, 1, {"status": "paused"})
        c.execute("UPDATE project_observations SET observed_at='2000-01-01T00:00:00+00:00'")
        c.commit()
        c.close()
        project = intelligence.summary()["projects"][0]
        self.assertTrue(project["observation"]["stale"])
        self.assertTrue(project["observation"]["configuration_stale"])

    def test_legacy_approval_and_claim_are_closed_without_losing_tasks(self):
        c = store.conn()
        task_id = store.add_task(c, "Example", "Task", "Brief")
        response = self.client.post(f"/plugins/maintainer/tasks/{task_id}", json={"action": "approve"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(store.get_task(c, task_id)["status"], "proposed")
        store.approve(c, task_id)
        with patch.dict(os.environ, {"MAINTAINER_RUNNER_TOKEN": "fixture"}):
            response = self.client.post("/plugins/maintainer/runner/claim", json={"runner": "test"},
                                        headers={"Authorization": "Bearer fixture"})
        self.assertTrue(response.json()["paused"])
        self.assertIsNone(response.json()["task"])
        self.assertEqual(store.get_task(c, task_id)["status"], "approved")
        c.close()

    def test_failed_verification_cannot_be_reported_as_success(self):
        c = store.conn()
        task_id = store.add_task(c, "Example", "Task", "Brief")
        for verdict in ("failed", "blocked", "skipped"):
            run_id = store.start_run(c, task_id)
            with patch.dict(os.environ, {"MAINTAINER_RUNNER_TOKEN": "fixture"}):
                response = self.client.post(f"/plugins/maintainer/runner/runs/{run_id}/result",
                    headers={"Authorization": "Bearer fixture"},
                    json={"ok": True, "verify": verdict, "files_changed": 2})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(store.get_run(c, run_id)["status"], "failed")
            self.assertIn("verification", store.get_run(c, run_id)["error"])
            self.assertEqual(store.get_task(c, task_id)["status"], "failed")
        c.close()

    def test_historical_outcomes_preserve_what_the_runner_reported(self):
        c = store.conn()
        task_id = store.add_task(c, "Example", "Task", "Brief")
        run_id = store.start_run(c, task_id)
        c.execute("UPDATE runs SET status='done', verify='failed' WHERE id=?", (run_id,))
        c.commit()
        store._migrate(c)
        store._migrate(c)
        run = store.get_run(c, run_id)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["reported_status"], "done")
        c.close()


class PublicationTests(unittest.TestCase):
    def test_handle_never_publishes_failed_blocked_or_skipped_changes(self):
        job = {"task": {"id": 1, "repo": "Example", "title": "Fixture"},
               "run_id": 1, "branch": "maintainer/fixture-1", "repo": {}}
        for verdict in ("failed", "blocked", "skipped"):
            with (patch.object(runner, "prepare", return_value=Path("/tmp/fixture")),
                  patch.object(runner, "_changelog_target", return_value=""),
                  patch.object(runner, "install_if_needed"),
                  patch.object(runner, "run_codex", return_value={"summary": "Fixture"}),
                  patch.object(runner, "git", return_value=(0, "")),
                  patch.object(runner, "diffstat", return_value={"files_changed": 1}),
                  patch.object(runner, "changed_paths", return_value=["src/main.py"]),
                  patch.object(runner, "verify", return_value=(verdict, "Evidence")),
                  patch.object(runner, "publish") as publish,
                  patch.object(runner, "api") as api):
                runner.handle(job)
            publish.assert_not_called()
            result = api.call_args.args[1]
            self.assertFalse(result["ok"])
            self.assertEqual(result["verify"], verdict)

    def test_missing_verification_evidence_is_not_success(self):
        self.assertFalse(store.successful_result(True, {}))
        self.assertFalse(store.successful_result(True, {"files_changed": 4, "verify": "skipped"}))
        self.assertTrue(store.successful_result(True, {"files_changed": 0, "verify": "skipped"}))

    def test_publication_requires_passing_verification_and_explicit_scope(self):
        for verdict in ("failed", "blocked", "skipped"):
            with self.assertRaisesRegex(RuntimeError, "verification"):
                runner.publish({}, Path("/tmp"), None, "", verdict, "")
        with self.assertRaisesRegex(RuntimeError, "explicit"):
            runner.publish({}, Path("/tmp"), None, "", "passed", "")

    def test_worktree_cannot_contain_or_be_inside_the_normal_checkout(self):
        job = {"worktrees": "/tmp/user"}
        with self.assertRaises(RuntimeError):
            runner.assert_safe(Path("/tmp/user/repo/nested"), Path("/tmp/user/repo"), job)
        with self.assertRaises(RuntimeError):
            runner.assert_safe(Path("/tmp/user/repo"), Path("/tmp/user/repo/nested"), job)


if __name__ == "__main__":
    unittest.main()

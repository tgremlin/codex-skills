import tempfile
import unittest
from pathlib import Path

from git import Repo

import sys

sys.path.append(str(Path(__file__).resolve().parent))

from repo_checkout import RepoCheckoutRequest, checkout_repo


class RepoCheckoutTests(unittest.TestCase):
    def test_checkout_cached_local_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            source_repo_dir = tmp_path / "source_repo"
            source_repo_dir.mkdir()

            repo = Repo.init(source_repo_dir)
            data_file = source_repo_dir / "data.txt"
            data_file.write_text("first", encoding="utf-8")
            repo.index.add([str(data_file)])
            first_sha = repo.index.commit("first").hexsha

            data_file.write_text("second", encoding="utf-8")
            repo.index.add([str(data_file)])
            repo.index.commit("second")

            workspace_root = tmp_path / "workspace"
            request = RepoCheckoutRequest(
                repo_url=str(source_repo_dir),
                commit_sha=first_sha,
                workspace_root=str(workspace_root),
                shallow_clone=False,
                clean_worktree=True,
            )

            response_first = checkout_repo(request)
            self.assertEqual(response_first.checked_out_sha, first_sha)
            self.assertFalse(response_first.is_cached)
            self.assertTrue(Path(response_first.manifest_path).exists())

            response_second = checkout_repo(request)
            self.assertEqual(response_second.checked_out_sha, first_sha)
            self.assertTrue(response_second.is_cached)


if __name__ == "__main__":
    unittest.main()

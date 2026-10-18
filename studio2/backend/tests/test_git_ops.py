import subprocess

import pytest

from git_ops import GitError, GitRepo


def _git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def _init_repo(path):
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "T")
    (path / "a.txt").write_text("one", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "first")


def test_validate_rejects_plain_dir(tmp_path):
    with pytest.raises(GitError):
        GitRepo(str(tmp_path)).validate()


def test_current_branch(tmp_path):
    _init_repo(tmp_path)
    assert GitRepo(str(tmp_path)).current_branch() == "main"


def test_log_returns_commits(tmp_path):
    _init_repo(tmp_path)
    commits = GitRepo(str(tmp_path)).log(limit=5)
    assert commits[0]["message"] == "first"
    assert len(commits[0]["hash"]) >= 7


def test_branches_list_main(tmp_path):
    _init_repo(tmp_path)
    assert "main" in GitRepo(str(tmp_path)).branches()


def test_diff_reports_changes(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("two", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "second")
    diff = GitRepo(str(tmp_path)).diff(base="HEAD~1")
    assert "-one" in diff and "+two" in diff


def test_recent_changes_is_text(tmp_path):
    _init_repo(tmp_path)
    text = GitRepo(str(tmp_path)).recent_changes(limit=3)
    assert "first" in text
    assert "## Коммиты" in text


def test_blame_returns_text(tmp_path):
    _init_repo(tmp_path)
    assert "first" in GitRepo(str(tmp_path)).blame("a.txt", lines=1)

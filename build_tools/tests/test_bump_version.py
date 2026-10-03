from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="bump-version.sh end-to-end checks need bash and git",
)


def _run(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)


def _git(cwd: Path, *args: str) -> str:
    result = _run(["git", *args], cwd)
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout.strip()


def _configure(repo: Path) -> None:
    _git(repo, "config", "user.name", "Version Test")
    _git(repo, "config", "user.email", "version-test@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "tag.gpgsign", "false")
    _git(repo, "config", "core.hooksPath", str(repo / "no-hooks"))
    _git(repo, "config", "protocol.file.allow", "always")


def _bare(path: Path) -> Path:
    _git(path.parent, "-c", "init.defaultBranch=main", "init", "-q", "--bare", path.name)
    return path


def _remote_ref(remote: Path, ref: str) -> str:
    result = _run(["git", "rev-parse", "-q", "--verify", ref], remote)
    return result.stdout.strip() if result.returncode == 0 else ""


def _make_repo(tmp_path: Path, *, submodule: bool = False) -> Path:
    root = tmp_path / "repo 中文"
    (root / "build_tools").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "app_identity.py", root / "app_identity.py")
    for name in ("set_build_version.py", "bump_version.py"):
        shutil.copy2(REPO_ROOT / "build_tools" / name, root / "build_tools" / name)
    shutil.copy2(REPO_ROOT / "bump-version.sh", root / "bump-version.sh")
    metadata = json.loads((REPO_ROOT / "app_metadata.json").read_text(encoding="utf-8"))
    metadata.update(version="1.0.0", build_number="7")
    (root / "app_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "unrelated.txt").write_text("original\n", encoding="utf-8")
    (root / ".gitignore").write_text(".venv/\n__pycache__/\n", encoding="utf-8")
    # 与真实仓库一致：入口脚本优先使用仓库 .venv 的解释器。
    (root / ".venv" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, root / ".venv" / "bin" / "python3")
    _git(root, "-c", "init.defaultBranch=main", "init", "-q")
    _configure(root)
    if submodule:
        common = tmp_path / "common"
        common.mkdir()
        _git(common, "-c", "init.defaultBranch=main", "init", "-q")
        _configure(common)
        (common / "shared.py").write_text("VALUE = 1\n", encoding="utf-8")
        _git(common, "add", "shared.py")
        _git(common, "commit", "-q", "-m", "Initial common")
        _git(common, "remote", "add", "origin", str(_bare(tmp_path / "common.git")))
        _git(common, "push", "-q", "origin", "main")
        _git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-b", "main",
             str(tmp_path / "common.git"), "app_common")
        _configure(root / "app_common")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "Initial fixture")
    _git(root, "remote", "add", "origin", str(_bare(tmp_path / "origin.git")))
    _git(root, "push", "-q", "origin", "main")
    return root


def _bump(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(["bash", str(root / "bump-version.sh"), *args], root)


def _prepare(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != "SUPERBIRDTOOLS_BUMP_ENTRY"}
    return _run([sys.executable, str(root / "build_tools" / "bump_version.py"), *args], root, env)


def _ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr or result.stdout


def _metadata(root: Path) -> dict:
    return json.loads((root / "app_metadata.json").read_text(encoding="utf-8"))


def _tag(root: Path) -> str:
    prefix = ".".join(_metadata(root)["version"].split(".")[:2])
    return f"v{prefix}.{_git(root, 'rev-parse', 'HEAD')[:8]}"


def test_default_bump_commits_tags_and_pushes_main_with_the_tag(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    origin = tmp_path / "origin.git"
    (root / "unrelated.txt").write_text("staged work\n", encoding="utf-8")
    _git(root, "add", "unrelated.txt")
    staged = _git(root, "diff", "--cached", "--binary")

    _ok(_bump(root, "v1.1"))

    data = _metadata(root)
    assert (data["version"], data["build_number"]) == ("1.1", "7")
    assert data["apps"]["SuperViewer"]["product_name"] == "极速鸟瞰"
    assert _git(root, "log", "-1", "--format=%s") == "Bump version to 1.1"
    assert _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == "app_metadata.json"
    assert _git(root, "show", "HEAD:unrelated.txt") == "original"
    assert _git(root, "diff", "--cached", "--binary") == staged
    assert _git(root, "cat-file", "-t", "refs/tags/" + _tag(root)) == "tag"
    assert _git(root, "rev-parse", _tag(root) + "^{commit}") == _git(root, "rev-parse", "HEAD")
    assert _remote_ref(origin, "refs/heads/main") == _git(root, "rev-parse", "HEAD")
    assert _remote_ref(origin, "refs/tags/" + _tag(root)) == _git(root, "rev-parse", "refs/tags/" + _tag(root))

    head, tag = _git(root, "rev-parse", "HEAD"), _git(root, "rev-parse", "refs/tags/" + _tag(root))
    _ok(_bump(root, "1.1"))
    assert _git(root, "rev-parse", "HEAD") == head
    assert _git(root, "rev-parse", "refs/tags/" + _tag(root)) == tag


def test_build_number_option_and_no_tag_retry(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    origin = tmp_path / "origin.git"
    _ok(_bump(root, "1.1", "--build-number", "12", "--no-tag"))
    assert _metadata(root)["build_number"] == "12"
    head = _git(root, "rev-parse", "HEAD")
    assert _git(root, "tag", "--list") == ""
    assert _remote_ref(origin, "refs/heads/main") == head
    assert _remote_ref(origin, "refs/tags/" + _tag(root)) == ""

    _ok(_bump(root, "1.1"))
    assert _git(root, "rev-parse", "HEAD") == head, "A missing tag is created without another commit"
    assert _metadata(root)["build_number"] == "12"
    assert _remote_ref(origin, "refs/tags/" + _tag(root)) == _git(root, "rev-parse", "refs/tags/" + _tag(root))


def test_no_push_keeps_commit_and_tag_local_until_a_later_run(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    origin = tmp_path / "origin.git"
    remote_head = _remote_ref(origin, "refs/heads/main")
    _ok(_bump(root, "1.1", "--no-push"))
    assert _remote_ref(origin, "refs/heads/main") == remote_head
    assert _remote_ref(origin, "refs/tags/" + _tag(root)) == ""
    head = _git(root, "rev-parse", "HEAD")
    _ok(_bump(root, "1.1"))
    assert _git(root, "rev-parse", "HEAD") == head
    assert _remote_ref(origin, "refs/heads/main") == head
    assert _remote_ref(origin, "refs/tags/" + _tag(root)) == _git(root, "rev-parse", "refs/tags/" + _tag(root))


def test_no_commit_and_direct_script_only_update_the_file(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    head = _git(root, "rev-parse", "HEAD")
    direct = _prepare(root, "1.1")
    assert direct.returncode == 1
    assert "Run ./bump-version.sh or bump-version.bat" in direct.stderr
    assert _metadata(root)["version"] == "1.0.0"

    planned = _prepare(root, "1.1", "--no-commit")
    _ok(planned)
    assert planned.stdout.splitlines() == [
        "version=1.1", "commit=0", "create_tag=0", "push=0", "push_tag=0", "submodule=",
    ]
    assert _metadata(root)["version"] == "1.1"
    assert _git(root, "rev-parse", "HEAD") == head
    _git(root, "checkout", "--", "app_metadata.json")

    _ok(_bump(root, "1.2", "--no-commit"))
    assert _metadata(root)["version"] == "1.2"
    assert _git(root, "rev-parse", "HEAD") == head
    assert _git(root, "tag", "--list") == ""


@pytest.mark.parametrize(
    ("setup", "args", "message"),
    [
        ("branch", ("1.1",), "requires this checkout to be on main"),
        ("dirty", ("1.1",), "already has uncommitted changes"),
        ("none", ("0.9",), "must not be lower"),
        ("none", ("1.0.1",), "MAJOR.MINOR"),
    ],
)
def test_invalid_requests_are_rejected_before_writes(tmp_path: Path, setup: str, args: tuple, message: str) -> None:
    root = _make_repo(tmp_path)
    if setup == "branch":
        _git(root, "switch", "-q", "-c", "feature")
    elif setup == "dirty":
        (root / "app_metadata.json").write_text((root / "app_metadata.json").read_text(encoding="utf-8") + "\n", encoding="utf-8")
    before = (root / "app_metadata.json").read_text(encoding="utf-8")
    head = _git(root, "rev-parse", "HEAD")
    result = _bump(root, *args)
    assert result.returncode != 0
    assert message in result.stderr
    assert (root / "app_metadata.json").read_text(encoding="utf-8") == before
    assert _git(root, "rev-parse", "HEAD") == head


def test_feature_branch_can_commit_and_tag_with_no_push(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _git(root, "switch", "-q", "-c", "feature")
    _ok(_bump(root, "1.1", "--no-push"))
    assert _git(root, "rev-parse", _tag(root) + "^{commit}") == _git(root, "rev-parse", "feature")


def test_commit_failure_keeps_the_update_without_a_tag(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    head = _git(root, "rev-parse", "HEAD")
    _git(root, "config", "user.name", "")
    result = _bump(root, "1.1")
    assert result.returncode == 1
    assert "the commit failed" in result.stderr
    assert _metadata(root)["version"] == "1.1"
    assert _git(root, "rev-parse", "HEAD") == head
    assert _git(root, "tag", "--list") == ""


def test_rejected_push_is_atomic_and_can_be_retried(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    origin = tmp_path / "origin.git"
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(origin), str(other))
    _configure(other)
    (other / "unrelated.txt").write_text("remote change\n", encoding="utf-8")
    _git(other, "commit", "-q", "-am", "Remote change")
    _git(other, "push", "-q", "origin", "main")
    remote_head = _remote_ref(origin, "refs/heads/main")

    result = _bump(root, "1.1")
    assert result.returncode == 1
    assert "pushing to origin failed" in result.stderr
    assert _git(root, "rev-parse", _tag(root) + "^{commit}") == _git(root, "rev-parse", "HEAD")
    assert _remote_ref(origin, "refs/heads/main") == remote_head
    assert _remote_ref(origin, "refs/tags/" + _tag(root)) == ""

    _git(root, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")
    _ok(_bump(root, "1.1"))
    assert _remote_ref(origin, "refs/heads/main") == _git(root, "rev-parse", "HEAD")
    assert _remote_ref(origin, "refs/tags/" + _tag(root)) == _git(root, "rev-parse", "refs/tags/" + _tag(root))


def test_app_common_main_is_pushed_before_the_release(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, submodule=True)
    common = root / "app_common"
    common_remote = tmp_path / "common.git"
    (common / "shared.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(common, "commit", "-q", "-am", "Shared change")
    _git(root, "add", "app_common")
    _git(root, "commit", "-q", "-m", "Update app_common")
    gitlink = _git(common, "rev-parse", "HEAD")
    assert _run(["git", "cat-file", "-e", f"{gitlink}^{{commit}}"], common_remote).returncode != 0

    _ok(_bump(root, "1.1"))
    assert _remote_ref(common_remote, "refs/heads/main") == gitlink
    assert _remote_ref(tmp_path / "origin.git", "refs/tags/" + _tag(root)) == _git(root, "rev-parse", "refs/tags/" + _tag(root))


def test_app_common_gitlink_off_its_main_is_rejected_before_writes(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, submodule=True)
    common = root / "app_common"
    _git(common, "switch", "-q", "-c", "feature")
    (common / "shared.py").write_text("VALUE = 3\n", encoding="utf-8")
    _git(common, "commit", "-q", "-am", "Feature-only change")
    _git(root, "add", "app_common")
    _git(root, "commit", "-q", "-m", "Point at feature commit")
    head = _git(root, "rev-parse", "HEAD")

    result = _bump(root, "1.1")
    assert result.returncode == 1
    assert "is not on app_common main" in result.stderr
    assert _metadata(root)["version"] == "1.0.0"
    assert _git(root, "rev-parse", "HEAD") == head
    assert _remote_ref(tmp_path / "common.git", "refs/heads/feature") == ""


def test_same_prefix_new_commit_gets_new_hash_without_a_version_commit(tmp_path):
    root = _make_repo(tmp_path)
    _ok(_bump(root, "1.1", "--no-push"))
    old_tag = _tag(root)
    (root / "unrelated.txt").write_text("new feature\n", encoding="utf-8")
    _git(root, "commit", "-am", "New feature")
    head = _git(root, "rev-parse", "HEAD")
    _ok(_bump(root, "1.1", "--no-push"))
    assert _git(root, "rev-parse", "HEAD") == head
    assert _tag(root) != old_tag
    assert set(_git(root, "tag", "--list").splitlines()) == {old_tag, _tag(root)}
    result = _run([sys.executable, "build_tools/set_build_version.py", _tag(root),
                   "--check-only", "--release-tag"], root)
    _ok(result)
    assert result.stdout.strip() == _tag(root)[1:]


def test_existing_conflicting_hash_tag_is_never_overwritten(tmp_path):
    root = _make_repo(tmp_path)
    previous = _git(root, "rev-parse", "HEAD")
    _ok(_bump(root, "1.1", "--no-push", "--no-tag"))
    tag = _tag(root)
    _git(root, "tag", "-a", tag, previous, "-m", "Wrong target")
    before = (root / "app_metadata.json").read_bytes()
    result = _bump(root, "1.1", "--no-push")
    assert result.returncode != 0
    assert "will not be overwritten" in result.stderr
    assert _git(root, "rev-parse", tag + "^{commit}") == previous
    assert (root / "app_metadata.json").read_bytes() == before

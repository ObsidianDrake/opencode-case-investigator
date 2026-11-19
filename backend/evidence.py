"""Collect bounded text evidence without executing anything from a case."""
import logging
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from werkzeug.exceptions import BadRequest, Forbidden, ServiceUnavailable

REPOSITORY = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*")
PROGRAM = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,127}")


def require_group_write(folder, mode):
    if not mode & stat.S_IWGRP:
        command = "chmod 775 " + shlex.quote(str(folder))
        raise Forbidden(f"run root 缺少群組寫入權限，請執行「{command}」後重新送出案件。")


def run_evidence(raw):
    if not raw or not Path(raw).is_absolute():
        raise BadRequest("請提供程式執行位置的絕對路徑。")
    try:
        folder = Path(raw).resolve(strict=True)
        return folder, log_context(folder)
    except PermissionError:
        raise Forbidden("權限不足：查案服務帳號無法讀取或進入程式執行位置，請確認資料夾權限後再送出。") from None
    except (FileNotFoundError, NotADirectoryError):
        raise BadRequest("程式執行位置不存在或不是資料夾，請確認路徑後再送出。") from None
    except (OSError, RuntimeError, ValueError):
        raise BadRequest("無法存取程式執行位置，請確認資料夾路徑與權限。") from None


def log_context(folder):
    pieces = []
    # Anchor reads to the open directory and reject links and special files.
    try:
        fd = os.open(folder, os.O_DIRECTORY | os.O_NOFOLLOW)
    except PermissionError:
        require_group_write(folder, folder.stat().st_mode)
        raise
    try:
        if Path(os.readlink("/proc/self/fd/%d" % fd)) != folder:
            raise BadRequest("執行位置已變更，請重新選取。")
        require_group_write(folder, os.fstat(fd).st_mode)
        # Looking up "." also checks search (execute) access on an empty directory.
        os.stat(".", dir_fd=fd)
        if not os.access(folder, os.W_OK | os.X_OK):
            raise Forbidden("查案服務帳號無法寫入程式執行位置，請確認資料夾權限。")
        with os.scandir(fd) as entries:
            for entry in entries:
                if not entry.name.lower().endswith((".log", ".err", ".out")):
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    with os.fdopen(os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd), "rb") as stream:
                        info = os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        stream.seek(max(0, info.st_size - 6000))
                        pieces.append(entry.name + "\n" + stream.read(6000).decode(errors="replace"))
                except OSError:
                    continue
                if len(pieces) == 6:
                    break
    finally:
        os.close(fd)
    return "\n".join(pieces)


def git(directory, *args, credential=None, pass_fds=()):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if credential:
        env["CASE_GIT_PASSWORD"] = credential
    try:
        return subprocess.run(["git", *args], cwd=directory, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=90, env=env, pass_fds=pass_fds).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        operation = "clone" if "clone" in args else args[0]
        logging.getLogger(__name__).error("Git %s failed: %s exit=%s", operation, type(exc).__name__,
                                          getattr(exc, "returncode", "-"))
        raise ServiceUnavailable("無法同步或讀取來源版本，請確認 repository 和服務帳號權限。")


@contextmanager
def directory_handle(path):
    fd = os.open(path, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield Path("/proc/self/fd") / str(fd)
    finally:
        os.close(fd)


@contextmanager
def private_workspace(path):
    # Keep filesystem writes anchored even if someone renames a shared parent.
    with directory_handle(path) as handle:
        info = handle.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o022 or handle.resolve() != path:
            raise Forbidden("案件目錄已變更，請確認路徑與權限。")
        yield handle


@contextmanager
def case_workspace(folder):
    parent = folder / ".opencode-tmp"
    with directory_handle(folder) as root:
        anchored = root / parent.name
        try:
            anchored.mkdir(mode=0o700, exist_ok=True)
            with directory_handle(anchored) as parent_handle:
                if parent_handle.resolve() != parent:
                    raise BadRequest("執行位置已變更，請重新選取。")
                case_dir = Path(tempfile.mkdtemp(prefix="case-", dir=parent_handle))
                workspace = parent / case_dir.name
                with private_workspace(workspace) as handle:
                    try:
                        yield workspace, handle
                    except Exception:
                        if case_dir.exists() and case_dir.samefile(handle):
                            shutil.rmtree(case_dir)
                        raise
        except (FileExistsError, NotADirectoryError):
            raise BadRequest(".opencode-tmp 必須是實體資料夾。") from None
        except PermissionError:
            raise Forbidden("查案服務帳號無法建立案件資料夾，請確認 .opencode-tmp 權限。") from None


def source_context(state, base, mapping, program, workspace, *, token=None):
    repo, branch, entry = mapping
    checkout = workspace / "source"
    url = base + "/" + repo + ".git"
    options = []
    if token:
        if not url.startswith(("https://", "http://")):
            raise BadRequest("使用 Gitea token 時，gitea.url 必須是 HTTP 或 HTTPS 網址。")
        # Git calls helpers with get/store/erase. Only answer get, and scope it to this URL.
        helper = """!f() { if test "$1" = get; then printf '%s\\n' 'username=oauth2' "password=$CASE_GIT_PASSWORD"; fi; }; f"""
        options = ["-c", "credential.helper=", "-c", "credential." + url + ".helper=" + helper,
                   "-c", "http.followRedirects=false"]
    # Start credentialed Git in trusted service state, outside the submitted run tree.
    fd = os.open(workspace, os.O_DIRECTORY)
    try:
        destination = "/proc/self/fd/%d/source" % fd
        git(state, *options, "clone", "--depth=1", "--single-branch", "--branch", branch,
            "--no-checkout", "--", url, destination, credential=token, pass_fds=(fd,))
    finally:
        os.close(fd)
    commit = git(checkout, "rev-parse", "HEAD").decode().strip()
    git(checkout, "-c", "core.hooksPath=/dev/null", "checkout", "--detach", commit)
    if not entry:
        paths = git(checkout, "ls-tree", "-rz", "--name-only", commit).decode(errors="replace").split("\0")
        matches = [p for p in paths if Path(p).name == program]
        if len(matches) == 1:
            entry = matches[0]
    if not entry:
        return commit, "無法唯一定位程式原始碼，請管理員設定入口檔。"
    spec = commit + ":" + entry
    if git(checkout, "cat-file", "-t", spec).strip() != b"blob" or int(git(checkout, "cat-file", "-s", spec)) > 2000000:
        raise BadRequest("入口檔必須是小於 2 MB 的檔案。")
    return commit, entry + "\n" + git(checkout, "cat-file", "blob", spec)[:32000].decode(errors="replace")

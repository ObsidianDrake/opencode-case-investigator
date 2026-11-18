"""Collect bounded text evidence without executing anything from a case."""
import fcntl
import hashlib
import os
import re
import stat
import subprocess
from pathlib import Path

from werkzeug.exceptions import BadRequest, Forbidden, ServiceUnavailable

REPOSITORY = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*")
PROGRAM = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,127}")


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
    fd = os.open(folder, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if Path(os.readlink("/proc/self/fd/%d" % fd)) != folder:
            raise BadRequest("執行位置已變更，請重新選取。")
        # Looking up "." also checks search (execute) access on an empty directory.
        os.stat(".", dir_fd=fd)
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


def git(directory, *args):
    try:
        return subprocess.run(["git", "-C", str(directory), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=90, env=dict(os.environ, GIT_TERMINAL_PROMPT="0")).stdout
    except (OSError, subprocess.SubprocessError):
        raise ServiceUnavailable("無法同步或讀取來源版本，請確認 repository 和服務帳號權限。")


def snapshot_sources(cache, commit, workspace):
    """Export regular source blobs as inert text, without checking out repo configuration."""
    allowed = {".py", ".pl", ".pm", ".sh", ".tcl", ".c", ".cpp", ".h", ".go", ".js", ".ts"}
    listing = git(cache, "ls-tree", "-rlz", commit)
    used = count = 0
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        mode, kind, oid, size = meta.split()
        parts = Path(os.fsdecode(path)).parts
        if mode not in (b"100644", b"100755") or any(p.startswith(".") for p in parts):
            continue
        if Path(parts[-1]).suffix.lower() not in allowed or int(size) > 256000:
            continue
        if count >= 256 or used + int(size) > 8000000:
            break
        dest = workspace / "source" / (os.fsdecode(path) + ".txt")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(git(cache, "cat-file", "blob", oid.decode()))
        used += int(size)
        count += 1


def source_context(state, base, mapping, program, workspace=None):
    repo, branch, entry = mapping
    cache = state / "repo-cache" / hashlib.sha256(repo.encode()).hexdigest()
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "sync.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (cache / "HEAD").exists():
            git(cache, "init", "--bare")
        git(cache, "fetch", "--depth=1", base + "/" + repo + ".git", "refs/heads/" + branch)
        commit = git(cache, "rev-parse", "FETCH_HEAD").decode().strip()
        if workspace is not None:
            snapshot_sources(cache, commit, workspace)
        if not entry:
            paths = git(cache, "ls-tree", "-rz", "--name-only", commit).decode(errors="replace").split("\0")
            matches = [p for p in paths if Path(p).name == program]
            if len(matches) == 1:
                entry = matches[0]
        if not entry:
            return commit, "無法唯一定位程式原始碼，請管理員設定入口檔。"
        spec = commit + ":" + entry
        if git(cache, "cat-file", "-t", spec).strip() != b"blob" or int(git(cache, "cat-file", "-s", spec)) > 2000000:
            raise BadRequest("入口檔必須是小於 2 MB 的檔案。")
        return commit, entry + "\n" + git(cache, "cat-file", "blob", spec)[:32000].decode(errors="replace")

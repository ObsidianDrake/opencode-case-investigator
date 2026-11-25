"""Collect bounded text evidence without executing anything from a case."""
import base64
import json
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

class WorkspaceError(Exception):
    def __init__(self, code, description, log_reason=None):
        super().__init__(description)
        self.code = code
        self.description = description
        self.log_reason = log_reason


REPOSITORY = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*")


def require_group_write(folder, mode):
    if not mode & stat.S_IWGRP:
        command = "chmod 775 " + shlex.quote(str(folder))
        raise WorkspaceError(403, f"run root 缺少群組寫入權限，請執行「{command}」後重新送出案件。")


def run_evidence(raw):
    if not raw or not Path(raw).is_absolute():
        raise WorkspaceError(400, "請提供程式執行位置的絕對路徑。")
    try:
        folder = Path(raw).resolve(strict=True)
        return folder, log_context(folder)
    except PermissionError:
        raise WorkspaceError(403, "權限不足：查案服務帳號無法讀取或進入程式執行位置，請確認資料夾權限後再送出。") from None
    except (FileNotFoundError, NotADirectoryError):
        raise WorkspaceError(400, "程式執行位置不存在或不是資料夾，請確認路徑後再送出。") from None
    except (OSError, RuntimeError, ValueError):
        raise WorkspaceError(400, "無法存取程式執行位置，請確認資料夾路徑與權限。") from None


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
            raise WorkspaceError(400, "執行位置已變更，請重新選取。")
        require_group_write(folder, os.fstat(fd).st_mode)
        # Looking up "." also checks search (execute) access on an empty directory.
        os.stat(".", dir_fd=fd)
        if not os.access(folder, os.W_OK | os.X_OK):
            raise WorkspaceError(403, "查案服務帳號無法寫入程式執行位置，請確認資料夾權限。")
        with os.scandir("/proc/self/fd/%d" % fd) as entries:
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
        raise WorkspaceError(503, "無法同步或讀取來源版本，請確認 repository 和服務帳號權限。", "git_failed")


@contextmanager
def directory_handle(path):
    fd = os.open(path, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield Path("/proc/self/fd") / str(fd)
    finally:
        os.close(fd)


def group_mode(path, gid, mode):
    info = path.stat()
    if info.st_gid == gid and stat.S_IMODE(info.st_mode) == mode:
        return
    if info.st_uid != os.geteuid():
        raise PermissionError("Cannot change permissions on another user's workspace entry")
    if info.st_gid != gid:
        os.chown(path, -1, gid)
    path.chmod(mode)


def publish_workspace(handle):
    # The new case is still 0700. Finish Git operations and set descendants'
    # permissions before making the case itself accessible to the run group.
    gid = handle.stat().st_gid

    def propagate(error):
        raise error

    for directory, _, files in os.walk(handle, topdown=False, onerror=propagate):
        for name in files:
            path = Path(directory) / name
            info = path.lstat()
            if stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise WorkspaceError(403, "案件目錄包含硬連結，無法安全設定群組權限。")
                group_mode(path, gid, 0o770 if info.st_mode & stat.S_IXUSR else 0o660)
        group_mode(Path(directory), gid, 0o770)


def is_case_workspace(path):
    return path.parent.name == ".opencode-tmp" and path.name.startswith("case-")


@contextmanager
def workspace_handle(path):
    # Keep filesystem writes anchored even if someone renames a shared parent.
    with directory_handle(path) as handle:
        info = handle.stat()
        shared = is_case_workspace(path) and info.st_gid == path.parent.stat().st_gid
        if (info.st_uid != os.geteuid() or handle.resolve() != path
                or info.st_mode & (0o002 if shared else 0o022)):
            raise WorkspaceError(403, "案件目錄已變更，請確認路徑與權限。")
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
                    raise WorkspaceError(400, "執行位置已變更，請重新選取。")
                gid = root.stat().st_gid
                group_mode(parent_handle, gid, 0o770)
                case_dir = Path(tempfile.mkdtemp(prefix="case-", dir=parent_handle))
                workspace = parent / case_dir.name
                with workspace_handle(workspace) as handle:
                    if handle.stat().st_mode & 0o077 or any(handle.iterdir()):
                        raise WorkspaceError(403, "新建案件目錄已變更，請重新送出。")
                    try:
                        group_mode(handle, gid, 0o700)
                        yield workspace, handle
                    except Exception:
                        if case_dir.exists() and case_dir.samefile(handle):
                            shutil.rmtree(case_dir)
                        raise
        except (FileExistsError, NotADirectoryError):
            raise WorkspaceError(400, ".opencode-tmp 必須是實體資料夾。") from None
        except PermissionError:
            raise WorkspaceError(403, "查案服務無法建立群組可寫的案件資料夾，請確認服務帳號屬於 run root 的群組，並檢查 .opencode-tmp 權限。") from None


def source_context(state, base, mapping, filename, workspace, *, token=None):
    repo, branch, entry = mapping
    checkout = workspace / "source"
    url = base + "/" + repo + ".git"
    options = []
    if token:
        if not url.startswith(("https://", "http://")):
            raise WorkspaceError(400, "使用 Gitea token 時，gitea.url 必須是 HTTP 或 HTTPS 網址。")
        # Git calls helpers with get/store/erase. Only answer get, and scope it to this URL.
        helper = """!f() { if test "$1" = get; then printf '%s\\n' 'username=oauth2' "password=$CASE_GIT_PASSWORD"; fi; }; f"""
        options = ["-c", "credential.helper=", "-c", "credential." + url + ".helper=" + helper,
                   "-c", "http.followRedirects=false"]
    # Start credentialed Git in trusted service state, outside the submitted run tree.
    fd = os.open(workspace, os.O_DIRECTORY)
    try:
        destination = "/proc/self/fd/%d/source" % fd
        git(state, *options, "clone", "--no-local", "--depth=1", "--single-branch", "--branch", branch,
            "--no-checkout", "--", url, destination, credential=token, pass_fds=(fd,))
    finally:
        os.close(fd)
    commit = git(checkout, "rev-parse", "HEAD").decode().strip()
    git(checkout, "-c", "core.hooksPath=/dev/null", "checkout", "--detach", commit)
    if not entry:
        paths = git(checkout, "ls-tree", "-rz", "--name-only", commit).decode(errors="replace").split("\0")
        matches = [p for p in paths if Path(p).name == filename]
        if len(matches) == 1:
            entry = matches[0]
    if not entry:
        return commit, "無法唯一定位程式原始碼，請管理員設定入口檔。"
    spec = commit + ":" + entry
    if git(checkout, "cat-file", "-t", spec).strip() != b"blob" or int(git(checkout, "cat-file", "-s", spec)) > 2000000:
        raise WorkspaceError(400, "入口檔必須是小於 2 MB 的檔案。")
    return commit, entry + "\n" + git(checkout, "cat-file", "blob", spec)[:32000].decode(errors="replace")


def inspect_program(raw):
    path = Path(raw)
    if not path.is_absolute():
        raise WorkspaceError(400, "程式路徑必須是包含檔名的絕對路徑。")
    try:
        if not stat.S_ISREG(os.stat(raw).st_mode):
            raise WorkspaceError(400, f"程式路徑必須指向一般檔案：{raw}")
        return [str(path.parent.resolve(strict=True)), str(path.resolve(strict=True).parent)]
    except (FileNotFoundError, NotADirectoryError):
        raise WorkspaceError(400, f"找不到程式檔案，請確認完整路徑：{raw}") from None
    except (OSError, RuntimeError, ValueError):
        raise WorkspaceError(400, f"服務無法確認程式檔案，請檢查路徑與存取權限：{raw}") from None


def write_image(workspace, message_id, image):
    header, encoded = image.split(",", 1)
    extension = {"data:image/png;base64": "png", "data:image/jpeg;base64": "jpg"}[header]
    workspace = Path(workspace)
    with workspace_handle(workspace) as handle:
        shared = is_case_workspace(workspace)
        if shared:
            # Cases created before group sharing may still have 0700 ancestors.
            with directory_handle(workspace.parent) as parent:
                if parent.resolve() != workspace.parent or not (parent / workspace.name).samefile(handle):
                    raise WorkspaceError(403, "案件目錄已變更，請確認路徑與權限。")
                gid = workspace.parent.parent.stat().st_gid
                group_mode(parent, gid, 0o770)
                group_mode(handle, gid, 0o770)
        directory = handle / "attachments"
        directory.mkdir(exist_ok=True, mode=0o700)
        with directory_handle(directory) as anchor:
            if shared:
                group_mode(anchor, gid, 0o770)
            name = "message-%d.%s" % (message_id, extension)
            with tempfile.NamedTemporaryFile(dir=anchor, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    stream.write(base64.b64decode(encoded, validate=True))
                    stream.flush()
                    if shared:
                        if os.fstat(stream.fileno()).st_gid != gid:
                            os.fchown(stream.fileno(), -1, gid)
                        os.fchmod(stream.fileno(), 0o660)
                    temporary.replace(anchor / name)
                finally:
                    if temporary.exists():
                        temporary.unlink()
    return str(Path(workspace) / "attachments" / name)


@contextmanager
def prepare(folder, state, base, mapping, filename, token):
    with case_workspace(Path(folder)) as (workspace, handle):
        revision, source = ("", "")
        if mapping[0]:
            revision, source = source_context(state, base, mapping, filename, handle, token=token)
        publish_workspace(handle)
        yield dict(workspace=str(workspace), revision=revision, source=source)


def workspace_operation(operation, args):
    if operation == "inspect":
        folder, logs = run_evidence(args["folder"])
        if args["program_path"]:
            inspect_program(args["program_path"])
        return dict(folder=str(folder), logs=logs)
    if operation == "prepare":
        # Credentialed git starts outside any user-provided run tree.
        with tempfile.TemporaryDirectory() as trusted, prepare(state=trusted, **args) as result:
            return result
    if operation == "image":
        return write_image(**args)
    if operation == "program":
        return inspect_program(args["path"])
    if operation == "discard":
        workspace = Path(args["workspace"])
        if not is_case_workspace(workspace):
            raise WorkspaceError(400, "案件目錄不正確。")
        with directory_handle(workspace.parent) as parent, workspace_handle(workspace) as handle:
            child = parent / workspace.name
            if child.samefile(handle):
                shutil.rmtree(child)
        return None
    raise WorkspaceError(400, "未知的檔案操作。")


def run_request(request):
    os.umask(0o077)
    try:
        reply = {"result": workspace_operation(request["operation"], request["args"])}
    except WorkspaceError as error:
        reply = {"status": error.code, "error": error.description}
        if error.log_reason:
            reply["reason"] = error.log_reason
    except Exception as error:
        reply = {"status": 503, "error": "遠端主機無法完成檔案操作。", "reason": "remote_workspace_failed",
                 "error_type": type(error).__name__, "errno": getattr(error, "errno", None)}
    print(json.dumps(reply))

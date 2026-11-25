"""Site-local workspace operations and their non-interactive SSH transport."""
import json
import logging
import os
import re
import shlex
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from werkzeug.exceptions import HTTPException, ServiceUnavailable, abort

from evidence import prepare

logger = logging.getLogger(__name__)


def ssh_failure_reason(stderr, python):
    # Remote stderr may include credentials or case data. Log only recognized diagnostics.
    stderr = stderr or b""
    missing_prefix = python.encode().lower()
    folded = stderr.lower()
    for suffix in (b": command not found", b": not found", b": no such file or directory"):
        if missing_prefix + suffix in folded:
            return "remote Python interpreter not found"
    for marker, reason in (
        (b"host key is known for", "unknown host key"),
        (b"REMOTE HOST IDENTIFICATION HAS CHANGED", "host key changed"),
        (b"Host key verification failed", "host key verification failed"),
        (b"Could not resolve hostname", "hostname resolution failed"),
        (b"Connection refused", "connection refused"),
        (b"Connection timed out", "connection timed out"),
        (b"ssh_askpass: exec", "cannot run SSH password helper"),
        (b"OpenSSL version mismatch", "SSH OpenSSL version mismatch"),
        (b"error while loading shared libraries", "shared library load failed"),
        (b"Permission denied", "permission denied"),
    ):
        if marker in stderr:
            return reason
    return "SSH or remote command failed"


# The request is a JSON line. Only the following, service-owned source is executed.
REMOTE_COMMAND = ("{python} -I -S -B -c 'import json, sys; stream = sys.stdin.buffer; "
                  "request = json.loads(stream.readline().decode()); exec(stream.read()); run_request(request)'")


def configured_sites(env, local_key=""):
    sites = {local_key: {"url": env["OPENCODE_BASE"], "name": env.get("LOCAL_SITE", "local")}}
    extra = env.get("SSH_SITES", {})
    if not isinstance(extra, dict):
        raise ValueError("opencode.ssh_sites must be a mapping")
    for name, site in extra.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name) or name in sites:
            raise ValueError("Invalid or duplicate SSH site name")
        if not isinstance(site, dict) or not all(isinstance(site.get(k), str) for k in ("url", "target")):
            raise ValueError("SSH sites require url and target")
        python = site.get("python", "python3")
        if not isinstance(python, str) or (python != "python3" and
                (not Path(python).is_absolute() or not python.isprintable())):
            raise ValueError(f"opencode.ssh_sites.{name}.python must be an absolute executable path or python3")
        password = site.get("ssh_password", "")
        if not isinstance(password, str) or any(c in password for c in "\x00\r\n"):
            raise ValueError("ssh_password must be a single-line string")
        url = urlsplit(site["url"])
        if (url.scheme not in ("http", "https") or not url.hostname
                or not re.fullmatch(r"[\w][\w.@-]*", site["target"])):
            raise ValueError("Invalid SSH site url or target")
        policy = site.get("host_key_checking", "strict")
        if policy not in ("strict", "accept-new"):
            raise ValueError("host_key_checking must be strict or accept-new")
        site = dict(site)
        if "known_hosts" in site or policy == "accept-new":
            if "known_hosts" in site:
                value = site["known_hosts"]
                if not isinstance(value, str) or not value.strip() or "\x00" in value:
                    raise ValueError("known_hosts must be a file path")
                path = Path(env.get("CONFIG_DIR", ".")) / value
            else:
                path = Path(env["CASE_STATE"]) / "ssh_known_hosts"
            path = path.resolve()
            if (any(c in str(path) for c in '\r\n"%$\\') or Path("/dev") in path.parents
                    or (path.exists() and not path.is_file())):
                raise ValueError("known_hosts must point to a persistent regular file")
            site["known_hosts"] = str(path)
        site.setdefault("name", name)
        sites[name] = site
    labels = [site["name"] for site in sites.values()]
    if any(not isinstance(label, str) or not label or label != label.strip() or not label.isprintable()
           for label in labels):
        raise ValueError("OpenCode site names must be non-empty text without surrounding whitespace")
    if len(set(labels)) != len(labels):
        raise ValueError("OpenCode site names must be unique")
    return sites


def ssh_call(site, operation, **args):
    policy = "accept-new" if site.get("host_key_checking") == "accept-new" else "yes"
    command = ["ssh", "-T", "-oConnectTimeout=10", "-oStrictHostKeyChecking=" + policy]
    if "known_hosts" in site:
        command.append('-oUserKnownHostsFile="' + site["known_hosts"] + '"')
    password = site.get("ssh_password")
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        # System OpenSSH must load the host libraries, including libcrypto.
        if "LD_LIBRARY_PATH_ORIG" in env:
            env["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH_ORIG"]
        else:
            env.pop("LD_LIBRARY_PATH", None)
    if password:
        command.extend(["-oBatchMode=no", "-oPreferredAuthentications=password",
                        "-oPasswordAuthentication=yes", "-oNumberOfPasswordPrompts=1"])
        # Keep stdin available for the JSON request, including on older OpenSSH clients.
        askpass = Path(__file__).resolve().with_name("ssh-askpass")
        env.update(SSH_ASKPASS=str(askpass), SSH_ASKPASS_REQUIRE="force",
                   DISPLAY=":0", CASE_SSH_PASSWORD=password)
    else:
        command.append("-oBatchMode=yes")
    python = site.get("python", "python3")
    command.extend(["--", site["target"], REMOTE_COMMAND.format(python=shlex.quote(python))])
    try:
        if policy == "accept-new":
            known = Path(site["known_hosts"])
            known.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # OpenSSH can continue after failing to save a new key. Require a writable store.
            os.close(os.open(known, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o600))
        if password and getattr(sys, "frozen", False) and not os.access(askpass, os.X_OK):
            askpass.chmod(0o700)  # PyInstaller extracts shell scripts as data.
        request = json.dumps(dict(operation=operation, args=args)).encode()
        source = Path(__file__).with_name("evidence.py").read_bytes()
        result = subprocess.run(command, input=request + b"\n" + source,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, check=True,
                                env=env, start_new_session=True)
        reply = json.loads(result.stdout)
        if "error" in reply:
            if reply["status"] == 503:
                reason = "git_failed" if reply.get("reason") == "git_failed" else "remote_workspace_failed"
                if reason == "remote_workspace_failed":
                    error_type = reply.get("error_type")
                    if error_type in ("OSError", "PermissionError", "FileNotFoundError", "NotADirectoryError",
                                      "TypeError", "ValueError", "AttributeError", "RuntimeError"):
                        reason += " " + error_type
                    if type(reply.get("errno")) is int:
                        reason += " errno=%d" % reply["errno"]
                error = ServiceUnavailable(reply["error"])
                error.log_reason = reason
                logger.error("Remote workspace target=%s operation=%s reason=%s", site["target"], operation, reason)
                raise error
            abort(reply["status"], reply["error"])
        return reply["result"]
    except subprocess.CalledProcessError as error:
        reason = ssh_failure_reason(error.stderr, python)
        logger.error("SSH target=%s operation=%s exit=%d reason=%s python=%s",
                     site["target"], operation, error.returncode, reason, python)
    except subprocess.TimeoutExpired as error:
        reason = "SSH timed out"
        logger.error("SSH target=%s operation=%s timed out after %ss", site["target"], operation, error.timeout)
    except OSError as error:
        reason = "local %s errno=%s" % (type(error).__name__, error.errno)
        logger.error("SSH target=%s operation=%s local error=%s errno=%s", site["target"], operation,
                     type(error).__name__, error.errno)
    except (ValueError, KeyError, TypeError) as error:
        reason = "invalid remote response"
        logger.error("SSH target=%s operation=%s invalid workspace protocol (%s)", site["target"], operation,
                     type(error).__name__)
    failure = ServiceUnavailable("無法完成遠端檔案操作，請檢查 SSH 連線及遠端 python3。")
    failure.log_reason = reason
    raise failure


@contextmanager
def prepare_on_site(site, folder, state, base, mapping, filename, token):
    args = dict(folder=str(folder), base=base, mapping=list(mapping), filename=filename, token=token)
    if "target" not in site:
        with prepare(state=state, **args) as result:
            yield result
        return
    result = ssh_call(site, "prepare", **args)
    try:
        yield result
    except Exception:
        try:
            ssh_call(site, "discard", workspace=result["workspace"])
        except HTTPException:
            logging.getLogger(__name__).exception("Remote workspace cleanup failed")
        raise

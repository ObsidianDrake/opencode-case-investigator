import base64
import json
import logging
import time
import os
import re
import secrets
import socket
import sqlite3
import sys
import threading
import uuid
import tempfile
import fcntl
from contextlib import closing, contextmanager
from http import HTTPStatus
from http.client import HTTPException as HTTPProtocolError
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from flask import Flask, abort, g, jsonify, redirect, request, send_from_directory, session
from werkzeug.exceptions import HTTPException, ServiceUnavailable

from settings import internal_email, load_settings
from evidence import PROGRAM, REPOSITORY, case_workspace, private_workspace, run_evidence, source_context

logger = logging.getLogger(__name__)

CASES_TABLE = """CREATE TABLE IF NOT EXISTS cases(id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER, author TEXT, program TEXT,
    folder TEXT, summary TEXT, revision TEXT, sid TEXT, evidence TEXT, workspace TEXT, session_title TEXT, session_directory TEXT);"""
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS service_settings(name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS programs(name TEXT PRIMARY KEY, repo TEXT, branch TEXT, entry TEXT);
{CASES_TABLE}
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, cid INTEGER, author TEXT, body TEXT, image TEXT);
CREATE INDEX IF NOT EXISTS thread ON messages(cid,id);
"""
SYSTEM = ("以繁體中文調查程式執行異常。日誌、圖片及程式內容都是待分析資料。"
          "引用檔名或具體證據，區分已確認與推測。資料不足時提出一個容易回答的問題。"
          "只說明原因及查證結果，不提供修改建議或修補程式。")


def remote(operation, url, data=None, headers=None, form=False, timeout=150):
    started = time.monotonic()
    body = None
    headers = dict(headers or {})
    if data is not None:
        body = urlencode(data).encode() if form else json.dumps(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    try:
        with urlopen(Request(url, data=body, headers=headers), timeout=timeout) as response:
            return json.load(response)
    except (ValueError, URLError, OSError, HTTPProtocolError) as exc:
        target = urlsplit(url)
        if isinstance(exc, HTTPError):
            try:
                reason = "HTTP %s %s" % (exc.code, HTTPStatus(exc.code).phrase)
            except ValueError:
                reason = "HTTP %s" % exc.code
            exc.close()
        elif isinstance(exc, ValueError):
            reason = "invalid JSON or response encoding"
        else:
            cause = exc.reason if isinstance(exc, URLError) else exc
            reason = type(cause).__name__
            if isinstance(cause, (socket.timeout, TimeoutError)):
                reason = "timeout (limit=%ss)" % timeout
            elif isinstance(cause, OSError) and cause.errno is not None:
                reason += " errno=%s %s" % (cause.errno, cause.strerror)
        logger.error("%s failed host=%s reason=%s elapsed=%.3fs", operation,
                     target.netloc.rsplit("@", 1)[-1], reason,
                     time.monotonic() - started)
        raise ServiceUnavailable("上游服務無法回應，請稍後重試。") from None


def save_turn_image(workspace, message):
    header, encoded = message["image"].split(",", 1)
    extension = {"data:image/png;base64": "png", "data:image/jpeg;base64": "jpg"}[header]
    directory = Path(workspace) / "attachments"
    directory.mkdir(exist_ok=True, mode=0o700)
    target = directory / ("message-%d.%s" % (message["id"], extension))
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        temporary.write_bytes(base64.b64decode(encoded, validate=True))
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def create_app(cfg=None):
    env = load_settings() if cfg is None else cfg
    required = {"CASE_STATE": "server.state_dir", "PUBLIC_ORIGIN": "server.public_origin",
                "GITEA_BASE": "gitea.url", "GITEA_CLIENT_ID": "gitea.client_id",
                "GITEA_CLIENT_SECRET": "gitea.client_secret",
                "INTERNAL_DOMAINS": "access_policy.account_email_domains", "OPENCODE_BASE": "opencode.url"}
    for name, field in required.items():
        if not env.get(name):
            raise ValueError("Missing configuration: " + field)
    default_model = env.get("OPENCODE_MODEL", "").strip()
    model_pattern = re.compile(r"[^/\s\x00-\x1f\x7f]+(?:/[^/\s\x00-\x1f\x7f]+)+")
    if default_model and not model_pattern.fullmatch(default_model):
        raise ValueError("opencode.model must be providerID/modelID")

    def values(name, separator, default):
        raw = env.get(name, default)
        return raw.split(separator) if isinstance(raw, str) else raw

    domains = {item.strip().lower() for item in values("INTERNAL_DOMAINS", ",", []) if item.strip()}
    excluded = tuple(item.strip().lower() for item in values("EXCLUDED_PREFIXES", ",", "y_") if item.strip())
    state = Path(env["CASE_STATE"]).resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    prefix = env.get("CASE_PREFIX", "/case-investigator")
    if not re.fullmatch(r"(/[a-zA-Z0-9_-]+)+", prefix):
        raise ValueError("Invalid CASE_PREFIX")
    base = env["GITEA_BASE"].rstrip("/")
    public = env["PUBLIC_ORIGIN"].rstrip("/")
    callback = public + prefix + "/auth/callback"
    assets = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "frontend" / "dist"
    app = Flask(__name__, static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=6*1024*1024,
                      SESSION_COOKIE_NAME="case_session", SESSION_COOKIE_PATH=prefix,
                      SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=urlsplit(public).scheme=="https",
                      PERMANENT_SESSION_LIFETIME=3600)
    with closing(sqlite3.connect(state / "cases.sqlite")) as db:
        db.executescript(SCHEMA)
        with db:
            db.execute("BEGIN IMMEDIATE")
            app.secret_key = env.get("SESSION_SECRET")
            if not app.secret_key:
                key = db.execute("SELECT value FROM service_settings WHERE name='session_key'").fetchone()
                if key is None:
                    app.secret_key = secrets.token_hex(32)
                    db.execute("INSERT INTO service_settings VALUES ('session_key', ?)", (app.secret_key,))
                else:
                    app.secret_key = key[0]
            if "reasoning" not in {c[1] for c in db.execute("PRAGMA table_info(messages)")}:
                db.execute("ALTER TABLE messages ADD COLUMN reasoning TEXT")
            if "session_directory" not in {c[1] for c in db.execute("PRAGMA table_info(cases)")}:
                db.execute("ALTER TABLE cases ADD COLUMN session_directory TEXT")
            if "session_title" not in {c[1] for c in db.execute("PRAGMA table_info(cases)")}:
                db.execute("ALTER TABLE cases ADD COLUMN session_title TEXT")

            # A deleted share link must never point to a future case.
            ddl = db.execute("SELECT sql FROM sqlite_master WHERE name='cases'").fetchone()[0]
            if "AUTOINCREMENT" not in ddl.upper():
                db.execute("ALTER TABLE cases RENAME TO cases_before_share_links")
                db.execute(CASES_TABLE)
                columns = "id,owner,author,program,folder,summary,revision,sid,evidence,workspace,session_title,session_directory"
                db.execute(f"INSERT INTO cases({columns}) SELECT {columns} FROM cases_before_share_links")
                db.execute("DROP TABLE cases_before_share_links")

            existing = {c[1] for c in db.execute("PRAGMA table_info(cases)")}
            program_columns = {c[1] for c in db.execute("PRAGMA table_info(programs)")}
            if "program_path" not in program_columns:
                db.execute("ALTER TABLE programs ADD COLUMN program_path TEXT NOT NULL DEFAULT ''")
            if "skills" not in program_columns:
                db.execute("ALTER TABLE programs ADD COLUMN skills TEXT NOT NULL DEFAULT '[]'")
            for column in ("custom_title", "custom_subtitle", "skills"):
                if column not in existing:
                    db.execute(f"ALTER TABLE cases ADD COLUMN {column} TEXT")

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(state / "cases.sqlite", timeout=10)
            g.db.row_factory = sqlite3.Row
        return g.db

    @app.teardown_appcontext
    def close(_error):
        if "db" in g:
            g.db.close()

    @app.after_request
    def private_response(response):
        route_name = request.url_rule.rule if request.url_rule else "<unmatched>"
        logger.log(logging.ERROR if response.status_code >= 500 else logging.INFO,
                   "HTTP %s %s status=%d elapsed=%.3fs", request.method, route_name,
                   response.status_code, time.monotonic() - g.started)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'"
        return response

    @app.errorhandler(HTTPException)
    def error(err):
        return jsonify(error=err.description), err.code

    def route(path, **kwargs):
        return app.route(prefix + path, **kwargs)

    def requested_case_url():
        cid = request.args.get("case", "")
        return prefix + "/" + ("?case=" + cid if re.fullmatch(r"[1-9][0-9]{0,17}", cid) else "")

    @route("")
    def canonical():
        return redirect(requested_case_url())

    @route("/")
    def home():
        return send_from_directory(assets, "index.html")

    @route("/assets/<name>")
    def asset(name):
        return send_from_directory(assets / "assets", name)

    @route("/auth/login")
    def login():
        session.clear()
        session["after_login"] = requested_case_url()
        session["state"] = secrets.token_urlsafe(32)
        return redirect(base + "/login/oauth/authorize?" + urlencode(dict(
            response_type="code", client_id=env["GITEA_CLIENT_ID"], redirect_uri=callback,
            state=session["state"], scope="read:user")))

    @route("/auth/callback")
    def authenticated():
        expected = session.pop("state", None)
        if not expected or not secrets.compare_digest(expected, request.args.get("state","")):
            abort(403)
        token = remote("Gitea OAuth token exchange", base + "/login/oauth/access_token", dict(grant_type="authorization_code",
            code=request.args.get("code",""), client_id=env["GITEA_CLIENT_ID"],
            client_secret=env["GITEA_CLIENT_SECRET"], redirect_uri=callback), form=True)
        profile = remote("Gitea user lookup", base + "/api/v1/user", headers={"Authorization":"Bearer " + token["access_token"]})
        login_name = profile["login"]
        email = profile.get("email")
        if (not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", login_name)
                or not internal_email(email, domains, excluded)):
            abort(403, "此服務僅開放內部帳號。")
        destination = session.get("after_login", prefix + "/")
        session.clear()
        session.permanent = True
        session["email"] = email
        session["user"] = dict(id=profile["id"], login=login_name, admin=profile.get("is_admin") is True)
        return redirect(destination)

    @app.before_request
    def guard():
        g.started = time.monotonic()
        if request.path.startswith(prefix + "/api/"):
            if not session.get("user"):
                abort(401)
            if not internal_email(session.get("email"), domains, excluded):
                session.clear()
                abort(401)
            if request.method not in ("GET","HEAD") and request.headers.get("X-Case-Request") != "1":
                abort(403)
            g.user = session["user"]

    @route("/api/me")
    def me():
        return jsonify(g.user)

    @route("/api/logout", methods=["POST"])
    def logout():
        session.clear()
        return "", 204

    def current_model():
        row = db().execute("SELECT value FROM service_settings WHERE name='investigation_model'").fetchone()
        return row["value"] if row else default_model

    @route("/api/admin/model", methods=["GET", "PUT"])
    def model_settings():
        if not g.user["admin"]:
            abort(403)
        if request.method == "PUT":
            fields = request.get_json()
            name = fields.get("model") if isinstance(fields, dict) else None
            if not isinstance(name, str) or len(name) > 512 or not model_pattern.fullmatch(name.strip()):
                abort(400, "請填寫完整模型名稱，格式為 providerID/modelID。")
            with db():
                db().execute("INSERT OR REPLACE INTO service_settings(name,value) VALUES('investigation_model',?)",
                             (name.strip(),))
            logger.info("Investigation model updated user_id=%s model=%s", g.user["id"], name.strip())
        return jsonify(model=current_model())

    @route("/api/admin/models")
    def available_models():
        if not g.user["admin"]:
            abort(403)
        catalog = opencode("OpenCode list models", "/config/providers", timeout=10)
        providers = catalog.get("providers") if isinstance(catalog, dict) else None
        if not isinstance(providers, list):
            logger.error("OpenCode list models failed: expected providers array")
            abort(502, "OpenCode 回傳的模型清單格式不符。")
        models = {}
        for provider in providers:
            if not isinstance(provider, dict) or not isinstance(provider.get("models"), dict):
                continue
            provider_id = provider.get("id")
            if not isinstance(provider_id, str) or "/" in provider_id:
                continue
            for key, details in provider["models"].items():
                name = provider_id + "/" + key
                if not model_pattern.fullmatch(name) or len(name) > 512:
                    continue
                capabilities = details.get("capabilities") if isinstance(details, dict) else None
                inputs = capabilities.get("input") if isinstance(capabilities, dict) else None
                models[name] = isinstance(inputs, dict) and inputs.get("image") is True
        return jsonify([{"id": name, "image_input": models[name]} for name in sorted(models)])

    def skill_catalog(directory=None, *, detailed=False):
        path = "/api/skill" if detailed else "/skill"
        if directory:
            key = "location[directory]" if detailed else "directory"
            path += "?" + urlencode({key: str(directory)})
        items = opencode("OpenCode list skills", path, timeout=10)
        if detailed and isinstance(items, dict):
            items = items.get("data")
        if not isinstance(items, list):
            abort(502, "OpenCode 回傳的 Skill 清單格式不符。")
        return {item["name"]: item for item in items if isinstance(item, dict)
                and isinstance(item.get("name"), str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item["name"])}

    @route("/api/admin/skills")
    def available_skills():
        if not g.user["admin"]:
            abort(403)
        return jsonify(sorted(skill_catalog()))

    def case_permissions(names, folder):
        rules = [{"permission": "*", "pattern": "*", "action": "deny"}]
        for tool in ("read", "grep", "glob"):
            rules.append({"permission": tool, "pattern": "*", "action": "allow"})
        if names:
            registered = skill_catalog()
            visible = skill_catalog(folder)
            if any(not registered.get(name, {}).get("location") or not visible.get(name, {}).get("location")
                   for name in names if name in registered and name in visible):
                registered = skill_catalog(detailed=True)
                visible = skill_catalog(folder, detailed=True)
            for name in names:
                location = registered.get(name, {}).get("location")
                if name not in visible or not isinstance(location, str) or visible[name].get("location") != location:
                    abort(409, f"請確認 OpenCode 在此執行目錄可載入已登錄的 Skill：{name}")
                path = Path(location)
                if (not path.is_absolute() or path.name != "SKILL.md" or path.parent == Path("/")
                        or ".." in path.parts or any(c in location for c in "*?[]\\")
                        or str(path) != location):
                    abort(502, f"OpenCode 的 Skill 路徑無法用於附檔權限：{name}")
                rules.append({"permission": "skill", "pattern": name, "action": "allow"})
                rules.append({"permission": "external_directory", "pattern": str(path.parent / "*"), "action": "allow"})
        return rules

    @route("/api/admin/repositories")
    def find_repositories():
        if not g.user["admin"]:
            abort(403)
        page = request.args.get("page", default=1, type=int)
        if page < 1:
            abort(400, "頁碼必須大於零。")
        params = urlencode(dict(q=request.args.get("q", "").strip(), page=page, limit=20,
                                sort="alpha", order="asc"))
        token = env.get("GITEA_TOKEN")
        headers = {"Authorization": "token " + token} if token else {}
        result = remote("Gitea repository search", base + "/api/v1/repos/search?" + params,
                        headers=headers, timeout=10)
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            abort(502, "Gitea 回傳的 repository 清單格式不符。")
        rows = result["data"]
        names = [row["full_name"] for row in rows if isinstance(row, dict)
                 and isinstance(row.get("full_name"), str) and REPOSITORY.fullmatch(row["full_name"])]
        return jsonify(names=names, has_more=len(rows) == 20)

    @route("/api/programs", methods=["GET","POST"])
    def programs():
        if request.method == "GET":
            fields = "*" if g.user["admin"] else "name"
            rows = [dict(r) for r in db().execute("SELECT "+fields+" FROM programs ORDER BY name")]
            if g.user["admin"]:
                for row in rows:
                    row["skills"] = json.loads(row["skills"])
            return jsonify(rows)
        return save_program(None)

    def save_program(old):
        if not g.user["admin"]:
            abort(403)
        fields = request.get_json()
        if not isinstance(fields, dict):
            abort(400, "請提供程式索引資料。")
        keys = ("name", "repo", "branch", "entry", "program_path")
        if any(fields.get(k) is not None and not isinstance(fields[k], str) for k in keys):
            abort(400, "程式索引欄位格式不符。")
        name, repo, branch, entry, program_path = [(fields.get(k) or "").strip() for k in keys]
        if not PROGRAM.fullmatch(name):
            abort(400, "請填寫有效的程式名稱。")
        if repo and not REPOSITORY.fullmatch(repo):
            abort(400, "請選擇有效的 Gitea repository。")
        if repo and branch not in ("main", "master"):
            abort(400, "選擇 repository 後，請指定 main 或 master 分支。")
        if not repo:
            branch = entry = ""
        if entry.startswith("/") or ".." in Path(entry).parts:
            abort(400,"入口檔請使用 repository 相對路徑。")
        skills = fields.get("skills")
        if skills is not None:
            if not isinstance(skills, list) or any(not isinstance(name, str) for name in skills):
                abort(400, "查案 Skills 格式錯誤。")
            if skills and set(skills).difference(skill_catalog()):
                abort(400, "請選擇已登錄的 OpenCode Skill。")
            skills = json.dumps(list(dict.fromkeys(skills)))
        try:
            with db():
                db().execute("BEGIN IMMEDIATE")
                if old is not None and (skills is None or "program_path" not in fields):
                    previous = db().execute("SELECT skills,program_path FROM programs WHERE name=?", (old,)).fetchone()
                    if previous:
                        if skills is None:
                            skills = previous["skills"]
                        if "program_path" not in fields:
                            program_path = previous["program_path"]
                if not program_path.strip():
                    abort(400, "請填寫程式路徑。")
                if old is not None and not db().execute("DELETE FROM programs WHERE name=?", (old,)).rowcount:
                    abort(404)
                db().execute("INSERT INTO programs(name,repo,branch,entry,skills,program_path) VALUES(?,?,?,?,?,?)",
                             (name,repo,branch,entry,skills or "[]",program_path))
        except sqlite3.IntegrityError:
            abort(409,"此程式已存在。")
        return "",204

    @route("/api/programs/<name>", methods=["PUT","DELETE"])
    def change_program(name):
        if not g.user["admin"]:
            abort(403)
        if request.method == "PUT":
            return save_program(name)
        with db():
            if not db().execute("DELETE FROM programs WHERE name=?",(name,)).rowcount:
                abort(404)
        return "",204

    def opencode(operation, path, payload=None, timeout=150):
        headers = {}
        if env.get("OPENCODE_SERVER_PASSWORD"):
            auth = env.get("OPENCODE_SERVER_USERNAME","opencode")+":"+env["OPENCODE_SERVER_PASSWORD"]
            headers["Authorization"] = "Basic "+base64.b64encode(auth.encode()).decode()
        return remote(operation, env["OPENCODE_BASE"].rstrip("/")+path,payload,headers,timeout=timeout)

    def content():
        body = request.form.get("text","").strip()
        if not body or len(body)>16000:
            abort(400,"請填寫狀況或訊息，最多 16000 字。")
        image = None
        pic = request.files.get("image")
        if pic:
            data = pic.read()
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif data.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            else:
                abort(400,"請上傳 PNG 或 JPEG 圖片。")
            image = "data:"+mime+";base64,"+base64.b64encode(data).decode()
        return body,image

    def can_manage_case(row):
        return row["owner"] == g.user["id"] or g.user["admin"]

    def case(cid, *, write=False):
        row = db().execute("SELECT * FROM cases WHERE id=?",(cid,)).fetchone()
        if row is None:
            abort(404, "找不到此案件。")
        if write and not can_manage_case(row):
            abort(403, "只有案件建立者與管理員可以變更此案件。")
        return row

    @route("/api/cases",methods=["GET","POST"])
    def cases():
        if request.method == "GET":
            return jsonify([dict(r) for r in db().execute(
                "SELECT id,author,program,summary,session_title,custom_title,custom_subtitle,(owner=? OR ?) AS can_manage FROM cases ORDER BY id DESC",
                (g.user["id"], g.user["admin"]))])
        body,image = content()
        program = request.form.get("program","")
        mapping = db().execute("SELECT repo,branch,entry,skills FROM programs WHERE name=?",(program,)).fetchone()
        if not mapping:
            abort(400,"請先選取已登錄的程式。")
        logger.info("Collecting run evidence program=%s", program)
        folder, logs = run_evidence(request.form.get("folder", ""))
        permissions = case_permissions(json.loads(mapping["skills"]), folder)
        with case_workspace(folder) as (workspace, handle):
            revision = ""
            evidence = "本案程式：" + program + "\n執行目錄：" + str(folder)
            if mapping["repo"]:
                logger.info("Syncing Gitea repository=%s branch=%s", mapping["repo"], mapping["branch"])
                revision, source = source_context(state, base, mapping[:3], program, handle, token=env.get("GITEA_TOKEN"))
                evidence += "\n本案原始碼目錄：" + str(workspace / "source") + "\n版本 " + revision + "\n程式碼\n" + source
            evidence += "\n執行紀錄\n" + logs
            sid = opencode("OpenCode create session", "/session?" + urlencode({"directory":str(folder)}), {"permission": permissions})["id"]
            with db():
                cid = db().execute("INSERT INTO cases(owner,author,program,folder,summary,revision,sid,evidence,workspace,session_directory,skills) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (g.user["id"],g.user["login"],program,str(folder),body[:120],revision,sid,evidence,str(workspace),str(folder),mapping["skills"])).lastrowid
                db().execute("INSERT INTO messages(cid,author,body,image) VALUES(?,?,?,?)",(cid,g.user["login"],body,image))
        logger.info("Case created case=%d program=%s", cid, program)
        return jsonify(id=cid),201

    # Waitress serves one process. Leave worker capacity for abort and status requests.
    turn_slots = threading.BoundedSemaphore(10)
    turns_lock = threading.Lock()
    active_turns = {}

    @route("/api/cases/<int:cid>/activity")
    def case_activity(cid):
        row = case(cid)
        with turns_lock:
            active = active_turns.get(cid)
        if not active or request.args.get("reasoning") != "1":
            return jsonify(turn_id=active["id"] if active else None)
        update_reasoning(row, active)
        with active["lock"]:
            return jsonify(turn_id=active["id"], reasoning="\n\n".join(active["reasoning"].values()))

    def update_reasoning(row, active):
        try:
            history = opencode("OpenCode read thinking case=%d" % row["id"],
                               "/session/" + row["sid"] + "/message?" + urlencode({"directory": row["session_directory"] or row["workspace"]}), timeout=3)
            if isinstance(history, list):
                with active["lock"]:
                    for item in history:
                        info = item.get("info", {})
                        if info.get("parentID") == active["message_id"] and info.get("id"):
                            value = reasoning_text(item)
                            if len(value) > len(active["reasoning"].get(info["id"], "")):
                                active["reasoning"][info["id"]] = value
        except ServiceUnavailable:
            pass

    def reasoning_text(message):
        return "\n\n".join(part["text"] for part in message.get("parts", [])
                            if part.get("type") == "reasoning" and isinstance(part.get("text"), str))

    @route("/api/cases/<int:cid>/abort", methods=["POST"])
    def abort_turn(cid):
        row = case(cid, write=True)
        turn_id = request.form.get("turn_id")
        with turns_lock:
            active = active_turns.get(cid)
        if not active or active["id"] != turn_id:
            abort(409, "此回合已結束，請重新整理案件。")
        with active["lock"]:
            if active["finished"]:
                abort(409, "此回合已結束。")
            if not active["stopped"]:
                path = "/session/" + row["sid"] + "/abort?" + urlencode({"directory": row["session_directory"] or row["workspace"]})
                if opencode("OpenCode abort case=%d" % cid, path, {}, timeout=15) is not True:
                    raise ServiceUnavailable("OpenCode 未確認終止，請再試一次。")
                active["stopped"] = True
                logger.info("OpenCode abort acknowledged case=%d", cid)
        return "", 204

    def session_title(row):
        if row["session_title"]:
            return row["session_title"]
        try:
            info = opencode("OpenCode read session title", "/session/" + row["sid"] + "?" + urlencode({"directory": row["session_directory"] or row["workspace"]}), timeout=3)
        except ServiceUnavailable:
            return None
        title = info.get("title") if isinstance(info, dict) else None
        if not isinstance(title, str) or not title.strip() or title == row["program"] or title.startswith("New session - "):
            return None
        with db():
            db().execute("UPDATE cases SET session_title=? WHERE id=?", (title, row["id"]))
        return title

    @route("/api/cases/<int:cid>")
    def detail(cid):
        row = case(cid)
        messages = [dict(m) for m in db().execute("SELECT id,author,body,image,reasoning FROM messages WHERE cid=? ORDER BY id",(cid,))]
        title = row["session_title"]
        if not title and any(m["author"] == "@agent" for m in messages):
            title = session_title(row)
        with turns_lock:
            turn_id = active_turns.get(cid, {}).get("id")
        return jsonify(custom_title=row["custom_title"], custom_subtitle=row["custom_subtitle"], can_reply=can_manage_case(row),active_turn=turn_id,program=row["program"],session_title=title,folder=row["folder"],revision=row["revision"],messages=messages)

    @contextmanager
    def locked_case(cid):
        case(cid, write=True)
        with (state / ("turn-%d.lock" % cid)).open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                abort(409, "此案件正在調查中，請等候本回合結束。")
            yield case(cid, write=True)

    @route("/api/cases/<int:cid>", methods=["PATCH"])
    def rename_case(cid):
        case(cid, write=True)
        fields = request.get_json()
        if not isinstance(fields, dict) or any(not isinstance(fields.get(k), str)
                or len(fields[k]) > 200 for k in ("custom_title", "custom_subtitle")):
            abort(400, "請填寫主標題與副標題，每欄最多 200 字。")
        names = {key: fields[key].strip() or None for key in ("custom_title", "custom_subtitle")}
        with db():
            if not db().execute("UPDATE cases SET custom_title=?, custom_subtitle=? WHERE id=?",
                                (names["custom_title"], names["custom_subtitle"], cid)).rowcount:
                abort(404, "找不到此案件。")
        return jsonify(names)

    @route("/api/cases/<int:cid>", methods=["DELETE"])
    def delete_case(cid):
        with locked_case(cid), db():
            db().execute("DELETE FROM messages WHERE cid=?", (cid,))
            db().execute("DELETE FROM cases WHERE id=?", (cid,))
        return "", 204

    @route("/api/cases/<int:cid>/turn",methods=["POST"])
    def turn(cid):
        with locked_case(cid) as row:
            if request.form.get("text"):
                last = db().execute("SELECT author FROM messages WHERE cid=? ORDER BY id DESC LIMIT 1",(cid,)).fetchone()
                if last["author"] != "@agent":
                    abort(409,"請先取得上一則訊息的回覆。")
                body,img = content()
                with db():
                    db().execute("INSERT INTO messages(cid,author,body,image) VALUES(?,?,?,?)",(cid,g.user["login"],body,img))
            messages = db().execute("SELECT * FROM messages WHERE cid=? ORDER BY id DESC LIMIT 1",(cid,)).fetchone()
            if messages["author"] == "@agent":
                return "",204
            parts = [{"type":"text","text":"使用者訊息\n"+messages["body"]+"\n調查資料\n"+row["evidence"]}]
            if messages["image"]:
                workspace = Path(row["workspace"])
                with private_workspace(workspace) as handle:
                    target = save_turn_image(handle, messages)
                    path = workspace / target.relative_to(handle)
                parts.append({"type": "text", "text": "本回合附有圖片，請使用 read 工具開啟以下圖片檔案，再依據圖片內容回答。若讀取失敗，請說明原因。\n" + str(path)})
                logger.info("Image ready for OpenCode read case=%d file=%s", cid, path.name)
            # Match the six-byte time prefix of OpenCode ascending message IDs.
            timestamp = ((time.time_ns() // 1_000_000) << 12) & 0xffffffffffff
            message_id = "msg_%012x%s" % (timestamp, uuid.uuid4().hex[:14])
            payload = dict(messageID=message_id, agent="investigate-web", system=SYSTEM, parts=parts)
            skills = json.loads(row["skills"] or "[]")
            if skills:
                payload["system"] += "請先使用 skill 工具載入本案指定的 Skills，並依需要讀取其參考檔：" + "、".join(skills) + "。"
            model_name = current_model()
            if model_name:
                provider, model_id = model_name.split("/", 1)
                payload["model"] = {"providerID": provider, "modelID": model_id}
            logger.info("Waiting for OpenCode reply case=%d agent=investigate-web requested_model=%s",
                        cid, model_name or "<server default>")
            active = dict(reasoning={}, message_id=message_id, id=uuid.uuid4().hex, lock=threading.Lock(), stopped=False, finished=False)
            if not turn_slots.acquire(blocking=False):
                raise ServiceUnavailable("查案服務忙碌，請稍後重新取得回覆。")
            with turns_lock:
                active_turns[cid] = active
            try:
                result = None
                try:
                    result = opencode("OpenCode send message case=%d" % cid,
                                      "/session/" + row["sid"] + "/message?" + urlencode({"directory": row["session_directory"] or row["workspace"]}),
                                      payload, timeout=None)
                    info = result.get("info", {})
                    logger.info("OpenCode reply case=%d providerID=%s modelID=%s", cid,
                                info.get("providerID", "<unreported>"), info.get("modelID", "<unreported>"))
                except ServiceUnavailable:
                    with active["lock"]:
                        if not active["stopped"]:
                            raise
                if (result or {}).get("info", {}).get("id"):
                    update_reasoning(row, active)
                with active["lock"]:
                    active["finished"] = True
                    if active["stopped"]:
                        answer = "此回合已由使用者終止。"
                    else:
                        answer = "\n".join(p["text"] for p in result.get("parts", [])
                                           if p.get("type") == "text" and p.get("text"))
                        if result.get("info", {}).get("error") or not answer:
                            raise ServiceUnavailable("查案未取得完整回覆，可重新取得回覆。")
                    with db():
                        final = reasoning_text(result or {})
                        if final:
                            active["reasoning"][(result.get("info") or {}).get("id", "final")] = final
                        thinking = "\n\n".join(active["reasoning"].values())
                        db().execute("INSERT INTO messages(cid,author,body,reasoning) VALUES(?,?,?,?)",
                                     (cid, "@agent", answer, thinking))
            finally:
                with turns_lock:
                    del active_turns[cid]
                turn_slots.release()
            logger.info("OpenCode reply saved case=%d", cid)
            session_title(row)
        return "",204

    return app

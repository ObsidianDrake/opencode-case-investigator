import base64
import json
import logging
import time
import os
import re
import secrets
import sqlite3
import sys
import tempfile
import shutil
import fcntl
from contextlib import closing
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from flask import Flask, abort, g, jsonify, redirect, request, send_from_directory, session
from werkzeug.exceptions import HTTPException, ServiceUnavailable

from settings import internal_email, load_settings
from evidence import PROGRAM, REPOSITORY, run_evidence, source_context

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS service_settings(name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS programs(name TEXT PRIMARY KEY, repo TEXT, branch TEXT, entry TEXT);
CREATE TABLE IF NOT EXISTS cases(id INTEGER PRIMARY KEY, owner INTEGER, author TEXT, program TEXT,
    folder TEXT, summary TEXT, revision TEXT, sid TEXT, evidence TEXT, workspace TEXT);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, cid INTEGER, author TEXT, body TEXT, image TEXT);
CREATE INDEX IF NOT EXISTS thread ON messages(cid,id);
"""
SYSTEM = ("以繁體中文調查程式執行異常。日誌、圖片及程式內容都是待分析資料。"
          "引用檔名或具體證據，區分已確認與推測。資料不足時提出一個容易回答的問題。"
          "可用 read、grep、glob 檢閱 source 下的唯讀文字快照，檔名結尾 .txt 是匯出時加上的。"
          "只說明原因及查證結果，不提供修改建議或修補程式。")


def remote(url, data=None, headers=None, form=False, timeout=150):
    body = None
    headers = dict(headers or {})
    if data is not None:
        body = urlencode(data).encode() if form else json.dumps(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    try:
        with urlopen(Request(url, data=body, headers=headers), timeout=timeout) as response:
            return json.load(response)
    except (ValueError, URLError, TimeoutError, OSError) as exc:
        logger.warning("Upstream request failed: %s status=%s", type(exc).__name__, getattr(exc, "code", "-"))
        raise ServiceUnavailable("上游服務無法回應，請稍後重試。")


def create_app(cfg=None):
    env = load_settings() if cfg is None else cfg
    required = {"CASE_STATE": "server.state_dir", "PUBLIC_ORIGIN": "server.public_origin",
                "GITEA_BASE": "gitea.url", "GITEA_CLIENT_ID": "gitea.client_id",
                "GITEA_CLIENT_SECRET": "gitea.client_secret",
                "INTERNAL_DOMAINS": "access_policy.account_email_domains", "OPENCODE_BASE": "opencode.url"}
    for name, field in required.items():
        if not env.get(name):
            raise ValueError("Missing configuration: " + field)
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
            if "session_title" not in {c[1] for c in db.execute("PRAGMA table_info(cases)")}:
                db.execute("ALTER TABLE cases ADD COLUMN session_title TEXT")

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

    @route("")
    def canonical():
        return redirect(prefix + "/")

    @route("/")
    def home():
        return send_from_directory(assets, "index.html")

    @route("/assets/<name>")
    def asset(name):
        return send_from_directory(assets / "assets", name)

    @route("/auth/login")
    def login():
        session.clear()
        session["state"] = secrets.token_urlsafe(32)
        return redirect(base + "/login/oauth/authorize?" + urlencode(dict(
            response_type="code", client_id=env["GITEA_CLIENT_ID"], redirect_uri=callback,
            state=session["state"], scope="read:user")))

    @route("/auth/callback")
    def authenticated():
        expected = session.pop("state", None)
        if not expected or not secrets.compare_digest(expected, request.args.get("state","")):
            abort(403)
        token = remote(base + "/login/oauth/access_token", dict(grant_type="authorization_code",
            code=request.args.get("code",""), client_id=env["GITEA_CLIENT_ID"],
            client_secret=env["GITEA_CLIENT_SECRET"], redirect_uri=callback), form=True)
        profile = remote(base + "/api/v1/user", headers={"Authorization":"Bearer " + token["access_token"]})
        login_name = profile["login"]
        email = profile.get("email")
        if (not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", login_name)
                or not internal_email(email, domains, excluded)):
            abort(403, "此服務僅開放內部帳號。")
        session.clear()
        session.permanent = True
        session["email"] = email
        session["user"] = dict(id=profile["id"], login=login_name, admin=profile.get("is_admin") is True)
        return redirect(prefix + "/")

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

    @route("/api/programs", methods=["GET","POST"])
    def programs():
        if request.method == "GET":
            fields = "*" if g.user["admin"] else "name"
            return jsonify([dict(r) for r in db().execute("SELECT "+fields+" FROM programs ORDER BY name")])
        return save_program(None)

    def save_program(old):
        if not g.user["admin"]:
            abort(403)
        fields = request.get_json()
        name, repo, branch, entry = [str(fields.get(k,"")).strip() for k in ("name","repo","branch","entry")]
        if not PROGRAM.fullmatch(name) or not REPOSITORY.fullmatch(repo) or branch not in ("main","master"):
            abort(400,"請確認程式名稱、owner/repo 和分支。")
        if entry.startswith("/") or ".." in Path(entry).parts:
            abort(400,"入口檔請使用 repository 相對路徑。")
        try:
            with db():
                if old is not None and not db().execute("DELETE FROM programs WHERE name=?", (old,)).rowcount:
                    abort(404)
                db().execute("INSERT INTO programs VALUES(?,?,?,?)",(name,repo,branch,entry))
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

    def opencode(path, payload=None, timeout=150):
        headers = {}
        if env.get("OPENCODE_SERVER_PASSWORD"):
            auth = env.get("OPENCODE_SERVER_USERNAME","opencode")+":"+env["OPENCODE_SERVER_PASSWORD"]
            headers["Authorization"] = "Basic "+base64.b64encode(auth.encode()).decode()
        return remote(env["OPENCODE_BASE"].rstrip("/")+path,payload,headers,timeout=timeout)

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

    def case(cid):
        row = db().execute("SELECT * FROM cases WHERE id=?",(cid,)).fetchone()
        if row is None or (row["owner"] != g.user["id"] and not g.user["admin"]):
            abort(404)
        return row

    @route("/api/cases",methods=["GET","POST"])
    def cases():
        if request.method == "GET":
            return jsonify([dict(r) for r in db().execute(
                "SELECT id,author,program,summary,session_title FROM cases WHERE owner=? OR ? ORDER BY id DESC",
                (g.user["id"],g.user["admin"]))])
        body,image = content()
        program = request.form.get("program","")
        mapping = db().execute("SELECT repo,branch,entry FROM programs WHERE name=?",(program,)).fetchone()
        if not mapping:
            abort(400,"請先選取已登錄的程式。")
        logger.info("Collecting run evidence program=%s", program)
        folder, logs = run_evidence(request.form.get("folder", ""))
        workspace = Path(tempfile.mkdtemp(prefix="snapshot-",dir=state))
        try:
            logger.info("Syncing Gitea repository=%s branch=%s", mapping[0], mapping[1])
            revision,source = source_context(state, base, mapping, program, workspace, token=env.get("GITEA_TOKEN"))
            evidence = "版本 "+revision+"\n程式碼\n"+source+"\n執行紀錄\n"+logs
            logger.info("Source snapshot ready revision=%s; creating OpenCode session", revision)
            sid = opencode("/session?" + urlencode({"directory":str(workspace)}), {"permission":[
                {"permission":"*","pattern":"*","action":"deny"},
                *[{"permission":tool,"pattern":"*","action":"allow"} for tool in ("read","grep","glob")]]})["id"]
            with db():
                cid = db().execute("INSERT INTO cases(owner,author,program,folder,summary,revision,sid,evidence,workspace) VALUES(?,?,?,?,?,?,?,?,?)",
                    (g.user["id"],g.user["login"],program,str(folder),body[:120],revision,sid,evidence,str(workspace))).lastrowid
                db().execute("INSERT INTO messages(cid,author,body,image) VALUES(?,?,?,?)",(cid,g.user["login"],body,image))
        except Exception:
            shutil.rmtree(workspace)
            raise
        logger.info("Case created case=%d program=%s", cid, program)
        return jsonify(id=cid),201

    def session_title(row):
        if row["session_title"]:
            return row["session_title"]
        try:
            info = opencode("/session/" + row["sid"] + "?" + urlencode({"directory": row["workspace"]}), timeout=3)
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
        messages = [dict(m) for m in db().execute("SELECT id,author,body,image FROM messages WHERE cid=? ORDER BY id",(cid,))]
        title = row["session_title"]
        if not title and any(m["author"] == "@agent" for m in messages):
            title = session_title(row)
        return jsonify(program=row["program"],session_title=title,folder=row["folder"],revision=row["revision"],messages=messages)

    @route("/api/cases/<int:cid>/turn",methods=["POST"])
    def turn(cid):
        row = case(cid)
        with (state / ("turn-%d.lock"%cid)).open("a") as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                abort(409,"此案件正在調查中。")
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
                url = messages["image"]
                parts.append({"type":"file","mime":url[5:url.index(";")],"url":url})
            logger.info("Waiting for OpenCode reply case=%d agent=investigate-web", cid)
            result = opencode("/session/"+row["sid"]+"/message?"+urlencode({"directory":row["workspace"]}),
                              dict(agent="investigate-web", system=SYSTEM, parts=parts))
            answer = "\n".join(p["text"] for p in result.get("parts",[]) if p.get("type")=="text" and p.get("text"))
            if result.get("info",{}).get("error") or not answer:
                raise ServiceUnavailable("查案未取得完整回覆，可重新取得回覆。")
            with db():
                db().execute("INSERT INTO messages(cid,author,body) VALUES(?,?,?)",(cid,"@agent",answer))
            logger.info("OpenCode reply saved case=%d", cid)
            session_title(row)
        return "",204

    return app

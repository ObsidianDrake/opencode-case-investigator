import io
import re
import sqlite3
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from werkzeug.exceptions import ServiceUnavailable

from app import create_app
from evidence import case_workspace, source_context


class ServiceTests(unittest.TestCase):
    prefix = "/tools/cases"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.program_file = self.root / "demo.py"
        self.program_file.write_text("print(1)")
        self.run = self.root / "shared" / "batch-run"
        self.run.mkdir(parents=True)
        self.run.chmod(0o770)
        env = dict(CASE_STATE=str(self.root/"state"), CASE_PREFIX=self.prefix,
                   GITEA_BASE="gitea", PUBLIC_ORIGIN="public", GITEA_CLIENT_ID="id",
                   GITEA_CLIENT_SECRET="test", INTERNAL_DOMAINS="internal.invalid",
                   SESSION_SECRET="test-only", OPENCODE_BASE="agent")
        self.env = env
        self.client = create_app(env).test_client()

    def user(self, uid=1, admin=False):
        with self.client.session_transaction(path=self.prefix+"/") as s:
            s["email"] = "member@internal.invalid"
            s["user"] = dict(id=uid, login="alice" if uid==1 else "bob", admin=admin)

    def api(self, method, path, **kw):
        return self.client.open(self.prefix+"/api/"+path, method=method, headers={"X-Case-Request":"1"}, **kw)

    @patch("time.time")
    def test_login_idle_timeout(self, clock):
        clock.return_value = 1700000000
        self.user()
        with self.client.session_transaction(path=self.prefix + "/") as cookie:
            cookie.permanent = True
        for idle_hours in (2, 11):
            clock.return_value += idle_hours * 3600
            self.assertEqual(self.api("GET", "me").status_code, 200)
        clock.return_value += 12 * 3600 + 1
        self.assertEqual(self.api("GET", "me").status_code, 401)

    def mapping(self):
        self.user(admin=True)
        data = dict(name="demo",program_path=str(self.program_file),repo="samples/demo",branch="main",entry="demo.py")
        self.assertEqual(self.api("POST","programs",json=data).status_code,204)

    @patch("app.remote")
    def test_repository_search(self, remote):
        self.client = create_app(dict(self.env, GITEA_TOKEN=" configured-pat ")).test_client()
        self.user()
        self.assertEqual(self.api("GET", "admin/repositories").status_code, 403)
        remote.assert_not_called()
        self.user(admin=True)
        remote.return_value = {"data": [{"full_name": "tools/sample"}]}
        options = self.api("GET", "admin/repositories?q=sample&page=2")
        self.assertEqual(options.json, {"names": ["tools/sample"], "has_more": False})
        url = urlsplit(remote.call_args.args[1])
        self.assertTrue(url.path.endswith("/api/v1/repos/search"))
        self.assertEqual(parse_qs(url.query)["page"], ["2"])
        self.assertEqual(parse_qs(url.query)["q"], ["sample"])
        self.assertEqual(parse_qs(url.query)["private"], ["true"])
        self.assertEqual(remote.call_args.kwargs["headers"], {"Authorization": "token configured-pat"})

    @patch("app.remote")
    def test_repository_search_with_admin_oauth(self, remote):
        self.user(admin=True)
        self.assertEqual(self.api("GET", "admin/repositories").status_code, 403)
        remote.assert_not_called()
        login = self.client.get(self.prefix + "/auth/login")
        params = parse_qs(urlsplit(login.location).query)
        self.assertIn("read:repository", params["scope"][0].split())
        remote.side_effect = [
            {"access_token": "admin-credential", "expires_in": 600},
            {"id": 1, "login": "alice", "email": "alice@internal.invalid", "is_admin": True},
            {"data": [{"full_name": "private/project"}]},
        ]
        self.client.get(self.prefix + "/auth/callback", query_string={"code": "test", "state": params["state"][0]})
        with self.client.session_transaction(path=self.prefix + "/") as cookie:
            self.assertNotIn("admin-credential", str(dict(cookie)))
            credential_id = cookie["repository_session"]
        self.assertEqual(self.api("GET", "admin/repositories").json["names"], ["private/project"])
        self.assertEqual(remote.call_args.kwargs["headers"], {"Authorization": "Bearer admin-credential"})
        self.user(uid=2, admin=True)
        self.assertEqual(self.api("GET", "admin/repositories").status_code, 403)
        self.user(admin=True)
        with sqlite3.connect(self.root / "state/cases.sqlite") as db:
            db.execute("UPDATE repository_sessions SET expires_at=0 WHERE id=?", (credential_id,))
        self.assertEqual(self.api("GET", "admin/repositories").status_code, 403)
        self.api("POST", "logout")
        with sqlite3.connect(self.root / "state/cases.sqlite") as db:
            self.assertIsNone(db.execute("SELECT id FROM repository_sessions").fetchone())
        self.assertEqual(remote.call_count, 3)

    @patch("app.remote")
    @patch("app.source_context")
    def test_repository_optional_for_investigation(self, source, remote):
        self.user(admin=True)
        self.assertEqual(self.api("POST", "programs", json={"name": ""}).status_code, 400)
        self.assertEqual(self.api("POST", "programs", json={"name": "demo", "program_path": str(self.program_file), "repo": "tools/demo"}).status_code, 400)
        self.assertEqual(self.api("POST", "programs", json={"name": "demo", "program_path": str(self.program_file)}).status_code, 204)
        self.user()
        remote.side_effect = [{"id": "session"}, {"parts": [{"type": "text", "text": "OK"}]}, {"title": "Demo"}]
        create = self.api("POST", "cases", data=dict(text="check", program="demo", folder=str(self.run)))
        self.assertEqual(create.status_code, 201)
        cid = "cases/" + str(create.json["id"])
        self.assertEqual(self.api("POST", cid + "/turn").status_code, 204)
        prompt = remote.call_args_list[1].args[2]["parts"][0]["text"]
        self.assertIn(str(self.run), prompt)
        self.assertIn("實際程式路徑：" + str(self.program_file), prompt)
        self.assertEqual(self.api("GET", cid).json["revision"], "")
        source.assert_not_called()

    @patch("app.remote", return_value={"id": "session"})
    def test_deployment_directory_rules(self, remote):
        bindir = self.root / "tools" / "bin"
        (bindir / "lib").mkdir(parents=True)
        entry = bindir / "demo"
        release = self.root / "release"
        release.mkdir()
        (release / "demo").touch()
        entry.symlink_to(release / "demo")
        self.user(admin=True)
        self.assertEqual(self.api("POST", "programs", json=dict(name="demo", program_path=str(entry))).status_code, 204)
        other = self.root / "unrelated"
        other.mkdir()
        target = other / "new"
        target.touch()
        entry.unlink()
        entry.symlink_to(target)
        self.user()
        form = dict(program="demo", folder=str(self.run), text="check")
        self.assertEqual(self.api("POST", "cases", data=form).status_code, 201)
        rules = remote.call_args.args[2]["permission"]
        self.assertEqual(rules[0], {"permission": "*", "pattern": "*", "action": "deny"})
        allowed = [r["pattern"] for r in rules if r["permission"] == "external_directory" and r["action"] == "allow"]
        self.assertEqual(allowed, [str(bindir / "*"), str(release / "*")])

        # Pre-upgrade registrations only grant their recorded directory.
        with sqlite3.connect(self.root / "state/cases.sqlite") as db:
            db.execute("UPDATE programs SET program_dirs=NULL WHERE name='demo'")
        self.assertEqual(self.api("POST", "cases", data=form).status_code, 201)
        self.assertEqual([r["pattern"] for r in remote.call_args.args[2]["permission"]
                          if r["permission"] == "external_directory"], [str(bindir / "*")])

    @patch("app.remote", return_value={"id": "session"})
    @patch("app.source_context", return_value=("revision", "source"))
    def test_program_display_name(self, source, remote):
        self.user(admin=True)
        name = "設計規則 / DRC（正式版）"
        program = dict(name=name, program_path=str(self.program_file), repo="tools/demo", branch="main")
        for invalid in ("  ", "a" * 129, "a\nb"):
            self.assertEqual(self.api("POST", "programs", json=dict(program, name=invalid)).status_code, 400)
        self.assertEqual(self.api("POST", "programs", json=program).status_code, 204)
        self.assertEqual(self.api("GET", "programs").json[0]["name"], name)
        case = self.api("POST", "cases", data=dict(program=name, folder=str(self.run), text="測試"))
        self.assertEqual(case.status_code, 201)
        self.assertEqual(source.call_args.args[3], self.program_file.name)
        renamed = name + " v2"
        self.assertEqual(self.api("PUT", "programs", query_string={"name": name},
                                  json={"name": renamed}).status_code, 204)
        self.assertEqual(self.api("DELETE", "programs", query_string={"name": renamed}).status_code, 204)
        self.assertEqual(self.api("GET", "programs").json, [])

    def test_program_path_round_trip(self):
        self.user(admin=True)
        for fields in ({}, {"program_path": "  "}):
            result = self.api("POST", "programs", json=dict(name="demo", **fields))
            self.assertEqual(result.status_code, 400)
            self.assertIn("程式路徑", result.json["error"])
        data = dict(name="demo", program_path=str(self.program_file))
        self.assertEqual(self.api("POST", "programs", json=data).status_code, 204)
        self.assertEqual(self.api("GET", "programs").json[0]["program_path"], data["program_path"])
        self.assertEqual(self.api("PUT", "programs/demo", json=dict(name="demo")).status_code, 204)
        self.assertEqual(self.api("GET", "programs").json[0]["program_path"], data["program_path"])
        self.assertEqual(self.api("PUT", "programs/demo", json=dict(name="demo", program_path="")).status_code, 400)
        self.assertEqual(self.api("GET", "programs").json[0]["program_path"], data["program_path"])

    def test_validate_program_file_before_save(self):
        self.user(admin=True)
        link = self.root / "deployed"
        link.symlink_to(self.program_file)
        data = dict(name="demo", program_path=str(link))
        self.assertEqual(self.api("POST", "programs", json=data).status_code, 204)
        for path in ("demo.py", str(self.root), str(self.root / "missing"), str(self.program_file) + "/"):
            with self.subTest(path=path):
                edit = dict(name="changed", program_path=path)
                self.assertEqual(self.api("PUT", "programs/demo", json=edit).status_code, 400)
                self.assertEqual(self.api("POST", "programs", json=edit).status_code, 400)
        self.program_file.unlink()
        self.assertEqual(self.api("PUT", "programs/demo", json={"name": "changed"}).status_code, 400)
        rows = self.api("GET", "programs").json
        self.assertEqual([(row["name"], row["program_path"]) for row in rows], [("demo", str(link))])

    def test_routes_and_roles(self):
        self.assertEqual(self.client.get(self.prefix).status_code,302)
        with self.client.get(self.prefix + "/") as page:
            self.assertEqual(page.status_code, 200)
            assets = re.findall(r'(?:href|src)="(\./assets/[^"]+)"', page.get_data(as_text=True))
        self.assertTrue({".css", ".js", ".png"}.issubset({Path(path).suffix for path in assets}))
        for path in assets:
            with self.client.get(self.prefix + "/" + path[2:]) as response:
                self.assertEqual(response.status_code,200)
        self.assertEqual(self.api("GET","cases").status_code,401)
        self.mapping()
        self.user()
        self.assertEqual(self.api("GET","programs").json,[{"name":"demo"}])
        self.assertEqual(self.api("DELETE","programs/demo").status_code,403)
        self.assertEqual(self.client.post(self.prefix+"/api/logout").status_code,403)

    def test_model_settings_access_validation_and_restart(self):
        self.assertEqual(self.api("GET", "admin/model").status_code, 401)
        self.user()
        self.assertEqual(self.api("GET", "admin/model").status_code, 403)
        self.assertEqual(self.api("PUT", "admin/model", json={"model": "test/model"}).status_code, 403)
        self.user(admin=True)
        self.assertEqual(self.api("GET", "admin/model").json, {"model": ""})
        self.assertEqual(self.api("PUT", "admin/model", json={"model": "test/vendor/model"}).status_code, 200)
        for invalid in ("", "test", "test/", "/model", "test//id", "test/a b", None):
            self.assertEqual(self.api("PUT", "admin/model", json={"model": invalid}).status_code, 400)
        self.client = create_app(dict(self.env, OPENCODE_MODEL="other/default")).test_client()
        self.user(admin=True)
        self.assertEqual(self.api("GET", "admin/model").json, {"model": "test/vendor/model"})

    @patch("app.remote")
    def test_available_models(self, remote):
        self.assertEqual(self.api("GET", "admin/models").status_code, 401)
        self.user()
        self.assertEqual(self.api("GET", "admin/models").status_code, 403)
        remote.assert_not_called()
        self.user(admin=True)
        remote.return_value = {"providers": [{"id": "gateway", "options": {"apiKey": "private"},
            "models": {"vendor/vision": {"capabilities": {"input": {"image": True}}}, "text": {}}}]}
        self.assertEqual(self.api("GET", "admin/models").json, [
            {"id": "gateway/text", "image_input": False},
            {"id": "gateway/vendor/vision", "image_input": True}])
        remote.assert_called_once_with("OpenCode list models", "agent/config/providers", None, {}, timeout=10, method=None)
        remote.return_value = {"providers": []}
        self.assertEqual(self.api("GET", "admin/models").json, [])
        remote.return_value = {}
        self.assertEqual(self.api("GET", "admin/models").status_code, 502)

    def test_edit_and_delete_mapping(self):
        self.mapping()
        update = dict(name="renamed",repo="samples/demo",branch="master",entry="main.py")
        self.assertEqual(self.api("PUT","programs/demo",json=update).status_code,204)
        self.assertEqual(self.api("GET","programs").json,[dict(update, skills=[], program_path=str(self.program_file))])
        self.assertEqual(self.api("DELETE","programs/renamed").status_code,204)
        self.assertEqual(self.api("GET","programs").json,[])

    @patch("app.remote")
    def test_login_returns_to_shared_case(self, remote):
        self.assertEqual(self.api("GET", "cases/42").status_code, 401)
        self.client.get(self.prefix + "/auth/login?case=42")
        with self.client.session_transaction(path=self.prefix+"/") as session:
            state = session["state"]
        remote.side_effect = [{"access_token":"test"}, {"id":2, "login":"bob", "email":"bob@internal.invalid"}]
        result = self.client.get(self.prefix+"/auth/callback", query_string={"code":"test", "state":state})
        self.assertEqual(result.location, self.prefix+"/?case=42")

    @patch("app.remote")
    def test_external_denied_even_if_admin(self,remote):
        self.client.get(self.prefix+"/auth/login")
        with self.client.session_transaction(path=self.prefix+"/") as s:
            state = s["state"]
        remote.side_effect = [{"access_token":"test"},{"id":1,"login":"vendor","email":"y_guest@internal.invalid","is_admin":True}]
        self.assertEqual(self.client.get(self.prefix+"/auth/callback",query_string=dict(code="code",state=state)).status_code,403)

    @patch("app.remote")
    @patch("app.source_context")
    def test_invalid_folder_stops_before_git_or_opencode(self, source, remote):
        self.mapping()
        regular_file = self.run / "run.log"
        regular_file.touch()
        for path in ("relative", str(self.run / "missing"), str(regular_file)):
            with self.subTest(path=path):
                response = self.api("POST", "cases", data=dict(text="失敗", program="demo", folder=path))
                self.assertEqual(response.status_code, 400)
        self.run.chmod(0o750)
        response = self.api("POST", "cases", data=dict(text="失敗", program="demo", folder=str(self.run)))
        self.assertEqual(response.status_code, 403)
        self.assertIn("chmod 775 " + str(self.run), response.json["error"])
        private = self.run / "private run"
        private.mkdir(mode=0o700)
        with patch("evidence.os.open", side_effect=PermissionError()):
            response = self.api("POST", "cases", data=dict(text="失敗", program="demo", folder=str(private)))
        self.assertEqual(response.status_code, 403)
        self.assertIn("chmod 775 '" + str(private) + "'", response.json["error"])
        self.run.chmod(0o770)
        (self.run / ".opencode-tmp").symlink_to(self.root)
        self.assertEqual(self.api("POST", "cases", data=dict(text="失敗", program="demo", folder=str(self.run))).status_code, 400)
        (self.run / ".opencode-tmp").unlink()
        for operation in ("scandir", "stat"):
            with self.subTest(operation=operation):
                # Simulate denial while listing or traversing, even when tests run as root.
                with patch("evidence.os." + operation, side_effect=PermissionError()):
                    response = self.api("POST", "cases", data=dict(text="失敗", program="demo", folder=str(self.run)))
                self.assertEqual(response.status_code, 403)
                self.assertIn("查案服務帳號", response.json["error"])
        self.assertEqual(self.api("GET", "cases").json, [])
        source.assert_not_called()
        remote.assert_not_called()

    @patch("app.remote")
    @patch("app.source_context", return_value=("revision", "source"))
    def test_selected_skill_permissions(self, source, remote):
        self.mapping()
        catalog = [dict(name="sample", location="/srv/skills/sample/SKILL.md")]
        remote.return_value = catalog
        program = dict(name="demo", repo="samples/demo", branch="main", skills=["sample"])
        self.assertEqual(self.api("PUT", "programs/demo", json=program).status_code, 204)
        self.user()
        form = dict(text="test", program="demo", folder=str(self.run))
        remote.side_effect = [catalog, [dict(name="sample", location="/other/SKILL.md")]]
        self.assertEqual(self.api("POST", "cases", data=form).status_code, 409)
        source.assert_not_called()
        names_only = [dict(name="sample")]
        wrapped = {"location": {"directory": str(self.run)}, "data": catalog}
        remote.side_effect = [names_only, names_only, wrapped, wrapped, {"id": "session"}]
        created = self.api("POST", "cases", data=form)
        self.assertEqual(created.status_code, 201)
        rules = remote.call_args.args[2]["permission"]
        self.assertEqual(rules, [{"permission": "*", "pattern": "*", "action": "deny"}] + [
            {"permission": key, "pattern": pattern, "action": "allow"} for key, pattern in
            [("read", "*"), ("grep", "*"), ("glob", "*"), ("external_directory", str(self.program_file.parent / "*")), ("skill", "sample"),
             ("external_directory", "/srv/skills/sample/*")]])
        remote.side_effect = [{"parts": [{"type": "text", "text": "OK"}]}, {"title": "OK"}]
        self.assertEqual(self.api("POST", "cases/%s/turn" % created.json["id"]).status_code, 204)
        self.assertIn("sample", remote.call_args_list[-2].args[2]["system"])

    @patch("app.remote")
    @patch("app.source_context",return_value=("revision","source"))
    def test_case_with_image_admin_continuation(self,source,remote):
        self.client = create_app(dict(self.env, OPENCODE_MODEL="gateway/vendor/vision")).test_client()
        self.mapping()
        self.user()
        remote.side_effect = [{"id":"session"}, {"parts":[{"type":"text","text":"需要更多資訊"}]},
                              {"title":"輸入資料異常"}, {"parts":[{"type":"text","text":"調查結果"}]}]
        form = dict(text="執行失敗",program="demo",folder=str(self.run),
                    image=(io.BytesIO(b"\x89PNG\r\n\x1a\nabc"),"capture.png"))
        create = self.api("POST","cases",data=form)
        self.assertEqual(create.status_code,201)
        cid = "cases/"+str(create.json["id"])
        self.assertEqual(create.json, self.api("GET", cid).json)
        self.assertEqual(self.api("POST",cid+"/turn").status_code,204)
        self.assertEqual(remote.call_args_list[1].args[2]["agent"], "investigate-web")
        def check_image(call, expected, suffix):
            parts = call.args[2]["parts"]
            self.assertEqual([part["type"] for part in parts], ["text", "text"])
            self.assertNotIn("data:image/", str(parts))
            instruction, filename = parts[1]["text"].split("\n")
            self.assertIn("read", instruction)
            path = Path(filename)
            self.assertEqual(path.parent.name, "attachments")
            self.assertEqual(path.suffix, suffix)
            self.assertEqual(path.read_bytes(), expected)
            return path
        expected_model = {"providerID": "gateway", "modelID": "vendor/vision"}
        self.assertEqual(remote.call_args_list[1].args[2]["model"], expected_model)
        first = check_image(remote.call_args_list[1], b"\x89PNG\r\n\x1a\nabc", ".png")
        self.assertNotIn("title",remote.call_args_list[0].args[2])
        self.assertEqual(self.api("GET","cases").json[0]["session_title"],"輸入資料異常")
        self.assertEqual(self.api("GET",cid).json["session_title"],"輸入資料異常")
        self.user(2)
        self.assertEqual(self.api("GET","cases").json[0]["id"], create.json["id"])
        shared = self.api("GET",cid)
        self.assertEqual(shared.status_code, 200)
        self.assertFalse(shared.json["can_reply"])
        self.assertEqual(len(shared.json["messages"]), 2)
        self.assertEqual(self.api("DELETE", cid).status_code, 403)
        for form in ({}, {"text": "追問"}):
            self.assertEqual(self.api("POST", cid+"/turn", data=form).status_code, 403)
        self.user(2,True)
        self.assertEqual(self.api("PUT", "admin/model", json={"model":"gateway/next/vision"}).status_code, 200)
        jpeg = b"\xff\xd8\xfftest"
        self.assertEqual(self.api("POST",cid+"/turn",data=dict(text="補充", image=(io.BytesIO(jpeg), "capture.jpg"))).status_code,204)
        self.assertEqual(remote.call_args.args[2]["model"], {"providerID":"gateway", "modelID":"next/vision"})
        followup = check_image(remote.call_args, jpeg, ".jpg")
        self.assertEqual(first.parent, followup.parent)
        self.assertTrue(first.is_file())
        self.assertEqual(remote.call_args.args[2]["agent"], "investigate-web")
        messages = self.api("GET",cid).json["messages"]
        self.assertEqual([m["author"] for m in messages],["alice","@agent","bob","@agent"])

    @patch("app.source_context", return_value=("revision", "source"))
    @patch("app.remote")
    def test_stop_active_turn(self, remote, source):
        started, stopped = threading.Event(), threading.Event()
        sent = {}
        history = []
        resume = threading.Event()
        def upstream(operation, url, *args, **kwargs):
            self.assertEqual(parse_qs(urlsplit(url).query)["directory"], [str(self.run)])
            if "/message?" in url and args[0] is None:
                if sent.pop("fail_poll", False):
                    raise ServiceUnavailable()
                return history
            if "/message?" in url:
                payload = args[0]
                self.assertNotIn("model", payload)
                self.assertIsNone(kwargs["timeout"])
                index = len(history)
                info = {"id": "assistant-%d" % index, "parentID": payload["messageID"]}
                part = {"type": "reasoning", "text": ["initial", "checking logs", "corrected question"][index]}
                history.append({"info": info, "parts": [part]})
                if index == 1:
                    started.set()
                    self.assertTrue(stopped.wait(5))
                    return {"info": {"error": {"name": "MessageAbortedError"}}}
                if index == 2:
                    started.set()
                    self.assertTrue(resume.wait(5))
                return {"info": info, "parts": [{"type": "text", "text": "done"}]}
            if "/abort?" in url:
                stopped.set()
                return True
            return {"id": "session", "title": "test"}
        remote.side_effect = upstream
        self.mapping()
        path = "cases/" + str(self.api("POST", "cases", data=dict(
            text="test", program="demo", folder=str(self.run))).json["id"])
        with ThreadPoolExecutor(1) as pool:
            self.assertEqual(self.api("POST", path + "/turn").status_code, 204)
            turn = pool.submit(self.api, "POST", path + "/turn", data={"text": "typo"})
            self.assertTrue(started.wait(5))
            self.assertEqual(self.api("GET", path + "/activity?reasoning=1").json["reasoning"], "checking logs")
            self.assertEqual(self.api("DELETE", path).status_code, 409)
            token = self.api("GET", path + "/activity").json
            self.assertEqual(self.api("POST", path + "/abort", data=dict(turn_id="stale")).status_code, 409)
            self.user(2)
            self.assertEqual(self.api("POST", path + "/abort", data=token).status_code, 403)
            self.user(2, True)
            self.assertEqual(self.api("POST", path + "/abort", data=token).status_code, 204)
            self.assertEqual(turn.result().status_code, 204)
            started.clear()
            turn = pool.submit(self.api, "POST", path + "/turn", data={"text": "corrected"})
            try:
                self.assertTrue(started.wait(5))
                poll = path + "/activity?reasoning=1"
                sent["fail_poll"] = True
                unavailable = self.api("GET", poll).json
                self.assertFalse(unavailable["history_available"])
                self.assertNotEqual(unavailable["turn_id"], token["turn_id"])
                live = self.api("GET", poll).json
                self.assertTrue(live["history_available"])
                self.assertEqual(live["reasoning"], "corrected question")
            finally:
                resume.set()
            self.assertEqual(turn.result().status_code, 204)
        detail = self.api("GET", path).json
        self.assertIsNone(detail["active_turn"])
        self.assertEqual(detail["messages"][-1]["reasoning"], "corrected question")
        self.assertEqual(detail["messages"][-3]["reasoning"], "checking logs")
        self.assertIn("終止", detail["messages"][-3]["body"])

    @patch("app.source_context", return_value=("revision", "source"))
    @patch("app.remote", return_value={"id":"session"})
    def test_delete_case_as_owner_and_admin(self, remote, source):
        self.mapping()
        form = dict(text="test", program="demo", folder=str(self.run))
        ids = []
        for admin in (False, True):
            self.user()
            remote.return_value = {"id": "session"}
            cid = self.api("POST", "cases", data=form).json["id"]
            ids.append(cid)
            if admin:
                self.user(2, True)
            path = "cases/%d" % cid
            remote.return_value = False
            self.assertEqual(self.api("DELETE", path).status_code, 502)
            self.assertEqual(self.api("GET", path).json["messages"][0]["body"], "test")
            remote.return_value = True
            self.assertEqual(self.api("DELETE", path).status_code, 204)
            url = urlsplit(remote.call_args.args[1])
            self.assertTrue(url.path.endswith("/session/session"))
            self.assertEqual(parse_qs(url.query)["directory"], [str(self.run)])
            self.assertEqual(remote.call_args.kwargs["method"], "DELETE")
            self.assertEqual(self.api("GET", path).status_code, 404)
            self.assertEqual(self.api("GET", "cases").json, [])
        self.assertGreater(ids[1], ids[0])
        with sqlite3.connect(self.root/"state"/"cases.sqlite") as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages").fetchone()[0], 0)

    @patch("app.source_context", return_value=("revision", "source"))
    @patch("app.remote", return_value={"id":"session"})
    def test_rename_case_permissions_and_original_program(self, remote, source):
        self.mapping()
        self.user()
        cid = self.api("POST", "cases", data=dict(text="test", program="demo", folder=str(self.run))).json["id"]
        path = "cases/%d" % cid
        names = dict(custom_title="標題", custom_subtitle="副標題")
        self.assertEqual(self.api("PATCH", path, json=names).json, names)
        self.user(2)
        self.assertEqual(self.api("PATCH", path, json=names).status_code, 403)
        item = self.api("GET", "cases").json[0]
        self.assertFalse(item["can_manage"])
        self.assertEqual(item["custom_title"], names["custom_title"])
        detail = self.api("GET", path).json
        self.assertEqual(detail["custom_subtitle"], names["custom_subtitle"])
        self.assertEqual(detail["program"], "demo")
        self.user(2, True)
        result = self.api("PATCH", path, json=dict(custom_title="", custom_subtitle=""))
        self.assertEqual(result.json, dict(custom_title=None, custom_subtitle=None))

    def test_upgrade_preserves_existing_shared_case_ids(self):
        self.mapping()
        with sqlite3.connect(self.root/"state"/"cases.sqlite") as db:
            db.execute("DROP TABLE cases")
            db.execute("""CREATE TABLE cases(id INTEGER PRIMARY KEY,owner INTEGER,author TEXT,
                       program TEXT,folder TEXT,summary TEXT,revision TEXT,sid TEXT,evidence TEXT,
                       workspace TEXT,session_title TEXT)""")
            db.execute("INSERT INTO cases(id,owner,session_title) VALUES(42,1,'原案件')")
        self.client = create_app(self.env).test_client()
        self.user(2)
        result = self.api("GET", "cases/42")
        self.assertEqual(result.json["session_title"], "原案件")
        self.assertFalse(result.json["can_reply"])
        self.user()
        self.assertEqual(self.api("DELETE","cases/42").status_code, 204)
        with sqlite3.connect(self.root/"state"/"cases.sqlite") as db:
            self.assertGreater(db.execute("INSERT INTO cases(owner) VALUES(1)").lastrowid, 42)

    @patch("app.remote")
    def test_legacy_session_keeps_original_directory(self, remote):
        self.user()
        snapshot = self.root / "state" / "snapshot-old"
        snapshot.mkdir()
        with sqlite3.connect(self.root / "state" / "cases.sqlite") as db:
            cid = db.execute("INSERT INTO cases(owner,folder,workspace,sid,evidence) VALUES(?,?,?,?,?)",
                             (1, str(self.run), str(snapshot), "old", "legacy")).lastrowid
            db.execute("INSERT INTO messages(cid,author,body) VALUES(?,?,?)", (cid, "alice", "retry"))
        remote.side_effect = [{"parts": [{"type": "text", "text": "ok"}]}, {"title": "old case"}]
        self.assertEqual(self.api("POST", "cases/%d/turn" % cid).status_code, 204)
        for call in remote.call_args_list:
            self.assertEqual(parse_qs(urlsplit(call.args[1]).query)["directory"], [str(snapshot)])

    def test_clone_pins_each_case_revision(self):
        repo = self.root/"repos"/"samples"/"demo.git"
        repo.mkdir(parents=True)
        def git(*args):
            subprocess.run(["git","-C",str(repo),*args],check=True,capture_output=True)
        git("init","-b","main")
        revisions = []
        for value in ("first","second"):
            (repo/"demo").write_text(value)
            git("add","demo")
            git("-c","user.name=Test","-c","user.email=test@invalid","commit","-m",value)
            (repo/"helper.py").write_text(value)
            git("add","helper.py")
            git("-c","user.name=Test","-c","user.email=test@invalid","commit","-m","Add helper")
            with case_workspace(self.run) as (snap, handle):
                rev, text = source_context(self.root, str(self.root/"repos"),
                                           ("samples/demo","main",""), "demo", handle)
                self.assertEqual(snap.parent, self.run / ".opencode-tmp")
                self.assertTrue((snap/"source"/".git").is_dir())
            self.assertEqual((snap/"source"/"helper.py").read_text(),value)
            self.assertIn(value,text)
            revisions.append(rev)
        self.assertNotEqual(*revisions)

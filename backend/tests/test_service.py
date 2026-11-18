import io
import re
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from evidence import source_context


class ServiceTests(unittest.TestCase):
    prefix = "/tools/cases"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "shared" / "batch-run"
        self.run.mkdir(parents=True)
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

    def mapping(self):
        self.user(admin=True)
        data = dict(name="demo",repo="samples/demo",branch="main",entry="demo.py")
        self.assertEqual(self.api("POST","programs",json=data).status_code,204)

    def test_routes_and_roles(self):
        self.assertEqual(self.client.get(self.prefix).status_code,302)
        with self.client.get(self.prefix + "/") as page:
            self.assertEqual(page.status_code, 200)
            assets = re.findall(r'(?:href|src)="(\./assets/[^"]+)"', page.get_data(as_text=True))
        self.assertTrue({".css", ".js", ".svg"}.issubset({Path(path).suffix for path in assets}))
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
        remote.assert_called_once_with("OpenCode list models", "agent/config/providers", None, {}, timeout=10)
        remote.return_value = {"providers": []}
        self.assertEqual(self.api("GET", "admin/models").json, [])
        remote.return_value = {}
        self.assertEqual(self.api("GET", "admin/models").status_code, 502)

    def test_edit_and_delete_mapping(self):
        self.mapping()
        update = dict(name="renamed",repo="samples/demo",branch="master",entry="main.py")
        self.assertEqual(self.api("PUT","programs/demo",json=update).status_code,204)
        self.assertEqual(self.api("GET","programs").json,[update])
        self.assertEqual(self.api("DELETE","programs/renamed").status_code,204)
        self.assertEqual(self.api("GET","programs").json,[])

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
        self.assertEqual(self.api("GET",cid).status_code,404)
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
        def upstream(operation, url, *args, **kwargs):
            if "/message?" in url and args[0] is None:
                return [{"info": {"id": "old", "parentID": "previous"}, "parts": [{"type": "reasoning", "text": "old"}]},
                        {"info": {"id": "current", "parentID": sent["id"]}, "parts": [{"type": "reasoning", "text": "checking logs"}]}]
            if "/message?" in url:
                sent["id"] = args[0]["messageID"]
                self.assertNotIn("model", args[0])
                self.assertIsNone(kwargs["timeout"])
                started.set()
                self.assertTrue(stopped.wait(5))
                return {"info": {"error": {"name": "MessageAbortedError"}}}
            if "/abort?" in url:
                stopped.set()
                return True
            return {"id": "session", "title": "test"}
        remote.side_effect = upstream
        self.mapping()
        path = "cases/" + str(self.api("POST", "cases", data=dict(
            text="test", program="demo", folder=str(self.run))).json["id"])
        with ThreadPoolExecutor(1) as pool:
            turn = pool.submit(self.api, "POST", path + "/turn")
            self.assertTrue(started.wait(5))
            self.assertEqual(self.api("GET", path + "/activity?reasoning=1").json["reasoning"], "checking logs")
            token = self.api("GET", path + "/activity").json
            self.assertEqual(self.api("POST", path + "/abort", data=dict(turn_id="stale")).status_code, 409)
            self.user(2)
            self.assertEqual(self.api("POST", path + "/abort", data=token).status_code, 404)
            self.user(2, True)
            self.assertEqual(self.api("POST", path + "/abort", data=token).status_code, 204)
            self.assertEqual(turn.result().status_code, 204)
        detail = self.api("GET", path).json
        self.assertIsNone(detail["active_turn"])
        self.assertEqual(detail["messages"][-1]["reasoning"], "checking logs")
        self.assertIn("終止", detail["messages"][-1]["body"])

    def test_refresh_repository_without_checkout(self):
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
            snap = self.root / ("snapshot-" + value)
            (repo/"helper.py").write_text(value)
            git("add","helper.py")
            git("-c","user.name=Test","-c","user.email=test@invalid","commit","-m","Add helper")
            rev, text = source_context(self.root/"cache",str(self.root/"repos"),("samples/demo","main",""),"demo",snap)
            self.assertEqual((snap/"source"/"helper.py.txt").read_text(),value)
            self.assertIn(value,text)
            revisions.append(rev)
        self.assertNotEqual(*revisions)

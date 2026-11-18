import io
import subprocess
import tempfile
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
        self.run = self.root / "runs" / "alice"
        self.run.mkdir(parents=True)
        env = dict(CASE_STATE=str(self.root/"state"), CASE_PREFIX=self.prefix,
                   GITEA_BASE="gitea", PUBLIC_ORIGIN="public", GITEA_CLIENT_ID="id",
                   GITEA_CLIENT_SECRET="test", INTERNAL_DOMAINS="internal.invalid",
                   SESSION_SECRET="test-only", RUN_ROOTS=str(self.run.parent/"{login}"), OPENCODE_BASE="agent")
        self.client = create_app(env).test_client()

    def user(self, uid=1, admin=False):
        with self.client.session_transaction(path=self.prefix+"/") as s:
            s["user"] = dict(id=uid, login="alice" if uid==1 else "bob", admin=admin)

    def api(self, method, path, **kw):
        return self.client.open(self.prefix+"/api/"+path, method=method, headers={"X-Case-Request":"1"}, **kw)

    def mapping(self):
        self.user(admin=True)
        data = dict(name="demo",repo="samples/demo",branch="main",entry="demo.py")
        self.assertEqual(self.api("POST","programs",json=data).status_code,204)

    def test_routes_and_roles(self):
        self.assertEqual(self.client.get(self.prefix).status_code,302)
        for p in ("/","/assets/app.js","/assets/style.css"):
            with self.client.get(self.prefix+p) as response:
                self.assertEqual(response.status_code,200)
        self.assertEqual(self.api("GET","cases").status_code,401)
        self.mapping()
        self.user()
        self.assertEqual(self.api("GET","programs").json,[{"name":"demo"}])
        self.assertEqual(self.api("DELETE","programs/demo").status_code,403)
        self.assertEqual(self.client.post(self.prefix+"/api/logout").status_code,403)

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
    def test_unreadable_or_outside_root_precedes_session(self,remote):
        self.mapping()
        for path in (str(self.root),str(self.run/"missing")):
            self.assertEqual(self.api("POST","cases",data=dict(text="失敗",program="demo",folder=path)).status_code,400)
        remote.assert_not_called()

    @patch("app.remote")
    @patch("app.source_context",return_value=("revision","source"))
    def test_case_with_image_admin_continuation(self,source,remote):
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
        self.assertEqual(remote.call_args_list[1].args[1]["parts"][1]["mime"],"image/png")
        self.assertNotIn("title",remote.call_args_list[0].args[1])
        self.assertEqual(self.api("GET","cases").json[0]["session_title"],"輸入資料異常")
        self.assertEqual(self.api("GET",cid).json["session_title"],"輸入資料異常")
        self.user(2)
        self.assertEqual(self.api("GET",cid).status_code,404)
        self.user(2,True)
        self.assertEqual(self.api("POST",cid+"/turn",data=dict(text="補充")).status_code,204)
        messages = self.api("GET",cid).json["messages"]
        self.assertEqual([m["author"] for m in messages],["alice","@agent","bob","@agent"])

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

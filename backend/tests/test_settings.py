import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from app import create_app
from settings import load_settings


class DeploymentSettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "config.yml"
        example = Path(__file__).resolve().parents[2] / "config.example.yml"
        self.config = yaml.safe_load(example.read_text(encoding="utf-8"))
        self.config["server"].update(state_dir=str(self.root / "state"), public_origin="http://test.invalid")
        self.config["gitea"].update(client_id="test", client_secret="test")
        self.write_config()

    def write_config(self):
        self.path.write_text(yaml.safe_dump(self.config), encoding="utf-8")

    def login(self, client, email, login):
        client.get("/case-investigator/auth/login")
        with client.session_transaction(path="/case-investigator/") as session:
            state = session["state"]
        profile = {"id": 1, "login": login, "email": email, "is_admin": True}
        with patch("app.remote", side_effect=[{"access_token": "test"}, profile]):
            return client.get("/case-investigator/auth/callback", query_string={"code": "test", "state": state})

    def test_lsf_email_policy_and_admin(self):
        client = create_app(load_settings(self.path)).test_client()
        for email in ["Y_member@EXAMPLE.INVALID", "member@sub.example.invalid", "a@b@example.invalid", None]:
            with self.subTest(email=email):
                self.assertEqual(self.login(client, email, "admin").status_code, 403)
        self.assertEqual(self.login(client, "Member@EXAMPLE.INVALID", "y_login").status_code, 302)
        self.assertTrue(client.get("/case-investigator/api/me").json["admin"])

    def test_cookie_survives_restart_and_new_policy_is_enforced(self):
        app = create_app(load_settings(self.path))
        client = app.test_client()
        self.login(client, "alice@example.invalid", "alice")
        cookie = client.get_cookie("case_session", path="/case-investigator").value
        for excluded, status in [(["y_"], 200), (["al"], 401)]:
            self.config["access_policy"]["excluded_account_prefixes"] = excluded
            self.write_config()
            restarted = create_app(load_settings(self.path))
            self.assertEqual(app.secret_key, restarted.secret_key)
            other = restarted.test_client()
            other.set_cookie("case_session", cookie, path="/case-investigator")
            self.assertEqual(other.get("/case-investigator/api/me").status_code, status)

    def test_explicit_file_never_falls_back_to_environment(self):
        with patch.dict(os.environ, {"CASE_STATE": "unused"}):
            with self.assertRaisesRegex(ValueError, "not found"):
                load_settings(self.root / "missing.yml")
            self.assertEqual(load_settings(self.path)["CASE_STATE"], self.config["server"]["state_dir"])

    def test_invalid_or_misspelled_exclusions_fail_closed(self):
        policy = self.config["access_policy"]
        for bad in ["y_", [""], [None]]:
            policy["excluded_account_prefixes"] = bad
            self.write_config()
            with self.assertRaisesRegex(ValueError, "excluded_account_prefixes"):
                load_settings(self.path)
        policy["excluded_prefix"] = policy.pop("excluded_account_prefixes")
        self.write_config()
        with self.assertRaisesRegex(ValueError, "Unknown access_policy"):
            load_settings(self.path)

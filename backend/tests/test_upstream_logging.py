import errno
import io
import socket
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from werkzeug.exceptions import ServiceUnavailable
from app import remote


class UpstreamLoggingTests(unittest.TestCase):
    def test_retry_session_delete_after_remote_removal(self):
        url = "http://example.invalid/session/ses_123"
        for body in (b'{"name":"NotFoundError"}', b"unknown route"):
            with self.subTest(body=body), patch("app.urlopen", side_effect=HTTPError(
                    url, 404, "Not Found", {}, io.BytesIO(body))) as send:
                if body.startswith(b"{"):
                    self.assertIs(remote("OpenCode delete session", url, method="DELETE"), True)
                else:
                    with self.assertRaises(ServiceUnavailable):
                        remote("OpenCode delete session", url, method="DELETE")
                self.assertEqual(send.call_args.args[0].get_method(), "DELETE")

    def test_failure_reason_without_credentials_or_request_data(self):
        url = "http://example.invalid:4096/session?directory=private-folder"
        failures = [
            (HTTPError(url, 401, "private-response", {}, io.BytesIO()), "HTTP 401 Unauthorized"),
            (URLError(ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")), "Connection refused"),
            (socket.timeout(), "timeout (limit=150s)"),
            (None, "invalid JSON"),
        ]
        for exc, expected in failures:
            with self.subTest(reason=expected), patch("app.urlopen", side_effect=exc,
                    return_value=io.BytesIO(b"private-response")), self.assertLogs("app", "ERROR") as logs:
                with self.assertRaises(ServiceUnavailable):
                    remote("OpenCode create session", url, {"message": "private-message"},
                           {"Authorization": "private-header"})
                output = logs.output[0]
                self.assertIn("OpenCode create session failed host=example.invalid:4096", output)
                self.assertIn(expected, output)
                self.assertNotIn("private-", output)

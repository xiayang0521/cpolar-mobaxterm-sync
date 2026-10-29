import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import cpolar_client as cc


class _StubResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    @property
    def text(self):
        return self._body

    def json(self):
        return json.loads(self._body)


class _FakeSession:
    """按 URL 后缀返回预设响应，并记录 POST 数据。"""

    def __init__(self, timeout=5):
        self.get_responses = {}
        self.post_responses = {}
        self.posted = {}

    def get(self, url, headers=None):
        for suffix, resp in self.get_responses.items():
            if url.endswith(suffix):
                return resp
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, data=None, json_body=None, headers=None):
        self.posted[url] = data or json_body
        for suffix, resp in self.post_responses.items():
            if url.endswith(suffix):
                return resp
        return _StubResp(200, "{}")


def _dashboard_login_html():
    return (
        '<form action="/login" method="POST">'
        '<input type="hidden" name="csrf_token" value="TOKEN123">'
        '<input name="login"><input name="password"></form>'
    )


def _dashboard_status_html():
    # 真实结构的精简版：website(http) + ssh(tcp)，ssh 行 href 是假锚点
    return """
    <h3 class="first">在线隧道</h3>
    <table class="table table-sm"><thead><tr>
      <th>隧道名称</th><th>URL</th><th>地区</th><th>本地地址</th><th>创建时间</th>
    </tr></thead><tbody>
      <tr><td>website</td>
          <th scope="row"><a href="http://7f346ef9.r21.cpolar.top/">http://7f346ef9.r21.cpolar.top</a></th>
          <td>cn_top</td><td>http://localhost:8080</td><td>t1</td></tr>
      <tr><td>ssh</td>
          <th scope="row"><a href="https://dashboard.cpolar.com/status#ZgotmplZ">tcp://example-tcp.cpolar.top:12345</a></th>
          <td>cn</td><td>tcp://127.0.0.1:22</td><td>t2</td></tr>
    </tbody></table>"""


class TestLocalApi(unittest.TestCase):
    def test_get_ssh_tunnel_parses_public_url(self):
        fake = _FakeSession()
        fake.post_responses = {
            "/api/v1/user/login": _StubResp(200, json.dumps({"token": "T"}))
        }
        fake.get_responses = {
            "/api/v1/tunnels": _StubResp(
                200,
                json.dumps(
                    {"tunnels": [
                        {"name": "ssh", "proto": "tcp", "public_url": "tcp://new-tcp.cpolar.top:23456"}
                    ]}
                ),
            )
        }
        with mock.patch("cpolar_client._Session", return_value=fake):
            client = cc.CpolarClient("http://x", "e@e.com", "pw", source="local")
            host, port = client.get_ssh_tunnel("ssh")
        self.assertEqual(host, "new-tcp.cpolar.top")
        self.assertEqual(port, "23456")

    def test_get_ssh_tunnel_not_found(self):
        fake = _FakeSession()
        fake.post_responses = {
            "/api/v1/user/login": _StubResp(200, json.dumps({"token": "T"}))
        }
        fake.get_responses = {
            "/api/v1/tunnels": _StubResp(200, json.dumps({"tunnels": []}))
        }
        with mock.patch("cpolar_client._Session", return_value=fake):
            client = cc.CpolarClient("http://x", "e@e.com", "pw", source="local")
            with self.assertRaises(cc.CpolarError):
                client.get_ssh_tunnel("ssh")


class TestDashboard(unittest.TestCase):
    def test_login_posts_csrf_and_login_field(self):
        login_page = _StubResp(200, _dashboard_login_html())
        status_page = _StubResp(200, _dashboard_status_html())
        fake = _FakeSession()
        fake.get_responses = {"/status": status_page, "/login": login_page}
        fake.post_responses = {"/login": _StubResp(200, _dashboard_status_html())}
        with mock.patch("cpolar_client._Session", return_value=fake):
            client = cc.CpolarClient(
                "https://dashboard.cpolar.com", "e@e.com", "pw", source="dashboard"
            )
            tunnels = client._fetch_tunnels_dashboard()
        posted = fake.posted.get("https://dashboard.cpolar.com/login")
        self.assertIsNotNone(posted)
        # 必须用表单字段 login（而非 email）且带上 csrf_token
        self.assertEqual(posted.get("login"), "e@e.com")
        self.assertEqual(posted.get("csrf_token"), "TOKEN123")
        ssh = [t for t in tunnels if t.get("name") == "ssh"]
        self.assertTrue(ssh, "未解析出 ssh 隧道")
        self.assertEqual(ssh[0]["public_url"], "tcp://example-tcp.cpolar.top:12345")

    def test_parse_html_uses_anchor_text_not_href(self):
        # href 是假锚点，必须用 <a> 文本
        tunnels = cc.CpolarClient._parse_dashboard_html(_dashboard_status_html())
        ssh = [t for t in tunnels if t.get("name") == "ssh"][0]
        self.assertEqual(ssh["public_url"], "tcp://example-tcp.cpolar.top:12345")

    def test_parse_html_fallback_regex(self):
        # 无表格时退化为全文正则
        html = '<div>tcp://fallback-tcp.cpolar.top:30000</div>'
        tunnels = cc.CpolarClient._parse_dashboard_html(html)
        self.assertTrue(any(t["public_url"] == "tcp://fallback-tcp.cpolar.top:30000" for t in tunnels))

    def test_extract_csrf(self):
        self.assertEqual(cc._extract_csrf(_dashboard_login_html()), "TOKEN123")


if __name__ == "__main__":
    unittest.main()

"""cpolar 读取客户端（仅依赖标准库）。

读取源由构造参数 source 决定：
- "dashboard"（默认）：登录 https://dashboard.cpolar.com，解析「在线隧道」表格。
- "local"：访问本机 cpolar API（http://127.0.0.1:9200/api/v1/...），返回 JSON。

两者最终都归一化为 tunnels 列表，每条含 name/proto/public_url，
由 get_ssh_tunnel 提取 SSH 隧道的 (host, port)。
"""

import html.parser
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar


DASHBOARD_BASE = "https://dashboard.cpolar.com"


class CpolarError(Exception):
    """cpolar 读取相关错误。"""


# ---------------------------------------------------------------------------
# 极简 HTTP 会话：用标准库 cookiejar 自动维持登录态
# ---------------------------------------------------------------------------

class _Session:
    def __init__(self, timeout=5):
        self.timeout = timeout
        cj = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cj)
        )

    def get(self, url, headers=None):
        req = urllib.request.Request(url, headers=headers or {})
        try:
            resp = self._opener.open(req, timeout=self.timeout)
        except urllib.error.URLError as e:
            raise CpolarError(f"网络请求失败：{e}")
        return _UrllibResp(resp)

    def post(self, url, data=None, json_body=None, headers=None):
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            h = {"Content-Type": "application/json", **(headers or {})}
        else:
            body = urllib.parse.urlencode(data or {}).encode("utf-8")
            h = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
        req = urllib.request.Request(url, data=body, headers=h)
        try:
            resp = self._opener.open(req, timeout=self.timeout)
        except urllib.error.URLError as e:
            raise CpolarError(f"网络请求失败：{e}")
        return _UrllibResp(resp)


class _UrllibResp:
    """把 urllib 响应包装成 requests 风格（status_code / text / json）。"""

    def __init__(self, resp):
        self.status_code = resp.getcode()
        self._body = resp.read().decode("utf-8", errors="replace")

    @property
    def text(self):
        return self._body

    def json(self):
        return json.loads(self._body)


# ---------------------------------------------------------------------------
# 云控制台表格解析（html.parser，纯标准库）
# ---------------------------------------------------------------------------

class _TableParser(html.parser.HTMLParser):
    """解析 <table class="...table-sm">，按行收集每个 cell 的文本与 <a> 文本。"""

    def __init__(self):
        super().__init__()
        self.in_target_table = False
        self.in_row = False
        self.row_cells = []
        self.capture = False
        self.cur_text = []
        self.cur_a = None  # 非 None 表示正在 <a> 内
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == "table" and not self.in_target_table:
            cls = attr_dict.get("class", "")
            if "table-sm" in cls.split():
                self.in_target_table = True
        if not self.in_target_table:
            return
        if tag == "tr":
            self.in_row = True
            self.row_cells = []
        if tag in ("td", "th"):
            self.capture = True
            self.cur_text = []
            self.cur_a = None
        if tag == "a" and self.capture:
            self.cur_a = ""  # 进入 <a>，开始记录其文本

    def handle_endtag(self, tag):
        if not self.in_target_table:
            return
        if tag in ("td", "th"):
            text = "".join(self.cur_text).strip()
            a_text = self.cur_a.strip() if self.cur_a is not None else None
            self.row_cells.append({"text": text, "a": a_text})
            self.capture = False
        elif tag == "tr":
            if self.row_cells:
                self.rows.append(self.row_cells)
            self.in_row = False
        elif tag == "table":
            self.in_target_table = False

    def handle_data(self, data):
        if self.capture:
            self.cur_text.append(data)
            if self.cur_a is not None:
                self.cur_a += data


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------

class CpolarClient:
    def __init__(self, base_url, email, password, timeout=5, source="local"):
        self.base_url = (base_url or "http://127.0.0.1:9200").rstrip("/")
        self.email = email
        self.password = password
        self.timeout = timeout
        self.source = source  # "local" 或 "dashboard"
        self._token = None

    # ---- 本地 API（本机 cpolar） ----
    def login(self):
        try:
            resp = self._session_post(
                f"{self.base_url}/api/v1/user/login",
                json_body={"email": self.email, "password": self.password},
            )
        except CpolarError:
            raise
        if resp.status_code != 200:
            raise CpolarError(f"cpolar 登录失败：HTTP {resp.status_code}")
        data = resp.json()
        token = data.get("token") or (data.get("data") or {}).get("token")
        if not token:
            raise CpolarError("cpolar 登录响应中未找到 token")
        self._token = token
        return token

    def get_ssh_tunnel(self, tunnel_name="ssh"):
        tunnels = (
            self._fetch_tunnels_dashboard()
            if self.source == "dashboard"
            else self._fetch_tunnels_local()
        )
        for t in tunnels:
            is_target = (t.get("name") == tunnel_name) or (
                t.get("proto") == "tcp" and ":22" in str(t.get("local_addr", ""))
            )
            if not is_target:
                continue
            public_url = t.get("public_url", "")
            if not public_url.startswith("tcp://"):
                continue
            host_port = public_url[len("tcp://"):]
            host, _, port = host_port.rpartition(":")
            if host and port:
                return host, port
        raise CpolarError(f"未找到 SSH 隧道（名称='{tunnel_name}'）")

    def _fetch_tunnels_local(self):
        if not self._token:
            self.login()
        headers = {"Authorization": f"Bearer {self._token}"}
        resp = self._session_get(
            f"{self.base_url}/api/v1/tunnels", headers=headers
        )
        if resp.status_code != 200:
            raise CpolarError(f"获取隧道失败：HTTP {resp.status_code}")
        return resp.json().get("tunnels", [])

    # ---- 云控制台（默认，cpolar 在远程服务器） ----
    def _login_dashboard(self, session: _Session):
        try:
            r = session.get(f"{DASHBOARD_BASE}/login")
        except CpolarError:
            raise
        if r.status_code != 200:
            raise CpolarError(f"访问登录页失败：HTTP {r.status_code}")
        csrf = _extract_csrf(r.text)
        if not csrf:
            raise CpolarError("登录页未找到 csrf_token")
        try:
            session.post(
                f"{DASHBOARD_BASE}/login",
                # 注意：云控制台表单字段名是 login（不是 email）
                data={"login": self.email, "password": self.password, "csrf_token": csrf},
            )
        except CpolarError:
            raise
        if not self._dashboard_logged_in(session):
            raise CpolarError(
                "cpolar 云控制台登录失败（账号/密码错误，或触发了验证码）"
            )

    def _dashboard_logged_in(self, session: _Session):
        try:
            r = session.get(f"{DASHBOARD_BASE}/status")
        except CpolarError:
            return False
        if r.status_code != 200:
            return False
        return ("在线隧道" in r.text) or ('class="table table-sm"' in r.text)

    def _fetch_tunnels_dashboard(self):
        session = _Session(self.timeout)
        self._login_dashboard(session)
        r = session.get(f"{DASHBOARD_BASE}/status")
        if r.status_code != 200:
            raise CpolarError(f"获取在线隧道失败：HTTP {r.status_code}")
        return self._parse_dashboard_html(r.text)

    @staticmethod
    def _parse_dashboard_html(html):
        parser = _TableParser()
        parser.feed(html)
        tunnels = []
        for cells in parser.rows:
            if len(cells) < 4:
                continue
            name = cells[0]["text"]
            url_cell = cells[1]
            # 关键：优先用 <a> 文本（ssh 行的 href 是假锚点 status#ZgotmplZ）
            url_text = url_cell["a"] if url_cell["a"] is not None else url_cell["text"]
            local = cells[3]["text"]
            is_ssh = (name.lower() == "ssh") or local.endswith(":22") or (":22" in local)
            if not is_ssh:
                continue
            if url_text.startswith("tcp://"):
                tunnels.append({"name": name, "proto": "tcp", "public_url": url_text})
        if tunnels:
            return tunnels
        # 退化兜底：全文正则抽 tcp://host:port（无法区分隧道，仅最后手段）
        return [
            {"name": "ssh", "proto": "tcp", "public_url": f"tcp://{host}:{port}"}
            for host, port in re.findall(r"tcp://([\w.\-]+):(\d+)", html)
        ]

    # ---- 统一 HTTP 入口（便于测试 mock） ----
    def _session_get(self, url, headers=None):
        return _Session(self.timeout).get(url, headers=headers)

    def _session_post(self, url, data=None, json_body=None, headers=None):
        return _Session(self.timeout).post(
            url, data=data, json_body=json_body, headers=headers
        )


def _extract_csrf(html):
    """从登录页 HTML 提取 csrf_token 隐藏域的值。"""
    m = re.search(
        r'<input[^>]*name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']',
        html,
    )
    if m:
        return m.group(1)
    # 顺序相反：value 在前
    m = re.search(
        r'<input[^>]*value=["\']([^"\']+)["\'][^>]*name=["\']csrf_token["\']',
        html,
    )
    return m.group(1) if m else None

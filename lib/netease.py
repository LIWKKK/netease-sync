"""网易云 API 客户端：只用公开 REST 接口 + Cookie，不需要 weapi 加密。"""
import json, os, time, urllib.request, urllib.parse

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
API = "https://music.163.com"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COOKIE_FILE = os.path.join(HERE, "state", "cookies.json")


class ApiError(Exception):
    pass


class Client:
    def __init__(self, cookie_file=COOKIE_FILE):
        self.cookie_file = cookie_file
        self.cookies = {}
        self.uid = None
        self.nickname = None
        self._load()

    # ---------- cookie ----------
    def _load(self):
        if not os.path.exists(self.cookie_file):
            return
        with open(self.cookie_file, encoding="utf-8") as f:
            d = json.load(f)
        for c in d.get("cookies", []):
            self.cookies[c["name"]] = c["value"]
        self.uid = d.get("uid")
        self.nickname = d.get("nickname")

    def save(self):
        os.makedirs(os.path.dirname(self.cookie_file), exist_ok=True)
        tmp = self.cookie_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"cookies": [{"name": k, "value": v} for k, v in self.cookies.items()],
                       "uid": self.uid, "nickname": self.nickname,
                       "saved": int(time.time())}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.cookie_file)
        os.chmod(self.cookie_file, 0o600)

    def set_cookie_list(self, arr):
        for c in arr or []:
            n, v = c.get("name"), c.get("value")
            if n and v is not None:
                self.cookies[n] = v

    def cookie_header(self):
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    # ---------- transport ----------
    def _request(self, path, params=None, data=None, timeout=25):
        url = API + path
        body = None
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        if data is not None:
            body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(url, data=body, method="POST" if body else "GET")
        req.add_header("User-Agent", UA)
        req.add_header("Referer", "https://music.163.com/")
        req.add_header("Accept", "*/*")
        if body:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        if self.cookies:
            req.add_header("Cookie", self.cookie_header())
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    raw = r.read()
                break
            except Exception as e:
                if attempt == 2:
                    raise ApiError(f"{path}: {e}")
                time.sleep(1.5 * (attempt + 1))
        if not raw:
            raise ApiError(f"{path}: 空响应")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ApiError(f"{path}: 非 JSON 响应 {raw[:80]!r}")

    # ---------- account ----------
    def account(self):
        d = self._request("/api/nuser/account/get")
        prof = d.get("profile") or {}
        if prof.get("userId"):
            self.uid = prof["userId"]
            self.nickname = prof.get("nickname")
        return d

    def logged_in(self):
        d = self.account()
        return bool((d.get("profile") or {}).get("userId"))

    def user_playlists(self, uid=None):
        """创建 + 收藏的歌单，全部翻页取完。"""
        uid = uid or self.uid
        if not uid:
            raise ApiError("未登录，取不到账号信息")
        out, offset = [], 0
        while True:
            d = self._request("/api/user/playlist", {"uid": uid, "limit": 1000,
                                                     "offset": offset, "includeVideo": "true"})
            batch = d.get("playlist") or []
            out.extend(batch)
            if not d.get("more") or not batch:
                break
            offset += len(batch)
        return out

    # ---------- playlist ----------
    def playlist_trackids(self, pid):
        """返回 (playlist_meta, trackIds[*含 at 添加时间])。"""
        d = self._request("/api/v6/playlist/detail", {"id": pid, "n": 1000, "s": 0})
        pl = d.get("playlist") or {}
        if not pl:
            raise ApiError(f"歌单 {pid} 读取失败: {d.get('code')}")
        total = pl.get("trackCount") or len(pl.get("trackIds") or [])
        ids = list(pl.get("trackIds") or [])
        if total > len(ids):
            for off in range(len(ids), total, 500):
                d2 = self._request("/api/v6/playlist/detail",
                                   {"id": pid, "n": 500, "s": off})
                more = (d2.get("playlist") or {}).get("trackIds") or []
                if not more:
                    break
                ids.extend(more)
        # 去重保序
        seen, uniq = set(), []
        for t in ids:
            tid = t.get("id") if isinstance(t, dict) else t
            if tid and tid not in seen:
                seen.add(tid)
                uniq.append(t if isinstance(t, dict) else {"id": tid, "at": 0})
        return pl, uniq

    def songs(self, ids, chunk=100):
        """批量歌曲详情。缺失的再单条补一次。"""
        got = {}
        ids = list(dict.fromkeys(ids))
        for i in range(0, len(ids), chunk):
            d = self._request("/api/song/detail", data={"ids": json.dumps(ids[i:i + chunk])})
            for s in d.get("songs") or []:
                got[s["id"]] = s
        missing = [i for i in ids if i not in got]
        for tid in missing:
            try:
                d = self._request("/api/v3/song/detail", data={"c": json.dumps([{"id": tid}])})
                for s in d.get("songs") or []:
                    got[s["id"]] = s
            except ApiError:
                pass
        return got

    def song_url(self, sid, br=320000):
        d = self._request("/api/song/enhance/player/url",
                          {"ids": f"[{sid}]", "br": br})
        item = (d.get("data") or [{}])[0]
        return item.get("url"), item.get("code"), item.get("fee"), item.get("br")

    def lyric(self, sid):
        try:
            d = self._request("/api/song/lyric", {"id": sid, "lv": 1, "kv": 1, "tv": -1})
            return (d.get("lrc") or {}).get("lyric")
        except ApiError:
            return None

    def stream(self, url, dest, timeout=120):
        """流式下载到 dest，先写 .part 再原子改名。"""
        part = dest + ".part"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", UA)
        req.add_header("Referer", "https://music.163.com/")
        if self.cookies:
            req.add_header("Cookie", self.cookie_header())
        size = 0
        with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:
            while True:
                buf = r.read(1 << 16)
                if not buf:
                    break
                f.write(buf)
                size += len(buf)
        if size < 200 * 1024:
            os.unlink(part)
            raise ApiError(f"下载内容过小 ({size} 字节)，疑似试听片段或无版权")
        os.replace(part, dest)
        return size


def load_level_br(level):
    return {"standard": 128000, "higher": 192000, "exhigh": 320000,
            "lossless": 999000, "hires": 1999000}.get(level, 320000)

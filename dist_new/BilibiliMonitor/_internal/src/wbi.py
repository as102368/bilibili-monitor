import hashlib
import time
import urllib.parse

import requests

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def _get_mixin_key(orig: str) -> str:
    return "".join([orig[i] for i in MIXIN_KEY_ENC_TAB])[:32]


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


class WBI:
    def __init__(self, sessdata: str):
        self.sessdata = sessdata
        self._img_key = None
        self._sub_key = None
        self._refresh_keys()

    def _refresh_keys(self):
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com",
        }
        cookies = {"SESSDATA": urllib.parse.unquote(self.sessdata)} if self.sessdata else {}
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=headers,
            cookies=cookies,
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0 and data.get("code") != -101:
            raise RuntimeError(f"获取WBI密钥失败: {data}")
        wbi_img = data["data"]["wbi_img"]
        self._img_key = wbi_img["img_url"].rsplit("/", 1)[-1].split(".")[0]
        self._sub_key = wbi_img["sub_url"].rsplit("/", 1)[-1].split(".")[0]

    def sign(self, params: dict) -> dict:
        mixin_key = _get_mixin_key(self._img_key + self._sub_key)
        params["wts"] = int(time.time())
        # 过滤空值
        filtered = {k: v for k, v in params.items() if v not in ("", None)}
        # 仿照 DownKyi WbiSign.EncWbi：
        # 1. 对 value 过滤掉 !'()* 字符
        # 2. 使用标准 URL 编码（空格 -> %20）
        cleaned = {}
        for k, v in sorted(filtered.items()):
            val = str(v).translate(str.maketrans("", "", "!'()*"))
            cleaned[k] = val
        query = urllib.parse.urlencode(cleaned, safe="", quote_via=urllib.parse.quote)
        w_rid = _md5(query + mixin_key)
        params["w_rid"] = w_rid
        return params

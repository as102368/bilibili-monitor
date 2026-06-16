import time
import urllib.parse
from typing import Optional, Dict, Any

import requests


class BilibiliWebClient:
    """
    仿照 DownKyi WebClient 的统一 HTTP 客户端，集成风控规避策略：
    - 自动获取 buvid3/buvid4 设备指纹
    - Session 连接复用
    - 统一 Headers（Origin、Referer、Accept-Language 等）
    - 请求失败自动重试
    """

    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
    }

    def __init__(
        self,
        sessdata: str,
        bili_jct: str,
        buvid3: str = "",
        dedeuserid: str = "",
    ):
        self.sessdata = sessdata
        self.bili_jct = bili_jct
        self._buvid3 = buvid3
        self._buvid4 = ""
        self.dedeuserid = dedeuserid

        # 使用 Session 复用 TCP 连接（仿照 SocketsHttpHandler 连接池）
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)

        # 若未提供 buvid3，自动从 B 站获取设备指纹
        if not self._buvid3:
            self._refresh_buvid()

    def _refresh_buvid(self) -> None:
        """仿照 DownKyi GetBuvid() 自动获取 buvid3 / buvid4"""
        try:
            resp = self.session.get(
                "https://api.bilibili.com/x/frontend/finger/spi",
                timeout=10,
            )
            data = resp.json()
            if data.get("code") == 0:
                self._buvid3 = data["data"].get("b_3", "")
                self._buvid4 = data["data"].get("b_4", "")
        except Exception as e:
            print(f"[WebClient] 获取 buvid 失败: {e}")

    def _build_cookies(self) -> Dict[str, str]:
        """构建请求所需的 Cookie 字典（仿照 DownKyi LoginHelper）"""
        cookies: Dict[str, str] = {
            "SESSDATA": self.sessdata,
            "bili_jct": self.bili_jct,
        }
        if self._buvid3:
            cookies["buvid3"] = urllib.parse.quote(self._buvid3, safe="")
        if self._buvid4:
            cookies["buvid4"] = urllib.parse.quote(self._buvid4, safe="")
        if self.dedeuserid:
            cookies["DedeUserID"] = self.dedeuserid
        return cookies

    def request(
        self,
        url: str,
        referer: Optional[str] = None,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        retry: int = 2,
        timeout: int = 15,
    ) -> dict:
        """
        统一请求入口，仿照 DownKyi WebClient.RequestWeb：
        - 自动附加 Cookie / Origin
        - 失败重试
        """
        if retry <= 0:
            raise RuntimeError(f"请求重试次数耗尽: {url}")

        # 非登录接口且 buvid 为空时，尝试刷新
        if not self._buvid3 and "getLogin" not in url:
            self._refresh_buvid()

        headers: Dict[str, str] = {}
        if referer:
            headers["Referer"] = referer
        if "getLogin" not in url:
            headers["Origin"] = "https://www.bilibili.com"

        cookies = self._build_cookies()

        try:
            if method.upper() == "GET":
                resp = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    cookies=cookies,
                    timeout=timeout,
                )
            else:
                resp = self.session.post(
                    url,
                    params=params,
                    json=json_data,
                    headers=headers,
                    cookies=cookies,
                    timeout=timeout,
                )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            print(f"[WebClient] 请求异常，剩余重试 {retry - 1}: {e}")
            time.sleep(0.5)
            return self.request(url, referer, method, params, json_data, retry - 1, timeout)
        except RuntimeError:
            raise
        except Exception as e:
            print(f"[WebClient] 其他异常，剩余重试 {retry - 1}: {e}")
            time.sleep(0.5)
            return self.request(url, referer, method, params, json_data, retry - 1, timeout)

    def get_cookies_dict(self) -> Dict[str, str]:
        """供外部组件（如 yt-dlp）获取当前 Cookie 字典"""
        return self._build_cookies()

    def get_cookie_string(self) -> str:
        """供外部组件获取 Cookie 字符串"""
        return "; ".join(f"{k}={v}" for k, v in self._build_cookies().items())

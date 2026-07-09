import time
import urllib.parse
from typing import Optional, Dict, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .logger import get_logger

logger = get_logger(__name__)


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

        # 为 Session 挂载重试适配器，缓解 SSL EOF / 连接重置等瞬时网络问题
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=20)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

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
            logger.warning(f"[WebClient] 获取 buvid 失败: {e}")

    def _build_cookies(self) -> Dict[str, str]:
        """构建请求所需的 Cookie 字典（仿照 DownKyi LoginHelper）"""
        cookies: Dict[str, str] = {
            "SESSDATA": urllib.parse.unquote(self.sessdata),
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
            logger.warning(f"[WebClient] 请求异常，剩余重试 {retry - 1}: {e}")
            time.sleep(0.5)
            return self.request(url, referer, method, params, json_data, retry - 1, timeout)
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"[WebClient] 其他异常，剩余重试 {retry - 1}: {e}")
            time.sleep(0.5)
            return self.request(url, referer, method, params, json_data, retry - 1, timeout)

    def get_cookies_dict(self) -> Dict[str, str]:
        """供外部组件（如 yt-dlp）获取当前 Cookie 字典"""
        return self._build_cookies()

    def get_cookie_string(self) -> str:
        """供外部组件获取 Cookie 字符串"""
        return "; ".join(f"{k}={v}" for k, v in self._build_cookies().items())

    def _get_default_fav_folder_id(self) -> Optional[int]:
        """获取用户默认收藏夹 media_id，首次调用后缓存。"""
        if getattr(self, "_default_fav_media_id", None):
            return self._default_fav_media_id
        mid = self.dedeuserid
        if not mid:
            return None
        try:
            resp = self.request(
                "https://api.bilibili.com/x/v3/fav/folder/created/list-all",
                params={"up_mid": mid},
            )
            if resp.get("code") == 0:
                folders = resp.get("data", {}).get("list", [])
                if folders:
                    # 默认收藏夹通常是第一个
                    self._default_fav_media_id = folders[0].get("id")
                    return self._default_fav_media_id
        except Exception as e:
            logger.warning(f"[WebClient] 获取默认收藏夹失败: {e}")
        return None

    def add_to_favorite(self, aid: int, media_id: Optional[int] = None) -> bool:
        """将稿件添加到收藏夹。media_id 为空时使用默认收藏夹。"""
        if not media_id:
            media_id = self._get_default_fav_folder_id()
        if not media_id:
            logger.warning("[WebClient] 无法获取默认收藏夹 media_id")
            return False

        url = "https://api.bilibili.com/x/v3/fav/resource/deal"
        data = {
            "rid": aid,
            "type": 2,
            "add_media_ids": str(media_id),
            "del_media_ids": "",
            "csrf": self.bili_jct,
            "platform": "web",
            "eavp": "",
            "fp": "",
            "jsonp": "jsonp",
        }
        try:
            cookies = self._build_cookies()
            headers = {
                "Referer": "https://www.bilibili.com",
                "Origin": "https://www.bilibili.com",
            }
            resp = self.session.post(
                url,
                data=data,
                headers=headers,
                cookies=cookies,
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") == 0:
                logger.info(f"[WebClient] 成功收藏 aid={aid} 到收藏夹 {media_id}")
                return True
            logger.warning(
                f"[WebClient] 收藏 aid={aid} 失败: code={result.get('code')}, "
                f"msg={result.get('message', '')}"
            )
            return False
        except Exception as e:
            logger.warning(f"[WebClient] 收藏请求异常 aid={aid}: {e}")
            return False

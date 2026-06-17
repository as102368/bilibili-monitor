"""
仿照 DownKyi VideoStream 获取 B站视频直链，绕过 yt-dlp 的 412 风控。
"""
from typing import List, Optional, Dict, Any

from .web_client import BilibiliWebClient
from .wbi import WBI
from .logger import get_logger

logger = get_logger(__name__)


class VideoStream:
    """视频流地址获取器"""

    def __init__(self, web_client: BilibiliWebClient, wbi: WBI):
        self.web = web_client
        self.wbi = wbi

    def get_playurl(self, bvid: str, cid: int, qn: int = 125) -> Optional[Dict[str, Any]]:
        """
        获取视频播放地址（DASH 格式）。
        仿照 DownKyi VideoStream.GetVideoPlayUrl()
        """
        params = {
            "bvid": bvid,
            "cid": cid,
            "qn": qn,
            "fourk": 1,
            "fnver": 0,
            "fnval": 4048,  # 请求 DASH 格式
        }
        signed = self.wbi.sign(params)
        data = self.web.request(
            "https://api.bilibili.com/x/player/wbi/playurl",
            referer=f"https://www.bilibili.com/video/{bvid}",
            params=signed,
            timeout=15,
        )
        if data.get("code") != 0:
            logger.warning(f"[VideoStream] 获取 playurl 失败: {data}")
            return None
        return data.get("data")

    def get_video_info(self, bvid: str) -> Optional[Dict[str, Any]]:
        """
        获取视频详情，提取 cid、标题等信息。
        仿照 DownKyi VideoInfo.VideoViewInfo()
        """
        params = {"bvid": bvid}
        signed = self.wbi.sign(params)
        data = self.web.request(
            "https://api.bilibili.com/x/web-interface/wbi/view",
            referer="https://www.bilibili.com",
            params=signed,
            timeout=15,
        )
        if data.get("code") != 0:
            logger.warning(f"[VideoStream] 获取视频详情失败: {data}")
            return None
        return data.get("data")

    @staticmethod
    def select_best_stream(streams: List[Dict[str, Any]], target_qn: int) -> Optional[Dict[str, Any]]:
        """从 DASH stream 列表中选择最接近目标画质的流"""
        if not streams:
            return None
        # 按画质从高到低排序
        sorted_streams = sorted(streams, key=lambda x: x.get("id", 0), reverse=True)
        # 选择不超过目标画质的最高画质
        for s in sorted_streams:
            if s.get("id", 0) <= target_qn:
                return s
        return sorted_streams[0]  # 如果没有合适的，返回最高画质

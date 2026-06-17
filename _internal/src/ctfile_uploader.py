"""
城通网盘 (CTFile) 文件上传模块。
参照官方 REST API v1 实现：
https://openapi.ctfile.com/
"""
import os
import time
import requests
from typing import Optional, List, Dict, Any

from .logger import get_logger

logger = get_logger(__name__)


API_BASE = "https://rest.ctfile.com/v1"


class CtfileUploader:
    """城通网盘文件上传器"""

    def __init__(self, session_token: str, folder_id: str = "0"):
        self.session_token = session_token
        self.folder_id = folder_id

    def _post_json(self, path: str, payload: dict, timeout: int = 30) -> dict:
        """发送 POST JSON 请求"""
        url = f"{API_BASE}{path}"
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def get_upload_url(self) -> Optional[str]:
        """
        获取文件上传 URL。
        POST /v1/public/file/upload
        """
        if not self.session_token:
            logger.warning("[CTFile] 未配置 session token，跳过上传")
            return None
        try:
            data = self._post_json(
                "/public/file/upload",
                {"session": self.session_token, "folder_id": self.folder_id},
                timeout=15,
            )
            if data.get("code") == 200:
                upload_url = data.get("upload_url")
                if upload_url:
                    logger.info("[CTFile] 获取上传地址成功")
                    return upload_url
                else:
                    logger.warning(f"[CTFile] 上传地址为空: {data}")
            else:
                logger.warning(f"[CTFile] 获取上传地址失败: {data.get('message')}")
        except Exception as e:
            logger.error(f"[CTFile] 获取上传地址异常: {e}")
        return None

    def list_files(self, keyword: str = "", start: int = 0, limit: int = 100) -> Optional[List[Dict[str, Any]]]:
        """
        列出指定文件夹中的文件。
        POST /v1/public/file/list
        返回 None 表示 API 调用异常（如 429 限流），调用方应重试。
        """
        try:
            payload = {
                "session": self.session_token,
                "folder_id": self.folder_id,
                "start": start,
                "limit": limit,
            }
            if keyword:
                payload["keyword"] = keyword
            data = self._post_json("/public/file/list", payload, timeout=15)
            if data.get("code") == 200:
                return data.get("results", [])
            else:
                logger.warning(f"[CTFile] 列出文件失败: {data.get('message')}")
        except Exception as e:
            logger.error(f"[CTFile] 列出文件异常: {e}")
            return None
        return []

    def verify_file_exists(self, file_name: str, file_size: int) -> bool:
        """
        校验网盘中是否存在指定文件名和大小的文件。
        上传后 keyword 索引可能有延迟，因此使用全量列表进行匹配。
        """
        logger.info(f"[CTFile] 校验网盘文件: {file_name} ({file_size} bytes)")
        for attempt in range(3):
            files = self.list_files(keyword="", limit=200)
            if files is None:
                # API 异常（如 429 限流），延长等待后重试
                wait_sec = 10 * (attempt + 1)
                logger.warning(f"[CTFile] 列表接口异常，{wait_sec}秒后重试...")
                time.sleep(wait_sec)
                continue
            for f in files:
                if f.get("name") == file_name and f.get("size") == file_size:
                    logger.info(f"[CTFile] 校验通过: 网盘中已存在 {file_name}")
                    return True
            if attempt < 2:
                wait_sec = 3 * (attempt + 1)
                logger.warning(f"[CTFile] 未找到文件，{wait_sec}秒后重试...")
                time.sleep(wait_sec)
        logger.warning(f"[CTFile] 校验失败: 网盘中未找到 {file_name}")
        return False

    def upload_file(self, file_path: str) -> bool:
        """
        上传单个文件到城通网盘。
        流程：
        1. 获取 upload_url
        2. 使用 multipart/form-data POST 文件
        """
        if not os.path.isfile(file_path):
            logger.error(f"[CTFile] 文件不存在: {file_path}")
            return False

        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)
        logger.info(f"[CTFile] 开始上传: {file_name} ({file_size / 1024 / 1024:.1f} MB)")

        upload_url = self.get_upload_url()
        if not upload_url:
            return False

        try:
            with open(file_path, "rb") as f:
                files = {"file": (file_name, f)}
                data_fields = {
                    "filesize": str(file_size),
                    "filename": file_name,
                }
                resp = requests.post(
                    upload_url,
                    files=files,
                    data=data_fields,
                    timeout=300,
                )
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") == 200:
                logger.info(f"[CTFile] 上传成功: {file_name}")
                return True
            else:
                logger.error(f"[CTFile] 上传失败: {result.get('message')}")
                return False
        except Exception as e:
            logger.error(f"[CTFile] 上传异常: {e}")
            return False

    def upload_and_delete(self, file_path: str) -> bool:
        """
        上传文件，上传成功后校验网盘中确实存在该文件，
        只有校验通过后才删除本地源文件。
        """
        if not os.path.isfile(file_path):
            logger.error(f"[CTFile] 文件不存在: {file_path}")
            return False

        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)

        # 1. 上传
        success = self.upload_file(file_path)
        if not success:
            logger.warning(f"[CTFile] 上传未成功，保留本地文件: {file_path}")
            return False

        # 2. 校验网盘中是否存在该文件（文件名 + 大小匹配）
        verified = self.verify_file_exists(file_name, file_size)
        if not verified:
            logger.warning(f"[CTFile] 网盘校验未通过，保留本地文件: {file_path}")
            return False

        # 3. 只有校验通过后才删除本地源文件
        try:
            os.remove(file_path)
            logger.info(f"[CTFile] 网盘校验通过，已删除本地文件: {file_path}")
        except OSError as e:
            logger.error(f"[CTFile] 删除本地文件失败: {e}")
            return False

        return True

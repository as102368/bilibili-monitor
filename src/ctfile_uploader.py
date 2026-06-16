"""
城通网盘 (CTFile) 文件上传模块。
参照官方 REST API v1 实现：
https://openapi.ctfile.com/
"""
import os
import requests
from typing import Optional


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
            print("[CTFile] 未配置 session token，跳过上传")
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
                    print(f"[CTFile] 获取上传地址成功")
                    return upload_url
                else:
                    print(f"[CTFile] 上传地址为空: {data}")
            else:
                print(f"[CTFile] 获取上传地址失败: {data.get('message')}")
        except Exception as e:
            print(f"[CTFile] 获取上传地址异常: {e}")
        return None

    def upload_file(self, file_path: str) -> bool:
        """
        上传单个文件到城通网盘。
        流程：
        1. 获取 upload_url
        2. 使用 multipart/form-data POST 文件
        """
        if not os.path.isfile(file_path):
            print(f"[CTFile] 文件不存在: {file_path}")
            return False

        file_size = os.path.getsize(file_path)
        print(f"[CTFile] 开始上传: {os.path.basename(file_path)} ({file_size / 1024 / 1024:.1f} MB)")

        upload_url = self.get_upload_url()
        if not upload_url:
            return False

        try:
            with open(file_path, "rb") as f:
                files = {"file": (os.path.basename(file_path), f)}
                resp = requests.post(
                    upload_url,
                    files=files,
                    timeout=300,
                )
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") == 200:
                print(f"[CTFile] 上传成功: {os.path.basename(file_path)}")
                return True
            else:
                print(f"[CTFile] 上传失败: {result.get('message')}")
                return False
        except Exception as e:
            print(f"[CTFile] 上传异常: {e}")
            return False

    def upload_and_delete(self, file_path: str) -> bool:
        """上传文件，成功后删除本地源文件"""
        success = self.upload_file(file_path)
        if success:
            try:
                os.remove(file_path)
                print(f"[CTFile] 已删除本地文件: {file_path}")
            except OSError as e:
                print(f"[CTFile] 删除本地文件失败: {e}")
        return success

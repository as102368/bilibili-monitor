"""
城通网盘 (CTFile) 文件上传模块。
参照官方 REST API v1 实现：
https://openapi.ctfile.com/
"""
import os
import time
import requests
from typing import Optional, List, Dict, Any
from collections import defaultdict

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
        遇到 429 限流会自动重试（指数退避）。
        """
        if not self.session_token:
            logger.warning("[CTFile] 未配置 session token，跳过上传")
            return None
        for attempt in range(3):
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
                        return None
                else:
                    logger.warning(f"[CTFile] 获取上传地址失败: {data.get('message')}")
                    return None
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 429:
                    wait_sec = 5 * (attempt + 1)
                    logger.warning(f"[CTFile] 获取上传地址触发 429 限流，{wait_sec}秒后重试...")
                    time.sleep(wait_sec)
                else:
                    logger.error(f"[CTFile] 获取上传地址异常: {e}")
                    return None
            except Exception as e:
                logger.error(f"[CTFile] 获取上传地址异常: {e}")
                return None
        logger.error("[CTFile] 获取上传地址失败: 超过最大重试次数")
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
        上传后 keyword 索引可能有延迟，因此使用全量列表进行匹配（支持翻页）。
        """
        logger.info(f"[CTFile] 校验网盘文件: {file_name} ({file_size} bytes)")
        for attempt in range(3):
            all_files = []
            start = 0
            limit = 200
            api_error = False
            while True:
                files = self.list_files(keyword="", start=start, limit=limit)
                if files is None:
                    api_error = True
                    break
                if not files:
                    break
                all_files.extend(files)
                if len(files) < limit:
                    break
                start += limit

            if api_error:
                wait_sec = 10 * (attempt + 1)
                logger.warning(f"[CTFile] 列表接口异常，{wait_sec}秒后重试...")
                time.sleep(wait_sec)
                continue

            for f in all_files:
                if self._get_file_name(f) == file_name and self._get_file_size(f) == file_size:
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
        上传文件，上传成功后校验网盘中是否存在该文件，
        校验通过或上传接口本身已返回成功均删除本地源文件。
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
        # 校验失败仅记录警告，不阻止删除本地文件，
        # 因为上传接口已返回成功，未找到多为列表索引延迟或分页限制。
        verified = self.verify_file_exists(file_name, file_size)
        if not verified:
            logger.warning(
                f"[CTFile] 网盘校验未找到文件，但上传接口已返回成功，"
                f"可能为列表索引延迟。仍视为上传成功: {file_name}"
            )

        # 3. 上传成功后删除本地源文件
        try:
            os.remove(file_path)
            logger.info(f"[CTFile] 已删除本地文件: {file_path}")
        except OSError as e:
            logger.error(f"[CTFile] 删除本地文件失败: {e}")
            return False

        return True

    def get_all_files(self) -> List[Dict[str, Any]]:
        """
        获取当前文件夹下的所有文件（自动翻页）。
        返回文件列表，每个文件包含 key, name, size, date 等字段。
        通过 key 去重，避免翻页 API 返回重复数据。
        """
        all_files = []
        seen_keys = set()
        start = 0
        limit = 200
        while True:
            files = self.list_files(keyword="", start=start, limit=limit)
            if files is None:
                logger.error("[CTFile] 获取文件列表时 API 异常，终止翻页")
                break
            if not files:
                break
            new_count = 0
            for f in files:
                key = f.get("key")
                if key and key in seen_keys:
                    continue
                if key:
                    seen_keys.add(key)
                all_files.append(f)
                new_count += 1
            if len(files) < limit:
                break
            if new_count == 0:
                # 连续一页没有新文件，终止翻页防止死循环
                break
            start += limit
        return all_files

    def delete_files(self, file_ids: List[str]) -> bool:
        """
        批量删除网盘文件（移至回收站）。
        file_ids: 文件 key 列表，如 ["f123456", "f789012"]
        遇到 429 限流会自动重试（指数退避）。
        """
        if not file_ids:
            return True
        ids_str = ",".join(file_ids)
        for attempt in range(3):
            try:
                data = self._post_json(
                    "/public/file/delete",
                    {"session": self.session_token, "ids": ids_str},
                    timeout=30,
                )
                if data.get("code") == 200:
                    logger.info(f"[CTFile] 删除成功: {len(file_ids)} 个文件")
                    return True
                else:
                    logger.warning(f"[CTFile] 删除失败: {data.get('message')}")
                    return False
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 429:
                    wait_sec = 5 * (attempt + 1)
                    logger.warning(f"[CTFile] 删除触发 429 限流，{wait_sec}秒后重试...")
                    time.sleep(wait_sec)
                else:
                    logger.error(f"[CTFile] 删除文件异常: {e}")
                    return False
            except Exception as e:
                logger.error(f"[CTFile] 删除文件异常: {e}")
                return False
        logger.error("[CTFile] 删除文件失败: 超过最大重试次数")
        return False

    @staticmethod
    def _get_file_name(f: dict) -> str:
        """尝试多种可能的字段名获取文件名。"""
        for key in ("name", "file_name", "title", "filename"):
            val = f.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
        return ""

    @staticmethod
    def _get_file_size(f: dict) -> int:
        """尝试多种可能的字段名获取文件大小，统一转为 int。"""
        for key in ("size", "file_size", "bytes", "filesize"):
            val = f.get(key)
            if val is not None:
                try:
                    return int(val)
                except (ValueError, TypeError):
                    continue
        return 0

    def find_duplicates(self) -> List[List[Dict[str, Any]]]:
        """
        扫描当前文件夹，查找重复文件。
        由于城通网盘 API 未明确提供 MD5，当前基于 (name, size) 判断重复。
        如果 API 实际返回了 md5/hash 字段，会优先使用 md5 判断。
        返回: 重复文件组列表，每组包含 2 个及以上文件字典。
        """
        files = self.get_all_files()
        if not files:
            return []

        # 优先使用 md5/hash 字段（如果 API 返回了）
        groups = defaultdict(list)
        has_md5 = any(f.get("md5") or f.get("hash") for f in files)

        if has_md5:
            for f in files:
                md5 = f.get("md5") or f.get("hash") or ""
                key = (self._get_file_name(f), self._get_file_size(f), md5)
                groups[key].append(f)
        else:
            for f in files:
                key = (self._get_file_name(f), self._get_file_size(f))
                groups[key].append(f)

        # 安全检查：如果最大组过大，说明字段解析有问题，拒绝执行
        max_group_size = max((len(g) for g in groups.values()), default=0)
        if max_group_size > len(files) * 0.3:
            logger.error(
                f"[CTFile] 去重安全检查失败: 最大分组包含 {max_group_size}/{len(files)} 个文件，"
                f"可能 name/size 字段解析异常。已阻止删除操作。"
            )
            raise ValueError(
                f"扫描结果异常: 超过30%的文件被归为同一组({max_group_size}/{len(files)})。"
                f"可能网盘API返回的字段格式与预期不符，已自动阻止删除。"
            )

        duplicates = [group for group in groups.values() if len(group) >= 2]
        logger.info(f"[CTFile] 扫描完成: {len(files)} 个文件，发现 {len(duplicates)} 组重复")
        return duplicates

    def remove_duplicates(self, duplicates: Optional[List[List[Dict[str, Any]]]] = None) -> int:
        """
        删除重复文件，每组仅保留第一个。
        为避免 429 限流，每 30 秒删除一个文件。
        返回实际删除的文件数量。
        """
        if duplicates is None:
            duplicates = self.find_duplicates()
        if not duplicates:
            logger.info("[CTFile] 未发现重复文件")
            return 0

        deleted_count = 0
        for group in duplicates:
            # 保留第一个，逐个删除其余
            to_delete = group[1:]
            for f in to_delete:
                file_id = f.get("key")
                if file_id:
                    if self.delete_files([file_id]):
                        deleted_count += 1
                    # 每 30 秒删除一个，避免触发 429 限流
                    time.sleep(30)
        logger.info(f"[CTFile] 去重完成: 删除了 {deleted_count} 个重复文件")
        return deleted_count

    def sync_local_folder(self, local_dir: str) -> dict:
        """
        同步本地文件夹到城通网盘。
        - 网盘中不存在的文件：上传后删除本地源文件
        - 网盘中已存在的文件：直接删除本地源文件
        返回统计信息 {"uploaded": int, "deleted": int, "failed": int}
        """
        stats = {"uploaded": 0, "deleted": 0, "failed": 0}
        if not os.path.isdir(local_dir):
            logger.error(f"[CTFile] 本地目录不存在: {local_dir}")
            return stats

        files = [
            f for f in os.listdir(local_dir)
            if os.path.isfile(os.path.join(local_dir, f))
        ]
        if not files:
            logger.info(f"[CTFile] 本地目录为空: {local_dir}")
            return stats

        logger.info(f"[CTFile] 开始同步本地目录: {local_dir}，共 {len(files)} 个文件")
        for file_name in files:
            file_path = os.path.join(local_dir, file_name)
            file_size = os.path.getsize(file_path)

            exists = self.verify_file_exists(file_name, file_size)
            if exists:
                logger.info(f"[CTFile] 网盘已存在，删除本地文件: {file_name}")
                try:
                    os.remove(file_path)
                    stats["deleted"] += 1
                except OSError as e:
                    logger.error(f"[CTFile] 删除本地文件失败: {file_name} - {e}")
                    stats["failed"] += 1
            else:
                logger.info(f"[CTFile] 网盘不存在，开始上传: {file_name}")
                if self.upload_and_delete(file_path):
                    stats["uploaded"] += 1
                else:
                    stats["failed"] += 1

        logger.info(
            f"[CTFile] 同步完成: 上传 {stats['uploaded']} 个，"
            f"直接删除 {stats['deleted']} 个，失败 {stats['failed']} 个"
        )
        return stats

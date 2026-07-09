import base64
import os
import sys
import yaml


def _get_base_dir() -> str:
    """动态推导程序根目录：
    - PyInstaller 打包后使用 exe 所在目录
    - 源码运行时使用项目根目录（src 的父目录）
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # 当前文件位于 src/config_loader.py，项目根目录为其父目录
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE_DIR = _get_base_dir()

# 需要加密的敏感字段路径 (section, key)
SENSITIVE_FIELDS = [
    ("cookie", "sessdata"),
    ("cookie", "bili_jct"),
    ("cookie", "buvid3"),
    ("cookie", "dedeuserid"),
    ("ctfile", "session"),
]


def _is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith("DPAPI:")


def _is_dpapi_blob_base64(value: str) -> bool:
    """检测 value 是否是 DPAPI blob 的 base64（防止用户把加密值又贴回输入框）。"""
    if not isinstance(value, str) or len(value) < 50:
        return False
    try:
        raw = base64.b64decode(value, validate=True)
        # DPAPI blob 头部魔数（CryptProtectData 输出固定以该 20 字节开头）
        return raw.startswith(
            b"\x01\x00\x00\x00\xd0\x8c\x9d\xdf\x01\x15\xd1\x11\x8c\x7a\x00\xc0\x4f\xc2\x97\xeb"
        )
    except Exception:
        return False


def _encrypt_value(value: str) -> str:
    """使用 Windows DPAPI 加密字符串，失败则原样返回并打印警告。"""
    if not value or _is_encrypted(value):
        return value
    # 用户若把 config.yaml 中 DPAPI: 后面的 base64 复制回来，不要再次加密
    if _is_dpapi_blob_base64(value):
        print("[Config] 检测到已加密的 DPAPI blob，跳过重复加密")
        return "DPAPI:" + value
    if sys.platform != "win32":
        return value
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", wintypes.LPBYTE),
            ]

        encoded = value.encode("utf-8")
        blob_in = DATA_BLOB(
            len(encoded),
            ctypes.cast(encoded, wintypes.LPBYTE),
        )
        blob_out = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        encrypted = bytes(blob_out.pbData[:blob_out.cbData])
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return "DPAPI:" + base64.b64encode(encrypted).decode("ascii")
    except Exception as e:
        print(f"[Config] 加密敏感字段失败，将明文保存: {e}")
        return value


def _decrypt_value(value: str) -> str:
    """解密 DPAPI 加密的字符串；非加密值原样返回。"""
    if not _is_encrypted(value):
        return value
    if sys.platform != "win32":
        return value
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", wintypes.LPBYTE),
            ]

        raw = base64.b64decode(value[6:])
        blob_in = DATA_BLOB(len(raw), ctypes.cast(raw, wintypes.LPBYTE))
        blob_out = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        decrypted = bytes(blob_out.pbData[:blob_out.cbData])
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return decrypted.decode("utf-8")
    except Exception as e:
        print(f"[Config] 解密敏感字段失败: {e}")
        return ""


DEFAULT_CONFIG = {
    "cookie": {
        "sessdata": "",
        "bili_jct": "",
        "buvid3": "",
        "dedeuserid": "",
    },
    "monitor": {
        "interval": 60,
        "page_size": 5,
    },
    "download": {
        "output_dir": "./downloads",
        "quality": "best",
        "filename_template": "%(uploader)s - %(title)s [%(id)s].%(ext)s",
        "concurrent_downloads": 2,
    },
    "database": {
        "path": "./data/downloaded.db",
    },
}


def _transform_sensitive(config: dict, transform):
    for section, key in SENSITIVE_FIELDS:
        if section in config and isinstance(config[section], dict) and key in config[section]:
            value = config[section][key]
            if isinstance(value, str):
                config[section][key] = transform(value)


def load_config(path=None):
    if path is None:
        path = os.path.join(BASE_DIR, "config.yaml")
    if not os.path.exists(path):
        save_config(DEFAULT_CONFIG, path)
        print(f"已创建默认配置文件: {path}，请先填写 Cookie 后再运行")
        return None
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    merged = _deep_copy(DEFAULT_CONFIG)
    _deep_update(merged, config or {})
    # 解密敏感字段
    _transform_sensitive(merged, _decrypt_value)
    return merged


def save_config(config, path=None):
    if path is None:
        path = os.path.join(BASE_DIR, "config.yaml")
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    # 写入前加密敏感字段，避免明文落盘
    config_to_save = _deep_copy(config)
    _transform_sensitive(config_to_save, _encrypt_value)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config_to_save, f, allow_unicode=True, default_flow_style=False)


def _deep_copy(d):
    if isinstance(d, dict):
        return {k: _deep_copy(v) for k, v in d.items()}
    return d


def _deep_update(base, update):
    for k, v in update.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            _deep_update(base[k], v)
        else:
            base[k] = v

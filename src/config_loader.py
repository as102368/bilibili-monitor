import os
import yaml

# 程序根目录，打包后统一放在该目录下
BASE_DIR = r"D:\BI\bilibili-monitor"

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
    },
    "database": {
        "path": "./data/downloaded.db",
    },
}


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
    return merged


def save_config(config, path=None):
    if path is None:
        path = os.path.join(BASE_DIR, "config.yaml")
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)


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

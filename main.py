import sys
import os
import asyncio
import logging

from src.config_loader import load_config, BASE_DIR
from src.monitor import BilibiliMonitor
from src.logger import setup_logging


def main():
    # 将工作目录切换到固定程序目录，确保 config.yaml 等资源路径正确
    os.makedirs(BASE_DIR, exist_ok=True)
    os.chdir(BASE_DIR)

    # PyInstaller 打包后额外处理证书路径
    if getattr(sys, "frozen", False):
        # 修复 certifi 证书路径，确保 requests/aiohttp 等能正常走 HTTPS
        cert_path = os.path.join(sys._MEIPASS, "certifi", "cacert.pem")
        if os.path.exists(cert_path):
            os.environ["SSL_CERT_FILE"] = cert_path
            os.environ["REQUESTS_CA_BUNDLE"] = cert_path
        # 如果配置目录没有 config.yaml，从 _internal 复制一份默认配置出来
        config_path = os.path.join(BASE_DIR, "config.yaml")
        if not os.path.exists(config_path):
            internal_config = os.path.join(sys._MEIPASS, "config.yaml")
            if os.path.exists(internal_config):
                import shutil
                shutil.copy2(internal_config, config_path)

    setup_logging(level=logging.INFO, handler=logging.StreamHandler())
    config = load_config()
    if config is None:
        print("请先填写 config.yaml 中的 Cookie 配置后再运行")
        sys.exit(1)

    if not config["cookie"].get("sessdata") or not config["cookie"].get("bili_jct"):
        print("错误: 请在 config.yaml 中配置 sessdata 和 bili_jct")
        print("获取方式:")
        print("  1. 用浏览器登录B站")
        print("  2. 按F12打开开发者工具 -> Application/应用 -> Cookies -> https://www.bilibili.com")
        print("  3. 复制 SESSDATA 和 bili_jct 的值到 config.yaml")
        sys.exit(1)

    async def _run():
        monitor = BilibiliMonitor(config)
        await monitor.init()
        monitor.start()

        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, SystemExit):
            print("\n[Exit] 收到中断信号，正在关闭...")
            monitor.stop()

    asyncio.run(_run())


if __name__ == "__main__":
    main()

import sys
import asyncio
from src.config_loader import load_config
from src.monitor import BilibiliMonitor


async def main():
    config = load_config("config.yaml")
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

    monitor = BilibiliMonitor(config)
    await monitor.init()
    monitor.start()

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        print("\n[Exit] 收到中断信号，正在关闭...")
        monitor.stop()


if __name__ == "__main__":
    asyncio.run(main())

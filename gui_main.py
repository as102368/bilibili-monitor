import sys
import os
import asyncio
import socket


def check_instance_running(port=37429):
    """检测是否已有实例在运行。返回 (is_running, sock)，第一个实例需要保持 sock 不关闭。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        return False, sock
    except OSError:
        sock.close()
        return True, None


from src.config_loader import BASE_DIR


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

    import qasync
    from PySide6.QtWidgets import QApplication
    from src.gui.main_window import MainWindow

    app = QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    is_running, instance_sock = check_instance_running()
    window = MainWindow(instance_running=is_running)
    window._instance_sock = instance_sock
    window.show()

    async def _keep_alive():
        while True:
            await asyncio.sleep(3600)

    loop.create_task(_keep_alive())
    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()

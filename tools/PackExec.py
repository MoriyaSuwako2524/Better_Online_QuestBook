import os
import sys
import socket
import threading
import time
import importlib
from http.server import SimpleHTTPRequestHandler
import socketserver
import subprocess
import webbrowser
import mimetypes
import shutil
import glob

# 配置区（集中修改）
APP_NAME = "BetterQuestBook"               # 打包生成的 exe 名称（无扩展名）
APP_TITLE = "Better Online QuestBook"      # 窗口标题（控制窗口）
APP_ICON = "bin/favicon.ico"               # 图标路径（相对于项目根或绝对路径），若无请留空 ''
WINDOW_WIDTH = 1024
WINDOW_HEIGHT = 768

# 确保静态资源的 MIME 映射
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")


def ensure_and_import(module_name, pip_name=None):
    """尝试导入 module_name；失败则 pip install 并重试，返回模块或 None。"""
    try:
        return importlib.import_module(module_name)
    except Exception:
        pass
    pkg = pip_name or module_name
    print(f"缺少依赖 {module_name}，尝试安装：pip install {pkg}")
    ret = subprocess.run([sys.executable, "-m", "pip", "install", pkg])
    if ret.returncode != 0:
        print(f"自动安装 {pkg} 失败（返回码 {ret.returncode}），将继续运行（可能功能受限）。")
        return None
    try:
        return importlib.import_module(module_name)
    except Exception as e:
        print(f"安装后导入 {module_name} 仍失败：{e}")
        return None


def find_free_port():
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def get_project_root():
    # tools/PackExec.py 位于 tools 子目录，项目根是上一级
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_resource_dir():
    # 返回项目根下的 bin 目录（兼容打包时 sys._MEIPASS）
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "bin")
    return os.path.join(get_project_root(), "bin")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    extensions_map = dict(SimpleHTTPRequestHandler.extensions_map)
    extensions_map.update({
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".wasm": "application/wasm",
    })


def start_static_server(resource_dir, host="127.0.0.1"):
    port = find_free_port()
    handler = lambda *args, **kwargs: QuietHandler(*args, directory=resource_dir, **kwargs)
    server = socketserver.ThreadingTCPServer((host, port), handler)
    server.allow_reuse_address = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def get_icon_path(project_root=None):
    if not APP_ICON:
        return None
    pr = project_root or get_project_root()
    icon_path = APP_ICON if os.path.isabs(APP_ICON) else os.path.join(pr, APP_ICON)
    return icon_path if os.path.isfile(icon_path) else None


def build_with_pyinstaller(exe_name=None):
    """
    使用 PyInstaller 打包本脚本（tools/PackExec.py）。
    打包后清理中间产物（build、.spec、__pycache__），保留 dist 下 exe。
    """
    exe_name = exe_name or APP_NAME
    project_root = get_project_root()
    bin_src = os.path.join(project_root, "bin")
    if not os.path.isdir(bin_src):
        print("未找到静态资源目录，无法打包:", bin_src)
        return 1

    sep = ";" if sys.platform.startswith("win") else ":"
    add_data = f"{bin_src}{sep}bin"

    # icon 参数
    icon_arg = []
    icon_file = get_icon_path(project_root)
    if icon_file:
        icon_arg = ["--icon", icon_file]
    else:
        if APP_ICON:
            print(f"未找到图标文件 {os.path.join(project_root, APP_ICON)}，打包时将不使用 --icon。")

    # 指定要打包的脚本为 tools/PackExec.py（相对于 project_root）
    script_path = os.path.join(project_root, "tools", os.path.basename(__file__))

    base_args = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        f"--add-data={add_data}",
        "--noconsole",
        "--name", exe_name,
        script_path,
    ]
    if icon_arg:
        base_args = base_args[:6] + icon_arg + base_args[6:]

    # 确保 PyInstaller 可用（尝试自动安装）
    if ensure_and_import("PyInstaller", "pyinstaller") is None:
        print("PyInstaller 安装或导入失败，无法继续打包。")
        return 1

    print("调用 PyInstaller 打包（可能需要几分钟）：")
    print(" ".join(base_args))
    ret = subprocess.run(base_args)

    if ret.returncode == 0:
        print("打包完成，在 dist 目录中查找生成的可执行文件。")
        # 清理 build 目录
        build_dir = os.path.join(project_root, "build")
        try:
            if os.path.isdir(build_dir):
                shutil.rmtree(build_dir, ignore_errors=True)
        except Exception:
            pass
        # 删除所有 *.spec 文件
        try:
            for spec in glob.glob(os.path.join(project_root, "*.spec")):
                try:
                    os.remove(spec)
                except Exception:
                    pass
        except Exception:
            pass
        # 清理 __pycache__
        for root, dirs, files in os.walk(project_root):
            for d in dirs:
                if d == "__pycache__":
                    try:
                        shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                    except Exception:
                        pass
        return 0

    print("打包失败，返回码：", ret.returncode)
    return ret.returncode


def show_controller(server, url):
    """弹出小控制窗口，便于打开浏览器或退出（如果 tkinter 可用）。"""
    project_root = get_project_root()
    icon_path = get_icon_path(project_root)

    try:
        import tkinter as tk
        from tkinter import PhotoImage
    except Exception:
        print("tkinter 不可用，回退到默认浏览器 + 阻塞等待模式。")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        return

    # Windows 上设置 AppUserModelID 改善任务栏图标显示
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
        except Exception:
            pass

    root = tk.Tk()
    root.title(f"{APP_TITLE} 控制")
    try:
        if icon_path:
            if sys.platform.startswith("win"):
                try:
                    root.iconbitmap(icon_path)
                except Exception:
                    try:
                        img = PhotoImage(file=icon_path)
                        root.iconphoto(True, img)
                    except Exception:
                        pass
            else:
                try:
                    img = PhotoImage(file=icon_path)
                    root.iconphoto(True, img)
                except Exception:
                    pass
    except Exception:
        pass

    root.geometry("360x140")
    root.resizable(False, False)

    lbl = tk.Label(root, text=f"服务地址：\n{url}", wraplength=340, justify="center")
    lbl.pack(pady=(10, 6))

    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=(0, 10))

    def open_browser():
        webbrowser.open(url)

    def on_close():
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass
        sys.exit(0)

    open_btn = tk.Button(btn_frame, text="打开浏览器", width=12, command=open_browser)
    quit_btn = tk.Button(btn_frame, text="退出应用", width=12, command=on_close)
    open_btn.pack(side="left", padx=8)
    quit_btn.pack(side="left", padx=8)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


def run_app_mode():
    """运行时行为：启动静态服务器并打开系统浏览器（用于被打包后的 exe）。"""
    resource_dir = get_resource_dir()
    if not os.path.isdir(resource_dir):
        print("未找到静态资源目录:", resource_dir)
        sys.exit(1)
    server, port = start_static_server(resource_dir)
    url = f"http://127.0.0.1:{port}/"
    webbrowser.open(url)
    show_controller(server, url)


def main():
    # 区分两种运行情形：
    # - 脚本直接运行（未被打包）：默认立即执行打包流程
    # - 被 PyInstaller 打包后运行（存在 sys._MEIPASS）：以运行模式启动应用
    if getattr(sys, "_MEIPASS", None):
        run_app_mode()
        return

    # 未被打包：执行打包（默认行为）
    # 确保 PyInstaller 可用（尝试安装）
    ensure_and_import("PyInstaller", "pyinstaller")
    exe_name = APP_NAME
    rc = build_with_pyinstaller(exe_name)
    if rc == 0:
        print("打包成功。生成的可执行文件位于 dist\\ 下。")
    else:
        print("打包遇到错误，返回码：", rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())

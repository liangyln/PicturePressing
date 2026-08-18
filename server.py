#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PicturePressing HTTP Server — 替代 server.js
纯 Python 标准库实现，无需 Node.js。

启动后自动打开浏览器访问 http://localhost:3000
"""

from __future__ import annotations

import http.server
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from socketserver import ThreadingMixIn

# ── 导入压缩模块 ──────────────────────────────────────────────────
from compress_images import (
    CompressConfig,
    PreviewConfig,
    generate_preview,
    normalize_path,
    run as run_compress,
    _safe_json_dumps,
)

PORT = int(os.environ.get("PORT", 3000))
PUBLIC_DIR = Path(__file__).resolve().parent / "public"

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

IMG_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"}


# ── 线程化的 HTTP 服务器 ───────────────────────────────────────────

class ThreadingHTTPServer(ThreadingMixIn, http.server.HTTPServer):
    """支持并发请求的 HTTP 服务器。"""
    daemon_threads = True
    allow_reuse_address = True


class PicturePressingHandler(http.server.BaseHTTPRequestHandler):
    """处理所有 API 和静态文件请求。"""

    # 禁用每次请求的 DNS 查询日志
    def log_message(self, format, *args):
        # 只打印关键错误，忽略标准访问日志以减少控制台噪音
        pass

    # ── CORS ──────────────────────────────────────────────────────

    def _set_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors()
        self.end_headers()

    # ── 请求体读取 ────────────────────────────────────────────────

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        if length > 10 * 1024 * 1024:  # 10 MB 限制
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    # ── JSON 响应 ─────────────────────────────────────────────────

    def _send_json(self, data, status=200):
        body = _safe_json_dumps(data).encode("utf-8")
        self.send_response(status)
        self._set_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── 路由 ──────────────────────────────────────────────────────

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/browse":
            self._handle_browse(parsed.query)
        else:
            self._serve_static(self.path)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/compress":
            self._handle_compress()
        elif parsed.path == "/api/preview":
            self._handle_preview()
        elif parsed.path == "/api/get-first-image":
            self._handle_get_first_image()
        else:
            self._send_json({"ok": False, "error": "未找到该 API"}, 404)

    # ── 静态文件服务 ──────────────────────────────────────────────

    def _serve_static(self, url_path):
        # 去掉查询参数
        url_path = url_path.split("?")[0]
        if url_path == "/":
            url_path = "/index.html"

        # 安全：防止路径遍历攻击
        file_path = (PUBLIC_DIR / url_path.lstrip("/")).resolve()
        try:
            file_path.relative_to(PUBLIC_DIR.resolve())
        except ValueError:
            self.send_error(403, "Forbidden")
            return

        if not file_path.is_file():
            self.send_error(404, "Not Found")
            return

        ext = file_path.suffix.lower()
        content_type = MIME_TYPES.get(ext, "application/octet-stream")

        try:
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self.send_error(500, "Internal Server Error")

    # ── API: 浏览目录 ─────────────────────────────────────────────

    def _handle_browse(self, query_string):
        params = urllib.parse.parse_qs(query_string)
        query_path = params.get("path", [""])[0]

        try:
            if not query_path:
                # 根级别：列出驱动器
                result = self._list_drives()
                self._send_json(result)
                return

            current_path = os.path.abspath(query_path)

            if not os.path.exists(current_path):
                self._send_json({"ok": False, "error": f"路径不存在: {query_path}"})
                return

            if not os.path.isdir(current_path):
                self._send_json({"ok": False, "error": f"不是文件夹: {query_path}"})
                return

            name = os.path.basename(current_path) or current_path

            children = []
            try:
                entries = os.scandir(current_path)
                for entry in sorted(entries, key=lambda e: e.name):
                    if entry.is_dir() and not entry.name.startswith(".") and not entry.name.startswith("$"):
                        children.append({
                            "path": os.path.join(current_path, entry.name),
                            "name": entry.name,
                        })
            except PermissionError:
                pass

            self._send_json({
                "ok": True,
                "current": current_path,
                "name": name,
                "children": children,
            })

        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 500)

    def _list_drives(self):
        """列举 Windows 驱动器号。"""
        if platform.system() == "Windows":
            try:
                result = subprocess.check_output(
                    ["wmic", "logicaldisk", "get", "name"],
                    text=True, timeout=5,
                    stderr=subprocess.DEVNULL,
                )
                drives = []
                for line in result.strip().split("\n")[1:]:
                    line = line.strip()
                    if len(line) >= 2 and line[1] == ":":
                        drives.append({
                            "path": line.rstrip() + "\\",
                            "name": line.rstrip() + "\\",
                        })
                if drives:
                    return {"ok": True, "children": drives}
            except Exception:
                pass

            # Fallback: 探测 A-Z
            children = []
            for c in range(65, 91):
                letter = chr(c)
                drive = f"{letter}:\\"
                try:
                    if os.path.exists(drive):
                        children.append({"path": drive, "name": drive})
                except Exception:
                    pass
            return {"ok": True, "children": children}
        else:
            return {"ok": True, "children": [{"path": "/", "name": "/"}]}

    # ── API: 获取第一张图片 ───────────────────────────────────────

    def _handle_get_first_image(self):
        try:
            body = self._read_body()
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 400)
            return

        input_path = body.get("input_path", "")
        if not input_path:
            self._send_json({"ok": False, "error": "缺少 input_path 参数"})
            return

        abs_path = os.path.abspath(input_path)

        if not os.path.exists(abs_path):
            self._send_json({"ok": False, "error": f"输入路径不存在: {input_path}"})
            return

        if not os.path.isdir(abs_path):
            self._send_json({"ok": False, "error": f"输入路径不是文件夹: {input_path}"})
            return

        first_image = None
        try:
            files = sorted(os.listdir(abs_path))
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in IMG_EXTENSIONS:
                    full = os.path.join(abs_path, f)
                    if os.path.isfile(full):
                        first_image = full
                        break
        except Exception as e:
            self._send_json({"ok": False, "error": f"读取目录失败: {e}"})
            return

        if not first_image:
            self._send_json({"ok": False, "error": "该文件夹中没有找到图片文件"})
            return

        self._send_json({"ok": True, "file_path": first_image})

    # ── API: 预览 ─────────────────────────────────────────────────

    def _handle_preview(self):
        try:
            body = self._read_body()
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 400)
            return

        if not body.get("file_path"):
            self._send_json({"ok": False, "error": "缺少 file_path 参数"})
            return

        try:
            cfg = PreviewConfig.from_dict(body)
            result = generate_preview(cfg)
            if result.get("ok"):
                self._send_json(result, 200)
            else:
                self._send_json(result, 500)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 500)

    # ── API: 批量压缩 (NDJSON 流式) ───────────────────────────────

    def _handle_compress(self):
        try:
            body = self._read_body()
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 400)
            return

        try:
            cfg = CompressConfig.from_dict(body)
        except Exception as e:
            self._send_json({"ok": False, "error": f"配置解析失败: {e}"}, 400)
            return

        # 设置 NDJSON 流式响应头
        self.send_response(200)
        self._set_cors()
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

        stream_active = True
        write_lock = threading.Lock()

        def emit(msg):
            nonlocal stream_active
            if not stream_active:
                return
            try:
                data = (_safe_json_dumps(msg) + "\n").encode("utf-8")
                with write_lock:
                    if stream_active:
                        self.wfile.write(data)
                        self.wfile.flush()
                if msg.get("event") == "done":
                    stream_active = False
            except (BrokenPipeError, ConnectionResetError, OSError):
                stream_active = False

        try:
            run_compress(cfg, emit=emit)
        except Exception as e:
            if stream_active:
                emit({
                    "ok": False,
                    "event": "done",
                    "error": str(e),
                    "total": 0,
                    "success": 0,
                    "failed": 0,
                    "results": [],
                })


# ── 启动 ──────────────────────────────────────────────────────────

def find_browser():
    """查找系统中可用的浏览器，按优先级返回 (名称, 路径列表)。

    优先使用 Edge/Chrome 的 --app 模式（无边框窗口像独立软件）。
    """
    candidates = [
        ("msedge",   ["msedge.exe", "Microsoft\\Edge\\Application\\msedge.exe"]),
        ("chrome",   ["chrome.exe", "Google\\Chrome\\Application\\chrome.exe"]),
        ("chromium", ["chromium.exe", "chrome.exe"]),
    ]

    for name, paths in candidates:
        for p in paths:
            # 先尝试 PATH
            found = shutil.which(p)
            if found:
                return name, found
            # 再尝试常见安装位置
            for base in [os.environ.get("ProgramFiles", "C:\\Program Files"),
                         os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
                         os.environ.get("LOCALAPPDATA", ""),
                         "C:\\Program Files"]:
                if base:
                    full = os.path.join(base, p)
                    if os.path.isfile(full):
                        return name, full
            # 检查 ProgramFiles 下以名称开头的目录
            for base in [os.environ.get("ProgramFiles", "C:\\Program Files"),
                         os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")]:
                if base:
                    try:
                        for d in os.listdir(base):
                            if d.lower().startswith(name):
                                full = os.path.join(base, d, "Application", f"{name}.exe")
                                if os.path.isfile(full):
                                    return name, full
                    except OSError:
                        pass

    return None, None


def open_app_window(url):
    """以 app 模式（无边框窗口）打开应用，失败则回退到系统默认浏览器。

    返回打开的浏览器进程对象（用于监控关闭），或 None（回退到浏览器时）。
    """
    name, path = find_browser()

    if name and path:
        try:
            if name == "msedge":
                # Edge: --app 模式
                proc = subprocess.Popen(
                    [path, f"--app={url}", "--window-size=760,860",
                     "--disable-extensions", "--disable-sync",
                     f"--user-data-dir={os.path.join(os.environ.get('TEMP', '.'), 'PicturePressing_Edge')}"],
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                )
            else:
                # Chrome/Chromium: --app 模式
                proc = subprocess.Popen(
                    [path, f"--app={url}", "--window-size=760,860"],
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                )
            print(f"已打开独立窗口 ({name})")
            return proc
        except Exception as e:
            print(f"无法以 app 模式启动 {name}: {e}")

    # 回退：用系统默认浏览器打开
    print("未找到 Edge/Chrome，使用系统默认浏览器...")
    webbrowser.open(url)
    return None


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), PicturePressingHandler)
    url = f"http://localhost:{PORT}"

    print(f"=" * 50)
    print(f"  PicturePressing — 图片压缩工具")
    print(f"  服务地址: {url}")
    print(f"=" * 50)

    # 等服务器就绪后打开窗口
    time.sleep(0.5)

    proc = open_app_window(url)

    if proc:
        print("关闭窗口即可退出程序。")
    else:
        print("按 Ctrl+C 停止服务。")

    try:
        if proc:
            # 监控浏览器进程，窗口关闭时自动退出
            while proc.poll() is None:
                time.sleep(0.5)
            print("\n窗口已关闭，正在退出...")
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭服务...")
    finally:
        server.server_close()
        print("已停止。")


if __name__ == "__main__":
    main()

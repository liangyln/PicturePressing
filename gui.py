#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PicturePressing — 图片批量压缩工具 (Native GUI)
基于 CustomTkinter，Windows 11 / iOS 风格现代界面。
"""

from __future__ import annotations

import base64
import ctypes
import io
import os
import platform
import subprocess
import sys
import threading

import customtkinter as ctk
import tkinter as tk
from PIL import Image, ImageTk

from compress_images import (
    IMAGE_EXTENSIONS,
    CompressConfig,
    PreviewConfig,
    check_encoder_available,
    generate_preview,
    normalize_path,
    run as run_compress,
)

# ── Win7 DPI 兼容 ──────────────────────────────────────────────────
if hasattr(sys, "getwindowsversion") and sys.getwindowsversion().major < 8:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ── 常量 ───────────────────────────────────────────────────────────
APP_TITLE = "PicturePressing — 图片压缩工具"
WINDOW_WIDTH = 540
WINDOW_HEIGHT = 700
_ALL_ENCODER_LABELS = {
    "mozjpeg":  "MozJPEG — 输出 .jpg（压缩比最优）",
    "webp":     "WebP — 输出 .webp（兼容性好）",
    "avif":     "AVIF — 输出 .avif（体积最小）",
    "pngquant": "pngquant — 输出 .png（无损压缩）",
}

# 检测当前环境可用编码器
_ENCODERS_AVAILABLE: list[str] = []
_ENCODER_LABELS: dict[str, str] = {}
for _enc in ["mozjpeg", "webp", "avif", "pngquant"]:
    if check_encoder_available(_enc):
        _ENCODERS_AVAILABLE.append(_enc)
        _ENCODER_LABELS[_enc] = _ALL_ENCODER_LABELS[_enc]

# 确保至少有一个编码器可用（mozjpeg 总是可用的）
if not _ENCODERS_AVAILABLE:
    _ENCODERS_AVAILABLE = ["mozjpeg"]
    _ENCODER_LABELS = {"mozjpeg": _ALL_ENCODER_LABELS["mozjpeg"]}

# ── 主题 ───────────────────────────────────────────────────────────
ctk.set_appearance_mode("system")       # 跟随系统亮/暗
ctk.set_default_color_theme("blue")     # 蓝色主题


# ══════════════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════════════

def _data_url_to_pil(data_url: str) -> Image.Image:
    """将 base64 data URL 解码为 PIL Image。"""
    b64 = data_url.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(b64)))


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    else:
        return f"{n / (1024 * 1024):.2f} MB"


def _list_drives() -> list[dict]:
    """列举 Windows 驱动器号。"""
    if platform.system() != "Windows":
        return [{"path": "/", "name": "/"}]
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
                drives.append({"path": line + "\\", "name": line + "\\"})
        if drives:
            return drives
    except Exception:
        pass
    # 回退：探测 A-Z
    drives = []
    for c in range(65, 91):
        letter = chr(c)
        drive = f"{letter}:\\"
        try:
            if os.path.exists(drive):
                drives.append({"path": drive, "name": drive})
        except Exception:
            pass
    return drives


def _list_subdirs(parent: str) -> list[dict]:
    """列出目录下的子目录。"""
    children = []
    try:
        for entry in sorted(os.scandir(parent), key=lambda e: e.name.lower()):
            if entry.is_dir() and not entry.name.startswith(".") and not entry.name.startswith("$"):
                children.append({"path": entry.path, "name": entry.name})
    except (PermissionError, OSError):
        pass
    return children


# ══════════════════════════════════════════════════════════════════════
# 目录选择弹窗
# ══════════════════════════════════════════════════════════════════════

class DirPickerDialog(ctk.CTkToplevel):
    """现代目录选择器弹窗。"""

    def __init__(self, master, callback, initial: str = ""):
        super().__init__(master)
        self.callback = callback
        self.selected_path = initial
        self.current_path = ""
        self.history: list[str] = []

        self.title("选择文件夹")
        self.geometry("520x480")
        self.minsize(400, 300)
        self.after(200, self.lift)

        # ── 面包屑 ──
        self.bread_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.bread_frame.pack(fill="x", padx=12, pady=(12, 0))

        # ── 目录列表 ──
        self.scroll = ctk.CTkScrollableFrame(self, label_text="")
        self.scroll.pack(fill="both", expand=True, padx=12, pady=8)

        # ── 底部栏 ──
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=12, pady=(0, 12))

        self.path_label = ctk.CTkLabel(
            bottom, text=initial or "请选择文件夹",
            anchor="w", font=ctk.CTkFont(size=12),
            text_color=("gray10", "gray90"),
        )
        self.path_label.pack(side="left", fill="x", expand=True, padx=(0, 8))

        cancel_btn = ctk.CTkButton(
            bottom, text="取消", width=70,
            text_color=("gray10", "gray90"),
            command=self.destroy,
        )
        cancel_btn.pack(side="right", padx=(4, 0))

        self.confirm_btn = ctk.CTkButton(
            bottom, text="确定", width=70,
            command=self._confirm,
            state="disabled",
            text_color=("gray10", "gray90"),
        )
        self.confirm_btn.pack(side="right")

        # 初始化
        self._navigate(initial or "")

    def _confirm(self):
        if self.selected_path:
            self.callback(self.selected_path)
        self.destroy()

    def _navigate(self, path: str):
        """导航到指定路径。"""
        self.current_path = os.path.abspath(path) if path else ""
        # 用户导航到某个目录后，直接视为已选择，可点击确定
        if self.current_path:
            self._select(self.current_path)
        self._render_breadcrumb()
        self._render_list()

    def _render_breadcrumb(self):
        for w in self.bread_frame.winfo_children():
            w.destroy()

        # 此电脑 按钮
        pc_btn = ctk.CTkButton(
            self.bread_frame, text="此电脑", width=60, height=26,
            font=ctk.CTkFont(size=12), fg_color="transparent",
            text_color=("gray10", "gray90"),
            command=lambda: self._navigate(""),
        )
        pc_btn.pack(side="left")

        if self.current_path:
            parts = self.current_path.split(os.sep)
            # Windows: 处理盘符
            if len(parts) > 1 and parts[1] == "":
                parts = [parts[0] + "\\"] + parts[2:]
            acc = ""
            for i, part in enumerate(parts):
                if not part:
                    continue
                sep_label = ctk.CTkLabel(
                    self.bread_frame, text=" › ", font=ctk.CTkFont(size=12),
                    text_color=("gray50", "gray60"),
                )
                sep_label.pack(side="left")
                if i == 0 and ":" in part:
                    acc = part
                else:
                    acc = os.path.join(acc, part) if acc else part
                target = acc
                btn = ctk.CTkButton(
                    self.bread_frame, text=part[:20], width=20, height=26,
                    font=ctk.CTkFont(size=12), fg_color="transparent",
                    text_color=("gray10", "gray90"),
                    command=lambda p=target: self._navigate(p),
                )
                btn.pack(side="left")

    def _render_list(self):
        for w in self.scroll.winfo_children():
            w.destroy()

        if not self.current_path:
            entries = _list_drives()
        else:
            entries = _list_subdirs(self.current_path)

        if not entries:
            empty = ctk.CTkLabel(
                self.scroll, text="（此文件夹下没有子文件夹）",
                text_color=("gray50", "gray60"),
            )
            empty.pack(pady=20)

        for entry in entries:
            row = ctk.CTkFrame(self.scroll, fg_color="transparent", height=36)
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)

            btn = ctk.CTkButton(
                row, text=f"📁  {entry['name']}", anchor="w",
                fg_color="transparent", text_color=("gray10", "gray90"),
                hover_color=("gray75", "gray25"),
                font=ctk.CTkFont(size=13),
                command=lambda p=entry["path"]: self._navigate(p),
            )
            btn.pack(fill="both", expand=True)

            select_btn = ctk.CTkButton(
                row, text="选择", width=44, height=26,
                font=ctk.CTkFont(size=11),
                fg_color=("gray20", "gray30"), hover_color=("gray35", "gray45"),
                text_color=("gray10", "gray90"),
                command=lambda p=entry["path"]: self._select(p),
            )
            select_btn.pack(side="right", padx=(4, 4))

    def _select(self, path: str):
        self.selected_path = path
        self.path_label.configure(text=path)
        self.confirm_btn.configure(state="normal")


# ══════════════════════════════════════════════════════════════════════
# 预览图片查看器（可缩放、导航）
# ══════════════════════════════════════════════════════════════════════

class ImageViewer(ctk.CTkToplevel):
    """全屏图片预览查看器 — 支持鼠标滚轮缩放、点击拖拽平移、键盘/按钮导航。"""

    def __init__(self, master, image_paths: list[str], start_index: int,
                 encoder: str, quality: int, scale_percent: int):
        super().__init__(master)
        self.title("预览对比")
        self.attributes("-topmost", True)
        self.after(150, lambda: self.attributes("-topmost", False))  # 置顶后恢复
        self.after(100, self.lift)
        self.after(150, self.focus_force)

        # 配置
        self._image_paths = image_paths        # 所有图片路径
        self._current_index = start_index       # 当前图片索引
        self._encoder = encoder
        self._quality = quality
        self._scale_percent = scale_percent

        # 缩放状态（跨图片保持）
        self._zoom = 1.0            # 1.0 = 适应窗口, >1 = 放大
        self._pan_x = 0             # 平移偏移（像素）
        self._pan_y = 0

        # 当前显示的 PIL 图像（原始和压缩）
        self._orig_pil = None       # type: Image.Image | None
        self._comp_pil = None       # type: Image.Image | None
        self._orig_ctk = None       # type: ctk.CTkImage | None
        self._comp_ctk = None       # type: ctk.CTkImage | None
        self._ctk_refs: list[ctk.CTkImage] = []  # 防止 GC
        self._pan_start_xy = None                 # 平移起点
        self._dragging = False                    # 是否正在拖拽

        # 窗口设置 — 按屏幕分辨率自适应，预留任务栏和标题栏空间
        self.resizable(True, True)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        # 宽：屏幕 90%，最大 1400px
        w = min(int(sw * 0.9), 1400)
        # 高：屏幕 85%，最大 850px（预留任务栏）
        h = min(int(sh * 0.85), 850)
        # 居中放置，y 轴略微上移 30px 避免遮挡底部操作栏
        x = (sw - w) // 2
        y = max(0, (sh - h) // 2 - 30)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(600, 400)
        self.bind("<Escape>", lambda e: self.destroy())

        # ── 顶部信息栏 ──
        self._build_top_bar()

        # ── 图片区域 ──
        self._build_image_area()

        # ── 底部栏 ──
        self._build_bottom_bar()

        # ── 导航箭头 ──
        self._build_nav_arrows()

        # 窗口大小变化时重新渲染（防抖）
        self._resize_after_id = None
        self.bind("<Configure>", self._on_configure)

        # 加载首张图片
        self.after(200, self._load_current)

    # ── 构建 UI ─────────────────────────────────────────────────

    def _build_top_bar(self):
        top = ctk.CTkFrame(self, fg_color="transparent", height=30)
        top.pack(fill="x", padx=8, pady=(6, 2))
        top.pack_propagate(False)

        self._filename_label = ctk.CTkLabel(
            top, text="", font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("gray10", "gray90"),
        )
        self._filename_label.pack(side="left")

        self._zoom_label = ctk.CTkLabel(
            top, text="", font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        )
        self._zoom_label.pack(side="right")

        self._count_label = ctk.CTkLabel(
            top, text="", font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        )
        self._count_label.pack(side="right", padx=(0, 16))

    def _build_image_area(self):
        """创建左右两个画布用于显示原始图和压缩图。"""
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=4, pady=2)

        # 左：原始图片
        left_frame = ctk.CTkFrame(main, fg_color="transparent")
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 2))
        ctk.CTkLabel(left_frame, text="原始图片", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=("gray10", "gray90")).pack(pady=(0, 2))
        canvas_bg = "#d9d9d9" if ctk.get_appearance_mode() == "Light" else "#2b2b2b"
        _binds = [
            ("<MouseWheel>",       self._on_mousewheel),
            ("<Button-4>",         self._on_mousewheel),
            ("<Button-5>",         self._on_mousewheel),
            ("<ButtonPress-1>",    self._on_left_press),
            ("<B1-Motion>",        self._on_left_drag),
            ("<ButtonRelease-1>",  self._on_left_release),
            ("<ButtonPress-2>",    self._on_middle_press),
            ("<B2-Motion>",        self._on_middle_drag),
            ("<ButtonRelease-2>",  self._on_middle_release),
            ("<ButtonPress-3>",    self._on_right_click),
        ]

        self._orig_canvas = tk.Canvas(left_frame, bg=canvas_bg, highlightthickness=0)
        self._orig_canvas.pack(fill="both", expand=True)
        for seq, handler in _binds:
            self._orig_canvas.bind(seq, handler)

        # 右：压缩图片
        right_frame = ctk.CTkFrame(main, fg_color="transparent")
        right_frame.pack(side="right", fill="both", expand=True, padx=(2, 0))
        ctk.CTkLabel(right_frame, text="压缩预览", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=("gray10", "gray90")).pack(pady=(0, 2))
        self._comp_canvas = tk.Canvas(right_frame, bg=canvas_bg, highlightthickness=0)
        self._comp_canvas.pack(fill="both", expand=True)
        for seq, handler in _binds:
            self._comp_canvas.bind(seq, handler)

        # 键盘导航
        self.bind_all("<Left>", lambda e: self._navigate(-1))
        self.bind_all("<Right>", lambda e: self._navigate(1))

    def _build_bottom_bar(self):
        bottom = ctk.CTkFrame(self, fg_color="transparent", height=40)
        bottom.pack(fill="x", padx=8, pady=(4, 6))
        bottom.pack_propagate(False)

        self._ratio_label = ctk.CTkLabel(
            bottom, text="", font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("gray10", "gray90"),
        )
        self._ratio_label.pack(side="left", padx=(8, 0))

        ctk.CTkButton(
            bottom, text="关闭", width=80, command=self.destroy,
        ).pack(side="right", padx=(0, 8))

        # 缩放提示
        ctk.CTkLabel(
            bottom, text="🖱 拖拽平移 | 滚轮缩放 | 右键缩小 | ← → 切换",
            font=ctk.CTkFont(size=11), text_color=("gray50", "gray60"),
        ).pack(side="right", padx=(0, 16))

    def _build_nav_arrows(self):
        """左右导航箭头（半透明覆盖在图片区域）。"""
        style = {
            "font": ctk.CTkFont(size=28, weight="bold"),
            "text_color": ("gray20", "gray80"),
            "fg_color": "transparent",
            "hover_color": ("gray85", "gray20"),
            "width": 40, "height": 80,
        }
        self._left_btn = ctk.CTkButton(
            self, text="◀", command=lambda: self._navigate(-1), **style,
        )
        self._right_btn = ctk.CTkButton(
            self, text="▶", command=lambda: self._navigate(1), **style,
        )

    def _position_arrows(self):
        """将导航箭头定位到窗口左右边缘。"""
        try:
            h = self.winfo_height()
            self._left_btn.place(x=4, y=(h - 80) // 2)
            self._right_btn.place(x=self.winfo_width() - 48, y=(h - 80) // 2)
        except Exception:
            pass

    # ── 图片加载 ─────────────────────────────────────────────────

    def _load_current(self):
        """加载当前索引的图片并生成预览。"""
        if 0 <= self._current_index < len(self._image_paths):
            path = self._image_paths[self._current_index]
        else:
            return

        self._filename_label.configure(text=os.path.basename(path))
        self._count_label.configure(
            text=f"{self._current_index + 1} / {len(self._image_paths)}"
        )
        self._clear_canvases()
        self._position_arrows()

        # 后台线程生成预览
        encoder = self._encoder
        quality = self._quality
        scale = self._scale_percent

        def _gen():
            try:
                cfg = PreviewConfig(file_path=path, encoder=encoder,
                                    quality=quality, scale_percent=scale)
                result = generate_preview(cfg)
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            self.after(0, lambda: self._show_result(result))

        threading.Thread(target=_gen, daemon=True).start()

    def _show_result(self, result: dict):
        """显示预览结果。"""
        if not result.get("ok"):
            self._orig_canvas.create_text(
                200, 150, text=f"错误: {result.get('error', '未知')}",
                fill="red", font=("", 12),
            )
            return

        # 解码 PIL 图像
        try:
            self._orig_pil = _data_url_to_pil(result["original_data_url"])
            self._comp_pil = _data_url_to_pil(result["compressed_data_url"])
        except Exception as e:
            self._orig_canvas.create_text(200, 150, text=f"解码失败: {e}", fill="red")
            return

        # 显示
        self._ratio_label.configure(text=result.get("ratio_text", ""))
        self._render_images()

    # ── 渲染 ──────────────────────────────────────────────────────

    def _clear_canvases(self):
        self._orig_canvas.delete("all")
        self._comp_canvas.delete("all")

    def _render_images(self):
        """根据当前 zoom 和 pan 渲染两张图片。"""
        if self._orig_pil is None or self._comp_pil is None:
            return

        for canvas, pil_img, store_attr in [
            (self._orig_canvas, self._orig_pil, "_orig_ctk"),
            (self._comp_canvas, self._comp_pil, "_comp_ctk"),
        ]:
            canvas.delete("all")
            cw = canvas.winfo_width() or 400
            ch = canvas.winfo_height() or 300

            if cw < 10 or ch < 10:
                continue

            pw, ph = pil_img.size

            if self._zoom <= 1.0:
                fit_scale = min(cw / pw, ch / ph, 1.0)
                display_w = int(pw * fit_scale)
                display_h = int(ph * fit_scale)
                display_img = pil_img.resize((display_w, display_h), Image.Resampling.LANCZOS)
            else:
                view_w = cw / self._zoom
                view_h = ch / self._zoom
                center_x = pw / 2 + self._pan_x
                center_y = ph / 2 + self._pan_y
                cx = max(0, min(center_x - view_w / 2, pw - view_w)) if view_w < pw else 0
                cy = max(0, min(center_y - view_h / 2, ph - view_h)) if view_h < ph else 0
                try:
                    crop = pil_img.crop((
                        int(cx), int(cy),
                        int(cx) + max(1, int(view_w)) if view_w < pw else pw,
                        int(cy) + max(1, int(view_h)) if view_h < ph else ph,
                    ))
                except Exception:
                    crop = pil_img
                display_img = crop.resize((cw, ch), Image.Resampling.LANCZOS)

            tk_img = ImageTk.PhotoImage(display_img)
            self._ctk_refs.append(tk_img)
            setattr(self, store_attr, tk_img)
            canvas.create_image(cw // 2, ch // 2, image=tk_img, anchor="center")

        if len(self._ctk_refs) > 10:
            self._ctk_refs = self._ctk_refs[-6:]

        self._update_zoom_label()

    def _update_zoom_label(self):
        if self._zoom <= 1.0:
            self._zoom_label.configure(text="100%")
        else:
            self._zoom_label.configure(text=f"{int(self._zoom * 100)}%")

    # ── 坐标转换 ──────────────────────────────────────────────────

    # ── 滚轮 / 右键缩放 ─────────────────────────────────────────

    def _do_zoom(self, canvas, mx: int, my: int, factor: float):
        """以画布上 (mx, my) 点为中心缩放（包括首次从适应窗口放大）。"""
        if self._orig_pil is None:
            return
        pw, ph = self._orig_pil.size
        old_zoom = max(self._zoom, 0.5)
        new_zoom = max(0.5, old_zoom * factor)
        new_zoom = max(0.5, min(10.0, new_zoom))

        cw = canvas.winfo_width() or 1
        ch = canvas.winfo_height() or 1

        if new_zoom <= 1.0:
            self._zoom = 1.0
            self._pan_x = 0
            self._pan_y = 0
            self._render_images()
            return

        if old_zoom <= 1.0:
            # 首次从适应窗口放大：计算鼠标指向的图片坐标
            fit = min(cw / pw, ch / ph, 1.0)
            offset_x = (cw - pw * fit) / 2
            offset_y = (ch - ph * fit) / 2
            img_px = max(0, min(pw, (mx - offset_x) / fit))
            img_py = max(0, min(ph, (my - offset_y) / fit))
        else:
            # 已放大：通过当前 pan 反算鼠标指向的图片坐标
            view_w = cw / old_zoom
            view_h = ch / old_zoom
            img_left = pw / 2 - view_w / 2 + self._pan_x
            img_top = ph / 2 - view_h / 2 + self._pan_y
            img_px = img_left + mx / old_zoom
            img_py = img_top + my / old_zoom

        self._zoom = new_zoom
        # 调整 pan 使同一图片坐标仍在鼠标位置下
        self._pan_x = img_px - pw / 2 - (mx - cw / 2) / new_zoom
        self._pan_y = img_py - ph / 2 - (my - ch / 2) / new_zoom
        self._render_images()

    def _on_mousewheel(self, event):
        if event.num == 5 or event.delta < 0:
            self._do_zoom(event.widget, event.x, event.y, 0.85)
        else:
            self._do_zoom(event.widget, event.x, event.y, 1.15)

    # ── 左键 / 中键拖拽：平移 ───────────────────────────────────

    def _on_left_press(self, event):
        if self._zoom <= 1.0:
            return
        event.widget.configure(cursor="fleur")
        self._pan_start_xy = (event.x, event.y)
        self._dragging = True

    def _on_left_drag(self, event):
        if not self._dragging:
            return
        dx = event.x - self._pan_start_xy[0]
        dy = event.y - self._pan_start_xy[1]
        self._pan_x -= dx / self._zoom
        self._pan_y -= dy / self._zoom
        self._pan_start_xy = (event.x, event.y)
        self._render_images()

    def _on_left_release(self, event):
        self._dragging = False
        self._pan_start_xy = None
        event.widget.configure(cursor="")

    # ── 右键：缩小 ────────────────────────────────────────────────

    def _on_right_click(self, event):
        if self._zoom <= 1.0:
            return
        self._do_zoom(event.widget, event.x, event.y, 0.7)

    # ── 中键：同样平移 ────────────────────────────────────────────

    def _on_middle_press(self, event):
        self._on_left_press(event)

    def _on_middle_drag(self, event):
        self._on_left_drag(event)

    def _on_middle_release(self, event):
        self._on_left_release(event)

    # ── 导航 ──────────────────────────────────────────────────────

    def _navigate(self, direction: int):
        """切换上/下一张图片，保持缩放比例。"""
        new_idx = self._current_index + direction
        if 0 <= new_idx < len(self._image_paths):
            self._current_index = new_idx
            self._load_current()

    # ── 窗口大小变化 ─────────────────────────────────────────────

    def _on_configure(self, event=None):
        """窗口大小变化时延迟重新渲染（防抖 150ms）。"""
        if event and event.widget != self:
            return
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(150, self._on_resize_debounced)

    def _on_resize_debounced(self):
        self._render_images()
        self._position_arrows()


# ══════════════════════════════════════════════════════════════════════
# 主窗口
# ══════════════════════════════════════════════════════════════════════

class PicturePressingApp(ctk.CTk):
    """主应用窗口。"""

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.minsize(460, 580)

        # 居中显示
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - WINDOW_WIDTH) // 2
        y = (sh - WINDOW_HEIGHT) // 2
        self.geometry(f"+{x}+{y}")

        # ── 顶部标题 ──
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(
            header, text="PicturePressing",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=("gray10", "gray90"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            header, text="图片批量压缩 · MozJPEG / WebP / AVIF / pngquant",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        ).pack(anchor="w")

        # ── 主区域（可滚动 — 适配不同窗口大小） ──
        self.main_content = ctk.CTkScrollableFrame(self, label_text="")
        self.main_content.pack(fill="both", expand=True, padx=10, pady=(4, 4))

        self._build_path_section()
        self._build_naming_section()
        self._build_encoder_section()
        self._build_action_section()
        self._build_progress_section()

    # ── 路径设置面板 ──────────────────────────────────────────────

    def _build_path_section(self):
        frame = self._section_frame("路径设置")

        # 输入路径
        ctk.CTkLabel(frame, text="输入文件夹", font=ctk.CTkFont(size=12),
                     text_color=("gray10", "gray90")).pack(anchor="w", pady=(0, 1))
        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x")
        self.input_entry = ctk.CTkEntry(row, placeholder_text="可粘贴路径，如 E:\\图片",
                                        text_color=("gray10", "gray90"), height=28)
        self.input_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            row, text="浏览", width=60, height=28,
            fg_color=("gray20", "gray30"), hover_color=("gray30", "gray40"),
            font=ctk.CTkFont(size=12),
            command=lambda: self._pick_folder(self.input_entry),
        ).pack(side="right")

        # 输出路径
        ctk.CTkLabel(frame, text="输出文件夹", font=ctk.CTkFont(size=12),
                     text_color=("gray10", "gray90")).pack(anchor="w", pady=(8, 1))
        row2 = ctk.CTkFrame(frame, fg_color="transparent")
        row2.pack(fill="x")
        self.output_entry = ctk.CTkEntry(row2, placeholder_text="不填则覆盖原文件",
                                         text_color=("gray10", "gray90"), height=28)
        self.output_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            row2, text="浏览", width=60, height=28,
            fg_color=("gray20", "gray30"), hover_color=("gray30", "gray40"),
            font=ctk.CTkFont(size=12),
            command=lambda: self._pick_folder(self.output_entry),
        ).pack(side="right")

        # 勾选项
        checks = ctk.CTkFrame(frame, fg_color="transparent")
        checks.pack(fill="x", pady=(6, 0))
        self.include_subfolders_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(checks, text="包含所有子文件夹", variable=self.include_subfolders_var,
                        font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 12))

        self.overwrite_var = ctk.BooleanVar(value=False)
        cb = ctk.CTkCheckBox(checks, text="覆盖原文件", variable=self.overwrite_var,
                             font=ctk.CTkFont(size=12),
                             command=self._on_overwrite_toggle)
        cb.pack(side="left")

    # ── 命名规则面板 ──────────────────────────────────────────────

    def _build_naming_section(self):
        frame = self._section_frame("命名规则")

        self.keep_name_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(frame, text="保持原来名字", variable=self.keep_name_var,
                        font=ctk.CTkFont(size=12)).pack(anchor="w")

        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(row, text="前缀", font=ctk.CTkFont(size=11),
                     text_color=("gray10", "gray90")).pack(side="left", padx=(0, 4))
        self.prefix_entry = ctk.CTkEntry(row, width=100, placeholder_text="可选",
                                         text_color=("gray10", "gray90"), height=26)
        self.prefix_entry.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(row, text="后缀", font=ctk.CTkFont(size=11),
                     text_color=("gray10", "gray90")).pack(side="left", padx=(0, 4))
        self.suffix_entry = ctk.CTkEntry(row, width=100, placeholder_text="可选",
                                         text_color=("gray10", "gray90"), height=26)
        self.suffix_entry.pack(side="left")

    # ── 编码器与压缩面板 ──────────────────────────────────────────

    def _build_encoder_section(self):
        frame = self._section_frame("编码器与压缩")

        # 编码器选择
        ctk.CTkLabel(frame, text="编码器", font=ctk.CTkFont(size=12),
                     text_color=("gray10", "gray90")).pack(anchor="w", pady=(0, 1))
        self.encoder_combo = ctk.CTkComboBox(
            frame,
            values=[_ENCODER_LABELS[e] for e in _ENCODERS_AVAILABLE],
            command=self._on_encoder_change,
            state="readonly",
            height=28,
        )
        self.encoder_combo.pack(fill="x")
        self.encoder_combo.set(_ENCODER_LABELS[_ENCODERS_AVAILABLE[0]])

        self.encoder_hint = ctk.CTkLabel(
            frame, text="编码器决定压缩算法与输出文件格式",
            font=ctk.CTkFont(size=10), text_color=("gray50", "gray60"),
        )
        self.encoder_hint.pack(anchor="w", pady=(1, 6))

        # 压缩质量
        qual_row = ctk.CTkFrame(frame, fg_color="transparent")
        qual_row.pack(fill="x")
        ctk.CTkLabel(qual_row, text="压缩质量", font=ctk.CTkFont(size=12),
                     text_color=("gray10", "gray90")).pack(side="left")
        self.quality_label = ctk.CTkLabel(qual_row, text="85", font=ctk.CTkFont(size=13, weight="bold"), width=28)
        self.quality_label.pack(side="right")

        self.quality_slider = ctk.CTkSlider(frame, from_=1, to=100, number_of_steps=99,
                                            command=self._on_quality_change)
        self.quality_slider.pack(fill="x")
        self.quality_slider.set(85)

        self.quality_hint = ctk.CTkLabel(
            frame, text="数值越小文件越小。PNG 为压缩等级（0=快速，9=最小体积）",
            font=ctk.CTkFont(size=10), text_color=("gray50", "gray60"),
        )
        self.quality_hint.pack(anchor="w", pady=(1, 6))

        # 尺寸比例
        scale_row = ctk.CTkFrame(frame, fg_color="transparent")
        scale_row.pack(fill="x")
        ctk.CTkLabel(scale_row, text="尺寸比例", font=ctk.CTkFont(size=12),
                     text_color=("gray10", "gray90")).pack(side="left")
        self.scale_label = ctk.CTkLabel(scale_row, text="100%", font=ctk.CTkFont(size=13, weight="bold"), width=36)
        self.scale_label.pack(side="right")

        self.scale_slider = ctk.CTkSlider(frame, from_=10, to=100, number_of_steps=90,
                                          command=self._on_scale_change)
        self.scale_slider.pack(fill="x")
        self.scale_slider.set(100)

        # 预览按钮
        preview_btn = ctk.CTkButton(
            frame, text="🔍 预览效果", height=32,
            fg_color=("gray20", "gray30"), hover_color=("gray30", "gray40"),
            font=ctk.CTkFont(size=12),
            command=self._on_preview,
        )
        preview_btn.pack(pady=(10, 0))

    # ── 操作按钮 ──────────────────────────────────────────────────

    def _build_action_section(self):
        frame = ctk.CTkFrame(self.main_content, fg_color="transparent")
        frame.pack(fill="x", pady=(8, 4))
        self.compress_btn = ctk.CTkButton(
            frame, text="开始压缩", height=48,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=("#1F6AA5", "#1F6AA5"), hover_color=("#155882", "#2980B9"),
            command=self._on_compress,
        )
        self.compress_btn.pack(fill="x")

    # ── 进度面板 ──────────────────────────────────────────────────

    def _build_progress_section(self):
        self.progress_frame = ctk.CTkFrame(self.main_content, fg_color="transparent")
        # 初始隐藏

        self.progress_bar = ctk.CTkProgressBar(self.progress_frame)
        self.progress_bar.set(0)

        self.progress_text = ctk.CTkLabel(
            self.progress_frame, text="", font=ctk.CTkFont(size=12),
        )

        self.log_box = ctk.CTkTextbox(self.progress_frame, height=180, font=ctk.CTkFont(size=12),
                                      wrap="word")

    # ═══════════════════════════════════════════════════════════════
    # 帮助方法
    # ═══════════════════════════════════════════════════════════════

    def _section_frame(self, title: str) -> ctk.CTkFrame:
        """创建一个带标题的分组面板。"""
        outer = ctk.CTkFrame(self.main_content)
        outer.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            outer, text=title,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("gray10", "gray90"),
        ).pack(anchor="w", padx=10, pady=(6, 0))
        inner = ctk.CTkFrame(outer, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=(0, 6))
        return inner

    def _pick_folder(self, entry: ctk.CTkEntry):
        """打开目录选择弹窗。"""
        initial = entry.get().strip()
        DirPickerDialog(self, callback=lambda p: entry.delete(0, "end") or entry.insert(0, p), initial=initial)

    def _on_overwrite_toggle(self):
        """覆盖原文件切换时更新输出路径状态。"""
        if self.overwrite_var.get():
            self.output_entry.configure(state="disabled", placeholder_text="（将覆盖原文件）")
            self.output_entry.delete(0, "end")
        else:
            self.output_entry.configure(state="normal", placeholder_text="压缩后保存位置")

    def _on_encoder_change(self, choice: str):
        enc = self._selected_encoder()
        self.quality_slider.configure(state="normal")
        if enc == "pngquant":
            # PNG 默认使用推荐压缩等级（50 = 平衡）
            self.quality_slider.set(50)
            self.quality_label.configure(text="50")
            self.quality_hint.configure(
                text="PNG 无损压缩：1=最小体积 | 50=推荐平衡 | 100=最快（文件较大）")
        else:
            self.quality_label.configure(text=str(int(self.quality_slider.get())))
            self.quality_hint.configure(
                text="数值越小文件越小。JPEG/WebP/AVIF 有损压缩")

    def _on_quality_change(self, val):
        self.quality_label.configure(text=str(int(float(val))))

    def _on_scale_change(self, val):
        self.scale_label.configure(text=f"{int(float(val))}%")

    def _selected_encoder(self) -> str:
        """从 combobox 文本中提取编码器 key。"""
        label = self.encoder_combo.get()
        for key, lbl in _ENCODER_LABELS.items():
            if lbl == label:
                return key
        return "mozjpeg"

    def _build_config(self) -> CompressConfig:
        """从 UI 控件构建压缩配置。"""
        return CompressConfig.from_dict({
            "input_path": normalize_path(self.input_entry.get().strip()),
            "output_path": normalize_path(self.output_entry.get().strip()),
            "include_subfolders": self.include_subfolders_var.get(),
            "overwrite_original": self.overwrite_var.get(),
            "keep_original_name": self.keep_name_var.get(),
            "prefix": self.prefix_entry.get().strip(),
            "suffix": self.suffix_entry.get().strip(),
            "encoder": self._selected_encoder(),
            "quality": int(self.quality_slider.get()),
            "scale_percent": int(self.scale_slider.get()),
        })

    # ═══════════════════════════════════════════════════════════════
    # 预览
    # ═══════════════════════════════════════════════════════════════

    def _on_preview(self):
        """打开可缩放、可导航的图片预览查看器。"""
        input_path = normalize_path(self.input_entry.get().strip())
        if not input_path:
            self._show_error("请先选择输入文件夹路径")
            return

        abs_path = os.path.abspath(input_path)
        if not os.path.isdir(abs_path):
            self._show_error(f"输入路径不存在或不是文件夹:\n{input_path}")
            return

        # 收集文件夹中所有图片
        image_paths: list[str] = []
        try:
            for f in sorted(os.listdir(abs_path)):
                ext = os.path.splitext(f)[1].lower()
                if ext in IMAGE_EXTENSIONS:
                    full = os.path.join(abs_path, f)
                    if os.path.isfile(full):
                        image_paths.append(full)
        except Exception as e:
            self._show_error(f"读取目录失败: {e}")
            return

        if not image_paths:
            self._show_error("该文件夹中没有找到图片文件")
            return

        # 打开可缩放查看器
        ImageViewer(
            self,
            image_paths=image_paths,
            start_index=0,
            encoder=self._selected_encoder(),
            quality=int(self.quality_slider.get()),
            scale_percent=int(self.scale_slider.get()),
        )

    # ═══════════════════════════════════════════════════════════════
    # 压缩
    # ═══════════════════════════════════════════════════════════════

    def _on_compress(self):
        """开始压缩（后台线程）。"""
        cfg = self._build_config()
        if not cfg.input_path:
            self._show_error("请选择输入文件夹路径")
            return
        if not cfg.overwrite_original and not cfg.output_path:
            self._show_error("未勾选「覆盖原文件」时，请选择输出文件夹路径")
            return

        # 显示进度面板
        self._show_progress_panel()

        self.compress_btn.configure(state="disabled", text="压缩中…")
        self.log_box.delete("1.0", "end")
        self.progress_bar.set(0)
        self.progress_text.configure(text="正在扫描图片…")

        # 后台线程
        def run():
            run_compress(cfg, emit=self._on_progress)
            # 完成后恢复按钮
            self.after(0, lambda: self.compress_btn.configure(state="normal", text="开始压缩"))

        threading.Thread(target=run, daemon=True).start()

    def _on_progress(self, msg: dict):
        """压缩进度回调（在后台线程中调用，需要 after 切回主线程）。"""
        self.after(0, lambda: self._handle_progress(msg))

    def _handle_progress(self, msg: dict):
        event = msg.get("event", "")
        if event == "start":
            total = msg.get("total", 0)
            self.progress_bar.set(0)
            self.progress_text.configure(
                text=f"共 {total} 张图片，开始处理…" if total > 0 else "未找到可处理的图片"
            )
        elif event == "progress":
            total = msg.get("total", 0)
            index = msg.get("index", 0)
            if total > 0:
                self.progress_bar.set(index / total)
            src = msg.get("source", "")
            name = os.path.basename(src) if src else ""
            self.progress_text.configure(
                text=f"正在处理 {index}/{total}{'：' + name if name else ''}"
            )
            if msg.get("ok"):
                self._log(
                    f"✓ {src}\n  → {msg.get('output', '')} "
                    f"({_fmt_size(msg.get('bytes_before', 0))} → {_fmt_size(msg.get('bytes_after', 0))})"
                )
            else:
                self._log(f"✗ {src}\n  {msg.get('error', '')}")
        elif event == "done":
            self.progress_bar.set(1)
            total = msg.get("total", 0)
            success = msg.get("success", 0)
            failed = msg.get("failed", 0)
            if msg.get("ok"):
                self.progress_text.configure(text=f"完成：共 {total} 张，成功 {success} 张，失败 {failed} 张")
            else:
                self.progress_text.configure(text=msg.get("error", "压缩失败"))
            self.compress_btn.configure(state="normal", text="开始压缩")

    def _show_progress_panel(self):
        self.progress_frame.pack(fill="x", pady=(8, 0))
        self.progress_bar.pack(fill="x", pady=(0, 6))
        self.progress_text.pack(anchor="w", pady=(0, 4))
        self.log_box.pack(fill="both", expand=True)

    def _log(self, text: str):
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")

    def _show_error(self, msg: str):
        """显示错误提示（简单弹窗）。"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("提示")
        dialog.geometry("360x150")
        dialog.after(200, dialog.lift)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog, text=msg, wraplength=300,
            font=ctk.CTkFont(size=13),
            text_color=("gray10", "gray90"),
        ).pack(pady=(24, 12), padx=20)

        ctk.CTkButton(dialog, text="确定", width=80, command=dialog.destroy).pack()


# ══════════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════════

def main():
    app = PicturePressingApp()
    app.mainloop()


if __name__ == "__main__":
    main()

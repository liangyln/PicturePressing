#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量图片压缩 — 供 Node.js 子进程调用。
支持编码器：mozjpeg, webp, avif, pngquant
用法:
  python compress_images.py --config config.json
  echo '{...}' | python compress_images.py

预览模式:
  python compress_images.py --preview
  从 stdin 读取 {file_path, encoder, quality, scale_percent}
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


# ── 代理字符清理 ──────────────────────────────────────────────────
def _clean_surrogates(val: object) -> object:
    """递归替换字符串中的孤立代理字符 (U+D800–U+DFFF) 为 U+FFFD。"""
    if isinstance(val, str):
        # 逐字符检查，避免正则对代理字符的处理问题
        cleaned: list[str] = []
        for ch in val:
            cp = ord(ch)
            if 0xD800 <= cp <= 0xDFFF:
                cleaned.append('\ufffd')
            else:
                cleaned.append(ch)
        return ''.join(cleaned)
    if isinstance(val, dict):
        return {k: _clean_surrogates(v) for k, v in val.items()}  # type: ignore[return-value]
    if isinstance(val, list):
        return [_clean_surrogates(v) for v in val]  # type: ignore[return-value]
    return val


def _safe_json_dumps(obj: object, **kwargs: object) -> str:
    """json.dumps 的安全封装：自动清理代理字符后序列化。"""
    kwargs.setdefault('ensure_ascii', False)
    return json.dumps(_clean_surrogates(obj), **kwargs)  # type: ignore[call-overload]


def normalize_path(p: str) -> str:
    """统一路径格式；避免 Windows 下 \\t 等被误当成转义。"""
    if not p:
        return p
    p = p.strip().strip('"').strip("'")
    if len(p) >= 2 and p[1] == ":":
        rest = p[2:]
        if rest[:1] in ("\t", "\n", "\r"):
            p = p[:2] + "\\" + rest[1:]
        elif rest.startswith("\\\\"):
            pass
        else:
            p = p[:2] + rest.replace("/", "\\")
    else:
        p = p.replace("/", os.sep)
    return str(Path(p))


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif",
}

# 编码器 -> (输出扩展名, Pillow 保存格式, 是否支持 quality 参数)
ENCODER_MAP = {
    "mozjpeg": (".jpg", "JPEG"),
    "webp": (".webp", "WEBP"),
    "avif": (".avif", "AVIF"),
    "pngquant": (".png", "PNG"),
}

# 有损编码器（使用 quality 参数）
LOSSY_ENCODERS = {"mozjpeg", "webp", "avif"}


def check_encoder_available(encoder: str) -> bool:
    """检测编码器在當前環境是否可用（用 1×1 像素做实際编码测试）。"""
    try:
        from PIL import Image as PilImage
        import io as _io
        test_img = PilImage.new("RGB", (1, 1), (255, 0, 0))
        buf = _io.BytesIO()
        test_img.save(buf, format=ENCODER_MAP[encoder][1], quality=80)
        return len(buf.getvalue()) > 0
    except Exception:
        return False


@dataclass
class CompressConfig:
    input_path: str
    output_path: str
    include_subfolders: bool = False
    overwrite_original: bool = False
    keep_original_name: bool = True
    prefix: str = ""
    suffix: str = ""
    encoder: str = "mozjpeg"
    quality: int = 85
    scale_percent: int = 100

    @classmethod
    def from_dict(cls, data: dict) -> "CompressConfig":
        encoder = str(data.get("encoder", data.get("output_format", "mozjpeg"))).lower()
        if encoder == "jpeg" or encoder == "jpg":
            encoder = "mozjpeg"
        if encoder not in ENCODER_MAP:
            encoder = "mozjpeg"
        return cls(
            input_path=normalize_path(str(data["input_path"])),
            output_path=normalize_path(str(data.get("output_path", ""))),
            include_subfolders=bool(data.get("include_subfolders", False)),
            overwrite_original=bool(data.get("overwrite_original", False)),
            keep_original_name=bool(data.get("keep_original_name", True)),
            prefix=str(data.get("prefix", "")),
            suffix=str(data.get("suffix", "")),
            encoder=encoder,
            quality=max(1, min(100, int(data.get("quality", 85)))),
            scale_percent=max(1, min(100, int(data.get("scale_percent", 100)))),
        )


@dataclass
class PreviewConfig:
    file_path: str
    encoder: str = "mozjpeg"
    quality: int = 85
    scale_percent: int = 100

    @classmethod
    def from_dict(cls, data: dict) -> "PreviewConfig":
        encoder = str(data.get("encoder", "mozjpeg")).lower()
        if encoder == "jpeg" or encoder == "jpg":
            encoder = "mozjpeg"
        if encoder not in ENCODER_MAP:
            encoder = "mozjpeg"
        return cls(
            file_path=normalize_path(str(data["file_path"])),
            encoder=encoder,
            quality=max(1, min(100, int(data.get("quality", 85)))),
            scale_percent=max(1, min(100, int(data.get("scale_percent", 100)))),
        )


# ── 编码器输出信息 ──────────────────────────────────────────────

def encoder_extension(encoder: str) -> str:
    return ENCODER_MAP.get(encoder, (".jpg", "JPEG"))[0]


def encoder_format(encoder: str) -> str:
    return ENCODER_MAP.get(encoder, (".jpg", "JPEG"))[1]


def encoder_is_lossy(encoder: str) -> bool:
    return encoder in LOSSY_ENCODERS


# ── stdio 配置 ──────────────────────────────────────────────────

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except OSError:
        pass


# ── 图片扫描 ────────────────────────────────────────────────────

def iter_image_files(root: Path, recursive: bool) -> Iterator[Path]:
    if not root.is_dir():
        return
    if recursive:
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
                yield p
    else:
        for p in sorted(root.iterdir()):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
                yield p


# ── 输出路径 ────────────────────────────────────────────────────

def build_output_stem(src: Path, cfg: CompressConfig) -> str:
    if cfg.keep_original_name:
        return src.stem
    return "compressed"


def resolve_output_path(
    src: Path, input_root: Path, cfg: CompressConfig, ext: str
) -> Path:
    name = f"{cfg.prefix}{build_output_stem(src, cfg)}{cfg.suffix}{ext}"

    if cfg.overwrite_original:
        return src.parent / name

    out_root = Path(cfg.output_path).resolve()
    if cfg.include_subfolders:
        try:
            rel = src.parent.relative_to(input_root)
            dest_dir = out_root / rel
        except ValueError:
            dest_dir = out_root
    else:
        dest_dir = out_root

    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir / name


# ── PIL ──────────────────────────────────────────────────────────

def _import_pil():
    try:
        from PIL import Image
        return Image
    except ImportError:
        emit_summary({
            "ok": False,
            "event": "done",
            "error": (
                "未安装 Pillow 库。请在项目目录打开终端执行：\n"
                "  python -m pip install -r requirements.txt\n"
                "若 pip 报错，请从 https://www.python.org/downloads/ 安装官方 Python（勾选 Add to PATH），不要使用 Windows 商店的 Python 占位程序。"
            ),
            "total": 0,
            "success": 0,
            "failed": 0,
            "results": [],
        })
        sys.exit(1)


def prepare_image(im, scale_percent: int, Image=None):
    if Image is None:
        Image = _import_pil()
    if scale_percent != 100:
        w, h = im.size
        nw = max(1, int(w * scale_percent / 100))
        nh = max(1, int(h * scale_percent / 100))
        im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    return im


def save_image(img, dest: Path, encoder: str, quality: int) -> None:
    """根据编码器保存图片到文件。"""
    fmt = encoder_format(encoder)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if encoder == "mozjpeg":
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        img.save(dest, format="JPEG", quality=quality, optimize=True)

    elif encoder == "webp":
        if img.mode in ("RGBA", "LA"):
            img.save(dest, format="WEBP", quality=quality, method=6, lossless=False)
        else:
            img.convert("RGB").save(dest, format="WEBP", quality=quality, method=6, lossless=False)

    elif encoder == "avif":
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        try:
            img.save(dest, format="AVIF", quality=quality)
        except (OSError, ValueError) as e:
            raise RuntimeError(
                "AVIF 编码失败。请安装 pillow-avif-plugin：\n"
                "  pip install pillow-avif-plugin\n"
                f"原始错误: {e}"
            ) from e

    elif encoder == "pngquant":
        if img.mode == "P":
            img = img.convert("RGBA")
        # quality 1→max compression(level 9), 100→fast(level 1)
        png_level = max(1, min(9, 9 - (quality - 1) * 8 // 99))
        img.save(dest, format="PNG", compress_level=png_level)

    else:
        raise ValueError(f"不支持的编码器: {encoder}")


def compress_to_bytes(img, encoder: str, quality: int) -> bytes:
    """压缩图片到内存，返回字节数据（用于预览）。"""
    buf = io.BytesIO()
    fmt = encoder_format(encoder)

    if encoder == "mozjpeg":
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=quality, optimize=True)

    elif encoder == "webp":
        if img.mode in ("RGBA", "LA"):
            img.save(buf, format="WEBP", quality=quality, method=6, lossless=False)
        else:
            img.convert("RGB").save(buf, format="WEBP", quality=quality, method=6, lossless=False)

    elif encoder == "avif":
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        try:
            img.save(buf, format="AVIF", quality=quality)
        except (OSError, ValueError) as e:
            raise RuntimeError(
                "AVIF 编码失败。请安装 pillow-avif-plugin：\n"
                "  pip install pillow-avif-plugin\n"
                f"原始错误: {e}"
            ) from e

    elif encoder == "pngquant":
        if img.mode == "P":
            img = img.convert("RGBA")
        # quality 1→max compression(level 9), 100→fast(level 1)
        png_level = max(1, min(9, 9 - (quality - 1) * 8 // 99))
        img.save(buf, format="PNG", compress_level=png_level)

    else:
        raise ValueError(f"不支持的编码器: {encoder}")

    return buf.getvalue()


def image_to_data_url(img, img_format: str = "PNG") -> str:
    """将 PIL Image 转为 base64 data URL。"""
    buf = io.BytesIO()
    if img.mode == "RGBA":
        img.save(buf, format="PNG")
    elif img.mode == "P":
        img = img.convert("RGBA")
        img.save(buf, format="PNG")
    else:
        img.save(buf, format=img_format)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    mime = "image/png" if img_format == "PNG" else f"image/{img_format.lower()}"
    return f"data:{mime};base64,{b64}"


def compress_one(src: Path, input_root: Path, cfg: CompressConfig) -> dict:
    Image = _import_pil()
    ext = encoder_extension(cfg.encoder)
    dest = resolve_output_path(src, input_root, cfg, ext)

    try:
        with Image.open(src) as im:
            im.load()
            im = prepare_image(im, cfg.scale_percent, Image)

            if cfg.overwrite_original and dest.resolve() == src.resolve():
                if src.suffix.lower() == ext.lower():
                    save_image(im, dest, cfg.encoder, cfg.quality)
                else:
                    tmp = src.with_suffix(ext + ".tmp")
                    save_image(im, tmp, cfg.encoder, cfg.quality)
                    src.unlink(missing_ok=True)
                    tmp.rename(dest)
            else:
                save_image(im, dest, cfg.encoder, cfg.quality)

        bytes_before = src.stat().st_size if src.exists() else None
        return {
            "ok": True,
            "source": str(src),
            "output": str(dest),
            "bytes_before": bytes_before,
            "bytes_after": dest.stat().st_size,
        }
    except Exception as e:
        return {"ok": False, "source": str(src), "error": str(e)}


# ── 预览生成 ────────────────────────────────────────────────────

def generate_preview(cfg: PreviewConfig) -> dict:
    """为单张图片生成预览对比数据。返回包含 data URL 的字典。"""
    Image = _import_pil()
    src = Path(cfg.file_path)

    if not src.exists():
        return {"ok": False, "error": f"文件不存在: {cfg.file_path}"}
    if not src.is_file():
        return {"ok": False, "error": f"路径不是文件: {cfg.file_path}"}
    if src.suffix.lower() not in IMAGE_EXTENSIONS:
        return {"ok": False, "error": f"不支持的图片格式: {src.suffix}"}

    try:
        with Image.open(src) as im:
            im.load()

            # 原始信息
            original_w, original_h = im.size
            original_mode = im.mode
            original_bytes = src.stat().st_size

            # 原始 data URL — 直接读文件字节，不重新编码（速度快）
            ext_to_mime = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".gif": "image/gif",
                ".bmp": "image/bmp", ".webp": "image/webp",
                ".tiff": "image/tiff", ".tif": "image/tiff",
            }
            orig_mime = ext_to_mime.get(src.suffix.lower(), "image/jpeg")
            orig_b64 = base64.b64encode(src.read_bytes()).decode("ascii")
            original_data_url = f"data:{orig_mime};base64,{orig_b64}"

            # 缩放
            processed = prepare_image(im.copy(), cfg.scale_percent, Image)
            new_w, new_h = processed.size

            # 压缩到内存
            compressed_bytes = compress_to_bytes(
                processed, cfg.encoder, cfg.quality
            )

            # 压缩后的 data URL
            enc_ext = encoder_extension(cfg.encoder)
            fmt_name = encoder_format(cfg.encoder)
            mime_map = {
                "JPEG": "image/jpeg",
                "WEBP": "image/webp",
                "AVIF": "image/avif",
                "PNG": "image/png",
            }
            mime = mime_map.get(fmt_name, "image/jpeg")
            compressed_b64 = base64.b64encode(compressed_bytes).decode("ascii")
            compressed_data_url = f"data:{mime};base64,{compressed_b64}"

            # 计算压缩比
            ratio = (1 - len(compressed_bytes) / max(original_bytes, 1)) * 100

            # 信息文本
            def fmt_size(b):
                if b < 1024:
                    return f"{b} B"
                elif b < 1024 * 1024:
                    return f"{b / 1024:.1f} KB"
                else:
                    return f"{b / (1024 * 1024):.2f} MB"

            original_info = (
                f"{original_w}×{original_h} | {original_mode} | {fmt_size(original_bytes)}"
            )
            compressed_info = (
                f"{new_w}×{new_h} | {cfg.encoder} | {fmt_size(len(compressed_bytes))}"
            )
            ratio_text = f"减小了 {ratio:.1f}%（{fmt_size(original_bytes)} → {fmt_size(len(compressed_bytes))}）" if ratio > 0 else (
                f"增大了 {-ratio:.1f}%（{fmt_size(original_bytes)} → {fmt_size(len(compressed_bytes))}）"
            )

            return {
                "ok": True,
                "original_data_url": original_data_url,
                "compressed_data_url": compressed_data_url,
                "original_info": original_info,
                "compressed_info": compressed_info,
                "ratio_text": ratio_text,
                "original_size": original_bytes,
                "compressed_size": len(compressed_bytes),
                "original_dimensions": f"{original_w}×{original_h}",
                "compressed_dimensions": f"{new_w}×{new_h}",
            }

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 输出 / 主流程 ────────────────────────────────────────────────

def emit_summary(summary: dict, emit=None) -> None:
    """Emit a summary/done message. If emit is provided, use callback; otherwise print to stdout."""
    summary = dict(summary)
    summary.setdefault("event", "done")
    if emit:
        emit(summary)
    else:
        print(_safe_json_dumps(summary), flush=True)


def run(cfg: CompressConfig, emit=None) -> dict:
    """Run compression.

    Args:
        cfg: Compression configuration.
        emit: Optional callback(msg: dict) for progress messages.
              If None, messages are printed to stdout (CLI mode).
    """
    if emit is None:
        emit = lambda msg: print(_safe_json_dumps(msg), flush=True)

    input_root = Path(cfg.input_path).resolve()
    if not input_root.exists():
        err = f"输入路径不存在: {input_root}"
    elif not input_root.is_dir():
        err = f"输入路径不是文件夹: {input_root}"
    else:
        err = None
    if err:
        summary = {
            "ok": False,
            "event": "done",
            "error": err,
            "total": 0,
            "success": 0,
            "failed": 0,
            "results": [],
        }
        emit(summary)
        return summary

    if not cfg.overwrite_original and not cfg.output_path:
        summary = {
            "ok": False,
            "event": "done",
            "error": "未勾选覆盖原文件时，必须填写输出路径",
            "total": 0,
            "success": 0,
            "failed": 0,
            "results": [],
        }
        emit(summary)
        return summary

    out_path = Path(cfg.output_path).resolve() if cfg.output_path else input_root
    if not cfg.overwrite_original and not out_path.exists():
        out_path.mkdir(parents=True, exist_ok=True)

    results = []
    files = list(iter_image_files(input_root, cfg.include_subfolders))
    total = len(files)

    emit({"event": "start", "total": total})

    for i, src in enumerate(files, 1):
        item = compress_one(src, input_root, cfg)
        item["index"] = i
        item["total"] = total
        results.append(item)
        emit({"event": "progress", **item})

    ok_count = sum(1 for r in results if r.get("ok"))
    summary = {
        "ok": True,
        "event": "done",
        "total": total,
        "success": ok_count,
        "failed": total - ok_count,
        "results": results,
    }
    emit(summary)
    return summary


def load_config() -> CompressConfig:
    parser = argparse.ArgumentParser(description="批量压缩图片")
    parser.add_argument("--config", "-c", help="JSON 配置文件路径")
    parser.add_argument("--preview", action="store_true", help="预览模式：从 stdin 读取预览参数，输出 JSON 结果")
    args = parser.parse_args()

    if args.config:
        with open(args.config, encoding="utf-8") as f:
            data = json.load(f)
    elif not sys.stdin.isatty():
        data = json.load(sys.stdin)
    else:
        print("请通过 --config 或 stdin 传入 JSON 配置", file=sys.stderr)
        sys.exit(1)

    return CompressConfig.from_dict(data)


def run_preview_mode() -> None:
    """预览模式入口：从 stdin 读取预览参数，输出 JSON 结果。"""
    if sys.stdin.isatty():
        result = {"ok": False, "error": "预览模式需要从 stdin 传入 JSON 参数"}
        print(_safe_json_dumps(result), flush=True)
        sys.exit(1)
    try:
        data = json.load(sys.stdin)
        cfg = PreviewConfig.from_dict(data)
        result = generate_preview(cfg)
        print(_safe_json_dumps(result), flush=True)
        if not result.get("ok"):
            sys.exit(1)
    except json.JSONDecodeError as e:
        result = {"ok": False, "error": f"参数 JSON 无效: {e}"}
        print(_safe_json_dumps(result), flush=True)
        sys.exit(1)
    except Exception as e:
        result = {"ok": False, "error": str(e)}
        print(_safe_json_dumps(result), flush=True)
        sys.exit(1)


def main() -> None:
    # 检查是否为预览模式
    parser = argparse.ArgumentParser(description="批量压缩图片")
    parser.add_argument("--config", "-c", help="JSON 配置文件路径")
    parser.add_argument("--preview", action="store_true", help="预览模式：从 stdin 读取预览参数，输出 JSON 结果")
    args, _ = parser.parse_known_args()

    if args.preview:
        run_preview_mode()
        return

    summary = None
    try:
        cfg = load_config()
        summary = run(cfg)
    except json.JSONDecodeError as e:
        emit_summary({
            "ok": False,
            "event": "done",
            "error": f"配置 JSON 无效: {e}",
            "total": 0,
            "success": 0,
            "failed": 0,
            "results": [],
        })
        sys.exit(1)
    except Exception as e:
        emit_summary({
            "ok": False,
            "event": "done",
            "error": str(e),
            "total": 0,
            "success": 0,
            "failed": 0,
            "results": [],
        })
        sys.exit(1)

    failed = summary.get("failed", 0)
    if not summary.get("ok") or failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
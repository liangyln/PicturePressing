# PicturePressing — 图片批量压缩工具

支持编码器：**MozJPEG**、**WebP**、**AVIF**、**pngquant**，支持缩放比例、预览对比。

## 快速开始（绿色免安装版）

1. 解压 `PicturePressing-Windows.zip` 到任意文件夹(根据widonws的版本选择PicturePressing-Windows 10-11或PicturePressing-Windows 7)
2. 双击 `PicturePressing.exe`（或 `Start.bat`）
3. 软件窗口自动打开，无需安装任何运行时

> 打包命令：`python build.py`（需 Python 3.8+ 及 PyInstaller）

## 开发运行

### 原生桌面 GUI（推荐）

```powershell
cd f:\python\PicturePressing
python -m pip install -r requirements.txt
python gui.py
```

基于 **CustomTkinter**，Windows 11 / iOS 风格现代界面：
- 自动跟随系统亮色/暗色主题
- 圆角按钮、扁平化设计
- 独立软件窗口，无需浏览器

### Web 模式（可选）

```powershell
cd f:\python\PicturePressing
python -m pip install -r requirements.txt
python server.py
```

浏览器打开：**http://localhost:3000**

> Web 模式仅需 Python 标准库 + Pillow，无需 Node.js。

## 环境准备

仅需安装 [Python 3](https://www.python.org/downloads/)（勾选 Add to PATH）。

不再需要 Node.js。项目已从 `server.js`（Node.js）迁移到 `server.py` / `gui.py`（纯 Python）。

## 功能概览

| 区域 | 说明 |
|------|------|
| **路径设置** | 输入/输出路径；支持粘贴路径或点击浏览选择；可勾选"包含所有子文件夹"和"覆盖原文件" |
| **命名规则** | 前缀 / 后缀自定义；可选保留原文件名 |
| **编码器 & 压缩** | MozJPEG / WebP / AVIF / pngquant；滑块调节质量 (1–100)；缩放比例 (10–100%) |
| **预览** | 选中参数后点击预览，弹出窗口对比原始图片与压缩效果（分辨率、文件大小、压缩比） |
| **进度** | 实时进度条 + 逐文件反馈（文件名、压缩前后大小） |

## 项目结构

| 文件 | 说明 |
|------|------|
| `gui.py` | 🆕 原生桌面 GUI（CustomTkinter，推荐使用） |
| `server.py` | Web 模式 HTTP 服务（可选，替代原 `server.js`） |
| `compress_images.py` | 压缩核心逻辑（Pillow） |
| `requirements.txt` | Python 依赖（Pillow + customtkinter） |
| `PicturePressing.spec` | PyInstaller 打包配置 |
| `build.py` | 一键构建脚本 |
| `public/` | Web 模式前端静态文件（GUI 模式不需要） |

### 已移除的文件

| 文件 | 原因 |
|------|------|
| `server.js` | 已被 `server.py` 替代 |
| `package.json` | 不再依赖 Node.js / npm |
| `package-lock.json` | 不再依赖 Node.js / npm |
| `node_modules/` | 不再依赖 Node.js / npm |

## API 端点（Web 模式）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 前端界面 |
| `GET` | `/api/browse?path=` | 浏览文件系统目录树 |
| `POST` | `/api/compress` | 启动批量压缩（NDJSON 流式） |
| `POST` | `/api/preview` | 生成单张预览 |
| `POST` | `/api/get-first-image` | 获取输入目录中第一张图片 |

## 单独测试压缩引擎

```powershell
# 命令行压缩
echo '{"input_path":"D:/test/in","output_path":"D:/test/out","include_subfolders":true,"encoder":"mozjpeg","quality":80,"scale_percent":100,"prefix":"","suffix":"","keep_original_name":true,"overwrite_original":false}' | python compress_images.py

# 预览模式
echo '{"file_path":"D:/test/in/photo.jpg","encoder":"webp","quality":85,"scale_percent":80}' | python compress_images.py --preview
```

完整配置字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `input_path` | string | 输入文件夹路径 |
| `output_path` | string | 输出文件夹路径 |
| `include_subfolders` | bool | 是否包含子文件夹 |
| `overwrite_original` | bool | 是否覆盖原文件 |
| `keep_original_name` | bool | 是否保留原文件名 |
| `prefix` | string | 文件名前缀 |
| `suffix` | string | 文件名后缀 |
| `encoder` | string | 编码器：`mozjpeg` / `webp` / `avif` / `pngquant` |
| `quality` | int | 压缩质量 1–100 |
| `scale_percent` | int | 缩放比例 1–100（100 = 原尺寸） |

## 更新日志

### v2.0 — 原生桌面 GUI

- **新增** `gui.py`：基于 CustomTkinter 的原生桌面应用，Windows 11 / iOS 风格界面
- **新增** 目录选择弹窗（面包屑导航 + 滚动列表）
- **新增** 预览对比弹窗（左右分栏显示原图与压缩效果）
- **改进** 压缩和预览直接函数调用，不再经过 HTTP，性能更好
- **改进** 无需浏览器，旧电脑也能使用
- **改进** 自动跟随系统亮色/暗色主题
- **修复** `sys.stdin` 编码问题，中文路径不再乱码
- **修改** `run()` 函数支持 `emit` 回调参数，GUI 可直接获取进度
- **保留** `server.py` Web 模式作为可选方案

### v1.0 — Web 版

- Node.js 界面 + Python 压缩引擎
- 目录树选择、预览对比、实时进度条

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PicturePressing build script.

Usage:
    python build.py              # Build and create ZIP package
    python build.py --clean      # Clean build artifacts only
    python build.py --no-zip     # Build but skip ZIP creation
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SPEC_FILE = PROJECT_DIR / "PicturePressing.spec"
DIST_DIR = PROJECT_DIR / "dist"
BUILD_DIR = PROJECT_DIR / "build"


def run(cmd, **kwargs):
    print(f"  > {' '.join(cmd)}")
    return subprocess.check_call(cmd, **kwargs)


def clean():
    """Remove build artifacts."""
    for d in [DIST_DIR, BUILD_DIR]:
        if d.exists():
            print(f"  Cleaning: {d}")
            shutil.rmtree(d, ignore_errors=True)
    for p in PROJECT_DIR.glob("*.spec.bak"):
        p.unlink()
        print(f"  Cleaning: {p}")
    print("  Clean done.")


def build(create_zip=True):
    """Run PyInstaller build and optionally create ZIP."""
    print("=" * 55)
    print("  PicturePressing - Build Script")
    print("=" * 55)
    print()

    # Ensure PyInstaller is available
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Installing PyInstaller...")
        run([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Clean old output
    clean()

    # Run PyInstaller
    print("\n[1/2] PyInstaller compiling...")
    run([
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--log-level", "WARN",
        "--noconfirm",
        str(SPEC_FILE),
    ], cwd=str(PROJECT_DIR))

    exe_path = DIST_DIR / "PicturePressing" / "PicturePressing.exe"
    if not exe_path.exists():
        print("\n[FAIL] Build failed: PicturePressing.exe not found in dist/")
        return 1

    exe_size_mb = exe_path.stat().st_size / (1024 * 1024)
    print(f"\n[OK] Build successful: {exe_path}")
    print(f"     Size: {exe_size_mb:.1f} MB")

    # Create launcher batch script
    bat_path = DIST_DIR / "PicturePressing" / "Start.bat"
    bat_path.write_text('@echo off\r\nstart "" "%~dp0PicturePressing.exe"\r\n', encoding="gbk")
    print(f"     Launcher: {bat_path}")

    if not create_zip:
        print("\nDone. (ZIP creation skipped)")
        return 0

    # Create ZIP package
    print("\n[2/2] Creating ZIP package...")
    zip_base = str(PROJECT_DIR / "PicturePressing-Windows")
    shutil.make_archive(zip_base, "zip", str(DIST_DIR / "PicturePressing"))

    zip_path = PROJECT_DIR / "PicturePressing-Windows.zip"
    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)

    print(f"\n[OK] Portable package ready: {zip_path}")
    print(f"     Size: {zip_size_mb:.1f} MB")
    print()
    print("  To distribute: Send PicturePressing-Windows.zip to users.")
    print("  To use: Unzip, double-click PicturePressing.exe or Start.bat")
    print()

    return 0


def main():
    parser = argparse.ArgumentParser(description="PicturePressing build tool")
    parser.add_argument("--clean", action="store_true", help="Only clean build artifacts")
    parser.add_argument("--no-zip", action="store_true", help="Skip ZIP creation")
    args = parser.parse_args()

    if args.clean:
        clean()
        return 0

    return build(create_zip=not args.no_zip)


if __name__ == "__main__":
    sys.exit(main())

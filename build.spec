# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — '모두의 리서치 모니터' 단일 실행파일.

빌드:  pyinstaller build.spec   (또는 build_exe.bat 더블클릭)
결과:  dist/리서치모니터.exe  (web/·legal/ 자원 포함, 소스는 바이트코드로 번들)
"""
import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# web/, legal/, notice.txt 등 런타임 자원을 함께 포함
datas = [
    ("web", "web"),
    ("legal", "legal"),
    ("notice.txt", "."),
    (".env.example", "."),
]
# .env 가 있으면 포함(없으면 무시)
if os.path.exists(".env"):
    datas.append((".env", "."))

# 동적 import(provider/uvicorn 워커 등) 누락 방지
hiddenimports = (
    collect_submodules("app")
    + collect_submodules("uvicorn")
    + ["anyio", "click", "h11"]
)

a = Analysis(
    ["app_main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide6"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ResearchMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,            # UPX 가 설치돼 있으면 실행파일 추가 압축
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,        # 콘솔 창(로그·종료 안내). 숨기려면 False
    disable_windowed_traceback=False,
    icon="web/icon-512.png" if os.path.exists("web/icon-512.png") else None,
)

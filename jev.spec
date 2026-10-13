# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包定义，由本地锁定发布脚本和 build.bat 共用。
onedir 不是 onefile：PySide6 + onnxruntime 打出来 ~150MB，onefile 每次启动都要解压一遍，慢且占临时盘。
只在 Windows 上跑，下面的 collect_all 也只认 Windows 上装好的那几个包。"""
from PyInstaller.utils.hooks import collect_all

NAME = "jev-chat-windows"

hiddenimports = [
    # 手动读取、预览、会话清理和填入路径有延迟导入；显式列出以便打包器保留它们。
    "app.capture", "app.manual_capture", "app.ocr", "app.fill", "app.fill_target", "app.overlay", "app.settings",
    "app.credentials", "app.privacy", "app.session", "app.preview_model", "app.result_view",
    "app.freshness", "app.visible_chat", "app.conversation_timeline",
    "app.single_instance", "app.version",
    "core.engine", "core.draft", "core.jev_client", "core.questions", "core.service_error",
    "core.adaptive_analysis",
]
datas, binaries = [], []
for pkg in (
    "rapidocr_onnxruntime",  # .onnx 模型 + config.yaml 是包数据，不收就是启动即炸
    "onnxruntime",           # capi 下面那堆 DLL
    "qfluentwidgets",        # qss / 图标资源
    "windows_capture",       # Rust 编译的 .pyd
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

excludes = [
    # 确认没人用：rapidocr 只 import 了 cv2 / PIL / yaml / pyclipper / shapely（PIL 千万别排，读图要它）
    "tkinter", "matplotlib", "scipy", "pandas",
] + ["PySide6." + m for m in (
    # 留着 QtCore / QtGui / QtWidgets / QtSvg / QtSvgWidgets / QtXml —— import qfluentwidgets 实测就这六个
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel",
    "QtMultimedia", "QtMultimediaWidgets", "QtCharts", "QtDataVisualization",
    "QtQuick", "QtQuick3D", "QtQuickControls2", "QtQuickWidgets", "QtQuickTest", "QtQml",
    "QtPdf", "QtPdfWidgets", "QtBluetooth", "QtNfc", "QtSensors", "QtSerialPort",
    "QtTest", "QtDesigner", "QtHelp", "QtRemoteObjects", "QtScxml", "QtStateMachine",
    "QtTextToSpeech", "QtPositioning", "QtLocation", "QtSql",
    "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation", "Qt3DExtras",
)]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # runner 上本来就没 upx，而且压 Qt / onnxruntime 的 DLL 是出了名的能压坏
    console=False,  # 不要黑框；print 也就跟着没了，状态界面上都有，聊天内容本来就不许落日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="docs/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=NAME,
)

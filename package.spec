# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

project_dir = Path(SPECPATH).resolve()

datas = [
    (str(project_dir / 'src' / 'web' / 'templates'), 'src/web/templates'),
    (str(project_dir / 'src' / 'web' / 'static'), 'src/web/static'),
    (str(project_dir / 'config' / 'config.json'), 'config'),
]
# ffmpeg/ffprobe are NOT bundled here: build_windows.py copies bin/ next to the exe, which is where
# config_loader looks first. Bundling them in _internal as well shipped two ~100 MB copies.

hidden_imports = [
    'waitress',
    'webview',
    'webview.platforms.winforms',
    'webview.platforms.edgechromium',
    'mutagen',
    'mutagen.id3',
    'mutagen.flac',
    'mutagen.oggvorbis',
    'mutagen.mp4',
    'mutagen.wave',
    'yt_dlp',
    'spotipy',
    'librespot',
    'rich',
    'requests',
    'sqlite3',
    'ctypes',
    'ctypes.wintypes',
    'watchdog',
    'watchdog.observers',
    'watchdog.observers.winapi',
    'watchdog.observers.polling',
    'watchdog.events',
    'mcp',
    'mcp.server',
    'mcp.server.mcpserver',
    'mcp.server.sse',
    'mcp.server.stdio',
    'uvicorn',
    'starlette',
    'anyio',
    'src.providers.ytmusic_provider',
    'src.providers.youtube_provider',
    'src.source_resolution',
    'src.unified_search',
    'src.cross_source_matcher',
    'src.playback_capability',
    'src.mcp_server.server',
    'src.web.state',
    'src.maintenance_service',
    'src.logging_setup',
    'src.http_client',
    'src.lyrics_service',
    'src.version',
    'src.web.routes.system',
    'src.web.routes.downloads',
    'src.web.routes.library',
    'src.web.routes.pages',
    'src.web.routes.automation',
    'src.web.routes.audio_lab',
]

a = Analysis(
    ['main.py'],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SpotifyDownloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed app: opening it shows only the UI (--version/--diagnostics reattach to the parent terminal; see main.py)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
    icon=str(project_dir / 'assets' / 'icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SpotifyDownloader',
)

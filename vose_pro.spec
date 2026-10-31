# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import pyopenjtalk

block_cipher = None

# --- 1. 辞書とDLL/dylibの場所を自動特定 ---
added_files = []

# pyopenjtalkの辞書取得
try:
    pyj_dir = os.path.dirname(pyopenjtalk.__file__)
    dic_path = os.path.join(pyj_dir, "dic")
    if os.path.exists(dic_path):
        added_files.append((dic_path, 'pyopenjtalk/dic'))
except Exception as e:
    print(f"DEBUG: Dictionary error: {e}")

# OSに応じたCエンジンのバイナリ判定
if sys.platform == 'win32':
    dll_name = 'vose_core.dll'
elif sys.platform == 'darwin':
    dll_name = 'libvose_core.dylib'
else:
    dll_name = 'libvose_core.so'

dll_path = os.path.join('bin', dll_name)
if os.path.exists(dll_path):
    # 'bin'フォルダとしてEXE内にパッキング
    added_files.append((dll_path, 'bin'))
    print(f"DEBUG: Added Engine Binary from {dll_path}")

# テーマQSSとアイコンSVGを同梱（themes.py / icons.py が実行時に参照）
for _src, _dst in (('assets/themes', 'assets/themes'), ('assets/icons', 'assets/icons')):
    if os.path.isdir(_src):
        added_files.append((_src, _dst))

# --- 2. ビルド設定 ---
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=added_files,
    hiddenimports=[
        'pyopenjtalk', 
        'numpy', 
        'PySide6',
        'rtmidi',
        'mido',              # 🔥 mido本体を追加
        'mido.backends',     # 🔥 バックエンド全体を追加
        'mido.backends.rtmidi',
        'modules.gui.mixins._mixin_base'
    ],
    # 🔥 さらに collect-all でサブモジュールを強制収集
    hookspath=[],
    hooksconfig={
        'mido': {
            'collect_submodules': ['mido.backends']
        }
    },
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VO-SE_Pro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True, # デバッグ用にTrueにしています。完成後はFalseでGUIのみにできます。
    icon=None,    # アイコンがあればここにパスを指定
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VO-SE_Pro', # ここが dist/ フォルダの中に作られるフォルダ名
)

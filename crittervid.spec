# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

block_cipher = None

# Collect metadata for packages that need it
datas = copy_metadata('streamlit')
datas += copy_metadata('altair')
datas += copy_metadata('pillow')
datas += copy_metadata('pandas')
datas += copy_metadata('numpy')

# Collect streamlit data files
datas += collect_data_files('streamlit')

# Add our scripts
datas += [
    ('app.py', '.'),
    ('ffmpeg_helper.py', '.'),
    ('convert_media.py', '.'),
    ('merge_videos.py', '.'),
    ('validate_output.py', '.'),
    ('process_all.py', '.'),
]

# Add Streamlit config
datas += [
    ('.streamlit/config.toml', '.streamlit'),
]

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[
        ('ffmpeg.exe', '.'),  # Bundle ffmpeg.exe if it exists in project root
    ],
    datas=datas,
    hiddenimports=[
        'streamlit',
        'streamlit.runtime.scriptrunner.magic_funcs',
        'streamlit.web.cli',
        'streamlit.web.bootstrap',
        'streamlit.runtime.caching.storage.dummy_cache_storage',
        'streamlit.runtime.legacy_caching.caching',
        'GPUtil',
        'wmi',
        'tkinter',
        'tkinter.filedialog',
        'click',
        'toml',
        'validators',
        'watchdog',
    ],
    hookspath=[],
    hooksconfig={},
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
    name='CritterVid',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Keep console for debugging
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Add icon path if you have one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CritterVid',
)
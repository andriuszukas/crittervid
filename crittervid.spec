# -*- mode: python ; coding: utf-8 -*-
import os
import sys

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

# The Streamlit app is run from its file, so it ships as one, next to the
# crittervid.gui modules (the rest of the package is compiled into the app)
datas += [
    ('src/crittervid/gui/streamlit_app.py', 'crittervid/gui'),
]

# Add Streamlit config
datas += [
    ('.streamlit/config.toml', '.streamlit'),
]

# Bundle FFmpeg only if it's in the project root. It's gitignored, so a fresh
# clone won't have it; the app then uses FFmpeg from PATH at runtime. Look next
# to this spec (SPECPATH), which is where PyInstaller resolves the other entries,
# so the result doesn't depend on the folder the build is started from.
exe = '.exe' if sys.platform == 'win32' else ''
ffmpeg_path = os.path.join(SPECPATH, 'ffmpeg' + exe)
binaries = []
if os.path.isfile(ffmpeg_path):
    binaries.append((ffmpeg_path, '.'))
    # ffprobe (shipped with FFmpeg) lets merges end each file exactly where its video ends
    ffprobe_path = os.path.join(SPECPATH, 'ffprobe' + exe)
    if os.path.isfile(ffprobe_path):
        binaries.append((ffprobe_path, '.'))
    else:
        print(f'WARNING: ffprobe{exe} not found in {SPECPATH}; merged files may drift slightly.')
else:
    print(f'WARNING: ffmpeg{exe} not found in {SPECPATH}; building without it. '
          'Users will need FFmpeg on their PATH.')

a = Analysis(
    ['src/crittervid/__main__.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        # The crittervid command imports each command's module by name
        'crittervid.pipeline',
        'crittervid.convert',
        'crittervid.merge',
        'crittervid.validate',
        'crittervid.gui.launch',
        'crittervid.doctor',
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
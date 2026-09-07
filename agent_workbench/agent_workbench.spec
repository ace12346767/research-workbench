from pathlib import Path
from importlib.metadata import distribution

project_dir = Path(SPECPATH)
repo_dir = project_dir.parent

datas = [
    (str(project_dir / "assets"), "agent_workbench/assets"),
    (str(project_dir / "router" / "anchors.json"), "agent_workbench/router"),
    (str(project_dir / "THIRD_PARTY_NOTICES.md"), "."),
]
binaries = []
for package in ('python-docx', 'openpyxl', 'xlrd', 'pillow', 'defusedxml', 'lxml', 'et-xmlfile'):
    dist = distribution(package)
    for entry in dist.files or ():
        if any(token in str(entry).lower() for token in ('license', 'copying', 'notice')):
            source = Path(dist.locate_file(entry))
            if source.is_file():
                datas.append((str(source), 'licenses/' + package + '/' + str(entry.parent)))
hiddenimports = [
    "keyring.backends.Windows",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
]

a = Analysis(
    [str(project_dir / "app.py")],
    pathex=[str(repo_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "notebook", "IPython"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AgentWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(project_dir / 'assets' / 'AgentWorkbench.ico'),
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AgentWorkbench",
)

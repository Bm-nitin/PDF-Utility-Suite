from pathlib import Path
from runpy import run_path

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VSVersionInfo,
    VarFileInfo, VarStruct,
)

project = Path(SPECPATH).parent
app_config = run_path(str(project / "config.py"))
app_version = app_config["APP_VERSION"]
numeric_version = (*map(int, app_version.split(".")), 0)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numeric_version, prodvers=numeric_version),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("FileDescription", app_config["APP_NAME"]),
            StringStruct("FileVersion", app_version),
            StringStruct("InternalName", "PDF-Merger-Compressor"),
            StringStruct("OriginalFilename", "PDF-Merger-Compressor.exe"),
            StringStruct("ProductName", "PDF Utility Suite"),
            StringStruct("ProductVersion", app_version),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

assets = project / "assets"
datas, binaries, hiddenimports = collect_all("pymupdf")
datas.append((str(assets), "assets"))
analysis = Analysis(
    [str(project / "app.py")],
    pathex=[str(project)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
)
exe = EXE(
    PYZ(analysis.pure),
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="PDF-Merger-Compressor",
    console=False,
    icon=str(assets / "icon.ico"),
    version=version_info,
)

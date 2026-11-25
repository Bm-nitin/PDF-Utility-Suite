@echo off
setlocal

REM ============================================================================
REM PDF Utility Suite - TEST BUILD
REM
REM Usage:
REM   build.bat
REM   build.bat onedir
REM   build.bat onefile
REM
REM Default: onedir
REM ============================================================================

pushd "%~dp0"

set "APP_NAME=PDF-Utility-Suite"
set "ENTRY_POINT=app.py"
set "PYTHON=.venv\Scripts\python.exe"
set "MODE=onedir"

if /I "%~1"=="onefile" set "MODE=onefile"
if /I "%~1"=="onedir"  set "MODE=onedir"

echo.
echo ============================================================
echo   PDF Utility Suite - TEST BUILD
echo ============================================================
echo   Mode: %MODE%
echo   Root: %CD%
echo ============================================================
echo.

REM ---------------------------------------------------------------------------
REM Check Python
REM ---------------------------------------------------------------------------

if not exist "%PYTHON%" (
    echo [ERROR] Virtual environment Python not found:
    echo         %PYTHON%
    goto :fail
)

if not exist "%ENTRY_POINT%" (
    echo [ERROR] Entry point not found:
    echo         %ENTRY_POINT%
    goto :fail
)

echo [1/4] Checking PyInstaller...

"%PYTHON%" -m PyInstaller --version

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller is not installed in .venv.
    echo.
    echo Install dependencies with:
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    goto :fail
)

echo.
echo [OK] PyInstaller available.

REM ---------------------------------------------------------------------------
REM Clean TEST build output
REM ---------------------------------------------------------------------------

echo.
echo [2/4] Cleaning previous test build...

if exist "build\%APP_NAME%" (
    rmdir /S /Q "build\%APP_NAME%"
)

if exist "dist\%APP_NAME%" (
    rmdir /S /Q "dist\%APP_NAME%"
)

if exist "dist\%APP_NAME%.exe" (
    del /Q "dist\%APP_NAME%.exe"
)

REM ---------------------------------------------------------------------------
REM Optional icon
REM ---------------------------------------------------------------------------

set "ICON_ARG="

if exist "assets\icon.ico" (
    set "ICON_ARG=--icon assets\icon.ico"
    echo [OK] Icon found: assets\icon.ico
) else (
    echo [INFO] No icon found. Building without custom icon.
)

REM ---------------------------------------------------------------------------
REM Optional assets
REM ---------------------------------------------------------------------------

set "DATA_ARG="

if exist "assets\" (
    set "DATA_ARG=--add-data assets;assets"
    echo [OK] Assets folder will be bundled.
) else (
    echo [INFO] No assets folder found.
)

REM ---------------------------------------------------------------------------
REM Select PyInstaller mode
REM ---------------------------------------------------------------------------

set "MODE_ARG=--onedir"

if /I "%MODE%"=="onefile" (
    set "MODE_ARG=--onefile"
)

REM ---------------------------------------------------------------------------
REM Build
REM ---------------------------------------------------------------------------

echo.
echo [3/4] Building application...
echo.
echo ------------------------------------------------------------

"%PYTHON%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --windowed ^
    %MODE_ARG% ^
    --name "%APP_NAME%" ^
    --collect-all pymupdf ^
    %ICON_ARG% ^
    %DATA_ARG% ^
    "%ENTRY_POINT%"

if errorlevel 1 (
    echo.
    echo ------------------------------------------------------------
    echo [ERROR] PyInstaller build FAILED.
    echo ------------------------------------------------------------
    goto :fail
)

REM ---------------------------------------------------------------------------
REM Result
REM ---------------------------------------------------------------------------

echo.
echo [4/4] BUILD SUCCESSFUL
echo ============================================================

if /I "%MODE%"=="onefile" (
    echo.
    echo EXE:
    echo dist\%APP_NAME%.exe
    echo.
    echo Run with:
    echo dist\%APP_NAME%.exe
) else (
    echo.
    echo EXE:
    echo dist\%APP_NAME%\%APP_NAME%.exe
    echo.
    echo Run with:
    echo dist\%APP_NAME%\%APP_NAME%.exe
)

echo.
echo ============================================================
echo TEST BUILD ONLY
echo Do not distribute this build yet.
echo ============================================================
echo.

popd
endlocal
exit /b 0

:fail
echo.
echo ============================================================
echo BUILD FAILED
echo ============================================================
echo.

popd
endlocal
exit /b 1

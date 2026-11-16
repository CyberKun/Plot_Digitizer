@echo off
setlocal
set "APP_DIR=%~dp0"
set "NATIVE_PYTHON=%APP_DIR%python314\python.exe"
if not exist "%NATIVE_PYTHON%" (
    echo Extract the complete release ZIP. The bundled Python runtime is missing.
    pause
    exit /b 1
)
if not exist "%APP_DIR%runtime_deps\py314\numpy" goto install_runtime
if not exist "%APP_DIR%runtime_deps\py314\cv2" goto install_runtime
goto ready
:install_runtime
if not exist "%APP_DIR%wheelhouse_py314" (
    echo Re-extract the ready package or use the offline-wheels package.
    pause
    exit /b 1
)
call "%APP_DIR%install_dependencies.bat"
if errorlevel 1 exit /b 1
:ready
"%NATIVE_PYTHON%" -I -B "%APP_DIR%api_launcher.py" %*
exit /b %ERRORLEVEL%

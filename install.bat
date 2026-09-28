@echo off
rem ============================================================================
rem  QuickDestruction extension installer
rem
rem  Usage:
rem    install.bat <blender.exe>            build, install and enable
rem    install.bat <blender.exe> /nopause   do not wait for a key press at the end
rem    set BLENDER=<blender.exe>            the path can also come from BLENDER
rem
rem  Steps, all done by the given Blender so the package always goes into the
rem  configuration of that Blender version:
rem    1. build the package from .\source into a temporary directory
rem    2. install it into the user_default repository and enable it
rem    3. verify with scripts\install_verify.py that every file matches the
rem       package and that the extension is enabled and registered
rem  Blender's "extension install-file" prints errors but always exits with 0,
rem  so step 3 is what decides success
rem  Output is plain ASCII, which keeps it code-page independent
rem ============================================================================

setlocal EnableExtensions
set "ROOT=%~dp0"
set "PAUSE_AT_END=1"
set "RESULT=1"
set "BUILD="
set "PACKAGE="

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="/nopause" (set "PAUSE_AT_END=") else (set "BLENDER=%~1")
shift
goto parse_args
:args_done

if not defined BLENDER (
  echo [ERROR] Pass the Blender executable, for example: install.bat "C:\Blender\blender.exe"
  goto finish
)
if not exist "%BLENDER%" (
  echo [ERROR] Blender executable not found: %BLENDER%
  goto finish
)

echo [1/3] Building the extension package
set "BUILD=%TEMP%\qd_build_%RANDOM%%RANDOM%"
mkdir "%BUILD%"
"%BLENDER%" -c extension build --source-dir="%ROOT%source" --output-dir="%BUILD%"
if errorlevel 1 goto failed
for %%F in ("%BUILD%\*.zip") do set "PACKAGE=%%F"
if not defined PACKAGE (
  echo [ERROR] The build produced no package
  goto failed
)

echo [2/3] Installing and enabling
"%BLENDER%" -c extension install-file -r user_default -e "%PACKAGE%"

echo [3/3] Verifying the installation
"%BLENDER%" -b --python-exit-code 1 --python "%ROOT%scripts\install_verify.py" -- "%PACKAGE%"
if errorlevel 1 goto failed

echo INSTALL SUCCEEDED
echo Restart Blender to load the new code.
set "RESULT=0"
goto finish

:failed
echo INSTALL FAILED

:finish
if defined BUILD if exist "%BUILD%" rd /s /q "%BUILD%"
if defined PAUSE_AT_END (
  echo.
  echo Press any key to close this window . . .
  pause >nul
)
endlocal & exit /b %RESULT%

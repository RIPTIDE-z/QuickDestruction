@echo off
rem ============================================================================
rem  QuickDestruction extension installer
rem
rem  Usage:
rem    install.bat             auto-select the highest Blender config version
rem    install.bat 5.2         use the given Blender config version directory
rem    install.bat 5.2 -Force  overwrite an existing install without asking
rem    install.bat /nopause    do not wait for a key press at the end
rem
rem  Flow: find and validate ZIP -> pick Blender config -> extract to a stage
rem        directory -> verify file count -> back up the old install ->
rem        publish -> roll back on failure
rem
rem  If an installation already exists and -Force was not given, the script asks
rem  before replacing it The default answer is yes: pressing Enter replaces the
rem  existing installation Only Enter or y/Y continues; other input cancels
rem
rem  Only cmd builtins and the tar.exe shipped with Windows are used, so there
rem  is no PowerShell dependency Every line of output is plain ASCII, which
rem  makes it code-page independent
rem ============================================================================

setlocal EnableExtensions EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
set "TARGET_VERSION="
set "FORCE="
set "PAUSE_AT_END=1"
rem Cleanup may only use paths created by this invocation, never inherited variables
set "MANIFEST="
set "STAGE="
set "BACKUP="

rem ------------------------------------------------------------- argument parse
:parse_args
if "%~1"=="" goto args_done
set "ARG=%~1"
if /i "%ARG%"=="-Force"   set "FORCE=1" & shift & goto parse_args
if /i "%ARG%"=="/Force"   set "FORCE=1" & shift & goto parse_args
if /i "%ARG%"=="/nopause" set "PAUSE_AT_END=" & shift & goto parse_args
if /i "%ARG%"=="nopause"  set "PAUSE_AT_END=" & shift & goto parse_args
rem Anything else is taken as the Blender version number
set "TARGET_VERSION=%ARG%"
shift
goto parse_args
:args_done

if defined TARGET_VERSION (
  echo %TARGET_VERSION%| findstr /r "^[0-9][0-9]*\.[0-9][0-9]*$" >nul
  if errorlevel 1 (
    echo [ERROR] Blender version must use major.minor format, got: %TARGET_VERSION%
    set "RESULT=1"
    goto finish
  )
)

rem --------------------------------------------- 1/6 find and validate the package
echo [1/6] Locating and validating the extension package
set "PACKAGE="
call :find_package
if not defined PACKAGE (
  echo [ERROR] No valid QuickDestruction ZIP was found.
  echo         Searched: script directory, parent directory.
  echo         Build it first, for example:
  echo           blender -c extension build --source-dir=.\source
  set "RESULT=1"
  goto finish
)

set "PKG_ID="
set "PKG_VERSION="
set "PKG_MINIMUM="
set "MANIFEST=%TEMP%\qd_manifest_%RANDOM%%RANDOM%.txt"
tar -xOf "%PACKAGE%" ./blender_manifest.toml >"%MANIFEST%" 2>nul
if errorlevel 1 (
  echo [ERROR] Cannot read blender_manifest.toml from the package.
  set "RESULT=1"
  goto finish
)
rem Only the three top-level scalar fields we need are selected, so section
rem headers such as [permissions] can never be misread as a key
rem Key and value are split on the first '=' and each side is stripped of
rem spaces, quotes and a possible UTF-8 BOM without using a nested for loop
for /f "usebackq tokens=1,2 delims==" %%A in ("%MANIFEST%") do (
  set "KEY=%%A"
  set "VALUE=%%B"
  set "KEY=!KEY: =!"
  set "KEY=!KEY:"=!"
  set "VALUE=!VALUE: =!"
  set "VALUE=!VALUE:"=!"
  if /i "!KEY!"=="id"                  set "PKG_ID=!VALUE!"
  if /i "!KEY!"=="version"             set "PKG_VERSION=!VALUE!"
  if /i "!KEY!"=="blender_version_min" set "PKG_MINIMUM=!VALUE!"
)
del "%MANIFEST%" >nul 2>&1

if not "!PKG_ID!"=="quick_destruction" (
  echo [ERROR] Package is not quick_destruction. Found id: !PKG_ID!
  set "RESULT=1"
  goto finish
)
rem Version fields must look like x.y.z
echo !PKG_VERSION!| findstr /r "^[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*$" >nul
if errorlevel 1 (
  echo [ERROR] Invalid manifest field: version = !PKG_VERSION!
  set "RESULT=1"
  goto finish
)
echo !PKG_MINIMUM!| findstr /r "^[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*$" >nul
if errorlevel 1 (
  echo [ERROR] Invalid manifest field: blender_version_min = !PKG_MINIMUM!
  set "RESULT=1"
  goto finish
)
echo   Package: %PACKAGE%
echo   Extension: !PKG_ID! !PKG_VERSION!
echo   Minimum Blender: !PKG_MINIMUM!

rem ------------------------------------------------ 2/6 pick the config and target
echo [2/6] Selecting the Blender configuration
set "CONFIG_ROOT=%APPDATA%\Blender Foundation\Blender"
if not exist "%CONFIG_ROOT%" (
  echo [ERROR] No Blender configuration directory was found.
  set "RESULT=1"
  goto finish
)

if not defined TARGET_VERSION (
  rem Compare versions numerically Sorting the names as strings would put
  rem 5.10 before 5.9
  set "BEST_MAJOR=0"
  set "BEST_MINOR=0"
  for /d %%D in ("%CONFIG_ROOT%\*") do (
    set "NAME=%%~nxD"
    echo !NAME!| findstr /r "^[0-9][0-9]*\.[0-9][0-9]*$" >nul
    if not errorlevel 1 (
      for /f "tokens=1,2 delims=." %%X in ("!NAME!") do (
        set /a "CUR_MAJOR=%%X"
        set /a "CUR_MINOR=%%Y"
        if !CUR_MAJOR! GTR !BEST_MAJOR! (
          set "BEST_MAJOR=!CUR_MAJOR!"
          set "BEST_MINOR=!CUR_MINOR!"
        ) else if !CUR_MAJOR! EQU !BEST_MAJOR! (
          if !CUR_MINOR! GTR !BEST_MINOR! set "BEST_MINOR=!CUR_MINOR!"
        )
      )
    )
  )
  if !BEST_MAJOR! EQU 0 (
    echo [ERROR] No Blender configuration version was found.
    set "RESULT=1"
    goto finish
  )
  set "TARGET_VERSION=!BEST_MAJOR!.!BEST_MINOR!"
)

set "CONFIG_DIR=%CONFIG_ROOT%\%TARGET_VERSION%"
if not exist "%CONFIG_DIR%" (
  echo [ERROR] Blender configuration does not exist: %CONFIG_DIR%
  set "RESULT=1"
  goto finish
)

rem Reject a configuration older than the minimum the package declares
for /f "tokens=1,2 delims=." %%X in ("%TARGET_VERSION%") do (
  set "CFG_MAJOR=%%X"
  set "CFG_MINOR=%%Y"
)
for /f "tokens=1,2 delims=." %%X in ("!PKG_MINIMUM!") do (
  set "MIN_MAJOR=%%X"
  set "MIN_MINOR=%%Y"
)
set "TOO_OLD="
if !CFG_MAJOR! LSS !MIN_MAJOR! set "TOO_OLD=1"
if !CFG_MAJOR! EQU !MIN_MAJOR! if !CFG_MINOR! LSS !MIN_MINOR! set "TOO_OLD=1"
if defined TOO_OLD (
  echo [ERROR] Blender %TARGET_VERSION% is below the package minimum !PKG_MINIMUM!.
  set "RESULT=1"
  goto finish
)

set "REPOSITORY=%CONFIG_DIR%\extensions\user_default"
set "DESTINATION=%REPOSITORY%\!PKG_ID!"
echo   Blender configuration: %TARGET_VERSION%
echo   Destination: %DESTINATION%

if exist "%DESTINATION%" if not defined FORCE (
  echo.
  echo   An installation already exists at:
  echo     %DESTINATION%
  rem Default is yes: pressing Enter with no input replaces the existing install
  rem Only Enter or y/Y continues; all other input cancels
  rem The closing paren is escaped with ^ because a bare ")" inside an if block
  rem would be taken as the end of the block and would never be printed
  echo   Replace the installed extension? [Y/n]  (Enter = Y, replace; N = cancel^)
  set "ANSWER="
  set /p "ANSWER=  > "
  if not "!ANSWER!"=="" if /i not "!ANSWER!"=="y" (
    echo INSTALL CANCELLED
    set "RESULT=0"
    goto finish
  )
)

if not exist "%REPOSITORY%" mkdir "%REPOSITORY%" >nul 2>&1
if not exist "%REPOSITORY%" (
  echo [ERROR] Cannot create the extension repository: %REPOSITORY%
  set "RESULT=1"
  goto finish
)

rem ------------------------------------------------- 3/6 extract into a stage dir
echo [3/6] Extracting to a temporary directory
set "STAGE=%REPOSITORY%\.quick_destruction-stage-%RANDOM%%RANDOM%"
mkdir "%STAGE%" >nul 2>&1
if not exist "%STAGE%" (
  echo [ERROR] Cannot create the staging directory: %STAGE%
  set "RESULT=1"
  goto finish
)
tar -xf "%PACKAGE%" -C "%STAGE%"
if errorlevel 1 (
  echo [ERROR] Extraction failed.
  set "RESULT=1"
  goto rollback
)

rem --------------------------------------------------- 4/6 verify what came out
echo [4/6] Verifying extracted files
if not exist "%STAGE%\blender_manifest.toml" (
  echo [ERROR] Extracted package is missing blender_manifest.toml.
  set "RESULT=1"
  goto rollback
)
if not exist "%STAGE%\__init__.py" (
  echo [ERROR] Extracted package is missing __init__.py.
  set "RESULT=1"
  goto rollback
)
set "ACTUAL_FILES=0"
for /f %%C in ('dir /a-d /b /s "%STAGE%" 2^>nul ^| find /c /v ""') do set "ACTUAL_FILES=%%C"
if !ACTUAL_FILES! LSS 2 (
  echo [ERROR] Extracted package looks empty.
  set "RESULT=1"
  goto rollback
)
echo   Verified: !ACTUAL_FILES! files

rem ------------------------------------------------------ 5/6 back up and publish
echo [5/6] Replacing the installed extension
set "BACKUP="
if exist "%DESTINATION%" (
  set "BACKUP=%REPOSITORY%\.quick_destruction-backup-%RANDOM%%RANDOM%"
  move "%DESTINATION%" "!BACKUP!" >nul
  if errorlevel 1 (
    echo [ERROR] Cannot move the existing installation aside.
    set "BACKUP="
    set "RESULT=1"
    goto rollback
  )
  echo   Backup: !BACKUP!
)
move "%STAGE%" "%DESTINATION%" >nul
if errorlevel 1 (
  echo [ERROR] Cannot publish the staged files.
  set "RESULT=1"
  goto rollback
)
set "STAGE="

if defined BACKUP (
  rd /s /q "!BACKUP!" >nul 2>&1
  if exist "!BACKUP!" (
    echo   [WARNING] Backup retained: !BACKUP!
  )
)
set "BACKUP="

rem ------------------------------------------------------------------ 6/6 done
echo [6/6] Installation complete
echo   Installed: !PKG_ID! !PKG_VERSION!
echo   Path: %DESTINATION%
echo INSTALL SUCCEEDED
echo.
echo A new extension is only scanned when Blender starts.
echo In a running Blender use: Edit - Preferences - Add-ons - Refresh Local Repository,
echo then search for "quick_destruction" and enable it.
set "RESULT=0"
goto finish

rem ------------------------------------------------------------- failure rollback
:rollback
if defined STAGE if exist "%STAGE%" rd /s /q "%STAGE%" >nul 2>&1
if defined BACKUP if exist "%BACKUP%" (
  if exist "%DESTINATION%" rd /s /q "%DESTINATION%" >nul 2>&1
  move "%BACKUP%" "%DESTINATION%" >nul 2>&1
  if exist "%BACKUP%" (
    echo [ERROR] Restore failed. Backup retained at: %BACKUP%
  ) else (
    echo   Previous installation restored.
  )
)
echo INSTALL FAILED
set "RESULT=1"

:finish
if defined MANIFEST if exist "%MANIFEST%" del "%MANIFEST%" >nul 2>&1
if "%PAUSE_AT_END%"=="1" (
  echo.
  echo Press any key to close this window . . .
  pause >nul
)
endlocal & exit /b %RESULT%

rem ------------------------------------------------------------------ subroutines
rem Search the script directory and its parent, newest first, and accept the first
rem ZIP whose manifest declares quick_destruction The documented build command
rem writes the package into the project root, so there is no build subdirectory to
rem look in The listing is read once per directory into numbered variables:
rem expanding %%F and !TRY! inside a nested for block proved unreliable, so
rem candidates are processed by index
:find_package
for %%D in ("%SCRIPT_DIR%." "%SCRIPT_DIR%..") do call :try_directory "%%~fD"
exit /b 0

:try_directory
rem The script directory has priority over its parent, even if the parent is newer
if defined PACKAGE exit /b 0
set "TRY_DIR=%~1"
if not exist "%TRY_DIR%" exit /b 0
rem Reset the count per directory; old slots beyond this count are never read
set "DIR_INDEX=0"
set "TRY_LIST="
for /f "delims=" %%F in ('dir /b /a-d /o-d "%TRY_DIR%\*.zip" 2^>nul') do (
  set /a "DIR_INDEX+=1"
  set "TRY_!DIR_INDEX!=%%F"
  set "TRY_LIST=1"
)
if not defined TRY_LIST exit /b 0
set "INDEX=0"
:try_next
set /a "INDEX+=1"
if %INDEX% GTR %DIR_INDEX% exit /b 0
set "NAME="
rem SET uses a prefix match: accept only the exact variable (TRY_1 is not TRY_10)
for /f "tokens=1,* delims==" %%A in ('set TRY_%INDEX% 2^>nul') do if "%%A"=="TRY_%INDEX%" set "NAME=%%B"
if not defined NAME exit /b 0
set "CANDIDATE=%TRY_DIR%\%NAME%"
set "MANIFEST=%TEMP%\qd_manifest_%RANDOM%%RANDOM%.txt"
tar -xOf "%CANDIDATE%" ./blender_manifest.toml >"%MANIFEST%" 2>nul
if not errorlevel 1 (
  findstr /b /c:"id = \"quick_destruction\"" "%MANIFEST%" >nul 2>&1
  if not errorlevel 1 (
    rem The caller reads the accepted package again; release this probe file now
    set "PACKAGE=%CANDIDATE%"
    del "%MANIFEST%" >nul 2>&1
    set "MANIFEST="
    exit /b 0
  )
)
del "%MANIFEST%" >nul 2>&1
goto try_next

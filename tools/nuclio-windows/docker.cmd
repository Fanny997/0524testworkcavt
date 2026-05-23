@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "REAL_DOCKER="
for /f "delims=" %%D in ('where docker.exe 2^>nul') do (
    set "REAL_DOCKER=%%D"
    goto :found_docker
)

:found_docker
if not defined REAL_DOCKER (
    echo docker.exe was not found in PATH. 1>&2
    exit /b 1
)

set "ARGS="
set "EXPECT_DOCKERFILE="

:next_arg
if "%~1"=="" goto :run_docker

set "ARG=%~1"
if "!ARG:~0,11!"=="C:tmpnuclio" (
    set "ARG=C:/tmp/nuclio/!ARG:~11!"
)

if defined EXPECT_DOCKERFILE (
    echo !ARG! | findstr /C:"Dockerfile." >nul
    if not errorlevel 1 (
        for /f "tokens=2 delims=." %%F in ("!ARG!") do set "ARG=Dockerfile.%%F"
    ) else (
        for %%F in ("!ARG!") do set "ARG=%%~nxF"
    )
    set "EXPECT_DOCKERFILE="
)

if "!ARG!"=="-f" set "EXPECT_DOCKERFILE=1"
if "!ARG!"=="--file" set "EXPECT_DOCKERFILE=1"

set "ARGS=!ARGS! "!ARG!""
shift
goto :next_arg

:run_docker
"%REAL_DOCKER%" %ARGS%
exit /b %ERRORLEVEL%

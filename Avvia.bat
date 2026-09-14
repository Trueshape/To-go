@echo off
setlocal
title maps-itinerary
cd /d "%~dp0"

set "PYCMD="

where python >nul 2>nul
if %errorlevel%==0 (
    set "PYCMD=python"
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        set "PYCMD=py"
    )
)

if "%PYCMD%"=="" (
    echo.
    echo  Python non e' installato ^(o non e' nel PATH^).
    echo.
    echo  Scaricalo da: https://www.python.org/downloads/
    echo  Durante l'installazione spunta "Add python.exe to PATH".
    echo  Poi rilancia questo file.
    echo.
    pause
    exit /b 1
)

%PYCMD% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not %errorlevel%==0 (
    echo.
    echo  Versione di Python troppo vecchia ^(serve 3.10 o superiore^):
    %PYCMD% --version
    echo.
    echo  Aggiornala da: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

%PYCMD% -c "import requests" >nul 2>nul
if not %errorlevel%==0 (
    echo Installo la libreria "requests" ^(serve una sola volta^)...
    %PYCMD% -m pip install --quiet requests
    if not %errorlevel%==0 (
        echo.
        echo  Installazione di "requests" non riuscita. Prova a lanciare a mano:
        echo    %PYCMD% -m pip install requests
        echo.
        pause
        exit /b 1
    )
)

%PYCMD% "%~dp0maps_itinerary_standalone.py" %*
if not %errorlevel%==0 pause

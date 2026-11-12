@echo off
rem Builds dist\Spiderweb.exe with PyInstaller (pip install pyinstaller).
cd /d "%~dp0"
python build_version.py
if errorlevel 1 (pause & exit /b 1)
python make_icon.py
if errorlevel 1 (pause & exit /b 1)
python make_notices.py
if errorlevel 1 (pause & exit /b 1)
rem The Kestrel connector goes inside when it's there (not in git: built from native\kestrel_bridge, or taken from a source zip).
set KESTREL=
if exist "%~dp0scripts\kestrel\x64\kestrel_bridge.dll" (set KESTREL=--add-binary "%~dp0scripts\kestrel\x64;kestrel\x64") else (echo No scripts\kestrel\x64\kestrel_bridge.dll: building without Kestrel ^(GPU^) playback.)
rem The own compiled loops (in git; rebuilt from native\loops with: python native\loops\build_release.py).
set LOOPS=
if exist "%~dp0scripts\loops\x64\spiderweb_loops.dll" (set LOOPS=--add-binary "%~dp0scripts\loops\x64;loops\x64") else (echo No scripts\loops\x64\spiderweb_loops.dll: building without the own compiled loops.)
rem Numba is no longer used (the own loops replaced it): kept out even when it's installed here.
rem notes.shaping is loaded only when the Text tool needs it: named so uharfbuzz + python-bidi go inside.
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Spiderweb ^
  --exclude-module numba --exclude-module llvmlite --hidden-import notes.shaping ^
  --distpath dist --workpath build --specpath build --paths "%~dp0scripts" ^
  --version-file "%~dp0build\version.txt" --icon "%~dp0scripts\icons\icon.ico" ^
  --add-data "%~dp0scripts\icons;icons" --add-data "%~dp0scripts\lang;lang" --add-data "%~dp0LICENSE;." --add-data "%~dp0clips;clips" --add-binary "%~dp0scripts\bass\x64;bass\x64" %KESTREL% %LOOPS% spiderweb.py
if errorlevel 1 (pause & exit /b 1)
copy /y LICENSE dist\ >nul
copy /y README.txt dist\ >nul
copy /y THIRD-PARTY-NOTICES.txt dist\ >nul
copy /y SPIDERWEB-NOTES-FORMAT.md dist\ >nul
if exist dist\clips rmdir /s /q dist\clips
rem Source archive for sharing: spiderweb.py + the scripts and clips folders, launchers, license and the Kestrel connector's + own loops' source only (no autosave/shapes/output).
if exist dist\Spiderweb-source.zip del dist\Spiderweb-source.zip
"%SystemRoot%\System32\tar.exe" -a -cf dist\Spiderweb-source.zip --exclude=__pycache__ --exclude=CLAUDE.md spiderweb.py scripts clips ^
  Spiderweb.bat build.bat build_version.py make_icon.py make_notices.py licences icon.svg icon-32.svg icon-tiny.svg LICENSE THIRD-PARTY-NOTICES.txt README.txt SPIDERWEB-NOTES-FORMAT.md ^
  native\kestrel_bridge\src native\kestrel_bridge\tests native\kestrel_bridge\licences native\kestrel_bridge\Cargo.toml ^
  native\kestrel_bridge\Cargo.lock native\kestrel_bridge\make_notices.py native\kestrel_bridge\NOTICES.txt ^
  native\loops\src native\loops\Cargo.toml native\loops\Cargo.lock native\loops\build_release.py ^
  native\loops\make_notices.py native\loops\NOTICES.txt
if errorlevel 1 (pause & exit /b 1)
rem The build stamp was only for this build (and its source zip): running from this folder shows the plain version again.
if exist scripts\files\build_stamp.py del scripts\files\build_stamp.py
echo.
echo Done: dist\Spiderweb.exe and dist\Spiderweb-source.zip
pause

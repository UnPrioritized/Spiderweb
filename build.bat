@echo off
rem Builds the folder version: dist\Spiderweb\ (Spiderweb.exe + data\<version>\) and dist\Spiderweb-<version>.zip,
rem plus dist\Spiderweb-source.zip. Needs Rust + Visual Studio's C++ tools (the starter, native\starter).
cd /d "%~dp0"
python make_icon.py
if errorlevel 1 (pause & exit /b 1)
python make_notices.py
if errorlevel 1 (pause & exit /b 1)
rem The Kestrel connector goes inside when it's there (not in git: built from native\kestrel_bridge, or taken from a source zip).
if not exist "%~dp0scripts\kestrel\x64\kestrel_bridge.dll" echo No scripts\kestrel\x64\kestrel_bridge.dll: building without Kestrel ^(GPU^) playback.
rem The own compiled loops are in git (rebuilt from native\loops with: python native\loops\build_release.py).
if exist dist\Spiderweb.exe del dist\Spiderweb.exe
python build_folder.py --out dist
if errorlevel 1 (pause & exit /b 1)
rem Source archive for sharing: spiderweb.py + the scripts and clips folders, launchers, license and the Kestrel connector's,
rem own loops' and starter's source only (no autosave/shapes/output).
if exist dist\Spiderweb-source.zip del dist\Spiderweb-source.zip
"%SystemRoot%\System32\tar.exe" -a -cf dist\Spiderweb-source.zip --exclude=__pycache__ --exclude=CLAUDE.md spiderweb.py scripts clips ^
  Spiderweb.bat build.bat build_folder.py build_version.py make_icon.py make_notices.py licences icon.svg icon-32.svg icon-tiny.svg LICENSE THIRD-PARTY-NOTICES.txt README.txt SPIDERWEB-NOTES-FORMAT.md ^
  native\kestrel_bridge\src native\kestrel_bridge\tests native\kestrel_bridge\licences native\kestrel_bridge\Cargo.toml ^
  native\kestrel_bridge\Cargo.lock native\kestrel_bridge\make_notices.py native\kestrel_bridge\NOTICES.txt ^
  native\loops\src native\loops\Cargo.toml native\loops\Cargo.lock native\loops\build_release.py ^
  native\loops\make_notices.py native\loops\NOTICES.txt ^
  native\starter\src native\starter\Cargo.toml native\starter\Cargo.lock native\starter\build.rs native\starter\build_release.py native\starter\starter.manifest
if errorlevel 1 (pause & exit /b 1)
echo.
echo Done: dist\Spiderweb\, dist\Spiderweb-^<version^>.zip and dist\Spiderweb-source.zip
pause

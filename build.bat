@echo off
rem Builds dist\Spiderweb.exe with PyInstaller (pip install pyinstaller).
cd /d "%~dp0"
python build_version.py
if errorlevel 1 (pause & exit /b 1)
python make_icon.py
if errorlevel 1 (pause & exit /b 1)
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Spiderweb ^
  --distpath dist --workpath build --specpath build --paths "%~dp0scripts" ^
  --version-file "%~dp0build\version.txt" --icon "%~dp0scripts\icons\icon.ico" ^
  --add-data "%~dp0scripts\icons;icons" --add-data "%~dp0scripts\lang;lang" --add-data "%~dp0LICENSE;." --add-data "%~dp0clips;clips" spiderweb.py
if errorlevel 1 (pause & exit /b 1)
copy /y LICENSE dist\ >nul
copy /y README.txt dist\ >nul
if exist dist\clips rmdir /s /q dist\clips
rem Source archive for sharing: spiderweb.py + the scripts and clips folders, launchers and license only (no autosave/shapes/output).
if exist dist\Spiderweb-source.zip del dist\Spiderweb-source.zip
"%SystemRoot%\System32\tar.exe" -a -cf dist\Spiderweb-source.zip --exclude=__pycache__ --exclude=CLAUDE.md spiderweb.py scripts clips ^
  Spiderweb.bat build.bat build_version.py make_icon.py icon.svg LICENSE README.txt
if errorlevel 1 (pause & exit /b 1)
echo.
echo Done: dist\Spiderweb.exe and dist\Spiderweb-source.zip
pause

@echo off
echo Starting build process...

echo Cleaning up previous builds...
rmdir /s /q build
rmdir /s /q dist

echo Building EXE...
.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed ^
    --name "LiveAssistant" ^
    --icon "src/gui/favicon.ico" ^
    --add-data "src/gui/styles.qss;src/gui" ^
    --add-data "src/gui/favicon.ico;src/gui" ^
    --add-data "src/gui/zsm.jpg;src/gui" ^
    --hidden-import "playwright" ^
    --hidden-import "speech_recognition" ^
    --hidden-import "pyaudio" ^
    --hidden-import "pkg_resources" ^
    --hidden-import "setuptools" ^
    src/main.py

echo Copying config files...
mkdir dist\config
copy config\config.json dist\config\
copy config\speech_templates.json dist\config\

echo Build complete! Check the 'dist' folder.
pause

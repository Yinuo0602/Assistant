import sys
import os

# Fix import path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from src.utils.logger import get_logger
from src.utils.paths import get_config_path
from src.gui.mainwindow import MainWindow

logger = get_logger()

def load_config():
    import os
    import json
    config_path = get_config_path("config.json")
    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def main():
    logger.info("Application starting...")
    
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

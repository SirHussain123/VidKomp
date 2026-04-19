import sys
import os

from PyQt6.QtWidgets import QApplication
from utils.logger import setup_logging
from ui.main_window import MainWindow

# Absolute path to project root — used for loading assets
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    setup_logging()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    qss_path = os.path.join(PROJECT_ROOT, "assets", "styles", "theme.qss")
    if os.path.exists(qss_path):
        with open(qss_path, "r") as f:
            app.setStyleSheet(f.read())

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
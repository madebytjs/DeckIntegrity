"""GUI entry point: python -m deck_integrity.gui"""
from __future__ import annotations

import pathlib
import sys

from deck_integrity import VERSION


def main(argv=None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print('The GUI needs PySide6. The command-line tool works without it:\n'
              '  python3 deck-integrity.pyz --help', file=sys.stderr)
        return 2
    from deck_integrity.gui.theme import QSS
    from deck_integrity.gui.window import MainWindow

    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName('DeckIntegrity')
    app.setApplicationVersion(VERSION)
    app.setDesktopFileName('deck-integrity')   # matches the .desktop file, for the Wayland icon
    app.setStyleSheet(QSS)
    icon = pathlib.Path(__file__).with_name('icon.png')
    if icon.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon)))
    win = MainWindow()
    if '--selftest' in app.arguments():
        # Packaging check: the toolkit loads and the window builds, then exit.
        win.resize(1280, 800)
        win.show()
        app.processEvents()
        ok = not win.grab().isNull()
        print('selftest', 'ok' if ok else 'FAILED', 'platform', app.platformName())
        return 0 if ok else 1
    screen = app.primaryScreen().availableGeometry()
    if screen.width() <= 1400:
        win.showMaximized()   # Steam Deck / Legion Go S style screens
    else:
        win.resize(1280, 800)
        win.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())

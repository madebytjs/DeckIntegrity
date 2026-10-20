"""Make square icons from the transparent source logo.

Usage: python scripts/make_icons.py   (needs PySide6, e.g. the project .venv)
Reads assets/logo-source.png; writes assets/icon.png (512), assets/logo.png (1024) and the
GUI window icon src/deck_integrity/gui/icon.png (256).
"""
import os
import pathlib

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter

ASSETS = pathlib.Path(__file__).resolve().parent.parent / 'assets'


def content_box(img: QImage) -> QRect:
    """Bounding box of every pixel that is not fully transparent."""
    w, h = img.width(), img.height()
    xs, ys = [], []
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            if img.pixelColor(x, y).alpha() > 8:
                xs.append(x)
                ys.append(y)
    return QRect(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def square(img: QImage, margin: float = 0.02) -> QImage:
    box = content_box(img)
    side = int(max(box.width(), box.height()) * (1 + 2 * margin))
    out = QImage(side, side, QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage((side - box.width()) // 2, (side - box.height()) // 2, img.copy(box))
    p.end()
    return out


def main() -> None:
    src = QImage(str(ASSETS / 'logo-source.png')).convertToFormat(QImage.Format_ARGB32)
    sq = square(src)
    for name, size in (('icon.png', 512), ('logo.png', 1024)):
        sq.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(str(ASSETS / name))
        print('wrote', ASSETS / name, f'{size}x{size}')
    gui_icon = ASSETS.parent / 'src' / 'deck_integrity' / 'gui' / 'icon.png'
    sq.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(str(gui_icon))
    print('wrote', gui_icon, '256x256')


if __name__ == '__main__':
    main()

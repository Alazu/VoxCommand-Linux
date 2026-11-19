"""Render voxcommand/icon.svg to a multi-size .ico for the Windows executables."""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

app = QGuiApplication([])
renderer = QSvgRenderer(str(Path(__file__).parents[2] / "voxcommand" / "icon.svg"))
images = []
for size in (16, 24, 32, 48, 64, 128, 256):
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    images.append((size, bytes(data)))

# ICO = header + directory + PNG images (supported since Windows Vista)
out = struct.pack("<HHH", 0, 1, len(images))
offset = 6 + 16 * len(images)
for size, png in images:
    out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
    offset += len(png)
out += b"".join(png for _, png in images)
Path(sys.argv[1]).write_bytes(out)
print(f"wrote {sys.argv[1]}")

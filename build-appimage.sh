#!/usr/bin/env bash
# Build a self-contained VoxCommand AppImage (bundles Python, Qt, Whisper runtime and PortAudio).
#   ./build-appimage.sh          CPU build   (~250 MB)
#   ./build-appimage.sh --gpu    adds the NVIDIA CUDA libraries (much larger; needs only the NVIDIA driver on the host)
# Needs: uv, curl, and the system portaudio library. Whisper models are downloaded on first use, not bundled.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PYVER=3.12
EXTRAS="gui"; SUFFIX=""
if [[ "${1:-}" == "--gpu" ]]; then EXTRAS="gui,gpu"; SUFFIX="-gpu"; fi
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' voxcommand/__init__.py)
ARCH=$(uname -m)
BUILD=build/appimage$SUFFIX
APPDIR=$BUILD/VoxCommand.AppDir
OUT=dist/VoxCommand-$VERSION$SUFFIX-$ARCH.AppImage

rm -rf "$APPDIR"
mkdir -p "$APPDIR/opt" "$APPDIR/usr/lib" build/tools dist

echo "==> Python $PYVER (relocatable build)"
uv python install -q "$PYVER"
PYBIN=$(readlink -f "$(uv python find --python-preference only-managed "$PYVER")")
cp -a "$(dirname "$(dirname "$PYBIN")")" "$APPDIR/opt/python"
PY="$APPDIR/opt/python/bin/python$PYVER"

echo "==> Installing VoxCommand[$EXTRAS]"
uv pip install -q --python "$PY" --target "$APPDIR/opt/site-packages" ".[$EXTRAS]"

echo "==> Trimming"
PYLIB="$APPDIR/opt/python/lib/python$PYVER"
rm -rf "$PYLIB"/{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3} "$APPDIR"/opt/python/lib/{tcl*,tk*,itcl*,thread*} \
       "$APPDIR/opt/python/share" "$APPDIR/opt/python/include"
QT="$APPDIR/opt/site-packages/PySide6"
rm -rf "$QT"/Qt/{qml,translations} "$QT"/{assistant,designer,linguist,lrelease,lupdate,qmlls,qmllint,qmlformat}
find "$APPDIR" -name __pycache__ -type d -prune -exec rm -rf {} +

echo "==> PortAudio"
PA=""
for dir in /usr/lib64 /usr/lib/x86_64-linux-gnu /usr/lib /usr/local/lib; do
    [[ -e "$dir/libportaudio.so.2" ]] && { PA="$dir/libportaudio.so.2"; break; }
done
[[ -n "$PA" ]] || { echo "libportaudio.so.2 not found: sudo dnf install portaudio" >&2; exit 1; }
cp -L "$PA" "$APPDIR/usr/lib/libportaudio.so.2"

echo "==> AppDir metadata"
cp voxcommand/icon.svg "$APPDIR/voxcommand.svg"
ln -sf voxcommand.svg "$APPDIR/.DirIcon"
cat > "$APPDIR/voxcommand.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=VoxCommand
Comment=Voice command macros powered by Whisper
Exec=voxcommand
Icon=voxcommand
Categories=Utility;Accessibility;
Terminal=false
DESKTOP
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
# No arguments opens the GUI; anything else goes to the command line (try --help).
HERE="$(dirname "$(readlink -f "$0")")"
export PYTHONPATH="$HERE/opt/site-packages"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export VOXCOMMAND_PORTAUDIO="$HERE/usr/lib/libportaudio.so.2"
[ $# -eq 0 ] && set -- gui
exec "$HERE/opt/python/bin/python3" -m voxcommand "$@"
APPRUN
chmod +x "$APPDIR/AppRun"
chmod -R a+rX "$APPDIR"

TOOL=build/tools/appimagetool-$ARCH.AppImage
if [[ ! -x "$TOOL" ]]; then
    echo "==> Fetching appimagetool"
    curl -fsSL -o "$TOOL" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
    chmod +x "$TOOL"
fi

echo "==> Packing $OUT"
ARCH=$ARCH "$TOOL" --appimage-extract-and-run --no-appstream "$PWD/$APPDIR" "$PWD/$OUT" >"$BUILD/appimagetool.log" 2>&1 \
    || { tail -20 "$BUILD/appimagetool.log" >&2; exit 1; }
chmod +x "$OUT"
ls -lh "$OUT"

#!/usr/bin/env bash
# Build dist/DeckIntegrity-x86_64.AppImage on an x86_64 Linux host (GitHub Actions ubuntu).
# Needs: curl, jq, network. No FUSE required.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYVER="${PYVER:-3.12}"
WORK="$ROOT/build/appimage"
APPDIR="$WORK/AppDir"
ICON="$ROOT/assets/icon.png"
VERSION="$(sed -n "s/^VERSION = '\(.*\)'/\1/p" "$ROOT/src/deck_integrity/__init__.py")"

[ -f "$ICON" ] || { echo "missing $ICON (256x256 or larger PNG)"; exit 1; }
rm -rf "$WORK" && mkdir -p "$WORK" "$ROOT/dist"
cd "$WORK"

# 1. Portable Python from python-appimage (manylinux2014: runs on old and new distros).
AUTH=()
[ -n "${GITHUB_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN")
PY_URL="$(curl -fsSL "${AUTH[@]}" "https://api.github.com/repos/niess/python-appimage/releases/tags/python$PYVER" \
  | jq -r '.assets[].browser_download_url' | grep 'manylinux2014_x86_64.AppImage$' | sort -V | tail -1)"
echo "Python runtime: $PY_URL"
curl -fsSL -o python.AppImage "$PY_URL"
chmod +x python.AppImage
./python.AppImage --appimage-extract >/dev/null
mv squashfs-root "$APPDIR"

# 2. Install the app and its GUI toolkit into the bundled Python.
"$APPDIR/AppRun" -m pip install --no-cache-dir --no-warn-script-location "$ROOT[gui]"

# 3. Our launcher, desktop entry and icon replace the Python ones.
mv "$APPDIR/AppRun" "$APPDIR/AppRun.python"
cp "$ROOT/packaging/appimage/AppRun" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
rm -f "$APPDIR"/*.desktop "$APPDIR"/*.png "$APPDIR"/.DirIcon "$APPDIR"/usr/share/metainfo/*.xml
cp "$ROOT/packaging/appimage/deck-integrity.desktop" "$APPDIR/"
cp "$ICON" "$APPDIR/deck-integrity.png"
cp "$ICON" "$APPDIR/.DirIcon"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp "$ICON" "$APPDIR/usr/share/icons/hicolor/256x256/apps/deck-integrity.png"

# 4. Trim what a diagnostics GUI never uses, to keep the download smaller.
SITE="$(echo "$APPDIR"/opt/python*/lib/python*/site-packages)"
rm -rf "$SITE"/PySide6/{Qt/qml,Qt/translations,examples,include,glue,typesystems,scripts}
find "$APPDIR" -name '__pycache__' -prune -exec rm -rf {} +

# 5. Pack.
curl -fsSL -o appimagetool \
  https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x appimagetool
OUT="$ROOT/dist/DeckIntegrity-$VERSION-x86_64.AppImage"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 ./appimagetool "$APPDIR" "$OUT"
cp "$OUT" "$ROOT/dist/DeckIntegrity-x86_64.AppImage"
ls -lh "$ROOT"/dist/*.AppImage

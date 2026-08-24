#!/usr/bin/env bash
set -euo pipefail

APP_ID="io.github.peclark1.CpmDiskAnalyzer"
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
VENV_DIR="${PROJECT_DIR}/.venv"
GUI_LAUNCHER="${VENV_DIR}/bin/cpm-disk-analyzer-gui"
CLI_LAUNCHER="${VENV_DIR}/bin/cpm-disk-analyzer"
LOCAL_BIN_DIR="${HOME}/.local/bin"
APPLICATIONS_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/icons/hicolor/scalable/apps"
DESKTOP_TEMPLATE="${PROJECT_DIR}/packaging/${APP_ID}.desktop.in"
ICON_SOURCE="${PROJECT_DIR}/assets/${APP_ID}.svg"
APPLICATION_FILE="${APPLICATIONS_DIR}/${APP_ID}.desktop"

usage() {
    cat <<'EOF'
Usage: ./install.sh [--no-desktop-shortcut]

Install CP/M Disk Analyzer for the current user. This creates the project
virtual environment, command-line launchers, an application-menu entry, and
(when available) a shortcut in the user's Desktop folder.

Options:
  --no-desktop-shortcut  Do not create a shortcut in the Desktop folder.
  -h, --help             Show this help.
EOF
}

install_desktop_shortcut=1
while (($#)); do
    case "$1" in
        --no-desktop-shortcut)
            install_desktop_shortcut=0
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required." >&2
    echo "Install the Ubuntu dependencies with:" >&2
    echo "  sudo apt install python3 python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1" >&2
    exit 1
fi

if ! python3 - <<'PY'
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: F401,E402
PY
then
    echo >&2
    echo "GTK4/libadwaita Python support is required." >&2
    echo "Install the Ubuntu dependencies with:" >&2
    echo "  sudo apt install python3 python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1" >&2
    exit 1
fi

if ! python3 -m venv --system-site-packages "${VENV_DIR}"; then
    echo >&2
    echo "Could not create ${VENV_DIR}." >&2
    echo "On Ubuntu, install python3-venv and run this installer again." >&2
    exit 1
fi

"${VENV_DIR}/bin/python" -m pip install -e "${PROJECT_DIR}"

if [[ ! -x "${GUI_LAUNCHER}" || ! -x "${CLI_LAUNCHER}" ]]; then
    echo "Installation completed without creating the expected launchers." >&2
    exit 1
fi

mkdir -p "${LOCAL_BIN_DIR}" "${APPLICATIONS_DIR}" "${ICON_DIR}"
ln -sfn "${CLI_LAUNCHER}" "${LOCAL_BIN_DIR}/cpm-disk-analyzer"
ln -sfn "${GUI_LAUNCHER}" "${LOCAL_BIN_DIR}/cpm-disk-analyzer-gui"
install -m 0644 "${ICON_SOURCE}" "${ICON_DIR}/${APP_ID}.svg"

"${VENV_DIR}/bin/python" - \
    "${DESKTOP_TEMPLATE}" "${APPLICATION_FILE}" "${GUI_LAUNCHER}" <<'PY'
from pathlib import Path
import sys

template_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
launcher = sys.argv[3]

if "\n" in launcher or "\r" in launcher:
    raise SystemExit("The repository path cannot contain a newline")

# Escape characters that are special inside a quoted Desktop Entry Exec value.
escaped = (
    launcher.replace("\\", "\\\\")
    .replace('"', '\\"')
    .replace("`", "\\`")
    .replace("$", "\\$")
)
desktop_entry = template_path.read_text(encoding="utf-8").replace("@EXEC@", escaped)
output_path.write_text(desktop_entry, encoding="utf-8")
PY
chmod 0644 "${APPLICATION_FILE}"

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "${APPLICATION_FILE}"
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${APPLICATIONS_DIR}" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "${XDG_DATA_HOME:-${HOME}/.local/share}/icons/hicolor" \
        >/dev/null 2>&1 || true
fi

desktop_dir=""
if command -v xdg-user-dir >/dev/null 2>&1; then
    desktop_dir="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
fi
if [[ -z "${desktop_dir}" ]]; then
    desktop_dir="${HOME}/Desktop"
fi

if ((install_desktop_shortcut)) && [[ -d "${desktop_dir}" && "${desktop_dir}" != "${HOME}" ]]; then
    desktop_shortcut="${desktop_dir}/CP-M Disk Analyzer.desktop"
    install -m 0755 "${APPLICATION_FILE}" "${desktop_shortcut}"
    if command -v gio >/dev/null 2>&1; then
        gio set "${desktop_shortcut}" metadata::trusted true >/dev/null 2>&1 || true
    fi
    echo "Desktop shortcut: ${desktop_shortcut}"
fi

echo
echo "CP/M Disk Analyzer is installed."
echo "Open it from the Applications menu or run:"
echo "  ${LOCAL_BIN_DIR}/cpm-disk-analyzer-gui"
if [[ ":${PATH}:" != *":${LOCAL_BIN_DIR}:"* ]]; then
    echo
    echo "${LOCAL_BIN_DIR} is not currently on PATH. The desktop launcher will still work."
fi


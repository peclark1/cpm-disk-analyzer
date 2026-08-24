# CP/M Disk Analyzer

CP/M Disk Analyzer identifies and inspects vintage CP/M disk images without
modifying them. It combines a reusable Python analysis engine with both a
command-line interface and a native GTK4/libadwaita desktop application.

This is an early, evidence-driven release. It deliberately distinguishes facts
observed in an image container from geometry or filesystem properties inferred
from known profiles.

## Current capabilities

- Reads raw sector images (`.img`, `.dsk`, `.raw`, or an unrecognized extension)
- Reads ImageDisk (`.imd`) track records, including compressed sectors
- Preserves IMD recording-mode and geometry observations
- Normalizes IMD sectors into logical cylinder/head/sector order
- Tests images against declarative CP/M disk profiles
- Validates CP/M directory entries, extents, record counts, and allocation blocks
- Reports alternative interpretations with evidence and confidence scores
- Displays directory extents in the CLI and GUI
- Groups directory extents into logical files for desktop file transfer
- Extracts files from raw and IMD images by copy-only drag and drop
- Copies host files into raw images after explicit confirmation
- Remembers the GUI window size and maximized state
- Exports complete machine-readable JSON reports
- Calculates SHA-256 over the untouched source image

The initial profile catalog includes IBM 3740, Digital Systems 26-sector single
density, Digital Systems 58-sector double density, and Kaypro II layouts.

## Ubuntu desktop installation

After cloning the repository, install the Ubuntu dependencies and run the
included installer:

```bash
cd ~/src/cpm-disk-analyzer
sudo apt install python3 python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
./install.sh
```

The installer creates a project virtual environment, installs the analyzer,
adds both commands to `~/.local/bin`, and creates an Applications-menu entry
with an icon. When Ubuntu has a Desktop folder, it also creates a desktop
shortcut. It only installs files for the current user and does not need
`sudo`.

The launcher records the repository's absolute location. If the repository is
moved again, rerun `./install.sh` from its new location.

## Manual/development installation

Ubuntu needs Python, GTK4, libadwaita, and the virtual-environment package:

```bash
sudo apt install python3 python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
git clone https://github.com/peclark1/cpm-disk-analyzer.git
cd cpm-disk-analyzer
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

The `cpm-disk-analyzer` and `cpm-disk-analyzer-gui` launchers are generated in
`.venv/bin` by this installation step; they are not source files in the
repository. Activate the virtual environment before using the short command
names, or run `.venv/bin/cpm-disk-analyzer-gui` directly.

`--system-site-packages` lets the virtual environment use Ubuntu's supported
PyGObject/GTK packages while keeping the analyzer itself isolated.

## Command line

```bash
cpm-disk-analyzer profiles
cpm-disk-analyzer analyze disk.imd
cpm-disk-analyzer analyze disk.img --show-files
cpm-disk-analyzer analyze disk.img --json disk-analysis.json
cpm-disk-analyzer analyze disk.img --profile dsi-dd58
```

Exit status is `0` when at least one candidate is found, `1` when no current
profile is credible, and `2` for an input or container error.

## Desktop GUI

```bash
cpm-disk-analyzer-gui
```

Or:

```bash
cpm-disk-analyzer gui
```

The GUI follows the Ubuntu/GNOME desktop style: a libadwaita header bar, native
open/save dialogs, a profile and candidate sidebar, system icons, automatic
light/dark styling, and Summary, Directory, and Evidence views. Selecting a
candidate updates the directory and supporting evidence shown for that specific
interpretation.

In the Directory view, select one or more CP/M files and drag them to Ubuntu
Files or the desktop to extract copies. To add files, choose the target CP/M
user area and drop host files onto the analyzer window. Raw images are supported
for import; IMD images currently support extraction only. Before changing a raw
image, the analyzer shows the host-to-CP/M 8.3 filename mappings and requires
confirmation. Existing CP/M files are never replaced by drag and drop.

The GUI can also open an image supplied on the command line:

```bash
cpm-disk-analyzer-gui disk.imd
```

The command-line analyzer does not require a graphical desktop or GTK imports.

## Confidence model

Reports use four labels: `high`, `moderate`, `low`, and `unlikely`. Each score is
accompanied by categorized evidence:

- **Observed:** directly present in the source or container
- **Derived:** calculated deterministically
- **Inferred:** a supported interpretation that the image does not state itself

A raw sector image does not preserve encoding, physical interleave, or data rate.
The analyzer therefore reports those fields as profile interpretations rather
than observed facts. Multiple profiles may tie when their filesystem layouts are
identical; vendor signatures can distinguish them when present.

## Safety

Analysis and extraction do not modify the source image. JSON export and dragged
file extraction write only to the explicitly selected destination. Dropping host
files into a raw disk image is the exception: after displaying the CP/M 8.3 name
mappings, the application requires confirmation and atomically replaces the
original image. It refuses filename conflicts, invalid directories, insufficient
space, and unsupported IMD writes before changing the image. CP/M records are
128 bytes, so an imported file's final record is padded when necessary.

## Development

```bash
python -m unittest discover -s tests -v
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for component boundaries and the
planned development stages.

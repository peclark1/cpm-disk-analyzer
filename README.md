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
- Exports complete machine-readable JSON reports
- Calculates SHA-256 over the untouched source image

The initial profile catalog includes IBM 3740, Digital Systems 26-sector single
density, Digital Systems 58-sector double density, and Kaypro II layouts.

## Install for development

Ubuntu needs Python, GTK4, libadwaita, and the virtual-environment package:

```bash
sudo apt install python3 python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
git clone https://github.com/peclark1/cpm-disk-analyzer.git
cd cpm-disk-analyzer
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

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

Analysis opens source images read-only. The only writes are explicitly requested
JSON reports. File extraction and repair are intentionally deferred until their
validation and output-isolation rules are implemented.

## Development

```bash
python -m unittest discover -s tests -v
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for component boundaries and the
planned development stages.

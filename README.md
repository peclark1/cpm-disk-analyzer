# CP/M Disk Analyzer

CP/M Disk Analyzer identifies, inspects, and safely edits supported vintage CP/M
disk images. It combines a reusable Python analysis engine with both a
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
- Displays directory extents in the CLI and logical files in the GUI
- Displays standard CP/M read-only, system, and archive file attributes
- Groups directory extents into logical files for desktop file transfer
- Extracts files from raw and IMD images by copy-only drag and drop
- Copies host files into raw images after explicit confirmation
- Renames and deletes files in raw images with extent-aware directory updates
- Sets CP/M read-only, system, and archive attributes in raw images
- Opens S100Computers/Z80-SBC/Dual IDE-CF `s100ide` CP/M 3 images, including
  stock short images that omit unused trailing CF sectors
- Recursively inventories `.img`, `.imd`, and `.dsk` archives with per-image and
  per-file SHA-256 hashes, duplicate counts, and density-folder provenance
- Looks for explicit Z80-only constructs in assembly/listing source and follows
  reachable code in `.COM`, Intel HEX, and boot/system areas to avoid treating
  arbitrary data bytes as Z80 instructions
- Remembers the GUI window size and maximized state
- Exports complete machine-readable JSON reports
- Calculates SHA-256 over the source image as opened

The profile catalog includes IBM 3740, Digital Systems 26-sector single density,
Digital Systems 58-sector double density, Kaypro II, and S100Computers IDE/CF
CP/M 3 layouts.

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
cpm-disk-analyzer analyze s100-cpm3.img --profile s100ide
cpm-disk-analyzer scan ~/DiskArchive --csv archive-files.csv --json archive-index.json
```

Exit status for `analyze` is `0` when at least one candidate is found, `1` when
no current profile is credible, and `2` for an input or container error.

### Archive archaeology scan

`scan` recursively visits `.img`, `.imd`, and `.dsk` files beneath an archive
root. It runs the normal profile detector, groups CP/M extents into logical
files, hashes each extracted file, and records repeated names and identical file
contents across disks. If the archive path contains `SingleDensity` or
`DoubleDensity`, that folder name is preserved as a `single` or `double`
provenance bucket; it does not override independent format detection.

The scan also looks for evidence that software requires a Z80 rather than a
plain 8080. Assembly/listing files are checked for explicit constructs such as
`IX`, `IY`, `DJNZ`, `JR`, `LDIR`, and Z80 interrupt modes. `.COM` and Intel HEX
files are decoded from their entry points and only reachable Z80-only opcodes
are reported, so opcode-looking bytes embedded in data do not count as code.
The boot/system area is treated the same way, starting from address `0000H`.
This is intentionally conservative and is an archaeological clue detector, not
a claim that every flagged binary uniquely identifies a specific CPU board.

The CSV contains one row per CP/M logical file and is convenient for sorting or
filtering. The JSON retains the full evidence list, image results, duplicate
counts, rare filenames, and boot/system-area findings. Both operations are
read-only with respect to the disk images.

For example, with an archive organized by density:

```text
DiskArchive/
├── SingleDensity/
│   └── ... presumptive IMSAI-history images ...
└── DoubleDensity/
    └── ... separately tracked provenance ...
```

run:

```bash
cpm-disk-analyzer scan ~/DiskArchive \
  --csv ~/archive-files.csv \
  --json ~/archive-index.json
```

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

Selecting exactly one file in a raw image also enables **Rename…**,
**Attributes…**, and **Delete**. Rename updates every extent while retaining
existing attribute bits. Attributes exposes the standard CP/M R/O, SYS, and ARC
flags. Delete requires destructive confirmation and marks every directory extent
of the selected logical file deleted. These editing controls remain disabled for
IMD images.

### S100 IDE/CF images

The `s100ide` profile matches the S100Computers/Z80-SBC/Dual IDE-CF CP/M 3
"no holes" layout used by the supported build:

- 512-byte physical sectors
- 64 sectors per CP/M track
- 256 CP/M tracks
- one reserved track
- 2048-byte allocation blocks
- 1024 directory entries
- 16-bit allocation pointers
- directory beginning at LBA 64

The logical CP/M volume is 8 MiB, but commonly distributed image files can be
shorter because unused trailing CF sectors are omitted. The analyzer accepts
these sector-aligned short images. If importing a new file requires allocation
blocks beyond the current end of a short image, it extends the image only as far
as necessary; it does not automatically pad the file to 8 MiB.

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

Analysis, archive scanning, and extraction do not modify the source image. JSON
or CSV export and dragged file extraction write only to the explicitly selected
destination. Raw-image imports, rename, delete, and attribute changes are
explicit editing operations. They validate the selected profile and directory
first and atomically replace the original image rather than editing it
piecemeal. The GUI requires confirmation for imports and deletion, refuses
filename conflicts and invalid 8.3 renames, and does not write IMD containers.
CP/M records are 128 bytes, so an imported file's final record is padded when
necessary.

## Development

```bash
python -m unittest discover -s tests -v
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for component boundaries and the
planned development stages.

# Architecture

## Design goals

1. Keep disk analysis in a reusable library shared by the CLI and GUI.
2. Never modify a source image.
3. Separate observed facts, deterministic derivations, and interpretations.
4. Show competing candidates rather than hiding ambiguity.
5. Add formats declaratively whenever code is not required.
6. Keep the analysis core and CLI dependency-free outside the Python standard
   library; use Ubuntu's system GTK4 bindings only for the desktop front end.

## Components

- `containers.py` recognizes containers and produces a normalized logical byte
  stream plus observations. IMD physical sector ordering is normalized using its
  sector-number map.
- `profiles.py` loads declarative geometry and filesystem profiles from packaged
  JSON.
- `cpm.py` parses CP/M directory entries and scores each profile using image
  length, directory structure, allocation ranges, and optional signatures.
- `analyzer.py` calculates the source checksum, coordinates container reading and
  profile scoring, and returns a structured result.
- `report.py` renders structured results as text or JSON.
- `cli.py` and `gui.py` are thin front ends over the same analyzer API. The GUI
  uses GTK4 and libadwaita for native Ubuntu/GNOME controls, styling, dialogs,
  dark-mode behavior, and file-manager open integration. GTK is loaded lazily so
  CLI use remains possible on headless systems.

## Confidence limitations

A raw sector dump contains bytes but normally lacks physical encoding, data rate,
sector headers, and physical ordering. A matching byte length is weak evidence.
Coherent directory extents and allocation pointers are substantially stronger.
Container metadata is reported independently so IMD facts do not get confused
with profile assumptions.

The current score is intentionally explainable rather than statistical. It will
be calibrated against synthetic fixtures and known-good archival images as the
test corpus grows.

## Planned stages

### Stage 2: richer CP/M inspection

- Consolidate extents into logical files
- Read file content using allocation blocks
- Safe extraction into a separate output directory
- CP/M attributes, timestamps, labels, and password entries
- Deleted-entry and orphaned-block diagnostics

### Stage 3: system identification

- Boot-track string and signature catalog
- CP/M generation and vendor fingerprints
- Z80 boot-code I/O-port heuristics
- Hardware and console dependency reports

### Stage 4: archive workflows

- Directory/batch scanning
- HTML and Markdown reports
- Image and file duplicate detection
- Comparison mode
- HFE, TD0, and flux-conversion adapters
- Integration hooks for `imsai-disk-archive`

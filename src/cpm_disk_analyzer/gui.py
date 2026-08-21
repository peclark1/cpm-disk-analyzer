"""Tkinter desktop front end for the shared analysis engine."""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .analyzer import analyze_image
from .models import ImageResult
from .profiles import load_profiles
from .report import as_json


class AnalyzerWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("CP/M Disk Analyzer")
        self.geometry("1000x680")
        self.minsize(800, 520)
        self.result: ImageResult | None = None
        self.path_var = tk.StringVar()
        self.profile_var = tk.StringVar(value="Automatic")
        self.status_var = tk.StringVar(value="Open a disk image to begin.")
        self._profile_ids = {"Automatic": None}
        for profile in load_profiles():
            label = f"{profile.name} ({profile.id})"
            self._profile_ids[label] = profile.id
        self._build()

    def _build(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Disk image:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(top, textvariable=self.path_var).grid(
            row=0, column=1, padx=6, sticky=tk.EW
        )
        ttk.Button(top, text="Open…", command=self.open_image).grid(row=0, column=2)
        ttk.Button(top, text="Analyze", command=self.analyze).grid(row=0, column=3, padx=(6, 0))
        ttk.Label(top, text="Profile:").grid(row=1, column=0, pady=(8, 0), sticky=tk.W)
        profile_box = ttk.Combobox(
            top,
            textvariable=self.profile_var,
            values=list(self._profile_ids),
            state="readonly",
        )
        profile_box.grid(row=1, column=1, columnspan=2, padx=6, pady=(8, 0), sticky=tk.EW)
        ttk.Button(top, text="Export JSON…", command=self.export_json).grid(
            row=1, column=3, pady=(8, 0), padx=(6, 0)
        )
        top.columnconfigure(1, weight=1)

        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        self.summary = self._text_tab(notebook, "Summary")

        candidates_frame = ttk.Frame(notebook)
        notebook.add(candidates_frame, text="Candidates")
        self.candidates = ttk.Treeview(
            candidates_frame,
            columns=("confidence", "score", "geometry"),
            show="tree headings",
        )
        self.candidates.heading("#0", text="Profile")
        self.candidates.heading("confidence", text="Confidence")
        self.candidates.heading("score", text="Score")
        self.candidates.heading("geometry", text="Geometry")
        self.candidates.column("#0", width=280)
        self.candidates.column("confidence", width=100)
        self.candidates.column("score", width=70)
        self.candidates.column("geometry", width=300)
        self.candidates.pack(fill=tk.BOTH, expand=True)
        self.candidates.bind("<<TreeviewSelect>>", self._show_candidate)

        self.directory = ttk.Treeview(
            notebook,
            columns=("user", "name", "extent", "records", "size"),
            show="headings",
        )
        for column, heading, width in (
            ("user", "User", 60),
            ("name", "Filename", 220),
            ("extent", "Extent", 80),
            ("records", "Records", 80),
            ("size", "Approx. bytes", 120),
        ):
            self.directory.heading(column, text=heading)
            self.directory.column(column, width=width)
        notebook.add(self.directory, text="Directory")
        self.evidence = self._text_tab(notebook, "Evidence")

        ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(
            fill=tk.X, side=tk.BOTTOM
        )

    @staticmethod
    def _text_tab(notebook: ttk.Notebook, title: str) -> tk.Text:
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=title)
        text = tk.Text(frame, wrap=tk.WORD, state=tk.DISABLED, font="TkFixedFont")
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        return text

    def open_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Open disk image",
            filetypes=[
                ("Disk images", "*.img *.imd *.dsk *.raw"),
                ("All files", "*"),
            ],
        )
        if path:
            self.path_var.set(path)
            self.analyze()

    def analyze(self) -> None:
        path = self.path_var.get().strip()
        if not path:
            messagebox.showinfo("CP/M Disk Analyzer", "Choose a disk image first.")
            return
        try:
            self.status_var.set("Analyzing…")
            self.update_idletasks()
            profile_id = self._profile_ids[self.profile_var.get()]
            self.result = analyze_image(path, profile_id)
            self._populate()
        except Exception as exc:  # GUI boundary: show errors rather than exiting.
            self.status_var.set("Analysis failed.")
            messagebox.showerror("Analysis failed", str(exc))

    def _populate(self) -> None:
        assert self.result is not None
        result = self.result
        best = result.best_candidate
        summary_lines = [
            f"Image:     {result.path}",
            f"SHA-256:   {result.sha256}",
            f"Size:      {result.size:,} bytes",
            f"Container: {result.container.upper()}",
            "",
            "Best interpretation:",
        ]
        if best:
            summary_lines.extend(
                [
                    f"  {best.profile_name}",
                    f"  Confidence: {best.confidence} ({best.score}/100)",
                    f"  Directory extents: {len(best.files)}",
                ]
            )
        else:
            summary_lines.append("  No supported profile produced a credible match.")
        summary_lines.extend(["", "Observed and derived facts:"])
        summary_lines.extend(f"  • {item}" for item in result.observations)
        summary_lines.extend(f"  • WARNING: {item}" for item in result.warnings)
        self._set_text(self.summary, "\n".join(summary_lines))

        self.candidates.delete(*self.candidates.get_children())
        for index, candidate in enumerate(result.candidates):
            geometry = candidate.geometry
            geometry_text = (
                f"{geometry['cylinders']}c/{geometry['heads']}h/"
                f"{geometry['sectors_per_track']}s/{geometry['sector_size']}b"
            )
            self.candidates.insert(
                "",
                tk.END,
                iid=str(index),
                text=candidate.profile_name,
                values=(candidate.confidence, candidate.score, geometry_text),
            )
        if result.candidates:
            self.candidates.selection_set("0")
            self._display_candidate(0)
        else:
            self._clear_candidate_details()
        self.status_var.set(f"Analyzed {Path(result.path).name} read-only.")

    def _show_candidate(self, _event: tk.Event) -> None:
        selection = self.candidates.selection()
        if selection:
            self._display_candidate(int(selection[0]))

    def _display_candidate(self, index: int) -> None:
        assert self.result is not None
        candidate = self.result.candidates[index]
        self.directory.delete(*self.directory.get_children())
        for entry in candidate.files:
            self.directory.insert(
                "",
                tk.END,
                values=(
                    entry.user,
                    entry.name,
                    entry.extent,
                    entry.records,
                    f"{entry.estimated_size:,}",
                ),
            )
        evidence_lines = [
            f"{candidate.profile_name} — {candidate.confidence} ({candidate.score}/100)",
            "",
        ]
        evidence_lines.extend(
            f"[{item.category.upper():8}] {item.points:+3d}  {item.message}"
            for item in candidate.evidence
        )
        evidence_lines.extend(f"\nWARNING: {warning}" for warning in candidate.warnings)
        self._set_text(self.evidence, "\n".join(evidence_lines))

    def _clear_candidate_details(self) -> None:
        self.directory.delete(*self.directory.get_children())
        self._set_text(self.evidence, "No candidate evidence to display.")

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        widget.configure(state=tk.DISABLED)

    def export_json(self) -> None:
        if self.result is None:
            messagebox.showinfo("CP/M Disk Analyzer", "Analyze an image first.")
            return
        suggested = f"{Path(self.result.path).stem}-analysis.json"
        path = filedialog.asksaveasfilename(
            title="Export analysis",
            initialfile=suggested,
            defaultextension=".json",
            filetypes=[("JSON report", "*.json")],
        )
        if path:
            Path(path).write_text(as_json(self.result) + "\n", encoding="utf-8")
            self.status_var.set(f"Report written to {path}")


def main() -> None:
    AnalyzerWindow().mainloop()


if __name__ == "__main__":
    main()


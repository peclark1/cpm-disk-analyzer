"""Native GTK4/libadwaita front end for CP/M Disk Analyzer."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .analyzer import analyze_image
from .models import CandidateResult, ImageResult
from .profiles import load_profiles
from .report import as_json


APPLICATION_ID = "io.github.peclark1.CpmDiskAnalyzer"


def _load_gtk() -> tuple[Any, Any, Any, Any]:
    """Load system GTK bindings only when the GUI is actually requested."""
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio, GLib, Gtk
    except (ImportError, ValueError) as exc:
        raise RuntimeError(
            "GTK4/libadwaita is not available. On Ubuntu, install it with:\n"
            "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n"
            "When using a virtual environment, create it with "
            "'python3 -m venv --system-site-packages .venv'."
        ) from exc
    return Adw, Gio, GLib, Gtk


def create_application() -> Any:
    """Create the application after GTK has been loaded successfully."""
    Adw, Gio, GLib, Gtk = _load_gtk()

    class AnalyzerApplication(Adw.Application):
        def __init__(self) -> None:
            super().__init__(
                application_id=APPLICATION_ID,
                flags=Gio.ApplicationFlags.HANDLES_OPEN,
            )
            self.window: AnalyzerWindow | None = None

        def do_activate(self) -> None:
            if self.window is None:
                self.window = AnalyzerWindow(self)
            self.window.present()

        def do_open(self, files: list[Any], _count: int, _hint: str) -> None:
            self.do_activate()
            if self.window is not None and files:
                path = files[0].get_path()
                if path:
                    self.window.load_path(Path(path))

    class AnalyzerWindow(Adw.ApplicationWindow):
        def __init__(self, application: AnalyzerApplication) -> None:
            super().__init__(application=application)
            self.set_title("CP/M Disk Analyzer")
            self.set_default_size(1120, 720)
            self.set_size_request(780, 520)

            self.result: ImageResult | None = None
            self.current_path: Path | None = None
            self._candidate_rows: list[Any] = []
            self._profile_ids: list[str | None] = [None]
            profile_labels = ["Automatic detection"]
            for profile in load_profiles():
                self._profile_ids.append(profile.id)
                profile_labels.append(f"{profile.name} ({profile.id})")

            self.toast_overlay = Adw.ToastOverlay()
            self.toolbar_view = Adw.ToolbarView()
            self.toast_overlay.set_child(self.toolbar_view)
            self.set_content(self.toast_overlay)

            self.header_bar = Adw.HeaderBar()
            self.window_title = Adw.WindowTitle(
                title="CP/M Disk Analyzer",
                subtitle="Read-only disk-image analysis",
            )
            self.header_bar.set_title_widget(self.window_title)
            self.toolbar_view.add_top_bar(self.header_bar)

            self.open_button = Gtk.Button.new_from_icon_name("document-open-symbolic")
            self.open_button.set_tooltip_text("Open a disk image")
            self.open_button.connect("clicked", self._choose_image)
            self.header_bar.pack_start(self.open_button)

            self.export_button = Gtk.Button.new_from_icon_name("document-save-symbolic")
            self.export_button.set_tooltip_text("Export a JSON analysis report")
            self.export_button.set_sensitive(False)
            self.export_button.connect("clicked", self._choose_export_path)
            self.header_bar.pack_end(self.export_button)

            self.main_paned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
            self.main_paned.set_position(330)
            self.main_paned.set_shrink_start_child(False)
            self.main_paned.set_shrink_end_child(False)
            self.main_paned.set_resize_start_child(False)
            self.main_paned.set_resize_end_child(True)
            self.toolbar_view.set_content(self.main_paned)

            self.main_paned.set_start_child(self._build_sidebar(profile_labels))
            self.main_paned.set_end_child(self._build_detail_view())
            self._show_empty_state()

        def _build_sidebar(self, profile_labels: list[str]) -> Any:
            sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            sidebar.set_size_request(280, -1)
            sidebar.set_margin_top(18)
            sidebar.set_margin_bottom(18)
            sidebar.set_margin_start(18)
            sidebar.set_margin_end(18)

            profile_label = Gtk.Label(label="FORMAT PROFILE", xalign=0)
            profile_label.add_css_class("caption")
            profile_label.add_css_class("dim-label")
            sidebar.append(profile_label)

            self.profile_dropdown = Gtk.DropDown.new_from_strings(profile_labels)
            self.profile_dropdown.set_hexpand(True)
            self.profile_dropdown.connect("notify::selected", self._profile_changed)
            sidebar.append(self.profile_dropdown)

            candidate_label = Gtk.Label(label="CANDIDATE INTERPRETATIONS", xalign=0)
            candidate_label.set_margin_top(8)
            candidate_label.add_css_class("caption")
            candidate_label.add_css_class("dim-label")
            sidebar.append(candidate_label)

            self.candidate_list = Gtk.ListBox()
            self.candidate_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
            self.candidate_list.add_css_class("boxed-list")
            self.candidate_list.connect("row-selected", self._candidate_selected)

            candidate_scroll = Gtk.ScrolledWindow()
            candidate_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            candidate_scroll.set_vexpand(True)
            candidate_scroll.set_child(self.candidate_list)
            sidebar.append(candidate_scroll)

            self.sidebar_status = Gtk.Label(
                label="Open an IMG or IMD disk image to begin.",
                xalign=0,
                wrap=True,
            )
            self.sidebar_status.add_css_class("caption")
            self.sidebar_status.add_css_class("dim-label")
            sidebar.append(self.sidebar_status)
            return sidebar

        def _build_detail_view(self) -> Any:
            detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

            switcher_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            switcher_bar.set_halign(Gtk.Align.CENTER)
            switcher_bar.set_margin_top(12)
            switcher_bar.set_margin_bottom(8)

            self.view_stack = Adw.ViewStack()
            self.view_stack.set_vexpand(True)
            switcher = Adw.ViewSwitcher()
            switcher.set_stack(self.view_stack)
            switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
            switcher_bar.append(switcher)
            detail.append(switcher_bar)

            self.summary_page = self._build_summary_page()
            summary_stack_page = self.view_stack.add_titled_with_icon(
                self.summary_page,
                "summary",
                "Summary",
                "view-list-symbolic",
            )
            summary_stack_page.set_use_underline(False)

            self.directory_page = self._build_directory_page()
            self.view_stack.add_titled_with_icon(
                self.directory_page,
                "directory",
                "Directory",
                "folder-symbolic",
            )

            self.evidence_page = self._build_evidence_page()
            self.view_stack.add_titled_with_icon(
                self.evidence_page,
                "evidence",
                "Evidence",
                "dialog-information-symbolic",
            )
            detail.append(self.view_stack)
            return detail

        def _build_summary_page(self) -> Any:
            page = Adw.PreferencesPage()
            self.image_group = Adw.PreferencesGroup(title="Image")
            self.interpretation_group = Adw.PreferencesGroup(title="Best interpretation")
            self.facts_group = Adw.PreferencesGroup(title="Observed and derived facts")
            self._group_rows: dict[int, list[Any]] = {
                id(self.image_group): [],
                id(self.interpretation_group): [],
                id(self.facts_group): [],
            }
            page.add(self.image_group)
            page.add(self.interpretation_group)
            page.add(self.facts_group)
            return page

        def _build_directory_page(self) -> Any:
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            outer.set_margin_top(12)
            outer.set_margin_bottom(18)
            outer.set_margin_start(18)
            outer.set_margin_end(18)

            self.directory_heading = Gtk.Label(
                label="No CP/M directory selected",
                xalign=0,
            )
            self.directory_heading.add_css_class("title-3")
            outer.append(self.directory_heading)

            header = Gtk.Grid(column_spacing=12)
            for index, (text, width) in enumerate(
                (("User", 6), ("Filename", 20), ("Extent", 8), ("Records", 8), ("Approx. bytes", 12))
            ):
                label = Gtk.Label(label=text, xalign=0)
                label.set_width_chars(width)
                label.add_css_class("caption")
                label.add_css_class("dim-label")
                header.attach(label, index, 0, 1, 1)
            outer.append(header)

            self.directory_list = Gtk.ListBox()
            self.directory_list.set_selection_mode(Gtk.SelectionMode.NONE)
            self.directory_list.add_css_class("boxed-list")
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_vexpand(True)
            scroll.set_child(self.directory_list)
            outer.append(scroll)
            return outer

        def _build_evidence_page(self) -> Any:
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroll.set_margin_top(12)
            scroll.set_margin_bottom(18)
            scroll.set_margin_start(18)
            scroll.set_margin_end(18)

            self.evidence_view = Gtk.TextView()
            self.evidence_view.set_editable(False)
            self.evidence_view.set_cursor_visible(False)
            self.evidence_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            self.evidence_view.set_monospace(True)
            self.evidence_view.set_left_margin(12)
            self.evidence_view.set_right_margin(12)
            self.evidence_view.set_top_margin(12)
            self.evidence_view.set_bottom_margin(12)
            scroll.set_child(self.evidence_view)
            return scroll

        def _choose_image(self, _button: Any) -> None:
            chooser = Gtk.FileChooserNative(
                title="Open disk image",
                transient_for=self,
                action=Gtk.FileChooserAction.OPEN,
                accept_label="Open",
                cancel_label="Cancel",
            )
            image_filter = Gtk.FileFilter()
            image_filter.set_name("Disk images")
            for pattern in ("*.img", "*.imd", "*.dsk", "*.raw", "*.IMG", "*.IMD"):
                image_filter.add_pattern(pattern)
            chooser.add_filter(image_filter)
            chooser.add_filter(self._all_files_filter(Gtk))
            chooser.connect("response", self._open_response)
            chooser.show()

        @staticmethod
        def _all_files_filter(gtk: Any) -> Any:
            file_filter = gtk.FileFilter()
            file_filter.set_name("All files")
            file_filter.add_pattern("*")
            return file_filter

        def _open_response(self, chooser: Any, response: int) -> None:
            if response == Gtk.ResponseType.ACCEPT:
                selected = chooser.get_file()
                if selected is not None and selected.get_path():
                    self.load_path(Path(selected.get_path()))
            chooser.destroy()

        def load_path(self, path: Path) -> None:
            self.current_path = path
            self._analyze_current_path()

        def _profile_changed(self, _dropdown: Any, _parameter: Any) -> None:
            if self.current_path is not None:
                self._analyze_current_path()

        def _analyze_current_path(self) -> None:
            assert self.current_path is not None
            self.open_button.set_sensitive(False)
            self.window_title.set_subtitle(f"Analyzing {self.current_path.name}…")
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            try:
                selected = self.profile_dropdown.get_selected()
                profile_id = self._profile_ids[selected]
                self.result = analyze_image(self.current_path, profile_id)
                self._populate_result()
            except Exception as exc:  # GUI boundary: report failures rather than exit.
                self.result = None
                self.export_button.set_sensitive(False)
                self.window_title.set_subtitle("Analysis failed")
                self._show_error("Analysis failed", str(exc))
            finally:
                self.open_button.set_sensitive(True)

        def _populate_result(self) -> None:
            assert self.result is not None
            result = self.result
            self.export_button.set_sensitive(True)
            self.window_title.set_subtitle(result.path.name)
            self.sidebar_status.set_label(
                f"{result.container.upper()} • {result.size:,} bytes • analyzed read-only"
            )

            self._populate_summary(result)
            self._clear_list_box(self.candidate_list)
            self._candidate_rows.clear()
            for index, candidate in enumerate(result.candidates):
                row = self._candidate_row(candidate, index)
                self._candidate_rows.append(row)
                self.candidate_list.append(row)

            if self._candidate_rows:
                self.candidate_list.select_row(self._candidate_rows[0])
            else:
                self._clear_candidate_details()
                self.toast_overlay.add_toast(
                    Adw.Toast(title="No supported profile produced a credible match")
                )

        def _populate_summary(self, result: ImageResult) -> None:
            self._clear_preferences_group(self.image_group)
            self._clear_preferences_group(self.interpretation_group)
            self._clear_preferences_group(self.facts_group)

            self._add_value_row(self.image_group, "File", result.path.name)
            self._add_value_row(self.image_group, "Container", result.container.upper())
            self._add_value_row(self.image_group, "Source size", f"{result.size:,} bytes")
            self._add_value_row(self.image_group, "SHA-256", result.sha256, selectable=True)

            best = result.best_candidate
            if best is None:
                row = Adw.ActionRow(
                    title="No credible match",
                    subtitle="The format may not be cataloged yet.",
                )
                row.add_prefix(Gtk.Image.new_from_icon_name("dialog-question-symbolic"))
                self._add_group_row(self.interpretation_group, row)
            else:
                self._add_value_row(self.interpretation_group, "Profile", best.profile_name)
                self._add_value_row(
                    self.interpretation_group,
                    "Confidence",
                    f"{best.confidence.title()} ({best.score}/100)",
                )
                self._add_value_row(
                    self.interpretation_group,
                    "Directory extents",
                    str(len(best.files)),
                )

            for fact in result.observations:
                row = Adw.ActionRow(title=fact)
                row.add_prefix(Gtk.Image.new_from_icon_name("emblem-ok-symbolic"))
                self._add_group_row(self.facts_group, row)
            for warning in result.warnings:
                row = Adw.ActionRow(title=warning)
                row.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
                self._add_group_row(self.facts_group, row)

        def _candidate_row(self, candidate: CandidateResult, index: int) -> Any:
            row = Gtk.ListBoxRow()
            row.candidate_index = index
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            box.set_margin_top(10)
            box.set_margin_bottom(10)
            box.set_margin_start(12)
            box.set_margin_end(12)

            title = Gtk.Label(label=candidate.profile_name, xalign=0, wrap=True)
            title.add_css_class("heading")
            box.append(title)

            geometry = candidate.geometry
            detail = Gtk.Label(
                label=(
                    f"{candidate.confidence.title()} • {candidate.score}/100\n"
                    f"{geometry['cylinders']} cyl • {geometry['heads']} head • "
                    f"{geometry['sectors_per_track']} × {geometry['sector_size']} bytes"
                ),
                xalign=0,
                wrap=True,
            )
            detail.add_css_class("caption")
            detail.add_css_class("dim-label")
            box.append(detail)
            row.set_child(box)
            return row

        def _candidate_selected(self, _list_box: Any, row: Any | None) -> None:
            if row is None or self.result is None:
                return
            self._display_candidate(self.result.candidates[row.candidate_index])

        def _display_candidate(self, candidate: CandidateResult) -> None:
            self._clear_list_box(self.directory_list)
            self.directory_heading.set_label(
                f"{candidate.profile_name} — {len(candidate.files)} directory extent(s)"
            )
            for entry in candidate.files:
                row = Gtk.ListBoxRow()
                grid = Gtk.Grid(column_spacing=12)
                grid.set_margin_top(8)
                grid.set_margin_bottom(8)
                grid.set_margin_start(10)
                grid.set_margin_end(10)
                values = (
                    (f"{entry.user:02d}", 6),
                    (entry.name, 20),
                    (str(entry.extent), 8),
                    (str(entry.records), 8),
                    (f"{entry.estimated_size:,}", 12),
                )
                for index, (text, width) in enumerate(values):
                    label = Gtk.Label(label=text, xalign=0)
                    label.set_width_chars(width)
                    label.set_selectable(index == 1)
                    grid.attach(label, index, 0, 1, 1)
                row.set_child(grid)
                self.directory_list.append(row)

            evidence_lines = [
                f"{candidate.profile_name}",
                f"Confidence: {candidate.confidence} ({candidate.score}/100)",
                "",
            ]
            evidence_lines.extend(
                f"[{item.category.upper():8}] {item.points:+3d}  {item.message}"
                for item in candidate.evidence
            )
            evidence_lines.extend(f"\nWARNING: {warning}" for warning in candidate.warnings)
            self.evidence_view.get_buffer().set_text("\n".join(evidence_lines))

        def _clear_candidate_details(self) -> None:
            self._clear_list_box(self.directory_list)
            self.directory_heading.set_label("No CP/M directory selected")
            self.evidence_view.get_buffer().set_text(
                "No candidate evidence is available for this image."
            )

        def _show_empty_state(self) -> None:
            self._clear_preferences_group(self.image_group)
            self._clear_preferences_group(self.interpretation_group)
            self._clear_preferences_group(self.facts_group)
            row = Adw.ActionRow(
                title="Open a disk image",
                subtitle="Choose an IMG, IMD, DSK, or RAW file. The source will not be modified.",
            )
            row.add_prefix(Gtk.Image.new_from_icon_name("document-open-symbolic"))
            self._add_group_row(self.image_group, row)
            self._clear_candidate_details()

        def _choose_export_path(self, _button: Any) -> None:
            if self.result is None:
                return
            chooser = Gtk.FileChooserNative(
                title="Export JSON analysis",
                transient_for=self,
                action=Gtk.FileChooserAction.SAVE,
                accept_label="Save",
                cancel_label="Cancel",
            )
            chooser.set_current_name(f"{self.result.path.stem}-analysis.json")
            json_filter = Gtk.FileFilter()
            json_filter.set_name("JSON report")
            json_filter.add_pattern("*.json")
            chooser.add_filter(json_filter)
            chooser.connect("response", self._export_response)
            chooser.show()

        def _export_response(self, chooser: Any, response: int) -> None:
            if response == Gtk.ResponseType.ACCEPT and self.result is not None:
                selected = chooser.get_file()
                if selected is not None and selected.get_path():
                    path = Path(selected.get_path())
                    try:
                        path.write_text(as_json(self.result) + "\n", encoding="utf-8")
                        self.toast_overlay.add_toast(
                            Adw.Toast(title=f"Report saved as {path.name}")
                        )
                    except OSError as exc:
                        self._show_error("Could not save report", str(exc))
            chooser.destroy()

        def _show_error(self, heading: str, body: str) -> None:
            dialog = Adw.MessageDialog.new(self, heading, body)
            dialog.add_response("close", "Close")
            dialog.set_default_response("close")
            dialog.set_close_response("close")
            dialog.present()

        @staticmethod
        def _clear_list_box(list_box: Any) -> None:
            child = list_box.get_first_child()
            while child is not None:
                next_child = child.get_next_sibling()
                list_box.remove(child)
                child = next_child

        def _clear_preferences_group(self, group: Any) -> None:
            rows = self._group_rows[id(group)]
            for row in rows:
                group.remove(row)
            rows.clear()

        def _add_value_row(
            self,
            group: Any,
            title: str,
            value: str,
            *,
            selectable: bool = False,
        ) -> None:
            row = Adw.ActionRow(title=title)
            label = Gtk.Label(label=value, xalign=1, selectable=selectable)
            label.set_max_width_chars(64)
            label.add_css_class("dim-label")
            row.add_suffix(label)
            self._add_group_row(group, row)

        def _add_group_row(self, group: Any, row: Any) -> None:
            group.add(row)
            self._group_rows[id(group)].append(row)

    return AnalyzerApplication()


def main() -> int:
    try:
        application = create_application()
    except RuntimeError as exc:
        print(f"cpm-disk-analyzer-gui: {exc}", file=sys.stderr)
        return 2
    return int(application.run(sys.argv))


if __name__ == "__main__":
    raise SystemExit(main())

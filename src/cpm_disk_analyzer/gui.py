"""Native GTK4/libadwaita front end for CP/M Disk Analyzer."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

from .analyzer import analyze_image
from .containers import read_image
from .filesystem import (
    FilesystemError,
    extract_logical_file,
    group_directory_entries,
    insert_files_into_raw_image,
    plan_imports,
)
from .models import CandidateResult, ImageResult, LogicalFile
from .profiles import get_profile, load_profiles
from .report import as_json
from .settings import load_window_state, save_window_state


APPLICATION_ID = "io.github.peclark1.CpmDiskAnalyzer"


def _load_gtk() -> tuple[Any, Any, Any, Any, Any]:
    """Load system GTK bindings only when the GUI is actually requested."""
    try:
        import gi

        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gdk, Gio, GLib, Gtk
    except (ImportError, ValueError) as exc:
        raise RuntimeError(
            "GTK4/libadwaita is not available. On Ubuntu, install it with:\n"
            "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n"
            "When using a virtual environment, create it with "
            "'python3 -m venv --system-site-packages .venv'."
        ) from exc
    return Adw, Gdk, Gio, GLib, Gtk


def create_application() -> Any:
    """Create the application after GTK has been loaded successfully."""
    Adw, Gdk, Gio, GLib, Gtk = _load_gtk()

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
            window_state = load_window_state()
            self.set_default_size(window_state.width, window_state.height)
            self.set_size_request(780, 520)
            self._last_normal_size = (window_state.width, window_state.height)
            self._geometry_timer = GLib.timeout_add_seconds(1, self._capture_window_size)
            self.connect("close-request", self._window_closing)
            if window_state.maximized:
                GLib.idle_add(self.maximize)

            self.result: ImageResult | None = None
            self.current_path: Path | None = None
            self.current_candidate: CandidateResult | None = None
            self._drag_directory = tempfile.TemporaryDirectory(
                prefix="cpm-disk-analyzer-drag-"
            )
            # Keep asynchronous chooser objects alive until their callbacks
            # finish. This avoids PyGObject/native-dialog lifetime crashes.
            self._open_dialog: Any | None = None
            self._save_dialog: Any | None = None
            self._import_dialog: Any | None = None
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
                subtitle="Disk-image analysis and file transfer",
            )
            self.header_bar.set_title_widget(self.window_title)
            self.toolbar_view.add_top_bar(self.header_bar)

            self.open_button = Gtk.Button(label="Open…")
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
            self.drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
            self.drop_target.set_preload(True)
            self.drop_target.connect("drop", self._files_dropped)
            self.add_controller(self.drop_target)
            self._show_empty_state()

        def _capture_window_size(self) -> bool:
            if not self.is_maximized():
                width, height = self.get_width(), self.get_height()
                if width >= 780 and height >= 520:
                    self._last_normal_size = (width, height)
            return True

        def _window_closing(self, _window: Any) -> bool:
            if self._geometry_timer:
                GLib.source_remove(self._geometry_timer)
                self._geometry_timer = 0
            self._capture_window_size()
            try:
                save_window_state(
                    self._last_normal_size[0],
                    self._last_normal_size[1],
                    self.is_maximized(),
                )
            except OSError:
                pass
            return False

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

            user_label = Gtk.Label(label="DROP TARGET USER", xalign=0)
            user_label.set_margin_top(8)
            user_label.add_css_class("caption")
            user_label.add_css_class("dim-label")
            sidebar.append(user_label)

            self.user_dropdown = Gtk.DropDown.new_from_strings(
                [f"User {user}" for user in range(16)]
            )
            self.user_dropdown.set_tooltip_text(
                "CP/M user area used when host files are dropped into a raw image"
            )
            sidebar.append(self.user_dropdown)

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

            transfer_hint = Gtk.Label(
                label=(
                    "Drag selected files to the desktop to extract copies. "
                    "Drop host files here to copy them into a raw image."
                ),
                xalign=0,
                wrap=True,
            )
            transfer_hint.add_css_class("caption")
            transfer_hint.add_css_class("dim-label")
            outer.append(transfer_hint)

            header = Gtk.Grid(column_spacing=12)
            for index, (text, width) in enumerate(
                (
                    ("User", 6),
                    ("Filename", 20),
                    ("Extents", 8),
                    ("Records", 8),
                    ("Approx. bytes", 12),
                )
            ):
                label = Gtk.Label(label=text, xalign=0)
                label.set_width_chars(width)
                label.add_css_class("caption")
                label.add_css_class("dim-label")
                header.attach(label, index, 0, 1, 1)
            outer.append(header)

            self.directory_list = Gtk.ListBox()
            self.directory_list.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
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
            dialog = Gtk.FileDialog()
            dialog.set_title("Open disk image")
            dialog.set_modal(True)
            dialog.set_accept_label("Open")
            image_filter = Gtk.FileFilter()
            image_filter.set_name("Disk images")
            for pattern in ("*.img", "*.imd", "*.dsk", "*.raw", "*.IMG", "*.IMD"):
                image_filter.add_pattern(pattern)
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(image_filter)
            filters.append(self._all_files_filter(Gtk))
            dialog.set_filters(filters)
            dialog.set_default_filter(image_filter)
            self._open_dialog = dialog
            dialog.open(self, None, self._open_response)

        @staticmethod
        def _all_files_filter(gtk: Any) -> Any:
            file_filter = gtk.FileFilter()
            file_filter.set_name("All files")
            file_filter.add_pattern("*")
            return file_filter

        def _open_response(self, dialog: Any, result: Any) -> None:
            try:
                selected = dialog.open_finish(result)
            except GLib.Error:
                # Dismissing the asynchronous dialog is reported as an error.
                return
            finally:
                self._open_dialog = None
            path = selected.get_path()
            if path:
                self.load_path(Path(path))
            else:
                self._show_error("Cannot open image", "Please choose a local disk-image file.")

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
            if result.container == "raw":
                transfer_status = "drag out or drop in with confirmation"
            else:
                transfer_status = "drag out; IMD import is not yet supported"
            self.sidebar_status.set_label(
                f"{result.container.upper()} • {result.size:,} bytes • {transfer_status}"
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
                    "Logical files",
                    str(len(group_directory_entries(best.files))),
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
            self.current_candidate = candidate
            self._clear_list_box(self.directory_list)
            logical_files = group_directory_entries(candidate.files)
            self.directory_heading.set_label(
                f"{candidate.profile_name} — {len(logical_files)} file(s)"
            )
            for logical_file in logical_files:
                row = Gtk.ListBoxRow()
                row.logical_file = logical_file
                grid = Gtk.Grid(column_spacing=12)
                grid.set_margin_top(8)
                grid.set_margin_bottom(8)
                grid.set_margin_start(10)
                grid.set_margin_end(10)
                values = (
                    (f"{logical_file.user:02d}", 6),
                    (logical_file.name, 20),
                    (str(len(logical_file.extents)), 8),
                    (str(logical_file.records), 8),
                    (f"{logical_file.estimated_size:,}", 12),
                )
                for index, (text, width) in enumerate(values):
                    label = Gtk.Label(label=text, xalign=0)
                    label.set_width_chars(width)
                    grid.attach(label, index, 0, 1, 1)
                row.set_child(grid)
                drag_source = Gtk.DragSource()
                drag_source.set_actions(Gdk.DragAction.COPY)
                drag_source.connect("prepare", self._prepare_file_drag, row)
                row.add_controller(drag_source)
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

        def _prepare_file_drag(
            self, _source: Any, _x: float, _y: float, row: Any
        ) -> Any | None:
            if self.current_path is None or self.current_candidate is None:
                return None

            selected_rows = list(self.directory_list.get_selected_rows())
            if row not in selected_rows:
                self.directory_list.unselect_all()
                self.directory_list.select_row(row)
                selected_rows = [row]
            logical_files: list[LogicalFile] = [
                selected.logical_file
                for selected in selected_rows
                if hasattr(selected, "logical_file")
            ]
            if not logical_files:
                return None

            try:
                container = read_image(self.current_path)
                profile = get_profile(self.current_candidate.profile_id)
                transfer_directory = Path(
                    tempfile.mkdtemp(
                        prefix="transfer-", dir=self._drag_directory.name
                    )
                )
                duplicate_names = {
                    item.name
                    for item in logical_files
                    if sum(other.name == item.name for other in logical_files) > 1
                }
                exported_files: list[Any] = []
                for logical_file in logical_files:
                    output_name = logical_file.name
                    if logical_file.name in duplicate_names:
                        output_name = f"U{logical_file.user:02d}_{logical_file.name}"
                    destination = transfer_directory / output_name
                    destination.write_bytes(
                        extract_logical_file(
                            container.logical_data, profile, logical_file
                        )
                    )
                    exported_files.append(Gio.File.new_for_path(str(destination)))
            except (OSError, FilesystemError, KeyError) as exc:
                GLib.idle_add(self._show_error, "Could not extract file", str(exc))
                return None

            return Gdk.ContentProvider.new_for_value(
                Gdk.FileList.new_from_list(exported_files)
            )

        def _files_dropped(
            self, _target: Any, value: Any, _x: float, _y: float
        ) -> bool:
            if self.current_path is None or self.result is None or self.current_candidate is None:
                self._show_error("No disk is open", "Open and select a CP/M disk image first.")
                return False
            if self.result.container != "raw":
                self._show_error(
                    "ImageDisk import is not supported yet",
                    "Files can be dragged out of IMD images, but files can currently "
                    "be copied into raw IMG, DSK, or RAW images only.",
                )
                return False

            source_paths: list[Path] = []
            for dropped_file in value.get_files():
                path = dropped_file.get_path()
                if path is None:
                    self._show_error(
                        "Cannot copy remote files",
                        "Drop files stored on the local computer.",
                    )
                    return False
                source_paths.append(Path(path))
            if not source_paths:
                return False

            profile = get_profile(self.current_candidate.profile_id)
            user = int(self.user_dropdown.get_selected())
            try:
                plans = plan_imports(source_paths, profile)
            except (OSError, FilesystemError) as exc:
                self._show_error("Cannot copy files into disk", str(exc))
                return False

            existing = {
                (entry.user, entry.name) for entry in self.current_candidate.files
            }
            conflicts = [plan.cpm_name for plan in plans if (user, plan.cpm_name) in existing]
            if conflicts:
                self._show_error(
                    "A file already exists",
                    f"User {user} already contains: {', '.join(conflicts)}. "
                    "Existing CP/M files are never replaced by drag and drop.",
                )
                return False

            mappings = [
                f"{plan.source.name}  →  {plan.cpm_name}" for plan in plans[:12]
            ]
            if len(plans) > 12:
                mappings.append(f"…and {len(plans) - 12} more")
            body = (
                f"Copy {len(plans)} host file(s) into CP/M user {user} on "
                f"{self.current_path.name}?\n\n"
                + "\n".join(mappings)
                + "\n\nThis will modify the original raw disk image. Existing files "
                "will not be replaced. CP/M stores sizes in 128-byte records, so "
                "the final record is padded when necessary."
            )
            dialog = Adw.MessageDialog.new(self, "Copy files into disk?", body)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("copy", "Copy into Disk")
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")
            dialog.set_response_appearance("copy", Adw.ResponseAppearance.SUGGESTED)
            dialog.connect(
                "response",
                self._import_response,
                self.current_path,
                self.current_candidate.profile_id,
                user,
                plans,
            )
            self._import_dialog = dialog
            dialog.present()
            return True

        def _import_response(
            self,
            _dialog: Any,
            response: str,
            image_path: Path,
            profile_id: str,
            user: int,
            plans: list[Any],
        ) -> None:
            self._import_dialog = None
            if response != "copy":
                return
            if self.current_path != image_path:
                self._show_error(
                    "Disk changed",
                    "The open disk changed before the copy was confirmed. Drop the files again.",
                )
                return

            try:
                imported = insert_files_into_raw_image(
                    image_path,
                    profile_id,
                    [plan.source for plan in plans],
                    user=user,
                )
                self._analyze_current_path()
            except (OSError, FilesystemError, KeyError) as exc:
                self._show_error("Could not copy files into disk", str(exc))
                return
            self.toast_overlay.add_toast(
                Adw.Toast(
                    title=f"Copied {len(imported)} file(s) into CP/M user {user}"
                )
            )

        def _clear_candidate_details(self) -> None:
            self.current_candidate = None
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
                subtitle=(
                    "Choose an IMG, IMD, DSK, or RAW file. Analysis and extraction "
                    "are read-only; imports require confirmation."
                ),
            )
            row.add_prefix(Gtk.Image.new_from_icon_name("document-open-symbolic"))
            open_button = Gtk.Button(label="Open…")
            open_button.set_valign(Gtk.Align.CENTER)
            open_button.connect("clicked", self._choose_image)
            row.add_suffix(open_button)
            # Setting an activatable widget makes clicks anywhere on the row,
            # plus Enter/Space keyboard activation, invoke the same button.
            row.set_activatable_widget(open_button)
            self._add_group_row(self.image_group, row)
            self._clear_candidate_details()

        def _choose_export_path(self, _button: Any) -> None:
            if self.result is None:
                return
            dialog = Gtk.FileDialog()
            dialog.set_title("Export JSON analysis")
            dialog.set_modal(True)
            dialog.set_accept_label("Save")
            dialog.set_initial_name(f"{self.result.path.stem}-analysis.json")
            json_filter = Gtk.FileFilter()
            json_filter.set_name("JSON report")
            json_filter.add_pattern("*.json")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(json_filter)
            dialog.set_filters(filters)
            dialog.set_default_filter(json_filter)
            self._save_dialog = dialog
            dialog.save(self, None, self._export_response)

        def _export_response(self, dialog: Any, result: Any) -> None:
            try:
                selected = dialog.save_finish(result)
            except GLib.Error:
                return
            finally:
                self._save_dialog = None
            if self.result is None:
                return
            selected_path = selected.get_path()
            if not selected_path:
                self._show_error("Could not save report", "Please choose a local folder.")
                return
            path = Path(selected_path)
            try:
                path.write_text(as_json(self.result) + "\n", encoding="utf-8")
                self.toast_overlay.add_toast(
                    Adw.Toast(title=f"Report saved as {path.name}")
                )
            except OSError as exc:
                self._show_error("Could not save report", str(exc))

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

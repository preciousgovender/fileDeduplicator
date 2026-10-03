"""Tkinter UI for the file deduplicator."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from scanner import FileEntry, ScanResult, Scanner, human_size, wasted_bytes


IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


def open_in_file_manager(path: Path) -> None:
    """Reveal file in the OS file manager."""
    try:
        if IS_WIN:
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif IS_MAC:
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception:
        pass


def open_with_default(path: Path) -> None:
    try:
        if IS_WIN:
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif IS_MAC:
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def read_preview(path: Path, max_bytes: int = 4096) -> str:
    """Best-effort text preview; binary shown as hex-ish placeholder."""
    try:
        with open(path, "rb") as f:
            head = f.read(max_bytes)
    except OSError as e:
        return f"<error reading file: {e}>"

    if b"\x00" in head:
        # binary — show first bytes as hex dump
        lines = []
        for i in range(0, min(len(head), 256), 16):
            chunk = head[i : i + 16]
            hexpart = " ".join(f"{b:02x}" for b in chunk)
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{i:08x}  {hexpart:<47}  {asc}")
        return "\n".join(lines) + ("\n..." if len(head) >= 256 else "")

    try:
        return head.decode("utf-8", errors="replace")
    except Exception:
        return "<unable to decode>"


class GroupView(ttk.Frame):
    """Detail pane for one duplicate group: file list + side-by-side preview."""

    def __init__(self, master: tk.Misc, on_delete: callable) -> None:
        super().__init__(master)
        self.on_delete = on_delete
        self.group: list[FileEntry] = []
        self.keep_index: int | None = None

        # --- top: file list with radio "keep" ---
        top = ttk.LabelFrame(self, text="Files in group (choose one to KEEP)")
        top.pack(fill="x", padx=4, pady=4)

        self.keep_var = tk.IntVar(value=-1)
        self.rows: list[dict] = []

        self.list_frame = ttk.Frame(top)
        self.list_frame.pack(fill="x", padx=4, pady=4)

        # --- bottom: previews ---
        prev = ttk.LabelFrame(self, text="Preview")
        prev.pack(fill="both", expand=True, padx=4, pady=4)
        prev.columnconfigure(0, weight=1)
        prev.columnconfigure(1, weight=1)
        prev.rowconfigure(1, weight=1)

        self.prev_titles = [ttk.Label(prev, anchor="w"), ttk.Label(prev, anchor="w")]
        self.prev_titles[0].grid(row=0, column=0, sticky="ew", padx=4)
        self.prev_titles[1].grid(row=0, column=1, sticky="ew", padx=4)

        self.prev_texts = [
            tk.Text(prev, wrap="none", height=14, font=("Consolas", 9)),
            tk.Text(prev, wrap="none", height=14, font=("Consolas", 9)),
        ]
        for i, t in enumerate(self.prev_texts):
            t.grid(row=1, column=i, sticky="nsew", padx=4, pady=4)
            sb = ttk.Scrollbar(prev, command=t.yview)
            t.configure(yscrollcommand=sb.set)
            sb.grid(row=1, column=i, sticky="nse", pady=4)

        # preview selector: which two files
        sel = ttk.Frame(prev)
        sel.grid(row=2, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 4))
        ttk.Label(sel, text="Preview:").pack(side="left")
        self.prev_a_menu = ttk.Combobox(sel, state="readonly", width=40)
        self.prev_b_menu = ttk.Combobox(sel, state="readonly", width=40)
        self.prev_a_menu.pack(side="left", padx=(4, 12))
        ttk.Label(sel, text="vs").pack(side="left")
        self.prev_b_menu.pack(side="left", padx=4)
        self.prev_a_menu.bind("<<ComboboxSelected>>", self._refresh_previews)
        self.prev_b_menu.bind("<<ComboboxSelected>>", self._refresh_previews)

    def load(self, group: list[FileEntry]) -> None:
        self.group = group

        # clear rows
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.rows.clear()
        self.keep_var.set(-1)

        # clear previews first — safe regardless of group content
        for t in self.prev_texts:
            t.configure(state="normal")
            t.delete("1.0", "end")
            t.configure(state="disabled")
        self.prev_titles[0].configure(text="")
        self.prev_titles[1].configure(text="")

        # empty-group path: reset comboboxes without calling .current()
        if not group:
            self.prev_a_menu.set("")
            self.prev_b_menu.set("")
            self.prev_a_menu["values"] = ()
            self.prev_b_menu["values"] = ()
            return

        self.keep_var.set(0)

        for i, entry in enumerate(group):
            row = ttk.Frame(self.list_frame)
            row.pack(fill="x", pady=1)

            rb = ttk.Radiobutton(row, variable=self.keep_var, value=i,
                                 command=self._on_keep_change)
            rb.pack(side="left")

            digest_short = entry.digest[:12] if entry.digest else "?"
            info = f"{entry.path}   ({human_size(entry.size)}, {digest_short}…)"
            ttk.Label(row, text=info, anchor="w").pack(side="left", fill="x", expand=True)

            ttk.Button(row, text="Open", width=6,
                       command=lambda p=entry.path: open_with_default(p)).pack(side="right", padx=2)
            ttk.Button(row, text="Reveal", width=7,
                       command=lambda p=entry.path: open_in_file_manager(p)).pack(side="right", padx=2)

            self.rows.append({"frame": row, "radio": rb})

        # preview selectors
        labels = [str(e.path) for e in group]
        self.prev_a_menu["values"] = labels
        self.prev_b_menu["values"] = labels
        self.prev_a_menu.current(0)
        self.prev_b_menu.current(1 if len(labels) > 1 else 0)
        self._refresh_previews()

    def _on_keep_change(self) -> None:
        pass  # selection is read at delete time

    def _refresh_previews(self, _evt=None) -> None:
        a = self.prev_a_menu.current()
        b = self.prev_b_menu.current()
        for idx, widget in ((a, self.prev_texts[0]), (b, self.prev_texts[1])):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            if 0 <= idx < len(self.group):
                e = self.group[idx]
                widget.insert("1.0", read_preview(e.path))
            widget.configure(state="disabled")

    def get_keep_and_deletes(self) -> tuple[FileEntry | None, list[FileEntry]]:
        keep_idx = self.keep_var.get()
        if not (0 <= keep_idx < len(self.group)):
            return None, []
        keep = self.group[keep_idx]
        deletes = [e for i, e in enumerate(self.group) if i != keep_idx]
        return keep, deletes


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("File Deduplicator")
        self.geometry("1100x720")
        self.minsize(900, 600)

        self.result: ScanResult | None = None
        self.scanner: Scanner | None = None
        self.worker: threading.Thread | None = None
        self.current_group_idx: int | None = None
        self.paths: list[Path] = []

        self._build_ui()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=6, pady=6)

        ttk.Button(toolbar, text="Add Folder…", command=self.add_folder).pack(side="left")
        self.paths_label = ttk.Label(toolbar, text="No folders selected", anchor="w")
        self.paths_label.pack(side="left", padx=8, fill="x", expand=True)

        self.scan_btn = ttk.Button(toolbar, text="Scan", command=self.start_scan)
        self.scan_btn.pack(side="right")
        self.cancel_btn = ttk.Button(toolbar, text="Cancel", command=self.cancel_scan, state="disabled")
        self.cancel_btn.pack(side="right", padx=4)

        # progress
        prog = ttk.Frame(self)
        prog.pack(fill="x", padx=6)
        self.progress = ttk.Progressbar(prog, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.status = ttk.Label(prog, text="Ready.", width=46, anchor="w")
        self.status.pack(side="left", padx=8)

        # main split: groups list (left) + detail (right)
        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6, pady=6)

        left = ttk.LabelFrame(paned, text="Duplicate groups")
        paned.add(left, weight=1)
        right = ttk.LabelFrame(paned, text="Details")
        paned.add(right, weight=3)

        # groups tree
        cols = ("count", "size", "wasted", "example")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("count", text="Files")
        self.tree.heading("size", text="Size each")
        self.tree.heading("wasted", text="Reclaimable")
        self.tree.heading("example", text="Path")
        self.tree.column("count", width=50, anchor="center")
        self.tree.column("size", width=90, anchor="e")
        self.tree.column("wasted", width=90, anchor="e")
        self.tree.column("example", width=220)
        self.tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(left, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_group_select)

        self.group_view = GroupView(right, on_delete=self.delete_current_group)
        self.group_view.pack(fill="both", expand=True)

        # bottom action bar
        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=6, pady=6)
        self.delete_btn = ttk.Button(actions, text="Delete duplicates in selected group",
                                     command=self.delete_current_group, state="disabled")
        self.delete_btn.pack(side="left")
        ttk.Button(actions, text="Delete ALL duplicates (keep first of each)",
                   command=self.delete_all).pack(side="left", padx=8)
        self.summary = ttk.Label(actions, text="")
        self.summary.pack(side="right")

    # ---------------- folder selection ----------------
    def add_folder(self) -> None:
        d = filedialog.askdirectory(title="Choose a folder to scan")
        if not d:
            return
        p = Path(d)
        if p not in self.paths:
            self.paths.append(p)
        self._update_paths_label()

    def _update_paths_label(self) -> None:
        if not self.paths:
            self.paths_label.configure(text="No folders selected")
        else:
            self.paths_label.configure(text=" | ".join(str(p) for p in self.paths))

    # ---------------- scanning ----------------
    def start_scan(self) -> None:
        if not self.paths:
            messagebox.showinfo("No folders", "Add at least one folder to scan.")
            return
        if self.worker and self.worker.is_alive():
            return

        self.result = None
        self.tree.delete(*self.tree.get_children())
        self.group_view.load([])
        self.delete_btn.configure(state="disabled")
        self.progress.configure(value=0, maximum=100)
        self.status.configure(text="Scanning…")

        self.scanner = Scanner(self.paths)
        self.scan_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")

        self.worker = threading.Thread(target=self._scan_thread, daemon=True)
        self.worker.start()

    def cancel_scan(self) -> None:
        if self.scanner:
            self.scanner.cancel()
            self.status.configure(text="Cancelling…")

    def _scan_thread(self) -> None:
        assert self.scanner
        try:
            result = self.scanner.run(
                on_walk=lambda n: self.after(0, self._on_walk, n),
                on_hash=lambda done, total: self.after(0, self._on_hash, done, total),
            )
        except Exception as e:  # includes Cancelled
            self.after(0, self._scan_done, None, str(e))
            return
        self.after(0, self._scan_done, result, None)

    def _on_walk(self, n: int) -> None:
        self.status.configure(text=f"Walking… {n} files found")

    def _on_hash(self, done: int, total: int) -> None:
        pct = (done / total) * 100 if total else 100
        self.progress.configure(value=pct)
        self.status.configure(text=f"Hashing… {done}/{total}")

    def _scan_done(self, result: ScanResult | None, error: str | None) -> None:
        self.scan_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        if error:
            if "cancel" in error.lower():
                self.status.configure(text="Cancelled.")
            else:
                self.status.configure(text=f"Error: {error}")
                messagebox.showerror("Scan error", error)
            return

        assert result is not None
        self.result = result
        self.progress.configure(value=100)

        n_groups = len(result.groups)
        n_files = sum(len(g) for g in result.groups)
        wasted = wasted_bytes(result.groups)
        self.status.configure(
            text=f"Done. {result.files_scanned} files scanned, "
                 f"{n_groups} groups, {human_size(result.bytes_hashed)} hashed."
        )
        self.summary.configure(
            text=f"{n_files} duplicate files in {n_groups} groups "
                 f"→ {human_size(wasted)} reclaimable"
        )

        self._rebuild_tree()

        if result.errors:
            print("\n".join(result.errors[:20]), file=sys.stderr)

    def _rebuild_tree(self) -> None:
        """Re-populate the groups tree from self.result, dropping stale groups."""
        assert self.result is not None

        # drop any group that no longer has >1 survivor on disk
        new_groups: list[list[FileEntry]] = []
        for group in self.result.groups:
            survivors = [e for e in group if e.path.exists()]
            if len(survivors) > 1:
                new_groups.append(survivors)
        self.result.groups = new_groups

        self.tree.delete(*self.tree.get_children())
        for i, group in enumerate(self.result.groups):
            self.tree.insert(
                "", "end", iid=str(i),
                values=(
                    len(group),
                    human_size(group[0].size),
                    human_size((len(group) - 1) * group[0].size),
                    str(group[0].path),
                ),
            )

    # ---------------- group interaction ----------------
    def on_group_select(self, _evt=None) -> None:
        sel = self.tree.selection()
        if not sel or not self.result:
            return
        idx = int(sel[0])
        if idx >= len(self.result.groups):
            return
        self.current_group_idx = idx
        group = self.result.groups[idx]
        self.group_view.load(group)
        self.delete_btn.configure(state="normal")

    def delete_current_group(self) -> None:
        if self.current_group_idx is None or not self.result:
            return
        if self.current_group_idx >= len(self.result.groups):
            return
        group = self.result.groups[self.current_group_idx]
        keep, deletes = self.group_view.get_keep_and_deletes()
        if not keep or not deletes:
            messagebox.showinfo("Pick a keeper", "Select which file to KEEP first.")
            return

        total = sum(e.size for e in deletes)
        confirm = messagebox.askyesno(
            "Delete duplicates?",
            f"Keep:\n  {keep.path}\n\n"
            f"Delete {len(deletes)} file(s) ({human_size(total)}):\n" +
            "\n".join(f"  {e.path}" for e in deletes) +
            "\n\nThis cannot be undone.",
        )
        if not confirm:
            return

        errors = []
        for e in deletes:
            try:
                e.path.unlink()
            except OSError as ex:
                errors.append(f"{e.path}: {ex}")

        if errors:
            messagebox.showerror("Some deletions failed", "\n".join(errors[:10]))

        # rebuild tree iids to match result.groups indices
        self._rebuild_tree()
        self.current_group_idx = None
        self.group_view.load([])
        self.delete_btn.configure(state="disabled")
        self._refresh_summary()

    def delete_all(self) -> None:
        if not self.result or not self.result.groups:
            messagebox.showinfo("Nothing to do", "No duplicate groups.")
            return
        n = sum(len(g) - 1 for g in self.result.groups)
        size = wasted_bytes(self.result.groups)
        if not messagebox.askyesno(
            "Delete all duplicates?",
            f"Delete {n} files, reclaiming {human_size(size)}?\n"
            "In each group, the FIRST file (sorted by path) is kept.\n"
            "This cannot be undone.",
        ):
            return

        errors: list[str] = []
        for group in self.result.groups:
            for e in group[1:]:  # group[0] is the keeper
                try:
                    e.path.unlink()
                except OSError as ex:
                    errors.append(f"{e.path}: {ex}")

        self._rebuild_tree()
        self.current_group_idx = None
        self.group_view.load([])
        self.delete_btn.configure(state="disabled")
        self._refresh_summary()

        if errors:
            messagebox.showerror("Some deletions failed", "\n".join(errors[:10]))

    def _refresh_summary(self) -> None:
        if not self.result:
            return
        n_files = sum(len(g) for g in self.result.groups)
        wasted = wasted_bytes(self.result.groups)
        self.summary.configure(
            text=f"{n_files} duplicate files in {len(self.result.groups)} groups "
                 f"→ {human_size(wasted)} reclaimable"
        )


if __name__ == "__main__":
    App().mainloop()
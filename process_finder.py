from __future__ import annotations
import json
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import (
        PRIMARY, SUCCESS, INFO, WARNING, DANGER, SECONDARY, DARK, LIGHT
    )
    BOOTSTRAP_AVAILABLE = True
    TK = tb.Window
    TTK = tb
except Exception:
    BOOTSTRAP_AVAILABLE = False
    import tkinter as tk
    from tkinter import ttk
    from tkinter import messagebox, filedialog
    TK = tk.Tk
    TTK = ttk
try:
    from tkinter import messagebox, filedialog
except Exception:
    pass
import psutil
def find_processes(
    query,
    *,
    exact: bool = False,
    pid_mode: bool = False,
    use_regex: bool = False,
    use_cmd: bool = False,
) -> List[Dict[str, Any]]:
    """Return a list of psutil process info dicts matching the given query."""
    matches = []
    regex = None
    if use_regex and not pid_mode:
        regex = re.compile(query, re.IGNORECASE)
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            info = proc.info
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        if pid_mode:
            if info["pid"] == query:
                matches.append(info)
            continue
        if use_cmd:
            field_raw = " ".join(info.get("cmdline") or [])
        else:
            field_raw = info.get("name") or ""
        if not field_raw:
            continue
        if use_regex:
            if regex.search(field_raw):
                matches.append(info)
            continue
        field = field_raw.lower()
        q = str(query).lower()
        if exact:
            if field == q:
                matches.append(info)
        else:
            if q in field:
                matches.append(info)
    return matches
def best_exe_path(info: dict) -> str:
    """Best-effort resolve executable path (exe then first cmdline token)."""
    exe = info.get("exe")
    if exe:
        return exe
    cmdline = info.get("cmdline") or []
    if cmdline:
        return cmdline[0]
    return ""
@dataclass
class MatchRow:
    pid: int
    name: str
    exe: str
    cmdline: str
def to_rows(infos: List[Dict[str, Any]], include_cmdline: bool) -> List[MatchRow]:
    rows: List[MatchRow] = []
    for info in infos:
        rows.append(
            MatchRow(
                pid=info["pid"],
                name=info.get("name") or "",
                exe=best_exe_path(info),
                cmdline=" ".join(info.get("cmdline") or []) if include_cmdline else "",
            )
        )
    return rows
def to_json_list(infos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for info in infos:
        out.append(
            {
                "pid": info["pid"],
                "name": info.get("name"),
                "exe": best_exe_path(info),
                "cmdline": info.get("cmdline") or [],
            }
        )
    return out
class ProcessFinderApp(TK):
    """Main application window."""
    def __init__(self):
        if BOOTSTRAP_AVAILABLE:
            super().__init__(title="Process Finder", themename="darkly")
        else:
            super().__init__()
            self.title("Process Finder")
        self.geometry("1100x680")
        self.minsize(900, 540)
        self._build_styles()
        self._build_widgets()
        self._search_thread: Optional[threading.Thread] = None
        self._stop_refresh = threading.Event()
        self._last_results: List[Dict[str, Any]] = []
        self._sort_desc: Dict[str, bool] = {}
    def _build_styles(self):
        if not BOOTSTRAP_AVAILABLE:
            style = TTK.Style()
            style.theme_use("clam")
            style.configure("TButton", padding=6)
            style.configure("TCheckbutton", padding=4)
            style.configure("Treeview", rowheight=24)
            style.configure("TEntry", padding=4)
    def _build_widgets(self):
        main = TTK.Frame(self)
        main.grid(column=0, row=0, sticky="nsew", padx=12, pady=12)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=0)  # left pane fixed
        main.columnconfigure(1, weight=1)  # right pane expands
        main.rowconfigure(0, weight=1)
        self.left = self._build_left_controls(main)
        self.right = self._build_right_results(main)
        self.status_var = self._mk_strvar("")
        status = TTK.Label(self, textvariable=self.status_var, anchor="w")
        status.grid(column=0, row=1, sticky="ew", padx=10, pady=(0, 10))
        self._set_status("Ready.")
    def _build_left_controls(self, parent):
        lf = TTK.Labelframe(parent, text="Search", padding=10)
        lf.grid(column=0, row=0, sticky="nsew")
        for i in range(12):
            lf.rowconfigure(i, weight=0)
        lf.columnconfigure(0, weight=1)
        TTK.Label(lf, text="Query").grid(column=0, row=0, sticky="w")
        self.query_var = self._mk_strvar("")
        self.query_entry = TTK.Entry(lf, textvariable=self.query_var)
        self.query_entry.grid(column=0, row=1, sticky="ew", pady=(2, 8))
        TTK.Label(lf, text="Search in").grid(column=0, row=2, sticky="w")
        self.field_var = self._mk_strvar("Name")
        self.field_combo = TTK.Combobox(
            lf, textvariable=self.field_var, state="readonly",
            values=["Name", "Command line", "PID"],
        )
        self.field_combo.grid(column=0, row=3, sticky="ew", pady=(2, 8))
        self.field_combo.bind("<<ComboboxSelected>>", self._on_field_change)
        self.exact_var = self._mk_boolvar(False)
        self.regex_var = self._mk_boolvar(False)
        self.cmd_var = self._mk_boolvar(False)
        self.first_var = self._mk_boolvar(False)
        self.cmdline_var = self._mk_boolvar(False)
        self.exact_chk = TTK.Checkbutton(lf, text="Exact match", variable=self.exact_var, command=self._enforce_mutual_options)
        self.regex_chk = TTK.Checkbutton(lf, text="Regex", variable=self.regex_var, command=self._enforce_mutual_options)
        self.cmd_chk = TTK.Checkbutton(lf, text="Match against command line", variable=self.cmd_var)
        self.first_chk = TTK.Checkbutton(lf, text="First match only", variable=self.first_var)
        self.cmdline_chk = TTK.Checkbutton(lf, text="Include full command line in table", variable=self.cmdline_var)
        self.exact_chk.grid(column=0, row=4, sticky="w")
        self.regex_chk.grid(column=0, row=5, sticky="w")
        self.cmd_chk.grid(column=0, row=6, sticky="w")
        self.first_chk.grid(column=0, row=7, sticky="w")
        self.cmdline_chk.grid(column=0, row=8, sticky="w")
        btn_frame = TTK.Frame(lf)
        btn_frame.grid(column=0, row=9, sticky="ew", pady=(8, 4))
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)
        self.search_btn = TTK.Button(btn_frame, text="Search", command=self._on_search)
        self.search_btn.grid(column=0, row=0, sticky="ew", padx=(0, 6))
        self.clear_btn = TTK.Button(btn_frame, text="Clear", command=self._on_clear)
        self.clear_btn.grid(column=1, row=0, sticky="ew")
        util_frame = TTK.Frame(lf)
        util_frame.grid(column=0, row=10, sticky="ew", pady=(4, 8))
        util_frame.columnconfigure(0, weight=1)
        util_frame.columnconfigure(1, weight=1)
        self.copy_first_btn = TTK.Button(util_frame, text="Copy EXE of first match", command=self._on_copy_first_exe)
        self.copy_first_btn.grid(column=0, row=0, sticky="ew", padx=(0, 6))
        self.copy_json_btn = TTK.Button(util_frame, text="Copy JSON", command=self._on_copy_json)
        self.copy_json_btn.grid(column=1, row=0, sticky="ew")
        refresh_frame = TTK.Labelframe(lf, text="Auto refresh", padding=8)
        refresh_frame.grid(column=0, row=11, sticky="ew", pady=(8, 0))
        refresh_frame.columnconfigure(0, weight=1)
        refresh_frame.columnconfigure(1, weight=1)
        refresh_frame.columnconfigure(2, weight=1)
        self.auto_var = self._mk_boolvar(False)
        self.interval_var = self._mk_intvar(3)
        self.auto_chk = TTK.Checkbutton(refresh_frame, text="Enable", variable=self.auto_var, command=self._on_auto_toggle)
        self.auto_chk.grid(column=0, row=0, sticky="w")
        TTK.Label(refresh_frame, text="Interval (s)").grid(column=1, row=0, sticky="e")
        self.interval_spin = TTK.Spinbox(refresh_frame, from_=1, to=60, textvariable=self.interval_var, width=6)
        self.interval_spin.grid(column=2, row=0, sticky="w", padx=(6, 0))
        return lf
    def _build_right_results(self, parent):
        rf = TTK.Labelframe(parent, text="Results", padding=10)
        rf.grid(column=1, row=0, sticky="nsew")
        rf.columnconfigure(0, weight=1)
        rf.rowconfigure(1, weight=1)
        bar = TTK.Frame(rf)
        bar.grid(column=0, row=0, sticky="ew", pady=(0, 6))
        for i in range(5):
            bar.columnconfigure(i, weight=0)
        bar.columnconfigure(5, weight=1)
        self.save_json_btn = TTK.Button(bar, text="Save JSON…", command=self._on_save_json)
        self.save_csv_btn = TTK.Button(bar, text="Save CSV…", command=self._on_save_csv)
        self.copy_table_btn = TTK.Button(bar, text="Copy table (TSV)", command=self._on_copy_table)
        self.save_json_btn.grid(column=0, row=0, padx=(0, 6))
        self.save_csv_btn.grid(column=1, row=0, padx=(0, 6))
        self.copy_table_btn.grid(column=2, row=0, padx=(0, 6))
        columns = ("PID", "NAME", "EXE", "CMDLINE")
        self.tree = TTK.Treeview(rf, columns=columns, show="headings", selectmode="extended")
        for col in columns:
            self.tree.heading(col, text=col, command=lambda c=col: self._sort_by_column(c))
        self.tree.column("PID", width=100, stretch=False, anchor="center")
        self.tree.column("NAME", width=200, stretch=True)
        self.tree.column("EXE", width=380, stretch=True)
        self.tree.column("CMDLINE", width=600, stretch=True)
        yscroll = TTK.Scrollbar(rf, orient="vertical", command=self.tree.yview)
        xscroll = TTK.Scrollbar(rf, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(column=0, row=1, sticky="nsew")
        yscroll.grid(column=1, row=1, sticky="ns")
        xscroll.grid(column=0, row=2, sticky="ew")
        self.tree.bind("<Double-1>", self._on_row_double_click)
        return rf
    def _mk_strvar(self, value: str):
        if BOOTSTRAP_AVAILABLE:
            return TTK.StringVar(value=value)
        import tkinter as tk
        return tk.StringVar(value=value)
    def _mk_boolvar(self, value: bool):
        if BOOTSTRAP_AVAILABLE:
            return TTK.BooleanVar(value=value)
        import tkinter as tk
        return tk.BooleanVar(value=value)
    def _mk_intvar(self, value: int):
        if BOOTSTRAP_AVAILABLE:
            return TTK.IntVar(value=value)
        import tkinter as tk
        return tk.IntVar(value=value)
    def _on_field_change(self, _evt=None):
        """If PID is selected, disable regex/exact to match CLI semantics."""
        field = self.field_var.get()
        if field == "PID":
            self.regex_var.set(False)
            self.exact_var.set(False)
            self.exact_chk.configure(state="disabled")
            self.regex_chk.configure(state="disabled")
            self.cmd_var.set(False)
            self.cmd_chk.configure(state="disabled")
        else:
            self.exact_chk.configure(state="normal")
            self.regex_chk.configure(state="normal")
            self.cmd_chk.configure(state="normal")
    def _enforce_mutual_options(self):
        """CLI behavior: --regex overrides --exact (we disable exact when regex on)."""
        if self.regex_var.get():
            self.exact_var.set(False)
    def _on_search(self):
        query = self.query_var.get().strip()
        if not query:
            messagebox.showinfo("Search", "Please enter a query.")
            return
        field = self.field_var.get()
        pid_mode = False
        query_value = query
        if field == "PID":
            try:
                query_value = int(query)
                pid_mode = True
            except ValueError:
                messagebox.showerror("Invalid PID", "Please enter a numeric PID.")
                return
        use_regex = self.regex_var.get()
        exact = self.exact_var.get()
        use_cmd = self.cmd_var.get()
        include_cmdline = self.cmdline_var.get()
        first_only = self.first_var.get()
        if use_regex and not pid_mode:
            try:
                re.compile(query)  # just validate
            except re.error as e:
                messagebox.showerror("Invalid regular expression", f"Regex error: {e}")
                return
        self._disable_controls()
        self._set_status("Searching…")
        def worker():
            start = time.time()
            try:
                matches = find_processes(
                    query_value,
                    exact=exact,
                    pid_mode=pid_mode,
                    use_regex=use_regex,
                    use_cmd=use_cmd,
                )
                self._last_results = matches
                display_infos = matches[:1] if first_only and matches else matches
                rows = to_rows(display_infos, include_cmdline=include_cmdline)
            except Exception as ex:
                self.after(0, lambda: messagebox.showerror("Error", str(ex)))
                rows = []
                matches = []
            elapsed = time.time() - start
            self.after(0, lambda: self._populate_tree(rows))
            self.after(0, lambda: self._set_status(
                f"Found {len(matches)} match(es) in {elapsed:.3f}s"
                + (" (showing first only)" if first_only else "")
            ))
            self.after(0, self._enable_controls)
        self._search_thread = threading.Thread(target=worker, daemon=True)
        self._search_thread.start()
    def _on_clear(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._last_results = []
        self._set_status("Cleared.")
    def _on_copy_first_exe(self):
        if not self._last_results:
            messagebox.showinfo("Copy", "No results to copy.")
            return
        info = self._last_results[0]
        exe = best_exe_path(info) or ""
        self.clipboard_clear()
        self.clipboard_append(exe)
        self._set_status("Copied EXE path of first match to clipboard.")
    def _on_copy_json(self):
        data = to_json_list(self._last_results)
        self.clipboard_clear()
        self.clipboard_append(json.dumps(data, indent=2))
        self._set_status("Copied JSON to clipboard.")
    def _on_copy_table(self):
        rows = self._get_selected_rows_tsv()
        if not rows:
            rows = self._get_all_rows_tsv()
        self.clipboard_clear()
        self.clipboard_append(rows)
        self._set_status("Copied table as TSV to clipboard.")
    def _on_save_json(self):
        data = to_json_list(self._last_results)
        if not data:
            messagebox.showinfo("Save JSON", "No results to save.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Save results as JSON",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._set_status(f"Saved JSON to: {path}")
        except Exception as ex:
            messagebox.showerror("Save JSON", str(ex))
    def _on_save_csv(self):
        items = self.tree.get_children()
        if not items:
            messagebox.showinfo("Save CSV", "No results to save.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Save results as CSV",
        )
        if not path:
            return
        import csv
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["PID", "NAME", "EXE", "CMDLINE"])
                for iid in items:
                    vals = self.tree.item(iid, "values")
                    writer.writerow(vals)
            self._set_status(f"Saved CSV to: {path}")
        except Exception as ex:
            messagebox.showerror("Save CSV", str(ex))
    def _on_row_double_click(self, _evt):
        item = self.tree.focus()
        if not item:
            return
        vals = self.tree.item(item, "values")
        pid, name, exe, cmdline = vals
        details = (
            f"PID: {pid}\n"
            f"Name: {name}\n"
            f"EXE: {exe}\n"
            f"CMDLINE:\n{cmdline or '(not included)'}"
        )
        messagebox.showinfo("Process details", details)
    def _on_auto_toggle(self):
        if self.auto_var.get():
            self._stop_refresh.clear()
            self._start_auto_refresh()
            self._set_status("Auto refresh enabled.")
        else:
            self._stop_refresh.set()
            self._set_status("Auto refresh disabled.")
    def _start_auto_refresh(self):
        """Auto refresh using Tk 'after' to avoid thread overlap."""
        if self._stop_refresh.is_set():
            return
        self._on_search()
        interval_ms = max(1, int(self.interval_var.get())) * 1000
        self.after(interval_ms, self._start_auto_refresh)
    def _populate_tree(self, rows: List[MatchRow]):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for r in rows:
            self.tree.insert(
                "", "end",
                values=(str(r.pid), r.name, r.exe, r.cmdline),
            )
        self._sort_desc.clear()
    def _sort_by_column(self, col: str):
        data = []
        for iid in self.tree.get_children(""):
            vals = self.tree.item(iid, "values")
            data.append((iid, vals))
        idx_map = {"PID": 0, "NAME": 1, "EXE": 2, "CMDLINE": 3}
        i = idx_map[col]
        def key_func(pair):
            vals = pair[1]
            v = vals[i]
            if col == "PID":
                try:
                    return int(v)
                except ValueError:
                    return 0
            return v.lower()
        desc = self._sort_desc.get(col, False)
        data.sort(key=key_func, reverse=not desc)
        self._sort_desc[col] = not desc
        for index, (iid, _) in enumerate(data):
            self.tree.move(iid, "", index)
    def _get_selected_rows_tsv(self) -> str:
        sel = self.tree.selection()
        if not sel:
            return ""
        lines = ["PID\tNAME\tEXE\tCMDLINE"]
        for iid in sel:
            vals = self.tree.item(iid, "values")
            lines.append("\t".join(vals))
        return "\n".join(lines)
    def _get_all_rows_tsv(self) -> str:
        items = self.tree.get_children()
        lines = ["PID\tNAME\tEXE\tCMDLINE"]
        for iid in items:
            vals = self.tree.item(iid, "values")
            lines.append("\t".join(vals))
        return "\n".join(lines)
    def _set_status(self, text: str):
        self.status_var.set(text)
    def _disable_controls(self):
        for w in (
            self.search_btn, self.clear_btn, self.copy_first_btn, self.copy_json_btn,
            self.save_json_btn, self.save_csv_btn, self.copy_table_btn,
            self.field_combo, self.query_entry, self.exact_chk, self.regex_chk,
            self.cmd_chk, self.first_chk, self.cmdline_chk, self.auto_chk,
            self.interval_spin,
        ):
            try:
                w.configure(state="disabled")
            except Exception:
                pass
    def _enable_controls(self):
        for w in (
            self.search_btn, self.clear_btn, self.copy_first_btn, self.copy_json_btn,
            self.save_json_btn, self.save_csv_btn, self.copy_table_btn,
            self.field_combo, self.query_entry, self.exact_chk, self.regex_chk,
            self.cmd_chk, self.first_chk, self.cmdline_chk, self.auto_chk,
            self.interval_spin,
        ):
            try:
                w.configure(state="normal")
            except Exception:
                pass
def main():
    app = ProcessFinderApp()
    app.mainloop()
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

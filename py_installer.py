#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Compact, multi-tab Tkinter GUI wrapper for PyInstaller.

Highlights:
- Minimal, focused UI with ttk.Notebook tabs
- Background build with live logs, cancel, elapsed time
- Exact command preview (copy-paste safe)
- Save/Load JSON profiles
- Detects and installs PyInstaller
- Robust quoting, OS-correct add-data separator (; on Windows, : on macOS/Linux)
- Professional dark theme, keyboard shortcuts, tooltips

Best practice: Build Windows .exe on Windows with matching architecture.
"""

import os
import sys
import json
import time
import queue
import shlex
import threading
import subprocess
import tkinter as tk
from dataclasses import dataclass, field, asdict
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
import platform
import webbrowser

APP_NAME = "PyInstaller Builder"
APP_VERSION = "2.0.0"

IS_WINDOWS = (platform.system() == "Windows")
DATA_SEP = ";" if IS_WINDOWS else ":"

# ---------- Helpers for command preview (no shell) ----------
def shlex_join_win(args):
    return subprocess.list2cmdline(args)

def shlex_join_posix(args):
    try:
        return shlex.join(args)
    except Exception:
        return " ".join(shlex.quote(a) for a in args)

def join_preview(args):
    return shlex_join_win(args) if IS_WINDOWS else shlex_join_posix(args)

def safe_str(x):
    return "" if x is None else str(x)

# ---------- Data model ----------
@dataclass
class BuildOptions:
    mode: str = "script"  # "script" or "spec"
    script_path: str = ""
    spec_path: str = ""
    dist_path: str = ""
    work_path: str = ""

    onefile: bool = True
    windowed: bool = True
    clean: bool = True
    debug: bool = False
    noupx: bool = False

    icon_path: str = ""
    add_data: list = field(default_factory=list)  # list[{"src": str, "dst": str}]
    hidden_imports: list = field(default_factory=list)  # list[str]
    pathex: list = field(default_factory=list)  # list[str]
    excludes: list = field(default_factory=list)  # list[str]

    output_name: str = ""  # --name
    additional_args: list = field(default_factory=list)  # list[str]

    def normalized(self):
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, str):
                d[k] = v.strip()
        return d

# ---------- Tooltip ----------
class Tooltip:
    def __init__(self, widget, text, delay=500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.id = None
        self.tip = None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, _):
        self._schedule()

    def _leave(self, _):
        self._unschedule()
        self._hide()

    def _schedule(self):
        self._unschedule()
        self.id = self.widget.after(self.delay, self._show)

    def _unschedule(self):
        if self.id:
            self.widget.after_cancel(self.id)
            self.id = None

    def _show(self):
        if self.tip or not self.text:
            return
        x, y, cx, cy = self.widget.bbox("insert") if self.widget.winfo_ismapped() else (0,0,0,0)
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(tw, text=self.text, justify="left",
                       background="#2a3040", foreground="#e6e6e6",
                       relief="solid", borderwidth=1,
                       padx=8, pady=5, font=("Segoe UI", 9))
        lbl.pack()

    def _hide(self):
        if self.tip:
            self.tip.destroy()
            self.tip = None

# ---------- GUI ----------
class BuilderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.minsize(980, 640)

        self.opts = BuildOptions()
        self.proc = None
        self.queue = queue.Queue()
        self.build_thread = None
        self.build_start_ts = None

        self._profile_path = None

        self._init_style()
        self._build_ui()
        self._bind_shortcuts()
        self._detect_pyinstaller_async()
        self._pump_queue()

    # ---------- Style / Theme ----------
    def _init_style(self):
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        bg = "#1f2430"
        panel = "#232834"
        fg = "#e6e6e6"
        acc = "#4098ff"
        muted = "#9aa4af"
        entry_bg = "#2a3040"

        self.configure(bg=bg)
        self.style.configure(".", background=bg, foreground=fg, fieldbackground=entry_bg)
        self.style.configure("TLabel", background=bg, foreground=fg)
        self.style.configure("TFrame", background=bg)
        self.style.configure("TLabelframe", background=panel, foreground=fg, relief="groove")
        self.style.configure("TLabelframe.Label", background=panel, foreground=fg)
        self.style.configure("TButton", padding=5)
        self.style.map("TButton", background=[("active", acc)])
        self.style.configure("TCheckbutton", background=bg, foreground=fg)
        self.style.configure("TRadiobutton", background=bg, foreground=fg)
        self.style.configure("TEntry", fieldbackground=entry_bg)
        self.style.configure("TNotebook", background=bg, tabposition="n")
        self.style.configure("TNotebook.Tab", padding=(10, 6))
        self.style.configure("Status.TLabel", foreground=muted)

    # ---------- UI Layout ----------
    def _build_ui(self):
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True)

        # Header bar
        header = ttk.Frame(root)
        header.pack(fill="x", padx=10, pady=(10, 6))
        self.pyinst_status_var = tk.StringVar(value="Detecting PyInstaller…")
        ttk.Label(header, textvariable=self.pyinst_status_var, style="Status.TLabel").pack(side="left")

        header_spacer = ttk.Frame(header)
        header_spacer.pack(side="left", padx=8)
        # Actions
        self.btn_install = ttk.Button(header, text="Install PyInstaller", command=self._install_pyinstaller, state="disabled")
        self.btn_install.pack(side="left", padx=4)

        ttk.Separator(header, orient="vertical").pack(side="left", fill="y", padx=8)
        self.btn_preview = ttk.Button(header, text="Preview", command=self._refresh_preview)
        self.btn_preview.pack(side="left", padx=2)
        self.btn_build = ttk.Button(header, text="Build", command=self._build)
        self.btn_build.pack(side="left", padx=2)
        self.btn_cancel = ttk.Button(header, text="Cancel", command=self._cancel_build, state="disabled")
        self.btn_cancel.pack(side="left", padx=2)

        ttk.Separator(header, orient="vertical").pack(side="left", fill="y", padx=8)
        self.btn_save = ttk.Button(header, text="Save Profile", command=self._save_profile)
        self.btn_save.pack(side="left", padx=2)
        self.btn_load = ttk.Button(header, text="Load Profile", command=self._load_profile)
        self.btn_load.pack(side="left", padx=2)

        ttk.Separator(header, orient="vertical").pack(side="left", fill="y", padx=8)
        help_btn = ttk.Button(header, text="Help", command=lambda: webbrowser.open("https://pyinstaller.org/en/stable/"))
        help_btn.pack(side="left", padx=2)
        Tooltip(help_btn, "Open PyInstaller documentation")

        # Notebook
        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Tabs
        self.tab_project = ttk.Frame(nb)
        self.tab_paths = ttk.Frame(nb)
        self.tab_bundling = ttk.Frame(nb)
        self.tab_flags = ttk.Frame(nb)
        self.tab_build = ttk.Frame(nb)

        nb.add(self.tab_project, text="Project")
        nb.add(self.tab_paths, text="Paths")
        nb.add(self.tab_bundling, text="Bundling")
        nb.add(self.tab_flags, text="Flags")
        nb.add(self.tab_build, text="Build")

        # Build tab content first (preview + log)
        self._build_tab_build(self.tab_build)
        # Then content tabs
        self._build_tab_project(self.tab_project)
        self._build_tab_paths(self.tab_paths)
        self._build_tab_bundling(self.tab_bundling)
        self._build_tab_flags(self.tab_flags)

        # Status bar
        status = ttk.Frame(root)
        status.pack(fill="x", padx=10, pady=(0, 10))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").pack(side="right")

    # ---------- Tabs ----------
    def _build_tab_project(self, tab):
        # Compact grid
        g = ttk.Frame(tab)
        g.pack(fill="x", padx=8, pady=8)
        for i in range(3): g.columnconfigure(i, weight=1 if i==1 else 0)

        # Mode
        ttk.Label(g, text="Build target:").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.mode_var = tk.StringVar(value="script")
        rb1 = ttk.Radiobutton(g, text="Script (.py)", variable=self.mode_var, value="script", command=self._refresh_preview)
        rb2 = ttk.Radiobutton(g, text="Spec (.spec)", variable=self.mode_var, value="spec", command=self._refresh_preview)
        rb1.grid(row=0, column=1, sticky="w", pady=(0, 4))
        rb2.grid(row=0, column=2, sticky="w", pady=(0, 4))
        Tooltip(rb1, "Build from a Python script (most common)")
        Tooltip(rb2, "Build from a PyInstaller .spec file (advanced)")

        # Script
        ttk.Label(g, text="Script:").grid(row=1, column=0, sticky="w", pady=2)
        self.script_var = tk.StringVar()
        e_script = ttk.Entry(g, textvariable=self.script_var)
        e_script.grid(row=1, column=1, sticky="ew", pady=2, padx=(0, 4))
        btn_script = ttk.Button(g, text="Browse…", command=self._pick_script)
        btn_script.grid(row=1, column=2, sticky="w", pady=2)
        Tooltip(e_script, "Path to your .py entry script")

        # Spec
        ttk.Label(g, text="Spec:").grid(row=2, column=0, sticky="w", pady=2)
        self.spec_var = tk.StringVar()
        e_spec = ttk.Entry(g, textvariable=self.spec_var)
        e_spec.grid(row=2, column=1, sticky="ew", pady=2, padx=(0, 4))
        btn_spec = ttk.Button(g, text="Browse…", command=self._pick_spec)
        btn_spec.grid(row=2, column=2, sticky="w", pady=2)
        Tooltip(e_spec, "Path to a .spec file (uses its own settings)")

        # Name + Icon + Console/GUI
        ttk.Label(g, text="Name:").grid(row=3, column=0, sticky="w", pady=2)
        self.name_var = tk.StringVar()
        e_name = ttk.Entry(g, textvariable=self.name_var)
        e_name.grid(row=3, column=1, sticky="ew", pady=2, padx=(0, 4))
        Tooltip(e_name, "Optional output name (--name)")

        ttk.Label(g, text="Icon:").grid(row=4, column=0, sticky="w", pady=2)
        self.icon_var = tk.StringVar()
        e_icon = ttk.Entry(g, textvariable=self.icon_var)
        e_icon.grid(row=4, column=1, sticky="ew", pady=2, padx=(0, 4))
        ttk.Button(g, text="Browse…", command=self._pick_icon).grid(row=4, column=2, sticky="w", pady=2)
        Tooltip(e_icon, "Windows .ico recommended")

        self.windowed_var = tk.BooleanVar(value=True)
        cb_windowed = ttk.Checkbutton(g, text="Windowed (no console)", variable=self.windowed_var, command=self._refresh_preview)
        cb_windowed.grid(row=5, column=1, sticky="w", pady=4)
        Tooltip(cb_windowed, "Use --windowed for GUI apps")

    def _build_tab_paths(self, tab):
        g = ttk.Frame(tab)
        g.pack(fill="both", expand=True, padx=8, pady=8)
        for i in range(3): g.columnconfigure(i, weight=1 if i==1 else 0)

        ttk.Label(g, text="Dist (output):").grid(row=0, column=0, sticky="w", pady=2)
        self.dist_var = tk.StringVar()
        e_dist = ttk.Entry(g, textvariable=self.dist_var)
        e_dist.grid(row=0, column=1, sticky="ew", pady=2, padx=(0, 4))
        ttk.Button(g, text="Browse…", command=self._pick_dist).grid(row=0, column=2, sticky="w", pady=2)
        Tooltip(e_dist, "Folder where executables/folders are created")

        ttk.Label(g, text="Work (build):").grid(row=1, column=0, sticky="w", pady=2)
        self.work_var = tk.StringVar()
        e_work = ttk.Entry(g, textvariable=self.work_var)
        e_work.grid(row=1, column=1, sticky="ew", pady=2, padx=(0, 4))
        ttk.Button(g, text="Browse…", command=self._pick_work).grid(row=1, column=2, sticky="w", pady=2)
        Tooltip(e_work, "Temporary build directory")

        # Pathex
        ttk.Label(g, text="Extra Paths (pathex):").grid(row=2, column=0, sticky="w", pady=(8, 2))
        pf = ttk.Frame(g)
        pf.grid(row=3, column=0, columnspan=3, sticky="nsew")
        pf.columnconfigure(0, weight=1)
        self.pathex_list = tk.Listbox(pf, height=4)
        self.pathex_list.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(pf, orient="vertical", command=self.pathex_list.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.pathex_list.configure(yscrollcommand=sb.set)

        btns = ttk.Frame(g)
        btns.grid(row=4, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Button(btns, text="Add…", command=self._add_pathex).pack(side="left", padx=2)
        ttk.Button(btns, text="Remove", command=lambda: self._remove_selected(self.pathex_list)).pack(side="left", padx=2)

    def _build_tab_bundling(self, tab):
        g = ttk.Frame(tab)
        g.pack(fill="both", expand=True, padx=8, pady=8)
        for i in range(3): g.columnconfigure(i, weight=1 if i==0 else 0)

        # Add-Data (compact table: src -> dst)
        ttk.Label(g, text=f"Add-Data (src {DATA_SEP} dst):").grid(row=0, column=0, sticky="w", pady=(0, 4))
        table = ttk.Frame(g)
        table.grid(row=1, column=0, sticky="nsew")
        g.rowconfigure(1, weight=1)
        table.columnconfigure(0, weight=1)
        self.data_tree = ttk.Treeview(table, columns=("src", "dst"), show="headings", height=6)
        self.data_tree.heading("src", text="Source")
        self.data_tree.heading("dst", text="Destination")
        self.data_tree.column("src", width=520, anchor="w")
        self.data_tree.column("dst", width=220, anchor="w")
        self.data_tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(table, orient="vertical", command=self.data_tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.data_tree.configure(yscrollcommand=sb.set)

        bar = ttk.Frame(g)
        bar.grid(row=2, column=0, sticky="w", pady=4)
        ttk.Button(bar, text="Add File…", command=self._add_data_file).pack(side="left", padx=2)
        ttk.Button(bar, text="Add Folder…", command=self._add_data_folder).pack(side="left", padx=2)
        ttk.Button(bar, text="Edit", command=self._edit_data_item).pack(side="left", padx=2)
        ttk.Button(bar, text="Remove", command=self._remove_data_item).pack(side="left", padx=2)
        Tooltip(self.data_tree, "Include extra files/folders into your build")

        # Hidden imports
        ttk.Label(g, text="Hidden Imports:").grid(row=3, column=0, sticky="w", pady=(10, 4))
        hf = ttk.Frame(g)
        hf.grid(row=4, column=0, sticky="nsew")
        hf.columnconfigure(0, weight=1)
        self.hidden_list = tk.Listbox(hf, height=4)
        self.hidden_list.grid(row=0, column=0, sticky="nsew")
        hs = ttk.Scrollbar(hf, orient="vertical", command=self.hidden_list.yview)
        hs.grid(row=0, column=1, sticky="ns")
        self.hidden_list.configure(yscrollcommand=hs.set)

        hb = ttk.Frame(g)
        hb.grid(row=5, column=0, sticky="w", pady=4)
        ttk.Button(hb, text="Add", command=lambda: self._prompt_add_to_list(self.hidden_list, "Hidden import (module):")).pack(side="left", padx=2)
        ttk.Button(hb, text="Remove", command=lambda: self._remove_selected(self.hidden_list)).pack(side="left", padx=2)

        # Excludes
        ttk.Label(g, text="Exclude Modules:").grid(row=6, column=0, sticky="w", pady=(10, 4))
        ef = ttk.Frame(g)
        ef.grid(row=7, column=0, sticky="nsew")
        ef.columnconfigure(0, weight=1)
        self.excl_list = tk.Listbox(ef, height=4)
        self.excl_list.grid(row=0, column=0, sticky="nsew")
        es = ttk.Scrollbar(ef, orient="vertical", command=self.excl_list.yview)
        es.grid(row=0, column=1, sticky="ns")
        self.excl_list.configure(yscrollcommand=es.set)

        eb = ttk.Frame(g)
        eb.grid(row=8, column=0, sticky="w", pady=4)
        ttk.Button(eb, text="Add", command=lambda: self._prompt_add_to_list(self.excl_list, "Exclude module (name):")).pack(side="left", padx=2)
        ttk.Button(eb, text="Remove", command=lambda: self._remove_selected(self.excl_list)).pack(side="left", padx=2)

    def _build_tab_flags(self, tab):
        g = ttk.Frame(tab)
        g.pack(fill="x", padx=8, pady=8)

        # Two rows of toggles
        self.onefile_var = tk.BooleanVar(value=True)
        self.clean_var = tk.BooleanVar(value=True)
        self.debug_var = tk.BooleanVar(value=False)
        self.noupx_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(g, text="One-file (--onefile)", variable=self.onefile_var, command=self._refresh_preview).grid(row=0, column=0, sticky="w", padx=(0, 18), pady=2)
        ttk.Checkbutton(g, text="Clean (--clean)", variable=self.clean_var, command=self._refresh_preview).grid(row=0, column=1, sticky="w", padx=(0, 18), pady=2)
        ttk.Checkbutton(g, text="Debug (--debug)", variable=self.debug_var, command=self._refresh_preview).grid(row=0, column=2, sticky="w", padx=(0, 18), pady=2)
        ttk.Checkbutton(g, text="Disable UPX (--noupx)", variable=self.noupx_var, command=self._refresh_preview).grid(row=0, column=3, sticky="w", pady=2)

        ttk.Label(g, text="Additional args (advanced):").grid(row=1, column=0, sticky="w", pady=(12, 4))
        self.extra_args_var = tk.StringVar()
        e_extra = ttk.Entry(g, textvariable=self.extra_args_var)
        e_extra.grid(row=2, column=0, columnspan=4, sticky="ew", pady=2)
        g.columnconfigure(0, weight=1)
        Tooltip(e_extra, "Space-separated PyInstaller flags (respects quotes)")

    def _build_tab_build(self, tab):
        container = ttk.Frame(tab)
        container.pack(fill="both", expand=True, padx=8, pady=8)
        container.rowconfigure(1, weight=1)
        container.columnconfigure(0, weight=1)

        # Preview
        prev_frame = ttk.Labelframe(container, text=" Command Preview ")
        prev_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        prev_frame.columnconfigure(0, weight=1)
        self.preview_text = ScrolledText(prev_frame, height=4)
        self.preview_text.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        self.preview_text.configure(state="disabled")
        Tooltip(self.preview_text, "Exact command the builder will run")

        # Log
        log_frame = ttk.Labelframe(container, text=" Build Log ")
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = ScrolledText(log_frame, wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.log_text.configure(state="disabled")

    # ---------- Shortcuts ----------
    def _bind_shortcuts(self):
        self.bind_all("<Control-b>", lambda e: self._build())
        self.bind_all("<Control-B>", lambda e: self._build())
        self.bind_all("<Control-p>", lambda e: self._refresh_preview())
        self.bind_all("<Control-P>", lambda e: self._refresh_preview())
        self.bind_all("<Control-s>", lambda e: self._save_profile())
        self.bind_all("<Control-S>", lambda e: self._save_profile())
        self.bind_all("<Control-o>", lambda e: self._load_profile())
        self.bind_all("<Control-O>", lambda e: self._load_profile())
        self.bind_all("<F1>", lambda e: webbrowser.open("https://pyinstaller.org/en/stable/"))

    # ---------- Pickers ----------
    def _pick_script(self):
        p = filedialog.askopenfilename(title="Select Python Script", filetypes=[("Python", "*.py")])
        if p:
            self.script_var.set(p)
            self._refresh_preview()

    def _pick_spec(self):
        p = filedialog.askopenfilename(title="Select Spec File", filetypes=[("Spec", "*.spec")])
        if p:
            self.spec_var.set(p)
            self._refresh_preview()

    def _pick_icon(self):
        p = filedialog.askopenfilename(title="Select Icon", filetypes=[("Icon", "*.ico"), ("All files", "*.*")])
        if p:
            self.icon_var.set(p)
            self._refresh_preview()

    def _pick_dist(self):
        d = filedialog.askdirectory(title="Select Dist (output) Folder")
        if d:
            self.dist_var.set(d)
            self._refresh_preview()

    def _pick_work(self):
        d = filedialog.askdirectory(title="Select Work (build) Folder")
        if d:
            self.work_var.set(d)
            self._refresh_preview()

    # ---------- Bundling helpers ----------
    def _add_data_file(self):
        src = filedialog.askopenfilename(title="Select data file")
        if not src:
            return
        dst = self._prompt_text("Destination path inside app:", "data/")
        if dst is None:
            return
        self.data_tree.insert("", "end", values=(src, dst))
        self._refresh_preview()

    def _add_data_folder(self):
        folder = filedialog.askdirectory(title="Select folder to include")
        if not folder:
            return
        default_dst = Path(folder).name
        dst = self._prompt_text("Destination path inside app:", default_dst)
        if dst is None:
            return
        self.data_tree.insert("", "end", values=(folder, dst))
        self._refresh_preview()

    def _edit_data_item(self):
        sel = self.data_tree.selection()
        if not sel:
            return
        item_id = sel[0]
        src, dst = self.data_tree.item(item_id, "values")
        new_src = filedialog.askopenfilename(title="Change source (cancel to keep)", initialdir=str(Path(src).parent)) or src
        new_dst = self._prompt_text("Destination path inside app:", dst)
        if new_dst is None:
            new_dst = dst
        self.data_tree.item(item_id, values=(new_src, new_dst))
        self._refresh_preview()

    def _remove_data_item(self):
        for item in self.data_tree.selection():
            self.data_tree.delete(item)
        self._refresh_preview()

    def _add_pathex(self):
        folder = filedialog.askdirectory(title="Select folder to add to sys.path (pathex)")
        if folder:
            self.pathex_list.insert("end", folder)
            self._refresh_preview()

    def _prompt_text(self, title, initial=""):
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.grab_set()
        frame = ttk.Frame(win)
        frame.pack(padx=10, pady=10)
        ttk.Label(frame, text=title).pack(anchor="w", pady=(0, 6))
        var = tk.StringVar(value=initial)
        ent = ttk.Entry(frame, textvariable=var, width=48)
        ent.pack()
        ent.focus_set()

        out = {"value": None}
        def ok(): out.__setitem__("value", var.get().strip()) ; win.destroy()
        def cancel(): win.destroy()

        btns = ttk.Frame(frame)
        btns.pack(pady=8)
        ttk.Button(btns, text="OK", command=ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=cancel).pack(side="left", padx=4)
        win.wait_window()
        return out["value"]

    def _prompt_add_to_list(self, lb, prompt):
        v = self._prompt_text(prompt, "")
        if v:
            lb.insert("end", v.strip())
            self._refresh_preview()

    def _remove_selected(self, lb):
        sel = list(lb.curselection())
        for i in reversed(sel):
            lb.delete(i)
        self._refresh_preview()

    # ---------- Options collect/validate/build ----------
    def _collect_options(self) -> BuildOptions:
        opts = BuildOptions(
            mode=self.mode_var.get(),
            script_path=self.script_var.get(),
            spec_path=self.spec_var.get(),
            dist_path=self.dist_var.get(),
            work_path=self.work_var.get(),
            onefile=self.onefile_var.get(),
            windowed=self.windowed_var.get(),
            clean=self.clean_var.get(),
            debug=self.debug_var.get(),
            noupx=self.noupx_var.get(),
            icon_path=self.icon_var.get(),
            output_name=self.name_var.get(),
        )
        # Add-data from tree
        for iid in self.data_tree.get_children(""):
            src, dst = self.data_tree.item(iid, "values")
            if src:
                opts.add_data.append({"src": str(src), "dst": str(dst or ".")})
        # Lists
        for i in range(self.hidden_list.size()):
            opts.hidden_imports.append(self.hidden_list.get(i))
        for i in range(self.pathex_list.size()):
            opts.pathex.append(self.pathex_list.get(i))
        for i in range(self.excl_list.size()):
            opts.excludes.append(self.excl_list.get(i))
        # Additional args
        xargs = self.extra_args_var.get().strip()
        if xargs:
            try:
                opts.additional_args = shlex.split(xargs)
            except Exception:
                opts.additional_args = xargs.split()
        return opts

    def _validate(self, opts: BuildOptions) -> bool:
        # Inline validation messaging, not spammy modals
        if opts.mode == "script":
            if not opts.script_path:
                self._status_warn("Select a script (.py).")
                return False
            if not Path(opts.script_path).is_file():
                self._status_warn("Script path does not exist.")
                return False
        else:
            if not opts.spec_path:
                self._status_warn("Select a spec (.spec).")
                return False
            if not Path(opts.spec_path).is_file():
                self._status_warn("Spec path does not exist.")
                return False
        if IS_WINDOWS and opts.icon_path and not opts.icon_path.lower().endswith(".ico"):
            self._status_warn("On Windows, icon should be a .ico file.")
        # Ensure dist/work exist or are creatable
        for pth, label in [(opts.dist_path, "Dist path"), (opts.work_path, "Work path")]:
            if pth and not Path(pth).exists():
                try:
                    Path(pth).mkdir(parents=True, exist_ok=True)
                except Exception:
                    self._status_warn(f"{label} cannot be created.")
                    return False
        return True

    def _build_command(self, opts: BuildOptions) -> list:
        cmd = [sys.executable, "-m", "PyInstaller"]
        target = opts.script_path if opts.mode == "script" else opts.spec_path
        if not target:
            return cmd

        if opts.clean: cmd.append("--clean")
        if opts.mode == "script":
            if opts.onefile: cmd.append("--onefile")
            if opts.windowed: cmd.append("--windowed")
            if opts.icon_path.strip(): cmd.extend(["--icon", opts.icon_path.strip()])
        if opts.debug: cmd.append("--debug")
        if opts.noupx: cmd.append("--noupx")
        if opts.output_name.strip(): cmd.extend(["--name", opts.output_name.strip()])
        if opts.dist_path.strip(): cmd.extend(["--distpath", opts.dist_path.strip()])
        if opts.work_path.strip(): cmd.extend(["--workpath", opts.work_path.strip()])

        for p in opts.pathex:
            p = p.strip()
            if p: cmd.extend(["--paths", p])

        for item in opts.add_data:
            src = safe_str(item.get("src")).strip()
            dst = safe_str(item.get("dst")).strip() or "."
            if src: cmd.extend(["--add-data", f"{src}{DATA_SEP}{dst}"])

        for mod in opts.hidden_imports:
            mod = safe_str(mod).strip()
            if mod: cmd.extend(["--hidden-import", mod])

        for mod in opts.excludes:
            mod = safe_str(mod).strip()
            if mod: cmd.extend(["--exclude-module", mod])

        for a in opts.additional_args:
            if a: cmd.append(a)

        cmd.append(target)
        return cmd

    # ---------- Build flow ----------
    def _build(self):
        if self.proc is not None:
            self._status_warn("A build is already running.")
            return
        opts = self._collect_options()
        if not self._validate(opts):
            return

        cmd = self._build_command(opts)
        self._clear_log()
        self._log("=== Build started ===\n", "info")
        self._log(f"Command: {join_preview(cmd)}\n\n", "info")
        self.status_var.set("Building…")
        self.build_start_ts = time.time()

        self.btn_build.config(state="disabled")
        self.btn_cancel.config(state="normal")

        t = threading.Thread(target=self._run_build_proc, args=(cmd,), daemon=True)
        t.start()
        self.build_thread = t
        self._refresh_preview()  # keep preview in sync

    def _run_build_proc(self, cmd):
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
        except FileNotFoundError:
            self._qlog("Error: Python or PyInstaller not found.\n", "error")
            self._qfinish(False)
            return
        except Exception as e:
            self._qlog(f"Failed to start build: {e}\n", "error")
            self._qfinish(False)
            return
        try:
            for line in self.proc.stdout:
                self._qlog(line, "log")
            ret = self.proc.wait()
        except Exception as e:
            self._qlog(f"Build process error: {e}\n", "error")
            ret = 1

        self.proc = None
        self._qfinish(ret == 0)

    def _cancel_build(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self._log("Build canceled by user.\n", "warn")
            except Exception as e:
                self._log(f"Failed to cancel build: {e}\n", "error")
        self.proc = None
        self.btn_cancel.config(state="disabled")
        self.btn_build.config(state="normal")
        self.status_var.set("Canceled.")

    # ---------- Queue pump ----------
    def _qlog(self, text, tag="log"):
        self.queue.put(("log", text, tag))

    def _qfinish(self, success: bool):
        self.queue.put(("finish", success))

    def _pump_queue(self):
        try:
            while True:
                item = self.queue.get_nowait()
                if item[0] == "log":
                    _, text, tag = item
                    self._log(text, tag)
                elif item[0] == "finish":
                    _, success = item
                    self._on_build_finished(success)
                elif item[0] == "pi_status":
                    _, ok, ver = item
                    self._apply_pi_status(ok, ver)
        except queue.Empty:
            pass
        self.after(60, self._pump_queue)

    def _on_build_finished(self, success: bool):
        elapsed = (time.time() - self.build_start_ts) if self.build_start_ts else 0.0
        self.build_start_ts = None
        self.btn_cancel.config(state="disabled")
        self.btn_build.config(state="normal")
        if success:
            self._log(f"\n=== Build finished successfully in {elapsed:.1f}s ===\n", "info")
            self.status_var.set(f"Done in {elapsed:.1f}s")
            dist = self.dist_var.get().strip()
            if dist and Path(dist).exists():
                if messagebox.askyesno(APP_NAME, "Open dist folder?"):
                    self._open_path(dist)
        else:
            self._log(f"\n=== Build failed after {elapsed:.1f}s ===\n", "error")
            self.status_var.set("Build failed.")

    # ---------- Logging / status ----------
    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _log(self, text, _tag="log"):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _status_warn(self, msg):
        # concise, non-blocking feedback
        self.status_var.set(msg)

    # ---------- Preview ----------
    def _refresh_preview(self):
        opts = self._collect_options()
        cmd = self._build_command(opts)
        preview = join_preview(cmd)
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", preview.strip() + "\n")
        self.preview_text.configure(state="disabled")

    # ---------- PyInstaller detect/install ----------
    def _detect_pyinstaller_async(self):
        def worker():
            ok, ver = self._check_pyinstaller()
            self.queue.put(("pi_status", ok, ver))
            self.queue.put(("log", f"PyInstaller: {'found' if ok else 'not found'}{(' ('+ver+')') if ver else ''}\n", "info"))
        threading.Thread(target=worker, daemon=True).start()

    def _apply_pi_status(self, ok: bool, ver: str):
        if ok:
            self.pyinst_status_var.set(f"PyInstaller detected ({ver})")
            self.btn_install.config(state="disabled")
        else:
            self.pyinst_status_var.set("PyInstaller not found")
            self.btn_install.config(state="normal")

    def _check_pyinstaller(self):
        try:
            out = subprocess.check_output([sys.executable, "-m", "PyInstaller", "--version"], universal_newlines=True)
            return True, out.strip()
        except Exception:
            return False, ""

    def _install_pyinstaller(self):
        if not messagebox.askyesno(APP_NAME, "Install/Upgrade PyInstaller in this environment?"):
            return
        self.status_var.set("Installing PyInstaller…")
        self.btn_install.config(state="disabled")

        def worker():
            try:
                cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools", "pyinstaller"]
                self._qlog(f"Running: {join_preview(cmd)}\n", "info")
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                for line in proc.stdout:
                    self._qlog(line, "log")
                proc.wait()
                ok, ver = self._check_pyinstaller()
                self.queue.put(("pi_status", ok, ver))
                self._qlog("PyInstaller installation completed.\n", "info")
            except Exception as e:
                self._qlog(f"Installation failed: {e}\n", "error")

        threading.Thread(target=worker, daemon=True).start()

    # ---------- Profiles ----------
    def _save_profile(self):
        opts = self._collect_options().normalized()
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON profile", "*.json")], title="Save build profile")
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(opts, f, indent=2)
            self._profile_path = p
            self.status_var.set(f"Profile saved: {Path(p).name}")
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Failed to save profile: {e}")

    def _load_profile(self):
        p = filedialog.askopenfilename(filetypes=[("JSON profile", "*.json")], title="Load build profile")
        if not p:
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._apply_profile(data)
            self._profile_path = p
            self.status_var.set(f"Profile loaded: {Path(p).name}")
            self._refresh_preview()
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Failed to load profile: {e}")

    def _apply_profile(self, data: dict):
        self.mode_var.set(data.get("mode", "script"))
        self.script_var.set(data.get("script_path", ""))
        self.spec_var.set(data.get("spec_path", ""))
        self.dist_var.set(data.get("dist_path", ""))
        self.work_var.set(data.get("work_path", ""))

        self.onefile_var.set(bool(data.get("onefile", True)))
        self.windowed_var.set(bool(data.get("windowed", True)))
        self.clean_var.set(bool(data.get("clean", True)))
        self.debug_var.set(bool(data.get("debug", False)))
        self.noupx_var.set(bool(data.get("noupx", False)))

        self.icon_var.set(data.get("icon_path", ""))
        self.name_var.set(data.get("output_name", ""))

        # Data tree
        for child in self.data_tree.get_children(""):
            self.data_tree.delete(child)
        for item in data.get("add_data", []):
            src = safe_str(item.get("src"))
            dst = safe_str(item.get("dst")) or "."
            if src:
                self.data_tree.insert("", "end", values=(src, dst))

        self.hidden_list.delete(0, "end")
        for mod in data.get("hidden_imports", []):
            self.hidden_list.insert("end", mod)

        self.pathex_list.delete(0, "end")
        for p in data.get("pathex", []):
            self.pathex_list.insert("end", p)

        self.excl_list.delete(0, "end")
        for p in data.get("excludes", []):
            self.excl_list.insert("end", p)

        extra = data.get("additional_args", [])
        if isinstance(extra, list):
            self.extra_args_var.set(" ".join(shlex.quote(a) if " " in a else a for a in extra))
        else:
            self.extra_args_var.set(safe_str(extra))

    # ---------- Utils ----------
    def _open_path(self, path):
        p = Path(path)
        try:
            if IS_WINDOWS:
                os.startfile(str(p))  # type: ignore
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception:
            webbrowser.open(p.as_uri())

    def destroy(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass
        super().destroy()


if __name__ == "__main__":
    # Tooltip requires tk.Label in Tooltip; import after tk root is defined
    # (Already fine in this file since Tooltip is defined above.)
    app = BuilderApp()
    app.mainloop()

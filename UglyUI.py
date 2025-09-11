import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk

import pandas as pd
import openpyxl


# -------- Helpers --------

def to_snake_case(header: str) -> str:
    header = re.sub(r'[^a-zA-Z0-9]+', '_', str(header))
    return header.strip('_').lower()


# -------- App --------

class ExcelToCSVApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Excel → CSV Converter")
        self.root.minsize(900, 540)

        # State
        self.file_path: str | None = None
        self.sheet_names: list[str] = []
        self.headers: list[str] = []
        self.header_vars: dict[str, tk.BooleanVar] = {}

        # ---- Layout: resizable split view ----
        self.pane = ttk.Panedwindow(self.root, orient="horizontal")
        self.pane.pack(fill="both", expand=True)

        self.left = ttk.Frame(self.pane, padding=10)
        self.right = ttk.Frame(self.pane, padding=10)
        self.pane.add(self.left, weight=1)   # left narrow
        self.pane.add(self.right, weight=3)  # right wide

        # ---- Left panel ----
        row = 0
        ttk.Label(self.left, text="Excel → CSV Converter", font=("Segoe UI", 12, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 8)
        ); row += 1

        ttk.Button(self.left, text="Select Excel File", command=self.select_file).grid(row=row, column=0, sticky="w")
        self.file_lbl = ttk.Label(self.left, text="", foreground="#555")
        self.file_lbl.grid(row=row, column=1, columnspan=2, sticky="w", padx=8)
        row += 1

        ttk.Label(self.left, text="Sheet:").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.sheet_var = tk.StringVar()
        self.sheet_combo = ttk.Combobox(self.left, textvariable=self.sheet_var, state="readonly", width=28)
        self.sheet_combo.grid(row=row, column=1, columnspan=2, sticky="we", pady=(8, 0))
        self.sheet_combo.bind("<<ComboboxSelected>>", lambda e: self.reset_headers())
        row += 1

        ttk.Label(self.left, text="Header row (1-based):").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.header_row_var = tk.StringVar(value="1")
        ttk.Spinbox(self.left, from_=1, to=9999, textvariable=self.header_row_var, width=6).grid(
            row=row, column=1, sticky="w", pady=(8, 0)
        )
        ttk.Button(self.left, text="Load Headers", command=self.load_headers).grid(row=row, column=2, sticky="e", pady=(8, 0))
        row += 1

        # Options (minimal)
        self.opt_snake = tk.BooleanVar(value=True)
        self.opt_dedup = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.left, text="snake_case headers", variable=self.opt_snake).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(self.left, text="Remove duplicates", variable=self.opt_dedup).grid(row=row, column=2, sticky="w", pady=(8, 0))
        row += 1

        # Columns list (scrollable)
        ttk.Label(self.left, text="Columns:").grid(row=row, column=0, sticky="w", pady=(8, 2))
        row += 1

        self.columns_frame = ttk.Frame(self.left)
        self.columns_frame.grid(row=row, column=0, columnspan=3, sticky="nsew")
        self.left.rowconfigure(row, weight=1)   # make this area expand
        self.left.columnconfigure(1, weight=1)

        self.col_canvas = tk.Canvas(self.columns_frame, borderwidth=0, highlightthickness=0)
        self.col_scroll_y = ttk.Scrollbar(self.columns_frame, orient="vertical", command=self.col_canvas.yview)
        self.col_list = ttk.Frame(self.col_canvas)
        self.col_list_id = self.col_canvas.create_window((0, 0), window=self.col_list, anchor="nw")

        self.col_canvas.configure(yscrollcommand=self.col_scroll_y.set)
        self.col_canvas.pack(side="left", fill="both", expand=True)
        self.col_scroll_y.pack(side="right", fill="y")

        # Keep inner frame width synced to canvas width
        self.col_list.bind("<Configure>", lambda e: self.col_canvas.configure(scrollregion=self.col_canvas.bbox("all")))
        self.col_canvas.bind("<Configure>", lambda e: self.col_canvas.itemconfigure(self.col_list_id, width=e.width))
        row += 1

        # Column selection buttons
        btn_row = ttk.Frame(self.left)
        btn_row.grid(row=row, column=0, columnspan=3, sticky="we", pady=(6, 0))
        ttk.Button(btn_row, text="Select All", command=lambda: self.set_all_checkboxes(True)).pack(side="left")
        ttk.Button(btn_row, text="Deselect All", command=lambda: self.set_all_checkboxes(False)).pack(side="left", padx=8)
        row += 1

        # Actions
        act_row = ttk.Frame(self.left)
        act_row.grid(row=row, column=0, columnspan=3, sticky="we", pady=(8, 0))
        ttk.Button(act_row, text="Preview", command=self.preview_data).pack(side="left")
        ttk.Button(act_row, text="Export CSV", command=self.export_csv).pack(side="right")
        row += 1

        self.status_lbl = ttk.Label(self.left, text="", foreground="#2e7d32")
        self.status_lbl.grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))
        row += 1

        # ---- Right panel: preview (treeview) ----
        right_top = ttk.Frame(self.right)
        right_top.pack(fill="x")
        ttk.Label(right_top, text="Data Preview", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Label(right_top, text="   Rows:").pack(side="left", padx=(10, 0))
        self.preview_rows_var = tk.IntVar(value=10)
        ttk.Spinbox(right_top, from_=1, to=2000, textvariable=self.preview_rows_var, width=6).pack(side="left", padx=(4, 0))

        tv_wrap = ttk.Frame(self.right)
        tv_wrap.pack(fill="both", expand=True, pady=(8, 0))
        self.tree = ttk.Treeview(tv_wrap, columns=(), show="headings")
        yscroll = ttk.Scrollbar(tv_wrap, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(tv_wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="we")
        tv_wrap.rowconfigure(0, weight=1)
        tv_wrap.columnconfigure(0, weight=1)

    # ---- Utility ----
    def set_status(self, text: str, ok: bool = True):
        self.status_lbl.configure(text=text, foreground="#2e7d32" if ok else "#c62828")

    def reset_headers(self):
        self.headers = []
        self.header_vars.clear()
        for w in list(self.col_list.children.values()):
            w.destroy()
        self.clear_preview()
        self.set_status("")

    # ---- Actions ----
    def select_file(self):
        path = filedialog.askopenfilename(
            title="Select Excel File",
            filetypes=[("Excel files", "*.xlsx *.xlsm *.xltx *.xltm *.xls"), ("All files", "*.*")]
        )
        if not path:
            return
        self.file_path = path
        self.file_lbl.configure(text=os.path.basename(path))
        try:
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            self.sheet_names = wb.sheetnames
            self.sheet_combo["values"] = self.sheet_names
            if self.sheet_names:
                self.sheet_var.set(self.sheet_names[0])
            self.set_status("File loaded. Pick sheet & header row, then Load Headers.", ok=True)
        except Exception as e:
            messagebox.showerror("Error", f"Could not read workbook:\n{e}")
            self.set_status("Failed to read workbook.", ok=False)

    def load_headers(self):
        if not self.file_path or not self.sheet_var.get():
            messagebox.showwarning("Missing info", "Please select a file and sheet first.")
            return
        try:
            header_row = int(self.header_row_var.get())
            if header_row < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid input", "Header row must be a positive integer.")
            return

        try:
            df = pd.read_excel(
                self.file_path,
                sheet_name=self.sheet_var.get(),
                header=header_row - 1,
                nrows=0,
                engine="openpyxl"
            )
            self.headers = list(df.columns)
            self.header_vars = {h: tk.BooleanVar(value=True) for h in self.headers}
            # Render checkboxes
            for w in list(self.col_list.children.values()):
                w.destroy()
            for col in self.headers:
                ttk.Checkbutton(self.col_list, text=str(col), variable=self.header_vars[col]).pack(anchor="w", pady=1)
            self.set_status("Headers loaded. Select columns to preview/export.", ok=True)
        except Exception as e:
            messagebox.showerror("Error", f"Could not load headers:\n{e}")
            self.set_status("Failed to load headers.", ok=False)

    def set_all_checkboxes(self, value: bool):
        for v in self.header_vars.values():
            v.set(value)

    def get_selected_columns(self) -> list[str]:
        return [c for c, v in self.header_vars.items() if v.get()]

    # ---- Preview ----
    def clear_preview(self):
        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = ()

    def render_preview(self, df: pd.DataFrame):
        self.clear_preview()
        cols = list(df.columns)
        self.tree["columns"] = cols
        for c in cols:
            self.tree.heading(c, text=str(c))
            self.tree.column(c, width=120, stretch=True)
        for _, row in df.iterrows():
            values = [("" if pd.isna(v) else v) for v in row.tolist()]
            self.tree.insert("", "end", values=values)

    def preview_data(self):
        if not self.file_path or not self.sheet_var.get():
            messagebox.showwarning("Missing info", "Please select a file and sheet first.")
            return
        if not self.headers:
            messagebox.showwarning("No headers", "Click 'Load Headers' first.")
            return
        selected = self.get_selected_columns()
        if not selected:
            messagebox.showwarning("No columns", "Select at least one column.")
            return
        try:
            n = max(1, int(self.preview_rows_var.get()))
            header_row = int(self.header_row_var.get()) - 1
            df = pd.read_excel(
                self.file_path,
                sheet_name=self.sheet_var.get(),
                header=header_row,
                usecols=selected,
                nrows=n,
                engine="openpyxl"
            )
            self.render_preview(df)
            self.set_status(f"Preview loaded ({len(df)} rows).", ok=True)
        except Exception as e:
            messagebox.showerror("Error", f"Could not load preview:\n{e}")
            self.set_status("Preview failed.", ok=False)

    # ---- Export ----
    def export_csv(self):
        if not self.file_path or not self.sheet_var.get():
            messagebox.showwarning("Missing info", "Please select a file and sheet first.")
            return
        if not self.headers:
            messagebox.showwarning("No headers", "Click 'Load Headers' first.")
            return
        selected = self.get_selected_columns()
        if not selected:
            messagebox.showwarning("No columns", "Select at least one column.")
            return

        base = os.path.splitext(os.path.basename(self.file_path))[0]
        default_name = f"{to_snake_case(base)}_{to_snake_case(self.sheet_var.get())}.csv"
        out_path = filedialog.asksaveasfilename(
            title="Save CSV As",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if not out_path:
            return

        try:
            header_row = int(self.header_row_var.get()) - 1
            df = pd.read_excel(
                self.file_path,
                sheet_name=self.sheet_var.get(),
                header=header_row,
                usecols=selected,
                engine="openpyxl"
            )
            if self.opt_dedup.get():
                df.drop_duplicates(inplace=True)

            if self.opt_snake.get():
                df.rename(columns={c: to_snake_case(c) for c in df.columns}, inplace=True)

            # utf-8-sig makes it easy to open in Excel
            df.to_csv(out_path, index=False, encoding="utf-8-sig")
            self.set_status(f"Exported: {os.path.basename(out_path)}", ok=True)
            messagebox.showinfo("Done", f"CSV saved to:\n{out_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Export failed:\n{e}")
            self.set_status("Export failed.", ok=False)


if __name__ == "__main__":
    root = tk.Tk()
    app = ExcelToCSVApp(root)
    root.mainloop()

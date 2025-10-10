import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import tkinter as tk
import keyword
import re
def is_comment_line(line):
    stripped = line.strip()
    return stripped.startswith("#") or stripped in {"#", "##", "# #"}
def clean_code(code, remove_empty, remove_docstrings):
    lines = code.splitlines()
    cleaned = []
    comment_lines = 0
    docstring_lines = 0
    empty_lines = 0
    in_docstring = False
    for line in lines:
        stripped = line.strip()
        if remove_docstrings and (stripped.startswith('"""') or stripped.startswith("'''")):
            if stripped.count('"""') == 2 or stripped.count("'''") == 2:
                docstring_lines += 1
                continue
            in_docstring = not in_docstring
            docstring_lines += 1
            continue
        if in_docstring:
            docstring_lines += 1
            continue
        if is_comment_line(line):
            comment_lines += 1
            continue
        if remove_empty and not stripped:
            empty_lines += 1
            continue
        cleaned.append(line)
    cleaned_code = "\n".join(cleaned)
    stats = {
        "total_lines": len(lines),
        "comment_lines": comment_lines,
        "docstring_lines": docstring_lines,
        "empty_lines": empty_lines,
        "cleaned_lines": len(cleaned),
        "chars_before": len(code),
        "chars_after": len(cleaned_code),
        "char_reduction": round((1 - len(cleaned_code) / len(code)) * 100, 2) if code else 0
    }
    return cleaned_code, stats
def highlight_syntax(text_widget):
    text_widget.tag_remove("keyword", "1.0", tk.END)
    text_widget.tag_remove("comment", "1.0", tk.END)
    text_widget.tag_remove("string", "1.0", tk.END)
    code = text_widget.get("1.0", tk.END)
    for kw in keyword.kwlist:
        start = "1.0"
        while True:
            pos = text_widget.search(rf'\b{kw}\b', start, stopindex=tk.END, regexp=True)
            if not pos:
                break
            end = f"{pos}+{len(kw)}c"
            text_widget.tag_add("keyword", pos, end)
            start = end
    for match in re.finditer(r"#.*", code):
        start = f"1.0+{match.start()}c"
        end = f"1.0+{match.end()}c"
        text_widget.tag_add("comment", start, end)
    for match in re.finditer(r"(\".*?\"|\'.*?\')", code):
        start = f"1.0+{match.start()}c"
        end = f"1.0+{match.end()}c"
        text_widget.tag_add("string", start, end)
class PasteCleanerApp:
    def __init__(self, root):
        root.title("Python Code Cleaner")
        root.geometry("1000x600")
        control_frame = ttk.Frame(root)
        control_frame.pack(side="top", fill="x", padx=5, pady=5)
        self.remove_empty_var = tk.BooleanVar()
        self.remove_docstring_var = tk.BooleanVar()
        ttk.Checkbutton(control_frame, text="Remove empty rows", variable=self.remove_empty_var).pack(side="left")
        ttk.Checkbutton(control_frame, text="Remove docstrings", variable=self.remove_docstring_var).pack(side="left", padx=5)
        ttk.Button(control_frame, text="Process", command=self.process_code).pack(side="left", padx=5)
        ttk.Button(control_frame, text="Copy", command=self.copy_cleaned).pack(side="left", padx=5)
        ttk.Button(control_frame, text="Export", command=self.export_cleaned).pack(side="left", padx=5)
        ttk.Button(control_frame, text="Reset", command=self.reset_all).pack(side="left", padx=5)
        self.stats_label = ttk.Label(control_frame, text="Stats: ")
        self.stats_label.pack(side="left", padx=10)
        text_frame = ttk.Frame(root)
        text_frame.pack(fill="both", expand=True)
        self.input_text = tk.Text(text_frame, wrap="none", width=50)
        self.output_text = tk.Text(text_frame, wrap="none", width=50)
        input_scroll_y = ttk.Scrollbar(text_frame, orient="vertical", command=self.input_text.yview)
        input_scroll_x = ttk.Scrollbar(text_frame, orient="horizontal", command=self.input_text.xview)
        self.input_text.configure(yscrollcommand=input_scroll_y.set, xscrollcommand=input_scroll_x.set)
        output_scroll_y = ttk.Scrollbar(text_frame, orient="vertical", command=self.output_text.yview)
        output_scroll_x = ttk.Scrollbar(text_frame, orient="horizontal", command=self.output_text.xview)
        self.output_text.configure(yscrollcommand=output_scroll_y.set, xscrollcommand=output_scroll_x.set)
        self.input_text.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        input_scroll_y.grid(row=0, column=1, sticky="ns")
        input_scroll_x.grid(row=1, column=0, sticky="ew")
        self.output_text.grid(row=0, column=2, sticky="nsew", padx=5, pady=5)
        output_scroll_y.grid(row=0, column=3, sticky="ns")
        output_scroll_x.grid(row=1, column=2, sticky="ew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.columnconfigure(2, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self.input_text.tag_configure("keyword", foreground="#ffcc00")
        self.input_text.tag_configure("comment", foreground="#E48686")
        self.input_text.tag_configure("string", foreground="#00cc99")
        self.message_bar = ttk.Label(root, text="", anchor="w", bootstyle="light")
        self.message_bar.pack(side="bottom", fill="x")
    def show_message(self, text, style="info"):
        self.message_bar.config(text=text, bootstyle=style)
    def process_code(self):
        code = self.input_text.get("1.0", tk.END)
        cleaned, stats = clean_code(
            code,
            remove_empty=self.remove_empty_var.get(),
            remove_docstrings=self.remove_docstring_var.get()
        )
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert(tk.END, cleaned)
        highlight_syntax(self.input_text)
        stats_text = (
            f"Lines: {stats['total_lines']} → {stats['cleaned_lines']}, "
            f"Comments: {stats['comment_lines']}, "
            f"Docstrings: {stats['docstring_lines']}, "
            f"Empty: {stats['empty_lines']}, "
            f"Chars: {stats['chars_before']} → {stats['chars_after']} "
            f"({stats['char_reduction']}% ↓)"
        )
        self.stats_label.config(text="DONE ✔")
        self.show_message(stats_text, style="success")
    def copy_cleaned(self):
        cleaned = self.output_text.get("1.0", tk.END)
        root.clipboard_clear()
        root.clipboard_append(cleaned)
        self.show_message("Cleaned code copied to clipboard.", "info")
    def export_cleaned(self):
        cleaned = self.output_text.get("1.0", tk.END).strip()
        if not cleaned:
            self.show_message("Nothing to export. Please process code first.", "danger")
            return
        try:
            with open("cleaned_output.py", "w", encoding="utf-8") as f:
                f.write(cleaned)
            self.show_message("Exported to cleaned_output.py", "success")
        except Exception as e:
            self.show_message(f"Export failed: {e}", "danger")
    def reset_all(self):
        self.input_text.delete("1.0", tk.END)
        self.output_text.delete("1.0", tk.END)
        self.stats_label.config(text="Stats: ")
        self.show_message("Reset complete.", "info")
if __name__ == "__main__":
    root = ttk.Window(themename="cyborg")
    app = PasteCleanerApp(root)
    root.mainloop()

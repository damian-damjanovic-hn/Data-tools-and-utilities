"""
Standalone GUI: PHP Serialized → JSON (full nested), stdlib-only.

Features:
- Accurate PHP unserializer (arrays, nested, ints, floats, bools, null).
- Optional lenient repair mode for mismatched string lengths (logs diagnostics).
- Optional shell-only cleanup (decodes HTML entities outside s:<len>:"...").
- Tkinter GUI with left controls and right panes (Input, Output, Diagnostics).
- Shortcuts: Ctrl+1 / F5 (Convert), Ctrl+O (Open), Ctrl+S (Save), Ctrl+L (Clear).

"""

import json
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


LENIENT_STRING_TERMINATOR = False  # allow repairs when s:<len> doesn't match close
WARNINGS = []

def _reset_warnings():
    WARNINGS.clear()


def _warn(kind: str, **data):
    WARNINGS.append({"kind": kind, **data})


class ParseError(Exception):
    """Parser error including byte position for better diagnostics."""
    def __init__(self, message: str, pos: int):
        super().__init__(message)
        self.pos = pos


def _read_until(b: bytes, i: int, delim: bytes):
    j = b.find(delim, i)
    if j == -1:
        raise ParseError("Unexpected end: delimiter not found", i)
    return b[i:j], j + len(delim)


def _parse_int(b: bytes, i: int):
    num, i = _read_until(b, i, b';')
    try:
        return int(num), i
    except Exception:
        raise ParseError(f"Invalid integer: {num!r}", i)


def _parse_float(b: bytes, i: int):
    num, i = _read_until(b, i, b';')
    try:
        return float(num), i
    except Exception:
        raise ParseError(f"Invalid float: {num!r}", i)


def _parse_bool(b: bytes, i: int):
    if b[i:i+2] not in (b'0;', b'1;'):
        raise ParseError("Invalid boolean token (expected 0; or 1;)", i)
    return (b[i:i+1] == b'1'), i + 2


def _decode_bytes(sbytes: bytes) -> str:
    try:
        return sbytes.decode('utf-8')
    except UnicodeDecodeError:
        return sbytes.decode('latin-1')


def _parse_string(b: bytes, i: int):
    """
    Parse s:<len>:"<bytes>";

    Strict behavior:
      - Consume exactly <len> bytes, then require closing '"' optional-ws ';'.

    Lenient repair (if LENIENT_STRING_TERMINATOR is True):
      - If the expected closing quote is not found at the exact position (i+len),
        search forward for the next '"' followed by optional whitespace and ';',
        and treat bytes up to that quote as the string content. Record a warning.
    """
    global LENIENT_STRING_TERMINATOR

    strlen_bytes, i = _read_until(b, i, b':')
    try:
        strlen = int(strlen_bytes)
    except Exception:
        raise ParseError(f"Invalid string length: {strlen_bytes!r}", i)

    if b[i:i+1] != b'"':
        raise ParseError('Expected opening quote for string', i)
    i += 1

    start = i
    end_expected = start + strlen

    if len(b) - start < strlen:
        if not LENIENT_STRING_TERMINATOR:
            raise ParseError('String length mismatch vs s:<len> (too short)', i)
        sbytes, i_new = _lenient_scan_close(b, start)
        if sbytes is None:
            raise ParseError('String length mismatch and no viable closing found', i)
        _warn(
            "string_length_repair_short",
            at_byte=start,
            declared_length=int(strlen),
            actual_length=int(len(sbytes)),
        )
        return _decode_bytes(sbytes), i_new

    sbytes = b[start:end_expected]
    i = end_expected

    if b[i:i+1] == b'"':
        i += 1
        while i < len(b) and b[i] in b' \t\r\n':
            i += 1
        if b[i:i+1] == b';':
            i += 1
            return _decode_bytes(sbytes), i

    if LENIENT_STRING_TERMINATOR:
        sbytes2, i_new = _lenient_scan_close(b, start)
        if sbytes2 is None:
            got = b[i:i+1]
            raise ParseError(f'Expected closing "\";" for string, got {got!r}', i)
        if len(sbytes2) != strlen:
            _warn(
                "string_length_repair_mismatch",
                at_byte=start,
                declared_length=int(strlen),
                actual_length=int(len(sbytes2)),
            )
        return _decode_bytes(sbytes2), i_new

    got = b[i:i+1]
    raise ParseError(f'Expected closing "\";" for string', i)


def _lenient_scan_close(b: bytes, start: int):
    """
    Search forward from 'start' for the earliest '"' followed by optional whitespace and ';'.
    Return (sbytes, new_index_after_;) or (None, None) if not found.

    Note: This is a heuristic fallback used only when the declared length is unreliable.
    """
    # Limit lookahead to reduce pathological scans (e.g., 1MB)
    MAX_LOOKAHEAD = 1_000_000
    end_limit = min(len(b), start + MAX_LOOKAHEAD)

    k = start
    while True:
        k = b.find(b'"', k, end_limit)
        if k == -1:
            return None, None
        j = k + 1
        while j < len(b) and b[j] in b' \t\r\n':
            j += 1
        if j < len(b) and b[j:j+1] == b';':
            sbytes = b[start:k]
            return sbytes, j + 1
        k += 1


def _parse_key(b: bytes, i: int):
    t = b[i:i+2]
    if t == b'i:':
        return _parse_int(b, i+2)
    elif t == b's:':
        return _parse_string(b, i+2)
    else:
        raise ParseError(f'Unsupported key type: {t!r}', i)


def _parse_value(b: bytes, i: int):
    t = b[i:i+2]

    if t == b's:':
        return _parse_string(b, i+2)
    if t == b'i:':
        return _parse_int(b, i+2)
    if t == b'd:':
        return _parse_float(b, i+2)
    if t == b'b:':
        return _parse_bool(b, i+2)
    if b[i:i+2] == b'N;':
        return None, i + 2
    if t == b'a:':
        i += 2
        count_bytes, i = _read_until(b, i, b':')
        try:
            count = int(count_bytes)
        except Exception:
            raise ParseError(f"Invalid array count: {count_bytes!r}", i)
        if b[i:i+1] != b'{':
            raise ParseError('Expected "{" after array length', i)
        i += 1
        items = []
        for _ in range(count):
            k, i = _parse_key(b, i)
            v, i = _parse_value(b, i)
            items.append((k, v))
        if b[i:i+1] != b'}':
            raise ParseError('Expected "}" to close array', i)
        i += 1

        # Convert to list if keys are contiguous ints starting at 0; else dict
        keys = [k for k, _ in items]
        if keys and all(isinstance(k, int) for k in keys) and keys == list(range(len(keys))):
            return [v for _, v in items], i
        else:
            d = {}
            for k, v in items:
                d[k] = v
            return d, i

    raise ParseError(f'Unsupported value type: {b[i:i+10]!r}', i)


def php_unserialize(serialized: str):
    """
    Deserialize a PHP-serialized value into Python (dict/list/str/int/float/bool/None).
    Works in bytes to respect s:<len> byte counts; decodes strings afterwards.
    """
    _reset_warnings()
    b = serialized.encode('utf-8', errors='surrogatepass')
    val, pos = _parse_value(b, 0)
    if b[pos:].strip():
        _warn("trailing_data", at_byte=pos, bytes_remaining=int(len(b) - pos))
    return val

def php_to_json(serialized: str, *, indent: int = 2, ensure_ascii: bool = False) -> str:
    obj = php_unserialize(serialized.strip())
    return json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii)

_STRING_TOKEN = re.compile(r's:(\d+):"((?:\\.|[^"\\])*)";', re.S)

def safe_cleanup_shell_only(s: str) -> str:
    """
    Cleans HTML entities/spacing in the NON-string shell only, preserving s:<len>:"...".
    Use this only if inputs sometimes contain entities like &quot; etc.
    """
    saved = []
    def _stash(m: re.Match) -> str:
        saved.append(m.group(0))
        return f'@@S{len(saved)-1}@@'

    shell = _STRING_TOKEN.sub(_stash, s)

    shell = (shell
        .replace("&gt;", ">")
        .replace("&lt;", "<")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#34;", '"')
        .replace("&#39;", "'")
    )
    shell = re.sub(r'[ \t\f\v]+', ' ', shell)
    shell = re.sub(r'\s*;\s*', ';', shell)
    shell = re.sub(r'\s*:\s*', ':', shell)
    shell = re.sub(r'\s*\{\s*', '{', shell)
    shell = re.sub(r'\s*\}\s*', '}', shell)

    for idx, tok in enumerate(saved):
        shell = shell.replace(f'@@S{idx}@@', tok)
    return shell

APP_TITLE = "PHP Serialized → JSON"
DEFAULT_SAMPLE = (
    'a:2:{s:10:"created_at";s:19:"2025-09-15 17:28:59";'
    's:5:"items";a:1:{i:0;a:3:{s:3:"sku";s:6:"807224";s:4:"name";s:11:"Sample Name";s:11:"qty_ordered";s:6:"2.0000";}}}'
)

try:
    TtkSpinbox = ttk.Spinbox  # type: ignore[attr-defined]
    HAS_TTK_SPINBOX = True
except Exception:
    TtkSpinbox = None
    HAS_TTK_SPINBOX = False


class PhpToJsonApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1200x760")
        self.minsize(980, 620)

        self.wrap_input_var = tk.BooleanVar(value=True)
        self.wrap_output_var = tk.BooleanVar(value=True)
        self.pretty_var = tk.BooleanVar(value=True)
        self.indent_var = tk.IntVar(value=2)
        self.cleanup_shell_var = tk.BooleanVar(value=False)
        self.lenient_var = tk.BooleanVar(value=True)
        self.theme_var = tk.StringVar(value="Dark")

        self._build_style()
        self._build_ui()
        self._bind_shortcuts()

        self.input_text.insert("1.0", DEFAULT_SAMPLE)

    def _build_style(self):
        self.style = ttk.Style(self)
        self._apply_theme("Dark")

    def _apply_theme(self, theme: str):
        # Basic light/dark palette
        if theme == "Dark":
            bg = "#0f172a"      # slate-900
            panel = "#111827"   # gray-900
            fg = "#e5e7eb"      # gray-200
            subfg = "#cbd5e1"   # slate-300
            accent = "#22d3ee"  # cyan-400
            text_bg = "#0b1020"
            text_sel = "#1e293b"
        else:
            bg = "#f8fafc"      # slate-50
            panel = "#ffffff"   # white
            fg = "#0f172a"      # slate-900
            subfg = "#334155"   # slate-700
            accent = "#0ea5e9"  # sky-500
            text_bg = "#ffffff"
            text_sel = "#e2e8f0" # slate-200

        self.configure(bg=bg)
        style = self.style
        style.theme_use("clam")

        style.configure("TFrame", background=bg)
        style.configure("Left.TFrame", background=panel, relief="flat")
        style.configure("Right.TFrame", background=bg)
        style.configure("TLabel", background=panel, foreground=fg)
        style.configure("Title.TLabel", background=panel, foreground=fg, font=("Segoe UI", 12, "bold"))
        style.configure("Sublabel.TLabel", background=panel, foreground=subfg, font=("Segoe UI", 9))
        style.configure("TButton", background=panel, foreground=fg, padding=6, relief="flat")
        style.map("TButton", background=[("active", accent)])
        style.configure("Accent.TButton", padding=8)
        style.configure("TCheckbutton", background=panel, foreground=fg)
        style.configure("TMenubutton", background=panel, foreground=fg)
        style.configure("TSeparator", background=bg)

        self.colors = {
            "bg": bg, "panel": panel, "fg": fg, "subfg": subfg,
            "accent": accent, "text_bg": text_bg, "text_sel": text_sel
        }

    def _build_ui(self):
        root_pw = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        root_pw.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(root_pw, style="Left.TFrame", width=300)
        root_pw.add(left, weight=0)

        right = ttk.Frame(root_pw, style="Right.TFrame")
        root_pw.add(right, weight=1)

        title = ttk.Label(left, text="PHP → JSON", style="Title.TLabel")
        subtitle = ttk.Label(left, text="Full nested conversion · stdlib only", style="Sublabel.TLabel")

        btn_parse = ttk.Button(left, text="Convert (Ctrl+1 / F5)", style="Accent.TButton", command=self.on_parse)
        btn_open  = ttk.Button(left, text="Open… (Ctrl+O)", command=self.on_open)
        btn_save  = ttk.Button(left, text="Save JSON… (Ctrl+S)", command=self.on_save)
        btn_copy  = ttk.Button(left, text="Copy Output", command=self.on_copy_output)
        btn_clear = ttk.Button(left, text="Clear (Ctrl+L)", command=self.on_clear)

        opts = ttk.LabelFrame(left, text="Options", padding=(8, 6))
        theme_label = ttk.Label(opts, text="Theme:")
        theme_combo = ttk.Combobox(opts, textvariable=self.theme_var, values=["Dark", "Light"], state="readonly", width=10)
        theme_combo.bind("<<ComboboxSelected>>", self.on_theme_change)

        pretty_cb = ttk.Checkbutton(opts, text="Pretty print", variable=self.pretty_var, command=self._update_indent_state)
        indent_label = ttk.Label(opts, text="Indent:")
        if HAS_TTK_SPINBOX:
            indent_spin = TtkSpinbox(opts, from_=0, to=8, textvariable=self.indent_var, width=4)
        else:
            indent_spin = tk.Spinbox(opts, from_=0, to=8, textvariable=self.indent_var, width=4)
        indent_spin.bind("<FocusOut>", lambda e: self._coerce_indent())
        indent_spin.bind("<Return>", lambda e: self._coerce_indent())

        wrap_in_cb = ttk.Checkbutton(opts, text="Wrap input", variable=self.wrap_input_var, command=self.on_wrap_change)
        wrap_out_cb = ttk.Checkbutton(opts, text="Wrap output", variable=self.wrap_output_var, command=self.on_wrap_change)
        cleanup_cb = ttk.Checkbutton(opts, text="Shell-only cleanup (HTML entities)", variable=self.cleanup_shell_var)
        lenient_cb = ttk.Checkbutton(opts, text="Lenient repairs (mismatched s:<len>)", variable=self.lenient_var)

        for w in (title, subtitle):
            w.pack(anchor="w", padx=12, pady=(12 if w is title else 2, 8))
        btn_parse.pack(fill=tk.X, padx=12, pady=(8, 6))
        btn_open.pack(fill=tk.X, padx=12, pady=4)
        btn_save.pack(fill=tk.X, padx=12, pady=4)
        btn_copy.pack(fill=tk.X, padx=12, pady=4)
        btn_clear.pack(fill=tk.X, padx=12, pady=(4, 10))

        opts.pack(fill=tk.X, padx=12, pady=(8, 12))
        opts.columnconfigure(1, weight=1)
        theme_label.grid(row=0, column=0, sticky="w", padx=(2, 6), pady=4)
        theme_combo.grid(row=0, column=1, sticky="w", padx=2, pady=4)

        pretty_cb.grid(row=1, column=0, sticky="w", padx=2, pady=4)
        indent_label.grid(row=1, column=1, sticky="w", padx=(16, 4), pady=4)
        indent_spin.grid(row=1, column=1, sticky="e", padx=(0, 4), pady=4)

        wrap_in_cb.grid(row=2, column=0, sticky="w", padx=2, pady=4)
        wrap_out_cb.grid(row=2, column=1, sticky="w", padx=2, pady=4)
        cleanup_cb.grid(row=3, column=0, columnspan=2, sticky="w", padx=2, pady=4)
        lenient_cb.grid(row=4, column=0, columnspan=2, sticky="w", padx=2, pady=(4, 6))

        right_pw = ttk.Panedwindow(right, orient=tk.VERTICAL)
        right_pw.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        input_frame = ttk.Frame(right_pw)
        output_frame = ttk.Frame(right_pw)
        diag_frame   = ttk.Frame(right_pw)
        right_pw.add(input_frame, weight=1)
        right_pw.add(output_frame, weight=1)
        right_pw.add(diag_frame,   weight=0)

        in_label = ttk.Label(input_frame, text="PHP serialized input")
        self.input_text = tk.Text(input_frame, undo=True, wrap="word", height=12,
                                  bg=self.colors["text_bg"], fg=self.colors["fg"],
                                  insertbackground=self.colors["fg"],
                                  selectbackground=self.colors["text_sel"])
        in_scroll = ttk.Scrollbar(input_frame, command=self.input_text.yview)
        self.input_text.configure(yscrollcommand=in_scroll.set)
        in_label.pack(anchor="w", padx=2, pady=(2, 4))
        self.input_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        in_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        out_label = ttk.Label(output_frame, text="JSON output")
        self.output_text = tk.Text(output_frame, wrap="word", height=12,
                                   bg=self.colors["text_bg"], fg=self.colors["fg"],
                                   insertbackground=self.colors["fg"],
                                   selectbackground=self.colors["text_sel"])
        out_scroll = ttk.Scrollbar(output_frame, command=self.output_text.yview)
        self.output_text.configure(yscrollcommand=out_scroll.set)
        out_label.pack(anchor="w", padx=2, pady=(2, 4))
        self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        out_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        diag_label = ttk.Label(diag_frame, text="Diagnostics (repairs, notes)")
        self.diag_text = tk.Text(diag_frame, wrap="word", height=8,
                                 bg=self.colors["text_bg"], fg=self.colors["fg"],
                                 insertbackground=self.colors["fg"],
                                 selectbackground=self.colors["text_sel"])
        diag_scroll = ttk.Scrollbar(diag_frame, command=self.diag_text.yview)
        self.diag_text.configure(yscrollcommand=diag_scroll.set)
        diag_label.pack(anchor="w", padx=2, pady=(2, 4))
        self.diag_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        diag_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.diag_text.configure(state="disabled")

        self.status = tk.StringVar(value="Ready")
        status_bar = ttk.Label(self, textvariable=self.status, anchor="w")
        status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=2)

        self.input_text.tag_configure("error_here", background="#7f1d1d", foreground="#ffffff")
        self._update_indent_state()
        self.on_wrap_change()

    def _bind_shortcuts(self):
        self.bind("<Control-Key-1>", lambda e: self.on_parse())
        self.bind("<F5>",           lambda e: self.on_parse())
        self.bind("<Control-o>",    lambda e: self.on_open())
        self.bind("<Control-s>",    lambda e: self.on_save())
        self.bind("<Control-l>",    lambda e: self.on_clear())

    def on_theme_change(self, *_):
        self._apply_theme(self.theme_var.get())
        for w in (self.input_text, self.output_text, self.diag_text):
            w.configure(bg=self.colors["text_bg"], fg=self.colors["fg"],
                        insertbackground=self.colors["fg"],
                        selectbackground=self.colors["text_sel"])

    def _coerce_indent(self):
        try:
            v = int(self.indent_var.get())
        except Exception:
            v = 2
        v = max(0, min(8, v))
        self.indent_var.set(v)

    def _update_indent_state(self):
        pass

    def on_wrap_change(self):
        self.input_text.configure(wrap=("word" if self.wrap_input_var.get() else "none"))
        self.output_text.configure(wrap=("word" if self.wrap_output_var.get() else "none"))

    def on_parse(self):
        global LENIENT_STRING_TERMINATOR
        self.clear_error_highlight()
        self._clear_diag()

        raw = self.input_text.get("1.0", tk.END).strip()
        if not raw:
            self.set_status("Input is empty.")
            return
        try:
            LENIENT_STRING_TERMINATOR = bool(self.lenient_var.get())

            if self.cleanup_shell_var.get():
                raw = safe_cleanup_shell_only(raw)

            indent = self.indent_var.get() if self.pretty_var.get() else 0
            obj = php_unserialize(raw)
            out = json.dumps(obj, indent=indent, ensure_ascii=False)
            self.output_text.delete("1.0", tk.END)
            self.output_text.insert("1.0", out)

            self._emit_diag(WARNINGS)
            if WARNINGS:
                self.set_status(f"Converted with {len(WARNINGS)} note(s).")
            else:
                self.set_status("Converted successfully.")
        except ParseError as pe:
            context = self._context_around_byte(raw, pe.pos)
            diag = {
                "error": str(pe),
                "byte_pos": pe.pos,
                "context": context
            }
            self.output_text.delete("1.0", tk.END)
            self.output_text.insert("1.0", json.dumps(diag, indent=2, ensure_ascii=False))
            self._highlight_error_at_byte(pe.pos, raw)
            self.set_status(f"Parse error at byte {pe.pos}")
        except Exception as e:
            self.output_text.delete("1.0", tk.END)
            self.output_text.insert("1.0", json.dumps({"error": str(e)}, indent=2))
            self.set_status(f"Error: {e}")

    def on_open(self):
        path = filedialog.askopenfilename(
            title="Open PHP serialized text",
            filetypes=[("Text files", "*.txt *.log *.php *.data *.ser *.dump"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            self.input_text.delete("1.0", tk.END)
            self.input_text.insert("1.0", content)
            self.set_status(f"Loaded: {path}")
        except Exception as e:
            messagebox.showerror("Open failed", str(e))
            self.set_status(f"Open failed: {e}")

    def on_save(self):
        data = self.output_text.get("1.0", tk.END).strip()
        if not data:
            if messagebox.askyesno("No output", "Output is empty. Convert now?"):
                self.on_parse()
                data = self.output_text.get("1.0", tk.END).strip()
                if not data:
                    return
            else:
                return
        path = filedialog.asksaveasfilename(
            title="Save JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
            self.set_status(f"Saved: {path}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            self.set_status(f"Save failed: {e}")

    def on_copy_output(self):
        data = self.output_text.get("1.0", tk.END).strip()
        if not data:
            self.set_status("Nothing to copy.")
            return
        self.clipboard_clear()
        self.clipboard_append(data)
        self.set_status("Output copied to clipboard.")

    def on_clear(self):
        self.input_text.delete("1.0", tk.END)
        self.output_text.delete("1.0", tk.END)
        self._clear_diag()
        self.clear_error_highlight()
        self.set_status("Cleared.")

    # ---- Diagnostics helpers ----
    def _clear_diag(self):
        self.diag_text.configure(state="normal")
        self.diag_text.delete("1.0", tk.END)
        self.diag_text.configure(state="disabled")

    def _emit_diag(self, notes):
        if not notes:
            return
        self.diag_text.configure(state="normal")
        self.diag_text.insert("1.0", "Diagnostics:\n")
        for n in notes:
            self.diag_text.insert(tk.END, f"- {n['kind']}: {json.dumps({k:v for k,v in n.items() if k!='kind'}, ensure_ascii=False)}\n")
        self.diag_text.configure(state="disabled")

    # ---- Utilities ----
    def set_status(self, msg: str):
        self.status.set(msg)

    def clear_error_highlight(self):
        self.input_text.tag_remove("error_here", "1.0", tk.END)

    def _highlight_error_at_byte(self, byte_pos: int, raw_text: str):
        """
        Convert byte offset to a Tk Text index (assuming utf-8), then highlight.
        """
        try:
            b = raw_text.encode("utf-8", errors="surrogatepass")
            prefix = b[:max(0, byte_pos)]
            prefix_txt = prefix.decode("utf-8", errors="ignore")
            ch_index = len(prefix_txt)
            start_idx = f"1.0+{ch_index}c"
            end_idx = f"1.0+{ch_index+1}c"
            self.input_text.tag_add("error_here", start_idx, end_idx)
            self.input_text.see(start_idx)
        except Exception:
            pass

    def _context_around_byte(self, raw_text: str, byte_pos: int, radius: int = 24) -> str:
        """
        Return a short context excerpt around the given byte position to help debug.
        """
        b = raw_text.encode("utf-8", errors="surrogatepass")
        start = max(0, byte_pos - radius)
        end = min(len(b), byte_pos + radius)
        snippet = b[start:end].decode("utf-8", errors="replace")
        pointer = " " * (len(b[start:byte_pos].decode("utf-8", errors="ignore"))) + "▲"
        return f"...{snippet}...\n{pointer}"


def main():
    app = PhpToJsonApp()
    app.mainloop()


if __name__ == "__main__":
    main()

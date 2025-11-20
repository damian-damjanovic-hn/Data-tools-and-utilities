from __future__ import annotations
import os
import json
import base64
import ssl
import threading
import time
from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional
try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import PRIMARY, SUCCESS, WARNING, DANGER
    BOOTSTRAP_AVAILABLE = True
    TKBase = tb.Window
    ttkmod = tb
except Exception:
    BOOTSTRAP_AVAILABLE = False
    import tkinter as tk
    from tkinter import ttk
    TKBase = tk.Tk
    ttkmod = ttk
try:
    from tkinter import messagebox, filedialog
except Exception:
    pass
USE_REQUESTS = False
try:
    import requests
    USE_REQUESTS = True
except Exception:
    import urllib.request
    import urllib.error
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".order_client_config.json")
@dataclass
class ApiConfig:
    base_url: str = "https://api.example.com"
    endpoint_path: str = "/orders"
    username: str = ""
    password: str = ""
    save_password: bool = False
    timeout_sec: int = 15
    verify_ssl: bool = True
    default_supplier_id: str = ""
    default_tax_rate: str = "0.00"
    currency_symbol: str = "$"
    @property
    def endpoint_url(self) -> str:
        if not self.base_url:
            return ""
        return self.base_url.rstrip("/") + "/" + self.endpoint_path.lstrip("/")
@dataclass
class OrderData:
    supplier_id: str = ""
    order_reference: str = ""
    comment: str = ""
    full_name: str = ""
    phone: str = ""
    line_1: str = ""
    city: str = ""
    state: str = ""
    post_code: str = ""
    sku: str = ""
    sap_id: str = ""
    product_name: str = ""
    qty: str = "1"
    cost_ex: str = "0.00"
    subtotal: str = "0.00"
    tax: str = "0.00"
    total: str = "0.00"
def d(value: str | float | int) -> Decimal:
    """Safe Decimal conversion."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
def fmt_money(value: Decimal, symbol: str = "$") -> str:
    return f"{symbol}{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"
def compute_totals(order: OrderData, tax_rate: Decimal) -> OrderData:
    qty = int(order.qty or "0")
    cost_ex = d(order.cost_ex or "0")
    subtotal = (cost_ex * Decimal(qty)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tax = (subtotal * tax_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = (subtotal + tax).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    order.subtotal = f"{subtotal:.2f}"
    order.tax = f"{tax:.2f}"
    order.total = f"{total:.2f}"
    return order
class OrderApiClient:
    def __init__(self, cfg: ApiConfig):
        self.cfg = cfg
    def send_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send POST {payload} to cfg.endpoint_url with Basic Auth.
        Returns dict with status_code, body_text, and duration.
        """
        if not self.cfg.endpoint_url:
            raise ValueError("Endpoint URL is not configured.")
        start = time.time()
        token = base64.b64encode(f"{self.cfg.username}:{self.cfg.password}".encode("utf-8")).decode("ascii")
        headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = json.dumps(payload).encode("utf-8")
        if USE_REQUESTS:
            try:
                r = requests.post(
                    self.cfg.endpoint_url,
                    headers=headers,
                    data=body,
                    timeout=self.cfg.timeout_sec,
                    verify=self.cfg.verify_ssl,
                )
                duration = time.time() - start
                try:
                    text = r.text
                except Exception:
                    text = ""
                return {"status_code": r.status_code, "body_text": text, "duration": duration}
            except Exception as ex:
                duration = time.time() - start
                raise RuntimeError(f"Network error: {ex}") from ex
        else:
            req = urllib.request.Request(self.cfg.endpoint_url, data=body, headers=headers, method="POST")
            context = None
            if not self.cfg.verify_ssl:
                context = ssl._create_unverified_context()
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.timeout_sec, context=context) as resp:
                    status_code = resp.getcode()
                    text = resp.read().decode("utf-8", errors="replace")
                    duration = time.time() - start
                    return {"status_code": status_code, "body_text": text, "duration": duration}
            except urllib.error.HTTPError as e:
                duration = time.time() - start
                text = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
                return {"status_code": e.code, "body_text": text, "duration": duration}
            except urllib.error.URLError as e:
                raise RuntimeError(f"Network error: {e.reason}") from e
class OrderClientApp(TKBase):
    def __init__(self):
        if BOOTSTRAP_AVAILABLE:
            super().__init__(title="Order Create Client", themename="darkly")
        else:
            super().__init__()
            self.title("Order Create Client")
        self.geometry("1100x720")
        self.minsize(950, 600)
        self.cfg = self._load_config()
        self.order = OrderData()
        self._search_thread: Optional[threading.Thread] = None
        self._build_styles()
        self._build_ui()
        self._refresh_preview()  # initial preview
    def _build_styles(self):
        if not BOOTSTRAP_AVAILABLE:
            style = ttkmod.Style()
            style.theme_use("clam")
            style.configure("TLabel", padding=2)
            style.configure("TEntry", padding=4)
            style.configure("TButton", padding=6)
            style.configure("Treeview", rowheight=24)
    def _build_ui(self):
        nb = ttkmod.Notebook(self)
        nb.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.tab_order = ttkmod.Frame(nb)
        self.tab_settings = ttkmod.Frame(nb)
        nb.add(self.tab_order, text="Order")
        nb.add(self.tab_settings, text="Settings")
        container = ttkmod.Frame(self.tab_order)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=0)  # left fixed
        container.columnconfigure(1, weight=1)  # right expands
        container.rowconfigure(0, weight=1)
        left = self._build_order_form(container)
        right = self._build_order_preview(container)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right.grid(row=0, column=1, sticky="nsew")
        self._build_settings_tab(self.tab_settings)
        self.status_var = self._mk_str("")
        status = ttkmod.Label(self, textvariable=self.status_var, anchor="w")
        status.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self._set_status("Ready.")
        self.bind("<Control-Return>", lambda e: self._on_submit())
        self.bind("<F5>", lambda e: self._on_recalc())
        self.bind("<Control-s>", lambda e: self._save_config())
    def _build_order_form(self, parent):
        lf = ttkmod.Labelframe(parent, text="Order Details", padding=8)
        for r in range(20):
            lf.rowconfigure(r, weight=0)
        lf.columnconfigure(0, weight=1)
        lf.columnconfigure(1, weight=1)
        self._label(lf, "supplier_id", "Supplier ID", row=0, bootstyle=DANGER)
        self.var_supplier_id = self._mk_str(self.cfg.default_supplier_id)
        self._entry(lf, self.var_supplier_id, row=0, col=1)
        self._label(lf, "order_reference", "Order reference", row=1, bootstyle=DANGER)
        self.var_order_reference = self._mk_str("")
        self._entry(lf, self.var_order_reference, row=1, col=1)
        self._label(lf, "comment", "Comment", row=2, bootstyle=SUCCESS)
        self.var_comment = self._mk_str("")
        self._entry(lf, self.var_comment, row=2, col=1)
        self._label(lf, "full_name", "Full name", row=3, bootstyle=DANGER)
        self.var_full_name = self._mk_str("")
        self._entry(lf, self.var_full_name, row=3, col=1)
        self._label(lf, "phone", "Phone", row=4, bootstyle=DANGER)
        self.var_phone = self._mk_str("")
        self._entry(lf, self.var_phone, row=4, col=1)
        self._label(lf, "line_1", "Address line 1", row=5, bootstyle=WARNING)
        self.var_line_1 = self._mk_str("")
        self._entry(lf, self.var_line_1, row=5, col=1)
        self._label(lf, "city", "City", row=6, bootstyle=WARNING)
        self.var_city = self._mk_str("")
        self._entry(lf, self.var_city, row=6, col=1)
        self._label(lf, "state", "State", row=7, bootstyle=DANGER)
        self.var_state = self._mk_str("NSW")
        states = ["NSW", "VIC", "QLD", "SA", "WA", "TAS", "ACT", "NT"]
        self._combo(lf, self.var_state, states, row=7, col=1)
        self._label(lf, "post_code", "Post Code", row=8, bootstyle=WARNING)
        self.var_post_code = self._mk_str("")
        self._entry(lf, self.var_post_code, row=8, col=1)
        self._label(lf, "sku", "SKU", row=9, bootstyle=DANGER)
        self.var_sku = self._mk_str("")
        self._entry(lf, self.var_sku, row=9, col=1)
        self._label(lf, "sap_id", "SAP ID", row=10, bootstyle=DANGER)
        self.var_sap_id = self._mk_str("")
        self._entry(lf, self.var_sap_id, row=10, col=1)
        self._label(lf, "product_name", "Product Name", row=11, bootstyle=DANGER)
        self.var_product_name = self._mk_str("")
        self._entry(lf, self.var_product_name, row=11, col=1)
        self._label(lf, "qty", "Qty", row=12, bootstyle=DANGER)
        self.var_qty = self._mk_str("1")
        self._spin(lf, self.var_qty, row=12, col=1, from_=1, to=1_000_000, width=12)
        self._label(lf, "cost_ex", "Cost ex", row=13, bootstyle=DANGER)
        self.var_cost_ex = self._mk_str("0.00")
        e_cost = self._entry(lf, self.var_cost_ex, row=13, col=1)
        e_cost.bind("<KeyRelease>", lambda e: self._on_recalc())
        sep = ttkmod.Separator(lf)
        sep.grid(row=14, column=0, columnspan=2, sticky="ew", pady=6)
        self._label(lf, "subtotal", "Subtotal", row=15, bootstyle=SUCCESS)
        self.subtotal_var = self._mk_str(f"{self.cfg.currency_symbol}0.00")
        self._readonly_entry(lf, self.subtotal_var, row=15, col=1)
        self._label(lf, "tax", "Tax", row=16, bootstyle=SUCCESS)
        self.tax_var = self._mk_str(f"{self.cfg.currency_symbol}0.00")
        self._readonly_entry(lf, self.tax_var, row=16, col=1)
        self._label(lf, "total", "Total", row=17, bootstyle=SUCCESS)
        self.total_var = self._mk_str(f"{self.cfg.currency_symbol}0.00")
        self._readonly_entry(lf, self.total_var, row=17, col=1)
        btns = ttkmod.Frame(lf)
        btns.grid(row=18, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        for c in range(3):
            btns.columnconfigure(c, weight=1)
        self.btn_recalc = ttkmod.Button(btns, text="Recalculate", command=self._on_recalc)
        self.btn_submit = ttkmod.Button(btns, text="Submit Order", command=self._on_submit)
        self.btn_clear = ttkmod.Button(btns, text="Clear", command=self._on_clear)
        self.btn_recalc.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.btn_submit.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self.btn_clear.grid(row=0, column=2, sticky="ew")
        return lf
    def _build_order_preview(self, parent):
        rf = ttkmod.Labelframe(parent, text="Preview & Response", padding=8)
        rf.columnconfigure(0, weight=1)
        rf.rowconfigure(0, weight=1)
        rf.rowconfigure(2, weight=1)
        ttkmod.Label(rf, text="Payload preview").grid(row=0, column=0, sticky="w")
        self.txt_preview = self._mk_text(rf, height=16)
        self.txt_preview.grid(row=1, column=0, sticky="nsew", pady=(2, 8))
        ttkmod.Label(rf, text="API response").grid(row=2, column=0, sticky="w")
        self.txt_response = self._mk_text(rf, height=14)
        self.txt_response.grid(row=3, column=0, sticky="nsew", pady=(2, 0))
        bar = ttkmod.Frame(rf)
        bar.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        for c in range(5):
            bar.columnconfigure(c, weight=0)
        bar.columnconfigure(5, weight=1)
        self.btn_copy_payload = ttkmod.Button(bar, text="Copy payload", command=self._copy_payload)
        self.btn_save_payload = ttkmod.Button(bar, text="Save payload…", command=self._save_payload)
        self.btn_copy_response = ttkmod.Button(bar, text="Copy response", command=self._copy_response)
        self.btn_copy_payload.grid(row=0, column=0, padx=(0, 6))
        self.btn_save_payload.grid(row=0, column=1, padx=(0, 6))
        self.btn_copy_response.grid(row=0, column=2, padx=(0, 6))
        return rf
    def _build_settings_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        sf = ttkmod.Labelframe(parent, text="API Configuration", padding=10)
        sf.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        for r in range(12):
            sf.rowconfigure(r, weight=0)
        sf.columnconfigure(0, weight=1)
        sf.columnconfigure(1, weight=1)
        ttkmod.Label(sf, text="Base URL").grid(row=0, column=0, sticky="w")
        self.var_base_url = self._mk_str(self.cfg.base_url)
        ttkmod.Entry(sf, textvariable=self.var_base_url).grid(row=0, column=1, sticky="ew")
        ttkmod.Label(sf, text="Endpoint path").grid(row=1, column=0, sticky="w")
        self.var_endpoint = self._mk_str(self.cfg.endpoint_path)
        ttkmod.Entry(sf, textvariable=self.var_endpoint).grid(row=1, column=1, sticky="ew")
        ttkmod.Label(sf, text="Username").grid(row=2, column=0, sticky="w")
        self.var_username = self._mk_str(self.cfg.username)
        ttkmod.Entry(sf, textvariable=self.var_username).grid(row=2, column=1, sticky="ew")
        ttkmod.Label(sf, text="Password").grid(row=3, column=0, sticky="w")
        self.var_password = self._mk_str(self.cfg.password if self.cfg.save_password else "")
        ttkmod.Entry(sf, textvariable=self.var_password, show="•").grid(row=3, column=1, sticky="ew")
        self.var_save_pwd = self._mk_bool(self.cfg.save_password)
        ttkmod.Checkbutton(sf, text="Save password (plain text in local config)", variable=self.var_save_pwd).grid(row=4, column=1, sticky="w", pady=(2, 8))
        ttkmod.Label(sf, text="Timeout (sec)").grid(row=5, column=0, sticky="w")
        self.var_timeout = self._mk_int(self.cfg.timeout_sec)
        ttkmod.Spinbox(sf, from_=5, to=120, textvariable=self.var_timeout, width=8).grid(row=5, column=1, sticky="w")
        self.var_verify_ssl = self._mk_bool(self.cfg.verify_ssl)
        ttkmod.Checkbutton(sf, text="Verify SSL certificate", variable=self.var_verify_ssl).grid(row=6, column=1, sticky="w")
        ttkmod.Label(sf, text="Default Supplier ID").grid(row=7, column=0, sticky="w")
        self.var_default_supplier = self._mk_str(self.cfg.default_supplier_id)
        ttkmod.Entry(sf, textvariable=self.var_default_supplier).grid(row=7, column=1, sticky="ew")
        ttkmod.Label(sf, text="Default tax rate (e.g., 0.10)").grid(row=8, column=0, sticky="w")
        self.var_tax_rate = self._mk_str(self.cfg.default_tax_rate)
        ttkmod.Entry(sf, textvariable=self.var_tax_rate).grid(row=8, column=1, sticky="ew")
        ttkmod.Label(sf, text="Currency symbol").grid(row=9, column=0, sticky="w")
        self.var_currency_symbol = self._mk_str(self.cfg.currency_symbol or "$")
        ttkmod.Entry(sf, textvariable=self.var_currency_symbol, width=6).grid(row=9, column=1, sticky="w")
        btns = ttkmod.Frame(sf)
        btns.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        for c in range(3):
            btns.columnconfigure(c, weight=1)
        ttkmod.Button(btns, text="Save settings", command=self._save_config).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttkmod.Button(btns, text="Test connection", command=self._test_connection).grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttkmod.Button(btns, text="Reset to defaults", command=self._reset_defaults).grid(row=0, column=2, sticky="ew")
    def _label(self, parent, key: str, text: str, row: int, bootstyle=None):
        lbl = ttkmod.Label(parent, text=text)
        if BOOTSTRAP_AVAILABLE and bootstyle:
            try:
                lbl.configure(bootstyle=bootstyle)
            except Exception:
                pass
        lbl.grid(row=row, column=0, sticky="w", pady=2)
    def _entry(self, parent, var, row: int, col: int):
        e = ttkmod.Entry(parent, textvariable=var)
        e.grid(row=row, column=col, sticky="ew", pady=2)
        return e
    def _readonly_entry(self, parent, var, row: int, col: int):
        e = ttkmod.Entry(parent, textvariable=var, state="readonly")
        e.grid(row=row, column=col, sticky="ew", pady=2)
        return e
    def _combo(self, parent, var, values, row: int, col: int):
        cb = ttkmod.Combobox(parent, textvariable=var, values=values, state="readonly")
        cb.grid(row=row, column=col, sticky="ew", pady=2)
        return cb
    def _spin(self, parent, var, row: int, col: int, **opts):
        sp = ttkmod.Spinbox(parent, textvariable=var, **opts)
        sp.grid(row=row, column=col, sticky="w", pady=2)
        sp.bind("<KeyRelease>", lambda e: self._on_recalc())
        sp.bind("<<Increment>>", lambda e: self._on_recalc())
        sp.bind("<<Decrement>>", lambda e: self._on_recalc())
        return sp
    def _mk_str(self, v=""):
        if BOOTSTRAP_AVAILABLE:
            return ttkmod.StringVar(value=v)
        import tkinter as tk
        return tk.StringVar(value=v)
    def _mk_bool(self, v=False):
        if BOOTSTRAP_AVAILABLE:
            return ttkmod.BooleanVar(value=v)
        import tkinter as tk
        return tk.BooleanVar(value=v)
    def _mk_int(self, v=0):
        if BOOTSTRAP_AVAILABLE:
            return ttkmod.IntVar(value=v)
        import tkinter as tk
        return tk.IntVar(value=v)
    def _mk_text(self, parent, height=12):
        import tkinter as tk
        txt = tk.Text(parent, height=height, wrap="none")
        yscroll = ttkmod.Scrollbar(parent, orient="vertical", command=txt.yview)
        xscroll = ttkmod.Scrollbar(parent, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        txt.grid(row=1, column=0, sticky="nsew")
        yscroll.grid(row=1, column=1, sticky="ns")
        xscroll.grid(row=2, column=0, sticky="ew")
        return txt
    def _set_status(self, s: str):
        self.status_var.set(s)
    def _collect_order(self) -> OrderData:
        od = OrderData(
            supplier_id=self.var_supplier_id.get().strip(),
            order_reference=self.var_order_reference.get().strip(),
            comment=self.var_comment.get().strip(),
            full_name=self.var_full_name.get().strip(),
            phone=self.var_phone.get().strip(),
            line_1=self.var_line_1.get().strip(),
            city=self.var_city.get().strip(),
            state=self.var_state.get().strip(),
            post_code=self.var_post_code.get().strip(),
            sku=self.var_sku.get().strip(),
            sap_id=self.var_sap_id.get().strip(),
            product_name=self.var_product_name.get().strip(),
            qty=self.var_qty.get().strip(),
            cost_ex=self.var_cost_ex.get().strip(),
            subtotal=self.order.subtotal,
            tax=self.order.tax,
            total=self.order.total,
        )
        return od
    def _validate_order(self, od: OrderData) -> list[str]:
        errs = []
        required_fields = {
            "supplier_id": od.supplier_id,
            "order_reference": od.order_reference,
            "full_name": od.full_name,
            "phone": od.phone,
            "state": od.state,
            "sku": od.sku,
            "sap_id": od.sap_id,
            "product_name": od.product_name,
            "qty": od.qty,
            "cost_ex": od.cost_ex,
        }
        for k, v in required_fields.items():
            if not v:
                errs.append(f"{k.replace('_', ' ').title()} is required.")
        try:
            q = int(od.qty)
            if q <= 0:
                errs.append("Qty must be a positive integer.")
        except Exception:
            errs.append("Qty must be an integer.")
        try:
            _ = d(od.cost_ex)
        except Exception:
            errs.append("Cost ex must be a number (e.g., 12.34).")
        if od.post_code and not od.post_code.isdigit():
            errs.append("Post Code should be numeric.")
        return errs
    def _build_payload(self, od: OrderData) -> Dict[str, Any]:
        """
        Map UI fields to snake_case API payload (customize as needed).
        """
        payload = {
            "supplier_id": od.supplier_id,
            "order_reference": od.order_reference,
            "comment": od.comment,
            "full_name": od.full_name,
            "phone": od.phone,
            "line_1": od.line_1,
            "city": od.city,
            "state": od.state,
            "post_code": od.post_code,
            "sku": od.sku,
            "sap_id": od.sap_id,
            "product_name": od.product_name,
            "qty": int(od.qty or "0"),
            "cost_ex": float(d(od.cost_ex)),
            "subtotal": float(d(od.subtotal)),
            "tax": float(d(od.tax)),
            "total": float(d(od.total)),
        }
        return payload
    def _refresh_preview(self):
        od = self._collect_order()
        try:
            tax_rate = d(self.var_tax_rate.get())
        except Exception:
            tax_rate = d("0.00")
        self.order = compute_totals(od, tax_rate)
        self.subtotal_var.set(fmt_money(d(self.order.subtotal), self.var_currency_symbol.get()))
        self.tax_var.set(fmt_money(d(self.order.tax), self.var_currency_symbol.get()))
        self.total_var.set(fmt_money(d(self.order.total), self.var_currency_symbol.get()))
        payload = self._build_payload(self.order)
        self._set_text(self.txt_preview, json.dumps(payload, indent=2))
    def _set_text(self, widget, text: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="normal")
    def _on_recalc(self):
        self._refresh_preview()
        self._set_status("Totals recalculated.")
    def _on_clear(self):
        self.var_supplier_id.set(self.cfg.default_supplier_id or "")
        self.var_order_reference.set("")
        self.var_comment.set("")
        self.var_full_name.set("")
        self.var_phone.set("")
        self.var_line_1.set("")
        self.var_city.set("")
        self.var_state.set("NSW")
        self.var_post_code.set("")
        self.var_sku.set("")
        self.var_sap_id.set("")
        self.var_product_name.set("")
        self.var_qty.set("1")
        self.var_cost_ex.set("0.00")
        self._refresh_preview()
        self._set_status("Cleared.")
    def _on_submit(self):
        od = self._collect_order()
        errs = self._validate_order(od)
        if errs:
            messagebox.showerror("Validation errors", "\n".join(errs))
            return
        self._refresh_preview()
        payload = self._build_payload(self.order)
        client = OrderApiClient(self._collect_config())
        self._set_status("Submitting order…")
        self._disable_buttons(True)
        def worker():
            try:
                res = client.send_order(payload)
                msg = (
                    f"Status: {res['status_code']}\n"
                    f"Duration: {res['duration']:.3f}s\n"
                    f"Body:\n{res['body_text']}"
                )
                self.after(0, lambda: self._set_text(self.txt_response, msg))
                self.after(0, lambda: self._set_status(f"Submit complete (HTTP {res['status_code']})."))
            except Exception as ex:
                self.after(0, lambda: self._set_text(self.txt_response, f"Error: {ex}"))
                self.after(0, lambda: self._set_status("Submit failed."))
            finally:
                self.after(0, lambda: self._disable_buttons(False))
        t = threading.Thread(target=worker, daemon=True)
        t.start()
    def _disable_buttons(self, disabled: bool):
        state = "disabled" if disabled else "normal"
        try:
            self.btn_submit.configure(state=state)
            self.btn_recalc.configure(state=state)
            self.btn_clear.configure(state=state)
            self.btn_copy_payload.configure(state=state)
            self.btn_copy_response.configure(state=state)
            self.btn_save_payload.configure(state=state)
        except Exception:
            pass
    def _copy_payload(self):
        od = self._collect_order()
        payload = self._build_payload(compute_totals(od, d(self.var_tax_rate.get() or "0")))
        self.clipboard_clear()
        self.clipboard_append(json.dumps(payload, indent=2))
        self._set_status("Payload copied to clipboard.")
    def _save_payload(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            title="Save payload"
        )
        if not path:
            return
        od = self._collect_order()
        payload = self._build_payload(compute_totals(od, d(self.var_tax_rate.get() or "0")))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        self._set_status(f"Saved payload to {path}")
    def _copy_response(self):
        import tkinter as tk
        text = self.txt_response.get("1.0", tk.END).strip()
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("Response copied to clipboard.")
    def _collect_config(self) -> ApiConfig:
        cfg = ApiConfig(
            base_url=self.var_base_url.get().strip(),
            endpoint_path=self.var_endpoint.get().strip(),
            username=self.var_username.get().strip(),
            password=self.var_password.get().strip(),
            save_password=self.var_save_pwd.get(),
            timeout_sec=int(self.var_timeout.get()),
            verify_ssl=bool(self.var_verify_ssl.get()),
            default_supplier_id=self.var_default_supplier.get().strip(),
            default_tax_rate=self.var_tax_rate.get().strip() or "0.00",
            currency_symbol=self.var_currency_symbol.get().strip() or "$",
        )
        self.subtotal_var.set(fmt_money(d(self.order.subtotal), cfg.currency_symbol))
        self.tax_var.set(fmt_money(d(self.order.tax), cfg.currency_symbol))
        self.total_var.set(fmt_money(d(self.order.total), cfg.currency_symbol))
        return cfg
    def _save_config(self):
        cfg = self._collect_config()
        data = asdict(cfg)
        if not cfg.save_password:
            data["password"] = ""  # do not persist
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._set_status(f"Settings saved to {CONFIG_PATH}")
        except Exception as ex:
            messagebox.showerror("Save settings", str(ex))
        self.cfg = cfg
    def _load_config(self) -> ApiConfig:
        if not os.path.exists(CONFIG_PATH):
            return ApiConfig()
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ApiConfig(**{
                **asdict(ApiConfig()),
                **data,
            })
        except Exception:
            return ApiConfig()
    def _reset_defaults(self):
        self.var_base_url.set("https://api.example.com")
        self.var_endpoint.set("/orders")
        self.var_username.set("")
        self.var_password.set("")
        self.var_save_pwd.set(False)
        self.var_timeout.set(15)
        self.var_verify_ssl.set(True)
        self.var_default_supplier.set("")
        self.var_tax_rate.set("0.00")
        self.var_currency_symbol.set("$")
        self._set_status("Settings reset to defaults.")
    def _test_connection(self):
        cfg = self._collect_config()
        self._set_status("Testing connection…")
        def worker():
            try:
                client = OrderApiClient(cfg)
                res = client.send_order({"ping": True})
                msg = f"Test status: {res['status_code']}, duration {res['duration']:.3f}s\n{res['body_text']}"
                self.after(0, lambda: self._set_text(self.txt_response, msg))
                self.after(0, lambda: self._set_status("Connection test finished."))
            except Exception as ex:
                self.after(0, lambda: self._set_text(self.txt_response, f"Test failed: {ex}"))
                self.after(0, lambda: self._set_status("Connection test failed."))
        threading.Thread(target=worker, daemon=True).start()
def main():
    app = OrderClientApp()
    app.mainloop()
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

"""
Order Create Client — Compact portrait UI with scrollable Order tab, Supplier filter, and visibility toggles
------------------------------------------------------------------------------------------------------------
Tabs (single-order flow):
  1) Order & Item(s)  (portrait layout; scrollable; Item #2 optional via toggle; Quick Tools bar)
  2) Customer         (shipping address)
  3) Preview          (live JSON; read-only)
  4) Response         (friendly code + raw body; pretty-prints JSON)
Extras:
  5) Supplier Picker  (filterable, persistent cache, stats; sets Supplier ID; NO URL column)
  6) Settings         (API endpoints, Basic Auth / authorization override, presets, HIDE ROWS options)
Schema (exactly as specified):
{
  "supplier": "<supplier_url>",
  "order_reference": "...",
  "order_date": "YYYY-MM-DDTHH:MM:SS.000",
  "test_flag": false,
  "currency_code": "AUD",
  "comment": "order_submitted_manually",
  "items": [ { ... } ],
  "shipping_address": { ... }
}
Author: M365 Copilot for Damian
"""
from __future__ import annotations
import os
import json
import base64
import ssl
import threading
import time
from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import DANGER, SUCCESS, WARNING
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
SUPPLIER_CACHE_PATH = os.path.join(os.path.expanduser("~"), ".order_suppliers_cache.json")
@dataclass
class ApiConfig:
    orders_base_url: str = "https://www.the-edge.io"
    orders_endpoint_path: str = "/restapi/v4/orders/"
    suppliers_url: str = "https://api.virtualstock.com/restapi/v4/suppliers/?limit=1000&offset=0"
    username: str = ""
    password: str = ""
    save_password: bool = False
    auth_header_override: str = ""
    timeout_sec: int = 25
    verify_ssl: bool = True
    supplier_url_template: str = "https://www.the-edge.io/restapi/v4/suppliers/{id}/"
    default_supplier_id: str = ""
    default_currency_code: str = "AUD"
    default_comment: str = "order_submitted_manually"
    default_tax_rate_percent: str = "10"  # percent (string)
    default_days_to_promise: int = 7
    default_state: str = "NSW"
    default_country: str = "AU"
    default_test_flag: bool = False
    currency_symbol: str = "$"
    hide_supplier_fields: bool = False        # Supplier URL + Supplier ID
    hide_order_date: bool = False
    hide_currency: bool = False
    hide_supplier_sku: bool = False           # both Item 1 and Item 2 supplier SKU rows
    hide_tax_rate: bool = False               # both Item 1 and Item 2 tax rate rows
    hide_promised_date: bool = False          # both Item 1 and Item 2 promised date rows
    @property
    def orders_endpoint_url(self) -> str:
        return self.orders_base_url.rstrip("/") + "/" + self.orders_endpoint_path.lstrip("/")
@dataclass
class SingleOrderForm:
    supplier_url: str = ""
    supplier_id: str = ""
    order_reference: str = ""
    order_date_iso: str = ""
    currency_code: str = "AUD"
    comment: str = "order_submitted_manually"
    test_flag: bool = False
    supplier_sku_reference_1: str = "order_submitted_manually"
    retailer_sku_reference_1: str = ""
    line_reference_1: str = ""     # SAP ID
    name_1: str = ""               # Product Name
    description_1: str = ""
    quantity_1: str = "1"
    unit_cost_price_1: str = "0.00"
    tax_rate_percent_1: str = "10"
    promised_date_iso_1: str = ""
    subtotal_1: str = "0.00"
    tax_1: str = "0.00"
    total_1: str = "0.00"
    use_item_2: bool = False
    supplier_sku_reference_2: str = "order_submitted_manually"
    retailer_sku_reference_2: str = ""
    line_reference_2: str = ""
    name_2: str = ""
    description_2: str = ""
    quantity_2: str = "1"
    unit_cost_price_2: str = "0.00"
    tax_rate_percent_2: str = "10"
    promised_date_iso_2: str = ""
    subtotal_2: str = "0.00"
    tax_2: str = "0.00"
    total_2: str = "0.00"
    full_name: str = ""
    line_1: str = ""
    city: str = ""
    state: str = "NSW"
    postal_code: str = ""
    phone: str = ""
    country: str = "AU"
def d(value: str | float | int) -> Decimal:
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
def fmt_money(value: Decimal, symbol: str = "$") -> str:
    return f"{symbol}{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"
def now_iso_ms() -> str:
    dt = datetime.now()
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000")
def promised_iso_ms(days: int) -> str:
    dt = datetime.now() + timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000")
def compute_item_totals(quantity: str, unit_cost_price: str, tax_rate_percent: str) -> tuple[str, str, str]:
    q = int(quantity or "0")
    unit = d(unit_cost_price or "0")
    subtotal = (unit * Decimal(q)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    try:
        tax_pct = Decimal(str(tax_rate_percent or "0"))
    except Exception:
        tax_pct = Decimal("0")
    tax = (subtotal * (tax_pct / Decimal("100"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = (subtotal + tax).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return (f"{subtotal:.2f}", f"{tax:.2f}", f"{total:.2f}")
class HttpClient:
    def __init__(self, cfg: ApiConfig):
        self.cfg = cfg
    def _build_headers(self) -> Dict[str, str]:
        if self.cfg.auth_header_override.strip():
            return {
                "Authorization": self.cfg.auth_header_override.strip(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        token = base64.b64encode(f"{self.cfg.username}:{self.cfg.password}".encode("utf-8")).decode("ascii")
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    def post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        headers = self._build_headers()
        body = json.dumps(payload).encode("utf-8")
        if USE_REQUESTS:
            try:
                r = requests.post(
                    url,
                    headers=headers,
                    data=body,
                    timeout=self.cfg.timeout_sec,
                    verify=self.cfg.verify_ssl,
                )
                return {"status_code": r.status_code, "body_text": r.text, "duration": time.time() - start}
            except Exception as ex:
                raise RuntimeError(f"Network error: {ex}") from ex
        else:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            context = None
            if not self.cfg.verify_ssl:
                context = ssl._create_unverified_context()
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.timeout_sec, context=context) as resp:
                    return {
                        "status_code": resp.getcode(),
                        "body_text": resp.read().decode("utf-8", errors="replace"),
                        "duration": time.time() - start,
                    }
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
                return {"status_code": e.code, "body_text": text, "duration": time.time() - start}
            except urllib.error.URLError as e:
                raise RuntimeError(f"Network error: {e.reason}") from e
    def get_json(self, url: str) -> Dict[str, Any]:
        start = time.time()
        headers = self._build_headers()
        if USE_REQUESTS:
            try:
                r = requests.get(url, headers=headers, timeout=self.cfg.timeout_sec, verify=self.cfg.verify_ssl)
                return {"status_code": r.status_code, "body_text": r.text, "duration": time.time() - start}
            except Exception as ex:
                raise RuntimeError(f"Network error: {ex}") from ex
        else:
            req = urllib.request.Request(url, headers=headers, method="GET")
            context = None
            if not self.cfg.verify_ssl:
                context = ssl._create_unverified_context()
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.timeout_sec, context=context) as resp:
                    return {
                        "status_code": resp.getcode(),
                        "body_text": resp.read().decode("utf-8", errors="replace"),
                        "duration": time.time() - start,
                    }
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
                return {"status_code": e.code, "body_text": text, "duration": time.time() - start}
            except urllib.error.URLError as e:
                raise RuntimeError(f"Network error: {e.reason}") from e
class OrderClientApp(TKBase):
    def __init__(self):
        if BOOTSTRAP_AVAILABLE:
            super().__init__(title="Order Create Client", themename="darkly")
        else:
            super().__init__()
            self.title("Order Create Client")
        self.geometry("980x1100")
        self.minsize(880, 900)
        self.cfg = self._load_config()
        self.form = SingleOrderForm(
            supplier_id=self.cfg.default_supplier_id,
            order_date_iso=now_iso_ms(),
            currency_code=self.cfg.default_currency_code,
            comment=self.cfg.default_comment,
            tax_rate_percent_1=self.cfg.default_tax_rate_percent,
            promised_date_iso_1=promised_iso_ms(self.cfg.default_days_to_promise),
            state=self.cfg.default_state,
            country=self.cfg.default_country,
            test_flag=self.cfg.default_test_flag,
        )
        self.form.tax_rate_percent_2 = self.cfg.default_tax_rate_percent
        self.form.promised_date_iso_2 = promised_iso_ms(self.cfg.default_days_to_promise)
        self.suppliers_cache: List[Dict[str, Any]] = []
        self.suppliers_last_updated: Optional[str] = None
        self._load_supplier_cache()
        self._build_styles()
        self._build_ui()
        self._apply_visibility_settings()   # apply hidden rows
        self._refresh_preview()
    def _build_styles(self):
        if not BOOTSTRAP_AVAILABLE:
            style = ttkmod.Style()
            style.theme_use("clam")
            style.configure("TLabel", padding=2)
            style.configure("TEntry", padding=4)
            style.configure("TButton", padding=6)
    def _build_ui(self):
        nb = ttkmod.Notebook(self)
        nb.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.tab_order_items = ttkmod.Frame(nb)   # 1
        self.tab_customer = ttkmod.Frame(nb)      # 2
        self.tab_preview = ttkmod.Frame(nb)       # 3
        self.tab_response = ttkmod.Frame(nb)      # 4
        self.tab_supplier_picker = ttkmod.Frame(nb)  # 5
        self.tab_settings = ttkmod.Frame(nb)      # 6
        nb.add(self.tab_order_items, text="1) Order & Item(s)")
        nb.add(self.tab_customer, text="2) Customer")
        nb.add(self.tab_preview, text="3) Preview")
        nb.add(self.tab_response, text="4) Response")
        nb.add(self.tab_supplier_picker, text="Supplier Picker")
        nb.add(self.tab_settings, text="Settings")
        self._build_order_items_tab(self.tab_order_items)
        self._build_customer_tab(self.tab_customer)
        self._build_preview_tab(self.tab_preview)
        self._build_response_tab(self.tab_response)
        self._build_supplier_picker_tab(self.tab_supplier_picker)
        self._build_settings_tab(self.tab_settings)
        self.status_var = self._mk_str("")
        ttkmod.Label(self, textvariable=self.status_var, anchor="w").grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self._set_status("Ready.")
        self.bind("<Control-Return>", lambda e: self._submit_order())
        self.bind("<F5>", lambda e: self._refresh_preview())
        self.bind("<Control-s>", lambda e: self._save_config())
    def _make_scrollable(self, parent) -> ttkmod.Frame:
        """
        Create a scrollable vertical frame inside 'parent'.
        Returns the inner content frame you can grid children into.
        """
        canvas = ttkmod.Canvas(parent, highlightthickness=0)
        vs = ttkmod.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vs.set)
        canvas.grid(row=1, column=0, sticky="nsew")
        vs.grid(row=1, column=1, sticky="ns")
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        inner = ttkmod.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        def _on_configure(_evt=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(inner_id, width=canvas.winfo_width())  # stretch to full width
        inner.bind("<Configure>", _on_configure)
        canvas.bind("<Configure>", _on_configure)
        def _on_mousewheel(e):
            delta = -1 if e.delta > 0 else 1
            canvas.yview_scroll(delta, "units")
        try:
            inner.bind_all("<MouseWheel>", _on_mousewheel)
        except Exception:
            pass
        return inner
    def _build_order_items_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        tools = ttkmod.Frame(parent)
        tools.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))
        for c in range(6): tools.columnconfigure(c, weight=0)
        tools.columnconfigure(6, weight=1)
        ttkmod.Label(tools, text="Supplier filter").grid(row=0, column=0, sticky="w")
        self.var_main_filter_field = self._mk_str("name")
        ttkmod.Combobox(tools, textvariable=self.var_main_filter_field,
                        values=["name", "id", "account_id"], state="readonly", width=12).grid(row=0, column=1, sticky="w", padx=(6, 8))
        self.var_main_filter_text = self._mk_str("")
        ttkmod.Entry(tools, textvariable=self.var_main_filter_text).grid(row=0, column=2, sticky="ew")
        tools.columnconfigure(2, weight=1)
        ttkmod.Button(tools, text="Pick…", command=self._main_filter_pick_dialog).grid(row=0, column=3, padx=(6, 12))
        ttkmod.Button(tools, text="Order date: Now", command=lambda: self.var_order_date.set(now_iso_ms())).grid(row=0, column=4)
        ttkmod.Button(tools, text="Promised +7d (Item 1)", command=lambda: self.var_promised_date_1.set(promised_iso_ms(self.cfg.default_days_to_promise))).grid(row=0, column=5)
        ttkmod.Button(tools, text="Promised +7d (Item 2)", command=lambda: self.var_promised_date_2.set(promised_iso_ms(self.cfg.default_days_to_promise))).grid(row=0, column=6, sticky="e")
        container = ttkmod.Frame(parent)
        container.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=4, pady=(0, 8))
        inner = self._make_scrollable(container)  # inner is where we grid sections
        gh = ttkmod.Labelframe(inner, text="Supplier & Order Header", padding=10)
        gh.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 8))
        for r in range(10): gh.rowconfigure(r, weight=0)
        gh.columnconfigure(0, weight=1); gh.columnconfigure(1, weight=1)
        self.lbl_supplier_id = self._label(gh, "supplier_id", "Supplier ID", 0, DANGER)
        self.var_supplier_id = self._mk_str(self.form.supplier_id)
        self.ent_supplier_id = self._entry(gh, self.var_supplier_id, 0, 1)
        self.lbl_supplier_url = self._label(gh, "supplier_url", "Supplier URL (auto from ID)", 1, SUCCESS)
        self.var_supplier_url = self._mk_str(self.form.supplier_url)
        self.ent_supplier_url = self._entry(gh, self.var_supplier_url, 1, 1)
        self.lbl_order_reference = self._label(gh, "order_reference", "Order reference", 2, DANGER)
        self.var_order_reference = self._mk_str(self.form.order_reference)
        self.ent_order_reference = self._entry(gh, self.var_order_reference, 2, 1)
        self.lbl_order_date = self._label(gh, "order_date_iso", "Order date (ISO)", 3, SUCCESS)
        self.var_order_date = self._mk_str(self.form.order_date_iso)
        self.ent_order_date = self._entry(gh, self.var_order_date, 3, 1)
        self.lbl_currency_code = self._label(gh, "currency_code", "Currency", 4, SUCCESS)
        self.var_currency_code = self._mk_str(self.form.currency_code)
        self.ent_currency_code = self._entry(gh, self.var_currency_code, 4, 1)
        self.lbl_comment = self._label(gh, "comment", "Comment", 5, SUCCESS)
        self.var_comment = self._mk_str(self.form.comment)
        self.ent_comment = self._entry(gh, self.var_comment, 5, 1)
        self.lbl_test_flag = self._label(gh, "test_flag", "Test flag", 6, SUCCESS)
        self.var_test_flag = self._mk_bool(self.form.test_flag)
        self.chk_test_flag = ttkmod.Checkbutton(gh, text="Enable", variable=self.var_test_flag)
        self.chk_test_flag.grid(row=6, column=1, sticky="w")
        g1 = ttkmod.Labelframe(inner, text="Item #1", padding=10)
        g1.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 8))
        for r in range(14): g1.rowconfigure(r, weight=0)
        g1.columnconfigure(0, weight=1); g1.columnconfigure(1, weight=1)
        self.lbl_retailer_sku_1 = self._label(g1, "retailer_sku_reference_1", "Retailer SKU", 0, DANGER)
        self.var_retailer_sku_1 = self._mk_str(self.form.retailer_sku_reference_1)
        self.ent_retailer_sku_1 = self._entry(g1, self.var_retailer_sku_1, 0, 1)
        self.lbl_supplier_sku_1 = self._label(g1, "supplier_sku_reference_1", "Supplier SKU", 1, DANGER)
        self.var_supplier_sku_1 = self._mk_str(self.form.supplier_sku_reference_1)
        self.ent_supplier_sku_1 = self._entry(g1, self.var_supplier_sku_1, 1, 1)
        self.lbl_line_ref_1 = self._label(g1, "line_reference_1", "SAP ID (line reference)", 2, DANGER)
        self.var_line_ref_1 = self._mk_str(self.form.line_reference_1)
        self.ent_line_ref_1 = self._entry(g1, self.var_line_ref_1, 2, 1)
        self.lbl_name_1 = self._label(g1, "name_1", "Product Name", 3, DANGER)
        self.var_name_1 = self._mk_str(self.form.name_1)
        self.ent_name_1 = self._entry(g1, self.var_name_1, 3, 1)
        self.lbl_desc_1 = self._label(g1, "description_1", "Description", 4, SUCCESS)
        self.var_desc_1 = self._mk_str(self.form.description_1)
        self.ent_desc_1 = self._entry(g1, self.var_desc_1, 4, 1)
        self.lbl_qty_1 = self._label(g1, "quantity_1", "Qty", 5, DANGER)
        self.var_qty_1 = self._mk_str(self.form.quantity_1)
        self.sp_qty_1 = self._spin(g1, self.var_qty_1, 5, 1, from_=1, to=1_000_000, width=10)
        self.lbl_unit_cost_1 = self._label(g1, "unit_cost_price_1", "Unit cost ex", 6, DANGER)
        self.var_unit_cost_1 = self._mk_str(self.form.unit_cost_price_1)
        self.ent_unit_cost_1 = self._entry(g1, self.var_unit_cost_1, 6, 1)
        self.ent_unit_cost_1.bind("<KeyRelease>", lambda e: self._refresh_preview())
        self.lbl_tax_rate_1 = self._label(g1, "tax_rate_percent_1", "Tax rate (%)", 7, SUCCESS)
        self.var_tax_pct_1 = self._mk_str(self.form.tax_rate_percent_1)
        self.ent_tax_pct_1 = self._entry(g1, self.var_tax_pct_1, 7, 1)
        self.ent_tax_pct_1.bind("<KeyRelease>", lambda e: self._refresh_preview())
        self.lbl_promised_1 = self._label(g1, "promised_date_iso_1", "Promised date (ISO)", 8, SUCCESS)
        self.var_promised_date_1 = self._mk_str(self.form.promised_date_iso_1)
        self.ent_promised_date_1 = self._entry(g1, self.var_promised_date_1, 8, 1)
        se1 = ttkmod.Separator(g1); se1.grid(row=9, column=0, columnspan=3, sticky="ew", pady=6)
        self.lbl_subtotal_1 = self._label(g1, "subtotal_1", "Subtotal", 10, SUCCESS)
        self.subtotal_var_1 = self._mk_str(f"{self.cfg.currency_symbol}0.00")
        self.ent_subtotal_1 = self._readonly_entry(g1, self.subtotal_var_1, 10, 1)
        self.lbl_tax_1 = self._label(g1, "tax_1", "Tax", 11, SUCCESS)
        self.tax_var_1 = self._mk_str(f"{self.cfg.currency_symbol}0.00")
        self.ent_tax_1 = self._readonly_entry(g1, self.tax_var_1, 11, 1)
        self.lbl_total_1 = self._label(g1, "total_1", "Total", 12, SUCCESS)
        self.total_var_1 = self._mk_str(f"{self.cfg.currency_symbol}0.00")
        self.ent_total_1 = self._readonly_entry(g1, self.total_var_1, 12, 1)
        toggle_frame = ttkmod.Frame(inner)
        toggle_frame.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 4))
        self.var_use_item_2 = self._mk_bool(False)
        ttkmod.Checkbutton(toggle_frame, text="Add second item",
                           variable=self.var_use_item_2, command=self._toggle_item2).grid(row=0, column=0, sticky="w")
        self.g2 = ttkmod.Labelframe(inner, text="Item #2 (optional)", padding=10)
        for r in range(14): self.g2.rowconfigure(r, weight=0)
        self.g2.columnconfigure(0, weight=1); self.g2.columnconfigure(1, weight=1)
        self.lbl_retailer_sku_2 = self._label(self.g2, "retailer_sku_reference_2", "Retailer SKU", 0, DANGER)
        self.var_retailer_sku_2 = self._mk_str(self.form.retailer_sku_reference_2)
        self.ent_retailer_sku_2 = self._entry(self.g2, self.var_retailer_sku_2, 0, 1)
        self.lbl_supplier_sku_2 = self._label(self.g2, "supplier_sku_reference_2", "Supplier SKU", 1, DANGER)
        self.var_supplier_sku_2 = self._mk_str(self.form.supplier_sku_reference_2)
        self.ent_supplier_sku_2 = self._entry(self.g2, self.var_supplier_sku_2, 1, 1)
        self.lbl_line_ref_2 = self._label(self.g2, "line_reference_2", "SAP ID (line reference)", 2, DANGER)
        self.var_line_ref_2 = self._mk_str(self.form.line_reference_2)
        self.ent_line_ref_2 = self._entry(self.g2, self.var_line_ref_2, 2, 1)
        self.lbl_name_2 = self._label(self.g2, "name_2", "Product Name", 3, DANGER)
        self.var_name_2 = self._mk_str(self.form.name_2)
        self.ent_name_2 = self._entry(self.g2, self.var_name_2, 3, 1)
        self.lbl_desc_2 = self._label(self.g2, "description_2", "Description", 4, SUCCESS)
        self.var_desc_2 = self._mk_str(self.form.description_2)
        self.ent_desc_2 = self._entry(self.g2, self.var_desc_2, 4, 1)
        self.lbl_qty_2 = self._label(self.g2, "quantity_2", "Qty", 5, DANGER)
        self.var_qty_2 = self._mk_str(self.form.quantity_2)
        self.sp_qty_2 = self._spin(self.g2, self.var_qty_2, 5, 1, from_=1, to=1_000_000, width=10)
        self.lbl_unit_cost_2 = self._label(self.g2, "unit_cost_price_2", "Unit cost ex", 6, DANGER)
        self.var_unit_cost_2 = self._mk_str(self.form.unit_cost_price_2)
        self.ent_unit_cost_2 = self._entry(self.g2, self.var_unit_cost_2, 6, 1)
        self.ent_unit_cost_2.bind("<KeyRelease>", lambda e: self._refresh_preview())
        self.lbl_tax_rate_2 = self._label(self.g2, "tax_rate_percent_2", "Tax rate (%)", 7, SUCCESS)
        self.var_tax_pct_2 = self._mk_str(self.form.tax_rate_percent_2)
        self.ent_tax_pct_2 = self._entry(self.g2, self.var_tax_pct_2, 7, 1)
        self.ent_tax_pct_2.bind("<KeyRelease>", lambda e: self._refresh_preview())
        self.lbl_promised_2 = self._label(self.g2, "promised_date_iso_2", "Promised date (ISO)", 8, SUCCESS)
        self.var_promised_date_2 = self._mk_str(self.form.promised_date_iso_2)
        self.ent_promised_date_2 = self._entry(self.g2, self.var_promised_date_2, 8, 1)
        se2 = ttkmod.Separator(self.g2); se2.grid(row=9, column=0, columnspan=3, sticky="ew", pady=6)
        self.lbl_subtotal_2 = self._label(self.g2, "subtotal_2", "Subtotal", 10, SUCCESS)
        self.subtotal_var_2 = self._mk_str(f"{self.cfg.currency_symbol}0.00")
        self.ent_subtotal_2 = self._readonly_entry(self.g2, self.subtotal_var_2, 10, 1)
        self.lbl_tax_2 = self._label(self.g2, "tax_2", "Tax", 11, SUCCESS)
        self.tax_var_2 = self._mk_str(f"{self.cfg.currency_symbol}0.00")
        self.ent_tax_2 = self._readonly_entry(self.g2, self.tax_var_2, 11, 1)
        self.lbl_total_2 = self._label(self.g2, "total_2", "Total", 12, SUCCESS)
        self.total_var_2 = self._mk_str(f"{self.cfg.currency_symbol}0.00")
        self.ent_total_2 = self._readonly_entry(self.g2, self.total_var_2, 12, 1)
        action = ttkmod.Frame(inner)
        action.grid(row=4, column=0, sticky="ew", padx=4, pady=(4, 8))
        for c in range(3): action.columnconfigure(c, weight=1)
        ttkmod.Button(action, text="Submit Order", command=self._submit_order).grid(row=0, column=0, sticky="ew", padx=(0,6))
        ttkmod.Button(action, text="Reset form", command=self._reset_single_form).grid(row=0, column=1, sticky="ew", padx=(0,6))
        ttkmod.Button(action, text="Refresh preview", command=self._refresh_preview).grid(row=0, column=2, sticky="ew")
    def _build_customer_tab(self, parent):
        lf = ttkmod.Labelframe(parent, text="Shipping Address", padding=10)
        lf.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        lf.columnconfigure(0, weight=1); lf.columnconfigure(1, weight=1)
        for r in range(20): lf.rowconfigure(r, weight=0)
        self.lbl_full_name = self._label(lf, "full_name", "Full name", 0, DANGER)
        self.var_full_name = self._mk_str(self.form.full_name)
        self.ent_full_name = self._entry(lf, self.var_full_name, 0, 1)
        self.lbl_line_1 = self._label(lf, "line_1", "Address line 1", 1, WARNING)
        self.var_line_1 = self._mk_str(self.form.line_1)
        self.ent_line_1 = self._entry(lf, self.var_line_1, 1, 1)
        self.lbl_city = self._label(lf, "city", "City", 2, WARNING)
        self.var_city = self._mk_str(self.form.city)
        self.ent_city = self._entry(lf, self.var_city, 2, 1)
        self.lbl_state = self._label(lf, "state", "State", 3, DANGER)
        self.var_state = self._mk_str(self.form.state)
        self.cb_state = self._combo(lf, self.var_state, ["NSW","VIC","QLD","SA","WA","TAS","ACT","NT"], 3, 1)
        self.lbl_postal = self._label(lf, "postal_code", "Post Code", 4, WARNING)
        self.var_postal = self._mk_str(self.form.postal_code)
        self.ent_postal = self._entry(lf, self.var_postal, 4, 1)
        self.lbl_phone = self._label(lf, "phone", "Phone", 5, DANGER)
        self.var_phone = self._mk_str(self.form.phone)
        self.ent_phone = self._entry(lf, self.var_phone, 5, 1)
        self.lbl_country = self._label(lf, "country", "Country", 6, SUCCESS)
        self.var_country = self._mk_str(self.form.country)
        self.ent_country = self._entry(lf, self.var_country, 6, 1)
    def _build_preview_tab(self, parent):
        parent.columnconfigure(0, weight=1); parent.rowconfigure(1, weight=1)
        ttkmod.Label(parent, text="Payload preview (read-only)").grid(row=0, column=0, sticky="w")
        self.txt_preview = self._mk_text(parent, height=36)
        self.txt_preview.grid(row=1, column=0, sticky="nsew", pady=(2,8))
        bar = ttkmod.Frame(parent); bar.grid(row=2, column=0, sticky="ew")
        ttkmod.Button(bar, text="Copy payload", command=self._copy_payload).grid(row=0, column=0, padx=(0,6))
        ttkmod.Button(bar, text="Save payload…", command=self._save_payload).grid(row=0, column=1, padx=(0,6))
        ttkmod.Button(bar, text="Refresh preview", command=self._refresh_preview).grid(row=0, column=2)
    def _build_response_tab(self, parent):
        parent.columnconfigure(0, weight=1); parent.rowconfigure(1, weight=1)
        ttkmod.Label(parent, text="API response").grid(row=0, column=0, sticky="w")
        self.txt_response = self._mk_text(parent, height=36)
        self.txt_response.grid(row=1, column=0, sticky="nsew", pady=(2,8))
        bar = ttkmod.Frame(parent); bar.grid(row=2, column=0, sticky="ew")
        ttkmod.Button(bar, text="Copy response", command=self._copy_response).grid(row=0, column=0, padx=(0,6))
        ttkmod.Button(bar, text="Clear response", command=lambda: self._set_text(self.txt_response, "")).grid(row=0, column=1)
    def _build_supplier_picker_tab(self, parent):
        parent.columnconfigure(0, weight=1); parent.rowconfigure(2, weight=1)
        bar = ttkmod.Frame(parent); bar.grid(row=0, column=0, sticky="ew", padx=6, pady=(6,4))
        ttkmod.Button(bar, text="Refresh from API", command=self._refresh_suppliers).grid(row=0, column=0, padx=(0,8))
        ttkmod.Button(bar, text="Load cache", command=self._load_supplier_cache).grid(row=0, column=1, padx=(0,8))
        ttkmod.Button(bar, text="Use selected", command=self._use_selected_supplier).grid(row=0, column=2, padx=(0,8))
        filt = ttkmod.Frame(parent); filt.grid(row=1, column=0, sticky="ew", padx=6, pady=(0,6))
        ttkmod.Label(filt, text="Filter by").grid(row=0, column=0, sticky="w")
        self.var_filter_field = self._mk_str("name")
        ttkmod.Combobox(filt, textvariable=self.var_filter_field, values=["name","id","account_id"], state="readonly", width=12).grid(row=0, column=1, sticky="w", padx=(6,8))
        self.var_filter_text = self._mk_str("")
        ent = ttkmod.Entry(filt, textvariable=self.var_filter_text)
        ent.grid(row=0, column=2, sticky="ew")
        ent.bind("<KeyRelease>", lambda e: self._apply_supplier_filter())
        filt.columnconfigure(2, weight=1)
        columns = ("id","name","account_id")
        self.sup_tree = ttkmod.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        for c in columns:
            self.sup_tree.heading(c, text=c)
            self.sup_tree.column(c, width=220 if c == "name" else 140, stretch=True)
        self.sup_tree.grid(row=2, column=0, sticky="nsew", padx=6)
        yscroll = ttkmod.Scrollbar(parent, orient="vertical", command=self.sup_tree.yview)
        self.sup_tree.configure(yscrollcommand=yscroll.set)
        yscroll.grid(row=2, column=1, sticky="ns")
        stats = ttkmod.Frame(parent); stats.grid(row=3, column=0, sticky="ew", padx=6, pady=(6,8))
        self.sup_stats_var = self._mk_str("Suppliers: 0  |  Last updated: -")
        ttkmod.Label(stats, textvariable=self.sup_stats_var, anchor="w").grid(row=0, column=0, sticky="w")
        self._render_supplier_table(self.suppliers_cache)
    def _build_settings_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        sf = ttkmod.Labelframe(parent, text="API & Presets", padding=10)
        sf.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        for r in range(24): sf.rowconfigure(r, weight=0)
        sf.columnconfigure(0, weight=1); sf.columnconfigure(1, weight=1)
        ttkmod.Label(sf, text="Orders Base URL").grid(row=0, column=0, sticky="w")
        self.var_orders_base = self._mk_str(self.cfg.orders_base_url); ttkmod.Entry(sf, textvariable=self.var_orders_base).grid(row=0, column=1, sticky="ew")
        ttkmod.Label(sf, text="Orders Endpoint Path").grid(row=1, column=0, sticky="w")
        self.var_orders_path = self._mk_str(self.cfg.orders_endpoint_path); ttkmod.Entry(sf, textvariable=self.var_orders_path).grid(row=1, column=1, sticky="ew")
        ttkmod.Label(sf, text="Suppliers URL").grid(row=2, column=0, sticky="w")
        self.var_suppliers_url = self._mk_str(self.cfg.suppliers_url); ttkmod.Entry(sf, textvariable=self.var_suppliers_url).grid(row=2, column=1, sticky="ew")
        ttkmod.Label(sf, text="Username").grid(row=3, column=0, sticky="w")
        self.var_username = self._mk_str(self.cfg.username); ttkmod.Entry(sf, textvariable=self.var_username).grid(row=3, column=1, sticky="ew")
        ttkmod.Label(sf, text="Password").grid(row=4, column=0, sticky="w")
        self.var_password = self._mk_str(self.cfg.password if self.cfg.save_password else "")
        ttkmod.Entry(sf, textvariable=self.var_password, show="•").grid(row=4, column=1, sticky="ew")
        self.var_save_pwd = self._mk_bool(self.cfg.save_password)
        ttkmod.Checkbutton(sf, text="Save password (plain text local config)", variable=self.var_save_pwd).grid(row=5, column=1, sticky="w")
        ttkmod.Label(sf, text="Authorization header override").grid(row=6, column=0, sticky="w")
        self.var_auth_override = self._mk_str(self.cfg.auth_header_override); ttkmod.Entry(sf, textvariable=self.var_auth_override).grid(row=6, column=1, sticky="ew")
        ttkmod.Label(sf, text="Timeout (sec)").grid(row=7, column=0, sticky="w")
        self.var_timeout = self._mk_int(self.cfg.timeout_sec); ttkmod.Spinbox(sf, from_=5, to=120, textvariable=self.var_timeout, width=8).grid(row=7, column=1, sticky="w")
        self.var_verify = self._mk_bool(self.cfg.verify_ssl)
        ttkmod.Checkbutton(sf, text="Verify SSL certificate", variable=self.var_verify).grid(row=8, column=1, sticky="w")
        ttkmod.Label(sf, text="Supplier URL template").grid(row=9, column=0, sticky="w")
        self.var_sup_tpl = self._mk_str(self.cfg.supplier_url_template); ttkmod.Entry(sf, textvariable=self.var_sup_tpl).grid(row=9, column=1, sticky="ew")
        ttkmod.Label(sf, text="Default Supplier ID").grid(row=10, column=0, sticky="w")
        self.var_default_supplier = self._mk_str(self.cfg.default_supplier_id); ttkmod.Entry(sf, textvariable=self.var_default_supplier).grid(row=10, column=1, sticky="ew")
        ttkmod.Label(sf, text="Currency code").grid(row=11, column=0, sticky="w")
        self.var_currency = self._mk_str(self.cfg.default_currency_code); ttkmod.Entry(sf, textvariable=self.var_currency).grid(row=11, column=1, sticky="ew")
        ttkmod.Label(sf, text="Default comment").grid(row=12, column=0, sticky="w")
        self.var_comment_default = self._mk_str(self.cfg.default_comment); ttkmod.Entry(sf, textvariable=self.var_comment_default).grid(row=12, column=1, sticky="ew")
        ttkmod.Label(sf, text="Tax rate (%)").grid(row=13, column=0, sticky="w")
        self.var_tax_default = self._mk_str(self.cfg.default_tax_rate_percent); ttkmod.Entry(sf, textvariable=self.var_tax_default).grid(row=13, column=1, sticky="ew")
        ttkmod.Label(sf, text="Days to promised").grid(row=14, column=0, sticky="w")
        self.var_days_promise = self._mk_int(self.cfg.default_days_to_promise)
        ttkmod.Spinbox(sf, from_=0, to=60, textvariable=self.var_days_promise, width=6).grid(row=14, column=1, sticky="w")
        ttkmod.Label(sf, text="Default state").grid(row=15, column=0, sticky="w")
        self.var_state_default = self._mk_str(self.cfg.default_state); ttkmod.Entry(sf, textvariable=self.var_state_default).grid(row=15, column=1, sticky="ew")
        ttkmod.Label(sf, text="Default country").grid(row=16, column=0, sticky="w")
        self.var_country_default = self._mk_str(self.cfg.default_country); ttkmod.Entry(sf, textvariable=self.var_country_default).grid(row=16, column=1, sticky="ew")
        hide_box = ttkmod.Labelframe(sf, text="Hide rows in Order tab", padding=8)
        hide_box.grid(row=17, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.var_hide_supplier_fields = self._mk_bool(self.cfg.hide_supplier_fields)
        self.var_hide_order_date = self._mk_bool(self.cfg.hide_order_date)
        self.var_hide_currency = self._mk_bool(self.cfg.hide_currency)
        self.var_hide_supplier_sku = self._mk_bool(self.cfg.hide_supplier_sku)
        self.var_hide_tax_rate = self._mk_bool(self.cfg.hide_tax_rate)
        self.var_hide_promised_date = self._mk_bool(self.cfg.hide_promised_date)
        ttkmod.Checkbutton(hide_box, text="Supplier URL and Supplier ID", variable=self.var_hide_supplier_fields).grid(row=0, column=0, sticky="w")
        ttkmod.Checkbutton(hide_box, text="Order date", variable=self.var_hide_order_date).grid(row=0, column=1, sticky="w")
        ttkmod.Checkbutton(hide_box, text="Currency", variable=self.var_hide_currency).grid(row=1, column=0, sticky="w")
        ttkmod.Checkbutton(hide_box, text="Supplier SKU (Item 1 & 2)", variable=self.var_hide_supplier_sku).grid(row=1, column=1, sticky="w")
        ttkmod.Checkbutton(hide_box, text="Tax rate (Item 1 & 2)", variable=self.var_hide_tax_rate).grid(row=2, column=0, sticky="w")
        ttkmod.Checkbutton(hide_box, text="Promised date (Item 1 & 2)", variable=self.var_hide_promised_date).grid(row=2, column=1, sticky="w")
        btns = ttkmod.Frame(sf); btns.grid(row=18, column=0, columnspan=2, sticky="ew", pady=(10,0))
        for c in range(3): btns.columnconfigure(c, weight=1)
        ttkmod.Button(btns, text="Save settings", command=self._save_config).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttkmod.Button(btns, text="Apply visibility now", command=self._apply_visibility_settings).grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttkmod.Button(btns, text="Refresh supplier list", command=self._refresh_suppliers).grid(row=0, column=2, sticky="ew")
    def _collect_single(self) -> SingleOrderForm:
        f = SingleOrderForm(
            supplier_id=self.var_supplier_id.get().strip(),
            supplier_url=self.var_supplier_url.get().strip(),
            order_reference=self.var_order_reference.get().strip(),
            order_date_iso=self.var_order_date.get().strip(),
            currency_code=self.var_currency_code.get().strip() or "AUD",
            comment=self.var_comment.get().strip() or self.cfg.default_comment,
            test_flag=bool(self.var_test_flag.get()),
            retailer_sku_reference_1=self.var_retailer_sku_1.get().strip(),
            supplier_sku_reference_1=self.var_supplier_sku_1.get().strip(),
            line_reference_1=self.var_line_ref_1.get().strip(),
            name_1=self.var_name_1.get().strip(),
            description_1=self.var_desc_1.get().strip(),
            quantity_1=self.var_qty_1.get().strip(),
            unit_cost_price_1=self.var_unit_cost_1.get().strip(),
            tax_rate_percent_1=self.var_tax_pct_1.get().strip() or self.cfg.default_tax_rate_percent,
            promised_date_iso_1=self.var_promised_date_1.get().strip(),
            use_item_2=bool(self.var_use_item_2.get()),
            retailer_sku_reference_2=self.var_retailer_sku_2.get().strip(),
            supplier_sku_reference_2=self.var_supplier_sku_2.get().strip(),
            line_reference_2=self.var_line_ref_2.get().strip(),
            name_2=self.var_name_2.get().strip(),
            description_2=self.var_desc_2.get().strip(),
            quantity_2=self.var_qty_2.get().strip(),
            unit_cost_price_2=self.var_unit_cost_2.get().strip(),
            tax_rate_percent_2=self.var_tax_pct_2.get().strip() or self.cfg.default_tax_rate_percent,
            promised_date_iso_2=self.var_promised_date_2.get().strip(),
            full_name=self.var_full_name.get().strip(),
            line_1=self.var_line_1.get().strip(),
            city=self.var_city.get().strip(),
            state=self.var_state.get().strip(),
            postal_code=self.var_postal.get().strip(),
            phone=self.var_phone.get().strip(),
            country=self.var_country.get().strip() or "AU",
        )
        if not f.supplier_url and f.supplier_id:
            try:
                f.supplier_url = self.var_sup_tpl.get().strip().format(id=f.supplier_id)
            except Exception:
                pass
        f.subtotal_1, f.tax_1, f.total_1 = compute_item_totals(f.quantity_1, f.unit_cost_price_1, f.tax_rate_percent_1)
        if f.use_item_2:
            f.subtotal_2, f.tax_2, f.total_2 = compute_item_totals(f.quantity_2, f.unit_cost_price_2, f.tax_rate_percent_2)
        return f
    def _validate_single(self, f: SingleOrderForm) -> List[str]:
        errs = []
        req = {
            "Supplier ID or Supplier URL": f.supplier_id or f.supplier_url,
            "Order reference": f.order_reference,
            "Full name": f.full_name,
            "Phone": f.phone,
            "State": f.state,
        }
        req.update({
            "Retailer SKU (Item 1)": f.retailer_sku_reference_1,
            "Supplier SKU (Item 1)": f.supplier_sku_reference_1,
            "SAP ID (Item 1)": f.line_reference_1,
            "Product Name (Item 1)": f.name_1,
            "Qty (Item 1)": f.quantity_1,
            "Unit cost ex (Item 1)": f.unit_cost_price_1,
        })
        for k, v in req.items():
            if not v:
                errs.append(f"{k} is required.")
        try:
            q1 = int(f.quantity_1)
            if q1 <= 0: errs.append("Qty (Item 1) must be a positive integer.")
        except Exception:
            errs.append("Qty (Item 1) must be an integer.")
        try:
            _ = d(f.unit_cost_price_1)
        except Exception:
            errs.append("Unit cost ex (Item 1) must be a valid number.")
        if f.use_item_2:
            if not f.retailer_sku_reference_2 or not f.supplier_sku_reference_2 or not f.line_reference_2 or not f.name_2:
                errs.append("Retailer/Supplier SKU, SAP ID, and Product Name (Item 2) are required when second item is enabled.")
            try:
                q2 = int(f.quantity_2)
                if q2 <= 0: errs.append("Qty (Item 2) must be a positive integer.")
            except Exception:
                errs.append("Qty (Item 2) must be an integer.")
            try:
                _ = d(f.unit_cost_price_2)
            except Exception:
                errs.append("Unit cost ex (Item 2) must be a valid number.")
        if f.postal_code and not f.postal_code.isdigit():
            errs.append("Post Code should be numeric.")
        if f.order_date_iso and "T" not in f.order_date_iso:
            errs.append("Order date should be ISO (YYYY-MM-DDTHH:MM:SS.000).")
        if f.promised_date_iso_1 and "T" not in f.promised_date_iso_1:
            errs.append("Promised date (Item 1) should be ISO (YYYY-MM-DDTHH:MM:SS.000).")
        if f.use_item_2 and f.promised_date_iso_2 and "T" not in f.promised_date_iso_2:
            errs.append("Promised date (Item 2) should be ISO (YYYY-MM-DDTHH:MM:SS.000).")
        return errs
    def _build_payload(self, f: SingleOrderForm) -> Dict[str, Any]:
        items = [{
            "supplier_sku_reference": f.supplier_sku_reference_1 or "order_submitted_manually",
            "retailer_sku_reference": f.retailer_sku_reference_1,
            "line_reference": f.line_reference_1,
            "name": f.name_1,
            "description": f.description_1,
            "quantity": int(f.quantity_1 or "0"),
            "unit_cost_price": f"{d(f.unit_cost_price_1):.2f}",
            "subtotal": f.subtotal_1,
            "tax_rate": f"{Decimal(str(f.tax_rate_percent_1 or '0')):.0f}",
            "tax": f.tax_1,
            "total": f.total_1,
            "promised_date": f.promised_date_iso_1,
        }]
        if f.use_item_2:
            items.append({
                "supplier_sku_reference": f.supplier_sku_reference_2 or "order_submitted_manually",
                "retailer_sku_reference": f.retailer_sku_reference_2,
                "line_reference": f.line_reference_2,
                "name": f.name_2,
                "description": f.description_2,
                "quantity": int(f.quantity_2 or "0"),
                "unit_cost_price": f"{d(f.unit_cost_price_2):.2f}",
                "subtotal": f.subtotal_2,
                "tax_rate": f"{Decimal(str(f.tax_rate_percent_2 or '0')):.0f}",
                "tax": f.tax_2,
                "total": f.total_2,
                "promised_date": f.promised_date_iso_2,
            })
        payload = {
            "supplier": f.supplier_url,
            "order_reference": f.order_reference,
            "order_date": f.order_date_iso,
            "test_flag": bool(f.test_flag),
            "currency_code": f.currency_code,
            "comment": f.comment,
            "items": items,
            "shipping_address": {
                "full_name": f.full_name,
                "line_1": f.line_1,
                "city": f.city,
                "state": f.state,
                "postal_code": f.postal_code,
                "phone": f.phone,
                "country": f.country,
            },
        }
        return payload
    def _refresh_preview(self):
        f = self._collect_single()
        self.subtotal_var_1.set(fmt_money(d(f.subtotal_1), self.cfg.currency_symbol))
        self.tax_var_1.set(fmt_money(d(f.tax_1), self.cfg.currency_symbol))
        self.total_var_1.set(fmt_money(d(f.total_1), self.cfg.currency_symbol))
        if f.use_item_2:
            self.subtotal_var_2.set(fmt_money(d(f.subtotal_2), self.cfg.currency_symbol))
            self.tax_var_2.set(fmt_money(d(f.tax_2), self.cfg.currency_symbol))
            self.total_var_2.set(fmt_money(d(f.total_2), self.cfg.currency_symbol))
        payload = self._build_payload(f)
        self._set_text(self.txt_preview, json.dumps(payload, indent=2))
    def _submit_order(self):
        f = self._collect_single()
        errs = self._validate_single(f)
        if errs:
            messagebox.showerror("Validation errors", "\n".join(errs)); return
        payload = self._build_payload(f)
        client = HttpClient(self._collect_config())
        self._set_status("Submitting order…")
        def worker():
            try:
                res = client.post_json(self._collect_config().orders_endpoint_url, payload)
                msg = self._format_response(res)  # pretty print if JSON
                self.after(0, lambda: self._set_text(self.txt_response, msg))
                self.after(0, lambda: self._set_status(self._status_bar_text(res)))
            except Exception as ex:
                self.after(0, lambda: self._set_text(self.txt_response, f"Error: {ex}"))
                self.after(0, lambda: self._set_status("Submit failed."))
        threading.Thread(target=worker, daemon=True).start()
    def _toggle_item2(self):
        use = bool(self.var_use_item_2.get())
        if use:
            self.g2.grid(row=3, column=0, sticky="ew", padx=4, pady=(0, 8))
        else:
            try: self.g2.grid_forget()
            except Exception: pass
        self._refresh_preview()
    def _reset_single_form(self):
        self.var_supplier_id.set(self.cfg.default_supplier_id or "")
        self.var_supplier_url.set("")
        self.var_order_reference.set("")
        self.var_order_date.set(now_iso_ms())
        self.var_currency_code.set(self.cfg.default_currency_code)
        self.var_comment.set(self.cfg.default_comment)
        self.var_test_flag.set(self.cfg.default_test_flag)
        self.var_retailer_sku_1.set("")
        self.var_supplier_sku_1.set("order_submitted_manually")
        self.var_line_ref_1.set("")
        self.var_name_1.set("")
        self.var_desc_1.set("")
        self.var_qty_1.set("1")
        self.var_unit_cost_1.set("0.00")
        self.var_tax_pct_1.set(self.cfg.default_tax_rate_percent)
        self.var_promised_date_1.set(promised_iso_ms(self.cfg.default_days_to_promise))
        self.var_use_item_2.set(False)
        try: self.g2.grid_forget()
        except Exception: pass
        self.var_retailer_sku_2.set("")
        self.var_supplier_sku_2.set("order_submitted_manually")
        self.var_line_ref_2.set("")
        self.var_name_2.set("")
        self.var_desc_2.set("")
        self.var_qty_2.set("1")
        self.var_unit_cost_2.set("0.00")
        self.var_tax_pct_2.set(self.cfg.default_tax_rate_percent)
        self.var_promised_date_2.set(promised_iso_ms(self.cfg.default_days_to_promise))
        self.var_full_name.set("")
        self.var_line_1.set("")
        self.var_city.set("")
        self.var_state.set(self.cfg.default_state)
        self.var_postal.set("")
        self.var_phone.set("")
        self.var_country.set(self.cfg.default_country)
        self._set_status("Form reset.")
        self._refresh_preview()
    def _format_response(self, res: Dict[str, Any]) -> str:
        code = res.get("status_code", 0)
        duration = res.get("duration", 0.0)
        body = res.get("body_text", "")
        pretty = body
        try:
            obj = json.loads(body)
            pretty = json.dumps(obj, indent=2)
        except Exception:
            pass
        friendly = {
            201: "Created — order accepted",
            400: "Bad Request — payload/values",
            403: "Forbidden — credentials/permissions",
            409: "Conflict — duplicate order_reference or state conflict",
            500: "Server Error — retry later",
        }.get(code, "Response received")
        return f"HTTP {code} — {friendly}\nDuration: {duration:.3f}s\nBody:\n{pretty}"
    def _status_bar_text(self, res: Dict[str, Any]) -> str:
        code = res.get("status_code", 0)
        if code == 201: return "Order created (201)."
        if code in (400,403,409,500): return f"Error {code}."
        return f"HTTP {code}."
    def _set_text(self, widget, text: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="normal")
    def _copy_payload(self):
        text = self.txt_preview.get("1.0", "end").strip()
        self.clipboard_clear(); self.clipboard_append(text)
        self._set_status("Payload copied.")
    def _save_payload(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON","*.json"),("All files","*.*")], title="Save payload")
        if not path: return
        text = self.txt_preview.get("1.0", "end").strip()
        with open(path, "w", encoding="utf-8") as f: f.write(text)
        self._set_status(f"Saved payload to {path}")
    def _copy_response(self):
        text = self.txt_response.get("1.0", "end").strip()
        self.clipboard_clear(); self.clipboard_append(text)
        self._set_status("Response copied.")
    def _render_supplier_table(self, rows: List[Dict[str, Any]]):
        for iid in self.sup_tree.get_children(): self.sup_tree.delete(iid)
        for rec in rows:
            self.sup_tree.insert("", "end", values=[
                rec.get("id",""), rec.get("name",""), rec.get("account_id","N/A")
            ])
        total = len(rows)
        last = self.suppliers_last_updated or "-"
        self.sup_stats_var.set(f"Suppliers: {total}  |  Last updated: {last}")
    def _apply_supplier_filter(self):
        field = self.var_filter_field.get()
        text = self.var_filter_text.get().strip().lower()
        if not text:
            self._render_supplier_table(self.suppliers_cache); return
        filtered = []
        for rec in self.suppliers_cache:
            val = str(rec.get(field, "")).lower()
            if text in val:
                filtered.append(rec)
        self._render_supplier_table(filtered)
    def _main_filter_pick_dialog(self):
        """Filter suppliers (using cached list) from main tab and pick one."""
        field = self.var_main_filter_field.get()
        text = self.var_main_filter_text.get().strip().lower()
        rows = self.suppliers_cache
        if text:
            rows = [r for r in rows if text in str(r.get(field, "")).lower()]
        top = ttkmod.Toplevel(self); top.title("Pick Supplier"); top.geometry("640x420")
        columns = ("id","name","account_id")
        tv = ttkmod.Treeview(top, columns=columns, show="headings", selectmode="browse")
        for c in columns:
            tv.heading(c, text=c); tv.column(c, width=220 if c == "name" else 140, stretch=True)
        tv.grid(row=0, column=0, sticky="nsew")
        ttkmod.Scrollbar(top, orient="vertical", command=tv.yview).grid(row=0, column=1, sticky="ns")
        top.rowconfigure(0, weight=1); top.columnconfigure(0, weight=1)
        for rec in rows:
            tv.insert("", "end", values=[rec.get("id",""), rec.get("name",""), rec.get("account_id","N/A")])
        def use_sel():
            sel = tv.selection()
            if not sel: return
            vals = tv.item(sel[0], "values")
            supplier_id = str(vals[0]) if vals else ""
            self.var_supplier_id.set(supplier_id)
            tpl = self.var_sup_tpl.get().strip()
            if tpl and supplier_id:
                try: self.var_supplier_url.set(tpl.format(id=supplier_id))
                except Exception: pass
            self._set_status(f"Supplier ID set to {supplier_id}")
            self._refresh_preview()
            top.destroy()
        ttkmod.Button(top, text="Use selected", command=use_sel).grid(row=1, column=0, sticky="e", padx=8, pady=8)
    def _refresh_suppliers(self):
        cfg = self._collect_config()
        client = HttpClient(cfg)
        self._set_status("Refreshing suppliers from API…")
        def worker():
            try:
                res = client.get_json(cfg.suppliers_url)
                if res["status_code"] not in (200, 201):
                    self.after(0, lambda: messagebox.showerror("Suppliers", f"HTTP {res['status_code']}\n{res['body_text']}"))
                    self.after(0, lambda: self._set_status("Supplier refresh failed.")); return
                data = json.loads(res["body_text"])
                results = data.get("results", [])
                self.suppliers_cache = results
                self.suppliers_last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._save_supplier_cache()
                self.after(0, lambda: self._render_supplier_table(results))
                self.after(0, lambda: self._set_status(f"Loaded {len(results)} suppliers from API."))
            except Exception as ex:
                self.after(0, lambda: messagebox.showerror("Suppliers", f"Error: {ex}"))
                self.after(0, lambda: self._set_status("Supplier refresh failed."))
        threading.Thread(target=worker, daemon=True).start()
    def _use_selected_supplier(self):
        sel = self.sup_tree.selection()
        if not sel:
            messagebox.showinfo("Supplier", "Please select a supplier."); return
        vals = self.sup_tree.item(sel[0], "values")
        supplier_id = str(vals[0]) if vals else ""
        self.var_supplier_id.set(supplier_id)
        tpl = self.var_sup_tpl.get().strip()
        if tpl and supplier_id:
            try: self.var_supplier_url.set(tpl.format(id=supplier_id))
            except Exception: pass
        self._set_status(f"Supplier ID set to {supplier_id}")
        self._refresh_preview()
    def _save_supplier_cache(self):
        payload = {
            "last_updated": self.suppliers_last_updated or "",
            "results": self.suppliers_cache
        }
        try:
            with open(SUPPLIER_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as ex:
            messagebox.showerror("Cache save", str(ex))
    def _load_supplier_cache(self):
        if not os.path.exists(SUPPLIER_CACHE_PATH):
            self.suppliers_cache = []
            self.suppliers_last_updated = None
            return
        try:
            with open(SUPPLIER_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.suppliers_cache = data.get("results", [])
            self.suppliers_last_updated = data.get("last_updated") or None
        except Exception:
            self.suppliers_cache = []
            self.suppliers_last_updated = None
    def _apply_visibility_settings(self):
        cfg = self._collect_config()  # updates self.cfg from Settings tab fields without saving to disk
        def show_widgets(widgets: List[Any], show: bool):
            for w in widgets:
                try:
                    if show: w.grid()  # restore previous grid
                    else: w.grid_remove()
                except Exception:
                    pass
        show_widgets([self.lbl_supplier_id, self.ent_supplier_id,
                      self.lbl_supplier_url, self.ent_supplier_url], not cfg.hide_supplier_fields)
        show_widgets([self.lbl_order_date, self.ent_order_date], not cfg.hide_order_date)
        show_widgets([self.lbl_currency_code, self.ent_currency_code], not cfg.hide_currency)
        show_widgets([self.lbl_supplier_sku_1, self.ent_supplier_sku_1], not cfg.hide_supplier_sku)
        show_widgets([self.lbl_supplier_sku_2, self.ent_supplier_sku_2], not cfg.hide_supplier_sku)
        show_widgets([self.lbl_tax_rate_1, self.ent_tax_pct_1], not cfg.hide_tax_rate)
        show_widgets([self.lbl_tax_rate_2, self.ent_tax_pct_2], not cfg.hide_tax_rate)
        show_widgets([self.lbl_promised_1, self.ent_promised_date_1], not cfg.hide_promised_date)
        show_widgets([self.lbl_promised_2, self.ent_promised_date_2], not cfg.hide_promised_date)
        self._set_status("Visibility updated.")
        self._refresh_preview()
    def _collect_config(self) -> ApiConfig:
        cfg = ApiConfig(
            orders_base_url=self.var_orders_base.get().strip(),
            orders_endpoint_path=self.var_orders_path.get().strip(),
            suppliers_url=self.var_suppliers_url.get().strip(),
            username=self.var_username.get().strip(),
            password=self.var_password.get().strip(),
            save_password=self.var_save_pwd.get(),
            auth_header_override=self.var_auth_override.get().strip(),
            timeout_sec=int(self.var_timeout.get()),
            verify_ssl=bool(self.var_verify.get()),
            supplier_url_template=self.var_sup_tpl.get().strip(),
            default_supplier_id=self.var_default_supplier.get().strip(),
            default_currency_code=self.var_currency.get().strip() or "AUD",
            default_comment=self.var_comment_default.get().strip() or "order_submitted_manually",
            default_tax_rate_percent=self.var_tax_default.get().strip() or "10",
            default_days_to_promise=int(self.var_days_promise.get()),
            default_state=self.var_state_default.get().strip() or "NSW",
            default_country=self.var_country_default.get().strip() or "AU",
            hide_supplier_fields=bool(self.var_hide_supplier_fields.get()),
            hide_order_date=bool(self.var_hide_order_date.get()),
            hide_currency=bool(self.var_hide_currency.get()),
            hide_supplier_sku=bool(self.var_hide_supplier_sku.get()),
            hide_tax_rate=bool(self.var_hide_tax_rate.get()),
            hide_promised_date=bool(self.var_hide_promised_date.get()),
        )
        self.cfg = cfg
        return cfg
    def _set_status(self, s: str):
        self.status_var.set(s)
    def _save_config(self):
        cfg = self._collect_config()
        data = asdict(cfg)
        if not cfg.save_password:
            data["password"] = ""
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._set_status(f"Settings saved to {CONFIG_PATH}")
        except Exception as ex:
            messagebox.showerror("Save settings", str(ex))
    def _load_config(self) -> ApiConfig:
        if not os.path.exists(CONFIG_PATH): return ApiConfig()
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f: data = json.load(f)
            return ApiConfig(**{**asdict(ApiConfig()), **data})
        except Exception:
            return ApiConfig()
    def _test_orders_endpoint(self):
        cfg = self._collect_config()
        client = HttpClient(cfg)
        self._set_status("Testing orders endpoint…")
        def worker():
            try:
                res = client.post_json(cfg.orders_endpoint_url, {"ping": True})
                msg = self._format_response(res)
                self.after(0, lambda: self._set_text(self.txt_response, msg))
                self.after(0, lambda: self._set_status(self._status_bar_text(res)))
            except Exception as ex:
                self.after(0, lambda: self._set_text(self.txt_response, f"Test failed: {ex}"))
                self.after(0, lambda: self._set_status("Test failed."))
        threading.Thread(target=worker, daemon=True).start()
    def _label(self, parent, key, text, row, bootstyle=None):
        lbl = ttkmod.Label(parent, text=text)
        if BOOTSTRAP_AVAILABLE and bootstyle:
            try: lbl.configure(bootstyle=bootstyle)
            except Exception: pass
        lbl.grid(row=row, column=0, sticky="w", pady=2)
        return lbl
    def _entry(self, parent, var, row, col):
        e = ttkmod.Entry(parent, textvariable=var); e.grid(row=row, column=col, sticky="ew", pady=2); return e
    def _readonly_entry(self, parent, var, row, col):
        e = ttkmod.Entry(parent, textvariable=var, state="readonly"); e.grid(row=row, column=col, sticky="ew", pady=2); return e
    def _combo(self, parent, var, values, row, col):
        cb = ttkmod.Combobox(parent, textvariable=var, values=values, state="readonly"); cb.grid(row=row, column=col, sticky="ew", pady=2); return cb
    def _spin(self, parent, var, row, col, **opts):
        sp = ttkmod.Spinbox(parent, textvariable=var, **opts); sp.grid(row=row, column=col, sticky="w", pady=2)
        sp.bind("<KeyRelease>", lambda e: self._refresh_preview())
        sp.bind("<<Increment>>", lambda e: self._refresh_preview())
        sp.bind("<<Decrement>>", lambda e: self._refresh_preview())
        return sp
    def _mk_str(self, v=""):
        if BOOTSTRAP_AVAILABLE: return ttkmod.StringVar(value=v)
        import tkinter as tk; return tk.StringVar(value=v)
    def _mk_bool(self, v=False):
        if BOOTSTRAP_AVAILABLE: return ttkmod.BooleanVar(value=v)
        import tkinter as tk; return tk.BooleanVar(value=v)
    def _mk_int(self, v=0):
        if BOOTSTRAP_AVAILABLE: return ttkmod.IntVar(value=v)
        import tkinter as tk; return tk.IntVar(value=v)
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
def main():
    app = OrderClientApp()
    app.mainloop()
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Exiting…")

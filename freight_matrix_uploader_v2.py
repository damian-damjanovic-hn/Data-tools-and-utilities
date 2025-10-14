import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from azure.cosmos import CosmosClient
import logging
import csv
import json
import os
import re
from decimal import Decimal, InvalidOperation

# =========================
# Logging setup
# =========================
logger = logging.getLogger("cosmos_upload")
logger.setLevel(logging.INFO)  # default; will be replaced by settings
stream_handler = logging.StreamHandler()
file_handler = logging.FileHandler("cosmos_upload.log", encoding="utf-8")
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
stream_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)
# prevent duplicate handlers in reruns
if not logger.handlers:
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)

# =========================
# Settings persistence
# =========================
SETTINGS_FILE = "app_settings.json"
DEFAULT_SETTINGS = {
    "connection_string": "",
    "database_name": "soh",
    "container_name": "dropshipPricing",
    "allow_partial_upload": False,
    "log_level": "INFO"
}
app_state = DEFAULT_SETTINGS.copy()

def load_settings():
    global app_state
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # merge with defaults to avoid missing keys
            for k, v in DEFAULT_SETTINGS.items():
                app_state[k] = data.get(k, v)
            apply_log_level(app_state.get("log_level", "INFO"))
            logger.info("Settings loaded from %s", SETTINGS_FILE)
        except Exception as e:
            logger.error("Failed to load settings: %s", e)
    else:
        apply_log_level(app_state.get("log_level", "INFO"))

def save_settings():
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(app_state, f, indent=2)
        logger.info("Settings saved to %s", SETTINGS_FILE)
        messagebox.showinfo("Settings", "Settings saved successfully.")
    except Exception as e:
        logger.error("Failed to save settings: %s", e)
        messagebox.showerror("Settings", f"Failed to save settings:\n{e}")

def apply_log_level(level_name: str):
    level = logging.INFO
    if level_name.upper() == "DEBUG":
        level = logging.DEBUG
    elif level_name.upper() == "WARNING":
        level = logging.WARNING
    elif level_name.upper() == "ERROR":
        level = logging.ERROR
    logger.setLevel(level)

# =========================
# Cosmos helpers
# =========================
def get_cosmos_container():
    """
    Return a ContainerProxy based on current app_state settings.
    """
    conn = app_state["connection_string"].strip()
    dbname = app_state["database_name"].strip()
    cname = app_state["container_name"].strip()

    client = CosmosClient.from_connection_string(conn)
    db = client.get_database_client(dbname)
    return db.get_container_client(cname)

def preflight_cosmos_connection():
    """
    Validate current connection string, database and container exist/access.
    """
    try:
        client = CosmosClient.from_connection_string(app_state["connection_string"].strip())
        db = client.get_database_client(app_state["database_name"].strip())
        _ = db.read()  # read db metadata
        container = db.get_container_client(app_state["container_name"].strip())
        _ = container.read()  # read container properties
        return True, None
    except Exception as e:
        return False, e

# =========================
# Validation rules
# =========================
RE_AU_POSTCODE = re.compile(r"^\d{4}$")
INVALID_CHARS = set('=\\@^;|,\':?"{}~[]`')  # legacy invalids
CSV_FIELD_ALIASES = {
    "sku": ["sku", "SKU", "Sku"],
    "postCode": ["postCode", "postcode", "post_code", "Postcode", "postal_code", "PostalCode"],
    "price": ["price", "Price", "unit_price", "UnitPrice"]
}

def normalize_str(v):
    if v is None:
        return ""
    return str(v).strip()

def is_valid_sku(s):
    if not s or not s.isascii():
        return False, "sku must be ASCII and non-empty"
    if any(c in INVALID_CHARS for c in s):
        return False, "sku contains one or more invalid characters"
    if "  " in s:
        return False, "sku contains multiple consecutive spaces"
    if len(s) > 128:
        return False, "sku too long (>128 chars)"
    return True, ""

def is_valid_postcode(pc):
    if not RE_AU_POSTCODE.match(pc):
        return False, "postCode must be 4 digits (AU)"
    return True, ""

def normalize_price(p):
    """
    Accepts str/number, returns (ok, normalized_str_price, error)
    Normalizes to 2 decimal places as string to match schema.
    """
    if p is None or str(p).strip() == "":
        return False, None, "price missing"
    try:
        d = Decimal(str(p)).quantize(Decimal("0.01"))
        if d < 0:
            return False, None, "price must be >= 0"
        return True, format(d, "f"), ""
    except (InvalidOperation, ValueError):
        return False, None, "price must be numeric (up to 2 decimals)"

def field_from_row(row, logical_key):
    for k in CSV_FIELD_ALIASES[logical_key]:
        if k in row and str(row[k]).strip() != "":
            return row[k]
    return None

def build_doc(sku, postcode, price):
    doc_id = f"{sku}{postcode}"
    return {
        "id": doc_id,
        "postCode": str(postcode),
        "price": str(price),
        "sku": str(sku),
        "message": ""
    }

def write_error_report(error_rows, report_path):
    headers = ["row", "context", "error"]
    try:
        with open(report_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
            for e in error_rows:
                w.writerow({
                    "row": e.get("row"),
                    "context": e.get("context", ""),
                    "error": e.get("error", "")
                })
    except Exception as e:
        logger.error("Failed to write error report: %s", e)

def validate_csv(file_path):
    valid_docs = []
    errors = []
    warnings = []
    seen_ids = set()

    with open(file_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            errors.append({"row": 1, "context": "header", "error": "Missing header row"})
            return valid_docs, errors, warnings

        # Ensure required columns present in some alias
        missing_min = []
        for key in ["sku", "postCode", "price"]:
            if not any(alias in reader.fieldnames for alias in CSV_FIELD_ALIASES[key]):
                missing_min.append(key)
        if missing_min:
            errors.append({
                "row": 1,
                "context": "header",
                "error": f"Missing required columns: {', '.join(missing_min)}"
            })
            return valid_docs, errors, warnings

        for idx, row in enumerate(reader, start=2):  # header is row 1
            raw_sku = normalize_str(field_from_row(row, "sku"))
            raw_pc = normalize_str(field_from_row(row, "postCode"))
            raw_price = normalize_str(field_from_row(row, "price"))

            if not raw_sku and not raw_pc and not raw_price:
                continue  # skip empty lines

            ok_sku, sku_err = is_valid_sku(raw_sku)
            ok_pc, pc_err = is_valid_postcode(raw_pc)
            ok_price, norm_price, price_err = normalize_price(raw_price)

            errs = []
            if not raw_sku: errs.append("sku missing")
            if not raw_pc: errs.append("postCode missing")
            if not raw_price: errs.append("price missing")
            if raw_sku and not ok_sku: errs.append(sku_err)
            if raw_pc and not ok_pc: errs.append(pc_err)
            if raw_price and not ok_price: errs.append(price_err)

            if errs:
                errors.append({"row": idx, "context": f"sku={raw_sku}, postCode={raw_pc}", "error": "; ".join(errs)})
                continue

            doc_id = f"{raw_sku}{raw_pc}"
            if doc_id in seen_ids:
                errors.append({"row": idx, "context": f"sku={raw_sku}, postCode={raw_pc}", "error": "Duplicate id within file"})
                continue
            seen_ids.add(doc_id)

            doc = build_doc(raw_sku, raw_pc, norm_price)
            valid_docs.append(doc)

    return valid_docs, errors, warnings

def validate_json(file_path):
    valid_docs = []
    errors = []
    warnings = []
    seen_ids = set()

    def validate_obj(obj, idx_for_report):
        raw_sku = normalize_str(obj.get("sku"))
        if not raw_sku:
            for k in CSV_FIELD_ALIASES["sku"]:
                if k in obj:
                    raw_sku = normalize_str(obj[k]); break

        raw_pc = normalize_str(obj.get("postCode") or obj.get("postcode") or obj.get("post_code"))

        raw_price_val = obj.get("price")
        if raw_price_val is None:
            for k in ["Price", "unit_price", "UnitPrice"]:
                if k in obj:
                    raw_price_val = obj[k]; break
        raw_price = normalize_str(raw_price_val)

        ok_sku, sku_err = is_valid_sku(raw_sku)
        ok_pc, pc_err = is_valid_postcode(raw_pc)
        ok_price, norm_price, price_err = normalize_price(raw_price)

        errs = []
        if not raw_sku: errs.append("sku missing")
        if not raw_pc: errs.append("postCode missing")
        if raw_price == "": errs.append("price missing")
        if raw_sku and not ok_sku: errs.append(sku_err)
        if raw_pc and not ok_pc: errs.append(pc_err)
        if raw_price != "" and not ok_price: errs.append(price_err)

        if errs:
            errors.append({"row": idx_for_report, "context": f"sku={raw_sku}, postCode={raw_pc}", "error": "; ".join(errs)})
            return

        doc_id = f"{raw_sku}{raw_pc}"
        if doc_id in seen_ids:
            errors.append({"row": idx_for_report, "context": f"sku={raw_sku}, postCode={raw_pc}", "error": "Duplicate id within file"})
            return
        seen_ids.add(doc_id)

        doc = build_doc(raw_sku, raw_pc, norm_price)
        valid_docs.append(doc)

    # Try array first
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            for i, obj in enumerate(data, start=1):
                if not isinstance(obj, dict):
                    errors.append({"row": i, "context": "", "error": "Each item must be a JSON object"})
                    continue
                validate_obj(obj, i)
            return valid_docs, errors, warnings
        else:
            warnings.append("Top-level JSON is not an array; falling back to NDJSON parser.")
    except json.JSONDecodeError:
        warnings.append("JSON is not an array; attempting NDJSON (one JSON object per line).")

    # NDJSON fallback
    with open(file_path, encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    errors.append({"row": i, "context": "", "error": "Line is not a JSON object"})
                    continue
                validate_obj(obj, i)
            except json.JSONDecodeError as e:
                errors.append({"row": i, "context": "", "error": f"Invalid JSON: {e}"})

    return valid_docs, errors, warnings

def validate_file(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        return validate_csv(file_path)
    elif ext == ".json":
        return validate_json(file_path)
    else:
        return [], [{"row": 0, "context": "", "error": "Unsupported file type. Use CSV or JSON."}], []

# =========================
# Upload functions
# =========================
def upload_sku_price(sku, postcode, price, log_area):
    # Preflight connection
    ok_conn, err_conn = preflight_cosmos_connection()
    if not ok_conn:
        messagebox.showerror("Connection Error", f"Cosmos connection failed.\n\nDetails:\n{err_conn}")
        return

    if not sku or not postcode or not price:
        messagebox.showwarning("Missing Data", "Please fill in all fields.")
        return

    sku = sku.strip()
    postcode = postcode.strip()

    ok_sku, sku_err = is_valid_sku(sku)
    ok_pc, pc_err = is_valid_postcode(postcode)
    ok_price, norm_price, price_err = normalize_price(price)

    errs = []
    if not ok_sku: errs.append(sku_err)
    if not ok_pc: errs.append(pc_err)
    if not ok_price: errs.append(price_err)
    if errs:
        messagebox.showerror("Validation Error", "\n".join(errs))
        return

    doc = build_doc(sku, postcode, norm_price)
    try:
        container = get_cosmos_container()
        response = container.upsert_item(doc)

        # Best-effort request charge capture
        ru = None
        try:
            ru = container.client_connection.last_response_headers.get("x-ms-request-charge")
        except Exception:
            pass

        log_area.insert(tk.END, f"Uploaded {doc['id']} successfully. RU: {ru}\n")
        log_area.see(tk.END)
        logger.info(f"Uploaded document {doc['id']} (RU: {ru})")

    except Exception as e:
        log_area.insert(tk.END, f"Error uploading {doc['id']}: {e}\n")
        log_area.see(tk.END)
        logger.error(f"Upload failed for {doc['id']}: {e}")
        messagebox.showerror("Upload Error", str(e))

def bulk_upload(file_path, log_area):
    # Preflight connection
    ok_conn, err_conn = preflight_cosmos_connection()
    if not ok_conn:
        msg = f"Cosmos connection failed. Please verify connection string / DB / container.\n\nDetails:\n{err_conn}"
        log_area.insert(tk.END, msg + "\n")
        log_area.see(tk.END)
        logger.error(msg)
        messagebox.showerror("Connection Error", msg)
        return

    try:
        log_area.insert(tk.END, f"Validating file: {file_path}\n")
        log_area.see(tk.END)
        valid_docs, errors, warnings = validate_file(file_path)

        for w in warnings:
            log_area.insert(tk.END, f"Warning: {w}\n")
            logger.warning(w)

        report_path = f"{file_path}.errors.csv"
        allow_partial = app_state.get("allow_partial_upload", False)

        if errors and not allow_partial:
            write_error_report(errors, report_path)
            msg = (
                f"Validation failed.\n"
                f"Valid rows: {len(valid_docs)}\n"
                f"Errors: {len(errors)}\n\n"
                f"Error report written to:\n{report_path}\n\n"
                f"No data has been uploaded (strict mode)."
            )
            log_area.insert(tk.END, msg + "\n")
            log_area.see(tk.END)
            logger.error(f"Bulk validation failed with {len(errors)} errors. Report: {report_path}")
            messagebox.showerror("Validation Failed", msg)
            return

        if errors and allow_partial:
            write_error_report(errors, report_path)
            log_area.insert(tk.END, f"Partial mode: {len(errors)} errors logged to {report_path}. Uploading {len(valid_docs)} valid rows...\n")
            log_area.see(tk.END)
            logger.warning(f"Partial upload enabled. Errors: {len(errors)}; Proceeding with {len(valid_docs)} valid rows.")

        # Proceed to upload valid docs
        container = get_cosmos_container()
        uploaded = 0
        total_ru = Decimal("0")
        for i, doc in enumerate(valid_docs, start=1):
            try:
                container.upsert_item(doc)
                uploaded += 1
                # RU capture (best-effort)
                try:
                    ru_val = container.client_connection.last_response_headers.get("x-ms-request-charge")
                    if ru_val is not None:
                        total_ru += Decimal(str(ru_val))
                except Exception:
                    pass

                if i % 500 == 0 or i == len(valid_docs):
                    log_area.insert(tk.END, f"Uploaded {i}/{len(valid_docs)}...\n")
                    log_area.see(tk.END)
            except Exception as e:
                # In partial mode, continue; in strict mode we never reach here with errors
                log_area.insert(tk.END, f"Error uploading {doc['id']}: {e}\n")
                log_area.see(tk.END)
                logger.error(f"Error uploading {doc['id']}: {e}")

        messagebox.showinfo("Bulk Upload Complete", f"Uploaded {uploaded} records successfully.\nApprox total RU: {total_ru}")
        logger.info(f"Bulk upload complete. Uploaded: {uploaded}. Approx total RU: {total_ru}")

    except Exception as e:
        log_area.insert(tk.END, f"Bulk upload error: {e}\n")
        log_area.see(tk.END)
        logger.error(f"Bulk upload failed: {e}")
        messagebox.showerror("Bulk Upload Error", str(e))

def select_file_and_upload(log_area):
    file_path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("JSON files", "*.json")])
    if file_path:
        bulk_upload(file_path, log_area)

# =========================
# Settings UI helpers
# =========================
def apply_settings_from_ui(conn_var, db_var, cont_var, partial_var, loglevel_var):
    app_state["connection_string"] = conn_var.get().strip()
    app_state["database_name"] = db_var.get().strip()
    app_state["container_name"] = cont_var.get().strip()
    app_state["allow_partial_upload"] = bool(partial_var.get())
    app_state["log_level"] = loglevel_var.get()
    apply_log_level(app_state["log_level"])

def test_connection_action():
    ok, err = preflight_cosmos_connection()
    if ok:
        messagebox.showinfo("Connection Test", "Connection successful!")
    else:
        messagebox.showerror("Connection Test Failed", str(err))

# =========================
# Build UI
# =========================
def build_app():
    load_settings()

    root = tk.Tk()
    root.title("Freight Matrix Loader")
    root.geometry("780x540")
    root.minsize(720, 480)

    style = ttk.Style()
    try:
        # pick a clean built-in theme
        style.theme_use("clam")
    except Exception:
        pass

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    # ------------- Upload Tab -------------
    tab_upload = ttk.Frame(notebook)
    notebook.add(tab_upload, text="Upload")

    # Left controls frame
    left = ttk.Frame(tab_upload, padding=10)
    left.grid(row=0, column=0, sticky="nsw")

    # Right log area frame
    right = ttk.Frame(tab_upload, padding=10)
    right.grid(row=0, column=1, sticky="nsew")
    tab_upload.columnconfigure(1, weight=1)
    tab_upload.rowconfigure(0, weight=1)

    # Controls
    ttk.Label(left, text="SKU:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
    sku_entry = ttk.Entry(left, width=28)
    sku_entry.grid(row=0, column=1, sticky="w", padx=4, pady=4)

    ttk.Label(left, text="Postcode:").grid(row=1, column=0, sticky="e", padx=4, pady=4)
    postcode_entry = ttk.Entry(left, width=28)
    postcode_entry.grid(row=1, column=1, sticky="w", padx=4, pady=4)

    ttk.Label(left, text="Price:").grid(row=2, column=0, sticky="e", padx=4, pady=4)
    price_entry = ttk.Entry(left, width=28)
    price_entry.grid(row=2, column=1, sticky="w", padx=4, pady=4)

    send_button = ttk.Button(left, text="Single SKU Update",
                             command=lambda: upload_sku_price(sku_entry.get(), postcode_entry.get(), price_entry.get(), log_area))
    send_button.grid(row=3, column=0, columnspan=2, sticky="ew", padx=4, pady=(8, 4))

    bulk_button = ttk.Button(left, text="Bulk Upload CSV/JSON (Validate First)",
                             command=lambda: select_file_and_upload(log_area))
    bulk_button.grid(row=4, column=0, columnspan=2, sticky="ew", padx=4, pady=4)

    # Log area
    log_area = scrolledtext.ScrolledText(right, width=80, height=24)
    log_area.pack(fill="both", expand=True)

    # ------------- Settings Tab -------------
    tab_settings = ttk.Frame(notebook)
    notebook.add(tab_settings, text="Settings")

    # Variables bound to UI
    conn_var = tk.StringVar(value=app_state["connection_string"])
    db_var = tk.StringVar(value=app_state["database_name"])
    cont_var = tk.StringVar(value=app_state["container_name"])
    partial_var = tk.IntVar(value=1 if app_state["allow_partial_upload"] else 0)
    loglevel_var = tk.StringVar(value=app_state["log_level"])

    # Layout grid
    tab_settings.columnconfigure(1, weight=1)

    # Connection String (masked with show/hide toggle)
    ttk.Label(tab_settings, text="Connection String:").grid(row=0, column=0, sticky="e", padx=6, pady=6)
    conn_entry = ttk.Entry(tab_settings, textvariable=conn_var, width=80)
    conn_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=6)

    # Optional mask toggle (replace above with masked if you prefer)
    # If you want masking, uncomment next two lines and comment the unmasked entry above:
    # conn_entry = ttk.Entry(tab_settings, textvariable=conn_var, width=80, show="•")
    # conn_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=6)

    ttk.Label(tab_settings, text="Database Name:").grid(row=1, column=0, sticky="e", padx=6, pady=6)
    db_entry = ttk.Entry(tab_settings, textvariable=db_var, width=40)
    db_entry.grid(row=1, column=1, sticky="w", padx=6, pady=6)

    ttk.Label(tab_settings, text="Container Name:").grid(row=2, column=0, sticky="e", padx=6, pady=6)
    cont_entry = ttk.Entry(tab_settings, textvariable=cont_var, width=40)
    cont_entry.grid(row=2, column=1, sticky="w", padx=6, pady=6)

    ttk.Label(tab_settings, text="Log Level:").grid(row=3, column=0, sticky="e", padx=6, pady=6)
    loglevel_combo = ttk.Combobox(tab_settings, textvariable=loglevel_var, values=["DEBUG", "INFO", "WARNING", "ERROR"], width=12, state="readonly")
    loglevel_combo.grid(row=3, column=1, sticky="w", padx=6, pady=6)

    partial_check = ttk.Checkbutton(tab_settings, text="Allow partial upload if validation errors exist", variable=partial_var)
    partial_check.grid(row=4, column=1, sticky="w", padx=6, pady=6)

    # Buttons: Apply, Save, Test Connection
    btn_frame = ttk.Frame(tab_settings)
    btn_frame.grid(row=5, column=1, sticky="w", padx=6, pady=10)

    apply_btn = ttk.Button(btn_frame, text="Apply (No Save)", command=lambda: (apply_settings_from_ui(conn_var, db_var, cont_var, partial_var, loglevel_var), messagebox.showinfo("Settings", "Settings applied.")))
    apply_btn.grid(row=0, column=0, padx=4)

    save_btn = ttk.Button(btn_frame, text="Save Settings", command=lambda: (apply_settings_from_ui(conn_var, db_var, cont_var, partial_var, loglevel_var), save_settings()))
    save_btn.grid(row=0, column=1, padx=4)

    test_btn = ttk.Button(btn_frame, text="Test Connection", command=test_connection_action)
    test_btn.grid(row=0, column=2, padx=4)

    # Footer help
    help_text = (
        "Tips:\n"
        "- Enter the exact Primary connection string from Azure Portal → Cosmos DB → Keys.\n"
        "- Use 'Apply' to test without saving; 'Save' persists to app_settings.json.\n"
        "- In strict mode, any validation error blocks the upload. In partial mode, valid rows upload and errors are written to <file>.errors.csv.\n"
        "- Approximate RU charge is displayed when available."
    )
    ttk.Label(tab_settings, text=help_text, foreground="#555").grid(row=6, column=0, columnspan=2, sticky="w", padx=6, pady=6)

    # Start
    return root

# =========================
# Main
# =========================
if __name__ == "__main__":
    root = build_app()
    root.mainloop()






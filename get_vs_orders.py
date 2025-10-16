import httpx
import time
import threading
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker
import tkinter as tk
from tkinter import ttk
Base = declarative_base()
class Order(Base):
    __tablename__ = 'orders'
    order_reference = Column(String, primary_key=True)
    order_date = Column(DateTime)
    status = Column(String)
    part_number = Column(String)
    quantity = Column(Integer)
    item_total = Column(Float)
class Log(Base):
    __tablename__ = 'logs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    url = Column(Text)
    status_code = Column(Integer)
    message = Column(Text)
class OrderSyncApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Order Sync Tool")
        self.config = {
            "verify_ssl": tk.BooleanVar(value=True),
            "max_retries": tk.IntVar(value=3),
            "rate_limit": tk.IntVar(value=150),
            "api_url": tk.StringVar(value="https://api.virtualstock.com/restapi/v4/orders/"),
            "db_path": tk.StringVar(value="orders.db"),
            "days_ago": tk.IntVar(value=365),
            "username": tk.StringVar(),
            "password": tk.StringVar()
        }
        self.setup_gui()
    def setup_gui(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill='both', expand=True)
        settings_tab = ttk.Frame(notebook)
        notebook.add(settings_tab, text='Settings')
        ttk.Checkbutton(settings_tab, text="Verify SSL", variable=self.config["verify_ssl"]).grid(row=0, column=0, sticky='w')
        ttk.Label(settings_tab, text="Max Retries:").grid(row=1, column=0, sticky='w')
        ttk.Spinbox(settings_tab, from_=1, to=10, textvariable=self.config["max_retries"]).grid(row=1, column=1)
        ttk.Label(settings_tab, text="Requests per Minute:").grid(row=2, column=0, sticky='w')
        ttk.Entry(settings_tab, textvariable=self.config["rate_limit"]).grid(row=2, column=1)
        ttk.Label(settings_tab, text="API Base URL:").grid(row=3, column=0, sticky='w')
        ttk.Entry(settings_tab, textvariable=self.config["api_url"], width=40).grid(row=3, column=1)
        ttk.Label(settings_tab, text="SQLite DB Path:").grid(row=4, column=0, sticky='w')
        ttk.Entry(settings_tab, textvariable=self.config["db_path"], width=40).grid(row=4, column=1)
        ttk.Label(settings_tab, text="Days Ago Filter:").grid(row=5, column=0, sticky='w')
        ttk.Spinbox(settings_tab, from_=1, to=1000, textvariable=self.config["days_ago"]).grid(row=5, column=1)
        auth_tab = ttk.Frame(notebook)
        notebook.add(auth_tab, text='Auth')
        ttk.Label(auth_tab, text="Username:").grid(row=0, column=0, sticky='w')
        ttk.Entry(auth_tab, textvariable=self.config["username"]).grid(row=0, column=1)
        ttk.Label(auth_tab, text="Password:").grid(row=1, column=0, sticky='w')
        ttk.Entry(auth_tab, textvariable=self.config["password"], show="*").grid(row=1, column=1)
        log_tab = ttk.Frame(notebook)
        notebook.add(log_tab, text='Live Log')
        self.log_text = tk.Text(log_tab, height=20, width=80)
        self.log_text.pack(fill='both', expand=True)
        ttk.Button(self.root, text="Start Sync", command=self.start_sync).pack(pady=5)
    def start_sync(self):
        threading.Thread(target=self.sync_orders, daemon=True).start()
        self.update_logs()
    def sync_orders(self):
        engine = create_engine(f"sqlite:///{self.config['db_path'].get()}")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        offset = 0
        limit = 10
        has_more = True
        rate_delay = 60 / self.config["rate_limit"].get()
        days_ago = self.config["days_ago"].get()
        cutoff_date = datetime.now() - timedelta(days=days_ago)
        auth = httpx.BasicAuth(self.config["username"].get(), self.config["password"].get())
        with httpx.Client(auth=auth, timeout=30.0, verify=self.config["verify_ssl"].get()) as client:
            while has_more:
                url = f"{self.config['api_url'].get()}?limit={limit}&offset={offset}"
                attempt = 0
                success = False
                response = None
                while attempt < self.config["max_retries"].get() and not success:
                    try:
                        response = client.get(url)
                        response.raise_for_status()
                        success = True
                        data = response.json()
                        session.add(Log(url=url, status_code=response.status_code, message="Success"))
                    except httpx.HTTPError as e:
                        attempt += 1
                        status_code = response.status_code if response else 0
                        session.add(Log(url=url, status_code=status_code, message=f"Attempt {attempt}: {str(e)}"))
                        time.sleep(2 ** attempt)
                session.commit()
                if not success:
                    break
                orders = data.get("results", [])
                if not orders:
                    has_more = False
                    break
                for order in orders:
                    try:
                        order_date = datetime.fromisoformat(order["order_date"])
                    except Exception:
                        continue
                    if order_date < cutoff_date:
                        continue
                    items = order.get("items", [])
                    part_number = items[0]["part_number"] if items else None
                    quantity = int(items[0]["quantity"]) if items else None
                    item_total = float(items[0]["total"]) if items else None
                    if not session.query(Order).filter_by(order_reference=order["order_reference"]).first():
                        new_order = Order(
                            order_reference=order["order_reference"],
                            order_date=order_date,
                            status=order["status"],
                            part_number=part_number,
                            quantity=quantity,
                            item_total=item_total
                        )
                        session.add(new_order)
                session.commit()
                offset += limit
                time.sleep(rate_delay)
        session.close()
    def update_logs(self):
        try:
            engine = create_engine(f"sqlite:///{self.config['db_path'].get()}")
            Session = sessionmaker(bind=engine)
            session = Session()
            logs = session.query(Log).order_by(Log.timestamp.desc()).limit(50).all()
            self.log_text.delete(1.0, tk.END)
            for log in logs:
                self.log_text.insert(tk.END, f"[{log.timestamp}] {log.status_code} - {log.message}\n")
            session.close()
        except Exception as e:
            self.log_text.insert(tk.END, f"Error loading logs: {str(e)}\n")
        self.root.after(5000, self.update_logs)
root = tk.Tk()
app = OrderSyncApp(root)
root.mainloop()

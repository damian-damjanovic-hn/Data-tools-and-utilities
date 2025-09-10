import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from datetime import datetime
import calendar

MONTHS_FULL = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]
MONTHS_ABBR = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
]

def create_folders(base_dir, year, format_type, include_days, use_parent, suffix):
    try:
        if use_parent:
            parent_folder_name = f"{year}_{suffix.strip()}"
            base_dir = os.path.join(base_dir, parent_folder_name)
            os.makedirs(base_dir, exist_ok=True)
            for extra in ["documents", "archive", "{other}"]:
                os.makedirs(os.path.join(base_dir, extra), exist_ok=True)

        for i in range(12):
            if format_type == "Full":
                folder_name = f"{year}_{i+1:02d}_{MONTHS_FULL[i]}"
            else:
                folder_name = f"{i+1:02d}-{MONTHS_ABBR[i]}"

            folder_path = os.path.join(base_dir, folder_name)
            os.makedirs(folder_path, exist_ok=True)

            readme_path = os.path.join(folder_path, "README.txt")
            with open(readme_path, "w") as f:
                f.write(f"This folder is for storing files for {MONTHS_FULL[i]} {year}.")

            if include_days:
                days_in_month = calendar.monthrange(int(year), i+1)[1]
                for day in range(1, days_in_month + 1):
                    day_folder = os.path.join(folder_path, f"{day:02d}_{MONTHS_ABBR[i]}_{year}")
                    os.makedirs(day_folder, exist_ok=True)

        messagebox.showinfo("Success", f"Folders created successfully in:\n{base_dir}")
    except Exception as e:
        messagebox.showerror("Error", f"An error occurred:\n{str(e)}")

class FolderCreatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Monthly Folder Creator")
        self.root.geometry("560x420")
        self.root.resizable(False, False)

        ttk.Label(root, text="Create Monthly Folders", font=("Helvetica", 16)).pack(pady=10)

        year_frame = ttk.Frame(root)
        year_frame.pack(pady=5)
        ttk.Label(year_frame, text="Select Year:").pack(side="left", padx=5)
        self.year_var = tk.StringVar()
        current_year = datetime.now().year
        years = [str(y) for y in range(current_year - 10, current_year + 11)]
        self.year_dropdown = ttk.Combobox(year_frame, textvariable=self.year_var, values=years, state="readonly", width=10)
        self.year_dropdown.set(str(current_year))
        self.year_dropdown.pack(side="left")

        format_frame = ttk.Frame(root)
        format_frame.pack(pady=5)
        ttk.Label(format_frame, text="Folder Format:").pack(side="left", padx=5)
        self.format_var = tk.StringVar(value="Abbr")
        ttk.Radiobutton(format_frame, text="YYYY_MM_MonthName", variable=self.format_var, value="Full").pack(side="left")
        ttk.Radiobutton(format_frame, text="MM-MonthAbbrev", variable=self.format_var, value="Abbr").pack(side="left")

        self.daily_var = tk.BooleanVar()
        ttk.Checkbutton(root, text="Create folders for each day of the month", variable=self.daily_var).pack(pady=5)

        self.parent_var = tk.BooleanVar()
        parent_frame = ttk.Frame(root)
        parent_frame.pack(pady=5)
        ttk.Checkbutton(parent_frame, text="Create parent folder with year and suffix", variable=self.parent_var).pack(side="left")
        ttk.Label(parent_frame, text="Suffix:").pack(side="left", padx=5)
        self.suffix_entry = ttk.Entry(parent_frame, width=20)
        self.suffix_entry.insert(0, "Project")
        self.suffix_entry.pack(side="left")

        dir_frame = ttk.Frame(root)
        dir_frame.pack(pady=5)
        ttk.Label(dir_frame, text="Base Directory:").pack(side="left", padx=5)
        self.dir_entry = ttk.Entry(dir_frame, width=40)
        self.dir_entry.pack(side="left")
        ttk.Button(dir_frame, text="Browse", command=self.browse_directory).pack(side="left", padx=5)

        ttk.Button(root, text="Create Folders", command=self.create).pack(pady=20)

    def browse_directory(self):
        selected_dir = filedialog.askdirectory()
        if selected_dir:
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, selected_dir)

    def create(self):
        year = self.year_var.get()
        base_dir = self.dir_entry.get()
        format_type = self.format_var.get()
        include_days = self.daily_var.get()
        use_parent = self.parent_var.get()
        suffix = self.suffix_entry.get()

        if not year.isdigit() or len(year) != 4:
            messagebox.showerror("Invalid Input", "Please select a valid 4-digit year.")
            return

        if not base_dir:
            messagebox.showerror("Missing Directory", "Please select a base directory.")
            return

        create_folders(base_dir, year, format_type, include_days, use_parent, suffix)

if __name__ == "__main__":
    root = tk.Tk()
    app = FolderCreatorApp(root)
    root.mainloop()

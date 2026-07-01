# main_screen.py
import tkinter as tk
from tkinter import ttk
from pathlib import Path
import csv
from tkinter import filedialog, messagebox
import os
import sys

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from generator_frontend import GeneratorFrame
from batch_program import BatchFrame
from dgpx_builder import DGPXBuilderFrame
from batch_dgpx import DGPXBatchFrame
from index_param_frame import IndexParameterFrame
from jobs_frame import JobsFrame
from job_manager import JobManager

# ------------------ Configure your base paths here ------------------
CLOSEST_STATIONS_CSV = Path(r"E:\Support files for PCG\closest_stations.csv")
US_GRID_LIST_XLSX = Path(r"E:\Support files for PCG\us_grid_list.xlsx")
SCENARIO_ROOT = Path(r"E:\Support files for PCG\Scenario Root")
HCD_INPUT_DIR_GEN = Path(r"E:\Support files for PCG\MERRA-2 - HCD files")
OUTPUT_BASE_HCD = Path(r"E:\Support files for PCG\new HCD")
OUTPUT_BASE_DGPX = Path(r"E:\Support files for PCG\new DGPX")
OUTPUT_BASE_INDEX_ANALYSIS = Path(r"E:\Support files for PCG\index parameter analysis")
# --------------------------------------------------------------------

BG_APP = "#eef0ed"
BG_CARD = "#ffffff"
BG_PANEL = "#dde4de"
ACCENT = "#369790"
ACCENT_HOVER = "#2f857f"
ACCENT_DARK = "#266b66"
TEXT_MUTED = "#5f6962"
TEXT_SOFT = "#7e8881"
CARD_BORDER = "#cfd7d1"
FIELD_BG = "#fafcf9"


def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        return Path(os.path.join(sys._MEIPASS, relative_path))
    return Path(os.path.join(os.path.abspath("."), relative_path))


def list_subdirs(root: Path):
    try:
        return sorted([p.name for p in Path(root).iterdir() if p.is_dir()])
    except Exception:
        return []


def load_city_and_station_data(path: Path):
    """Read city and closest-station metadata from the configured CSV."""
    cities, info = [], {}
    if not path.exists():
        return [], {}

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)

        def norm(value):
            return value.strip().lower().replace("_", " ").replace("-", " ")

        cols = {norm(col): col for col in (reader.fieldnames or [])}

        city_col = cols.get("city") or cols.get("name") or cols.get("town")
        lat_col = cols.get("latitude") or cols.get("lat") or cols.get("city latitude")
        lon_col = cols.get("longitude") or cols.get("lon") or cols.get("long") or cols.get("city longitude")
        station_col = (
            cols.get("closest station")
            or cols.get("closest_station")
            or cols.get("station")
            or cols.get("station 1 id")
        )
        station_lat_col = (
            cols.get("station latitude")
            or cols.get("station_latitude")
            or cols.get("station_lat")
            or cols.get("station 1 lat")
        )
        station_lon_col = (
            cols.get("station longitude")
            or cols.get("station_longitude")
            or cols.get("station_lon")
            or cols.get("station 1 lon")
        )
        state_col = cols.get("state") or cols.get("province") or cols.get("region")

        if not (city_col and lat_col and lon_col and station_col):
            raise ValueError("CSV must have City, Latitude, Longitude, Closest Station")

        for row in reader:
            city = (row.get(city_col) or "").strip()
            if not city:
                continue
            try:
                lat = float((row.get(lat_col) or "").strip())
                lon = float((row.get(lon_col) or "").strip())
            except ValueError:
                continue

            station = (row.get(station_col) or "").strip()
            state = (row.get(state_col) or "").strip() if state_col else ""
            station_lat = None
            station_lon = None
            try:
                if station_lat_col and row.get(station_lat_col):
                    station_lat = float((row.get(station_lat_col) or "").strip())
            except Exception:
                station_lat = None
            try:
                if station_lon_col and row.get(station_lon_col):
                    station_lon = float((row.get(station_lon_col) or "").strip())
            except Exception:
                station_lon = None

            if city not in info:
                info[city] = {
                    "lat": lat,
                    "lon": lon,
                    "station": station,
                    "state": state,
                    "station_lat": station_lat,
                    "station_lon": station_lon,
                }
                cities.append(city)

    return cities, info


class SplashScreen(ttk.Frame):
    def __init__(self, parent, controller, project_name="Pavement Climate Generator"):
        super().__init__(parent, padding=18, style="App.TFrame")
        self.controller = controller

        title_frame = ttk.Frame(self, style="App.TFrame")
        title_frame.pack(pady=(44, 16))

        ttk.Label(title_frame, text=project_name, style="SplashTitle.TLabel").pack()
        ttk.Label(
            title_frame,
            text="Generating hourly climate/HCD inputs made simple",
            style="Subtitle.TLabel",
        ).pack(pady=(4, 0))

        loading_shell = tk.Frame(self, bg=BG_CARD, highlightbackground=CARD_BORDER, highlightthickness=1, bd=0)
        loading_shell.pack(pady=(20, 0))
        loading_frame = ttk.Frame(loading_shell, padding=(16, 12), style="Card.TFrame")
        loading_frame.pack(fill="x")

        ttk.Label(loading_frame, text="Loading application...", style="Muted.TLabel").pack()
        progress = ttk.Progressbar(loading_frame, mode="indeterminate", length=300, style="Accent.Horizontal.TProgressbar")
        progress.pack(pady=(10, 0))
        progress.start(10)

        self.after(2000, self._show_main_menu)

    def _show_main_menu(self):
        if not hasattr(self.controller, "frames") or "MainMenu" not in self.controller.frames:
            self.after(100, self._show_main_menu)
            return
        self.controller.show_frame("MainMenu")


class MainMenu(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, padding=14, style="App.TFrame")

        self.columnconfigure(0, weight=2)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        left_frame = ttk.Frame(self, padding=(0, 6), style="App.TFrame")
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        hero_shell = tk.Frame(left_frame, bg=BG_PANEL, highlightbackground=CARD_BORDER, highlightthickness=1, bd=0)
        hero_shell.pack(fill="x", pady=(4, 12))
        hero_frame = ttk.Frame(hero_shell, padding=(14, 12), style="Panel.TFrame")
        hero_frame.pack(fill="x")

        ttk.Label(hero_frame, text="Pavement Climate Generator", style="Title.TLabel").pack(anchor="w")
        ttk.Label(hero_frame, text="Arizona Department of Transportation", style="Subtitle.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Label(
            hero_frame,
            text="Generate climate files, update ME design inputs, and run supporting analysis tools.",
            style="PanelMuted.TLabel",
        ).pack(anchor="w", pady=(5, 0))

        button_frame = ttk.Frame(left_frame, style="App.TFrame")
        button_frame.pack(fill="x", pady=(0, 6))

        section1 = self._create_menu_card(
            button_frame,
            "Future Weather Files",
            "",
        )
        ttk.Button(section1, text="Create Future Weather File (.hcd)",
                   command=lambda: controller.show_frame("GeneratorFrame"),
                   style="MainMenu.TButton").pack(fill="x", pady=(0, 8))
        ttk.Button(section1, text="Create Future Weather File (Batch mode)",
                   command=lambda: controller.show_frame("BatchFrame"),
                   style="MainMenu.Secondary.TButton").pack(fill="x")

        section2 = self._create_menu_card(
            button_frame,
            "ME Design Climate",
            "",
        )
        ttk.Button(section2, text="Modify Climate in ME Design File (.dgpx)",
                   command=lambda: controller.show_frame("DGPXBuilder"),
                   style="MainMenu.TButton").pack(fill="x", pady=(0, 8))
        ttk.Button(section2, text="Modify Climate in ME Design File (Batch mode)",
                   command=lambda: controller.show_frame("DGPXBatch"),
                   style="MainMenu.Secondary.TButton").pack(fill="x")

        analysis_tools = self._create_menu_card(
            button_frame,
            "Analysis",
            "",
        )
        ttk.Button(analysis_tools, text="Index Parameter Calculator",
                   command=lambda: controller.show_frame("IndexParameterFrame"),
                   style="MainMenu.TButton").pack(fill="x")

        settings_tools = self._create_menu_card(
            button_frame,
            "Settings",
            "",
        )
        ttk.Button(settings_tools, text="Settings",
                   command=lambda: controller.show_frame("Settings"),
                   style="MainMenu.TButton").pack(fill="x", pady=(0, 8))
        ttk.Button(settings_tools, text="Jobs",
                   command=lambda: controller.show_frame("JobsFrame"),
                   style="MainMenu.Secondary.TButton").pack(fill="x")

        logo_shell = tk.Frame(self, bg=BG_CARD, highlightbackground=CARD_BORDER, highlightthickness=1, bd=0)
        logo_shell.grid(row=0, column=1, sticky="nsew")
        logo_frame = ttk.Frame(logo_shell, padding=(10, 14), style="Card.TFrame")
        logo_frame.pack(fill="both", expand=True)
        logo_frame.columnconfigure(0, weight=1)
        logo_frame.rowconfigure(0, weight=1)
        logo_frame.rowconfigure(2, weight=1)
        logo_stack = ttk.Frame(logo_frame, style="Card.TFrame")
        logo_stack.grid(row=1, column=0, sticky="n")

        try:
            if not PIL_AVAILABLE:
                raise ImportError("PIL not available")

            adot_logo_path = resource_path("assets/ADOT_logo.png")
            if adot_logo_path.exists():
                adot_img = Image.open(adot_logo_path)
                adot_img = adot_img.resize((150, 75), Image.Resampling.LANCZOS)
                self.adot_logo = ImageTk.PhotoImage(adot_img)
                ttk.Label(logo_stack, image=self.adot_logo, style="Card.TLabel").pack(pady=(8, 16))

            nc_logo_path = resource_path("assets/NC State logo.png")
            if nc_logo_path.exists():
                nc_img = Image.open(nc_logo_path)
                nc_img = nc_img.resize((150, 75), Image.Resampling.LANCZOS)
                self.nc_logo = ImageTk.PhotoImage(nc_img)
                ttk.Label(logo_stack, image=self.nc_logo, style="Card.TLabel").pack(pady=(0, 8))

        except ImportError:
            ttk.Label(logo_stack, text="NC State\nUniversity",
                     font=("Segoe UI", 12, "bold"),
                     foreground="#CC0000",
                     justify="center").pack(pady=(8, 16))
            ttk.Label(logo_stack, text="Arizona DOT",
                     font=("Segoe UI", 12, "bold"),
                     foreground="#0066CC",
                     justify="center").pack(pady=(0, 8))
        except Exception:
            ttk.Label(logo_stack, text="NC State\nUniversity",
                     font=("Segoe UI", 12, "bold"),
                     foreground="#CC0000",
                     justify="center").pack(pady=(8, 16))
            ttk.Label(logo_stack, text="Arizona DOT",
                     font=("Segoe UI", 12, "bold"),
                     foreground="#0066CC",
                     justify="center").pack(pady=(0, 8))

        footer_frame = ttk.Frame(left_frame, style="App.TFrame")
        footer_frame.pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Label(footer_frame, text="Climate Data Processing & Analysis Tool", style="Footnote.TLabel").pack(anchor="w")

    def _create_menu_card(self, parent, title, subtitle):
        shell = tk.Frame(parent, bg=BG_CARD, highlightbackground=CARD_BORDER, highlightthickness=1, bd=0)
        shell.pack(fill="x", pady=(0, 8))
        inner = ttk.Frame(shell, padding=(10, 8), style="Card.TFrame")
        inner.pack(fill="x")
        ttk.Label(inner, text=title, style="SectionTitle.TLabel").pack(anchor="w")
        if subtitle.strip():
            ttk.Label(inner, text=subtitle, style="Muted.TLabel").pack(anchor="w", pady=(2, 6))
        return inner


class SettingsFrame(ttk.Frame):
    """Settings screen to change folder/file paths used by the app."""

    def __init__(self, parent, controller):
        super().__init__(parent, padding=12, style="App.TFrame")
        self.c = controller

        top = ttk.Frame(self, style="App.TFrame")
        top.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        ttk.Button(top, text="Back", command=lambda: self.c.show_frame("MainMenu")).pack(side="left")
        ttk.Label(top, text="Settings", style="ScreenTitle.TLabel").pack(side="left", padx=12)

        intro_shell = tk.Frame(self, bg=BG_CARD, highlightbackground=CARD_BORDER, highlightthickness=1, bd=0)
        intro_shell.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        intro = ttk.Frame(intro_shell, padding=(10, 8), style="Card.TFrame")
        intro.pack(fill="x")
        ttk.Label(intro, text="Manage the data locations used across the application.", style="Muted.TLabel").pack(anchor="w")

        form = ttk.LabelFrame(self, text="Paths", padding=10)
        form.grid(row=2, column=0, columnspan=3, sticky="ew")

        rows = [
            ("Closest stations CSV:", "closest_csv", True),
            ("Scenario root:", "scenario_root", False),
            ("HCD input root:", "hcd_input", False),
            ("Output HCD base:", "out_hcd", False),
            ("Output DGPX base:", "out_dgpx", False),
            ("Index parameter analysis folder:", "out_index_analysis", False),
        ]

        self.vars = {}
        row_index = 0
        for label, name, is_file in rows:
            ttk.Label(form, text=label).grid(row=row_index, column=0, sticky="w", pady=(6, 0))
            var = tk.StringVar()
            ttk.Entry(form, textvariable=var).grid(row=row_index, column=1, sticky="ew", pady=(6, 0))
            ttk.Button(form, text="Browse...", style="Secondary.TButton",
                       command=lambda n=name, f=is_file: self._browse(n, f)).grid(
                row=row_index, column=2, sticky="ew", padx=(8, 0), pady=(6, 0)
            )
            self.vars[name] = var
            row_index += 1

        actions_shell = tk.Frame(self, bg=BG_CARD, highlightbackground=CARD_BORDER, highlightthickness=1, bd=0)
        actions_shell.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        actions = ttk.Frame(actions_shell, padding=(10, 8), style="Card.TFrame")
        actions.pack(fill="x")
        ttk.Button(actions, text="Save", command=self._on_save).pack(fill="x")
        ttk.Button(actions, text="Delete Generated Files", style="Danger.TButton",
                   command=self._on_delete_files).pack(fill="x", pady=(6, 0))

        for col in range(3):
            self.columnconfigure(col, weight=1)
            form.columnconfigure(col, weight=1 if col == 1 else 0)

        self._load_current()

    def _load_current(self):
        self.vars["closest_csv"].set(str(getattr(self.c, "city_csv_path", CLOSEST_STATIONS_CSV)))
        self.vars["scenario_root"].set(str(getattr(self.c, "scenario_root", SCENARIO_ROOT)))
        self.vars["hcd_input"].set(str(getattr(self.c, "hcd_input_dir", HCD_INPUT_DIR_GEN)))
        self.vars["out_hcd"].set(str(getattr(self.c, "output_base", OUTPUT_BASE_HCD)))
        self.vars["out_dgpx"].set(str(getattr(self.c, "output_base_dgpx", OUTPUT_BASE_DGPX)))
        self.vars["out_index_analysis"].set(str(getattr(self.c, "output_base_index_analysis", OUTPUT_BASE_INDEX_ANALYSIS)))

    def _browse(self, key, is_file: bool):
        if is_file:
            path = filedialog.askopenfilename(title="Select CSV", filetypes=[("CSV", "*.csv"), ("All files", "*")])
        else:
            path = filedialog.askdirectory(title="Select folder")
        if path:
            self.vars[key].set(path)

    def _validate_scenario_root(self, path: Path):
        p = Path(path)
        if not p.exists() or not p.is_dir():
            return False, f"Scenario root does not exist: {p}"
        for rcp in ("RCP45", "RCP85"):
            rp = p / rcp
            if not rp.exists() or not rp.is_dir():
                return False, f"Missing required directory: {rp.name} in scenario root"
            try:
                has_child = any(x.is_dir() for x in rp.iterdir())
            except Exception:
                has_child = False
            if not has_child:
                return False, f"{rp.name} appears empty (no scenario/model subfolders found)."
        return True, "OK"

    def _on_save(self):
        if self.c.job_manager.has_active_jobs():
            messagebox.showwarning("Settings", "Cannot change settings while jobs are queued or running.")
            return

        csv_path = Path(self.vars["closest_csv"].get().strip())
        scen_root = Path(self.vars["scenario_root"].get().strip())
        hcd_root = Path(self.vars["hcd_input"].get().strip())
        out_hcd = Path(self.vars["out_hcd"].get().strip())
        out_dgpx = Path(self.vars["out_dgpx"].get().strip())
        out_index_analysis = Path(self.vars["out_index_analysis"].get().strip())

        if not csv_path.exists() or not csv_path.is_file():
            messagebox.showerror("Settings", f"CSV file does not exist: {csv_path}")
            return

        ok, msg = self._validate_scenario_root(scen_root)
        if not ok:
            messagebox.showerror("Settings", msg)
            return

        if not hcd_root.exists() or not hcd_root.is_dir():
            messagebox.showerror("Settings", f"HCD input folder does not exist: {hcd_root}")
            return

        try:
            self.c.city_csv_path = csv_path
            self.c.city_list, self.c.city_to_info = load_city_and_station_data(csv_path)
        except Exception as exc:
            messagebox.showerror("Settings", f"Failed to reload city CSV: {exc}")
            return

        self.c.scenario_root = scen_root
        self.c.hcd_input_dir = hcd_root
        self.c.output_base = out_hcd
        self.c.output_base_dgpx = out_dgpx
        self.c.output_base_index_analysis = out_index_analysis
        self.c.hcd_input_dir_dgpx = out_hcd

        messagebox.showinfo("Settings", "Settings saved successfully.")
        self.c.show_frame("MainMenu")

    def _on_delete_files(self):
        if self.c.job_manager.has_active_jobs():
            messagebox.showwarning("Delete Generated Files", "Cannot delete generated files while jobs are queued or running.")
            return

        out_hcd = Path(self.vars["out_hcd"].get().strip())
        out_dgpx = Path(self.vars["out_dgpx"].get().strip())
        out_index_analysis = Path(self.vars["out_index_analysis"].get().strip())

        if not messagebox.askyesno(
            "Confirm Deletion",
            f"This will delete:\n"
            f"- All HCD folders except 'RCP45-Pre loaded' and 'RCP85-Pre loaded'\n"
            f"- All DGPX folders and files\n\n"
            f"- All index parameter analysis folders and files\n\n"
            f"HCD path: {out_hcd}\n"
            f"DGPX path: {out_dgpx}\n\n"
            f"Index analysis path: {out_index_analysis}\n\n"
            f"Are you sure?"
        ):
            return

        deleted_hcd = 0
        deleted_dgpx = 0
        deleted_analysis = 0
        errors = []

        if out_hcd.exists() and out_hcd.is_dir():
            try:
                for item in out_hcd.iterdir():
                    if item.is_dir() and item.name not in ("RCP45-Pre loaded", "RCP85-Pre loaded"):
                        import shutil
                        shutil.rmtree(item)
                        deleted_hcd += 1
            except Exception as exc:
                errors.append(f"HCD deletion error: {exc}")

        if out_dgpx.exists() and out_dgpx.is_dir():
            try:
                import shutil
                for item in out_dgpx.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item)
                        deleted_dgpx += 1
                    elif item.is_file():
                        item.unlink()
                        deleted_dgpx += 1
            except Exception as exc:
                errors.append(f"DGPX deletion error: {exc}")

        if out_index_analysis.exists() and out_index_analysis.is_dir():
            try:
                import shutil
                for item in out_index_analysis.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item)
                        deleted_analysis += 1
                    elif item.is_file():
                        item.unlink()
                        deleted_analysis += 1
            except Exception as exc:
                errors.append(f"Index analysis deletion error: {exc}")

        msg = (
            f"Deletion complete:\n"
            f"- HCD folders deleted: {deleted_hcd}\n"
            f"- DGPX items deleted: {deleted_dgpx}\n"
            f"- Index analysis items deleted: {deleted_analysis}"
        )
        if errors:
            msg += f"\n\nErrors:\n" + "\n".join(errors)
            messagebox.showwarning("Deletion Complete", msg)
        else:
            messagebox.showinfo("Deletion Complete", msg)


class App(tk.Tk):
    def __init__(self, project_name="Pavement Climate Generator"):
        super().__init__()
        self.title(project_name)
        self.geometry("920x620")
        self.minsize(920, 620)

        try:
            self.configure(bg=BG_APP)
            self._configure_styles()
        except Exception:
            pass

        self.city_list, self.city_to_info = load_city_and_station_data(CLOSEST_STATIONS_CSV)
        self.us_grid_list_path = US_GRID_LIST_XLSX.resolve()
        self.scenario_root = SCENARIO_ROOT.resolve()
        self.hcd_input_dir = HCD_INPUT_DIR_GEN.resolve()
        self.output_base = OUTPUT_BASE_HCD.resolve()
        self.output_base_dgpx = OUTPUT_BASE_DGPX.resolve()
        self.output_base_index_analysis = OUTPUT_BASE_INDEX_ANALYSIS.resolve()
        self.hcd_input_dir_dgpx = OUTPUT_BASE_HCD.resolve()

        self.list_subdirs = list_subdirs
        self.is_processing = False
        self.current_frame_name = None
        self.job_manager = JobManager(max_concurrent_jobs=2)

        container = ttk.Frame(self, style="App.TFrame")
        container.pack(fill="both", expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.frames = {}
        self.frames["SplashScreen"] = SplashScreen(container, self, project_name)
        self.frames["MainMenu"] = MainMenu(container, self)
        self.frames["GeneratorFrame"] = GeneratorFrame(container, self)
        self.frames["BatchFrame"] = BatchFrame(container, self)
        self.frames["DGPXBuilder"] = DGPXBuilderFrame(container, self)
        self.frames["DGPXBatch"] = DGPXBatchFrame(container, self)
        self.frames["IndexParameterFrame"] = IndexParameterFrame(container, self)
        self.frames["Settings"] = SettingsFrame(container, self)
        self.frames["JobsFrame"] = JobsFrame(container, self)
        for frame in self.frames.values():
            frame.grid(row=0, column=0, sticky="nsew")
        self.show_frame("SplashScreen")

        about_btn = ttk.Button(self, text="i", width=3, style="Secondary.TButton", command=self._show_about)
        about_btn.place(relx=1.0, rely=1.0, x=-12, y=-12, anchor="se")

        self.after(150, lambda: self._set_title_bar_color("369790"))
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _configure_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", background=BG_APP, foreground=ACCENT_DARK)
        style.configure("App.TFrame", background=BG_APP)
        style.configure("Card.TFrame", background=BG_CARD)
        style.configure("Panel.TFrame", background=BG_PANEL)
        style.configure("TLabel", background=BG_APP, foreground=ACCENT_DARK, font=("Segoe UI", 8))
        style.configure("Card.TLabel", background=BG_CARD, foreground=ACCENT_DARK)
        style.configure("TLabelframe", background=BG_APP, bordercolor=CARD_BORDER, relief="solid")
        style.configure("TLabelframe.Label", background=BG_APP, foreground=ACCENT_DARK, font=("Segoe UI", 9, "bold"))
        style.configure("Title.TLabel", background=BG_PANEL, foreground=ACCENT_DARK, font=("Segoe UI", 16, "bold"))
        style.configure("SplashTitle.TLabel", background=BG_APP, foreground=ACCENT_DARK, font=("Segoe UI", 19, "bold"))
        style.configure("Subtitle.TLabel", background=BG_PANEL, foreground=TEXT_MUTED, font=("Segoe UI", 9))
        style.configure("Muted.TLabel", background=BG_CARD, foreground=TEXT_MUTED, font=("Segoe UI", 8))
        style.configure("PanelMuted.TLabel", background=BG_PANEL, foreground=TEXT_MUTED, font=("Segoe UI", 8))
        style.configure("SectionTitle.TLabel", background=BG_CARD, foreground=ACCENT_DARK, font=("Segoe UI", 10, "bold"))
        style.configure("ScreenTitle.TLabel", background=BG_APP, foreground=ACCENT_DARK, font=("Segoe UI", 12, "bold"))
        style.configure("Footnote.TLabel", background=BG_APP, foreground=TEXT_SOFT, font=("Segoe UI", 7))

        style.configure(
            "TButton",
            padding=(8, 6),
            relief="flat",
            borderwidth=0,
            background=ACCENT,
            foreground="white",
            focusthickness=0,
            font=("Segoe UI", 8, "bold"),
        )
        style.map(
            "TButton",
            background=[("active", ACCENT_HOVER), ("pressed", ACCENT_DARK), ("disabled", "#c6d0c9")],
            foreground=[("disabled", "#f4f7f8")],
        )

        style.configure("MainMenu.TButton", padding=(10, 8), font=("Segoe UI", 9, "bold"))
        style.configure(
            "MainMenu.Secondary.TButton",
            padding=(10, 8),
            font=("Segoe UI", 9),
            background="#dde5df",
            foreground=ACCENT_DARK,
        )
        style.map(
            "MainMenu.Secondary.TButton",
            background=[("active", "#d3ddd6"), ("pressed", "#c7d1ca")],
            foreground=[("active", ACCENT_DARK)],
        )

        style.configure(
            "Secondary.TButton",
            background="#dde5df",
            foreground=ACCENT_DARK,
            font=("Segoe UI", 8),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", "#d3ddd6"), ("pressed", "#c7d1ca")],
            foreground=[("active", ACCENT_DARK)],
        )

        style.configure("Danger.TButton", background="#b76969", foreground="white", font=("Segoe UI", 8, "bold"))
        style.map("Danger.TButton", background=[("active", "#b45050"), ("pressed", "#9f4545")])

        style.configure("TEntry", fieldbackground=FIELD_BG, bordercolor=CARD_BORDER, lightcolor=CARD_BORDER, darkcolor=CARD_BORDER)
        style.configure("TCombobox", fieldbackground=FIELD_BG, bordercolor=CARD_BORDER, lightcolor=CARD_BORDER, darkcolor=CARD_BORDER)
        style.configure("Accent.Horizontal.TProgressbar", troughcolor="#dde4de", background=ACCENT, bordercolor="#dde4de", lightcolor=ACCENT, darkcolor=ACCENT)

    def show_frame(self, name: str):
        self.current_frame_name = name
        self.frames[name].tkraise()
        on_show = getattr(self.frames[name], "on_show", None)
        if callable(on_show):
            on_show()

    def _on_closing(self):
        if self.current_frame_name != "MainMenu":
            messagebox.showwarning(
                "Close Restricted",
                "Return to the home screen before closing the application.",
            )
            return

        if self.job_manager.has_active_jobs():
            messagebox.showwarning(
                "Processing in Progress",
                "Cannot close the application while jobs are queued or running.\n"
                "Please wait for them to complete or cancel them first.",
            )
            return
        self.destroy()

    def set_busy_cursor(self):
        self.config(cursor="wait")
        self.update()

    def set_normal_cursor(self):
        self.config(cursor="")
        self.update()

    def _set_title_bar_color(self, hex_rgb: str = "369790"):
        try:
            import ctypes

            r = int(hex_rgb[0:2], 16)
            g = int(hex_rgb[2:4], 16)
            b = int(hex_rgb[4:6], 16)
            colorref = (b << 16) | (g << 8) | r

            DWMWA_CAPTION_COLOR = 35
            DWMWA_TEXT_COLOR = 36

            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_CAPTION_COLOR, ctypes.byref(ctypes.c_int(colorref)), ctypes.sizeof(ctypes.c_int)
            )
            white = 0x00FFFFFF
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_TEXT_COLOR, ctypes.byref(ctypes.c_int(white)), ctypes.sizeof(ctypes.c_int)
            )
        except Exception:
            pass

    def _show_about(self):
        about_text = (
            "Pavement Climate Generator\n\n"
            "Generating hourly climate/HCD inputs made simple.\n\n"
            "Created by - Dr. Shane Underwood and Shourya Nanda Kumar"
        )
        try:
            messagebox.showinfo("About", about_text)
        except Exception:
            tk.messagebox.showinfo("About", about_text)


if __name__ == "__main__":
    App().mainloop()

# python -m PyInstaller --onefile --noconsole --add-data "assets;assets" main_screen.py

import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
import re
from queue import Queue, Empty

from main_v3 import calculate_temperature
from multiselect_dropdown import MultiSelectDropdown
from operation_control import OperationCancelledError


def list_subdirs(root: Path):
    root = Path(root).expanduser()
    return sorted([p.name for p in root.iterdir() if p.is_dir()]) if root.exists() else []


def _safe(name: str) -> str:
    s = (name or "").strip().replace(" ", "_")
    return re.sub(r"[^A-Za-z0-9._-]+", "", s)

def _station_from_hcd_filename(path: Path) -> str:
    """
    Extract a station token from an HCD filename.
    Accepts names like: 'PHOENIX_INTL_US.hcd' or 'PHOENIX_INTL.hcd'
    Returns 'PHOENIX_INTL' for both.
    """
    stem = path.stem  # e.g., 'PHOENIX_INTL_US' or 'PHOENIX_INTL'
    if stem.endswith("_US"):
        stem = stem[:-3]
    return _safe(stem)


def build_hcd_paths(output_base: Path, city: str, scenario: str, model: str,
                    station_token: str, hcd_input_dir: Path | None = None,
                    hcd_in_override: Path | None = None):
    """
    If hcd_in_override is given, use it directly; otherwise build from station in hcd_input_dir.
    Output name is: output-{model}_{scenario}_tasmax.hcd  (in <output_base>/<city>/<scenario>/<model>/)
    """
    if hcd_in_override:
        hcd_in = Path(hcd_in_override).resolve()
    else:
        hcd_input_dir = Path(hcd_input_dir) if hcd_input_dir else Path.cwd()
        hcd_in = (hcd_input_dir / f"{station_token}_US.hcd").resolve()

    # --- derive output filename from input ---
    stem = hcd_in.stem  # e.g. "136149_US"
    if stem.endswith("_US"):
        stem = stem[:-3]  # drop "_US"
    out_name = f"{stem}.hcd"   # e.g. "136149.hcd"

    # model_token    = _safe(model)
    # scenario_token = _safe(scenario)
    # out_name = f"output-{model_token}_{scenario_token}_tasmax.hcd"

    # hcd_out = (Path(output_base) / city / scenario / model / out_name).resolve()
    hcd_out = (Path(output_base) / scenario / model / out_name).resolve()
    hcd_out.parent.mkdir(parents=True, exist_ok=True)
    return hcd_in, hcd_out


def _cleanup_cancelled_output(path: Path):
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass

    current = path.parent
    while current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        except Exception:
            break
        current = current.parent


def run_one_generation(city: str,
                       info: dict | None,
                       scenario: str,
                       model: str,
                       scenario_root: Path,
                       output_base: Path,
                       start_year: int = 2025,
                       end_year: int = 2099,
                       hcd_input_dir: Path | None = None,
                       overwrite: bool = False,
                       hcd_in_override: Path | None = None,
                       city_label_override: str | None = None,
                       progress_cb=None,
                       cancel_event=None) -> str:
    """
    If hcd_in_override is provided, we:
      - use that file as input
      - derive station token from the filename
      - use city_label_override (or station token) as the <city> folder
    Otherwise, we require 'info' with a 'station' field (classic city flow).
    """
    if hcd_in_override:
        hcd_in_path = Path(hcd_in_override)
        if not hcd_in_path.exists():
            raise FileNotFoundError(f"HCD file not found: {hcd_in_path}")
        station = _station_from_hcd_filename(hcd_in_path)
        city_folder = city_label_override or station
    else:
        if not info or not info.get("station"):
            raise ValueError(f"No closest station for city: {city}")
        station = _safe(info["station"])
        city_folder = city  # original city folder when running by city

    model_base = (Path(scenario_root) / scenario / model).resolve()
    hcd_in, hcd_out = build_hcd_paths(output_base,
                                      city=city_folder,
                                      scenario=scenario,
                                      model=model,
                                      station_token=station,
                                      hcd_input_dir=hcd_input_dir,
                                      hcd_in_override=hcd_in_override)

    if hcd_out.exists() and not overwrite:
        return str(hcd_out)  # skip reprocessing

    # If we have station info (from 'info' or derived from override), pass its coordinates
    target_lon = None
    target_lat = None
    if hcd_in_override:
        # try to find station coords from provided info dict if available
        if info and info.get("station_lat") and info.get("station_lon"):
            target_lat = info.get("station_lat")
            target_lon = info.get("station_lon")
    else:
        # classic city flow: info contains lat/lon for the city/station
        if info:
            try:
                target_lat = float(info.get("lat")) if info.get("lat") is not None else None
                target_lon = float(info.get("lon")) if info.get("lon") is not None else None
            except Exception:
                target_lat = None; target_lon = None

    try:
        calculate_temperature(
            hcd_in_path=str(hcd_in),
            hcd_out_path=str(hcd_out),
            MODEL_BASE=model_base,
            MODEL_NAME=model,
            SCENARIO=scenario,
            TARGET_START_YEAR=start_year,
            TARGET_END_YEAR=end_year,
            TARGET_LONGITUDE=target_lon,
            TARGET_LATITUDE=target_lat,
            PROGRESS_CB=progress_cb,
            cancel_event=cancel_event,
        )
    except OperationCancelledError:
        _cleanup_cancelled_output(hcd_out)
        raise
    return str(hcd_out)


class GeneratorFrame(ttk.Frame):
    """
    Simple generator UI:
      - City (Combobox)
      - Closest Station (readonly)
      - Latitude / Longitude (readonly)
      - Scenario (Combobox)
      - Model (Combobox)
      - Generate (Button)
    """
    def __init__(self, parent, controller):
        super().__init__(parent, padding=16)
        self.c = controller

        # Header
        top = ttk.Frame(self)
        top.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 12))
        self.back_btn = ttk.Button(top, text="← Back", command=lambda: self.c.show_frame("MainMenu"))
        self.back_btn.pack(side="left")
        ttk.Label(top, text="Create Future Weather File", font=("Segoe UI", 14, "bold")).pack(side="left", padx=12)

        # City
        ttk.Label(self, text="City:").grid(row=1, column=0, sticky="w", padx=(0,6), pady=6)
        # self.station_var = tk.StringVar()
        self._all_cities = list(self.c.city_list)  # master list
        self.city_var = tk.StringVar()
        self.city_dd = MultiSelectDropdown(
            self,
            placeholder="Select city",
            width=42,
            searchable=True,
            single_select=True,
            command=self._on_city_selected,
        )
        self.city_dd.grid(row=1, column=1, columnspan=3, sticky="ew", pady=6)
        self.city_dd.set_items(self._all_cities)

        # Closest Station (readonly)
        ttk.Label(self, text="Closest Station:").grid(row=2, column=0, sticky="w", padx=(0,6), pady=6)
        self.station_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.station_var, state="readonly").grid(row=2, column=1, columnspan=3, sticky="ew", pady=6)

        # Latitude / Longitude (readonly)
        ttk.Label(self, text="Latitude:").grid(row=3, column=0, sticky="w", padx=(0,6), pady=6)
        self.lat_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.lat_var, state="readonly").grid(row=3, column=1, sticky="w", pady=6)

        ttk.Label(self, text="Longitude:").grid(row=3, column=2, sticky="w", padx=(12,6), pady=6)
        self.lon_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.lon_var, state="readonly").grid(row=3, column=3, sticky="w", pady=6)

        # Scenario / Model
        ttk.Label(self, text="Scenario:").grid(row=5, column=0, sticky="w", padx=(0,6), pady=6)
        self.scenario_var = tk.StringVar()
        self.scenario_cb  = ttk.Combobox(self, textvariable=self.scenario_var, state="readonly",
                                         values=list_subdirs(self.c.scenario_root), width=26, height=12)
        self.scenario_cb.grid(row=5, column=1, sticky="ew", pady=6)
        self.scenario_cb.bind("<<ComboboxSelected>>", self._on_scenario_selected)

        ttk.Label(self, text="Model:").grid(row=5, column=2, sticky="w", padx=(12,6), pady=6)
        self.model_var = tk.StringVar()
        self.model_cb  = ttk.Combobox(self, textvariable=self.model_var, state="readonly", width=26, height=12)
        self.model_cb.grid(row=5, column=3, sticky="ew", pady=6)

        # Generate
        self.generate_btn = ttk.Button(self, text="Generate", command=self._on_generate)
        self.generate_btn.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(12,0))
        self.stop_btn = ttk.Button(self, text="Stop", command=self._on_stop, state="disabled")
        self.stop_btn.grid(row=6, column=3, sticky="ew", pady=(12,0), padx=(8, 0))

        for c in range(4):
            self.columnconfigure(c, weight=1)

        # Progress (single run)
        self.pb_years = ttk.Progressbar(self, mode="determinate", maximum=1)  # we will set max at run time
        self.pb_years.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(6,0))
        self.pb_label = ttk.Label(self, text="")
        self.pb_label.grid(row=7, column=3, sticky="e", pady=(6,0))
        self._job_id = None
        self._job_notified = False

    def refresh_dropdowns(self):
        """Refresh scenario and city dropdowns from current controller state."""
        self._all_cities = list(self.c.city_list)
        self.city_dd.set_items(self._all_cities, selected_values=self.city_dd.get_selected_values())
        self.scenario_cb["values"] = list_subdirs(self.c.scenario_root)

    def _disable_all(self):
        """Disable all stateful children and remember original states."""
        self._saved_states = []
        for w in self.winfo_children():
            try:
                st = w.cget("state")     # raises if widget has no 'state'
                self._saved_states.append((w, st))
                w.configure(state="disabled")
            except Exception:
                pass

    def _restore_states(self):
        """Restore the original states saved by _disable_all()."""
        for w, st in getattr(self, "_saved_states", []):
            try:
                w.configure(state=st)
            except Exception:
                pass
        self._saved_states = []

    def _on_city_selected(self, event=None):
        selected = self.city_dd.get_selected_values()
        city = selected[0] if selected else ""
        self.city_var.set(city)
        info = self.c.city_to_info.get(city)
        if not info:
            self.station_var.set(""); self.lat_var.set(""); self.lon_var.set("")
            return
        self.station_var.set(info["station"])
        self.lat_var.set(info["lat"]); self.lon_var.set(info["lon"])

    def _on_scenario_selected(self, event=None):
        s = self.scenario_var.get().strip()
        models = list_subdirs(self.c.scenario_root / s) if s else []
        # special mapping
        special = [("Hot", "GFDL-CM3"), ("Medium", "HadGEM2-CC"), ("Cold", "MRI-CGCM3")]
        display = [f"{label} — {name}" for label, name in special]
        display.append("-----")
        display.extend(models)
        self._special_models = {f"{label} — {name}": name for label, name in special}
        self.model_cb["values"] = display
        self.model_var.set(display[0] if display else "")

        # validate presence under RCP45 and RCP85
        missing = {}
        for rcp in ("RCP45", "RCP85"):
            rp = Path(self.c.scenario_root) / rcp
            ms = [name for _, name in special if not (rp / name).exists()]
            if ms:
                missing[rcp] = ms
        if missing:
            msgs = [f"{rcp}: {', '.join(names)}" for rcp, names in missing.items()]
            messagebox.showwarning("Model validation", "Special models missing:\n" + "\n".join(msgs))

    def _on_generate(self):
        selected = self.city_dd.get_selected_values()
        city = selected[0].strip() if selected else ""
        scen = self.scenario_var.get().strip()
        raw_model = self.model_var.get().strip()
        # resolve special label to actual model name
        model = getattr(self, '_special_models', {}).get(raw_model, raw_model)
        info = self.c.city_to_info.get(city)

        if not city or not info:
            messagebox.showerror("City", "Select a valid city."); return
        if not scen:
            messagebox.showerror("Scenario", "Select a scenario."); return
        if not model:
            messagebox.showerror("Model", "Select a model."); return

         # progress setup
        start_year, end_year = 2025, 2099
        total_years = end_year - start_year + 1
        self.pb_years["maximum"] = total_years
        self.pb_years["value"] = 0
        self.pb_label.config(text=f"0 / {total_years} years")

        def runner(ctx):
            progress = [0]

            def progress_cb():
                progress[0] += 1
                ctx.set_progress(progress[0], total_years, f"{city} / {scen} / {model}")

            ctx.set_progress(0, total_years, f"{city} / {scen} / {model}")
            out_path = run_one_generation(
                city=city,
                info=info,
                scenario=scen,
                model=model,
                scenario_root=self.c.scenario_root,
                output_base=self.c.output_base,
                hcd_input_dir=self.c.hcd_input_dir,
                start_year=start_year,
                end_year=end_year,
                progress_cb=progress_cb,
                cancel_event=ctx.cancel_event,
            )
            ctx.log(f"Generated: {out_path}")
            return str(out_path)

        self._job_id = self.c.job_manager.submit(
            title=f"HCD Generate: {city} / {scen} / {model}",
            kind="hcd-single",
            runner=runner,
        )
        self._job_notified = False
        self.stop_btn.config(state="normal")
        self.after(100, self._poll_job)

    def _poll_job(self):
        if self._job_id is None:
            return

        snapshot = self.c.job_manager.get_job_snapshot(self._job_id)
        if snapshot is None:
            return

        self.pb_years["maximum"] = max(1, int(snapshot["progress_total"]))
        self.pb_years["value"] = min(int(snapshot["progress_current"]), int(self.pb_years["maximum"]))
        if snapshot["status"] in {"queued", "running", "cancelling"}:
            self.pb_label.config(
                text=f"{snapshot['progress_current']} / {snapshot['progress_total']} years - {snapshot['status']}"
            )
            self.stop_btn.config(state="normal")
            self.after(200, self._poll_job)
            return

        self.stop_btn.config(state="disabled")
        if not self._job_notified and self.c.current_frame_name == "GeneratorFrame":
            if snapshot["status"] == "completed":
                messagebox.showinfo("Done", f"Generated:\n{snapshot['result']}")
            elif snapshot["status"] == "cancelled":
                messagebox.showinfo("Stopped", "Generation was stopped.")
            elif snapshot["status"] == "failed":
                messagebox.showerror("Generation failed", snapshot["error"] or "Unknown error")
            self._job_notified = True

        if snapshot["status"] == "completed":
            self.pb_label.config(text="Completed")
        elif snapshot["status"] == "cancelled":
            self.pb_label.config(text="Cancelled")
        else:
            self.pb_label.config(text="Failed")

    def _on_stop(self):
        if self._job_id is not None:
            self.c.job_manager.cancel(self._job_id)
            self.stop_btn.config(state="disabled")
            self.pb_label.config(text="Stopping...")

    def _iter_all_children(self, root=None):
        """Yield all descendants (breadth-first) including root's children."""
        root = self if root is None else root
        for w in root.winfo_children():
            yield w
            yield from self._iter_all_children(w)

    def _supports_ttk_state(self, w):
        # ttk widgets have a .state() method
        return hasattr(w, "state")

    def _supports_tk_state(self, w):
        # classic Tk widgets expose 'state' in configurable keys (e.g., Entry, Text, Button)
        try:
            return "state" in w.keys()
        except Exception:
            return False

    def _get_state(self, w):
        """Return a restorable state token for the widget, or None if unsupported."""
        try:
            if self._supports_ttk_state(w):
                # ttk returns a tuple like ('!disabled', 'focus')
                return tuple(w.state())
            if self._supports_tk_state(w):
                return w.cget("state")  # e.g., 'normal' / 'disabled' / 'readonly'
        except Exception:
            pass
        return None

    def _set_state(self, w, saved):
        """Restore a previously saved state token to the widget."""
        try:
            if saved is None:
                return
            if self._supports_ttk_state(w):
                # clear to default then re-apply saved tuple
                # First remove disabled, then add whatever was saved
                w.state(())                  # clear all
                if saved:
                    w.state(saved)          # re-apply tuple like ('!disabled',)
                return
            if self._supports_tk_state(w):
                w.configure(state=saved)
        except Exception:
            pass

    def _disable_widget(self, w):
        """Disable a single widget using the right API."""
        try:
            if self._supports_ttk_state(w):
                w.state(('disabled',))
            elif self._supports_tk_state(w):
                w.configure(state='disabled')
        except Exception:
            pass

    def _disable_all(self):
        """Disable all stateful descendants and remember original states."""
        self._saved_states = []
        for w in self._iter_all_children():
            saved = self._get_state(w)
            if saved is not None:
                self._saved_states.append((w, saved))
                self._disable_widget(w)

    def _restore_states(self):
        """Restore the original states saved by _disable_all()."""
        for w, saved in getattr(self, "_saved_states", []):
            self._set_state(w, saved)
        self._saved_states = []

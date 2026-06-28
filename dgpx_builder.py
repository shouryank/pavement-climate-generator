import re
import tempfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from dgpx_from_hcd import rebuild
from multiselect_dropdown import MultiSelectDropdown
from operation_control import OperationCancelledError


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "", (s or "").strip())

def _station_from_info(info: dict) -> str:
    # Your controller.city_to_info entries typically hold 'station' and lat/lon/state
    st = (info.get("station") or "").strip()
    return re.sub(r"[^A-Za-z0-9_-]+", "", st)

def _update_climate_station_block(dgpx_text: str, *, city: str, state: str, lat: float, lon: float,
                                  elevation: float, station_id: str, country: str = "US") -> str:
    """
    Replace the inner <ClimateStation>...</ClimateStation> block under <climateStation>.
    If the block doesn't exist, insert it.
    """
    city = _safe(city)
    state = _safe(state)
    station_id = re.sub(r"[^A-Za-z0-9_-]+", "", station_id)

    block = (
        "    <climateStation>\n"
        "      <ClimateStation>\n"
        f"        <Elevation>{elevation}</Elevation>\n"
        f"        <city>{city}</city>\n"
        f"        <Country>{country}</Country>\n"
        f"        <Latitude>{lat}</Latitude>\n"
        f"        <Longitude>{lon}</Longitude>\n"
        f"        <state>{state}</state>\n"
        f"        <stationId>{station_id}</stationId>\n"
        "      </ClimateStation>"
    )

    # Try to replace an existing <climateStation>...</climateStation> (case-insensitive)
    pat = re.compile(r"<\s*climateStation\s*>.*?</\s*climateStation\s*>",
                     flags=re.IGNORECASE | re.DOTALL)
    if pat.search(dgpx_text):
        return pat.sub(block, dgpx_text, count=1)

    # If not found, insert after <climateData> if present; else prepend.
    m_cd = re.search(r"<\s*climateData\s*>", dgpx_text, flags=re.IGNORECASE)
    if m_cd:
        idx = m_cd.end()
        return dgpx_text[:idx] + "\n" + block + dgpx_text[idx:]
    else:
        return block + dgpx_text


class DGPXBuilderFrame(ttk.Frame):
    """
    Button 3 screen:
      - Pick input .dgpx
      - Pick City / Scenario / Model
      - Enter Elevation and Output filename
      - Preview final output path (DGPX root / city / scenario / model / filename)
      - Build: update <ClimateStation> + regenerate <ClimateRecord> from the matched HCD
    """
    def __init__(self, parent, controller):
        super().__init__(parent, padding=16)
        self.c = controller  # has: city_list, city_to_info, scenario_root, hcd_input_dir_dgpx, output_base_dgpx, etc.

        # ---------- Header ----------
        top = ttk.Frame(self)
        top.grid(row=0, column=0, columnspan=6, sticky="ew", pady=(0, 12))
        self.back_btn = ttk.Button(top, text="← Back", command=lambda: self.c.show_frame("MainMenu"))
        self.back_btn.pack(side="left")
        ttk.Label(top, text="DGPX Builder", font=("Segoe UI", 14, "bold")).pack(side="left", padx=12)

        # ---------- DGPX input file ----------
        ttk.Label(self, text="DGPX file:").grid(row=1, column=0, sticky="w")
        self.dgpx_in_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.dgpx_in_var).grid(row=1, column=1, columnspan=4, sticky="ew")
        ttk.Button(self, text="Browse...", command=self._browse_dgpx).grid(row=1, column=5, sticky="ew")

        # ---------- City (editable combobox with type-to-filter) ----------
        ttk.Label(self, text="City:").grid(row=2, column=0, sticky="w", pady=(8,0))
        self.city_var = tk.StringVar()
        self._all_cities = list(self.c.city_list)
        self.city_dd = MultiSelectDropdown(
            self,
            placeholder="Select city",
            width=44,
            searchable=True,
            single_select=True,
            command=self._on_city_selected,
        )
        self.city_dd.grid(row=2, column=1, sticky="ew", pady=(8,0))
        self.city_dd.set_items(self._all_cities)

        # ---------- Scenario ----------
        ttk.Label(self, text="Scenario:").grid(row=2, column=2, sticky="w", padx=(12,0), pady=(8,0))
        self.scenario_var = tk.StringVar()
        self._all_scenarios = self.c.list_subdirs(self.c.scenario_root)
        self.scenario_cb = ttk.Combobox(self, textvariable=self.scenario_var, state="normal",
                                        values=self._all_scenarios, width=26, height=12)
        self.scenario_cb.grid(row=2, column=3, sticky="ew", pady=(8,0))
        self.scenario_cb.bind("<KeyRelease>", lambda e: self._filter_cb_values(self.scenario_cb, self._all_scenarios, self.scenario_var, e))
        self.scenario_cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_models())

        # ---------- Model (depends on Scenario) ----------
        ttk.Label(self, text="Model:").grid(row=2, column=4, sticky="w", padx=(12,0), pady=(8,0))
        self.model_var = tk.StringVar()
        self._all_models = []
        self.model_cb = ttk.Combobox(self, textvariable=self.model_var, state="normal", values=self._all_models, width=26, height=12)
        self.model_cb.grid(row=2, column=5, sticky="ew", pady=(8,0))
        self.model_cb.bind("<KeyRelease>", lambda e: self._filter_cb_values(self.model_cb, self._all_models, self.model_var, e))

        # ---------- Elevation & Output filename ----------
        ttk.Label(self, text="Elevation (ft):").grid(row=3, column=0, sticky="w", pady=(8,0))
        self.elev_var = tk.StringVar(value="0.0")
        ttk.Entry(self, textvariable=self.elev_var, width=12).grid(row=3, column=1, sticky="w", pady=(8,0))

        ttk.Label(self, text="Output file name:").grid(row=3, column=2, sticky="w", padx=(12,0), pady=(8,0))
        self.out_name_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.out_name_var).grid(row=3, column=3, columnspan=3, sticky="ew", pady=(8,0))

        # ---------- Output path preview ----------
        ttk.Label(self, text="Will save to:").grid(row=4, column=0, sticky="w", pady=(8,0))
        self.preview_var = tk.StringVar(value="")
        ttk.Entry(self, textvariable=self.preview_var, state="readonly").grid(row=4, column=1, columnspan=5, sticky="ew", pady=(8,0))

        # ---------- Build button ----------
        self.build_btn = ttk.Button(self, text="Build DGPX", command=self._on_build)
        self.build_btn.grid(row=5, column=0, columnspan=5, sticky="ew", pady=(12,0))
        self.stop_btn = ttk.Button(self, text="Stop", command=self._on_stop, state="disabled")
        self.stop_btn.grid(row=5, column=5, sticky="ew", pady=(12,0), padx=(8, 0))

        # weights
        for c in range(6):
            self.columnconfigure(c, weight=1)

        # auto-preview when fields change
        for var in (self.city_var, self.scenario_var, self.model_var, self.out_name_var):
            var.trace_add("write", lambda *_: self._update_preview())
        self._job_id = None
        self._job_notified = False

    def refresh_dropdowns(self):
        """Refresh city and scenario dropdowns from current controller state."""
        self._all_cities = list(self.c.city_list)
        self.city_dd.set_items(self._all_cities, selected_values=self.city_dd.get_selected_values())
        self._all_scenarios = self.c.list_subdirs(self.c.scenario_root)
        self.scenario_cb["values"] = self._all_scenarios
        self._refresh_models()

    def _on_city_selected(self):
        selected = self.city_dd.get_selected_values()
        self.city_var.set(selected[0] if selected else "")
        self._update_preview()

    # -------- type-to-filter combobox (no focus stealing) --------
    def _filter_cb_values(self, cb: ttk.Combobox, all_values, var: tk.StringVar, event=None):
        if event and event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        typed = (var.get() or "").strip().lower()
        if not typed:
            cb["values"] = all_values; return
        starts = [x for x in all_values if x.lower().startswith(typed)]
        contains = [x for x in all_values if typed in x.lower() and x not in starts]
        cb["values"] = starts + contains
        # keep focus/caret; do not auto-open

    def _browse_dgpx(self):
        p = filedialog.askopenfilename(title="Select a .dgpx file", filetypes=[("DGPX", "*.dgpx"), ("All files", "*.*")])
        if p:
            self.dgpx_in_var.set(p)
            # default output name suggestion
            stem = Path(p).stem
            self.out_name_var.set(f"{stem}_new.dgpx")
            self._update_preview()

    def _refresh_models(self):
        scen = (self.scenario_var.get() or "").strip()
        models = self.c.list_subdirs(self.c.scenario_root / scen) if scen else []
        # special mapping
        special = [("Hot", "GFDL-CM3"), ("Medium", "HadGEM2-CC"), ("Cold", "MRI-CGCM3")]
        display = [f"{label} — {name}" for label, name in special]
        display.append("-----")
        display.extend(models)
        self._special_models = {f"{label} — {name}": name for label, name in special}
        self._special_model_names = {name for _, name in special}  # just the model names
        self._all_models = models
        self.model_cb["values"] = display
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
        # Try to select first display entry if current selection is invalid
        if display and self.model_var.get() not in display:
            self.model_var.set(display[0])
        self._update_preview()

    def _build_hcd_path(self, city: str, scenario: str, model: str, station: str) -> Path:
        # Use your configured HCD input root; controller has hcd_input_dir_dgpx
        # For special models (GFDL-CM3, HadGEM2-CC, MRI-CGCM3), use the "Pre loaded" folder structure
        special_models = getattr(self, '_special_model_names', {})
        if model in special_models:
            # Path: hcd_input_dir_dgpx / (scenario + "-Pre loaded") / model / station.hcd
            return (Path(self.c.hcd_input_dir_dgpx) / f"{scenario}-Pre loaded" / model / f"{station}.hcd").resolve()
        else:
            # Normal path: hcd_input_dir_dgpx / city / scenario / model / station.hcd
            return (Path(self.c.hcd_input_dir_dgpx) / city / scenario / model / f"{station}.hcd").resolve()

    def _update_preview(self):
        city = (self.city_var.get() or "").strip()
        scen = (self.scenario_var.get() or "").strip()
        raw_model = (self.model_var.get() or "").strip()
        # Resolve special label to actual model name
        model = getattr(self, '_special_models', {}).get(raw_model, raw_model)
        out_name = (self.out_name_var.get() or "").strip() or "output.dgpx"
        # DGPX output root: city / scenario / model / out_name
        preview = (Path(self.c.output_base_dgpx) / city / scen / model / out_name).resolve()
        self.preview_var.set(str(preview))

    def _on_build(self):
        try:
            # Collect inputs
            dgpx_in = Path(self.dgpx_in_var.get().strip())
            if not dgpx_in.exists():
                messagebox.showerror("DGPX", "Pick a valid .dgpx file."); return

            city = (self.city_var.get() or "").strip()
            scen = (self.scenario_var.get() or "").strip()
            raw_model = (self.model_var.get() or "").strip()
            # resolve special label to actual model name
            model = getattr(self, '_special_models', {}).get(raw_model, raw_model)
            if not city or not scen or not raw_model:
                messagebox.showerror("Inputs", "Pick City, Scenario, and Model."); return

            info = self.c.city_to_info.get(city)
            if not info or not info.get("station"):
                messagebox.showerror("City mapping", f"No station mapping for city: {city}"); return

            # Numbers
            try:
                elevation = float(self.elev_var.get().strip())
            except:
                messagebox.showerror("Elevation", "Enter a valid elevation (number)."); return

            # HCD input path
            station = _station_from_info(info)
            hcd_path = self._build_hcd_path(city, scen, model, station)
            if not hcd_path.exists():
                messagebox.showerror("HCD", f"HCD not found:\n{hcd_path} \n\nPls generate the HCD file first by using the 'Create Future Weather File' Button"); return

            # Output path
            out_name = (self.out_name_var.get() or "").strip() or f"{dgpx_in.stem}.dgpx"
            dgpx_out = (Path(self.c.output_base_dgpx) / city / scen / model / out_name).resolve()
            dgpx_out.parent.mkdir(parents=True, exist_ok=True)

            # Read input .dgpx, update <ClimateStation> block with City/State/Lat/Lon/Elevation/Station
            dgpx_text = dgpx_in.read_text(encoding="utf-8", errors="ignore")
            lat, lon = info.get("lat"), info.get("lon")
            state = info.get("state", "")
            if lat is None or lon is None:
                messagebox.showerror("City mapping", f"Missing lat/lon in mapping for: {city}"); return

            updated = _update_climate_station_block(
                dgpx_text,
                city=city, state=state, lat=float(lat), lon=float(lon),
                elevation=elevation, station_id=station, country="US"
            )

            def runner(ctx):
                ctx.set_progress(0, 1, f"{city} / {scen} / {model}")
                ctx.check_cancelled()
                with tempfile.NamedTemporaryFile(suffix=".dgpx", delete=False) as tmp:
                    tmp_path = Path(tmp.name)
                    tmp.write(updated.encode("utf-8", errors="ignore"))

                try:
                    ctx.check_cancelled()
                    rebuild(str(tmp_path), str(hcd_path), str(dgpx_out))
                finally:
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except Exception:
                        pass

                ctx.set_progress(1, 1, f"{city} / {scen} / {model}")
                ctx.log(f"Saved: {dgpx_out}")
                return str(dgpx_out)

            self._job_id = self.c.job_manager.submit(
                title=f"DGPX Build: {city} / {scen} / {model}",
                kind="dgpx-single",
                runner=runner,
            )
            self._job_notified = False
            self.stop_btn.config(state="normal")
            self.after(100, self._poll_job)

        except Exception as e:
            messagebox.showerror("Build failed", str(e))

    def _on_stop(self):
        if self._job_id is not None:
            self.c.job_manager.cancel(self._job_id)
            self.stop_btn.config(state="disabled")

    def _poll_job(self):
        if self._job_id is None:
            return

        snapshot = self.c.job_manager.get_job_snapshot(self._job_id)
        if snapshot is None:
            return

        if snapshot["status"] in {"queued", "running", "cancelling"}:
            self.stop_btn.config(state="normal")
            self.after(200, self._poll_job)
            return

        self.stop_btn.config(state="disabled")
        if not self._job_notified and self.c.current_frame_name == "DGPXBuilder":
            if snapshot["status"] == "completed":
                messagebox.showinfo("Done", f"Saved:\n{snapshot['result']}")
            elif snapshot["status"] == "cancelled":
                messagebox.showinfo("Stopped", "DGPX build was stopped.")
            else:
                messagebox.showerror("Build failed", snapshot["error"] or "Unknown error")
            self._job_notified = True

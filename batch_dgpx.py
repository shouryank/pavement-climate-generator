import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
import queue
from tempfile import NamedTemporaryFile

from dgpx_builder import _station_from_info, _update_climate_station_block
from dgpx_from_hcd import rebuild
from multiselect_dropdown import MultiSelectDropdown
from operation_control import OperationCancelledError


class DGPXBatchFrame(ttk.Frame):
    """Batch builder for DGPX files."""

    def __init__(self, parent, controller):
        super().__init__(parent, padding=16)
        self.c = controller

        top = ttk.Frame(self)
        top.grid(row=0, column=0, columnspan=8, sticky="ew", pady=(0, 12))
        self.back_btn = ttk.Button(top, text="Back", command=lambda: self.c.show_frame("MainMenu"))
        self.back_btn.pack(side="left")
        ttk.Label(top, text="DGPX Batch Builder", font=("Segoe UI", 14, "bold")).pack(side="left", padx=12)

        dgpx_box = ttk.LabelFrame(self, text="Input DGPX (single file)")
        dgpx_box.grid(row=1, column=0, columnspan=8, sticky="ew")
        self.dgpx_in_var = tk.StringVar()
        ttk.Entry(dgpx_box, textvariable=self.dgpx_in_var).grid(row=0, column=0, sticky="ew", padx=(6, 6), pady=6)
        ttk.Button(dgpx_box, text="Browse...", command=self._browse_dgpx).grid(row=0, column=1, padx=(0, 6), pady=6)
        dgpx_box.columnconfigure(0, weight=1)

        alt_box = ttk.Frame(self)
        alt_box.grid(row=2, column=0, columnspan=8, sticky="ew", pady=(6, 0))
        ttk.Label(alt_box, text="Elevation (ft):").pack(side="left")
        self.alt_var = tk.StringVar(value="0.0")
        ttk.Entry(alt_box, textvariable=self.alt_var, width=12).pack(side="left", padx=(6, 0))

        ttk.Label(self, text="Cities").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.city_dd = MultiSelectDropdown(self, placeholder="Select cities", width=36, searchable=True)
        self.city_dd.grid(row=4, column=0, rowspan=4, sticky="ew", padx=(0, 6))
        self.city_dd.set_items(self.c.city_list)

        ttk.Label(self, text="Scenarios").grid(row=3, column=2, sticky="w", padx=(12, 0), pady=(6, 0))
        ttk.Label(self, text="Models").grid(row=3, column=4, sticky="w", padx=(12, 0), pady=(6, 0))

        self._all_scenarios = self.c.list_subdirs(self.c.scenario_root)
        self.scen_dd = MultiSelectDropdown(self, placeholder="Select scenarios", width=36, command=self._refresh_models)
        self.scen_dd.grid(row=4, column=2, rowspan=2, sticky="ew", padx=(12, 6))
        self.scen_dd.set_items(self._all_scenarios)

        self.model_dd = MultiSelectDropdown(self, placeholder="Select models", width=36)
        self.model_dd.grid(row=4, column=4, rowspan=2, sticky="ew", padx=(12, 6))

        ttk.Label(self, text="DGPX Output Base:").grid(row=9, column=0, sticky="w", pady=(12, 0))
        self.out_var = tk.StringVar(value=str(self.c.output_base_dgpx))
        ttk.Entry(self, textvariable=self.out_var, state="readonly").grid(
            row=9, column=1, columnspan=7, sticky="ew", pady=(12, 0)
        )

        self.run_btn = ttk.Button(self, text="Run DGPX Batch", command=self._run_batch)
        self.run_btn.grid(row=10, column=0, columnspan=6, sticky="ew", pady=(12, 0))
        self.stop_btn = ttk.Button(self, text="Stop", command=self._on_stop, state="disabled")
        self.stop_btn.grid(row=10, column=6, columnspan=2, sticky="ew", pady=(12, 0), padx=(8, 0))

        self.pb_total = ttk.Progressbar(self, mode="determinate", maximum=1)
        self.pb_total.grid(row=11, column=0, columnspan=8, sticky="ew", pady=(6, 0))
        self.pb_label = ttk.Label(self, text="")
        self.pb_label.grid(row=12, column=0, columnspan=8, sticky="w")

        self.log = tk.Text(self, height=10, wrap="word", state="disabled")
        self.log.grid(row=13, column=0, columnspan=8, sticky="nsew", pady=(12, 0))

        for col in range(8):
            self.columnconfigure(col, weight=1)
        self.rowconfigure(13, weight=1)

        self._refresh_models()
        self._job_id = None
        self._job_log_offset = 0
        self._job_notified = False

    def _browse_dgpx(self):
        path = filedialog.askopenfilename(title="Select a .dgpx file", filetypes=[("DGPX", "*.dgpx"), ("All files", "*.*")])
        if path:
            self.dgpx_in_var.set(path)

    def refresh_dropdowns(self):
        self.city_dd.set_items(self.c.city_list, selected_values=self.city_dd.get_selected_values())

        self._all_scenarios = self.c.list_subdirs(self.c.scenario_root)
        self.scen_dd.set_items(self._all_scenarios, selected_values=self.scen_dd.get_selected_values())
        self._refresh_models()
        self.out_var.set(str(self.c.output_base_dgpx))

    def _refresh_models(self, event=None):
        scenarios = self.scen_dd.get_selected_values() or self._all_scenarios
        models = sorted({model for scenario in scenarios for model in self.c.list_subdirs(self.c.scenario_root / scenario)})
        previous = [model for model in self.model_dd.get_selected_values() if model in models]
        self.model_dd.set_items(models, selected_values=previous)

    def _append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _run_batch(self):
        dgpx_in = Path(self.dgpx_in_var.get().strip())
        if not dgpx_in.exists():
            messagebox.showerror("DGPX", "Pick a valid .dgpx file.")
            return

        scenarios = self.scen_dd.get_selected_values()
        models = self.model_dd.get_selected_values()
        cities = self.city_dd.get_selected_values()

        if not scenarios:
            messagebox.showerror("Missing selection", "Pick at least one Scenario.")
            return
        if not models:
            messagebox.showerror("Missing selection", "Pick at least one Model.")
            return
        if not cities:
            messagebox.showerror("Missing selection", "Pick at least one City.")
            return

        try:
            elevation = float(self.alt_var.get().strip())
        except Exception:
            messagebox.showerror("Elevation", "Enter a valid elevation (number).")
            return

        items = [(city, scenario, model) for city in cities for scenario in scenarios for model in models]

        total = len(items)
        self.pb_total.configure(maximum=total, value=0)
        self.pb_label.config(text=f"0 / {total} jobs")

        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.configure(state="disabled")

        def runner(ctx):
            ok = fail = 0
            stem = dgpx_in.stem
            out_name = f"{stem}_new.dgpx"
            completed = 0
            ctx.set_progress(0, total, "Waiting for first item")
            for city, scenario, model in items:
                try:
                    ctx.check_cancelled()
                    info = self.c.city_to_info.get(city)
                    if not info or not info.get("station"):
                        fail += 1
                        ctx.log(f"Skip city (missing info/station): {city}")
                        completed += 1
                        ctx.set_progress(completed, total, f"{city} / {scenario} / {model}")
                        continue

                    station = _station_from_info(info)
                    hcd_path = (Path(self.c.hcd_input_dir_dgpx) / city / scenario / model / f"{station}.hcd").resolve()
                    if not hcd_path.exists():
                        fail += 1
                        ctx.log(f"Missing HCD: {hcd_path}")
                        completed += 1
                        ctx.set_progress(completed, total, f"{city} / {scenario} / {model}")
                        continue

                    dgpx_text = dgpx_in.read_text(encoding="utf-8", errors="ignore")
                    lat, lon = info.get("lat"), info.get("lon")
                    state = info.get("state", "")
                    if lat is None or lon is None:
                        fail += 1
                        ctx.log(f"Missing lat/lon for city: {city}")
                        completed += 1
                        ctx.set_progress(completed, total, f"{city} / {scenario} / {model}")
                        continue

                    updated = _update_climate_station_block(
                        dgpx_text,
                        city=city,
                        state=state,
                        lat=float(lat),
                        lon=float(lon),
                        elevation=elevation,
                        station_id=station,
                        country="US",
                    )

                    with NamedTemporaryFile(suffix=".dgpx", delete=False) as tmp:
                        tmp_path = Path(tmp.name)
                        tmp.write(updated.encode("utf-8", errors="ignore"))

                    dgpx_out = (Path(self.c.output_base_dgpx) / city / scenario / model / out_name).resolve()
                    dgpx_out.parent.mkdir(parents=True, exist_ok=True)
                    rebuild(str(tmp_path), str(hcd_path), str(dgpx_out))

                    try:
                        tmp_path.unlink(missing_ok=True)
                    except Exception:
                        pass

                    ok += 1
                    ctx.log(f"OK {city} / {scenario} / {model} -> {dgpx_out}")
                    completed += 1
                    ctx.set_progress(completed, total, f"{city} / {scenario} / {model}")
                except OperationCancelledError:
                    raise
                except Exception as exc:
                    fail += 1
                    ctx.log(f"FAIL {city} / {scenario} / {model}: {exc}")
                    completed += 1
                    ctx.set_progress(completed, total, f"{city} / {scenario} / {model}")

            ctx.log(f"Done. Success: {ok}, Failed: {fail}.")
            return {"success": ok, "failed": fail}

        self._job_id = self.c.job_manager.submit(
            title=f"DGPX Batch ({len(items)} jobs)",
            kind="dgpx-batch",
            runner=runner,
        )
        self._job_log_offset = 0
        self._job_notified = False
        self.stop_btn.config(state="normal")
        self.after(100, self._poll_job)

    def _poll_job(self):
        if self._job_id is None:
            return

        snapshot = self.c.job_manager.get_job_snapshot(self._job_id)
        if snapshot is None:
            return

        new_logs = snapshot["logs"][self._job_log_offset:]
        self._job_log_offset = len(snapshot["logs"])
        if new_logs:
            self.log.configure(state="normal")
            for line in new_logs:
                self.log.insert(tk.END, line + "\n")
            self.log.see(tk.END)
            self.log.configure(state="disabled")

        self.pb_total["maximum"] = max(1, int(snapshot["progress_total"]))
        self.pb_total["value"] = min(int(snapshot["progress_current"]), int(self.pb_total["maximum"]))
        self.pb_label.config(text=f"{snapshot['progress_current']} / {snapshot['progress_total']} jobs - {snapshot['status']}")

        if snapshot["status"] in {"queued", "running", "cancelling"}:
            self.stop_btn.config(state="normal")
            self.after(200, self._poll_job)
            return

        self.stop_btn.config(state="disabled")
        if not self._job_notified and self.c.current_frame_name == "DGPXBatch":
            if snapshot["status"] == "completed":
                messagebox.showinfo("DGPX Batch", "DGPX batch completed.")
            elif snapshot["status"] == "cancelled":
                messagebox.showinfo("DGPX Batch", "DGPX batch was stopped.")
            else:
                messagebox.showerror("DGPX Batch Failed", snapshot["error"] or "Unknown error")
            self._job_notified = True

    def _on_stop(self):
        if self._job_id is not None:
            self.c.job_manager.cancel(self._job_id)
            self.stop_btn.config(state="disabled")

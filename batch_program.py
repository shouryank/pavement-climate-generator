import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

from generator_frontend import run_one_generation, list_subdirs
from multiselect_dropdown import MultiSelectDropdown
from operation_control import OperationCancelledError


class BatchFrame(ttk.Frame):
    """Batch mode: either pick Cities OR pick a folder with *.hcd files."""

    def __init__(self, parent, controller):
        super().__init__(parent, padding=16)
        self.c = controller

        top = ttk.Frame(self)
        top.grid(row=0, column=0, columnspan=8, sticky="ew", pady=(0, 12))
        self.back_btn = ttk.Button(top, text="Back", command=lambda: self.c.show_frame("MainMenu"))
        self.back_btn.pack(side="left")
        ttk.Label(top, text="Create Future Weather File - Batch", font=("Segoe UI", 14, "bold")).pack(side="left", padx=12)

        self.mode = tk.StringVar(value="cities")
        mode_row = ttk.Frame(self)
        mode_row.grid(row=1, column=0, columnspan=8, sticky="ew", pady=(0, 6))
        ttk.Radiobutton(mode_row, text="Select cities", value="cities", variable=self.mode,
                        command=self._sync_mode).pack(side="left")
        ttk.Radiobutton(mode_row, text="Use HCD folder", value="folder", variable=self.mode,
                        command=self._sync_mode).pack(side="left", padx=(12, 0))

        ttk.Label(self, text="Cities").grid(row=2, column=0, sticky="w")
        self.city_dd = MultiSelectDropdown(self, placeholder="Select cities", width=36, searchable=True)
        self.city_dd.grid(row=3, column=0, rowspan=4, sticky="ew", padx=(0, 6))
        self.city_dd.set_items(self.c.city_list)

        folder_box = ttk.LabelFrame(self, text="HCD Folder (process all *.hcd inside)")
        folder_box.grid(row=2, column=2, rowspan=2, columnspan=6, sticky="ew", padx=(12, 0))
        self.hcd_dir_var = tk.StringVar()
        ttk.Entry(folder_box, textvariable=self.hcd_dir_var).grid(row=0, column=0, sticky="ew", padx=(6, 6), pady=6)
        ttk.Button(folder_box, text="Browse...", command=self._browse_dir).grid(row=0, column=1, padx=(0, 6), pady=6)
        folder_box.columnconfigure(0, weight=1)

        ttk.Label(self, text="Scenarios").grid(row=4, column=2, sticky="w", padx=(12, 0))
        ttk.Label(self, text="Models").grid(row=4, column=4, sticky="w", padx=(12, 0))

        self._all_scenarios = list_subdirs(self.c.scenario_root)
        self.scen_dd = MultiSelectDropdown(self, placeholder="Select scenarios", width=36, command=self._refresh_models)
        self.scen_dd.grid(row=5, column=2, rowspan=2, sticky="ew", padx=(12, 6))
        self.scen_dd.set_items(self._all_scenarios)

        self.model_dd = MultiSelectDropdown(self, placeholder="Select models", width=36)
        self.model_dd.grid(row=5, column=4, rowspan=2, sticky="ew", padx=(12, 6))

        ttk.Label(self, text="Output Base:").grid(row=8, column=0, sticky="w", pady=(12, 0))
        self.out_var = tk.StringVar(value=str(self.c.output_base))
        ttk.Entry(self, textvariable=self.out_var, state="readonly").grid(
            row=8, column=1, columnspan=7, sticky="ew", pady=(12, 0)
        )

        self.run_btn = ttk.Button(self, text="Run Batch", command=self._run_batch)
        self.run_btn.grid(row=9, column=0, columnspan=6, sticky="ew", pady=(12, 0))
        self.stop_btn = ttk.Button(self, text="Stop", command=self._on_stop, state="disabled")
        self.stop_btn.grid(row=9, column=6, columnspan=2, sticky="ew", pady=(12, 0), padx=(8, 0))

        self.log = tk.Text(self, height=10, wrap="word", state="disabled")
        self.log.grid(row=12, column=0, columnspan=8, sticky="nsew", pady=(12, 0))

        for col in range(8):
            self.columnconfigure(col, weight=1)
        self.rowconfigure(12, weight=1)

        self._sync_mode()
        self._refresh_models()
        self._job_id = None
        self._job_log_offset = 0
        self._job_notified = False

        self.pb_total = ttk.Progressbar(self, mode="determinate", maximum=1)
        self.pb_total.grid(row=10, column=0, columnspan=8, sticky="ew", pady=(6, 0))
        self.pb_label = ttk.Label(self, text="")
        self.pb_label.grid(row=11, column=0, columnspan=8, sticky="w")

    def _sync_mode(self):
        by_cities = (self.mode.get() == "cities")
        state_city = ("!disabled" if by_cities else "disabled")
        self.city_dd.configure_state("normal" if by_cities else "disabled")

        folder_state = ("!disabled" if not by_cities else "disabled")
        folder_box = None
        for widget in self.grid_slaves():
            if isinstance(widget, ttk.Labelframe) and str(widget.cget("text")).startswith("HCD Folder"):
                folder_box = widget
                break
        if folder_box:
            for child in folder_box.winfo_children():
                child.state([folder_state])

    def refresh_dropdowns(self):
        self.city_dd.set_items(self.c.city_list, selected_values=self.city_dd.get_selected_values())

        self._all_scenarios = list_subdirs(self.c.scenario_root)
        self.scen_dd.set_items(self._all_scenarios, selected_values=self.scen_dd.get_selected_values())
        self._refresh_models()
        self.out_var.set(str(self.c.output_base))

    def _browse_dir(self):
        folder = filedialog.askdirectory(title="Select folder containing .hcd files")
        if folder:
            self.hcd_dir_var.set(folder)

    def _refresh_models(self, event=None):
        scenarios = self.scen_dd.get_selected_values() or self._all_scenarios
        models = sorted({model for scenario in scenarios for model in list_subdirs(self.c.scenario_root / scenario)})
        special = [("Hot", "GFDL-CM3"), ("Medium", "HadGEM2-CC"), ("Cold", "MRI-CGCM3")]
        previous = set(self.model_dd.get_selected_values())
        items = [{"label": f"{label} - {name}", "value": name} for label, name in special]
        items.append({"label": "-----", "selectable": False})
        items.extend({"label": model, "value": model} for model in models)

        missing = {}
        for rcp in ("RCP45", "RCP85"):
            root = Path(self.c.scenario_root) / rcp
            missing_models = [name for _, name in special if not (root / name).exists()]
            if missing_models:
                missing[rcp] = missing_models
        if missing:
            msgs = [f"{rcp}: {', '.join(names)}" for rcp, names in missing.items()]
            messagebox.showwarning("Model validation", "Special models missing:\n" + "\n".join(msgs))

        valid_previous = [
            model for model in previous
            if any(item.get("value") == model and item.get("selectable", True) for item in items)
        ]
        self.model_dd.set_items(items, selected_values=valid_previous)

    def _append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _run_batch(self):
        scenarios = self.scen_dd.get_selected_values()
        models = self.model_dd.get_selected_values()

        if not scenarios:
            messagebox.showerror("Missing selection", "Pick at least one Scenario.")
            return
        if not models:
            messagebox.showerror("Missing selection", "Pick at least one Model.")
            return

        by_cities = (self.mode.get() == "cities")
        if by_cities:
            cities = self.city_dd.get_selected_values()
            if not cities:
                messagebox.showerror("Missing selection", "Pick at least one City.")
                return
            items = [(("city", city), scenario, model) for city in cities for scenario in scenarios for model in models]
        else:
            folder = Path(self.hcd_dir_var.get().strip())
            if not folder or not folder.exists() or not folder.is_dir():
                messagebox.showerror("HCD Folder", "Please pick a valid folder containing .hcd files.")
                return
            hcd_files = sorted(p for p in folder.glob("*.hcd") if p.is_file())
            if not hcd_files:
                messagebox.showerror("HCD Folder", "No .hcd files found in that folder.")
                return
            items = [(("file", hcd_file), scenario, model) for hcd_file in hcd_files for scenario in scenarios for model in models]

        start_year, end_year = 2025, 2099
        years = end_year - start_year + 1
        total_ticks = years * len(items)

        self.pb_total.configure(maximum=total_ticks, value=0)
        self.pb_label.config(text=f"0 / {total_ticks} year-steps")
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.configure(state="disabled")

        def runner(ctx):
            ok = fail = 0
            cancelled = False
            progress = [0]

            def make_progress_cb(city_label, scenario_label, model_label):
                def _progress():
                    progress[0] += 1
                    ctx.set_progress(
                        progress[0],
                        total_ticks,
                        f"{city_label} / {scenario_label} / {model_label}",
                    )
                return _progress

            ctx.log(f"Starting batch: {len(items)} jobs x {years} years")
            ctx.set_progress(0, total_ticks, "Waiting for first item")
            for item, scenario, model in items:
                try:
                    ctx.check_cancelled()
                    if item[0] == "city":
                        city = item[1]
                        info = self.c.city_to_info.get(city)
                        if not (info and info.get("station")):
                            fail += 1
                            ctx.log(f"Skip city (missing info/station): {city}")
                            for _ in range(years):
                                progress[0] += 1
                                ctx.set_progress(progress[0], total_ticks, f"{city} / {scenario} / {model}")
                            continue

                        out_path = run_one_generation(
                            city=city,
                            info=info,
                            scenario=scenario,
                            model=model,
                            scenario_root=self.c.scenario_root,
                            output_base=self.c.output_base,
                            hcd_input_dir=self.c.hcd_input_dir,
                            start_year=start_year,
                            end_year=end_year,
                            overwrite=False,
                            progress_cb=make_progress_cb(city, scenario, model),
                            cancel_event=ctx.cancel_event,
                        )
                        ok += 1
                        ctx.log(f"OK {city} / {scenario} / {model} -> {out_path}")
                    else:
                        hcd_file = item[1]
                        out_path = run_one_generation(
                            city="(by-folder)",
                            info=None,
                            scenario=scenario,
                            model=model,
                            scenario_root=self.c.scenario_root,
                            output_base=self.c.output_base,
                            start_year=start_year,
                            end_year=end_year,
                            overwrite=False,
                            hcd_in_override=hcd_file,
                            city_label_override=None,
                            progress_cb=make_progress_cb(hcd_file.name, scenario, model),
                            cancel_event=ctx.cancel_event,
                        )
                        ok += 1
                        ctx.log(f"OK {hcd_file.name} / {scenario} / {model} -> {out_path}")
                except OperationCancelledError as exc:
                    cancelled = True
                    ctx.log(str(exc))
                    break
                except Exception as exc:
                    fail += 1
                    ctx.log(f"FAIL: {exc}")
            if cancelled:
                ctx.log(f"Stopped. Success: {ok}, Failed: {fail}.")
            else:
                ctx.log(f"Done. Success: {ok}, Failed: {fail}.")
            return {"success": ok, "failed": fail, "cancelled": cancelled}

        self._job_id = self.c.job_manager.submit(
            title=f"HCD Batch ({len(items)} jobs)",
            kind="hcd-batch",
            runner=runner,
        )
        self._job_log_offset = 0
        self._job_notified = False
        self.stop_btn.config(state="normal")
        self.after(100, self._poll_job)

    def _on_stop(self):
        if self._job_id is not None:
            self.c.job_manager.cancel(self._job_id)
            self.stop_btn.config(state="disabled")
            self.pb_label.config(text="Stopping...")

    def _append_logs(self, lines: list[str]):
        if not lines:
            return
        self.log.configure(state="normal")
        for line in lines:
            self.log.insert(tk.END, line + "\n")
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _poll_job(self):
        if self._job_id is None:
            return

        snapshot = self.c.job_manager.get_job_snapshot(self._job_id)
        if snapshot is None:
            return

        new_logs = snapshot["logs"][self._job_log_offset:]
        self._job_log_offset = len(snapshot["logs"])
        self._append_logs(new_logs)

        self.pb_total["maximum"] = max(1, int(snapshot["progress_total"]))
        self.pb_total["value"] = min(int(snapshot["progress_current"]), int(self.pb_total["maximum"]))
        self.pb_label.config(
            text=f"{snapshot['progress_current']} / {snapshot['progress_total']} year-steps - {snapshot['status']}"
        )

        if snapshot["status"] in {"queued", "running", "cancelling"}:
            self.stop_btn.config(state="normal")
            self.after(200, self._poll_job)
            return

        self.stop_btn.config(state="disabled")
        if not self._job_notified and self.c.current_frame_name == "BatchFrame":
            if snapshot["status"] == "failed":
                messagebox.showerror("Batch failed", snapshot["error"] or "Unknown error")
            elif snapshot["status"] == "cancelled":
                messagebox.showinfo("Batch stopped", "Batch processing was stopped.")
            else:
                messagebox.showinfo("Batch complete", "Batch processing completed.")
            self._job_notified = True

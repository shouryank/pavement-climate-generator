import tkinter as tk
from tkinter import ttk


class JobsFrame(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, padding=16)
        self.c = controller
        self._selected_job_id = None
        self._last_log_job_id = None
        self._polling = False

        top = ttk.Frame(self)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Button(top, text="Back", command=lambda: self.c.show_frame("MainMenu")).pack(side="left")
        ttk.Label(top, text="Jobs", style="ScreenTitle.TLabel").pack(side="left", padx=12)
        ttk.Button(top, text="Cancel Selected", style="Secondary.TButton", command=self._cancel_selected).pack(side="right")

        self.tree = ttk.Treeview(
            self,
            columns=("title", "kind", "status", "progress", "message"),
            show="headings",
            height=12,
        )
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        self.tree.heading("title", text="Title")
        self.tree.heading("kind", text="Kind")
        self.tree.heading("status", text="Status")
        self.tree.heading("progress", text="Progress")
        self.tree.heading("message", text="Message")
        self.tree.column("title", width=250, anchor="w")
        self.tree.column("kind", width=120, anchor="center")
        self.tree.column("status", width=100, anchor="center")
        self.tree.column("progress", width=120, anchor="center")
        self.tree.column("message", width=220, anchor="w")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        log_frame = ttk.LabelFrame(self, text="Job Log", padding=6)
        log_frame.grid(row=1, column=1, sticky="nsew")
        self.log = tk.Text(log_frame, height=20, wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

    def on_show(self):
        if not self._polling:
            self._polling = True
            self.after(100, self._poll_jobs)

    def _poll_jobs(self):
        snapshots = self.c.job_manager.list_job_snapshots()
        current_ids = {str(item["job_id"]) for item in snapshots}
        for item_id in self.tree.get_children():
            if item_id not in current_ids:
                self.tree.delete(item_id)

        for snap in snapshots:
            job_id = str(snap["job_id"])
            progress = f"{snap['progress_current']} / {snap['progress_total']}"
            values = (
                snap["title"],
                snap["kind"],
                snap["status"],
                progress,
                snap["message"],
            )
            if self.tree.exists(job_id):
                self.tree.item(job_id, values=values)
            else:
                self.tree.insert("", "end", iid=job_id, values=values)

        if self._selected_job_id is None and snapshots:
            self._selected_job_id = snapshots[0]["job_id"]
            self.tree.selection_set(str(self._selected_job_id))

        self._refresh_log()
        if self._polling:
            self.after(500, self._poll_jobs)

    def _on_select(self, event=None):
        selected = self.tree.selection()
        if not selected:
            self._selected_job_id = None
            return
        self._selected_job_id = int(selected[0])
        self._refresh_log()

    def _refresh_log(self):
        if self._selected_job_id is None:
            return
        snap = self.c.job_manager.get_job_snapshot(self._selected_job_id)
        if snap is None:
            return
        log_text = "\n".join(snap["logs"])
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.insert(tk.END, log_text)
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _cancel_selected(self):
        if self._selected_job_id is not None:
            self.c.job_manager.cancel(self._selected_job_id)

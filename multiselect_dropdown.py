import tkinter as tk
from tkinter import ttk


class MultiSelectDropdown(ttk.Frame):
    """Dropdown-style multi-select that stays open until the user clicks away."""

    _style_counter = 0

    def __init__(
        self,
        parent,
        *,
        placeholder="Select...",
        width=32,
        command=None,
        max_height=480,
        searchable=False,
        single_select=False,
        trigger_padding=(8, 10),
    ):
        super().__init__(parent)
        self.placeholder = placeholder
        self.command = command
        self.max_height = max_height
        self.searchable = searchable
        self.single_select = single_select
        self.trigger_padding = trigger_padding
        self._items = []
        self._vars = {}
        self._labels = {}
        self._selected_value = None
        self._popup = None
        self._popup_canvas = None
        self._popup_inner = None
        self._popup_outer = None
        self._popup_actions = None
        self._search_var = tk.StringVar()
        self._search_entry = None
        self._select_all_btn = None
        self._clear_all_btn = None
        self._root_click_binding = None
        self._style_name = self._build_styles()

        self.text_var = tk.StringVar(value=self.placeholder)
        self.button = ttk.Button(
            self,
            textvariable=self.text_var,
            width=width,
            style=self._style_name["trigger"],
        )
        self.button.grid(row=0, column=0, sticky="ew")
        self.button.bind("<Button-1>", self._toggle_popup, add=False)
        self.columnconfigure(0, weight=1)

    def _build_styles(self):
        style = ttk.Style(self)
        MultiSelectDropdown._style_counter += 1
        base = f"MultiSelectDropdown{MultiSelectDropdown._style_counter}"
        frame_style = f"{base}.Popup.TFrame"
        check_style = f"{base}.Popup.TCheckbutton"
        option_style = f"{base}.Popup.TButton"
        trigger_style = f"{base}.Trigger.TButton"

        try:
            field_bg = style.lookup("TEntry", "fieldbackground") or "white"
        except Exception:
            field_bg = "white"

        style.configure(frame_style, background=field_bg)
        style.configure(trigger_style, padding=self.trigger_padding, anchor="center")
        style.configure(
            check_style,
            background=field_bg,
            focuscolor=field_bg,
            indicatorbackground=field_bg,
            indicatormargin=6,
            padding=(6, 4),
        )
        style.map(
            check_style,
            background=[
                ("active", field_bg),
                ("selected", field_bg),
                ("!active", field_bg),
            ],
            indicatorbackground=[
                ("selected", field_bg),
                ("active", field_bg),
            ],
            focuscolor=[
                ("focus", field_bg),
                ("!focus", field_bg),
            ],
        )
        style.configure(
            option_style,
            background=field_bg,
            foreground=style.lookup("TLabel", "foreground") or "black",
            padding=(8, 4),
            relief="flat",
            anchor="w",
        )
        style.map(
            option_style,
            background=[("active", field_bg), ("pressed", field_bg)],
            foreground=[("active", style.lookup("TLabel", "foreground") or "black")],
        )
        return {
            "frame": frame_style,
            "check": check_style,
            "option": option_style,
            "trigger": trigger_style,
            "background": field_bg,
        }

    def set_items(self, items, selected_values=None):
        selected_values = set(selected_values or [])
        self._items = []
        self._vars = {}
        self._labels = {}

        for item in items:
            if isinstance(item, dict):
                label = item["label"]
                value = item.get("value", label)
                selectable = item.get("selectable", True)
            else:
                label = str(item)
                value = label
                selectable = True

            spec = {"label": label, "value": value, "selectable": selectable}
            self._items.append(spec)

            if selectable:
                self._vars[value] = tk.BooleanVar(value=value in selected_values)
                self._labels[value] = label

        if self.single_select:
            self._selected_value = next(iter(selected_values), None)

        self._update_text()
        if self._popup is not None:
            self._rebuild_popup_contents()

    def _filtered_items(self):
        if not self.searchable:
            return list(self._items)

        query = self._search_var.get().strip().lower()
        if not query:
            return list(self._items)

        filtered = []
        for item in self._items:
            if not item["selectable"]:
                continue
            if query in item["label"].lower():
                filtered.append(item)
        return filtered

    def get_selected_values(self):
        if self.single_select:
            return [self._selected_value] if self._selected_value in self._labels else []
        return [
            item["value"]
            for item in self._items
            if item["selectable"] and self._vars[item["value"]].get()
        ]

    def set_selected_values(self, values):
        selected = set(values)
        if self.single_select:
            self._selected_value = next(iter(selected), None)
        for value, var in self._vars.items():
            var.set(value == self._selected_value if self.single_select else value in selected)
        self._update_text()
        self._update_action_state()
        if self._popup is not None and self._popup.winfo_exists():
            self._rebuild_popup_contents()

    def clear_selection(self):
        self.set_selected_values([])

    def select_all(self):
        if self.single_select:
            return
        self.set_selected_values([
            item["value"] for item in self._items if item["selectable"]
        ])
        if self.command:
            self.command()

    def configure_state(self, state):
        self.button.configure(state=state)
        if state == "disabled":
            self._close_popup()

    def _selection_changed(self):
        self._update_text()
        self._update_action_state()
        if self.command:
            self.command()

    def _option_text(self, value, label):
        if self._vars.get(value) and self._vars[value].get():
            return f"\u2713 {label}"
        return f"\u2610 {label}"

    def _toggle_value(self, value):
        if value not in self._vars:
            return
        self._vars[value].set(not self._vars[value].get())
        self._selection_changed()
        if self._popup is not None and self._popup.winfo_exists():
            self._rebuild_popup_contents()

    def _update_text(self):
        labels = [self._labels[value] for value in self.get_selected_values()]
        if not labels:
            text = self.placeholder
        elif len(labels) == 1:
            text = labels[0]
        else:
            text = f"{len(labels)} selected"
        self.text_var.set(text)

    def _toggle_popup(self, event=None):
        if str(self.button.cget("state")) == "disabled":
            return "break"
        if self._popup is not None and self._popup.winfo_exists():
            self._close_popup()
        else:
            self._open_popup()
        return "break"

    def _open_popup(self):
        if self._popup is not None and self._popup.winfo_exists():
            return

        root = self.winfo_toplevel()
        self._popup = tk.Toplevel(self)
        self._popup.withdraw()
        self._popup.overrideredirect(True)
        self._popup.transient(root)

        outer = ttk.Frame(self._popup, relief="solid", borderwidth=1, style=self._style_name["frame"])
        outer.pack(fill="both", expand=True)
        self._popup_outer = outer

        top_row = 0
        if self.searchable:
            self._search_var.set("")
            self._search_entry = ttk.Entry(outer, textvariable=self._search_var)
            self._search_entry.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 6))
            self._search_entry.bind("<KeyRelease>", lambda e: self._rebuild_popup_contents())
            self._search_entry.bind("<MouseWheel>", self._on_mousewheel)
            top_row = 1

        if not self.single_select:
            actions = ttk.Frame(outer, style=self._style_name["frame"])
            actions.grid(row=top_row, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 6))
            actions.columnconfigure(0, weight=1)
            actions.columnconfigure(1, weight=1)
            self._popup_actions = actions

            self._select_all_btn = ttk.Button(actions, text="Select All", style="Secondary.TButton", command=self._select_all_from_popup)
            self._select_all_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))

            self._clear_all_btn = ttk.Button(actions, text="Clear All", style="Secondary.TButton", command=self._clear_all_from_popup)
            self._clear_all_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))
            top_row += 1

        self._popup_canvas = tk.Canvas(
            outer,
            highlightthickness=0,
            borderwidth=0,
            background=self._style_name["background"],
        )
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=self._popup_canvas.yview)
        self._popup_inner = ttk.Frame(self._popup_canvas, style=self._style_name["frame"])
        self._popup_inner.bind("<Configure>", self._sync_popup_scrollregion)
        self._popup_canvas.create_window((0, 0), window=self._popup_inner, anchor="nw")
        self._popup_canvas.configure(yscrollcommand=scrollbar.set)

        canvas_row = top_row
        self._popup_canvas.grid(row=canvas_row, column=0, sticky="nsew")
        scrollbar.grid(row=canvas_row, column=1, sticky="ns")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(canvas_row, weight=1)

        self._rebuild_popup_contents()
        self._position_popup()

        self._popup.deiconify()
        self._popup.lift()
        self._popup.bind("<Escape>", lambda e: self._close_popup())
        self._popup.bind("<MouseWheel>", self._on_mousewheel)
        self._popup_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._popup_inner.bind("<MouseWheel>", self._on_mousewheel)

        self._root_click_binding = root.bind("<Button-1>", self._handle_root_click, add="+")
        self.after_idle(self._focus_popup)

    def _focus_popup(self):
        if not self._popup or not self._popup.winfo_exists():
            return
        if self.searchable and self._search_entry is not None:
            try:
                self._search_entry.focus_set()
            except Exception:
                pass

    def _close_popup(self):
        if self._popup is None:
            return

        root = self.winfo_toplevel()
        if self._root_click_binding:
            root.unbind("<Button-1>", self._root_click_binding)
            self._root_click_binding = None

        try:
            self._popup.destroy()
        except Exception:
            pass

        self._popup = None
        self._popup_canvas = None
        self._popup_inner = None
        self._popup_outer = None
        self._popup_actions = None
        self._search_entry = None
        self._select_all_btn = None
        self._clear_all_btn = None
        try:
            root.focus_set()
        except Exception:
            pass

    def _rebuild_popup_contents(self):
        if self._popup_inner is None:
            return

        for child in self._popup_inner.winfo_children():
            child.destroy()

        self._update_action_state()

        row = 0
        for item in self._filtered_items():
            if item["selectable"]:
                if self.single_select:
                    option = ttk.Button(
                        self._popup_inner,
                        text=item["label"],
                        command=lambda value=item["value"]: self._single_select(value),
                        style=self._style_name["option"],
                    )
                    option.grid(row=row, column=0, sticky="ew", padx=4, pady=1)
                    option.bind("<MouseWheel>", self._on_mousewheel)
                else:
                    option = ttk.Button(
                        self._popup_inner,
                        text=self._option_text(item["value"], item["label"]),
                        command=lambda value=item["value"]: self._toggle_value(value),
                        style=self._style_name["option"],
                    )
                    option.grid(row=row, column=0, sticky="ew", padx=4, pady=1)
                    option.bind("<MouseWheel>", self._on_mousewheel)
            else:
                ttk.Separator(self._popup_inner, orient="horizontal").grid(
                    row=row, column=0, sticky="ew", padx=4, pady=4
                )
            row += 1

        self._popup_inner.columnconfigure(0, weight=1)
        self._popup_inner.update_idletasks()
        self._sync_popup_scrollregion()
        self._position_popup()

    def _sync_popup_scrollregion(self, event=None):
        if self._popup is None or self._popup_canvas is None or self._popup_inner is None:
            return

        self._popup.update_idletasks()
        self._popup_canvas.configure(scrollregion=self._popup_canvas.bbox("all"))
        width = max(self.button.winfo_width(), self._popup_inner.winfo_reqwidth() + 16)

        chrome_height = 0
        if self.searchable and self._search_entry is not None:
            self._search_entry.update_idletasks()
            chrome_height += self._search_entry.winfo_reqheight() + 16
        if self._popup_actions is not None:
            self._popup_actions.update_idletasks()
            chrome_height += self._popup_actions.winfo_reqheight() + 6

        border_height = 8
        available_screen_height = max(120, self.winfo_screenheight() - 80)
        max_canvas_height = max(80, min(self.max_height, available_screen_height - chrome_height - border_height))
        canvas_height = min(max_canvas_height, self._popup_inner.winfo_reqheight() + 8)

        self._popup_canvas.configure(width=width, height=canvas_height)
        popup_height = canvas_height + chrome_height + border_height
        self._popup.geometry(f"{width + 16}x{popup_height}")

    def _position_popup(self):
        if self._popup is None or not self._popup.winfo_exists():
            return

        self.update_idletasks()
        self._popup.update_idletasks()
        x = self.button.winfo_rootx()
        y = self.button.winfo_rooty() + self.button.winfo_height()
        self._popup.geometry(f"+{x}+{y}")

    def _single_select(self, value):
        self._selected_value = value
        for item_value, var in self._vars.items():
            var.set(item_value == value)
        self._update_text()
        if self.command:
            self.command()
        self._close_popup()

    def _handle_root_click(self, event):
        widget = event.widget
        if self._is_descendant(widget, self.button) or self._is_descendant(widget, self._popup):
            return
        self._close_popup()
        self.after_idle(lambda w=widget: self._restore_click_focus(w))

    def _restore_click_focus(self, widget):
        if widget is None:
            return
        try:
            widget.focus_force()
        except Exception:
            pass

    def _is_descendant(self, widget, ancestor):
        if widget is None or ancestor is None:
            return False
        current = widget
        while current is not None:
            if current == ancestor:
                return True
            parent_name = current.winfo_parent()
            if not parent_name:
                break
            try:
                current = current.nametowidget(parent_name)
            except Exception:
                break
        return False

    def _on_mousewheel(self, event):
        if self._popup is None or self._popup_canvas is None:
            return "break"
        self._popup_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"

    def _select_all_from_popup(self):
        if self.single_select:
            return
        self.set_selected_values([item["value"] for item in self._items if item["selectable"]])
        self._update_action_state()
        if self.command:
            self.command()

    def _clear_all_from_popup(self):
        if self.single_select:
            return
        self.clear_selection()
        self._update_action_state()
        if self.command:
            self.command()

    def _update_action_state(self):
        if self.single_select or self._select_all_btn is None or self._clear_all_btn is None:
            return
        selectable = [item["value"] for item in self._items if item["selectable"]]
        selected = set(self.get_selected_values())
        all_selected = bool(selectable) and all(value in selected for value in selectable)
        any_selected = bool(selected)
        self._select_all_btn.configure(state="disabled" if all_selected else "normal")
        self._clear_all_btn.configure(state="normal" if any_selected else "disabled")

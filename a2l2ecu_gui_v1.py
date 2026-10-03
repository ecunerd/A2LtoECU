#!/usr/bin/env python3
"""
a2l2ecu_gui_v1.py - tiny GUI for a2l2ecu_v1.py

Keep this file in the same folder as a2l2ecu_v1.py, then run:
    python a2l2ecu_gui_v1.py
(or just double-click it).
"""
import contextlib
import io
import os
import sys
import tkinter as tk
from tkinter import filedialog, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import a2l2ecu_v1  # noqa: E402


def convert(a2l, out, ref="", restrict=False):
    """Run the converter, return (ok, log_text)."""
    argv = ["a2l2ecu_v1.py", a2l, "-o", out]
    if ref:
        argv += ["--reference", ref]
        if restrict:
            argv.append("--restrict")
    buf = io.StringIO()
    old = sys.argv
    sys.argv = argv
    ok = True
    try:
        with contextlib.redirect_stdout(buf):
            a2l2ecu_v1.main()
    except SystemExit as e:           # converter exits with a message on bad input
        if e.code not in (None, 0):
            ok = False
            buf.write(str(e.code) + "\n")
    except Exception as e:            # anything unexpected: show it instead of crashing
        ok = False
        buf.write("Error: %s\n" % e)
    finally:
        sys.argv = old
    return ok, buf.getvalue()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("A2L to ECU")
        self.geometry("560x330")
        self.a2l = tk.StringVar()
        self.ref = tk.StringVar()
        self.out = tk.StringVar()
        self.restrict = tk.BooleanVar(value=False)

        pad = {"padx": 8, "pady": 4}
        f = ttk.Frame(self)
        f.pack(fill="x", **pad)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="A2L file").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.a2l).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(f, text="Browse...", command=self.pick_a2l).grid(row=0, column=2)

        ttk.Label(f, text="Reference .ecu\n(optional)").grid(row=1, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.ref).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(f, text="Browse...", command=self.pick_ref).grid(row=1, column=2)

        ttk.Label(f, text="Output .ecu").grid(row=2, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.out).grid(row=2, column=1, sticky="ew", padx=6)
        ttk.Button(f, text="Browse...", command=self.pick_out).grid(row=2, column=2)

        ttk.Checkbutton(self, text="Only keep variables that are in the reference .ecu",
                        variable=self.restrict).pack(anchor="w", padx=8)
        ttk.Button(self, text="Generate .ecu", command=self.run).pack(fill="x", **pad)

        self.log = tk.Text(self, height=9, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, **pad)

    def pick_a2l(self):
        p = filedialog.askopenfilename(title="Select A2L",
                                       filetypes=[("A2L files", "*.a2l *.A2L"), ("All files", "*.*")])
        if p:
            self.a2l.set(p)
            self.out.set(os.path.splitext(p)[0] + ".ecu")

    def pick_ref(self):
        p = filedialog.askopenfilename(title="Select reference .ecu",
                                       filetypes=[("ECU files", "*.ecu"), ("All files", "*.*")])
        if p:
            self.ref.set(p)

    def pick_out(self):
        p = filedialog.asksaveasfilename(title="Save .ecu as", defaultextension=".ecu",
                                         filetypes=[("ECU files", "*.ecu")])
        if p:
            self.out.set(p)

    def show(self, text):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", text)
        self.log.config(state="disabled")

    def run(self):
        if not self.a2l.get():
            self.show("Pick an A2L file first.")
            return
        out = self.out.get() or (os.path.splitext(self.a2l.get())[0] + ".ecu")
        self.out.set(out)
        self.show("Working...")
        self.update_idletasks()
        ok, text = convert(self.a2l.get(), out, self.ref.get(), self.restrict.get())
        self.show(("Done.\n\n" if ok else "Failed.\n\n") + text)


if __name__ == "__main__":
    App().mainloop()

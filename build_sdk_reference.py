#!/usr/bin/env python3
"""
build_sdk_reference.py
Converts the Garmin Connect IQ SDK documentation into LLM-friendly Markdown files.

Output — separate mode (default):
  Consolidated SDK/
  ├── docs/             <- HTML docs converted to Markdown, split if large
  ├── samples/
  │   ├── SAMPLES_INDEX.md
  │   └── SampleName/
  │       ├── overview.md   <- metadata, compat warnings, project tree (tiny)
  │       ├── source/SampleApp.mc     <- all original project files, verbatim
  │       ├── manifest.xml
  │       └── resources/
  ├── templates/        <- same per-file structure
  └── SPLIT_FILES.md    <- catalog of all doc files split into parts

Output — consolidated mode:
  Consolidated SDK/
  ├── docs/             <- same as above
  ├── samples/
  │   ├── SAMPLES_INDEX.md
  │   ├── SampleName.md     <- all source + manifest in one file, split if large
  │   └── SampleName_part2.md
  └── templates/        <- same consolidated structure

Requirements: pip install beautifulsoup4 markdownify
"""

import os
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# GLOBAL STATE — all set at startup by the picker functions
# ---------------------------------------------------------------------------
SDK_ROOT: Path = None
OUTPUT_ROOT: Path = None
DEVICE_FILTERS: list[str] = []      # empty list = general build, no device filtering
STRICT_DEVICE_FILTER: bool = False  # exclude unlisted samples
STRICT_REQUIRE_ALL: bool = True     # True = must be listed for ALL devices; False = at least ONE
SAMPLE_OUTPUT_MODE: str = "separate"  # "separate" or "consolidated"
MAX_FILE_KB: float = 18.0           # set by pick_threshold()
UNSUPPORTED_APIS: dict[str, dict] = {}  # {symbol: {"qualified": str, "devices": set[str]}}
SPLIT_REGISTRY: dict[str, list[str]] = {}

# Monkey C language keywords + overly-generic API names.
# Any symbol matching these is never recorded as "unsupported" — they appear
# in code examples throughout the SDK HTML and would create false positives.
_GENERIC_SYMBOLS = {
    "var", "const", "function", "class", "module", "extends", "instanceof",
    "using", "import", "return", "null", "true", "false", "new", "as",
    "has", "self", "me", "do", "end", "elif", "else", "if", "for",
    "while", "break", "continue", "throw", "catch", "finally", "try",
    "switch", "case", "default", "enum", "typedef", "public", "private",
    "protected", "static", "native", "hidden",
    "initialize", "onUpdate", "onLayout", "onShow", "onHide", "onStart", "onStop",
    "compute", "getInitialView", "onBack", "onSelect", "onMenu", "onNextPage",
    "onPreviousPage", "onKey", "onTap", "onSwipe", "onHold", "onRelease",
    "drawText", "drawLine", "fillRectangle", "clear", "setColor",
    "format", "toString", "toNumber", "toFloat", "size", "get", "put",
    "info", "data", "value", "result", "state", "type", "name", "id",
}

HTML_SKIP_DIRS = {"css", "js", "branding", "resources"}
PROJECT_SKIP_DIRS = {"docs", "css", "js", ".git", ".vscode"}
PROJECT_SKIP_BUILD_DIRS = {"bin", "gen", "mir", "internal-mir"}
# Device-variant resource directories (e.g. resources-vivoactive_hr/) are skipped
# in consolidated mode — they are device-specific UI tweaks rarely needed for coding.
DEVICE_VARIANT_RESOURCE_RE = re.compile(r'^resources-')

# ---------------------------------------------------------------------------
# DEPENDENCY CHECK
# ---------------------------------------------------------------------------

def check_dependencies():
    missing = []
    try:
        import bs4  # noqa
    except ImportError:
        missing.append("beautifulsoup4")
    try:
        import markdownify  # noqa
    except ImportError:
        missing.append("markdownify")
    if missing:
        print(f"Missing packages. Install with: pip install {' '.join(missing)}")
        sys.exit(1)

# ---------------------------------------------------------------------------
# DIALOG CONSTANTS & HELPERS
# ---------------------------------------------------------------------------

DIALOG_W = 520
DIALOG_H = 700
_BACK = "<<BACK>>"
_DEFAULT_SDK = (
    r"C:\Users\innic\AppData\Roaming\Garmin\ConnectIQ\Sdks"
    r"\connectiq-sdk-win-8.4.1-2026-02-03-e9f77eeaa"
)


def _make_dialog(title: str):
    """Return a centred, always-on-top Tk window at the standard size."""
    import tkinter as tk
    win = tk.Tk()
    win.title(title)
    win.resizable(False, False)
    win.attributes("-topmost", True)
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = (sw - DIALOG_W) // 2
    y = (sh - DIALOG_H) // 2
    win.geometry(f"{DIALOG_W}x{DIALOG_H}+{x}+{y}")
    return win, tk


def _nav_buttons(win, tk, on_back=None, on_cancel=None):
    """Pin Back and Cancel buttons to the bottom of a dialog."""
    frame = tk.Frame(win)
    frame.pack(side="bottom", fill="x", padx=14, pady=10)
    if on_cancel:
        tk.Button(frame, text="Cancel", width=10,
                  command=on_cancel).pack(side="right", padx=4)
    if on_back:
        tk.Button(frame, text="\u2190 Back", width=10,
                  command=on_back).pack(side="right", padx=4)

# ---------------------------------------------------------------------------
# STARTUP PICKER 1 — SDK root
# ---------------------------------------------------------------------------

def pick_sdk_root(prev_path: str = None):
    """Returns a validated Path, or exits on Cancel. (No Back on step 1.)"""
    initial = prev_path or _DEFAULT_SDK
    EXPLANATION = (
        "Select your Connect IQ SDK root directory.\n\n"
        "This is the folder containing doc/, samples/, bin/, etc. that you\n"
        "downloaded from the Garmin developer portal. The path below is\n"
        "pre-filled with the default location — edit it directly or click\n"
        "Browse to navigate to a different folder.\n\n"
        "The script will verify the folder contains a doc/ subfolder\n"
        "before proceeding."
    )
    try:
        import tkinter as tk
        from tkinter import filedialog
        result = {"val": None}
        win, _ = _make_dialog("SDK Root Directory")
        tk.Label(win, text="SDK Root Directory", font=("", 11, "bold"),
                 justify="left").pack(padx=14, pady=(16, 2), anchor="w")
        tk.Label(win, text=EXPLANATION, justify="left",
                 wraplength=490).pack(padx=14, pady=(4, 12), anchor="w")
        path_var = tk.StringVar(value=str(initial))
        row = tk.Frame(win)
        row.pack(fill="x", padx=14, pady=(0, 4))
        tk.Entry(row, textvariable=path_var).pack(
            side="left", fill="x", expand=True, padx=(0, 6))
        err_label = tk.Label(win, text="", fg="red", wraplength=490)

        def browse():
            init_dir = path_var.get() if Path(path_var.get()).exists() else "C:\\"
            chosen = filedialog.askdirectory(
                parent=win,
                title="Select Connect IQ SDK root directory",
                initialdir=init_dir,
            )
            if chosen:
                path_var.set(chosen)
            err_label.config(text="")

        tk.Button(row, text="Browse\u2026", width=9, command=browse).pack(side="left")
        err_label.pack(padx=14, anchor="w")

        def on_ok(_event=None):
            p = Path(path_var.get().strip().strip('"').strip("'"))
            if not (p / "doc").is_dir():
                err_label.config(
                    text=f"'{p}' doesn't look like an SDK root — no doc/ folder found.")
                return
            result["val"] = p
            win.destroy()

        def on_cancel():
            win.destroy()
            sys.exit(0)

        tk.Button(win, text="OK", width=12, command=on_ok).pack(pady=10)
        win.bind("<Return>", on_ok)
        _nav_buttons(win, tk, on_back=None, on_cancel=on_cancel)
        win.mainloop()
        if result["val"] is None:
            sys.exit(0)
        print(f"  SDK root: {result['val']}")
        return result["val"]
    except Exception:
        pass
    # Console fallback
    print(f"\nEnter the full path to your Connect IQ SDK root")
    print(f"(press Enter to use default: {_DEFAULT_SDK}):")
    raw = input("SDK root: ").strip().strip('"').strip("'")
    chosen = Path(raw) if raw else Path(_DEFAULT_SDK)
    if not (chosen / "doc").is_dir():
        print(f"\nERROR: '{chosen}' does not look like an SDK root (no doc/ folder).")
        sys.exit(1)
    return chosen

# ---------------------------------------------------------------------------
# STARTUP PICKER 2 — target devices (multi-select)
# ---------------------------------------------------------------------------

def pick_devices(sdk_root: Path, prev_value: list = None):
    """Returns sorted list of device names (empty=general), or _BACK. Cancel exits."""
    device_ref_dir = sdk_root / "doc" / "docs" / "Device_Reference"
    devices = sorted([
        f.stem for f in device_ref_dir.glob("*.html")
        if f.stem.lower() != "overview"
    ]) if device_ref_dir.exists() else []

    if not devices:
        print("  (No Device_Reference pages found — proceeding as general build.)")
        return []

    prev_set = set(prev_value or [])
    EXPLANATION = (
        "Which device(s) are you targeting?\n\n"
        "Selecting devices does two things:\n"
        "  1. Device spec pages: only your selected devices' pages are included\n"
        "     in docs/Device_Reference/ (saves space, reduces noise).\n"
        "  2. Compatibility checking: each sample is checked against your devices.\n"
        "     APIs not supported on your device(s) are flagged with \u26a0\ufe0f, so you\n"
        "     don't accidentally use unavailable features.\n\n"
        "Leave all unselected for a general build with no filtering.\n"
        "Select multiple devices if you develop for more than one watch model\n"
        "(e.g. venu3 + venu3s). Use the search box to filter the list."
    )

    try:
        import tkinter as tk
        result = {"val": None}
        win, _ = _make_dialog("Target Devices")
        tk.Label(win, text="Target Devices", font=("", 11, "bold"),
                 justify="left").pack(padx=12, pady=(16, 2), anchor="w")
        tk.Label(win, text=EXPLANATION, justify="left",
                 wraplength=490).pack(padx=12, pady=(4, 6), anchor="w")

        search_var = tk.StringVar()
        tk.Entry(win, textvariable=search_var).pack(fill="x", padx=12, pady=(0, 4))

        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=12)
        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")
        listbox = tk.Listbox(
            frame, yscrollcommand=scrollbar.set, height=10,
            selectmode=tk.MULTIPLE, exportselection=False
        )
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=listbox.yview)

        visible_devices: list = []

        def refresh(*_):
            nonlocal visible_devices
            q = search_var.get().lower()
            listbox.delete(0, "end")
            visible_devices = [d for d in devices if q in d.lower()]
            for d in visible_devices:
                listbox.insert("end", d)
            for i, d in enumerate(visible_devices):
                if d in prev_set:
                    listbox.selection_set(i)

        search_var.trace_add("write", refresh)
        refresh()

        btn_row = tk.Frame(win)
        btn_row.pack(pady=(4, 0))

        def select_all():
            listbox.selection_set(0, "end")

        def clear_all():
            listbox.selection_clear(0, "end")

        tk.Button(btn_row, text="Select all", width=12,
                  command=select_all).pack(side="left", padx=4)
        tk.Button(btn_row, text="Clear", width=12,
                  command=clear_all).pack(side="left", padx=4)

        def on_ok_selected(_event=None):
            sel = listbox.curselection()
            result["val"] = sorted([visible_devices[i] for i in sel])
            win.destroy()

        def on_ok_general():
            result["val"] = []
            win.destroy()

        def on_back():
            result["val"] = _BACK
            win.destroy()

        def on_cancel():
            win.destroy()
            sys.exit(0)

        ok_frame = tk.Frame(win)
        ok_frame.pack(pady=6)
        tk.Button(ok_frame, text="OK \u2014 general build (no device filtering)",
                  width=44, command=on_ok_general).pack(pady=2)
        tk.Button(ok_frame, text="OK \u2014 use selected devices",
                  width=44, command=on_ok_selected).pack(pady=2)
        win.bind("<Return>", on_ok_selected)
        _nav_buttons(win, tk, on_back=on_back, on_cancel=on_cancel)
        win.mainloop()

        if result["val"] is None:
            sys.exit(0)
        if result["val"] == _BACK:
            return _BACK
        chosen = result["val"]
        print(f"  Device filter: {', '.join(chosen) if chosen else 'general (all devices)'}")
        return chosen

    except Exception:
        pass

    # Console fallback
    print("\n=== Target Devices ===")
    print(EXPLANATION)
    print("\nAvailable devices:")
    for i, d in enumerate(devices, 1):
        print(f"  {i:3}) {d}")
    print("\nEnter device numbers separated by commas, or press Enter for general build.")
    print("Type 'back' to return, 'cancel' to exit.")
    while True:
        raw = input("Selection: ").strip().lower()
        if raw == "cancel":
            sys.exit(0)
        if raw == "back":
            return _BACK
        if not raw:
            print("  Device filter: general (all devices)")
            return []
        selected = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(devices):
                    selected.append(devices[idx])
            else:
                matches = [d for d in devices if part.lower() in d.lower()]
                selected.extend(matches)
        selected = sorted(set(selected))
        print(f"  Device filter: {', '.join(selected) if selected else 'general'}")
        return selected

# ---------------------------------------------------------------------------
# STARTUP PICKER 3 — compatibility / strict mode (only if devices selected)
# ---------------------------------------------------------------------------

def pick_compat_mode(device_filters: list, prev_value: tuple = None):
    """
    Returns (strict, require_all), or _BACK.  Cancel exits.
      strict=False → include all samples, annotate compatibility
      strict=True, require_all=True  → exclude if not listed for ALL selected devices
      strict=True, require_all=False → exclude if not listed for ANY selected device
    """
    if not device_filters:
        return False, True

    multi = len(device_filters) > 1

    EXPLANATION = (
        "How should samples that don't list your device(s) be handled?\n\n"
        "Background: SDK samples were written at various points in time. Older samples\n"
        "don't include newer devices in their manifest.xml — but the code patterns\n"
        "they demonstrate are often still valid, as long as the specific APIs they use\n"
        "are supported on your device. This script always checks API compatibility\n"
        "separately and flags problems with \u26a0\ufe0f regardless of which option you choose.\n\n"
        "Options:\n"
        "  Include all, annotate: Every sample is included. Each is marked \u2713 (listed)\n"
        "    or (unlisted). Recommended — you still get the full SDK picture, with\n"
        "    compatibility info to guide you.\n\n"
    )
    if multi:
        EXPLANATION += (
            f"  Strict \u2014 at least one: Only include samples listed for at least one\n"
            f"    of your {len(device_filters)} selected devices. Good if you just want\n"
            f"    samples relevant to your devices without being too restrictive.\n\n"
            f"  Strict \u2014 all selected: Only include samples listed for every one of\n"
            f"    your {len(device_filters)} devices. Most restrictive — use this if you\n"
            f"    need the sample to work across all your targets.\n"
        )
    else:
        EXPLANATION += (
            f"  Strict: Only include samples that explicitly list {device_filters[0]}.\n"
            f"    You'll see fewer samples but all of them are confirmed for your device.\n"
        )

    try:
        import tkinter as tk
        result = {"val": None}
        win, _ = _make_dialog("Sample Compatibility")
        tk.Label(win, text="Sample Compatibility", font=("", 11, "bold"),
                 justify="left").pack(padx=14, pady=(16, 2), anchor="w")
        tk.Label(win, text=EXPLANATION, justify="left",
                 wraplength=490).pack(padx=14, pady=(4, 8), anchor="w")

        def choose(strict, require_all):
            result["val"] = (strict, require_all)
            win.destroy()

        def on_back():
            result["val"] = _BACK
            win.destroy()

        def on_cancel():
            win.destroy()
            sys.exit(0)

        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=6)
        tk.Button(
            btn_frame,
            text="Include all, annotate compatibility (recommended)",
            width=54,
            command=lambda: choose(False, True),
        ).pack(pady=3)
        if multi:
            tk.Button(
                btn_frame,
                text=f"Strict \u2014 include if listed for at least one of {len(device_filters)} devices",
                width=54,
                command=lambda: choose(True, False),
            ).pack(pady=3)
            tk.Button(
                btn_frame,
                text=f"Strict \u2014 only if listed for ALL {len(device_filters)} devices",
                width=54,
                command=lambda: choose(True, True),
            ).pack(pady=3)
        else:
            tk.Button(
                btn_frame,
                text=f"Strict \u2014 only samples that list {device_filters[0]}",
                width=54,
                command=lambda: choose(True, True),
            ).pack(pady=3)

        _nav_buttons(win, tk, on_back=on_back, on_cancel=on_cancel)
        win.mainloop()
        if result["val"] is None:
            sys.exit(0)
        if result["val"] == _BACK:
            return _BACK
        strict, require_all = result["val"]
        mode = "annotate all" if not strict else ("strict/all" if require_all else "strict/any")
        print(f"  Compat mode: {mode}")
        return strict, require_all

    except Exception:
        pass

    # Console fallback
    print("\n=== Sample Compatibility Mode ===")
    print(EXPLANATION)
    print("Type 'back' to return, 'cancel' to exit.")
    if multi:
        opts = {"1": (False, True), "2": (True, False), "3": (True, True)}
        print("  1) Include all, annotate (recommended)")
        print(f"  2) Strict — at least one of {len(device_filters)} devices")
        print(f"  3) Strict — all {len(device_filters)} devices")
        prompt = "Choose (1/2/3): "
    else:
        opts = {"1": (False, True), "2": (True, True)}
        print("  1) Include all, annotate (recommended)")
        print(f"  2) Strict — only samples listing {device_filters[0]}")
        prompt = "Choose (1/2): "
    while True:
        raw = input(prompt).strip().lower()
        if raw == "cancel":
            sys.exit(0)
        if raw == "back":
            return _BACK
        if raw in opts:
            return opts[raw]
        if raw in ("", "1"):
            return False, True

# ---------------------------------------------------------------------------
# STARTUP PICKER 4 — file size threshold
# ---------------------------------------------------------------------------

def pick_threshold(prev_value: float = None):
    """Returns the threshold in KB, or _BACK.  Cancel exits."""
    default = prev_value if prev_value is not None else 18.0
    EXPLANATION = (
        "How large can a single file be before it gets split into parts?\n\n"
        "When converting SDK documentation, some files are very large — for example\n"
        "the full method list or a detailed API reference. Files exceeding this\n"
        "threshold are split at heading boundaries into numbered parts\n"
        "(e.g. Communications.md \u2192 Communications.md + Communications_part2.md).\n"
        "Each part gets a navigation banner so an AI knows to look for more parts.\n\n"
        "In consolidated mode, large samples are also split at source-file boundaries.\n\n"
        "How to choose your threshold:\n"
        "  \u2022 A rough rule of thumb: 1 KB of text \u2248 250 tokens.\n"
        "  \u2022 Set the threshold so a single part fits comfortably alongside the rest\n"
        "    of your conversation — ideally no more than 10\u201320% of the context window.\n"
        "  \u2022 32k token model  \u2192 try 18\u201325 KB\n"
        "  \u2022 64k token model  \u2192 try 32\u201340 KB\n"
        "  \u2022 128k token model \u2192 try 50\u201370 KB\n"
        "  \u2022 200k+ tokens     \u2192 try 80\u2013120 KB\n\n"
        "Setting this too high means a single file may fill most of the context window.\n"
        "Setting it too low creates many small parts and more file reads per topic."
    )
    try:
        import tkinter as tk
        result = {"val": None}
        win, _ = _make_dialog("File Size Threshold")
        tk.Label(win, text="File Size Threshold", font=("", 11, "bold"),
                 justify="left").pack(padx=14, pady=(16, 2), anchor="w")
        tk.Label(win, text=EXPLANATION, justify="left",
                 wraplength=490).pack(padx=14, pady=(4, 8), anchor="w")

        entry_frame = tk.Frame(win)
        entry_frame.pack(pady=4)
        tk.Label(entry_frame, text="Threshold (KB):").pack(side="left", padx=(0, 6))
        disp = str(int(default)) if default == int(default) else str(default)
        entry_var = tk.StringVar(value=disp)
        entry = tk.Entry(entry_frame, textvariable=entry_var, width=8)
        entry.pack(side="left")
        entry.focus_set()
        entry.selection_range(0, "end")

        err_label = tk.Label(win, text="", fg="red")
        err_label.pack()

        def on_ok(_event=None):
            raw = entry_var.get().strip()
            try:
                val = float(raw)
                if val <= 0:
                    raise ValueError
                result["val"] = val
                win.destroy()
            except ValueError:
                err_label.config(text="Please enter a positive number.")

        def on_back():
            result["val"] = _BACK
            win.destroy()

        def on_cancel():
            win.destroy()
            sys.exit(0)

        win.bind("<Return>", on_ok)
        tk.Button(win, text="OK", width=12, command=on_ok).pack(pady=8)
        _nav_buttons(win, tk, on_back=on_back, on_cancel=on_cancel)
        win.mainloop()
        if result["val"] is None:
            sys.exit(0)
        if result["val"] == _BACK:
            return _BACK
        print(f"  Split threshold: {result['val']} KB")
        return result["val"]
    except Exception:
        pass

    # Console fallback
    print("\n=== File Size Threshold ===")
    print(EXPLANATION)
    print("Type 'back' to return, 'cancel' to exit.")
    while True:
        raw = input(f"Threshold in KB (default {int(default)}): ").strip().lower()
        if raw == "cancel":
            sys.exit(0)
        if raw == "back":
            return _BACK
        if not raw:
            print(f"  Split threshold: {default} KB")
            return default
        try:
            val = float(raw)
            if val > 0:
                print(f"  Split threshold: {val} KB")
                return val
        except ValueError:
            pass
        print("  Please enter a positive number.")

# ---------------------------------------------------------------------------
# STARTUP PICKER 5 — output mode for samples/templates
# ---------------------------------------------------------------------------

def pick_output_mode(prev_value: str = None):
    """Returns 'separate' or 'consolidated', or _BACK.  Cancel exits."""
    EXPLANATION = (
        "How should sample and template source code be packaged?\n\n"
        "SEPARATE FILES (recommended for most setups):\n"
        "  Each sample gets its own folder. Source files (.mc) and project files\n"
        "  are kept as individual files in their original structure. A small\n"
        "  overview.md acts as an index. Best for:\n"
        "    \u2022 Local/small models with limited context windows (e.g. 32k tokens)\n"
        "    \u2022 Any setup where you only need one or two source files at a time\n"
        "    \u2022 When using tooling that can read individual files on demand\n\n"
        "CONSOLIDATED SINGLE FILE:\n"
        "  All source files for each sample are merged into one .md file\n"
        "  (split into parts if it exceeds the size threshold). Best for:\n"
        "    \u2022 Large-context models (e.g. Claude with full context, GPT-4 128k)\n"
        "      where reading one file is faster than multiple tool calls\n"
        "    \u2022 Browsing samples without any tooling — just open the .md file\n"
        "    \u2022 Copying a complete sample into a chat window in one paste\n\n"
        "Note: SDK documentation files are always split at the threshold regardless\n"
        "of this setting. This only affects how sample/template source is packaged."
    )
    try:
        import tkinter as tk
        result = {"val": None}
        win, _ = _make_dialog("Sample Output Format")
        tk.Label(win, text="Sample Output Format", font=("", 11, "bold"),
                 justify="left").pack(padx=14, pady=(16, 2), anchor="w")
        tk.Label(win, text=EXPLANATION, justify="left",
                 wraplength=490).pack(padx=14, pady=(4, 10), anchor="w")

        def choose(mode):
            result["val"] = mode
            win.destroy()

        def on_back():
            result["val"] = _BACK
            win.destroy()

        def on_cancel():
            win.destroy()
            sys.exit(0)

        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=6)
        tk.Button(btn_frame, text="Separate files  (one subdirectory per sample)",
                  width=50, command=lambda: choose("separate")).pack(pady=4)
        tk.Button(btn_frame, text="Consolidated  (one .md file per sample)",
                  width=50, command=lambda: choose("consolidated")).pack(pady=4)

        _nav_buttons(win, tk, on_back=on_back, on_cancel=on_cancel)
        win.mainloop()
        if result["val"] is None:
            sys.exit(0)
        if result["val"] == _BACK:
            return _BACK
        print(f"  Output mode: {result['val']}")
        return result["val"]
    except Exception:
        pass

    # Console fallback
    print("\n=== Sample Output Mode ===")
    print(EXPLANATION)
    print("Type 'back' to return, 'cancel' to exit.")
    while True:
        raw = input("Choose (1=separate, 2=consolidated): ").strip().lower()
        if raw == "cancel":
            sys.exit(0)
        if raw == "back":
            return _BACK
        if raw in ("1", ""):
            return "separate"
        if raw == "2":
            return "consolidated"

# ---------------------------------------------------------------------------
# DEVICE NAME MATCHING
# ---------------------------------------------------------------------------

def _norm_device(s: str) -> str:
    import unicodedata
    s = s.replace('\u2122', '').replace('\u00ae', '').replace('\u00a9', '')
    s = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in s if c.isascii() and c.isalnum()).lower()


def _device_in_supported_list(device_id: str, list_items: list) -> bool:
    norm_id = _norm_device(device_id)
    for item in list_items:
        if _norm_device(item) == norm_id:
            return True
        for part in item.split('/'):
            if _norm_device(part.strip()) == norm_id:
                return True
    return False

# ---------------------------------------------------------------------------
# FILE SPLITTING — hierarchical: ## → ### → list items
# ---------------------------------------------------------------------------

def _split_at_pattern(content: str, pattern: str, max_kb: float) -> list[str]:
    max_bytes = int(max_kb * 1024)
    sections = [s for s in re.split(pattern, content) if s]
    if len(sections) <= 1:
        return [content]
    chunks, current = [], ""
    for section in sections:
        candidate = current + section
        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = section
    if current:
        chunks.append(current)
    return chunks if len(chunks) > 1 else [content]


def _split_at_headings(content: str, max_kb: float) -> list[str]:
    """Try ## first, then ###, then list items as final fallback."""
    chunks_h2 = _split_at_pattern(content, r'(?m)(?=^## )', max_kb)
    if len(chunks_h2) > 1:
        result = []
        for chunk in chunks_h2:
            if len(chunk.encode("utf-8")) / 1024 > max_kb:
                sub = _split_at_pattern(chunk, r'(?m)(?=^### )', max_kb)
                result.extend(sub)
            else:
                result.append(chunk)
        return result if len(result) > 1 else [content]

    chunks_h3 = _split_at_pattern(content, r'(?m)(?=^### )', max_kb)
    if len(chunks_h3) > 1:
        return chunks_h3

    chunks_list = _split_at_pattern(content, r'(?m)(?=^- )', max_kb)
    if len(chunks_list) > 1:
        return chunks_list

    return [content]


def _apply_part_navigation(parts: list[str], display_name: str, base_stem: str) -> list[str]:
    """Insert a navigation banner after the # title line of each part."""
    n = len(parts)
    filenames = [
        f"{base_stem}.md" if i == 0 else f"{base_stem}_part{i + 1}.md"
        for i in range(n)
    ]
    result = []
    for i, part in enumerate(parts):
        part_num = i + 1
        other_links = " | ".join(
            f"[Part {j + 1}]({filenames[j]})" for j in range(n) if j != i
        )
        banner = (
            f"\n> \U0001f4c4 **Multi-part file \u2014 Part {part_num} of {n}.**"
            f" See also: {other_links}\n"
        )
        if i == 0:
            m = re.search(r'^# .+$', part, re.MULTILINE)
            if m:
                modified = part[:m.end()] + banner + part[m.end():]
            else:
                nl = part.find('\n')
                modified = (part[:nl] + banner + part[nl:]) if nl != -1 else (part + banner)
        else:
            title = f"# {display_name} (Part {part_num} of {n})"
            modified = title + banner + "\n" + part.lstrip('\n')
        result.append(modified)
    return result


def _write_split_or_single(
    content: str, display_name: str, out_path: Path, registry_key: str
) -> list[str]:
    """Write content, splitting if over MAX_FILE_KB. Returns list of filenames written."""
    size_kb = len(content.encode("utf-8")) / 1024
    if size_kb <= MAX_FILE_KB:
        out_path.write_text(content, encoding="utf-8")
        return [out_path.name]

    parts = _split_at_headings(content, MAX_FILE_KB)
    if len(parts) == 1:
        out_path.write_text(content, encoding="utf-8")
        return [out_path.name]

    base_stem = out_path.stem
    parts = _apply_part_navigation(parts, display_name, base_stem)
    filenames = []
    for i, part_content in enumerate(parts):
        part_path = out_path if i == 0 else out_path.parent / f"{base_stem}_part{i + 1}.md"
        part_path.write_text(part_content, encoding="utf-8")
        filenames.append(part_path.name)

    SPLIT_REGISTRY[registry_key] = filenames
    print(f"    \u2702 Split into {len(parts)} parts: {', '.join(filenames)}")
    return filenames

# ---------------------------------------------------------------------------
# UNSUPPORTED API INDEX (multi-device aware)
# ---------------------------------------------------------------------------

def _extract_preceding_symbol(tag) -> str | None:
    node = tag
    for _ in range(10):
        parent = getattr(node, 'parent', None)
        if parent is None:
            break
        parent_classes = parent.get('class', []) if hasattr(parent, 'get') else []
        if 'method_details' in parent_classes or 'details' in parent_classes:
            h3 = parent.find('h3', class_='signature')
            if h3:
                strong = h3.find('strong')
                raw = strong.get_text(strip=True) if strong else h3.get_text(strip=True)
                symbol = re.split(r'[\s\(\[]', raw)[0].strip()
                if symbol and len(symbol) >= 2 and not symbol.startswith('<'):
                    return symbol
            break
        node = parent
    for prev in tag.previous_siblings:
        if not hasattr(prev, 'name'):
            continue
        if prev.name in ('h1', 'h2', 'h3', 'h4', 'h5'):
            strong = prev.find('strong')
            raw = strong.get_text(strip=True) if strong else prev.get_text(strip=True)
            symbol = re.split(r'[\s\(\[]', raw)[0].strip()
            if symbol and len(symbol) >= 2 and not symbol.startswith('<'):
                return symbol
    return None


def build_unsupported_api_index():
    """
    Build UNSUPPORTED_APIS: for every selected device, scan the Toybox HTML docs
    and record each method/property that is NOT supported on that device.
    Result: {symbol: {"qualified": str, "devices": set[str]}}
    """
    global UNSUPPORTED_APIS
    if not DEVICE_FILTERS:
        return

    from bs4 import BeautifulSoup
    toybox_dir = SDK_ROOT / "doc" / "Toybox"
    if not toybox_dir.exists():
        print("  WARNING: doc/Toybox not found — skipping unsupported API index.")
        return

    print(f"\n=== Building unsupported API index for: {', '.join(DEVICE_FILTERS)} ===")
    found_total = 0

    for html_file in sorted(toybox_dir.rglob("*.html")):
        try:
            text = html_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        soup = BeautifulSoup(text, "html.parser")
        try:
            rel_parts = list(html_file.relative_to(toybox_dir).parts)
            dir_parts = rel_parts[:-1]
            file_stem = html_file.stem
            qualified_prefix = ".".join(dir_parts) + "." + file_stem if dir_parts else file_stem
        except Exception:
            qualified_prefix = html_file.stem

        for tag in soup.find_all(True):
            if tag.name not in ("p", "dt"):
                continue
            if tag.get_text(strip=True) not in ("Supported Devices:", "Supported Devices"):
                continue
            sibling = tag.find_next_sibling()
            if not sibling or sibling.name not in ("ul", "dd"):
                continue
            list_items = [li.get_text(strip=True) for li in sibling.find_all("li")]

            # Determine which selected devices are NOT in the supported list
            unsupported_on = [
                d for d in DEVICE_FILTERS
                if not _device_in_supported_list(d, list_items)
            ]
            if not unsupported_on:
                continue  # all selected devices are supported

            symbol = _extract_preceding_symbol(tag)
            if not symbol or len(symbol) < 4 or symbol.lower() in _GENERIC_SYMBOLS:
                continue

            qualified = (
                qualified_prefix if symbol == html_file.stem
                else f"{qualified_prefix}.{symbol}"
            )
            if symbol not in UNSUPPORTED_APIS:
                UNSUPPORTED_APIS[symbol] = {"qualified": qualified, "devices": set()}
                found_total += 1
            UNSUPPORTED_APIS[symbol]["devices"].update(unsupported_on)

    print(f"  Indexed {found_total} unsupported API symbols.")
    if UNSUPPORTED_APIS:
        sample_keys = sorted(UNSUPPORTED_APIS.keys())[:8]
        print(f"  Sample entries: {', '.join(sample_keys)}" + (" ..." if len(UNSUPPORTED_APIS) > 8 else ""))


def find_unsupported_apis_in_sample(
    project_dir: Path,
) -> list[tuple[str, str, list[str]]]:
    """
    Returns list of (symbol, qualified_name, [devices_it_is_unsupported_on])
    for any symbol found in the project's .mc files that is in UNSUPPORTED_APIS.
    """
    if not UNSUPPORTED_APIS:
        return []
    found: dict[str, dict] = {}
    for mc_file in project_dir.rglob("*.mc"):
        try:
            source = mc_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for symbol, info in UNSUPPORTED_APIS.items():
            if symbol in found:
                continue
            if re.search(r'\b' + re.escape(symbol) + r'\b', source):
                found[symbol] = info
    return sorted(
        [(sym, info["qualified"], sorted(info["devices"])) for sym, info in found.items()],
        key=lambda x: x[1],
    )

# ---------------------------------------------------------------------------
# HTML -> MARKDOWN CONVERSION (multi-device aware)
# ---------------------------------------------------------------------------

def html_file_to_markdown(html_path: Path) -> str:
    from bs4 import BeautifulSoup
    from markdownify import markdownify as md

    try:
        text = html_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"<!-- Error reading file: {e} -->"

    soup = BeautifulSoup(text, "html.parser")
    for tag in soup.find_all(["nav", "header", "footer"]):
        tag.decompose()

    if DEVICE_FILTERS:
        for tag in list(soup.find_all(True)):
            if tag.name not in ("p", "dt"):
                continue
            if tag.get_text(strip=True) not in ("Supported Devices:", "Supported Devices"):
                continue
            sibling = tag.find_next_sibling()
            if not sibling or sibling.name not in ("ul", "dd"):
                tag.decompose()
                continue

            list_items = [li.get_text(strip=True) for li in sibling.find_all("li")]
            unsupported_on = [
                d for d in DEVICE_FILTERS
                if not _device_in_supported_list(d, list_items)
            ]

            if not unsupported_on:
                # All selected devices supported — strip the section entirely
                sibling.decompose()
                tag.decompose()
            else:
                # Build a concise warning showing exactly which devices are affected
                supported_on = [d for d in DEVICE_FILTERS if d not in unsupported_on]
                if supported_on:
                    msg = (
                        f"\u26a0\ufe0f Not supported on: {', '.join(unsupported_on)}. "
                        f"(Supported on: {', '.join(supported_on)}.)"
                    )
                else:
                    msg = f"\u26a0\ufe0f Not supported on: {', '.join(unsupported_on)}."
                warning = soup.new_tag("p")
                strong = soup.new_tag("strong")
                strong.string = msg
                warning.append(strong)
                tag.replace_with(warning)
                sibling.decompose()

    LANG_MAP = {
        "java": "monkeyc", "typescript": "monkeyc", "javascript": "javascript",
        "example": "monkeyc", "xml": "xml", "lua": "lua", "python": "python",
        "bash": "bash", "json": "json",
    }
    for pre in soup.find_all("pre"):
        classes = pre.get("class", [])
        lang = next((LANG_MAP[cls] for cls in classes if cls in LANG_MAP), "monkeyc")
        pre["data-lang"] = lang
        code = pre.find("code")
        if code is not None:
            code["class"] = [f"language-{lang}"]

    for img in list(soup.find_all("img")):
        prev = img.previous_sibling
        nxt = img.next_sibling
        if prev and getattr(prev, 'name', None) == 'br':
            prev.decompose()
        if nxt and getattr(nxt, 'name', None) == 'br':
            nxt.decompose()
        img.decompose()

    for dl in soup.find_all("dl"):
        replacement_tags = []
        for child in dl.children:
            if hasattr(child, 'name'):
                if child.name == "dt":
                    new_p = soup.new_tag("p")
                    new_b = soup.new_tag("b")
                    new_b.string = child.get_text(strip=True)
                    new_p.append(new_b)
                    replacement_tags.append(new_p)
                elif child.name == "dd":
                    new_p = soup.new_tag("p")
                    new_p.extend(list(child.children))
                    replacement_tags.append(new_p)
        for t in replacement_tags:
            dl.insert_before(t)
        dl.decompose()

    main = soup.find("main") or soup.find("body") or soup
    result = md(
        str(main), heading_style="ATX", bullets="-",
        code_language_callback=lambda el: el.get("data-lang", ""),
        escape_underscores=False, strip=["script", "style", "img"],
    )
    result = result.replace("\r\n", "\n").replace("\r", "\n")
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r"\[([^\]]+)\]\([^)]*\.html[^)]*\)", r"`\1`", result)
    result = re.sub(r"\[show all\]\(#\)", "", result)
    result = re.sub(r"\[collapse\]\(#\)", "", result)
    result = re.sub(r"\n[ \t]+\n", "\n\n", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def convert_html_docs():
    print("\n=== Converting HTML documentation ===")
    out_base = OUTPUT_ROOT / "docs"
    converted = split_count = 0

    for source_root in HTML_SOURCE_DIRS:
        for dirpath, dirnames, filenames in os.walk(source_root):
            dirpath = Path(dirpath)
            dirnames[:] = [d for d in dirnames if d not in HTML_SKIP_DIRS and not d.startswith(".")]

            for filename in filenames:
                if not filename.lower().endswith(".html"):
                    continue
                # Device_Reference: include pages for any selected device
                if DEVICE_FILTERS and dirpath.name == "Device_Reference":
                    stem = Path(filename).stem.lower()
                    if stem != "overview" and stem not in {d.lower() for d in DEVICE_FILTERS}:
                        continue

                html_path = dirpath / filename
                try:
                    rel = html_path.relative_to(source_root)
                except ValueError:
                    rel = Path(filename)

                out_path = out_base / rel.with_suffix(".md")
                out_path.parent.mkdir(parents=True, exist_ok=True)
                print(f"  Converting: {rel}")

                full_content = f"<!-- Source: {rel} -->\n\n" + html_file_to_markdown(html_path)
                display_name = Path(filename).stem.replace("_", " ")
                registry_key = str(rel.with_suffix(".md")).replace("\\", "/")
                written = _write_split_or_single(full_content, display_name, out_path, registry_key)
                converted += 1
                if len(written) > 1:
                    split_count += 1

    print(f"  Done. Converted: {converted}, split into parts: {split_count}")

# ---------------------------------------------------------------------------
# PROJECT METADATA EXTRACTION (multi-device aware)
# ---------------------------------------------------------------------------

def _norm_manifest(text: str) -> str:
    """Strip device product list from manifest if device filters are active."""
    if not DEVICE_FILTERS:
        return text
    return re.sub(
        r"\s*<iq:products>.*?</iq:products>",
        "\n        <iq:products><!-- device list stripped --></iq:products>",
        text, flags=re.DOTALL,
    )


def extract_sample_metadata(project_dir: Path) -> dict:
    app_type = "unknown"
    imports: set[str] = set()
    classes: list[str] = []
    functions: list[str] = []
    # {device: True/False} — whether each selected device is listed in manifest
    device_listed: dict[str, bool] = {}

    manifest = project_dir / "manifest.xml"
    if manifest.exists():
        text = manifest.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'type="([^"]+)"', text)
        if m:
            app_type = m.group(1)
        if DEVICE_FILTERS:
            products_match = re.search(r'<iq:products>(.*?)</iq:products>', text, re.DOTALL)
            if products_match:
                product_ids = re.findall(r'<iq:product\s+id="([^"]+)"', products_match.group(1))
                for device in DEVICE_FILTERS:
                    device_listed[device] = any(
                        _norm_device(pid) == _norm_device(device) for pid in product_ids
                    )

    for mc_file in project_dir.rglob("*.mc"):
        try:
            text = mc_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in re.finditer(r"import Toybox\.(\w+)|using Toybox\.(\w+)", text):
            imports.add(m.group(1) or m.group(2))
        for m in re.finditer(r"^class\s+(\w+)", text, re.MULTILINE):
            classes.append(m.group(1))
        for m in re.finditer(
            r"^\s+(?:public\s+|private\s+|protected\s+)?function\s+(\w+)", text, re.MULTILINE
        ):
            functions.append(m.group(1))

    return {
        "app_type": app_type,
        "imports": sorted(imports),
        "classes": list(dict.fromkeys(classes)),
        "functions": list(dict.fromkeys(functions)),
        "device_listed": device_listed,
        "unsupported_apis": find_unsupported_apis_in_sample(project_dir),
    }

# ---------------------------------------------------------------------------
# PROJECT TREE
# ---------------------------------------------------------------------------

def _build_project_tree(project_dir: Path, display_name: str = None) -> str:
    lines = []
    skip_dirs = PROJECT_SKIP_DIRS | PROJECT_SKIP_BUILD_DIRS

    def _walk(directory: Path, prefix: str = ""):
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return
        entries = [e for e in entries if not e.name.startswith(".")]
        entries = [e for e in entries if not (e.is_dir() and e.name in skip_dirs)]
        for i, entry in enumerate(entries):
            connector = "\u2514\u2500\u2500 " if i == len(entries) - 1 else "\u251c\u2500\u2500 "
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                child_pfx = "    " if i == len(entries) - 1 else "\u2502   "
                _walk(entry, prefix + child_pfx)
            else:
                lines.append(f"{prefix}{connector}{entry.name}")

    lines.append(display_name or project_dir.name)
    _walk(project_dir)
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# COMPAT ANNOTATION HELPERS
# ---------------------------------------------------------------------------

def _device_listed_lines(device_listed: dict[str, bool], label: str) -> list[str]:
    """Generate per-device manifest listing lines for overview.md (samples only)."""
    if label != "samples" or not device_listed:
        return []
    lines = []
    for device, listed in sorted(device_listed.items()):
        if listed:
            lines.append(f"**{device} in manifest:** Yes \u2713  ")
        else:
            lines.append(
                f"**{device} in manifest:** No \u2014 sample predates or omits this device; "
                f"API patterns remain valid if the specific APIs are supported on {device}.  "
            )
    return lines


def _unsupported_api_lines(unsupported_apis: list, label: str) -> list[str]:
    """Generate per-symbol unsupported API warning lines (samples only)."""
    if label != "samples" or not unsupported_apis:
        return []
    lines = []
    for sym, qual, devices in unsupported_apis:
        dev_str = ", ".join(devices)
        lines.append(f"**\u26a0\ufe0f `{sym}` ({qual}) not supported on:** {dev_str}  ")
    return lines


def _compat_decorators(e: dict) -> str:
    """Return index header decorators (✓, (unlisted), ⚠️) for an index entry."""
    decorators = ""
    dl = e.get("device_listed", {})
    if dl:
        all_listed = all(dl.values())
        none_listed = not any(dl.values())
        if all_listed:
            decorators += " \u2713"
        elif none_listed:
            decorators += " (unlisted)"
        else:
            decorators += " (partial)"
    if e.get("unsupported_apis"):
        decorators += " \u26a0\ufe0f"
    return decorators

# ---------------------------------------------------------------------------
# SEPARATE MODE — copy files verbatim, tiny overview.md
# ---------------------------------------------------------------------------

def _copy_all_project_files(project_dir: Path, project_out_dir: Path) -> list[tuple[str, str]]:
    """
    Copy every project file verbatim. Returns (rel_path, content) for all .mc files
    so the caller can note them in overview.md.
    """
    skip_dirs = PROJECT_SKIP_DIRS | PROJECT_SKIP_BUILD_DIRS
    source_files: list[tuple[str, str]] = []

    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirpath = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for filename in filenames:
            if filename.startswith("."):
                continue
            src = dirpath / filename
            rel = src.relative_to(project_dir)
            dst = project_out_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)

            if filename == "manifest.xml" and DEVICE_FILTERS:
                try:
                    text = _norm_manifest(src.read_text(encoding="utf-8", errors="replace"))
                    dst.write_text(text, encoding="utf-8")
                except Exception:
                    shutil.copy2(src, dst)
            else:
                shutil.copy2(src, dst)

            if src.suffix.lower() == ".mc":
                try:
                    content = src.read_text(encoding="utf-8", errors="replace")
                except Exception as exc:
                    content = f"(Could not read: {exc})"
                source_files.append((str(rel).replace("\\", "/"), content))

    source_files.sort(key=lambda x: x[0])
    return source_files


def _build_overview_md(
    name: str, project_dir: Path, meta: dict, mc_rel_paths: list[str], label: str
) -> str:
    """
    Minimal overview: metadata + compat warnings + project tree.
    mc_rel_paths: relative paths to the original .mc files (not .md wrappers).
    """
    lines = [f"# {name}", ""]
    app_type = meta['app_type'] if meta['app_type'] != 'unknown' else 'unknown'
    lines.append(f"**App type:** `{app_type}`  ")
    if meta["imports"]:
        lines.append(f"**Toybox APIs used:** {', '.join(f'`Toybox.{i}`' for i in meta['imports'])}  ")
    if meta["classes"]:
        lines.append(f"**Classes:** {', '.join(f'`{c}`' for c in meta['classes'])}  ")

    lines.extend(_device_listed_lines(meta["device_listed"], label))
    lines.extend(_unsupported_api_lines(meta["unsupported_apis"], label))
    lines.append("")

    if mc_rel_paths:
        lines.append("## Source Files")
        lines.append("")
        for rel in mc_rel_paths:
            lines.append(f"- [{Path(rel).name}]({rel})")
        lines.append("")

    lines.append("## Project Structure")
    lines.append("")
    lines.append("```")
    lines.append(_build_project_tree(project_dir, name))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _process_separate(project_dir, name, output_dir, index_entries, label):
    meta = extract_sample_metadata(project_dir)
    if label == "templates":
        meta["device_listed"] = {}
        meta["unsupported_apis"] = []

    if STRICT_DEVICE_FILTER and label == "samples" and meta["device_listed"]:
        listed_for = [d for d, v in meta["device_listed"].items() if v]
        should_skip = (
            len(listed_for) < len(DEVICE_FILTERS) if STRICT_REQUIRE_ALL
            else len(listed_for) == 0
        )
        if should_skip:
            print(f"  Skipping (compat filter): {name}")
            return

    n_unsupported = len(meta.get("unsupported_apis", []))
    flag = f" [{n_unsupported} unsupported API(s)]" if n_unsupported else ""
    print(f"  Processing: {name}{flag}")

    project_out_dir = output_dir / name
    project_out_dir.mkdir(parents=True, exist_ok=True)

    source_files = _copy_all_project_files(project_dir, project_out_dir)
    mc_rel_paths = [rel for rel, _ in source_files]

    overview = _build_overview_md(name, project_dir, meta, mc_rel_paths, label)
    (project_out_dir / "overview.md").write_text(overview, encoding="utf-8")

    index_entries.append({
        "name": name,
        "app_type": meta["app_type"],
        "imports": meta["imports"],
        "classes": meta["classes"],
        "functions": meta["functions"],
        "device_listed": meta["device_listed"],
        "unsupported_apis": meta["unsupported_apis"],
        "overview_file": f"{name}/overview.md",
        "source_files": mc_rel_paths,
        "mode": "separate",
    })

# ---------------------------------------------------------------------------
# CONSOLIDATED MODE — one .md per sample, all source embedded
# ---------------------------------------------------------------------------

def _build_consolidated_md(name: str, project_dir: Path, meta: dict, label: str) -> str:
    """
    Build one large .md containing: metadata header, manifest.xml,
    monkey.jungle, and all .mc source files as ## sections.
    Device-variant resource directories (resources-*) are skipped.
    """
    lines = [f"# {name}", ""]
    app_type = meta['app_type'] if meta['app_type'] != 'unknown' else 'unknown'
    lines.append(f"**App type:** `{app_type}`  ")
    if meta["imports"]:
        lines.append(f"**Toybox APIs used:** {', '.join(f'`Toybox.{i}`' for i in meta['imports'])}  ")
    if meta["classes"]:
        lines.append(f"**Classes:** {', '.join(f'`{c}`' for c in meta['classes'])}  ")

    lines.extend(_device_listed_lines(meta["device_listed"], label))
    lines.extend(_unsupported_api_lines(meta["unsupported_apis"], label))
    lines.append("")
    lines.append("---")
    lines.append("")

    # Embed manifest.xml
    manifest = project_dir / "manifest.xml"
    if manifest.exists():
        text = _norm_manifest(manifest.read_text(encoding="utf-8", errors="replace"))
        lines += [f"## manifest.xml", "", "```xml", text.rstrip(), "```", ""]

    # Embed monkey.jungle
    jungle = project_dir / "monkey.jungle"
    if jungle.exists():
        text = jungle.read_text(encoding="utf-8", errors="replace")
        lines += ["## monkey.jungle", "", "```bash", text.rstrip(), "```", ""]

    # Embed all .mc source files
    skip_dirs = PROJECT_SKIP_DIRS | PROJECT_SKIP_BUILD_DIRS
    source_collected: list[tuple[str, str]] = []

    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirpath = Path(dirpath)
        dirnames[:] = [
            d for d in dirnames
            if d not in skip_dirs and not DEVICE_VARIANT_RESOURCE_RE.match(d)
        ]
        for filename in filenames:
            if filename.startswith(".") or not filename.endswith(".mc"):
                continue
            filepath = dirpath / filename
            rel = str(filepath.relative_to(project_dir)).replace("\\", "/")
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                content = f"(Could not read: {exc})"
            source_collected.append((rel, content))

    source_collected.sort(key=lambda x: x[0])
    for rel, content in source_collected:
        lines += [f"## {rel}", "", "```monkeyc", content.rstrip(), "```", ""]

    if not source_collected:
        lines.append("*(No .mc source files found.)*")
        lines.append("")

    return "\n".join(lines)


def _process_consolidated(project_dir, name, output_dir, index_entries, label):
    meta = extract_sample_metadata(project_dir)
    if label == "templates":
        meta["device_listed"] = {}
        meta["unsupported_apis"] = []

    if STRICT_DEVICE_FILTER and label == "samples" and meta["device_listed"]:
        listed_for = [d for d, v in meta["device_listed"].items() if v]
        should_skip = (
            len(listed_for) < len(DEVICE_FILTERS) if STRICT_REQUIRE_ALL
            else len(listed_for) == 0
        )
        if should_skip:
            print(f"  Skipping (compat filter): {name}")
            return

    n_unsupported = len(meta.get("unsupported_apis", []))
    flag = f" [{n_unsupported} unsupported API(s)]" if n_unsupported else ""
    print(f"  Processing: {name}{flag}")

    content = _build_consolidated_md(name, project_dir, meta, label)
    out_path = output_dir / f"{name}.md"
    registry_key = f"{label}/{name}.md"

    written_files = _write_split_or_single(content, name, out_path, registry_key)

    index_entries.append({
        "name": name,
        "app_type": meta["app_type"],
        "imports": meta["imports"],
        "classes": meta["classes"],
        "functions": meta["functions"],
        "device_listed": meta["device_listed"],
        "unsupported_apis": meta["unsupported_apis"],
        "overview_file": written_files[0],   # part 1 or only file
        "all_files": written_files,
        "source_files": [],
        "mode": "consolidated",
    })

# ---------------------------------------------------------------------------
# PROJECT PROCESSING DISPATCHER
# ---------------------------------------------------------------------------

def _process_single_project(project_dir, name, output_dir, index_entries, label):
    if SAMPLE_OUTPUT_MODE == "consolidated":
        _process_consolidated(project_dir, name, output_dir, index_entries, label)
    else:
        _process_separate(project_dir, name, output_dir, index_entries, label)


def process_projects(source_dir, output_dir, label, use_variants=False):
    print(f"\n=== Processing {label} ({SAMPLE_OUTPUT_MODE} mode) ===")
    output_dir.mkdir(parents=True, exist_ok=True)
    index_entries = []
    subdirs = sorted([d for d in source_dir.iterdir() if d.is_dir()])
    for project_dir in subdirs:
        if use_variants:
            variant_dirs = sorted([d for d in project_dir.iterdir() if d.is_dir()])
            if variant_dirs:
                for vd in variant_dirs:
                    _process_single_project(
                        vd, f"{project_dir.name}-{vd.name}", output_dir, index_entries, label
                    )
            else:
                _process_single_project(project_dir, project_dir.name, output_dir, index_entries, label)
        else:
            _process_single_project(project_dir, project_dir.name, output_dir, index_entries, label)
    return index_entries

# ---------------------------------------------------------------------------
# INDEX WRITERS
# ---------------------------------------------------------------------------

def write_index(index_entries: list[dict], output_dir: Path, title: str):
    is_samples = "Sample" in title
    lines = [
        f"# {title}", "",
        "Each entry links to the overview or consolidated file for that project.",
        "",
    ]

    if DEVICE_FILTERS and is_samples:
        dev_label = ", ".join(f"`{d}`" for d in DEVICE_FILTERS)
        if STRICT_DEVICE_FILTER:
            if len(DEVICE_FILTERS) == 1:
                lines.append(f"> **Device filter:** Strict \u2014 only entries that list {dev_label}.")
            else:
                req = "ALL" if STRICT_REQUIRE_ALL else "at least one"
                lines.append(f"> **Device filter:** Strict \u2014 only entries listed for {req} of {dev_label}.")
        else:
            if len(DEVICE_FILTERS) == 1:
                lines.append(
                    f"> **Device filter:** All entries included. "
                    f"\u2713 = listed for {dev_label}; (unlisted) = not listed."
                )
            else:
                lines.append(
                    f"> **Device filter:** All entries included. "
                    f"\u2713 = listed for all of {dev_label}; "
                    f"(partial) = listed for some; (unlisted) = listed for none."
                )
        if UNSUPPORTED_APIS:
            lines.append(f"> \u26a0\ufe0f = uses APIs not supported on one or more selected devices.")
        lines.append("")

    if SAMPLE_OUTPUT_MODE == "consolidated":
        lines += [
            "> **Format:** Consolidated — each sample is a single `.md` file",
            "> (split into parts if large). `SPLIT_FILES.md` lists any splits.",
            "",
        ]
    else:
        lines += [
            "> **Format:** Separate files — each sample has its own subdirectory.",
            "> Start with `overview.md`, then read specific `.mc` files as needed.",
            "",
        ]

    lines += ["---", ""]

    by_type: dict[str, list] = {}
    for e in index_entries:
        by_type.setdefault(e["app_type"], []).append(e)

    for app_type, entries in sorted(by_type.items()):
        lines.append(f"## App type: `{app_type}`")
        lines.append("")
        for e in sorted(entries, key=lambda x: x["name"]):
            decorators = _compat_decorators(e) if is_samples else ""
            header = f"### [{e['name']}]({e['overview_file']}){decorators}"
            lines.append(header)

            if e["imports"]:
                lines.append(f"- **Toybox APIs:** {', '.join(f'`{i}`' for i in e['imports'])}")
            if e["classes"]:
                lines.append(f"- **Classes:** {', '.join(f'`{c}`' for c in e['classes'])}")
            interesting = [f for f in e.get("functions", []) if f not in {
                "initialize", "getInitialView", "onStart", "onStop", "onUpdate", "onLayout"
            }]
            if interesting:
                lines.append(f"- **Key functions:** {', '.join(f'`{f}()`' for f in interesting[:8])}")

            if is_samples and e.get("unsupported_apis"):
                for sym, qual, devices in e["unsupported_apis"]:
                    lines.append(f"- **\u26a0\ufe0f `{sym}` not on:** {', '.join(devices)}")

            # Consolidated: list part files if split
            if e.get("mode") == "consolidated":
                all_files = e.get("all_files", [e["overview_file"]])
                if len(all_files) > 1:
                    part_links = " | ".join(f"[Part {i+1}]({f})" for i, f in enumerate(all_files))
                    lines.append(f"- **Parts:** {part_links}")

            # Separate: list source .mc files
            if e.get("mode") == "separate" and e.get("source_files"):
                lines.append(f"- **Source files:** {', '.join(f'`{Path(p).name}`' for p in e['source_files'])}")

            lines.append("")

    index_name = "SAMPLES_INDEX.md" if is_samples else "TEMPLATES_INDEX.md"
    (output_dir / index_name).write_text("\n".join(lines), encoding="utf-8")
    print(f"  Index written: {index_name}")

# ---------------------------------------------------------------------------
# SPLIT FILE CATALOG
# ---------------------------------------------------------------------------

def write_split_catalog():
    if not SPLIT_REGISTRY:
        return
    lines = [
        "# Split Files Catalog",
        "",
        f"Files exceeding {MAX_FILE_KB} KB were split at heading boundaries "
        "(## then ### then list items).",
        "",
        "> **Important for LLMs:** When you read Part 1 of a split file, check the",
        "> navigation banner for additional parts before assuming you have the full content.",
        "",
        "---",
        "",
    ]
    by_dir: dict[str, list] = {}
    for key, parts in sorted(SPLIT_REGISTRY.items()):
        prefix = str(Path(key).parent)
        by_dir.setdefault(prefix, []).append((key, parts))
    for dir_prefix, entries in sorted(by_dir.items()):
        lines.append(f"## {dir_prefix}/")
        lines.append("")
        for key, parts in sorted(entries):
            part_links = " | ".join(f"`{p}`" for p in parts)
            lines.append(f"- **{Path(key).name}** \u2192 {part_links}")
        lines.append("")
    out_path = OUTPUT_ROOT / "SPLIT_FILES.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Split catalog written: {out_path.name} ({len(SPLIT_REGISTRY)} split files)")

# ---------------------------------------------------------------------------
# MASTER INDEX
# ---------------------------------------------------------------------------

def write_master_index():
    dev_line = (
        f"Device filter: {', '.join(DEVICE_FILTERS)}"
        if DEVICE_FILTERS else "Device filter: general (all devices)"
    )
    mode_line = f"Sample format: {SAMPLE_OUTPUT_MODE}"
    threshold_line = f"Split threshold: {MAX_FILE_KB} KB"

    if SAMPLE_OUTPUT_MODE == "separate":
        sample_structure = (
            "Each sample/template has its own subdirectory:\n\n"
            "```\n"
            "samples/SampleName/\n"
            "  overview.md    <- metadata, compat warnings, project tree (tiny)\n"
            "  source/App.mc  <- all original project files copied verbatim\n"
            "  manifest.xml\n"
            "  resources/\n"
            "```\n\n"
            "Start with `overview.md` for context, then read specific files as needed."
        )
    else:
        sample_structure = (
            "Each sample/template is a single `.md` file (split if large):\n\n"
            "```\n"
            "samples/SampleName.md       <- metadata + manifest + all .mc source\n"
            "samples/SampleName_part2.md <- continuation if split\n"
            "```\n\n"
            "Read the file (or part 1 + follow navigation banners for further parts)."
        )

    dev_spec = (
        f"docs/docs/Device_Reference/{DEVICE_FILTERS[0]}.md"
        if len(DEVICE_FILTERS) == 1
        else "docs/docs/Device_Reference/<device>.md  (one per selected device)"
        if DEVICE_FILTERS
        else "docs/docs/Device_Reference/<device>.md"
    )

    lines = [
        "# Garmin Connect IQ SDK \u2014 Consolidated LLM Reference (INDEX)",
        "",
        "SDK version 8.4.1 (Feb 2026). All files optimised for LLM consumption.",
        "",
        f"**{dev_line}**  ",
        f"**{mode_line}**  ",
        f"**{threshold_line}**  ",
        "",
        "## Files in this directory",
        "",
        "- `INDEX.md` \u2014 this file",
        "- `SPLIT_FILES.md` \u2014 lists all doc files split into parts",
        "- `docs/` \u2014 converted HTML documentation",
        "- `samples/` \u2014 sample projects",
        "- `templates/` \u2014 template projects",
        "",
        "---",
        "",
        "## Multi-Part Files",
        "",
        f"Files exceeding {MAX_FILE_KB} KB are split at `##`, then `###`, then list-item",
        "boundaries. Each part has a navigation banner. `SPLIT_FILES.md` catalogs all splits.",
        "",
        "---",
        "",
        "## Samples and Templates Structure",
        "",
        sample_structure,
        "",
        "---",
        "",
        "## Key Files",
        "",
        "| Task | File |",
        "|------|------|",
        "| Split file catalog | `SPLIT_FILES.md` |",
        "| Browse all classes | `docs/class_list.md` |",
        "| Find a method by name | `docs/method_list.md` |",
        "| All Toybox modules | `docs/index.md` |",
        "| App types & lifecycle | `docs/docs/Connect_IQ_Basics/App_Types.md` |",
        "| Monkey C syntax | `docs/docs/Monkey_C/Basic_Syntax.md` |",
        "| monkey.jungle format | `docs/docs/Reference_Guides/Jungle_Reference.md` |",
        "| Drawing to screen | `docs/docs/Core_Topics/Graphics.md` |",
        "| Input handling | `docs/docs/Core_Topics/Input_Handling.md` |",
        "| Sensors | `docs/docs/Core_Topics/Sensors.md` |",
        "| Persisting data | `docs/docs/Core_Topics/Persisting_Data.md` |",
        "| Manifest & permissions | `docs/docs/Core_Topics/Manifest_and_Permissions.md` |",
        f"| Device specs | `{dev_spec}` |",
        "| DataField base class | `docs/Toybox/WatchUi/DataField.md` |",
        "| New data field | `templates/datafield-simple/overview.md` |",
        "| New watch app | `templates/watch-app-simple/overview.md` |",
        "| Activity tracking example | `samples/ActivityTracking/overview.md` |",
        "| GPS/Position example | `samples/PositionSample/overview.md` |",
        "| BLE example | `samples/NordicThingy52/overview.md` |",
        "",
        "---",
        "",
        "*Generated from Garmin Connect IQ SDK 8.4.1 \u2014 Feb 2026*",
    ]
    content = "\n".join(lines)
    (OUTPUT_ROOT / "INDEX.md").write_bytes(content.encode("utf-8"))
    print(f"  Master INDEX written.")

# ---------------------------------------------------------------------------
# IMAGE CATALOG
# ---------------------------------------------------------------------------

def write_image_catalog():
    IMAGE_ROOT = SDK_ROOT / "doc" / "resources"
    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".gif"}
    SKIP_FILES = {
        "chopper-monkey.png", "learning-monkey.png", "captain-monkey.png",
        "artsy-monkey.png", "smart-monkey.png", "sculptor-monkey.png",
        "wizard-monkey.png", "cyclist-monkey.png", "archy-monkey.png",
        "swirl.gif", "clock.gif", "giphy.gif", "jquery-1.11.3.min.js",
    }
    DESCRIPTIONS = {
        "app-lifecycle.png": "App lifecycle flow diagram",
        "layout.png": "Layout system diagram",
        "amoled_layout.png": "AMOLED display layout constraints",
        "picker-layout.png": "Picker UI layout",
        "profiler.png": "Profiler tool screenshot",
        "16_color_palette.png": "Garmin 16-colour palette reference",
        "page-loops.png": "Page loop navigation pattern diagram",
        "one-field-layout.png": "Single data field layout",
        "two-field-layout.png": "Two data field layout",
        "three-field-layout.png": "Three data field layout",
    }
    SECTION_NOTES = {
        "programmers-guide": "Diagrams referenced in the programmer's guide.",
        "personality-library": "Screenshots of Garmin's Personality UI components.",
        "ux-guide": "UX pattern diagrams from the User Experience Guidelines.",
        "faq": "Images referenced in the Connect IQ FAQ.",
        "device-reference": "Per-device SVG layout diagrams for data field layouts.",
    }
    lines = [
        "# SDK Image Catalog", "",
        "Images from the SDK (not readable by text-only LLMs, but paths are listed here).", "",
        f"**SDK root:** `{SDK_ROOT}`", "", "---", "",
    ]
    for folder_name in ["programmers-guide", "personality-library", "ux-guide", "faq"]:
        folder_path = IMAGE_ROOT / folder_name
        if not folder_path.exists():
            continue
        files = sorted([
            f for f in folder_path.iterdir()
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS and f.name not in SKIP_FILES
        ], key=lambda p: p.name.lower())
        if not files:
            continue
        lines.append(f"## {folder_name}/")
        lines.append("")
        if folder_name in SECTION_NOTES:
            lines.append(f"*{SECTION_NOTES[folder_name]}*")
            lines.append("")
        for f in files:
            rel = f.relative_to(SDK_ROOT)
            desc = DESCRIPTIONS.get(f.name, f.stem.replace("_", " ").replace("-", " ").title())
            ext_note = " `[SVG]`" if f.suffix.lower() == ".svg" else ""
            lines.append(f"- **`{f.name}`**{ext_note} \u2014 {desc}  `{rel}`")
        lines.append("")

    device_ref_path = IMAGE_ROOT / "device-reference"
    if device_ref_path.exists():
        lines += ["## device-reference/", "", f"*{SECTION_NOTES['device-reference']}*", ""]
        all_device_dirs = sorted([d for d in device_ref_path.iterdir() if d.is_dir()])
        # Show only selected devices if filtering, otherwise all
        device_dirs = (
            [d for d in all_device_dirs if d.name.lower() in {df.lower() for df in DEVICE_FILTERS}]
            if DEVICE_FILTERS else all_device_dirs
        )
        for d in device_dirs:
            svgs = sorted(
                [f.name for f in d.iterdir() if f.suffix.lower() == ".svg"],
                key=lambda n: int(re.search(r'(\d+)', n).group(1)) if re.search(r'(\d+)', n) else 0
            )
            if svgs:
                layouts = ", ".join(f"`{s}`" for s in svgs)
                lines.append(f"- **`{d.name}`** \u2014 {layouts}  `{d.relative_to(SDK_ROOT)}`")
        lines.append("")

    out_path = OUTPUT_ROOT / "SDK_IMAGE_CATALOG.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Image catalog written: {out_path.name}")

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    global SDK_ROOT, OUTPUT_ROOT, DEVICE_FILTERS, STRICT_DEVICE_FILTER
    global STRICT_REQUIRE_ALL, SAMPLE_OUTPUT_MODE, MAX_FILE_KB
    global HTML_SOURCE_DIRS, SAMPLES_DIR, TEMPLATES_DIR

    check_dependencies()

    # --- Wizard loop with Back support ---
    # State variables preserve selections when the user goes back.
    _sdk     = None
    _devices = None
    _compat  = None
    _thresh  = None
    _mode    = None

    step = 0
    while step < 5:
        if step == 0:
            r = pick_sdk_root(str(_sdk) if _sdk else None)
            _sdk = r; step = 1

        elif step == 1:
            r = pick_devices(_sdk, _devices)
            if r == _BACK:  step = 0; continue
            _devices = r
            # Skip compat step when no devices selected
            step = 2 if _devices else 3

        elif step == 2:
            r = pick_compat_mode(_devices, _compat)
            if r == _BACK:  step = 1; continue
            _compat = r; step = 3

        elif step == 3:
            r = pick_threshold(_thresh)
            if r == _BACK:
                step = 2 if _devices else 1; continue
            _thresh = r; step = 4

        elif step == 4:
            r = pick_output_mode(_mode)
            if r == _BACK:  step = 3; continue
            _mode = r; step = 5

    SDK_ROOT       = _sdk
    DEVICE_FILTERS = _devices
    if _compat is not None:
        STRICT_DEVICE_FILTER, STRICT_REQUIRE_ALL = _compat
    else:
        STRICT_DEVICE_FILTER, STRICT_REQUIRE_ALL = False, True
    MAX_FILE_KB    = _thresh
    SAMPLE_OUTPUT_MODE = _mode

    OUTPUT_ROOT  = SDK_ROOT / "Consolidated SDK"
    HTML_SOURCE_DIRS = [SDK_ROOT / "doc"]
    SAMPLES_DIR  = SDK_ROOT / "samples"
    TEMPLATES_DIR = SDK_ROOT / "bin" / "templates"

    print(f"\nSDK root:       {SDK_ROOT}")
    print(f"Output root:    {OUTPUT_ROOT}")
    print(f"Devices:        {', '.join(DEVICE_FILTERS) if DEVICE_FILTERS else 'all (general build)'}")
    print(f"Compat mode:    {'strict/' + ('all' if STRICT_REQUIRE_ALL else 'any') if STRICT_DEVICE_FILTER else 'annotate all'}")
    print(f"Output mode:    {SAMPLE_OUTPUT_MODE}")
    print(f"Split threshold: {MAX_FILE_KB} KB")

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    build_unsupported_api_index()
    convert_html_docs()

    samples_out = OUTPUT_ROOT / "samples"
    sample_entries = process_projects(SAMPLES_DIR, samples_out, "samples", use_variants=False)
    write_index(sample_entries, samples_out, "Connect IQ Sample Projects Index")

    templates_out = OUTPUT_ROOT / "templates"
    template_entries = process_projects(TEMPLATES_DIR, templates_out, "templates", use_variants=True)
    write_index(template_entries, templates_out, "Connect IQ Templates Index")

    write_split_catalog()
    write_master_index()
    write_image_catalog()

    print("\n=== All done! ===")
    print(f"Output written to: {OUTPUT_ROOT}")
    if SPLIT_REGISTRY:
        print(f"Files split into parts: {len(SPLIT_REGISTRY)} (see SPLIT_FILES.md)")


if __name__ == "__main__":
    main()

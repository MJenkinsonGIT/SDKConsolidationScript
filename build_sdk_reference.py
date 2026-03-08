#!/usr/bin/env python3
"""
build_sdk_reference.py
Converts the Garmin Connect IQ SDK documentation into LLM-friendly Markdown files.

Output structure:
  Consolidated SDK/
  ├── docs/        <- HTML docs converted to Markdown, mirroring doc/ structure
  ├── samples/     <- One consolidated .md per sample project + SAMPLES_INDEX.md
  └── templates/   <- One consolidated .md per template type + TEMPLATES_INDEX.md

Requirements:
    pip install beautifulsoup4 markdownify

Usage:
    python build_sdk_reference.py
"""

import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIGURATION — resolved at runtime via directory picker
# ---------------------------------------------------------------------------

def pick_sdk_root() -> Path:
    """
    Open a native folder-picker dialog so the user can select their SDK root.
    Falls back to a plain text prompt if tkinter is unavailable.
    Returns a validated Path to the SDK root directory.
    """
    chosen = None

    # Try the native GUI picker first
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox

        root_win = tk.Tk()
        root_win.withdraw()  # hide the blank root window
        root_win.attributes("-topmost", True)

        print("Opening folder picker — select your Connect IQ SDK root directory...")
        chosen = filedialog.askdirectory(
            parent=root_win,
            title="Select Connect IQ SDK root directory",
        )
        root_win.destroy()
    except Exception:
        pass  # tkinter not available — fall through to text prompt

    # Fall back to plain text input if the GUI wasn't available or was cancelled
    if not chosen:
        print("\nEnter the full path to your Connect IQ SDK root directory.")
        print("Example: C:\\Users\\YourName\\AppData\\Roaming\\Garmin\\ConnectIQ\\Sdks\\connectiq-sdk-win-8.4.1-...")
        chosen = input("SDK root: ").strip().strip('"').strip("'")

    sdk = Path(chosen)

    # Basic validation — check for a known SDK subdirectory
    if not (sdk / "doc").is_dir():
        print(f"\nERROR: '{sdk}' does not look like a Connect IQ SDK root (no 'doc/' folder found).")
        print("Please re-run the script and select the correct directory.")
        sys.exit(1)

    return sdk


# These are set in main() after the user picks the SDK root / device
SDK_ROOT: Path = None
OUTPUT_ROOT: Path = None
DEVICE_FILTER: str | None = None  # None = general (all devices); str = device ID e.g. 'venu3'


def pick_device(sdk_root: Path) -> str | None:
    """
    Prompt the user to choose a specific target device or a general (all-devices) build.
    Returns the device ID string (e.g. 'venu3') or None for general mode.
    """
    device_ref_dir = sdk_root / "doc" / "docs" / "Device_Reference"
    devices = sorted([
        f.stem for f in device_ref_dir.glob("*.html")
        if f.stem.lower() != "overview"
    ]) if device_ref_dir.exists() else []

    if not devices:
        print("  (No Device_Reference pages found — proceeding as general build.)")
        return None

    # Try tkinter GUI — searchable listbox
    try:
        import tkinter as tk

        result = [None]  # mutable container so the inner callback can write to it

        win = tk.Tk()
        win.title("Target device")
        win.geometry("380x480")
        win.resizable(False, True)
        win.attributes("-topmost", True)

        tk.Label(
            win,
            text="Select your target device, or choose\n\"← All devices\" for a general build:",
            justify="left",
        ).pack(padx=12, pady=(12, 4), anchor="w")

        search_var = tk.StringVar()
        search_entry = tk.Entry(win, textvariable=search_var)
        search_entry.pack(fill="x", padx=12, pady=(0, 6))
        search_entry.focus_set()

        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=12)
        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")
        listbox = tk.Listbox(frame, yscrollcommand=scrollbar.set, height=18, exportselection=False)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=listbox.yview)

        ALL_LABEL = "\u2190 All devices \u2014 general build"
        all_items = [ALL_LABEL] + devices

        def refresh(*_):
            q = search_var.get().lower()
            listbox.delete(0, "end")
            for item in all_items:
                if q in item.lower():
                    listbox.insert("end", item)
            if listbox.size():
                listbox.selection_set(0)

        search_var.trace_add("write", refresh)
        refresh()

        def on_ok(_event=None):
            sel = listbox.curselection()
            if sel:
                val = listbox.get(sel[0])
                result[0] = None if val == ALL_LABEL else val
            win.destroy()

        listbox.bind("<Double-Button-1>", on_ok)
        win.bind("<Return>", on_ok)

        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=8)
        tk.Button(btn_frame, text="OK", width=12, command=on_ok).pack(side="left", padx=6)
        tk.Button(btn_frame, text="Cancel", width=12,
                  command=win.destroy).pack(side="left", padx=6)

        win.mainloop()

        chosen = result[0]
        label = chosen if chosen else "general (all devices)"
        print(f"  Device filter: {label}")
        return chosen

    except Exception:
        pass  # tkinter unavailable — fall through to text prompt

    # Text fallback
    print("\n=== Target Device ===")
    print("  0) All devices (general build)")
    for i, d in enumerate(devices, 1):
        print(f"  {i}) {d}")
    while True:
        raw = input("\nEnter number or device name (0 = general): ").strip()
        if not raw or raw == "0":
            print("  Device filter: general (all devices)")
            return None
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(devices):
                print(f"  Device filter: {devices[idx]}")
                return devices[idx]
        matches = [d for d in devices if raw.lower() in d.lower()]
        if len(matches) == 1:
            print(f"  Device filter: {matches[0]}")
            return matches[0]
        elif len(matches) > 1:
            print(f"  Multiple matches: {', '.join(matches[:10])}. Be more specific.")
        else:
            print(f"  No device matching '{raw}'. Try again.")

# Folders to skip entirely during HTML conversion
HTML_SKIP_DIRS = {
    "css", "js", "branding", "resources",
    # skip per-sample auto-generated docs folders
}

# Subdirectory names to skip when reading project files
PROJECT_SKIP_DIRS = {"docs", "css", "js", ".git", ".vscode"}

# File types to include when consolidating a sample/template project
PROJECT_TEXT_EXTENSIONS = {".mc", ".xml", ".jungle", ".json", ".txt", ".md"}
# Resource files that are worth including (layout/menu/string XMLs etc.)
# We exclude image files and CSS/JS automatically via extension filtering above.

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
        print("Missing required packages. Install them with:")
        print(f"  pip install {' '.join(missing)}")
        sys.exit(1)

# ---------------------------------------------------------------------------
# DEVICE NAME MATCHING
# ---------------------------------------------------------------------------

def _norm_device(s: str) -> str:
    """
    Normalise a device display name or ID to lowercase ASCII alphanumeric
    so that e.g. 'Venu\u00ae 3' and 'venu3' compare equal.

    Key rules:
    - Strip \u00ae (\u00ae), \u2122 (\u2122) and \u00a9 (\u00a9) BEFORE NFKD
      because \u2122 decomposes to the two ASCII letters 'TM' which would
      corrupt matches (e.g. 'D2\u2122 Bravo' -> 'd2tmbravo' not 'd2bravo').
    - NFKD + ASCII filter converts accented letters: \u0113 (\u0113) -> 'e', etc.
    """
    import unicodedata
    s = s.replace('\u2122', '').replace('\u00ae', '').replace('\u00a9', '')
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if c.isascii() and c.isalnum())
    return s.lower()


def _device_in_supported_list(device_id: str, list_items: list) -> bool:
    """
    Return True if device_id matches any item in a 'Supported Devices' list.

    Handles combined entries like 'Edge\u00ae 1040 / 1040 Solar' by splitting
    on '/' and testing each part independently, so 'edge1040' matches.
    """
    norm_id = _norm_device(device_id)
    for item in list_items:
        # Test the full item first (handles simple cases)
        if _norm_device(item) == norm_id:
            return True
        # Test each '/' separated part (handles combined entries)
        for part in item.split('/'):
            if _norm_device(part.strip()) == norm_id:
                return True
    return False


# ---------------------------------------------------------------------------
# HTML -> MARKDOWN CONVERSION
# ---------------------------------------------------------------------------

def html_file_to_markdown(html_path: Path) -> str:
    """Read an HTML file and return clean Markdown of the main content only."""
    from bs4 import BeautifulSoup
    from markdownify import markdownify as md

    try:
        text = html_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"<!-- Error reading file: {e} -->"

    soup = BeautifulSoup(text, "html.parser")

    # Remove nav, header, footer — we only want the main content
    for tag in soup.find_all(["nav", "header", "footer"]):
        tag.decompose()

    # Handle "Supported Devices" sections according to build mode:
    #
    # General build   — keep everything so the LLM sees full device compatibility.
    #
    # Device-specific — two cases:
    #   • Device IS in the list  → strip the whole section (saves tokens; support is implied).
    #   • Device NOT in the list → replace with a bold warning so the LLM knows the
    #     API/member is unavailable on the target device.
    if DEVICE_FILTER is not None:
        for tag in list(soup.find_all(True)):
            if tag.name in ("p", "dt") and tag.get_text(strip=True) in ("Supported Devices:", "Supported Devices"):
                sibling = tag.find_next_sibling()
                if sibling and sibling.name in ("ul", "dd"):
                    list_items = [li.get_text(strip=True) for li in sibling.find_all("li")]
                    if _device_in_supported_list(DEVICE_FILTER, list_items):
                        # Supported — strip silently to save tokens
                        sibling.decompose()
                        tag.decompose()
                    else:
                        # Not supported — replace label + list with a visible warning
                        warning = soup.new_tag("p")
                        strong = soup.new_tag("strong")
                        strong.string = f"\u26a0\ufe0f Not supported on {DEVICE_FILTER}."
                        warning.append(strong)
                        tag.replace_with(warning)
                        sibling.decompose()
                else:
                    # Label with no list sibling — remove it
                    tag.decompose()

    # Transfer language hints from <pre class="... java ..."> to inner <code> tag
    # Garmin uses "java" and "typescript" class names for Monkey C code
    LANG_MAP = {
        "java": "monkeyc",
        "typescript": "monkeyc",
        "javascript": "javascript",
        "example": "monkeyc",
        "xml": "xml",
        "lua": "lua",
        "python": "python",
        "bash": "bash",
        "json": "json",
    }
    for pre in soup.find_all("pre"):
        classes = pre.get("class", [])
        lang = None
        for cls in classes:
            if cls in LANG_MAP:
                lang = LANG_MAP[cls]
                break
        if lang is None:
            lang = "monkeyc"  # default: assume Monkey C
        # Set data-lang on <pre> — code_language_callback receives the <pre> element, not <code>
        pre["data-lang"] = lang
        code = pre.find("code")
        if code is not None:
            code["class"] = [f"language-{lang}"]

    # Remove all <img> elements plus any immediately adjacent <br> siblings.
    # Garmin device reference pages use <br><img><br> between headings and tables;
    # markdownify strips the img but leaves the orphaned <br> tags, which become
    # whitespace-only lines. Handling this in BeautifulSoup before conversion is cleaner.
    for img in list(soup.find_all("img")):
        prev = img.previous_sibling
        nxt  = img.next_sibling
        if prev and getattr(prev, 'name', None) == 'br':
            prev.decompose()
        if nxt and getattr(nxt, 'name', None) == 'br':
            nxt.decompose()
        img.decompose()

    # Flatten HTML definition lists (<dl>/<dt>/<dd>) — markdownify renders them with
    # ':   ' PHP Markdown Extra prefix, which is non-standard and looks odd in LLM context.
    # Convert to: **term** bold paragraph + plain paragraph for the definition.
    # Must be done before md() call so the soup changes are included in conversion.
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
        for tag in replacement_tags:
            dl.insert_before(tag)
        dl.decompose()

    # Try to extract just <main> first; fall back to <body>
    main = soup.find("main") or soup.find("body") or soup

    # Convert to markdown
    result = md(
        str(main),
        heading_style="ATX",
        bullets="-",
        code_language_callback=lambda el: el.get("data-lang", ""),
        escape_underscores=False,
        strip=["script", "style", "img"],
    )

    # Normalise line endings — source HTML on Windows uses CRLF which breaks \n-based regexes
    result = result.replace("\r\n", "\n").replace("\r", "\n")

    # Clean up excessive blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)

    # Strip broken relative HTML links — replace [text](path.html) with just `text`
    result = re.sub(r"\[([^\]]+)\]\([^)]*\.html[^)]*\)", r"`\1`", result)

    # Remove leftover [show all](#) collapse widget artifacts
    result = re.sub(r"\[show all\]\(#\)", "", result)
    result = re.sub(r"\[collapse\]\(#\)", "", result)

    # Strip lines that contain only whitespace (left behind by stripped <img> wrappers etc.)
    result = re.sub(r"\n[ \t]+\n", "\n\n", result)

    # Clean up any blank lines that stripping may have created
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result.strip()


def convert_html_docs():
    """Walk all HTML source dirs and write converted Markdown to OUTPUT_ROOT/docs/."""
    print("\n=== Converting HTML documentation ===")
    out_base = OUTPUT_ROOT / "docs"
    converted = 0

    for source_root in HTML_SOURCE_DIRS:
        for dirpath, dirnames, filenames in os.walk(source_root):
            dirpath = Path(dirpath)

            # Prune skip dirs in-place so os.walk won't descend into them
            dirnames[:] = [
                d for d in dirnames
                if d not in HTML_SKIP_DIRS and not d.startswith(".")
            ]

            for filename in filenames:
                if not filename.lower().endswith(".html"):
                    continue

                # In device-specific builds, skip Device_Reference pages for other devices
                if DEVICE_FILTER is not None and dirpath.name == "Device_Reference":
                    stem = Path(filename).stem.lower()
                    if stem not in ("overview", DEVICE_FILTER.lower()):
                        continue

                html_path = dirpath / filename

                # Compute relative path from SDK_ROOT/doc to preserve structure
                try:
                    rel = html_path.relative_to(source_root)
                except ValueError:
                    rel = Path(filename)

                out_path = out_base / rel.with_suffix(".md")
                out_path.parent.mkdir(parents=True, exist_ok=True)

                print(f"  Converting: {rel}")
                markdown = html_file_to_markdown(html_path)

                # Add a source header so the model knows where this came from
                # write_bytes avoids Python re-introducing \r\n on Windows text mode
                header = f"<!-- Source: {rel} -->\n\n"
                out_path.write_bytes((header + markdown).encode("utf-8"))
                converted += 1

    print(f"  Done. Converted: {converted}")


# ---------------------------------------------------------------------------
# SAMPLE / TEMPLATE CONSOLIDATION
# ---------------------------------------------------------------------------

def read_project_files(project_dir: Path) -> list[tuple[str, str]]:
    """
    Walk a project directory and return a list of (relative_path, content) tuples
    for all text files worth including, in a logical order.
    Excludes: auto-generated docs/ subfolders, images, CSS, JS, .project files.
    """
    SKIP_DIRS = PROJECT_SKIP_DIRS
    # Preferred file order for readability
    ORDER = ["manifest.xml", "monkey.jungle", "strings.xml", "drawables.xml", "layout.xml", "menu.xml"]

    collected = []
    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirpath = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for filename in filenames:
            filepath = dirpath / filename
            rel = filepath.relative_to(project_dir)

            if filepath.suffix.lower() not in PROJECT_TEXT_EXTENSIONS:
                continue
            if filename.startswith("."):
                continue

            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                content = f"(Could not read file: {e})"

            # Handle <iq:products> block in manifest files.
            # General build: keep all products so the LLM sees the full device list.
            # Device-specific build: strip entirely — the LLM only needs to think about one device.
            if filename == "manifest.xml" and DEVICE_FILTER is not None:
                content = re.sub(
                    r"\s*<iq:products>.*?</iq:products>",
                    "\n        <iq:products><!-- device list stripped --></iq:products>",
                    content,
                    flags=re.DOTALL,
                )

            collected.append((str(rel).replace("\\", "/"), content))

    # Sort: put known important files first, then alphabetical
    def sort_key(item):
        name = Path(item[0]).name
        try:
            return (0, ORDER.index(name))
        except ValueError:
            ext = Path(item[0]).suffix
            # .mc files after config files
            return (1 if ext == ".xml" else 2 if ext == ".jungle" else 3, name)

    collected.sort(key=sort_key)
    return collected


def extract_sample_metadata(project_dir: Path) -> dict:
    """Extract app type, Toybox imports, and class names from a sample project."""
    app_type = "unknown"
    imports = set()
    classes = []
    functions = []

    # Parse manifest for app type
    manifest = project_dir / "manifest.xml"
    if manifest.exists():
        text = manifest.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'type="([^"]+)"', text)
        if m:
            app_type = m.group(1)

    # Scan all .mc files
    for mc_file in project_dir.rglob("*.mc"):
        try:
            text = mc_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in re.finditer(r"import Toybox\.(\w+)|using Toybox\.(\w+)", text):
            imports.add(m.group(1) or m.group(2))
        for m in re.finditer(r"^class\s+(\w+)", text, re.MULTILINE):
            classes.append(m.group(1))
        for m in re.finditer(r"^\s+(?:public\s+|private\s+|protected\s+)?function\s+(\w+)", text, re.MULTILINE):
            functions.append(m.group(1))

    # Deduplicate while preserving order
    classes = list(dict.fromkeys(classes))
    functions = list(dict.fromkeys(functions))

    return {
        "app_type": app_type,
        "imports": sorted(imports),
        "classes": classes,
        "functions": functions,
    }


def build_project_tree(project_dir: Path, display_name: str = None) -> str:
    """Build an ASCII directory tree for the project, noting binary files."""
    lines = []

    def _walk(directory: Path, prefix: str = ""):
        SKIP_DIRS = PROJECT_SKIP_DIRS
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return
        entries = [e for e in entries if not e.name.startswith(".")]
        # Filter out skip dirs
        entries = [e for e in entries if not (e.is_dir() and e.name in SKIP_DIRS)]
        for i, entry in enumerate(entries):
            connector = "└── " if i == len(entries) - 1 else "├── "
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                extension = "    " if i == len(entries) - 1 else "│   "
                _walk(entry, prefix + extension)
            else:
                is_binary = entry.suffix.lower() not in PROJECT_TEXT_EXTENSIONS
                note = "  [binary — not included]" if is_binary else ""
                lines.append(f"{prefix}{connector}{entry.name}{note}")

    root_label = display_name if display_name else project_dir.name
    lines.append(f"{root_label}/")
    _walk(project_dir)
    return "\n".join(lines)


def build_consolidated_md(name: str, project_dir: Path, meta: dict) -> str:
    """Build a single consolidated Markdown string for a sample or template."""
    lines = []
    lines.append(f"# {name}")
    lines.append("")
    app_type_label = meta['app_type'] if meta['app_type'] != 'unknown' else 'unknown (fill in manifest before use)'
    lines.append(f"**App type:** `{app_type_label}`  ")
    if meta["imports"]:
        lines.append(f"**Toybox APIs used:** {', '.join(f'`Toybox.{i}`' for i in meta['imports'])}  ")
    if meta["classes"]:
        lines.append(f"**Classes:** {', '.join(f'`{c}`' for c in meta['classes'])}  ")
    lines.append("")
    lines.append("## Project Structure")
    lines.append("")
    lines.append("```")
    lines.append(build_project_tree(project_dir, name))
    lines.append("```")
    lines.append("")
    lines.append("> Files marked `[binary — not included]` must be copied manually when recreating this project.")
    lines.append("")
    lines.append("---")
    lines.append("")

    files = read_project_files(project_dir)
    for rel_path, content in files:
        ext = Path(rel_path).suffix.lower()
        lang = {
            ".mc": "monkeyc",
            ".xml": "xml",
            ".jungle": "bash",
            ".json": "json",
        }.get(ext, "")

        lines.append(f"## `{rel_path}`")
        lines.append("")
        lines.append(f"```{lang}")
        lines.append(content.rstrip())
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def process_projects(source_dir: Path, output_dir: Path, label: str, use_variants: bool = False) -> list[dict]:
    """
    Consolidate each subdirectory of source_dir into a single .md file in output_dir.
    use_variants=True: go one level deeper (for templates, which have watch-app/simple/ structure).
    use_variants=False: treat each direct child as one complete project (for samples).
    Returns a list of metadata dicts for index generation.
    """
    print(f"\n=== Processing {label} ===")
    output_dir.mkdir(parents=True, exist_ok=True)
    index_entries = []

    subdirs = sorted([d for d in source_dir.iterdir() if d.is_dir()])

    for project_dir in subdirs:
        if use_variants:
            # Template-style: each subdir has named variants (e.g. watch-app/simple)
            variant_dirs = sorted([d for d in project_dir.iterdir() if d.is_dir()])
            if variant_dirs:
                for variant_dir in variant_dirs:
                    variant_name = f"{project_dir.name}-{variant_dir.name}"
                    _process_single_project(variant_dir, variant_name, output_dir, index_entries, label)
            else:
                _process_single_project(project_dir, project_dir.name, output_dir, index_entries, label)
        else:
            # Sample-style: each direct child IS the complete project
            _process_single_project(project_dir, project_dir.name, output_dir, index_entries, label)

    return index_entries


def _process_single_project(project_dir: Path, name: str, output_dir: Path, index_entries: list, label: str):
    print(f"  Processing: {name}")
    meta = extract_sample_metadata(project_dir)
    content = build_consolidated_md(name, project_dir, meta)
    out_file = output_dir / f"{name}.md"
    out_file.write_text(content, encoding="utf-8")
    index_entries.append({
        "name": name,
        "app_type": meta["app_type"],
        "imports": meta["imports"],
        "classes": meta["classes"],
        "functions": meta["functions"],
        "file": out_file.name,
    })


def write_index(index_entries: list[dict], output_dir: Path, title: str):
    """Write a SAMPLES_INDEX.md or TEMPLATES_INDEX.md summarising all entries."""
    lines = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append("Quick-reference index. Each entry links to a consolidated Markdown file containing")
    lines.append("the full project source: manifest, jungle file, resource XMLs, and all .mc files.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Group by app type
    by_type: dict[str, list] = {}
    for e in index_entries:
        by_type.setdefault(e["app_type"], []).append(e)

    for app_type, entries in sorted(by_type.items()):
        lines.append(f"## App type: `{app_type}`")
        lines.append("")
        for e in sorted(entries, key=lambda x: x["name"]):
            lines.append(f"### [{e['name']}]({e['file']})")
            if e["imports"]:
                lines.append(f"- **Toybox APIs:** {', '.join(f'`{i}`' for i in e['imports'])}")
            if e["classes"]:
                lines.append(f"- **Classes:** {', '.join(f'`{c}`' for c in e['classes'])}")
            if e["functions"]:
                # Show only the most interesting functions (skip initialize, getInitialView etc.)
                interesting = [f for f in e["functions"] if f not in {
                    "initialize", "getInitialView", "onStart", "onStop", "onUpdate", "onLayout"
                }]
                if interesting:
                    lines.append(f"- **Key functions:** {', '.join(f'`{f}()`' for f in interesting[:8])}")
            lines.append("")

    index_path = output_dir / (
        "SAMPLES_INDEX.md" if "Sample" in title else "TEMPLATES_INDEX.md"
    )
    index_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Index written: {index_path.name}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    global SDK_ROOT, OUTPUT_ROOT, DEVICE_FILTER

    check_dependencies()

    SDK_ROOT = pick_sdk_root()
    DEVICE_FILTER = pick_device(SDK_ROOT)
    OUTPUT_ROOT = SDK_ROOT / "Consolidated SDK"

    # Resolve path-dependent constants now that SDK_ROOT is known
    global HTML_SOURCE_DIRS, SAMPLES_DIR, TEMPLATES_DIR
    HTML_SOURCE_DIRS = [SDK_ROOT / "doc"]
    SAMPLES_DIR = SDK_ROOT / "samples"
    TEMPLATES_DIR = SDK_ROOT / "bin" / "templates"

    print(f"\nSDK root:    {SDK_ROOT}")
    print(f"Output root: {OUTPUT_ROOT}")
    if DEVICE_FILTER:
        print(f"Device:      {DEVICE_FILTER}")
    else:
        print("Device:      all (general build)")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    # 1. Convert HTML docs
    convert_html_docs()

    # 2. Consolidate samples (each sample dir IS the project — no variant subdirs)
    samples_out = OUTPUT_ROOT / "samples"
    sample_entries = process_projects(SAMPLES_DIR, samples_out, "samples", use_variants=False)
    write_index(sample_entries, samples_out, "Connect IQ Sample Projects Index")

    # 3. Consolidate templates (each template has named variant subdirs e.g. watch-app/simple)
    templates_out = OUTPUT_ROOT / "templates"
    template_entries = process_projects(TEMPLATES_DIR, templates_out, "templates", use_variants=True)
    write_index(template_entries, templates_out, "Connect IQ Templates Index")

    # 4. Write master navigation index (LLM-oriented)
    write_master_index()

    # 5. Write image catalog
    write_image_catalog()

    print("\n=== All done! ===")
    print(f"Output written to: {OUTPUT_ROOT}")


def write_master_index():
    """Write INDEX.md — LLM-oriented navigation for the consolidated SDK reference."""
    content = """# Garmin Connect IQ SDK — Consolidated LLM Reference (INDEX)

SDK version 8.4.1 (Feb 2026). Generated from the official SDK HTML documentation and sample projects.
All files are Markdown, optimised for LLM consumption.

See `README.md` for a human-oriented project overview.

## Files in this directory

- `INDEX.md` — this file; full structure map and key-file quick reference (LLM-oriented)
- `README.md` — human-oriented project description and usage guide
- `SDK_IMAGE_CATALOG.md` — catalog of all SDK documentation images with paths and descriptions
- `docs/` — converted HTML documentation
  - `docs/index.md` — flat list of all Toybox modules
  - `docs/class_list.md` — hierarchical Toybox class browser: all namespaces → classes
  - `docs/method_list.md` — alphabetical method/property list across all Toybox classes with class context
  - `docs/docs/` — programmer's guide topics (see structure below)
  - `docs/Toybox/` — API reference (one file per class)
- `samples/` — consolidated sample projects
- `templates/` — consolidated template projects

---

## How to use this reference

When writing Connect IQ / Monkey C code:
1. Start with `docs/docs/` for conceptual guidance (language syntax, app structure, APIs by topic)
2. Use `docs/Toybox/` for precise class and method signatures
3. Use `docs/class_list.md` to browse all available classes, or `docs/method_list.md` to look up a method by name
4. Use `samples/` to see complete working app examples
5. Use `templates/` for minimal boilerplate to start a new app

---

## Directory Structure

```
Consolidated SDK/
├── INDEX.md                     ← This file (LLM navigation)
├── README.md                    ← Human-oriented overview
├── SDK_IMAGE_CATALOG.md         ← Image catalog
├── docs/
│   ├── index.md                 ← All Toybox module names (flat list)
│   ├── class_list.md            ← All Toybox classes, grouped by namespace
│   ├── method_list.md           ← All methods/properties, alphabetical, with class context
│   ├── docs/
│   │   ├── Readme/              ← SDK release notes and intro
│   │   │   ├── Intro.md
│   │   │   ├── Getting_Started.md
│   │   │   └── History.md       ← SDK version history / changelog
│   │   ├── Connect_IQ_Basics/   ← App types, project setup, first app
│   │   │   ├── App_Types.md
│   │   │   ├── Getting_Started.md
│   │   │   ├── Welcome_to_the_Jungle.md
│   │   │   └── Your_First_App.md
│   │   ├── Monkey_C/            ← Language syntax, types, functions, annotations
│   │   │   ├── Basic_Syntax.md
│   │   │   ├── Annotations.md
│   │   │   ├── Coding_Conventions.md
│   │   │   ├── Compiler_Options.md
│   │   │   ├── Containers.md
│   │   │   ├── Exceptions_and_Errors.md
│   │   │   ├── Functions.md
│   │   │   ├── Monkey_Types.md
│   │   │   └── Objects_and_Memory.md
│   │   ├── Core_Topics/         ← BLE, Graphics, Sensors, Layouts, Permissions, etc.
│   │   │   ├── Activity_Control.md
│   │   │   ├── Activity_Prompts.md
│   │   │   ├── Activity_Recording.md
│   │   │   ├── Ant_and_Ant_Plus.md
│   │   │   ├── Application_and_System_Modules.md
│   │   │   ├── Authenticated_Web_Services.md
│   │   │   ├── Backgrounding.md
│   │   │   ├── Beta_Apps.md
│   │   │   ├── Bluetooth_Low_Energy.md
│   │   │   ├── Build_Configuration.md
│   │   │   ├── Communicating_with_Mobile_Apps.md
│   │   │   ├── Complications.md
│   │   │   ├── Debugging.md
│   │   │   ├── Downloading_Content.md
│   │   │   ├── Editing_Watch_Faces_On_Device.md
│   │   │   ├── Exception_Reporting_Tool.md
│   │   │   ├── Getting_the_Users_Attention.md
│   │   │   ├── Glances.md
│   │   │   ├── Graphics.md
│   │   │   ├── HTTPS.md
│   │   │   ├── Input_Handling.md
│   │   │   ├── Intents.md
│   │   │   ├── Layouts.md
│   │   │   ├── Manifest_and_Permissions.md
│   │   │   ├── Mobile_SDK_for_Android.md
│   │   │   ├── Mobile_SDK_for_iOS.md
│   │   │   ├── Monkey_Style.md
│   │   │   ├── Native_Controls.md
│   │   │   ├── Notifications.md
│   │   │   ├── Overview.md
│   │   │   ├── Pairing_Wireless_Devices.md
│   │   │   ├── Persisting_Data.md
│   │   │   ├── Positioning.md
│   │   │   ├── Profiling.md
│   │   │   ├── Properties_and_App_Settings.md
│   │   │   ├── Publishing_to_the_Store.md
│   │   │   ├── Quantifying_the_User.md
│   │   │   ├── Requesting_Reviews.md
│   │   │   ├── Resources.md
│   │   │   ├── Security.md
│   │   │   ├── Sensors.md
│   │   │   ├── Shareable_Libraries.md
│   │   │   ├── Trial_Apps.md
│   │   │   ├── Unit_Testing.md
│   │   │   └── User_Interface.md
│   │   ├── Device_Reference/    ← Per-device specs (screen size, memory, fonts, field layouts)
│   │   │   ├── Overview.md      ← How to read device reference pages
│   │   │   └── {DEVICE_TREE_LINE}
│   │   ├── Reference_Guides/
│   │   │   ├── Jungle_Reference.md          ← monkey.jungle file format
│   │   │   ├── Monkey_C_Reference.md        ← Full language specification
│   │   │   ├── Monkey_C_Command_Line_Setup.md
│   │   │   ├── Monkey_Graph_Reference.md
│   │   │   ├── Monkey_Motion_Reference.md
│   │   │   ├── Devices_Reference.md         ← Cross-device compatibility table
│   │   │   └── Visual_Studio_Code_Extension.md
│   │   ├── Personality_Library/ ← Garmin UX components (colours, toasts, page loops, etc.)
│   │   │   ├── Personality_UI.md
│   │   │   ├── Action_Views.md
│   │   │   ├── Colors.md
│   │   │   ├── Confirmations.md
│   │   │   ├── Iconography.md
│   │   │   ├── Input_Hints.md
│   │   │   ├── Page_Loops.md
│   │   │   ├── Progress_Bars.md
│   │   │   ├── Prompts.md
│   │   │   ├── Toasts.md
│   │   │   └── Typography.md
│   │   ├── User_Experience_Guidelines/
│   │   │   ├── Overview.md
│   │   │   ├── Design_Principles.md
│   │   │   ├── Designing_Workflows_and_Interactions.md
│   │   │   ├── Developing_the_Concepts.md
│   │   │   ├── Entry_Points.md
│   │   │   ├── Incorporating_the_Visual_Design_and_Product_Personalities.md
│   │   │   ├── Localization.md
│   │   │   ├── Map_Views.md
│   │   │   ├── Menus.md
│   │   │   ├── Progress_Bars.md
│   │   │   ├── Understanding_What_You_Are_Building.md
│   │   │   ├── Views.md
│   │   │   ├── Watch_Faces.md
│   │   │   ├── Confirmations.md
│   │   │   └── Data_Fields.md
│   │   ├── App_Review_Guidelines/
│   │   │   └── Overview.md
│   │   ├── Monetization/
│   │   │   ├── Overview.md
│   │   │   ├── Account_Management.md
│   │   │   ├── App_Sales.md
│   │   │   ├── Merchant_Onboarding.md
│   │   │   └── Price_Points.md
│   │   └── Connect_IQ_FAQ/
│   │       ├── Overview.md
│   │       ├── How_Do_I_Create_an_Audio_Content_Provider.md
│   │       ├── How_Do_I_Create_a_Connect_IQ_Background_Service.md
│   │       ├── How_Do_I_Get_My_Watch_Face_to_Update_Every_Second.md
│   │       ├── How_Do_I_Make_a_Watch_Face_for_AMOLED_Products.md
│   │       ├── How_Do_I_Optimize_Bitmaps.md
│   │       ├── How_Do_I_Override_the_Goal_Animations.md
│   │       ├── How_Do_I_Use_a_Mapview.md
│   │       ├── How_Do_I_Use_Custom_Fonts.md
│   │       ├── How_Do_I_Use_REST_Services.md
│   │       └── How_Do_I_Use_the_Connect_IQ_Mobile_SDK.md
│   └── Toybox/                  ← API reference (one .md per namespace; subdirs per class)
│       ├── Activity.md          Activity.Info, ProfileInfo, WorkoutStep, etc.
│       ├── ActivityMonitor.md   ActiveMinutes, HeartRateIterator, History, Info, etc.
│       ├── ActivityPrompts.md
│       ├── ActivityRecording.md Session
│       ├── Ant.md               GenericChannel, Message, CryptoConfig, etc.
│       ├── AntPlus.md           BikeCadence, BikePower, FitnessEquipment, etc.
│       ├── Application.md       AppBase, Properties, Storage, WatchFaceConfig
│       ├── Attention.md         ToneProfile, VibeProfile
│       ├── Authentication.md
│       ├── Background.md
│       ├── BluetoothLowEnergy.md BleDelegate, Device, Characteristic, ScanResult, etc.
│       ├── Communications.md    ConnectionListener, SyncDelegate, etc.
│       ├── Complications.md
│       ├── Cryptography.md      Cipher, Hash, KeyPair, etc.
│       ├── FitContributor.md    Field
│       ├── Graphics.md          Dc, BufferedBitmap, AffineTransform, BoundingBox, etc.
│       ├── Lang.md              Array, Dictionary, String, Number, Exception, etc.
│       ├── Math.md              Filter, FirFilter, IirFilter + math functions
│       ├── Media.md             (audio content provider)
│       ├── Notifications.md
│       ├── PersistedContent.md  Course, Route, Track, Waypoint, Workout
│       ├── PersistedLocations.md
│       ├── Position.md          Info, Location
│       ├── Sensor.md            Info, SensorData, AccelerometerData, etc.
│       ├── SensorHistory.md     SensorHistoryIterator, SensorSample
│       ├── SensorLogging.md
│       ├── StringUtil.md
│       ├── System.md            Stats, DeviceSettings, ClockTime, Intent, etc.
│       ├── Test.md              AssertException, Logger
│       ├── Time.md              Duration, Moment, LocalMoment, Gregorian
│       ├── Timer.md             Timer
│       ├── UserProfile.md       Profile, UserActivity
│       ├── WatchUi.md           View, DataField, SimpleDataField, Menu2, Picker, etc.
│       └── Weather.md           CurrentConditions, DailyForecast, HourlyForecast
├── samples/
│   ├── SAMPLES_INDEX.md         ← Start here: grouped by app type with Toybox API tags
│   ├── AccelMag.md              ← Accelerometer + magnetometer sensor
│   ├── ActivityTracking.md      ← ActivityMonitor widget
│   ├── Analog.md                ← Full analog watch face
│   ├── AnimationWatchFace.md    ← Watch face with animations
│   ├── ApplicationStorage.md    ← Storage + Properties API
│   ├── Attention.md             ← Vibration and tone API
│   ├── BackgroundTimer.md       ← Background service
│   ├── BulkDownload.md          ← Communications bulk download
│   ├── Comm.md                  ← Phone communication
│   ├── ConfigurableWatchFace.md ← Watch face with Complications
│   ├── ConfirmationDialog.md
│   ├── Drawable.md              ← Custom drawables
│   ├── Encryption.md            ← ANT+ channel encryption
│   ├── ExtendedCodeSpace.md
│   ├── FieldTimerEvents.md      ← Data field timer events
│   ├── GenericChannelBurst.md   ← ANT burst transfers
│   ├── Input.md                 ← Input handling
│   ├── JsonDataResources.md     ← JSON resource files
│   ├── Keyboard.md              ← Text picker / keyboard
│   ├── MapSample.md             ← MapView
│   ├── Menu2Sample.md           ← Menu2 with custom items
│   ├── MenuTest.md
│   ├── MO2Display.md            ← ANT+ muscle oxygen
│   ├── MoxyField.md             ← ANT+ Moxy sensor data field
│   ├── NordicThingy52.md        ← BLE peripheral integration
│   ├── NordicThingy52CoinCollector.md ← BLE data field
│   ├── Notifications.md         ← Phone notifications
│   ├── Picker.md                ← Picker UI
│   ├── PitchCounter.md          ← SensorLogging + accelerometer
│   ├── PositionSample.md        ← GPS/Position API
│   ├── Primates.md              ← Multi-page widget / ViewLoop
│   ├── ProgressBar.md           ← ProgressBar UI
│   ├── RecordSample.md          ← Activity recording
│   ├── Selectable.md            ← Selectable / checkbox UI
│   ├── Sensor.md                ← Sensor API
│   ├── SensorHistory.md         ← SensorHistory API
│   ├── SimpleDataField.md       ← Data field with settings
│   ├── Strings.md               ← String resources
│   ├── Timer.md                 ← Timer API
│   ├── Toasts.md                ← Toast notifications
│   ├── TrueTypeFonts.md         ← Custom / vector fonts
│   ├── UserProfile.md           ← UserProfile API
│   └── WebRequest.md            ← HTTP web requests
└── templates/
    ├── TEMPLATES_INDEX.md                   ← Start here
    ├── watch-app-simple.md                  ← Minimal watch app boilerplate
    ├── datafield-simple.md                  ← Simple data field
    ├── datafield-complex.md                 ← Data field with settings
    ├── watchface-simple.md                  ← Simple watch face
    ├── watchface-settings.md                ← Watch face with settings
    ├── widget-simple.md                     ← Simple widget
    ├── audio-content-provider-app-simple.md ← Audio content provider
    └── barrel-simple.md                     ← Shareable library (barrel)
```

---

## Key files for common tasks

| Task | File |
|------|------|
| Browse all classes | `docs/class_list.md` |
| Find a method by name | `docs/method_list.md` |
| All Toybox module list | `docs/index.md` |
| SDK version history | `docs/docs/Readme/History.md` |
| Understand app types & lifecycle | `docs/docs/Connect_IQ_Basics/App_Types.md` |
| Monkey C language syntax | `docs/docs/Monkey_C/Basic_Syntax.md` |
| Full language specification | `docs/docs/Reference_Guides/Monkey_C_Reference.md` |
| monkey.jungle format | `docs/docs/Reference_Guides/Jungle_Reference.md` |
| Cross-device compatibility | `docs/docs/Reference_Guides/Devices_Reference.md` |
| VS Code / command-line setup | `docs/docs/Reference_Guides/Visual_Studio_Code_Extension.md` |
| Drawing to screen | `docs/docs/Core_Topics/Graphics.md` + `docs/Toybox/Graphics/Dc.md` |
| Layouts and UI | `docs/docs/Core_Topics/Layouts.md` |
| Input handling | `docs/docs/Core_Topics/Input_Handling.md` |
| Sensor data | `docs/docs/Core_Topics/Sensors.md` + `docs/Toybox/Sensor/` |
| Activity recording | `docs/docs/Core_Topics/Activity_Recording.md` |
| Persisting data | `docs/docs/Core_Topics/Persisting_Data.md` |
| App settings & properties | `docs/docs/Core_Topics/Properties_and_App_Settings.md` |
| Bluetooth Low Energy | `docs/docs/Core_Topics/Bluetooth_Low_Energy.md` |
| Manifest & permissions | `docs/docs/Core_Topics/Manifest_and_Permissions.md` |
| Backgrounding | `docs/docs/Core_Topics/Backgrounding.md` |
| Glances | `docs/docs/Core_Topics/Glances.md` |
| Complications | `docs/docs/Core_Topics/Complications.md` |
| Garmin UX components | `docs/docs/Personality_Library/Personality_UI.md` |
| UX design guidelines | `docs/docs/User_Experience_Guidelines/Overview.md` |
| App review guidelines | `docs/docs/App_Review_Guidelines/Overview.md` |
| Device specs | `docs/docs/Device_Reference/{DEVICE_SPEC_FILE}` |
| WatchUi class list | `docs/Toybox/WatchUi.md` |
| View lifecycle methods | `docs/Toybox/WatchUi/View.md` |
| Data field base class | `docs/Toybox/WatchUi/DataField.md` |
| SimpleDataField base class | `docs/Toybox/WatchUi/SimpleDataField.md` |
| New watch app boilerplate | `templates/watch-app-simple.md` |
| New data field boilerplate | `templates/datafield-simple.md` |
| New watch face boilerplate | `templates/watchface-simple.md` |
| New widget boilerplate | `templates/widget-simple.md` |
| Barrel (library) boilerplate | `templates/barrel-simple.md` |
| Activity tracking example | `samples/ActivityTracking.md` |
| Sensor reading example | `samples/Sensor.md` |
| BLE peripheral example | `samples/NordicThingy52.md` |
| Web request example | `samples/WebRequest.md` |
| Background service example | `samples/BackgroundTimer.md` |

---

*Generated from Garmin Connect IQ SDK 8.4.1 — Feb 2026*
"""
    # Resolve device-specific placeholders
    if DEVICE_FILTER:
        device_tree_line = f"{DEVICE_FILTER}.md"
        device_spec_file = f"{DEVICE_FILTER}.md"
    else:
        device_tree_line = "... (all 160+ devices included)"
        device_spec_file = "<device>.md  (one file per device in Device_Reference/)"
    content = content.replace("{DEVICE_TREE_LINE}", device_tree_line)
    content = content.replace("{DEVICE_SPEC_FILE}", device_spec_file)

    index_path = OUTPUT_ROOT / "INDEX.md"
    index_path.write_bytes(content.encode("utf-8"))
    print(f"  Master INDEX written: {index_path}")


def write_image_catalog():
    """
    Write SDK_IMAGE_CATALOG.md listing all reference images in the SDK docs.
    Images cannot be read by a text LLM, but knowing their paths and purpose
    allows directing users to the right visual reference.
    """
    IMAGE_ROOT = SDK_ROOT / "doc" / "resources"
    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".gif"}

    # Files to skip: decorative monkey illustrations, large animations, bundled JS
    SKIP_FILES = {
        "chopper-monkey.png", "learning-monkey.png", "captain-monkey.png",
        "artsy-monkey.png", "smart-monkey.png", "sculptor-monkey.png",
        "wizard-monkey.png", "cyclist-monkey.png", "archy-monkey.png",
        "swirl.gif", "clock.gif", "giphy.gif",
        "jquery-1.11.3.min.js",
    }

    # Manual descriptions for important files where the filename isn't self-explanatory
    DESCRIPTIONS = {
        # programmers-guide
        "app-lifecycle.png":         "App lifecycle flow diagram (onStart/onStop/getInitialView)",
        "layout.png":                "Layout system diagram showing how drawables are positioned",
        "layers.png":                "WatchUi Layer system diagram",
        "amoled_layout.png":         "AMOLED display layout constraints and safe areas",
        "oauth_flow.png":            "OAuth authentication flow diagram",
        "oauth_notification.png":    "OAuth notification UI example",
        "oauth_complete.png":        "OAuth completion UI example",
        "picker-layout.png":         "Picker UI layout and structure",
        "Menu2_labels.png":          "Menu2 label anatomy diagram",
        "ToggleFigure.png":          "Toggle menu item appearance",
        "CheckboxFigure.png":        "Checkbox menu item appearance",
        "IconFigure.png":            "Icon menu item appearance",
        "complication_publishers_and_subscribers.png": "Complications pub/sub architecture",
        "MappingDiagram.png":        "Map tile coverage and mapping architecture",
        "MapCoverage.png":           "Map coverage area diagram",
        "weak-reference-1.png":      "Weak reference memory diagram (part 1)",
        "weak-reference-2.png":      "Weak reference memory diagram (part 2)",
        "weak-reference-3.png":      "Weak reference memory diagram (part 3)",
        "profiler.png":              "Profiler tool screenshot",
        "profiler-empty.png":        "Profiler tool (empty state) screenshot",
        "profiler-command-line.png": "Profiler command-line usage",
        "vscode-debugging.png":      "VS Code debugger screenshot",
        "vscode-breakpoint.png":     "VS Code breakpoint example",
        "vscode-jungles.png":        "VS Code jungle file configuration",
        "new_project_structure.png": "New project folder structure diagram",
        "first_app.png":             "First app walkthrough screenshot",
        "glance_page.png":           "Glance page layout example",
        "qualifier-project.png":     "Device qualifier project structure",
        "burn-in-sim.png":           "Simulator burn-in mode screenshot",
        "monkey_motion.png":         "Monkey Motion animation tool screenshot",
        "monkey_motion_scrubber.png":"Monkey Motion timeline scrubber",
        "monkey_motion_advanced.png":"Monkey Motion advanced options",
        "intent-launched.png":       "Intent launch UI example",
        "resources-strings.png":     "Resources strings editor screenshot",
        "app_settings_editor.png":   "App settings editor screenshot",
        "fitgraph-file.png":         "FIT file graph tool",
        "fitgraph-graph.png":        "FIT file graph output (example 1)",
        "fitgraph-graph2.png":       "FIT file graph output (example 2)",
        "bmfont_options.png":        "Bitmap font tool options",
        "title_description.png":     "App store title/description fields",
        "upload_app.png":            "App store upload process",
        "beta_app.png":              "Beta app marker",
        "era_report_view.png":       "Exception Reporting App report view",
        "era_manage_apps.png":       "Exception Reporting App management",
        "era_app_info.png":          "Exception Reporting App info panel",
        "nRFConnectLaunch.png":      "nRF Connect app launch (BLE diagnostics)",
        "nRFJLinkDevMgr.png":        "nRF JLink device manager (BLE)",
        "nRFSetComPortSim.png":      "nRF COM port simulator config (BLE)",
        "nRFConnectPrepMemoryLayout.png":  "nRF Connect memory layout prep (BLE)",
        "nRFConnectWriteMemoryLayout.png": "nRF Connect memory layout write (BLE)",
        "nRFConnectProgrammerLaunch.png":  "nRF Connect programmer launch (BLE)",
        "sdk-manager-start.png":     "SDK Manager startup screen",
        "sdk-manager-login.png":     "SDK Manager login screen",
        "sdk-manager-sdk-tab.png":   "SDK Manager SDK tab",
        "sdk-manager-devices-tab.png": "SDK Manager devices tab",
        "sdk-manager-update-sdk.png":  "SDK Manager update SDK flow",
        "sdk-manager-update-devices.png": "SDK Manager update devices flow",
        "sdk-manager-download-button.png": "SDK Manager download button",
        "sdk-manager-x-button.png":  "SDK Manager remove button",
        "ios-image1.png":            "iOS companion app screenshot 1",
        "ios-image2.png":            "iOS companion app screenshot 2",
        "ios-image3.png":            "iOS companion app screenshot 3",
        "ios-image4.png":            "iOS companion app screenshot 4",
        "ios-image5.png":            "iOS companion app screenshot 5",
        "ios-image6.png":            "iOS companion app screenshot 6",
        "ios-image8.png":            "iOS companion app screenshot 7",
        "ios-image9.png":            "iOS companion app screenshot 8",
        # personality-library
        "personality_ui_light_dark_modeshigh.jpg":      "Light/dark mode UI comparison screenshot",
        "personality_ui_confirmationhigh.jpg":          "Confirmation dialog screenshot",
        "personality_ui_prompts_with_titlehigh.jpg":    "Prompt with title screenshot",
        "personality_ui_prompts_no_titlehigh.jpg":      "Prompt without title screenshot",
        "personality_ui_prompts_with_iconhigh.jpg":     "Prompt with icon screenshot",
        "personality_ui_action_hinthigh.jpg":           "Action hint UI screenshot",
        "personality_ui_delete_confirmationhigh.jpg":   "Delete confirmation dialog screenshot",
        "personality_ui_button_hintshigh.jpg":          "Button hints UI screenshot",
        "personality_ui_questionhigh.svg":              "Question icon SVG",
        "personality_ui_warninghigh.svg":               "Warning icon SVG",
        "personality_ui_abouthigh.svg":                 "About/info icon SVG",
        "personality_ui_undohigh.svg":                  "Undo icon SVG",
        "personality_ui_searchhigh.svg":                "Search icon SVG",
        "vivomove_trend_deletehigh.svg":                "Delete icon SVG (vivomove style)",
        "vivomove_trend_cancel_xhigh.svg":              "Cancel/X icon SVG (vivomove style)",
        "vivomove_trend_savehigh.svg":                  "Save icon SVG (vivomove style)",
        # ux-guide
        "page-loops.png":            "Page loop navigation pattern diagram",
        "widgets.png":               "Widget page layout examples",
        "watch-face.png":            "Watch face layout examples",
        "data-fields.png":           "Data field layout examples",
        "device-apps.png":           "Device app layout examples",
        "audio-content-providers.png": "Audio content provider layout examples",
        "glances.png":               "Glance layout examples",
        "one-field-layout.png":      "Single data field layout",
        "two-field-layout.png":      "Two data field layout",
        "three-field-layout.png":    "Three data field layout",
        "confirmations.png":         "Confirmation dialog layout examples",
        "dialogs.png":               "Dialog UI examples",
        "selection-menu.png":        "Selection menu UI example",
        "settings-menu.png":         "Settings menu UI example",
        "button-hint.png":           "Button hint UI",
        "touch-hint.png":            "Touch hint UI",
        "progress-bars.png":         "Progress bar examples",
        "infinite-progress.png":     "Infinite progress indicator",
        "partial-update.png":        "Partial screen update concept",
        "low-power-modes.png":       "Low power mode display behaviour",
        "five-button.png":           "Five-button device input layout",
        "touchscreen-two-button.png": "Touchscreen + two-button device layout",
        "edge-touchscreen-one-button.png": "Edge touchscreen + one-button layout",
        "dark-on-light.png":         "Dark-on-light contrast example",
        "obscurity-example.png":     "Obscurity/legibility example",
        "mobile-authentication.png": "Mobile authentication flow",
        "mobile-app-settings.png":   "Mobile app settings UI",
        # faq
        "16_color_palette.png":      "Garmin 16-colour palette reference",
        "cake_dithered.png":         "Dithered image example",
        "cake_undithered.jpg":       "Undithered image example (for comparison)",
        "two_color_face.png":        "Two-colour watch face example",
        "mask_glyphs.jpg":           "Font glyph masking example",
        "normal_and_reflected.jpg":  "Normal and reflected text rendering",
        "overlapping_rotated_glyphs.jpg": "Overlapping rotated glyph rendering",
        "rotated_glyphs.jpg":        "Rotated glyph rendering",
        "reflections.png":           "Text reflection effect",
        "reflecto_font.jpg":         "Reflecto font example",
        "no_frills.jpg":             "Simple text rendering example",
        "Select_Device_Flow.png":    "Device selection flow diagram",
        "map_view_1.png":            "MapView example 1",
        "map_view_2.png":            "MapView example 2",
        "downloading_music_content.png": "Music content download UI",
        "music_storage.png":         "Music storage UI",
        "playback_configuration.png": "Playback configuration UI",
        "playback_tree.png":         "Playback tree structure diagram",
        "sync_config.png":           "Sync configuration UI",
        "sync_flow.png":             "Sync flow diagram",
        "south_africa_1.png":        "Watch face graphics example (south_africa_1)",
        "south_africa_2.jpg":        "Watch face graphics example (south_africa_2)",
        "summer_sunet_1.png":        "Watch face graphics example (summer_sunset)",
        "Included_versus_custom_install_dialogs.png": "Install dialog comparison",
        "wouldnt_this_be_nice.jpg":  "Watch face composition example",
        "doge.png":                  "Doge image (used in graphics examples)",
    }

    SECTION_NOTES = {
        "programmers-guide": "Diagrams and screenshots referenced in the programmer's guide documentation.",
        "personality-library": "Screenshots of Garmin's Personality UI components as they appear on device. "
                               "Useful reference for understanding what built-in dialogs, confirmations, "
                               "and UI elements look like.",
        "ux-guide": "UX pattern diagrams from the User Experience Guidelines. Shows navigation patterns, "
                    "layout structures, button layouts, and design best practices.",
        "faq":      "Images referenced in the Connect IQ FAQ. Primarily graphics rendering and font examples.",
        "device-reference": "Per-device SVG layout diagrams showing the screen mask for each data field layout. "
                            "SVG files are text-readable. The exact pixel coordinates are also available "
                            "as tables in the device reference .md files.",
    }

    lines = []
    lines.append("# SDK Image Catalog")
    lines.append("")
    lines.append("Images from the SDK documentation. Images cannot be read directly by a text-only LLM,")
    lines.append("but this catalog records what exists and where, so you can direct users to the right")
    lines.append("visual reference or examine the file yourself.")
    lines.append("")
    lines.append(f"**SDK root:** `{SDK_ROOT}`")
    lines.append(f"**Image root:** `{IMAGE_ROOT}`")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Process non-device-reference folders first (they have flat file lists)
    flat_folders = ["programmers-guide", "personality-library", "ux-guide", "faq"]
    for folder_name in flat_folders:
        folder_path = IMAGE_ROOT / folder_name
        if not folder_path.exists():
            continue

        files = sorted([
            f for f in folder_path.iterdir()
            if f.is_file()
            and f.suffix.lower() in IMAGE_EXTENSIONS
            and f.name not in SKIP_FILES
        ], key=lambda p: p.name.lower())

        if not files:
            continue

        lines.append(f"## {folder_name}/")
        lines.append("")
        note = SECTION_NOTES.get(folder_name, "")
        if note:
            lines.append(f"*{note}*")
            lines.append("")

        for f in files:
            rel = f.relative_to(SDK_ROOT)
            desc = DESCRIPTIONS.get(f.name, "")
            ext_note = " `[SVG — text-readable]`" if f.suffix.lower() == ".svg" else ""
            if desc:
                lines.append(f"- **`{f.name}`**{ext_note} — {desc}")
            else:
                # Auto-generate a plain name from filename as fallback
                plain = f.stem.replace("_", " ").replace("-", " ").title()
                lines.append(f"- **`{f.name}`**{ext_note} — {plain}")
            lines.append(f"  Path: `{rel}`")

        lines.append("")

    # Device-reference: enumerate devices and note SVG layout files as a group
    device_ref_path = IMAGE_ROOT / "device-reference"
    if device_ref_path.exists():
        lines.append("## device-reference/")
        lines.append("")
        note = SECTION_NOTES.get("device-reference", "")
        if note:
            lines.append(f"*{note}*")
            lines.append("")
        lines.append("Each device folder contains SVG layout diagrams (`layout0.svg`, `layout1.svg`, etc.).")
        lines.append("The number of layouts varies by device — circular watches typically have 4, rectangular")
        lines.append("or multi-field devices may have up to 19. Each SVG is the circular/rectangular screen mask")
        lines.append("for that field layout.")
        lines.append("")
        lines.append("> **Note:** The exact pixel coordinates for each layout (left, top, width, height, obscurity)")
        lines.append("> are already available as text tables in `docs/docs/Device_Reference/{device}.md`.")
        lines.append("> The SVG files provide the visual representation only.")
        lines.append("")
        lines.append("Devices with layout SVGs:")
        lines.append("")
        all_device_dirs = sorted([d for d in device_ref_path.iterdir() if d.is_dir()])
        device_dirs = (
            [d for d in all_device_dirs if d.name.lower() == DEVICE_FILTER.lower()]
            if DEVICE_FILTER else all_device_dirs
        )
        for d in device_dirs:
            svgs = sorted(
                [f.name for f in d.iterdir() if f.suffix.lower() == ".svg"],
                key=lambda name: int(re.search(r'(\d+)', name).group(1)) if re.search(r'(\d+)', name) else 0
            )
            if svgs:
                count = len(svgs)
                layouts = ", ".join(f"`{s}`" for s in svgs)
                lines.append(f"- **`{d.name}`** ({count} layout{'s' if count != 1 else ''}) — {layouts}")
                lines.append(f"  Path: `{d.relative_to(SDK_ROOT)}`")
        lines.append("")

    out_path = OUTPUT_ROOT / "SDK_IMAGE_CATALOG.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Image catalog written: {out_path}")


if __name__ == "__main__":
    main()

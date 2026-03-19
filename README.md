# Garmin Connect IQ SDK — LLM Reference Builder

A Python script that converts the Garmin Connect IQ SDK HTML documentation into clean, LLM-friendly Markdown. Run it once against your local SDK install and get a structured reference you can feed selectively to a local language model without it drowning in boilerplate, navigation chrome, or device lists for 160 devices you're not targeting.

---

## Why This Exists

The Connect IQ SDK ships its API reference as hundreds of individual HTML files with navigation headers, footers, and "Supported Devices" lists that list every Garmin product ever made. When working with local LLMs (or with Claude via a context window), loading even a handful of these raw HTML files wastes a significant portion of the available context on irrelevant content.

This script strips all of that and produces clean Markdown files — one per class — plus consolidated indexes, sample projects, and templates, all ready to load into a model's context exactly when they're needed.

It also supports a **device-specific build mode**: when you're developing for a specific device, it can tailor the entire output to that device — silently removing supported-device lists where your device is covered, and injecting a visible warning (`⚠️ Not supported on venu3.`) wherever an API member or method is not available on your target device.

---

## About This Project

This script was built through **vibecoding** — a development approach where the human provides direction, intent, and testing, and an AI (Claude by Anthropic) writes the code. Every line of Python was written by Claude. My role was to describe what I wanted, test each iteration, report what worked and what didn't, and keep iterating.

---

## Requirements

- Python 3.10 or later
- The Garmin Connect IQ SDK installed locally (tested against **SDK 8.4.1**, released February 2026) — download free from the [Garmin developer portal](https://developer.garmin.com/connect-iq/sdk/)
- Two third-party packages:

```
pip install beautifulsoup4 markdownify
```

- `tkinter` for the graphical SDK root and device picker dialogs (included with most Python installs; falls back to a text prompt if unavailable)

---

## Usage

```
python build_sdk_reference.py
```

On launch, five configuration dialogs appear in order:

1. **SDK root picker** — browse to your Connect IQ SDK folder (the one containing `doc/`, `samples/`, etc.)
2. **Device picker** — multi-select checkboxes; choose one or more target devices, or leave all unselected for a general build. Selecting devices enables compatibility checking and filters the Device Reference directory.
3. **Compatibility mode** — *(only appears if devices are selected)* choose how samples that don't list your device are handled: annotate all (recommended), strict/any (exclude if not listed for at least one selected device), or strict/all (exclude if not listed for every selected device). When multiple devices are selected, per-device compatibility is annotated individually.
4. **Split threshold** — the maximum file size in KB before a doc file is split into numbered parts. Default is 18 KB (~4,500 tokens). See [Choosing a threshold](#choosing-a-threshold) below.
5. **Sample output mode** — choose how sample and template source code is packaged. See [Sample output modes](#sample-output-modes) below.

The script runs in under 30 seconds and writes everything to a `Consolidated SDK/` folder directly inside your SDK root.

---

## Output Structure

```
Consolidated SDK/
├── INDEX.md                     ← LLM navigation guide; start here
├── SPLIT_FILES.md               ← Catalog of all multi-part doc files
├── SDK_IMAGE_CATALOG.md         ← Catalog of all SDK documentation images
├── docs/
│   ├── index.md                 ← Flat list of all Toybox modules
│   ├── class_list.md            ← All classes, grouped by namespace
│   ├── method_list.md           ← All methods/properties, alphabetical
│   ├── docs/                    ← Programmer's guide topics
│   │   ├── Connect_IQ_Basics/
│   │   ├── Monkey_C/
│   │   ├── Core_Topics/
│   │   ├── Device_Reference/    ← Per-device specs (all devices, or just yours)
│   │   ├── Reference_Guides/
│   │   ├── Personality_Library/
│   │   └── ...
│   └── Toybox/                  ← API reference — one .md per class
│       ├── System/DeviceSettings.md
│       ├── WatchUi/DataField.md
│       └── ...
├── samples/                     ← Sample projects (structure depends on output mode)
└── templates/                   ← Boilerplate templates for each app type
```

---

## Sample Output Modes

The script offers two ways to package sample and template source code. The right choice depends on how much context your model can hold at once.

### Why this matters: context window constraints

This tooling was originally designed with large-context models in mind (Claude, GPT-4, etc.), where the bottleneck is relevance rather than capacity. As the project evolved to also support **small local models** — in particular 7B-parameter models with 32k token context windows, run via Ollama — a different problem emerged.

A 32k context window sounds large, but in practice:
- The model's system prompt + Cline's tool definitions consume ~6–8k tokens before any file is loaded
- Every file read stays in context for the entire session
- A single consolidated sample file can easily reach 20–30k tokens

This leaves almost no room for actual code generation. The output modes below let you match the packaging to your model's capacity.

### Separate files (default — recommended for small models)

```
samples/SampleName/
  overview.md          ← metadata, compat warnings, project tree (tiny — ~1–2 KB)
  source/App.mc        ← original .mc files copied verbatim
  source/View.mc
  manifest.xml
  resources/
```

Each sample gets its own subdirectory. Source files are copied verbatim as `.mc` files — no Markdown wrappers. The `overview.md` is intentionally minimal (app type, APIs used, classes, compat warnings, links to source files, and a project tree), so a model can load it cheaply to decide whether it needs to read the source at all. Individual source files are then loaded only when needed.

**Best for:** small local models (7B–13B, 32k context) and any setup where selective file loading is the norm.

### Consolidated (recommended for large-context models)

```
samples/SampleName.md          ← metadata + manifest + all .mc source in one file
samples/SampleName_part2.md   ← continuation, if the file exceeds the threshold
```

All source files for each sample are merged into a single `.md` file. This eliminates multiple round-trips to read a sample, at the cost of loading everything at once.

**Best for:** large-context models (Claude, GPT-4, 128k+ context) where minimising tool calls matters more than minimising tokens loaded.

---

## Choosing a Threshold

Doc files (and consolidated sample files) that exceed the threshold are split at heading boundaries into numbered parts. Each part gets a navigation banner listing all other parts.

A rough conversion: **1 KB ≈ 250 tokens**.

| Model context | Suggested threshold |
|---|---|
| 32k tokens (e.g. Qwen2.5-Coder-7B) | 18–25 KB |
| 64k tokens | 32–40 KB |
| 128k tokens | 50–70 KB |
| 200k+ tokens | 80–120 KB |

Setting the threshold too high means a single file can consume most of the model's context. Setting it too low creates many small parts, increasing the number of reads needed per topic. The default of 18 KB was chosen to fit comfortably within the usable working space of a 32k model after system prompts are accounted for.

---

## General vs Device-Specific Builds

### Multi-device support

The device picker is now multi-select. You can target multiple devices simultaneously (e.g. venu3 + venu3s). When multiple devices are selected:

- Each sample's `overview.md` shows per-device manifest listing status individually
- Unsupported API warnings name the specific affected device(s)
- The `SAMPLES_INDEX.md` shows ✓ (all listed), (partial) (some listed), or (unlisted) (none listed)
- Strict filtering offers two sub-modes: exclude if not listed for *at least one* selected device, or exclude if not listed for *all* selected devices

### General build

All "Supported Devices" sections are preserved exactly as they appear in the SDK. The Device Reference directory contains all ~160 device files. Use this if you're not targeting a specific device or want to understand cross-device compatibility.

### Device-specific build

When you select a target device, the output is tailored in four ways:

| Area | Behaviour |
|------|-----------|
| API member/method supported on your device | "Supported Devices" section stripped — support is implied, no need to list it |
| API member/method **not** supported on your device | Section replaced with **`⚠️ Not supported on [device].`** inline warning |
| Device Reference | Only `Overview.md` + your device's file included |
| Sample manifests | `<iq:products>` lists stripped to reduce noise |

The normalisation handles the full range of Garmin product naming: registered/trademark symbols (`®`, `™`), accented characters (fēnix → fenix), and combined entries like "Edge® 1040 / 1040 Solar" are all matched correctly.

### How useful is the device-specific build?

For a **Garmin Venu 3**, the numbers are:

- The Device Reference shrinks from **2.53 MB / 164 files → ~25 KB / 2 files**
- The Toybox API docs lose approximately **196 KB of "Supported Devices" list text (~50K tokens)**
- Total output reduction: roughly **2.7 MB / ~675K tokens**

The practical value of the token savings depends on how much of the reference you load at once — for task-focused development you'd typically only load a handful of files at a time, so the savings on those files are moderate rather than transformational.

The **warning injection** is where the device-specific build earns its keep. Across the full Venu 3 API surface, exactly **13 unsupported members and methods** were identified:

| Module | Unsupported item |
|--------|-----------------|
| `Attention` | `hasFlashlightColor()` |
| `BluetoothLowEnergy` | `getBondedDevices()` |
| `System.DeviceSettings` | `fontScale` |
| `System.DeviceSettings` | `isNightModeEnabled` |
| `PersistedContent` | `getAppCourses()`, `getAppRoutes()`, `getAppTracks()` |
| `PersistedContent` | `getCourses()`, `getRoutes()`, `getTracks()` |
| `PersistedLocations` | *(module-level)* |
| `WatchUi` | `getSubscreen()` |

The compatibility information for all of this is present in the general build too — it's the same "Supported Devices" lists that the script reads to generate these warnings. The difference is reliability of consumption. In the general build, an LLM has to actively read each device list, correctly identify whether the target device appears in it, and do that consistently across 101 separate occurrences. Device name matching is surprisingly error-prone: Garmin's naming includes symbols like `®` and `™`, accented characters (fēnix), combined entries like "Edge® 1040 / 1040 Solar", and names that share substrings (Venu® 3 vs Venu® 3S vs Venu® 4 45mm / D2™ Air X15). During development of this script, a purpose-built matching function initially produced a false positive on `fontScale` because an unrelated entry contained the character "3". A language model parsing the same lists ad hoc is exposed to the same class of error. The device-specific build eliminates that risk by doing the matching once, deterministically, and replacing each list with either silence (supported) or an unambiguous inline warning (not supported) that is hard to miss.

The Venu 3 is a relatively broad-compatibility device (88 of 101 "Supported Devices" sections confirm support). For a more constrained device — a cycling Edge computer or an older watch — the ratio would be less favourable and the filtering would matter proportionally more.

---

## What Gets Converted

| Source | Output |
|--------|--------|
| `doc/Toybox/*.html` + subdirs | One `.md` per class under `docs/Toybox/` |
| `doc/docs/**/*.html` | Programmer's guide under `docs/docs/` |
| `samples/*/` | One `.md` per sample project |
| `templates/*/` | One `.md` per template variant |
| `doc/resources/` images | Catalog entries in `SDK_IMAGE_CATALOG.md` |

### HTML processing pipeline

Each HTML file goes through the following steps before becoming Markdown:

1. `nav`, `header`, `footer` tags removed
2. "Supported Devices" sections handled (stripped or replaced with warning, depending on build mode)
3. Language hints transferred from `<pre class="java ...">` to inner `<code>` tag — Garmin uses `java` and `typescript` class names for Monkey C code; these are mapped to ` ```monkeyc `
4. `<img>` elements removed (catalogued separately)
5. `<dl>/<dt>/<dd>` definition lists flattened to bold/plain paragraph pairs
6. `<main>` extracted (falls back to `<body>`)
7. `markdownify` converts to Markdown
8. CRLF → LF normalisation
9. Dead `.html` links converted to backtick inline code
10. `[show all](#)` / `[collapse](#)` UI artefacts stripped
11. Excess blank lines collapsed

In **separate mode**, sample projects are copied verbatim into per-file subdirectories. The `overview.md` is generated fresh from the source; `.mc` files are copied as-is with no Markdown wrapping. In **consolidated mode**, all source content is merged into a single `.md` file with an ASCII project tree at the top, followed by all source files in order (manifest → jungle → `.mc` source). Device-variant resource directories (e.g. `resources-vivoactive_hr/`) are omitted from consolidated output as they are rarely relevant to coding tasks.

---

## Copyright Notice

This repository contains only the conversion script. The generated Markdown output is **not included** and cannot be, because it is derived directly from the Garmin Connect IQ SDK documentation. Garmin's SDK license agreement prohibits uploading or hosting the SDK — including its documentation — on any website or server, in whole or in part.

To use this script you must obtain the SDK yourself from the [Garmin developer portal](https://developer.garmin.com/connect-iq/sdk/) and run the script against your own local copy. The generated output is for personal local use only — do not redistribute it.

The script itself is MIT licensed — see `LICENSE`.

---

## Related

- [Venu 3 Claude Coding Knowledge Base](https://github.com/MJenkinsonGIT/Venu3ClaudeCodingKnowledge) — real-world Connect IQ development lessons for the Garmin Venu 3, built alongside this tooling

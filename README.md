<h1 align="center">
  <img src="assets/logo.png" alt="DeckIntegrity logo" width="200">
  <br>
  DeckIntegrity
  <br>
</h1>

<p align="center">
  <a href="https://github.com/madebytjs/DeckIntegrity/releases/latest/download/DeckIntegrity-x86_64.AppImage">
    <img src="assets/download-button.svg" alt="Download the AppImage" width="300">
  </a>
</p>

<p align="center">
  <a href="https://github.com/madebytjs/DeckIntegrity/releases"><img src="https://img.shields.io/github/downloads/madebytjs/DeckIntegrity/total?color=b26bff" alt="Downloads"></a>
  <a href="https://github.com/madebytjs/DeckIntegrity/releases/latest"><img src="https://img.shields.io/github/v/release/madebytjs/DeckIntegrity?color=ff4f86" alt="Latest release"></a>
  <a href="https://github.com/madebytjs/DeckIntegrity/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/madebytjs/DeckIntegrity/tests.yml?branch=main&label=tests" alt="Tests"></a>
  <a href="https://github.com/madebytjs/DeckIntegrity/stargazers"><img src="https://img.shields.io/github/stars/madebytjs/DeckIntegrity" alt="Stars"></a>
  <a href="https://github.com/madebytjs/DeckIntegrity/commits/main"><img src="https://img.shields.io/github/last-commit/madebytjs/DeckIntegrity.svg" alt="Last commit"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/madebytjs/DeckIntegrity" alt="Licence: MIT"></a>
</p>

<p align="center">
  Non-destructive data-integrity diagnostics for SteamOS handhelds such as the Steam Deck and Legion Go S, and other Linux machines.
</p>

## 📖 About

DeckIntegrity reads the same data again and again through independent paths and records every result. If your handheld sometimes corrupts data (files that hash differently each time, games that fail to verify, errors that come and go), it collects evidence you can hand to support or post in a bug report.

It came out of a Steam Deck OLED that produced different SHA-256 hashes for the same unchanged file, and even for a stream of zeros that never touches storage. The faults came and went between reboots, which made them hard to prove. This tool repeats the tests for as long as you like, records exactly when anything goes wrong, and keeps what was observed separate from what it might mean.

> **Status:** v0.3. The command-line engine has been tested on a Legion Go S running SteamOS 3.8. The desktop app and AppImage are new, and Steam Deck testing is pending.

### ✨ Features

- **Desktop app** with Quick (about 30 seconds), Full (about 2 minutes) and Endurance (15 minutes to 8 hours) modes
- **Live timeline** of every pass, so you can see whether faults cluster in time, plus temperatures
- **Pause, resume and stop** at any point, and stopping still saves a full report
- **Independent cross-checks:** coreutils (`sha256sum`, `dd`) and Python's own hashing, buffered and direct I/O
- **Locates corruption** in the SSD test file to 32 MiB regions
- **Reports for support:** a short plain-text summary, a full log and machine-readable JSON
- **Safe by design:** no root, no system changes, no installs; test files are written once and always cleaned up

### 🔬 What it checks

| Check | What it does | Touches the SSD? |
|---|---|---|
| 2 GiB `/dev/zero` stream | Hashes a known stream three ways (`head \| sha256sum`, `dd \| sha256sum`, Python) against the published hash | No |
| RAM-backed file | Random file in `/dev/shm`, hashed by `sha256sum` and Python | No |
| SSD test file | Random file in your home folder, read back five ways: cold buffered, page cache, cold buffered (Python), direct I/O (`dd`), direct I/O (Python) | Writes once, then only reads |
| Inventory | OS, kernel, device, CPU, GPU, storage, kernel log warnings | No |
| Counters | Btrfs and ext4 error counters, kernel taint flags | No |

Every hash is compared against a reference that was **never read back from disk**: either a published constant or the hash of the data in memory as it was written.

## 💾 Installation

On your Steam Deck or other SteamOS handheld, switch to **Desktop Mode**, then:

1. Click **Download AppImage** above (or get it from [Releases](https://github.com/madebytjs/DeckIntegrity/releases)).
2. In Dolphin, right-click `DeckIntegrity-x86_64.AppImage` → **Properties** → **Permissions** → tick **Is executable**.
3. Double-click it to open the app.

Nothing is installed. To remove it, delete the AppImage. Your reports stay in `~/deck-integrity-reports` until you delete them too.

The app is designed for Desktop Mode, with touchscreen and trackpad. Game Mode support may come later.

### 🖥️ Command line

The same engine runs without the app, from Konsole:

```bash
./DeckIntegrity-x86_64.AppImage --cli --quick              # about 30 seconds
./DeckIntegrity-x86_64.AppImage --cli                      # full run: 5 passes, 2 GiB SSD file
./DeckIntegrity-x86_64.AppImage --cli --endurance 60       # repeat every check for an hour
./DeckIntegrity-x86_64.AppImage --cli --help
```

Prefer something tiny? `deck-integrity.pyz` (about 75 KiB, on the Releases page) runs with the Python already on SteamOS: `python3 deck-integrity.pyz --quick`. It only runs from a terminal; double-clicking it does nothing apart from a notification.

## 📄 Reports

Each run gets its own folder, named so runs from different devices and OS versions sort and compare easily:

```text
~/deck-integrity-reports/2026-10-20_124234_Legion-Go-S-8ARP1_SteamOS-3.8.28_quick/
                         date and time     device            OS               mode
```

| File | Contents |
|---|---|
| `support-summary.txt` | Short, plain summary to send to support |
| `failures.txt` | Every failed pass: expected and observed hashes |
| `report.txt` | Full log, including command output |
| `results.json` | Everything, per pass, machine-readable. Saved as it goes, so a crash still leaves evidence |
| `system-info.json` | Device and software inventory |

The app's **Past runs** list shows every report folder with its outcome.

## 🔍 Reading the results

- A **PASS** means no fault was observed in this run. It does not certify the device as healthy. Intermittent faults can be missed, which is what Endurance mode is for.
- A **FAIL** is evidence of a data-integrity fault somewhere in the tested path. It does not by itself say which component.
- **ERROR** means a check could not complete. That is not the same as a fault.
- Redaction of usernames, hostnames and serial numbers is best effort. Check the files before posting them publicly.

## 🛡️ Safety

Runs as your normal user. It never needs root or `sudo`, never writes to block devices, never modifies system files and never installs anything. It writes only its own temporary test files, which are deleted afterwards even if you stop the run or close the window, and its reports.

## 🛠️ Development

```bash
python -m venv .venv && .venv/bin/pip install PySide6-Essentials
PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests -t tests
PYTHONPATH=src .venv/bin/python -m deck_integrity.gui      # run the app from source
python scripts/build_pyz.py                                # builds dist/deck-integrity.pyz
```

The engine is Python 3.9+ standard library only; the app adds PySide6. The AppImage is built by GitHub Actions (`scripts/build_appimage.sh`), and pushing a `v*` tag creates a draft release with both downloads attached.

## 📜 Licence

[MIT](LICENSE). Free to use, modify and share; provided as is, without warranty.

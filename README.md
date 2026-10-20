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
  <a href="https://github.com/madebytjs/DeckIntegrity/releases"><img src="https://img.shields.io/github/downloads/madebytjs/DeckIntegrity/total?color=b26bff&v=1" alt="Downloads"></a>
  <a href="https://github.com/madebytjs/DeckIntegrity/releases/latest"><img src="https://img.shields.io/github/v/release/madebytjs/DeckIntegrity?color=ff4f86&v=1" alt="Latest release"></a>
  <a href="https://github.com/madebytjs/DeckIntegrity/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/madebytjs/DeckIntegrity/tests.yml?branch=main&label=tests" alt="Tests"></a>
  <a href="https://github.com/madebytjs/DeckIntegrity/stargazers"><img src="https://img.shields.io/github/stars/madebytjs/DeckIntegrity?v=1" alt="Stars"></a>
  <a href="https://github.com/madebytjs/DeckIntegrity/commits/main"><img src="https://img.shields.io/github/last-commit/madebytjs/DeckIntegrity.svg" alt="Last commit"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/madebytjs/DeckIntegrity?v=1" alt="Licence: MIT"></a>
</p>

<p align="center">
  <b>A hardware health check for SteamOS handhelds</b> such as the Steam Deck and Legion Go S.<br>
  It tests whether your storage, memory and processor store and handle your data correctly.
</p>

## 📖 About

**Is my handheld healthy?** Most "health" tools only read numbers the hardware reports about itself. DeckIntegrity tests the thing that actually matters: whether your device gives you back exactly the data you gave it. It writes known data, reads it back many times through different routes, and checks every copy is identical, down to the last byte.

If a game keeps failing to verify, files seem to corrupt themselves, installs fail for no clear reason, or you just want peace of mind after a repair, this gives you a clear answer and a report you can hand to support.

It came out of a Steam Deck OLED that gave different results for the same unchanged file, and even for data that never touched the SSD. The problem came and went between reboots, which made it hard to prove. DeckIntegrity can repeat its checks for hours, records exactly when anything goes wrong, and keeps what it observed separate from what it might mean.

> [!WARNING]
> **Use at your own risk.** DeckIntegrity is unofficial, comes with no warranty, and is not affiliated with Valve or Lenovo. It is designed to be non-destructive, but it does write test files and work your device hard. See the [Disclaimer](#disclaimer).

> **Status:** v0.4.0. Tested on a Legion Go S running SteamOS 3.8. Steam Deck testing is pending.

### ✨ Features

- **Simple desktop app:** Quick (about 30 seconds), Full (about 2 minutes) and Endurance (15 minutes to 8 hours) checks
- **Catches problems that come and go:** Endurance mode repeats the checks and shows on a timeline exactly when anything went wrong, alongside temperatures
- **Clear results:** "No problems found" or "Problems found", with a plain-English summary to send to support
- **Health overview:** at-a-glance cards for processor and memory, RAM, storage, SSD wear, battery capacity, filesystem errors and temperatures, each showing the real measured value
- **Pause, resume and stop** at any point; stopping still saves a full report
- **Safe by design:** no admin rights, no system changes, nothing installed; test files are written once and always cleaned up

### 🔬 What it checks

| Check | In plain English | Technical detail |
|---|---|---|
| **Processor and memory** | Sends 2 GiB of known data through the processor and memory and checks it arrives unchanged. Never touches storage. | `/dev/zero` stream hashed three ways (`head \| sha256sum`, `dd \| sha256sum`, Python) against the published SHA-256 |
| **Memory (RAM)** | Stores test data in memory, reads it back repeatedly and checks it is identical every time. | Random file in `/dev/shm`, hashed by `sha256sum` and Python |
| **Storage (SSD)** | Writes one test file, then reads it back five different ways and checks every copy matches. Pinpoints which part of the file changed. | Read cold, from page cache and with direct I/O, by coreutils and Python; mismatches located to 32 MiB regions |
| **SSD health** | Reads the drive's own health log: how much of its rated life is used, spare capacity, and any media errors. Optional, and asks for your admin password. | NVMe SMART log via `smartctl` (read-only), elevated with `pkexec` only when you opt in |
| **Battery** | How much charge the battery holds now compared with when it was new, and its charge cycles. | `energy_full` / `energy_full_design` (or `charge_*`) from sysfs; no admin rights needed |
| **Error counters** | Asks the system whether it has already recorded any storage errors or serious kernel problems. | Btrfs and ext4 error counters, kernel taint flags |
| **Device details** | Records your model, OS and driver versions, so results can be compared between devices and updates. | DMI, OS release, kernel, CPU, GPU, storage, kernel log warnings |

Every result is compared against a reference that was **never read back from storage**: either a published constant or the fingerprint of the data in memory as it was written.

**What it does not check:** screen, controls, audio or graphics performance. It is not a full memory test like Memtest86 either. A clean result means no problems were seen during that run, not a guarantee of perfect health.

**About the SSD health report:** the drive's health log is normally readable only by an administrator, so DeckIntegrity asks for your password through the system's own prompt, and only if you tick the option. On a Steam Deck there is no admin password until you set one with `passwd` in Konsole. Without it, everything else still runs and the SSD health card shows "Not measured".

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
./DeckIntegrity-x86_64.AppImage --cli --ssd-health         # also read the SSD health log (asks for your password)
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

<a id="disclaimer"></a>

## ⚠️ Disclaimer

DeckIntegrity is free, unofficial software provided **as is**, without warranty of any kind, and you use it **entirely at your own risk**.

- It is designed to be non-destructive: it never needs root, never touches system files or partitions, and deletes its own test files. It still writes and reads large test files and keeps your device busy, which adds heat, battery drain and some SSD wear.
- The authors are not responsible for any damage, data loss, failed repairs, voided warranties or other problems with your device. Back up anything important first.
- Results are evidence to help an investigation, not a diagnosis. A pass does not prove a device is healthy, and a failure does not prove which part is faulty. Contact your device maker's support for repairs.
- DeckIntegrity is not affiliated with or endorsed by Valve, Lenovo or any other device maker. Steam, Steam Deck and SteamOS are trademarks of Valve Corporation. Legion is a trademark of Lenovo.

The app shows this notice before your first test. The [MIT licence](LICENSE) contains the full warranty disclaimer.

## 📜 Licence

[MIT](LICENSE). Free to use, modify and share; provided as is, without warranty.

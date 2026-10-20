# Deck Integrity Suite

Non-destructive data-integrity diagnostics for SteamOS handhelds (Steam Deck, Legion Go S) and other Linux machines. It hashes the same data many times through independent paths and records every result. Faults that come and go leave evidence you can hand to support.

Status: **v0.2, command line only, not yet validated on SteamOS hardware.**

## What it checks

| Check | What it does | Touches the SSD? |
|---|---|---|
| 2 GiB `/dev/zero` stream | Hashes a known stream three ways (`head \| sha256sum`, `dd \| sha256sum`, Python) against the published hash | No |
| RAM-backed file | Random file in `/dev/shm`, hashed by `sha256sum` and Python | No |
| SSD test file | Random file in your home folder, read back five ways: cold buffered, page cache, cold buffered (Python), direct I/O (`dd`), direct I/O (Python). Mismatches are located to 32 MiB regions | Writes once, then only reads |
| Inventory | OS, kernel, device, CPU, GPU, storage, kernel log warnings | No |
| Counters | Btrfs and ext4 error counters, kernel taint flags | No |

Every hash is compared against a reference that was **never read back from disk**: either a published constant or the hash of the data in memory as it was written.

## Run it

On the device, in Desktop Mode, open Konsole:

```bash
python3 deck-integrity.pyz --quick     # about 30 seconds
python3 deck-integrity.pyz             # full run: 5 passes, 2 GiB SSD file, about 2 minutes
python3 deck-integrity.pyz --help
```

Reports go to `~/deck-integrity-reports/<timestamp>/`:

| File | Contents |
|---|---|
| `support-summary.txt` | Short, plain summary to send to support |
| `failures.txt` | Every failed pass: expected and observed hashes |
| `report.txt` | Full log, including command output |
| `results.json` | Everything, per pass, machine-readable. Rewritten as it goes, so a crash still leaves evidence |
| `system-info.json` | Device and software inventory |

Exit codes: `0` no faults observed, `1` integrity fault observed, `2` a check could not complete, `130` interrupted.

## Safety

Runs as your normal user. Never needs root or `sudo`, never writes to block devices, never modifies system files, never installs anything. It only writes its own temporary test file (deleted afterwards, even on Ctrl+C or when the window closes) and its reports. Use `--keep-file` to keep the SSD test file.

## Reading the results

- A **PASS** means no fault was observed in this run. It does not certify the device as healthy.
- A **FAIL** is evidence of a data-integrity fault somewhere in the tested path. It does not by itself say which component.
- **ERROR** means a check could not complete. That is not the same as a fault.
- Redaction of usernames, hostnames and serials is best effort. Check the files before posting them publicly.

## Development

```bash
PYTHONPATH=src:tests python -m unittest discover -s tests -t tests   # use src;tests on Windows
python scripts/build_pyz.py                                           # builds dist/deck-integrity.pyz
```

Python 3.9+ standard library only.

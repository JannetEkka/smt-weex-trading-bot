# v2/ — Daemon Backup Folder

This folder holds versioned backups of `smt_daemon_v3_1.py` (the live production daemon).

## Naming Scheme

| File | Meaning |
|------|---------|
| `smt_daemon_v2_N.py` | Current working copy (latest version N) |
| `backup/smt_daemon_v2_N.py.bak` | Backup of the previous version N |

## On Each New Version

1. Copy old `smt_daemon_v2_N.py` → `backup/smt_daemon_v2_N.py.bak`
2. Copy updated `v3/smt_daemon_v3_1.py` → `smt_daemon_v2_(N+1).py`

## Current

- Live: `smt_daemon_v2_1.py` (snapshot of V3.2.1)
- Backups: `backup/` (empty until next version)

# LSM-Trees: Forensic Exploration

Hands-on lab for understanding LSM-Tree internals — the write-optimized data structure behind RocksDB, Cassandra, and LevelDB. Explores the write path (WAL → memtable → SSTable), read amplification, bloom filters, compaction, crash recovery, and head-to-head comparison with B+Trees.

## Files

| File | Purpose |
|------|---------|
| `setup.sh` | Installs all dependencies (one-time) |
| `lab_lsm.ipynb` | The lab notebook — run this |
| `lsm_engine.py` | Pure-Python LSM-Tree engine with full observability (used by the notebook) |
| `.gitignore` | Keeps generated files out of the repo |

All generated artifacts (SSTables, WAL files, images, SQLite DBs) are written to `_output/` at runtime.

## Setup

```bash
# 1. Install dependencies
./setup.sh

# 2. Activate the virtual environment
source .venv/bin/activate

# 3. Open the lab
jupyter notebook lab_lsm.ipynb
```

## Prerequisites

- Python 3.10+
- macOS or Linux

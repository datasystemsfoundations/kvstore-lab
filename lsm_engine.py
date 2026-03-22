"""
Educational LSM-Tree Engine — a from-scratch implementation for forensic exploration.

This is NOT a production LSM-tree. It's designed to make every internal operation
visible and measurable: WAL writes, memtable inserts, flushes to SSTables,
compaction, bloom filter checks, and crash recovery.

Usage:
    from lsm_engine import LSMTree
    db = LSMTree("_output/mydb", memtable_capacity=100)
    db.put("key1", "value1")
    print(db.get("key1"))
    db.close()
"""

from __future__ import annotations
import os
import json
import time
import struct
import hashlib
from collections import OrderedDict
from typing import Optional
from dataclasses import dataclass, field


# ── Bloom Filter ────────────────────────────────────────────────────────────

class BloomFilter:
    """Simple bloom filter for SSTable key lookups."""

    def __init__(self, capacity: int = 1000, fp_rate: float = 0.01):
        import math
        self.size = max(1, int(-capacity * math.log(fp_rate) / (math.log(2) ** 2)))
        self.num_hashes = max(1, int(self.size / capacity * math.log(2)))
        self.bits = bytearray(self.size)
        self.items_added = 0

    def _hashes(self, key: str) -> list[int]:
        h1 = int(hashlib.md5(key.encode()).hexdigest(), 16)
        h2 = int(hashlib.sha1(key.encode()).hexdigest(), 16)
        return [(h1 + i * h2) % self.size for i in range(self.num_hashes)]

    def add(self, key: str):
        for pos in self._hashes(key):
            self.bits[pos] = 1
        self.items_added += 1

    def might_contain(self, key: str) -> bool:
        return all(self.bits[pos] for pos in self._hashes(key))

    def false_positive_rate(self) -> float:
        if self.items_added == 0:
            return 0.0
        import math
        return (1 - math.exp(-self.num_hashes * self.items_added / self.size)) ** self.num_hashes


# ── Write-Ahead Log ────────────────────────────────────────────────────────

class WAL:
    """
    Append-only write-ahead log.
    Each entry: [length:4bytes][json_payload]
    """

    def __init__(self, path: str):
        self.path = path
        self.file = open(path, "ab")
        self.write_count = 0
        self.bytes_written = 0

    def append(self, op: str, key: str, value: Optional[str] = None):
        entry = json.dumps({"op": op, "key": key, "value": value, "ts": time.time()})
        data = entry.encode()
        header = struct.pack(">I", len(data))
        self.file.write(header + data)
        self.file.flush()
        self.write_count += 1
        self.bytes_written += len(header) + len(data)

    def close(self):
        self.file.close()

    @staticmethod
    def replay(path: str) -> list[dict]:
        """Replay WAL from disk — used for crash recovery."""
        entries = []
        if not os.path.exists(path):
            return entries
        with open(path, "rb") as f:
            while True:
                header = f.read(4)
                if len(header) < 4:
                    break
                length = struct.unpack(">I", header)[0]
                data = f.read(length)
                if len(data) < length:
                    break  # truncated entry — crash happened mid-write
                entries.append(json.loads(data.decode()))
        return entries

    def size_bytes(self) -> int:
        return os.path.getsize(self.path) if os.path.exists(self.path) else 0


# ── Memtable ────────────────────────────────────────────────────────────────

class Memtable:
    """
    In-memory sorted key-value store (simulates a red-black tree / skip list).
    We use a sorted dict for simplicity — the key insight is that it's SORTED.
    """

    def __init__(self, capacity: int = 1000):
        self.data: dict[str, Optional[str]] = {}  # None = tombstone (delete)
        self.capacity = capacity
        self.write_count = 0

    def put(self, key: str, value: Optional[str]):
        self.data[key] = value
        self.write_count += 1

    def get(self, key: str) -> tuple[bool, Optional[str]]:
        """Returns (found, value). value=None means tombstone (deleted)."""
        if key in self.data:
            return True, self.data[key]
        return False, None

    def is_full(self) -> bool:
        return len(self.data) >= self.capacity

    def sorted_items(self) -> list[tuple[str, Optional[str]]]:
        return sorted(self.data.items())

    def clear(self):
        self.data.clear()
        self.write_count = 0

    def __len__(self):
        return len(self.data)


# ── SSTable (Sorted String Table) ──────────────────────────────────────────

@dataclass
class SSTableMeta:
    """Metadata for an SSTable file."""
    path: str
    level: int
    seq_num: int
    num_keys: int
    min_key: str
    max_key: str
    size_bytes: int
    bloom_filter: BloomFilter
    created_at: float = field(default_factory=time.time)


class SSTable:
    """
    Immutable sorted file on disk.
    Format: sorted JSON lines (one key-value pair per line).
    Real SSTables use binary format with block indexes — we use JSON for readability.
    """

    @staticmethod
    def write(path: str, items: list[tuple[str, Optional[str]]], level: int,
              seq_num: int) -> SSTableMeta:
        """Flush sorted items to an SSTable file."""
        bloom = BloomFilter(capacity=max(len(items), 1))
        with open(path, "w") as f:
            for key, value in items:
                f.write(json.dumps({"key": key, "value": value}) + "\n")
                bloom.add(key)

        return SSTableMeta(
            path=path,
            level=level,
            seq_num=seq_num,
            num_keys=len(items),
            min_key=items[0][0] if items else "",
            max_key=items[-1][0] if items else "",
            size_bytes=os.path.getsize(path),
            bloom_filter=bloom,
        )

    @staticmethod
    def read_all(path: str) -> list[tuple[str, Optional[str]]]:
        """Read all entries from an SSTable."""
        items = []
        with open(path, "r") as f:
            for line in f:
                entry = json.loads(line)
                items.append((entry["key"], entry["value"]))
        return items

    @staticmethod
    def search(path: str, target_key: str) -> tuple[bool, Optional[str], int]:
        """
        Search for a key in an SSTable.
        Returns (found, value, comparisons).
        In a real SSTable, this would use a block index for O(log N) lookup.
        We do linear scan here to show the cost of checking each SSTable.
        """
        comparisons = 0
        with open(path, "r") as f:
            for line in f:
                entry = json.loads(line)
                comparisons += 1
                if entry["key"] == target_key:
                    return True, entry["value"], comparisons
                if entry["key"] > target_key:
                    return False, None, comparisons  # past it, sorted order
        return False, None, comparisons


# ── LSM-Tree ───────────────────────────────────────────────────────────────

class LSMTree:
    """
    Educational LSM-Tree with full observability.

    Write path:  put(k,v) → WAL append → memtable insert → [flush to SSTable if full]
    Read path:   get(k) → memtable → L0 SSTables → L1 SSTables → ... (newest first)
    Delete path:  delete(k) → put(k, TOMBSTONE)
    Recovery:     replay WAL → reconstruct memtable
    """

    def __init__(self, db_dir: str, memtable_capacity: int = 1000,
                 l0_compaction_trigger: int = 4):
        self.db_dir = db_dir
        self.memtable_capacity = memtable_capacity
        self.l0_compaction_trigger = l0_compaction_trigger
        os.makedirs(db_dir, exist_ok=True)

        self.memtable = Memtable(capacity=memtable_capacity)
        self.wal = WAL(os.path.join(db_dir, "wal.log"))
        self.sstables: list[SSTableMeta] = []  # ordered newest-first per level
        self.seq_num = 0

        # Stats
        self.stats = {
            "puts": 0, "gets": 0, "deletes": 0,
            "flushes": 0, "compactions": 0,
            "bloom_true_negatives": 0, "bloom_false_positives": 0,
            "bloom_true_positives": 0,
            "sstable_reads": 0,
            "wal_bytes": 0,
        }

    def put(self, key: str, value: str) -> dict:
        """Write a key-value pair. Returns stats about the operation."""
        self.stats["puts"] += 1

        # Step 1: Write to WAL (durability)
        self.wal.append("put", key, value)
        self.stats["wal_bytes"] = self.wal.bytes_written

        # Step 2: Write to memtable (fast, in-memory)
        self.memtable.put(key, value)

        # Step 3: If memtable is full, flush to SSTable
        flushed = False
        compacted = False
        if self.memtable.is_full():
            self._flush()
            flushed = True
            # Check if L0 needs compaction
            l0_count = sum(1 for s in self.sstables if s.level == 0)
            if l0_count >= self.l0_compaction_trigger:
                self._compact(0)
                compacted = True

        return {"flushed": flushed, "compacted": compacted}

    def get(self, key: str) -> tuple[Optional[str], dict]:
        """
        Read a key. Returns (value, read_stats).
        Checks memtable first (fast), then SSTables newest-to-oldest.
        """
        self.stats["gets"] += 1
        read_stats = {
            "found_in": None,
            "memtable_checked": True,
            "sstables_checked": 0,
            "bloom_skipped": 0,
            "total_comparisons": 0,
        }

        # Step 1: Check memtable
        found, value = self.memtable.get(key)
        if found:
            read_stats["found_in"] = "memtable"
            if value is None:
                return None, read_stats  # tombstone
            return value, read_stats

        # Step 2: Check SSTables (newest first within each level)
        for sst in self.sstables:
            # Bloom filter check — can we skip this SSTable entirely?
            if not sst.bloom_filter.might_contain(key):
                read_stats["bloom_skipped"] += 1
                self.stats["bloom_true_negatives"] += 1
                continue

            # Must check this SSTable
            read_stats["sstables_checked"] += 1
            self.stats["sstable_reads"] += 1
            found, value, comparisons = SSTable.search(sst.path, key)
            read_stats["total_comparisons"] += comparisons

            if found:
                if not sst.bloom_filter.might_contain(key):
                    self.stats["bloom_false_positives"] += 1
                else:
                    self.stats["bloom_true_positives"] += 1
                read_stats["found_in"] = f"L{sst.level}/sst_{sst.seq_num}"
                if value is None:
                    return None, read_stats  # tombstone
                return value, read_stats

        read_stats["found_in"] = None
        return None, read_stats

    def delete(self, key: str):
        """Delete by writing a tombstone."""
        self.stats["deletes"] += 1
        self.wal.append("delete", key, None)
        self.memtable.put(key, None)  # tombstone
        if self.memtable.is_full():
            self._flush()

    def _flush(self):
        """Flush memtable to a new L0 SSTable."""
        self.stats["flushes"] += 1
        self.seq_num += 1
        path = os.path.join(self.db_dir, f"sst_{self.seq_num:04d}_L0.json")
        items = self.memtable.sorted_items()
        meta = SSTable.write(path, items, level=0, seq_num=self.seq_num)
        self.sstables.insert(0, meta)  # newest first
        self.memtable.clear()

        # Reset WAL after successful flush
        self.wal.close()
        wal_path = os.path.join(self.db_dir, "wal.log")
        os.remove(wal_path)
        self.wal = WAL(wal_path)

    def _compact(self, level: int):
        """
        Merge all SSTables at `level` into a single SSTable at `level + 1`.
        This is simplified size-tiered compaction.
        """
        self.stats["compactions"] += 1
        tables_to_compact = [s for s in self.sstables if s.level == level]
        if len(tables_to_compact) < 2:
            return

        # Merge all entries, keeping newest value for each key
        merged: dict[str, Optional[str]] = {}
        # Process oldest first so newest overwrites
        for sst in reversed(tables_to_compact):
            for key, value in SSTable.read_all(sst.path):
                merged[key] = value

        # Remove tombstones during compaction (garbage collection)
        live_items = sorted((k, v) for k, v in merged.items() if v is not None)

        # Write merged SSTable at next level
        self.seq_num += 1
        new_level = level + 1
        path = os.path.join(self.db_dir, f"sst_{self.seq_num:04d}_L{new_level}.json")
        meta = SSTable.write(path, live_items, level=new_level, seq_num=self.seq_num)

        # Remove old SSTables
        for sst in tables_to_compact:
            os.remove(sst.path)
            self.sstables.remove(sst)

        self.sstables.append(meta)
        # Sort: newest first within each level, lower levels first
        self.sstables.sort(key=lambda s: (s.level, -s.seq_num))

    def close(self):
        self.wal.close()

    # ── Recovery ────────────────────────────────────────────────────────

    @classmethod
    def recover(cls, db_dir: str, memtable_capacity: int = 1000,
                l0_compaction_trigger: int = 4) -> tuple['LSMTree', dict]:
        """
        Recover an LSM-Tree from disk after a crash.
        1. Load existing SSTables
        2. Replay WAL to reconstruct memtable
        Returns (tree, recovery_stats).
        """
        recovery_stats = {
            "wal_entries_replayed": 0,
            "sstables_found": 0,
            "keys_recovered": 0,
            "recovery_time_ms": 0,
        }
        start = time.time()

        tree = cls.__new__(cls)
        tree.db_dir = db_dir
        tree.memtable_capacity = memtable_capacity
        tree.l0_compaction_trigger = l0_compaction_trigger
        tree.memtable = Memtable(capacity=memtable_capacity)
        tree.sstables = []
        tree.seq_num = 0
        tree.stats = {
            "puts": 0, "gets": 0, "deletes": 0,
            "flushes": 0, "compactions": 0,
            "bloom_true_negatives": 0, "bloom_false_positives": 0,
            "bloom_true_positives": 0,
            "sstable_reads": 0,
            "wal_bytes": 0,
        }

        # Step 1: Discover existing SSTables
        import glob
        for sst_path in sorted(glob.glob(os.path.join(db_dir, "sst_*.json"))):
            fname = os.path.basename(sst_path)
            # Parse level from filename: sst_0001_L0.json
            parts = fname.replace(".json", "").split("_")
            level = int(parts[2][1:])  # L0 → 0
            seq = int(parts[1])
            tree.seq_num = max(tree.seq_num, seq)

            items = SSTable.read_all(sst_path)
            bloom = BloomFilter(capacity=max(len(items), 1))
            for k, v in items:
                bloom.add(k)

            meta = SSTableMeta(
                path=sst_path, level=level, seq_num=seq,
                num_keys=len(items),
                min_key=items[0][0] if items else "",
                max_key=items[-1][0] if items else "",
                size_bytes=os.path.getsize(sst_path),
                bloom_filter=bloom,
            )
            tree.sstables.append(meta)
            recovery_stats["sstables_found"] += 1

        tree.sstables.sort(key=lambda s: (s.level, -s.seq_num))

        # Step 2: Replay WAL to reconstruct memtable
        wal_path = os.path.join(db_dir, "wal.log")
        if os.path.exists(wal_path):
            entries = WAL.replay(wal_path)
            for entry in entries:
                tree.memtable.put(entry["key"], entry["value"])
                recovery_stats["wal_entries_replayed"] += 1

        recovery_stats["keys_recovered"] = len(tree.memtable)
        recovery_stats["recovery_time_ms"] = (time.time() - start) * 1000

        # Reopen WAL for new writes (append mode)
        tree.wal = WAL(wal_path)

        return tree, recovery_stats

    # ── Observability ───────────────────────────────────────────────────

    def describe(self) -> str:
        """Human-readable summary of the LSM-Tree state."""
        lines = [f"LSM-Tree: {self.db_dir}"]
        lines.append(f"  Memtable: {len(self.memtable)}/{self.memtable.capacity} keys")

        levels: dict[int, list[SSTableMeta]] = {}
        for sst in self.sstables:
            levels.setdefault(sst.level, []).append(sst)

        for level in sorted(levels.keys()):
            tables = levels[level]
            total_keys = sum(t.num_keys for t in tables)
            total_bytes = sum(t.size_bytes for t in tables)
            lines.append(f"  L{level}: {len(tables)} SSTable(s), "
                         f"{total_keys:,} keys, {total_bytes:,} bytes")

        total_disk = sum(s.size_bytes for s in self.sstables)
        lines.append(f"  Total disk: {total_disk:,} bytes")
        lines.append(f"  WAL size: {self.wal.size_bytes():,} bytes")
        return "\n".join(lines)

    def disk_usage(self) -> dict:
        """Return disk usage breakdown."""
        usage = {"wal": self.wal.size_bytes(), "levels": {}}
        for sst in self.sstables:
            level = f"L{sst.level}"
            if level not in usage["levels"]:
                usage["levels"][level] = {"tables": 0, "bytes": 0, "keys": 0}
            usage["levels"][level]["tables"] += 1
            usage["levels"][level]["bytes"] += sst.size_bytes
            usage["levels"][level]["keys"] += sst.num_keys
        usage["total"] = sum(v["bytes"] for v in usage["levels"].values()) + usage["wal"]
        return usage

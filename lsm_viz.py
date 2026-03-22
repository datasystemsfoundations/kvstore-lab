"""
LSM-Tree Visualizer — renders the internal state of an LSM-Tree using graphviz.

Produces diagrams showing:
- WAL entries
- Memtable contents (sorted)
- SSTable levels with key ranges
- Read/write path highlights
"""

from __future__ import annotations
import subprocess
import shutil
import os
from typing import Optional


def render_lsm_state(
    memtable_keys: list[str],
    memtable_capacity: int,
    wal_entries: list[str],
    levels: dict[int, list[dict]],  # level -> [{name, keys, min_key, max_key}]
    title: str = "",
    filename: str = "lsm_state",
    output_dir: str = ".",
    highlight_key: Optional[str] = None,
    highlight_path: Optional[list[str]] = None,  # ["wal", "memtable", "L0/sst_1"]
    fmt: str = "png",
) -> str:
    """Render the full LSM-Tree state as a diagram."""

    dot = ['digraph LSMTree {']
    dot.append('    rankdir=TB;')
    dot.append('    node [fontname="Courier", fontsize=11];')
    dot.append('    edge [fontname="Courier", fontsize=9];')
    if title:
        dot.append(f'    labelloc="t"; label="{title}"; fontsize=14; fontname="Helvetica Bold";')
    dot.append('')

    highlight_path = highlight_path or []

    # ── WAL ──
    wal_color = "salmon" if "wal" in highlight_path else "lightyellow"
    if wal_entries:
        wal_rows = "\\n".join(wal_entries[-8:])  # show last 8
        if len(wal_entries) > 8:
            wal_rows = f"... ({len(wal_entries) - 8} more)\\n{wal_rows}"
        dot.append(f'    wal [shape=note, style=filled, fillcolor="{wal_color}",')
        dot.append(f'         label="WAL (append-only)\\n─────────────\\n{wal_rows}"];')
    else:
        dot.append(f'    wal [shape=note, style=filled, fillcolor="{wal_color}",')
        dot.append(f'         label="WAL\\n(empty)"];')

    # ── Memtable ──
    mt_color = "salmon" if "memtable" in highlight_path else "lightblue"
    fill_pct = len(memtable_keys) / memtable_capacity * 100 if memtable_capacity > 0 else 0
    fill_bar = "█" * int(fill_pct / 10) + "░" * (10 - int(fill_pct / 10))

    if memtable_keys:
        display_keys = memtable_keys[:12]
        mt_rows = "\\n".join(display_keys)
        if len(memtable_keys) > 12:
            mt_rows += f"\\n... +{len(memtable_keys) - 12} more"
    else:
        mt_rows = "(empty)"

    # Highlight specific key in memtable
    if highlight_key and highlight_key in memtable_keys:
        mt_color = "salmon"

    dot.append(f'    memtable [shape=box, style="filled,rounded", fillcolor="{mt_color}",')
    dot.append(f'         label="Memtable (sorted, in RAM)\\n'
               f'{len(memtable_keys)}/{memtable_capacity} keys  [{fill_bar}]\\n'
               f'─────────────\\n{mt_rows}"];')

    # Arrow from WAL to Memtable
    dot.append('    wal -> memtable [label="  put(k,v)", style=bold, color=blue];')

    # ── SSTable Levels ──
    prev_node = "memtable"

    if not levels:
        dot.append('    no_sst [shape=plaintext, label="(no SSTables yet)"];')
        dot.append(f'    {prev_node} -> no_sst [label="  flush when full", style=dashed];')
    else:
        for level_num in sorted(levels.keys()):
            tables = levels[level_num]
            level_id = f"level_{level_num}"

            # Create a subgraph for this level
            dot.append(f'    subgraph cluster_L{level_num} {{')
            dot.append(f'        label="Level {level_num}  ({len(tables)} SSTable(s))";')
            dot.append(f'        style=dashed; color=gray;')

            for i, tbl in enumerate(tables):
                sst_id = f"sst_L{level_num}_{i}"
                sst_label_id = f"L{level_num}/sst_{i}"
                sst_color = "lightyellow"

                if sst_label_id in highlight_path:
                    sst_color = "salmon"
                if highlight_key and tbl.get('keys') and highlight_key in tbl['keys']:
                    sst_color = "salmon"

                key_range = f"[{tbl['min_key']} .. {tbl['max_key']}]"
                num_keys = tbl.get('num_keys', len(tbl.get('keys', [])))

                # Show some keys if available
                if tbl.get('keys'):
                    display = tbl['keys'][:6]
                    key_list = "\\n".join(display)
                    if num_keys > 6:
                        key_list += f"\\n... +{num_keys - 6} more"
                else:
                    key_list = f"{num_keys} keys"

                dot.append(f'        {sst_id} [shape=box, style=filled, fillcolor="{sst_color}",')
                dot.append(f'             label="{tbl["name"]}\\n{key_range}\\n'
                           f'─────────\\n{key_list}"];')

            dot.append('    }')

            # Edge from previous level
            first_sst = f"sst_L{level_num}_0"
            if level_num == 0:
                dot.append(f'    {prev_node} -> {first_sst} '
                           f'[label="  flush", style=dashed, color=darkgreen];')
            else:
                prev_sst = f"sst_L{level_num - 1}_0"
                dot.append(f'    {prev_sst} -> {first_sst} '
                           f'[label="  compact", style=dashed, color=purple];')

    dot.append('}')
    dot_src = "\n".join(dot)

    # Write and render
    dot_path = os.path.join(output_dir, f"{filename}.dot")
    img_path = os.path.join(output_dir, f"{filename}.{fmt}")

    with open(dot_path, "w") as f:
        f.write(dot_src)

    if shutil.which("dot"):
        subprocess.run(["dot", f"-T{fmt}", dot_path, "-o", img_path],
                       check=True, capture_output=True)
        return img_path
    return dot_path


def render_write_path(
    key: str,
    value: str,
    step: str,  # "wal", "memtable", "flush"
    memtable_keys: list[str],
    memtable_capacity: int,
    wal_entries: list[str],
    levels: dict[int, list[dict]],
    filename: str = "write_path",
    output_dir: str = ".",
    fmt: str = "png",
) -> str:
    """Render the write path with the current step highlighted."""
    highlight = []
    if step in ("wal", "memtable", "flush"):
        highlight.append("wal")
    if step in ("memtable", "flush"):
        highlight.append("memtable")

    title = f"put({key}, ...) — step: {step.upper()}"
    return render_lsm_state(
        memtable_keys=memtable_keys,
        memtable_capacity=memtable_capacity,
        wal_entries=wal_entries,
        levels=levels,
        title=title,
        filename=filename,
        output_dir=output_dir,
        highlight_key=key,
        highlight_path=highlight,
        fmt=fmt,
    )


def render_read_path(
    key: str,
    found_in: Optional[str],
    checked: list[str],
    skipped: list[str],
    memtable_keys: list[str],
    memtable_capacity: int,
    levels: dict[int, list[dict]],
    filename: str = "read_path",
    output_dir: str = ".",
    fmt: str = "png",
) -> str:
    """Render the read path showing where the key was found and what was checked."""
    highlight = list(checked)
    if found_in:
        highlight.append(found_in)

    title_found = found_in or "NOT FOUND"
    title = f"get({key}) → {title_found}  |  checked: {len(checked)}  skipped: {len(skipped)}"
    return render_lsm_state(
        memtable_keys=memtable_keys,
        memtable_capacity=memtable_capacity,
        wal_entries=[],
        levels=levels,
        title=title,
        filename=filename,
        output_dir=output_dir,
        highlight_key=key,
        highlight_path=highlight,
        fmt=fmt,
    )


def get_lsm_snapshot(db) -> dict:
    """Extract visualization data from an LSMTree instance."""
    memtable_keys = sorted(db.memtable.data.keys())
    wal_entries = [f"put({k})" for k in list(db.memtable.data.keys())[-10:]]

    levels: dict[int, list[dict]] = {}
    for sst in db.sstables:
        lvl = sst.level
        if lvl not in levels:
            levels[lvl] = []
        # Read first few keys for display
        try:
            items = []
            with open(sst.path, "r") as f:
                import json
                for i, line in enumerate(f):
                    if i >= 20:
                        break
                    entry = json.loads(line)
                    items.append(entry["key"])
        except:
            items = []

        levels[lvl].append({
            "name": os.path.basename(sst.path).replace(".json", ""),
            "keys": items,
            "min_key": sst.min_key,
            "max_key": sst.max_key,
            "num_keys": sst.num_keys,
        })

    return {
        "memtable_keys": memtable_keys,
        "memtable_capacity": db.memtable.capacity,
        "wal_entries": wal_entries,
        "levels": levels,
    }

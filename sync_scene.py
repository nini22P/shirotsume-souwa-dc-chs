from __future__ import annotations

import collections
import csv
import sys
from pathlib import Path


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save_csv(rows: list[dict[str, str]], path: Path) -> None:
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["file", "stmt", "context", "text", "translation"],
            lineterminator="\r\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def match_contiguous_blocks(
    scene_keys: list[tuple[str, str]],
    main_keys: list[tuple[str, str]],
    main_pos_by_key: dict[tuple[str, str], list[int]],
) -> list[int | None]:
    """Map each index in scene_keys to its matched index in main_keys via longest contiguous runs."""
    m = len(scene_keys)
    n = len(main_keys)
    matched: list[int | None] = [None] * m

    i = 0
    while i < m:
        candidates = main_pos_by_key.get(scene_keys[i])
        if not candidates:
            i += 1
            continue

        best_len = 0
        best_j = candidates[0]
        for j in candidates:
            k = 1
            while i + k < m and j + k < n and scene_keys[i + k] == main_keys[j + k]:
                k += 1
            if k > best_len:
                best_len = k
                best_j = j

        for k in range(best_len):
            matched[i + k] = best_j + k
        i += best_len

    return matched


def sync_scenes(csv_path: Path) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    rows = load_csv(csv_path)

    main_indices: list[int] = []
    main_keys: list[tuple[str, str]] = []
    main_pos_by_key: dict[tuple[str, str], list[int]] = collections.defaultdict(list)
    scene_indices_by_file: dict[str, list[int]] = collections.defaultdict(list)

    for idx, row in enumerate(rows):
        fn = row["file"]
        if fn.startswith("SCENE"):
            scene_indices_by_file[fn].append(idx)
        else:
            key = (row["text"], row["context"])
            pos = len(main_indices)
            main_indices.append(idx)
            main_keys.append(key)
            main_pos_by_key[key].append(pos)

    total_scene = 0
    updated = 0
    unchanged = 0
    unmatched = 0

    for scene_fn, s_indices in sorted(scene_indices_by_file.items()):
        total_scene += len(s_indices)
        s_keys = [(rows[idx]["text"], rows[idx]["context"]) for idx in s_indices]
        mapped_pos = match_contiguous_blocks(s_keys, main_keys, main_pos_by_key)

        for s_idx, m_pos in zip(s_indices, mapped_pos):
            if m_pos is None:
                unmatched += 1
                continue

            src_translation = rows[main_indices[m_pos]]["translation"]
            if rows[s_idx]["translation"] != src_translation:
                rows[s_idx]["translation"] = src_translation
                updated += 1
            else:
                unchanged += 1

    if updated > 0:
        save_csv(rows, csv_path)

    print(
        f"Synced {csv_path.name}: {total_scene} SCENE rows "
        f"(updated={updated}, unchanged={unchanged}, unmatched={unmatched})"
    )
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: python {Path(sys.argv[0]).name} <csv_path>", file=sys.stderr)
        return 1

    csv_path = Path(sys.argv[1])
    if not csv_path.is_file():
        print(f"Error: File not found: {csv_path}", file=sys.stderr)
        return 1

    return sync_scenes(csv_path)


if __name__ == "__main__":
    sys.exit(main())

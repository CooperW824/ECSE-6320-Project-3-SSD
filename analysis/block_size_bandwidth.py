#!/usr/bin/env python3
"""Plot FIO bandwidth versus block size for the four block-size workloads."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt


WORKLOADS = (
    ("read", "Sequential read", "read"),
    ("write", "Sequential write", "write"),
    ("randread", "Random read", "read"),
    ("randwrite", "Random write", "write"),
)
JOB_PATTERN = re.compile(r"^block_(\d+)k_(read|write|randread|randwrite)$")


def block_size_bandwidth(result_dir: Path, workload: str, direction: str):
    """Return sorted (block size in KiB, bandwidth in MiB/s) measurements."""
    points = []
    for path in sorted(result_dir.glob("block_*_*.json")):
        match = JOB_PATTERN.match(path.stem)
        if not match or match.group(2) != workload:
            continue
        try:
            with path.open(encoding="utf-8") as result_file:
                jobs: list[dict[str, Any]] = json.load(result_file).get("jobs", [])
            if not jobs:
                print(f"Skipping {path}: no FIO jobs found", file=sys.stderr)
                continue
            bandwidth_kib_s = jobs[0].get(direction, {}).get("bw")
            if bandwidth_kib_s is None:
                print(f"Skipping {path}: no {direction} bandwidth", file=sys.stderr)
                continue
            points.append((int(match.group(1)), float(bandwidth_kib_s) / 1024))
        except (OSError, json.JSONDecodeError, ValueError) as error:
            print(f"Skipping {path}: {error}", file=sys.stderr)
    return sorted(points)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "results_dirs", nargs="*", type=Path,
        help="FIO result directories (default: discover ./fio-results-*)",
    )
    parser.add_argument(
        "-o", "--output", type=Path,
        help="output image (default: analysis/plots/block_size_bandwidth.png)",
    )
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parent.parent
    result_dirs = [path.expanduser().resolve() for path in args.results_dirs]
    if not result_dirs:
        result_dirs = sorted(project_dir.glob("fio-results-*"))
    result_dirs = [path for path in result_dirs if path.is_dir()]
    if not result_dirs:
        parser.error("no FIO result directories found; pass one or more directories")

    output = args.output.expanduser().resolve() if args.output else (
        Path(__file__).resolve().parent / "plots" / "block_size_bandwidth.png"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
    for ax, (workload, title, direction) in zip(axes.flat, WORKLOADS):
        plotted = False
        for result_dir in result_dirs:
            points = block_size_bandwidth(result_dir, workload, direction)
            if not points:
                continue
            sizes, bandwidths = zip(*points)
            label = result_dir.name if len(result_dirs) > 1 else title
            ax.plot(sizes, bandwidths, marker="o", linewidth=2, label=label)
            plotted = True
        ax.set_title(title)
        ax.set_ylabel("Bandwidth (MiB/s)")
        ax.set_xscale("log", base=2)
        ax.grid(True, which="both", alpha=0.25)
        if plotted and len(result_dirs) > 1:
            ax.legend()
        elif not plotted:
            ax.text(0.5, 0.5, "No data", transform=ax.transAxes,
                    ha="center", va="center")

    for ax in axes[-1, :]:
        ax.set_xlabel("Block size (KiB)")
    sizes = sorted({
        size
        for result_dir in result_dirs
        for workload, _, direction in WORKLOADS
        for size, _ in block_size_bandwidth(result_dir, workload, direction)
    })
    if sizes:
        for ax in axes.flat:
            ax.set_xticks(sizes, [str(size) for size in sizes])
    fig.suptitle("FIO bandwidth by block size")
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved bandwidth plot to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

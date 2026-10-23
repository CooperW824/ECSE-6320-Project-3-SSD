#!/usr/bin/env python3
"""Plot latency and bandwidth metrics from an FIO results directory."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np


PERCENTILES = ("95.000000", "99.000000", "99.900000")
METRICS = ("Mean", "P95", "P99", "P99.9")
METRIC_COLORS = ("#4C78A8", "#F58518", "#54A24B", "#E45756")
LATENCY_NS_PER_US = 1000.0


def read_job(path: Path) -> dict[str, Any]:
    """Read the single job result stored in an FIO JSON file."""
    with path.open(encoding="utf-8") as result_file:
        document = json.load(result_file)
    jobs = document.get("jobs", [])
    if not jobs:
        raise ValueError(f"No FIO jobs found in {path}")
    return jobs[0]


def latency_us(job: dict[str, Any], direction: str) -> dict[str, float] | None:
    """Return completion-latency mean and percentiles in microseconds."""
    clat = job.get(direction, {}).get("clat_ns", {})
    mean_ns = clat.get("mean")
    percentile_ns = clat.get("percentile", {})
    if not mean_ns or not percentile_ns:
        return None
    values: dict[str, float] = {"Mean": float(mean_ns) / LATENCY_NS_PER_US}
    for label, key in zip(("P95", "P99", "P99.9"), PERCENTILES):
        if key not in percentile_ns:
            return None
        values[label] = float(percentile_ns[key]) / LATENCY_NS_PER_US
    return values


def active_directions(job: dict[str, Any]) -> list[str]:
    """Find read/write directions that actually performed I/O."""
    return [direction for direction in ("read", "write")
            if job.get(direction, {}).get("total_ios", 0) > 0]


def load_jobs(results_dir: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    jobs = {}
    for path in sorted(results_dir.glob("*.json")):
        if path.name == "summary.json":
            continue
        try:
            job = read_job(path)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            print(f"Skipping {path.name}: {error}", file=sys.stderr)
            continue
        jobs[job.get("jobname", path.stem)] = (path, job)
    return jobs


def require_job(jobs: dict[str, tuple[Path, dict[str, Any]]], name: str):
    if name not in jobs:
        raise ValueError(f"Required FIO result '{name}' was not found")
    return jobs[name][1]


def plot_latency_bars(ax, labels: list[str], values: list[dict[str, float] | None],
                      title: str, *, width: float = 0.18) -> None:
    positions = np.arange(len(labels))
    offsets = (np.arange(len(METRICS)) - (len(METRICS) - 1) / 2) * width
    for index, (metric, color) in enumerate(zip(METRICS, METRIC_COLORS)):
        heights = [value[metric] if value is not None else 0 for value in values]
        bars = ax.bar(positions + offsets[index], heights, width,
                      label=metric, color=color)
        for bar, value in zip(bars, values):
            if value is not None:
                ax.annotate(f"{value[metric]:.1f}",
                            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                            xytext=(0, 3), textcoords="offset points",
                            ha="center", va="bottom", fontsize=7, rotation=0)
    ax.set_title(title)
    ax.set_ylabel("Completion latency (µs)")
    ax.set_xticks(positions, labels)
    ax.grid(axis="y", alpha=0.25)


def save_baseline(jobs, output_dir: Path) -> None:
    specifications = [
        ("baseline_randread_4k", "baseline_randwrite_4k", "4 KiB random I/O",
         "baseline_4k_random_latency.png"),
        ("baseline_seqread_128k", "baseline_seqwrite_128k", "128 KiB sequential I/O",
         "baseline_128k_sequential_latency.png"),
    ]
    for read_name, write_name, title, filename in specifications:
        fig, ax = plt.subplots(figsize=(8, 5))
        labels, values = [], []
        for name, direction, label in ((read_name, "read", "Read"),
                                       (write_name, "write", "Write")):
            data = latency_us(require_job(jobs, name), direction)
            labels.append(label)
            values.append(data)
        plot_latency_bars(ax, labels, values, title)
        ax.legend(title="Latency statistic")
        fig.tight_layout()
        fig.savefig(output_dir / filename, dpi=180, bbox_inches="tight")
        plt.close(fig)


def plot_block_size(jobs, output_dir: Path, workload: str, title: str) -> None:
    pattern = re.compile(rf"^block_(\d+k)_{workload}$")
    points = []
    for name, (_, job) in jobs.items():
        match = pattern.match(name)
        if match:
            size = int(match.group(1)[:-1])
            direction = "read" if workload.endswith("read") else "write"
            values = latency_us(job, direction)
            if values:
                points.append((size, values))
    if not points:
        raise ValueError(f"No block-size results found for {workload}")
    points.sort(key=lambda item: item[0])

    fig, ax = plt.subplots(figsize=(8, 5))
    sizes = [item[0] for item in points]
    for metric, color in zip(METRICS, METRIC_COLORS):
        ax.plot(sizes, [item[1][metric] for item in points], marker="o",
                linewidth=2, label=metric, color=color)
    ax.set_xscale("log", base=2)
    ax.set_xticks(sizes, [f"{size} KiB" for size in sizes])
    ax.set_xlabel("Block size")
    ax.set_ylabel("Completion latency (µs)")
    ax.set_title(f"{title} latency by block size")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(title="Latency statistic")
    fig.tight_layout()
    fig.savefig(output_dir / f"block_size_{workload}.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)


def save_mix(jobs, output_dir: Path) -> None:
    specifications = [
        ("mix_read100", "read", "100% read"),
        ("mix_write100", "write", "100% write"),
        ("mix_read50_randrw", "read", "50% read"),
        ("mix_read50_randrw", "write", "50% write"),
        ("mix_read70_randrw", "read", "70% read"),
        ("mix_read70_randrw", "write", "30% write"),
    ]
    values = [latency_us(require_job(jobs, name), direction)
              for name, direction, _ in specifications]
    labels = [label for _, _, label in specifications]
    fig, ax = plt.subplots(figsize=(12, 6))
    plot_latency_bars(ax, labels, values, "Latency across read/write mixes", width=0.18)
    ax.tick_params(axis="x", labelrotation=25)
    ax.legend(title="Latency statistic", ncols=2)
    fig.tight_layout()
    fig.savefig(output_dir / "read_write_mix_latency.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)


def save_queue_depth(jobs, output_dir: Path) -> None:
    records = []
    pattern = re.compile(r"^qd_(\d+)_(randread|randwrite)_4k$")
    for name, (_, job) in jobs.items():
        match = pattern.match(name)
        if not match:
            continue
        depth, workload = int(match.group(1)), match.group(2)
        direction = "read" if workload == "randread" else "write"
        latency = latency_us(job, direction)
        metrics = job.get(direction, {})
        if latency and metrics.get("bw"):
            records.append((depth, direction, latency, float(metrics["bw"]) / 1024))
    if not records:
        raise ValueError("No queue-depth results found")

    fig, ax = plt.subplots(figsize=(9, 6))
    styles = {("read", "Mean"): ("#4C78A8", "o"),
              ("read", "P99"): ("#F58518", "o"),
              ("write", "Mean"): ("#4C78A8", "s"),
              ("write", "P99"): ("#F58518", "s")}
    for direction in ("read", "write"):
        subset = sorted((record for record in records if record[1] == direction),
                        key=lambda item: item[0])
        if not subset:
            continue
        color, marker = styles[(direction, "Mean")]
        latencies = [record[2]["Mean"] for record in subset]
        bandwidths = [record[3] for record in subset]
        ax.plot(latencies, bandwidths, color=color, marker=marker,
                linestyle="--",
                label=f"{direction.title()} {"Mean"}")
        for record, x, y in zip(subset, latencies, bandwidths):
            ax.annotate(f"QD {record[0]}", (x, y), xytext=(4, 4),
                        textcoords="offset points", fontsize=8, rotation=45)
    ax.set_xlabel("Completion latency (µs)")
    ax.set_ylabel("Bandwidth (MiB/s)")
    ax.set_title("4 KiB random I/O: bandwidth vs. latency by queue depth")
    ax.grid(alpha=0.25)
    ax.legend(title="Workload / latency statistic")
    fig.tight_layout()
    fig.savefig(output_dir / "queue_depth_bandwidth_vs_latency.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)


def save_compressibility(jobs, output_dir: Path) -> None:
    specifications = [
        ("pattern_incompressible_randwrite", "Incompressible"),
        ("pattern_compressible_randwrite", "90% compressible"),
    ]
    values = [latency_us(require_job(jobs, name), "write")
              for name, _ in specifications]
    labels = [label for _, label in specifications]
    fig, ax = plt.subplots(figsize=(8, 5))
    plot_latency_bars(ax, labels, values,
                      "Random-write latency by data compressibility", width=0.18)
    ax.legend(title="Latency statistic")
    fig.tight_layout()
    fig.savefig(output_dir / "data_compressibility_latency.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_dir", type=Path,
                        help="directory containing FIO JSON result files")
    args = parser.parse_args()
    results_dir = args.results_dir.expanduser().resolve()
    if not results_dir.is_dir():
        parser.error(f"results directory does not exist: {results_dir}")
    output_dir = results_dir / "plots"
    output_dir.mkdir(exist_ok=True)

    try:
        jobs = load_jobs(results_dir)
        save_baseline(jobs, output_dir)
        for workload, title in (("read", "Sequential read"),
                                ("write", "Sequential write"),
                                ("randread", "Random read"),
                                ("randwrite", "Random write")):
            plot_block_size(jobs, output_dir, workload, title)
        save_mix(jobs, output_dir)
        save_queue_depth(jobs, output_dir)
        save_compressibility(jobs, output_dir)
    except (OSError, ValueError, KeyError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Saved 9 plots to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

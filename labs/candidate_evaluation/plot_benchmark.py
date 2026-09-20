"""Plot an existing explicit benchmark directory (optional matplotlib dependency)."""

import argparse
import gzip
import json
from pathlib import Path


def plot(directory: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    rows = summary["results"]
    groups = list(dict.fromkeys((row["fixture"], row["budget_unit"]) for row in rows))
    colors = {"equal": "#777777", "round_robin": "#b89b63", "variance": "#7c6ba6",
              "epsilon_greedy": "#70a6a6", "ocba": "#2368a2", "cost_ocba": "#be5a42"}
    figure, axes = plt.subplots((len(groups) + 2) // 3, 3, figsize=(13, 3.1 * ((len(groups) + 2) // 3)), squeeze=False)
    for ax, (fixture, unit) in zip(axes.flat, groups):
        for policy, color in colors.items():
            selected = sorted((r for r in rows if (r["fixture"], r["budget_unit"], r["policy"]) ==
                               (fixture, unit, policy)), key=lambda r: r["budget"])
            if not selected:
                continue
            x = [r["budget"] for r in selected]
            y = [r["pcs"] for r in selected]
            ax.plot(x, y, marker="o", markersize=3, label=policy, color=color, linewidth=1.3)
            ax.fill_between(x, [r["pcs_ci95_low"] for r in selected],
                            [r["pcs_ci95_high"] for r in selected], color=color, alpha=.055)
        ax.set_title(f"{fixture}\n{unit} budget", fontsize=9)
        ax.set_ylim(0, 1.04)
        ax.set_xscale("log")
        ax.set_ylabel("PCS")
        ax.grid(alpha=.15)
    for ax in list(axes.flat)[len(groups):]:
        ax.set_visible(False)
    handles, labels = list(axes.flat)[-2].get_legend_handles_labels()
    # Find the cost panel, which includes all six strategies.
    for ax in axes.flat:
        h, lab = ax.get_legend_handles_labels()
        if len(lab) > len(labels):
            handles, labels = h, lab
    figure.legend(handles, labels, loc="lower center", ncol=6, fontsize=9)
    figure.suptitle(f"Independent Monte Carlo PCS ({summary['repetitions']} trials per condition)\n"
                   "Shading: Wilson 95% intervals; artificial noise and costs", fontsize=12)
    figure.tight_layout(rect=(0, .035, 1, .94))
    figure.savefig(directory / "pcs.png", dpi=170)
    figure.savefig(directory / "pcs.svg")
    plt.close(figure)

    target = ("heterogeneous_variance", "ocba", max(summary["sample_budgets"]))
    with gzip.open(directory / "trials.jsonl.gz", "rt", encoding="utf-8") as file:
        trial = next(row for line in file if (row := json.loads(line))["repetition"] == 0
                     and (row["fixture"], row["policy"], row["budget"]) == target)
    figure, (counts_ax, means_ax) = plt.subplots(1, 2, figsize=(12, 4))
    count = len(trial["final_estimates"])
    counts = [0] * count
    means = [None] * count
    count_paths = [[] for _ in range(count)]
    mean_paths = [[] for _ in range(count)]
    for event in trial["trace"]:
        counts[event[0]] += 1
        means[event[0]] = event[5]
        for i in range(count):
            count_paths[i].append(counts[i])
            mean_paths[i].append(float("nan") if means[i] is None else means[i])
    for i in range(count):
        label = trial["final_estimates"][i]["candidate_id"]
        counts_ax.plot(count_paths[i], label=label)
        means_ax.plot(mean_paths[i], linewidth=.8)
    counts_ax.set(xlabel="Total attempted observations", ylabel="Cumulative attempts per candidate")
    means_ax.set(xlabel="Total attempted observations", ylabel="Estimated response mean")
    figure.suptitle(f"One replayable trajectory: {target[0]}, {target[1]}, repetition 0\n"
                   "Illustrative trace; aggregate PCS uses all independent repetitions", fontsize=11)
    counts_ax.legend(ncol=2, fontsize=7)
    for ax in (counts_ax, means_ax):
        ax.grid(alpha=.15)
    figure.tight_layout()
    figure.savefig(directory / "allocation_trace.png", dpi=170)
    figure.savefig(directory / "allocation_trace.svg")
    plt.close(figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    plot(parser.parse_args().directory)

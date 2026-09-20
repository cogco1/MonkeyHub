"""Plot an existing explicit benchmark directory (optional matplotlib dependency)."""

import argparse
import gzip
import json
from pathlib import Path


def plot(directory: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import NullLocator

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
        budgets = sorted({r["budget"] for r in rows if (r["fixture"], r["budget_unit"]) == (fixture, unit)})
        ax.set_xticks(budgets, [f"{budget:,}" for budget in budgets])
        ax.xaxis.set_minor_locator(NullLocator())
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
    if not any((row["fixture"], row["policy"], row["budget"]) == target for row in rows):
        selected = next((row for row in rows if row["policy"] == "ocba"), rows[0])
        target = (selected["fixture"], selected["policy"], selected["budget"])
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
        attempts = range(1, len(trial["trace"]) + 1)
        counts_ax.plot(attempts, count_paths[i], label=label)
        means_ax.plot(attempts, mean_paths[i], linewidth=.8)
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


def plot_paired(directory: Path) -> None:
    """Plot retained paired PCS differences, including simultaneous intervals."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    summary = json.loads((directory / "paired_summary.json").read_text(encoding="utf-8"))
    rows = sorted(summary["results"], key=lambda row: (row["fixture"], row["policy"] == "cost_ocba"))
    names = {"heterogeneous_cost": "异成本候选", "monkeyhub_fixed_massing": "固定体量候选"}
    policies = {"ocba": "经典 OCBA", "cost_ocba": "成本 OCBA"}
    with plt.rc_context({"font.family": "Microsoft YaHei", "svg.fonttype": "none"}):
        figure, ax = plt.subplots(figsize=(10, max(4.7, .75 * len(rows) + 1.8)))
        for index, row in enumerate(rows):
            center = 100 * row["delta_pcs"]
            low, high = (100 * row[field] for field in
                         ("delta_pcs_familywise95_low", "delta_pcs_familywise95_high"))
            ax.errorbar(center, index, xerr=[[center - low], [high - center]],
                        fmt="o", color="#171717", ecolor="#777777", capsize=5, markersize=5)
            ax.annotate(f"{center:+.2f}", (center, index), xytext=(0, -19),
                        textcoords="offset points", ha="center", fontsize=10)
        ax.set_yticks(range(len(rows)), [f"{names.get(row['fixture'], row['fixture'])} / {policies[row['policy']]}" for row in rows])
        ax.set_ylim(len(rows) - .15, -.7)
        ax.axvline(0, color="#999999", linestyle="--", linewidth=1)
        ax.set_xlabel("相对均分的 PCS 差值（百分点）", labelpad=12)
        ax.grid(axis="x", color="#e6e6e6")
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.tick_params(axis="y", length=0, pad=12)
        figure.suptitle("同成本比较：点估计与不确定性", fontsize=17, x=.045, ha="left")
        budgets = ", ".join(f"{unit} {budget:,}" for unit, budget in sorted({(r["budget_unit"], r["budget"]) for r in rows}))
        counts = ", ".join(str(n) for n in sorted({r["paired_trials"] for r in rows}))
        figure.text(.045, .015,
                    f"预算 {budgets} / 每组 {counts} 次配对重复 / {len(rows)} 项差值同时 95% 区间（保守 exact 方法）\n"
                    "人工噪声与费用；区间包含零不等于两策略等效。", fontsize=9, color="#555555")
        figure.tight_layout(rect=(0, .13, 1, .92))
        figure.savefig(directory / "paired_pcs.svg")
        svg = directory / "paired_pcs.svg"
        svg.write_text("\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()) + "\n",
                       encoding="utf-8", newline="\n")
        figure.savefig(directory / "paired_pcs.png", dpi=160)
        plt.close(figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--paired", action="store_true", help="plot paired_summary.json instead of summary.json")
    args = parser.parse_args()
    (plot_paired if args.paired else plot)(args.directory)

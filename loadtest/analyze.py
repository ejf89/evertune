"""Analysis over the committed JSONL records — separate from the harness on
purpose (PLAN.md's "Raw data" section: the harness's only job is writing
data; this is what turns it into FINDINGS.md's numbers and charts).

Every number this script prints or plots comes from loadtest/results/*.jsonl
— nothing here is illustrative. Run from repo root:
    python -m loadtest.analyze
"""

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from loadtest.runner import percentile

RESULTS_DIR = Path("loadtest/results")
CHARTS_DIR = RESULTS_DIR / "charts"

# Verified against Google Cloud's official Vertex AI pricing page
# (cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing,
# fetched 2026-08-17 — the URL itself moved under a "Gemini Enterprise Agent
# Platform" rebrand since PLAN.md's Phase 1 SDK research; this is the live
# successor to the old vertex-ai/generative-ai/pricing page) — not an
# aggregator estimate. Gemini 2.5 Flash, text/image/video input:
#   Input:  $0.30 / 1M tokens
#   Output: $2.50 / 1M tokens — the page's own row label is literally
#     "Text output (response and reasoning)": Google prices thinking tokens
#     at the *same* rate as visible output, no separate reasoning price.
#     That's convenient for us specifically: GeminiVertex.output_tokens is
#     already defined as visible+thinking combined (llm/llm.py), so it maps
#     directly onto this single rate with no further adjustment needed.
GEMINI_FLASH_INPUT_USD_PER_M_TOKENS = 0.30
GEMINI_FLASH_OUTPUT_USD_PER_M_TOKENS = 2.50  # covers visible + thinking

# Palette per the dataviz skill's reference palette (light surface, fixed
# categorical order) — these are static PNGs embedded in GitHub-rendered
# markdown, so one light-mode rendering, not a theme-switching HTML chart.
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
SLOT_1_BLUE = "#2a78d6"
SLOT_2_ORANGE = "#eb6834"


def _style_axes(ax):
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(BASELINE)
    ax.tick_params(colors=INK_MUTED, labelsize=9)
    ax.yaxis.grid(True, color=GRIDLINE, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    ax.title.set_color(INK_PRIMARY)
    ax.xaxis.label.set_color(INK_SECONDARY)
    ax.yaxis.label.set_color(INK_SECONDARY)


def load(name):
    path = RESULTS_DIR / f"{name}.jsonl"
    if not path.exists():
        print(f"[analyze] {path} not found — skipping")
        return []
    with open(path) as f:
        return [json.loads(line) for line in f]


def analyze_concurrency_sweep():
    records = load("concurrency_sweep")
    if not records:
        return
    by_level = defaultdict(list)
    for r in records:
        by_level[r["concurrency_level"]].append(r)

    levels = sorted(by_level)
    p50s, p95s, error_rates = [], [], []
    print("\n=== concurrency_sweep ===")
    for level in levels:
        rs = by_level[level]
        latencies = sorted(r["latency_ms"] for r in rs if r["latency_ms"] is not None)
        errors = [r for r in rs if r["error_class"]]
        p50 = percentile(latencies, 0.5)
        p95 = percentile(latencies, 0.95)
        p50s.append(p50)
        p95s.append(p95)
        error_rates.append(len(errors) / len(rs))
        print(f"  concurrency={level:>3}  n={len(rs):>3}  errors={len(errors)}  "
              f"p50={p50:.0f}ms  p95={p95:.0f}ms")

    fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
    ax.plot(levels, p50s, color=SLOT_1_BLUE, linewidth=2, marker="o", markersize=6, label="p50")
    ax.plot(levels, p95s, color=SLOT_2_ORANGE, linewidth=2, marker="o", markersize=6, label="p95")
    ax.set_xlabel("Concurrency level")
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Latency vs. concurrency (evertune-tests, us-central1)")
    ax.set_xticks(levels)
    ax.legend(frameon=False, labelcolor=INK_SECONDARY, fontsize=9)
    _style_axes(ax)
    fig.tight_layout()
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(CHARTS_DIR / "concurrency_latency.png", facecolor=SURFACE)
    plt.close(fig)
    print(f"  wrote {CHARTS_DIR / 'concurrency_latency.png'}")


def analyze_thinking_budget_sweep():
    records = load("thinking_budget_sweep")
    if not records:
        return

    # The four sweep levels (exclude the empty-response probe subset, which
    # is identified by its distinct max_output_tokens_setting=16).
    sweep = [r for r in records if r["max_output_tokens_setting"] != 16]
    probe = [r for r in records if r["max_output_tokens_setting"] == 16]

    by_budget = defaultdict(list)
    for r in sweep:
        by_budget[r["thinking_budget_setting"]].append(r)

    label_order = [0, 128, None, 8192]
    labels = {0: "disabled (0)", 128: "low (128)", None: "default", 8192: "high (8192)"}

    print("\n=== thinking_budget_sweep ===")
    visible_means, thinking_means, latency_means = [], [], []
    for budget in label_order:
        rs = by_budget.get(budget, [])
        if not rs:
            visible_means.append(0)
            thinking_means.append(0)
            latency_means.append(0)
            continue
        visible = statistics.mean(r["visible_output_tokens"] for r in rs)
        thinking = statistics.mean(r["thinking_tokens"] for r in rs)
        latency = statistics.mean(r["latency_ms"] for r in rs if r["latency_ms"])
        visible_means.append(visible)
        thinking_means.append(thinking)
        latency_means.append(latency)
        print(f"  {labels[budget]:<14} n={len(rs):>2}  "
              f"avg visible_output={visible:.0f}  avg thinking={thinking:.0f}  "
              f"avg latency={latency:.0f}ms")

    if probe:
        empty = [r for r in probe if not r["error_class"] and r["answer"] == ""]
        reasons = sorted(set(r["finish_reason"] for r in probe if r["finish_reason"]))
        print(f"  empty-response probe: {len(empty)}/{len(probe)} came back "
              f"finish_reason={reasons} with empty visible content")

    x = range(len(label_order))
    fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
    bar_labels = [labels[b] for b in label_order]
    # Stacked bars: visible (slot 1) + thinking (slot 2), 2px surface gap
    # between segments per the mark spec.
    ax.bar(x, visible_means, color=SLOT_1_BLUE, width=0.55, label="visible output tokens")
    ax.bar(
        x, thinking_means, bottom=visible_means, color=SLOT_2_ORANGE, width=0.55,
        label="thinking tokens", edgecolor=SURFACE, linewidth=2,
    )
    ax.set_xticks(list(x))
    ax.set_xticklabels(bar_labels)
    ax.set_ylabel("Tokens (mean per request)")
    ax.set_title("Token spend by thinking-budget setting")
    ax.legend(frameon=False, labelcolor=INK_SECONDARY, fontsize=9)
    _style_axes(ax)
    fig.tight_layout()
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(CHARTS_DIR / "thinking_budget_tokens.png", facecolor=SURFACE)
    plt.close(fig)
    print(f"  wrote {CHARTS_DIR / 'thinking_budget_tokens.png'}")


def analyze_output_variance():
    records = load("output_variance")
    if not records:
        return

    by_temp = defaultdict(list)
    for r in records:
        by_temp[r["temperature"]].append(r)

    candidate_brands = ("Nike", "Brooks", "Hoka", "Asics", "New Balance", "Saucony", "Adidas")

    print("\n=== output_variance ===")
    temps = sorted(by_temp)
    mention_rates = {}
    for temp in temps:
        rs = [r for r in by_temp[temp] if not r["error_class"]]
        distinct = len(set(r["answer"] for r in rs))
        counts = Counter()
        for r in rs:
            lowered = r["answer"].lower()
            for brand in candidate_brands:
                if brand.lower() in lowered:
                    counts[brand] += 1
        mention_rates[temp] = {b: counts.get(b, 0) / len(rs) for b in candidate_brands}
        print(f"  temperature={temp}  n={len(rs)}  distinct_answers={distinct}/{len(rs)}")
        print(f"    mention rates: {dict(mention_rates[temp])}")

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    width = 0.35
    x = range(len(candidate_brands))
    colors = [SLOT_1_BLUE, SLOT_2_ORANGE]
    for i, temp in enumerate(temps):
        offsets = [xi + (i - 0.5) * width for xi in x]
        rates = [mention_rates[temp][b] * 100 for b in candidate_brands]
        ax.bar(offsets, rates, width=width, color=colors[i % len(colors)],
               label=f"temperature={temp}")
    ax.set_xticks(list(x))
    ax.set_xticklabels(candidate_brands, rotation=20, ha="right")
    ax.set_ylabel("Mention rate (%)")
    ax.set_title('Brand mention rate across N=100 identical calls\n"best running shoes for marathon training"')
    ax.legend(frameon=False, labelcolor=INK_SECONDARY, fontsize=9)
    _style_axes(ax)
    fig.tight_layout()
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(CHARTS_DIR / "output_variance_mentions.png", facecolor=SURFACE)
    plt.close(fig)
    print(f"  wrote {CHARTS_DIR / 'output_variance_mentions.png'}")


def _cost_usd(input_tokens, output_tokens):
    """output_tokens is visible+thinking combined (llm/llm.py's
    SimpleResponse.output_tokens) — that's exactly what Google's "Text
    output (response and reasoning)" rate bills, so no further split is
    needed here."""
    return (
        input_tokens * GEMINI_FLASH_INPUT_USD_PER_M_TOKENS / 1_000_000
        + output_tokens * GEMINI_FLASH_OUTPUT_USD_PER_M_TOKENS / 1_000_000
    )


def analyze_cost_model():
    """Converts the thinking-budget sweep's token data into real dollars —
    the one number FINDINGS.md previously stated it couldn't produce
    without a verified pricing source. Deliberately reuses that sweep's
    data rather than a separate run: it's the one experiment that already
    varies thinking spend in a controlled way, which is exactly the axis
    that matters for this cost question."""
    records = load("thinking_budget_sweep")
    if not records:
        return

    sweep = [r for r in records if r["max_output_tokens_setting"] != 16]
    by_budget = defaultdict(list)
    for r in sweep:
        by_budget[r["thinking_budget_setting"]].append(r)

    label_order = [0, 128, None, 8192]
    labels = {0: "disabled (0)", 128: "low (128)", None: "default", 8192: "high (8192)"}

    print("\n=== cost_model ($/1K requests, Vertex list price, fetched 2026-08-17) ===")
    for budget in label_order:
        rs = by_budget.get(budget, [])
        if not rs:
            continue
        mean_input = statistics.mean(r["input_tokens"] for r in rs)
        mean_output = statistics.mean(r["visible_output_tokens"] + r["thinking_tokens"] for r in rs)
        cost_per_request = _cost_usd(mean_input, mean_output)
        print(f"  {labels[budget]:<14} avg input={mean_input:.0f}  avg output(visible+thinking)={mean_output:.0f}  "
              f"${cost_per_request * 1000:.3f} / 1K requests")

    disabled_cost = _cost_usd(
        statistics.mean(r["input_tokens"] for r in by_budget[0]),
        statistics.mean(r["visible_output_tokens"] + r["thinking_tokens"] for r in by_budget[0]),
    )
    default_cost = _cost_usd(
        statistics.mean(r["input_tokens"] for r in by_budget[None]),
        statistics.mean(r["visible_output_tokens"] + r["thinking_tokens"] for r in by_budget[None]),
    )
    print(f"  disabled -> default multiplies cost {default_cost / disabled_cost:.1f}x "
          f"for the ~2x visible-output improvement FINDINGS.md reports")


def analyze_escalation_test():
    """The second same-day follow-up: escalation past the concurrency
    sweep's 150 stopping point, hunting for a real failure. Plots the
    sweep's 1-150 range and the escalation's 250-2000 range on one figure
    so the flat-then-climbing shape is visible in a single picture, and
    fits a line through the escalation's points to characterize the
    climb quantitatively rather than just eyeballing the chart."""
    sweep = load("concurrency_sweep")
    escalation = load("escalation_test")
    if not escalation:
        return

    def p50_by_level(records):
        by_level = defaultdict(list)
        for r in records:
            by_level[r["concurrency_level"]].append(r["latency_ms"])
        out = {}
        for level, lats in by_level.items():
            lats = sorted(l for l in lats if l is not None)
            out[level] = percentile(lats, 0.5)
        return out

    sweep_p50 = p50_by_level(sweep)
    esc_p50 = p50_by_level(escalation)

    print("\n=== escalation_test (past the sweep's 150 ceiling) ===")
    esc_levels = sorted(esc_p50)
    for level in esc_levels:
        errors = [r for r in escalation if r["concurrency_level"] == level and r["error_class"]]
        print(f"  concurrency={level:>5}  n={sum(1 for r in escalation if r['concurrency_level'] == level):>5}  "
              f"errors={len(errors)}  p50={esc_p50[level]:.0f}ms")

    # Linear fit through the escalation's p50s only (the flat 1-150 range
    # would bias the slope toward zero if included) — characterizes the
    # climb, not just the presence of one.
    xs = esc_levels
    ys = [esc_p50[l] for l in xs]
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    slope = num / den if den else 0
    r2 = (num ** 2) / (den * sum((y - mean_y) ** 2 for y in ys)) if den else 0
    print(f"  linear fit above 150: p50 ~= {slope:.1f}ms * concurrency + {mean_y - slope * mean_x:.0f}  "
          f"(r^2={r2:.3f})")
    print(f"  implied sustained throughput ~= {1000 / slope:.1f} req/s (~{1000 / slope * 60:.0f} req/min)")

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    all_levels = sorted(sweep_p50) + esc_levels
    all_p50 = [sweep_p50[l] for l in sorted(sweep_p50)] + [esc_p50[l] for l in esc_levels]
    ax.plot(all_levels, all_p50, color=SLOT_1_BLUE, linewidth=2, marker="o", markersize=5)
    fit_xs = [min(esc_levels), max(esc_levels)]
    fit_ys = [slope * x + (mean_y - slope * mean_x) for x in fit_xs]
    ax.plot(fit_xs, fit_ys, color=SLOT_2_ORANGE, linewidth=1.5, linestyle="--",
            label=f"linear fit (r²={r2:.2f})")
    ax.axvline(150, color=BASELINE, linewidth=1, linestyle=":")
    ax.set_xlabel("Concurrency level (note: non-linear spacing)")
    ax.set_ylabel("p50 latency (ms)")
    ax.set_title("Latency vs. concurrency, full range (evertune-tests, us-central1)")
    ax.set_xticks(all_levels)
    ax.set_xticklabels([str(l) for l in all_levels], rotation=45, ha="right", fontsize=7)
    ax.legend(frameon=False, labelcolor=INK_SECONDARY, fontsize=9)
    _style_axes(ax)
    fig.tight_layout()
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(CHARTS_DIR / "concurrency_latency_full_range.png", facecolor=SURFACE)
    plt.close(fig)
    print(f"  wrote {CHARTS_DIR / 'concurrency_latency_full_range.png'}")


def analyze_burst_test():
    """Splits burst_test.jsonl into cycles by record order (see
    experiments/burst_test.py's module docstring for why there's no
    explicit cycle field: asyncio.gather preserves task order, and each
    cycle writes exactly CHUNK_SIZE records before the next cycle starts)."""
    records = load("burst_test")
    if not records:
        return

    workload_len = 12  # len(loadtest.workload.WORKLOAD) — kept in sync manually,
    repeats = 3         # matching experiments/burst_test.py's REPEATS constant
    chunk = workload_len * repeats

    print("\n=== burst_test ===")
    for i in range(0, len(records), chunk):
        cycle_records = records[i:i + chunk]
        cycle_num = i // chunk + 1
        errors = [r for r in cycle_records if r["error_class"]]
        latencies = sorted(r["latency_ms"] for r in cycle_records if r["latency_ms"] is not None)
        p50 = percentile(latencies, 0.5)
        p95 = percentile(latencies, 0.95)
        label = "cold start" if cycle_num == 1 else "warm"
        print(f"  cycle {cycle_num} ({label})  n={len(cycle_records)}  errors={len(errors)}  "
              f"p50={p50:.0f}ms  p95={p95:.0f}ms")


def analyze_retry_amplification():
    records = load("retry_amplification")
    if not records:
        return

    by_setting = defaultdict(list)
    for r in records:
        by_setting[r["retry_attempts_setting"]].append(r)

    print("\n=== retry_amplification ===")
    for setting in sorted(by_setting, key=lambda s: (s is None, s)):
        rs = by_setting[setting]
        errors = [r for r in rs if r["error_class"]]
        total_tokens = sum(r["total_tokens"] for r in rs)
        latencies = [r["latency_ms"] for r in rs if r["latency_ms"] is not None]
        label = "retries_off" if setting == 1 else "retries_on"
        print(f"  {label} (retry_attempts={setting})  n={len(rs)}  errors={len(errors)}  "
              f"total_tokens={total_tokens}  mean_latency={statistics.mean(latencies):.0f}ms")


def analyze_multi_process_test():
    """Three independent processes (loadtest/experiments/multi_process_test.py),
    each writing loadtest/results/multi_process_{tag}.jsonl. Compares each
    process's own p50 against what the single-process escalation fit would
    predict for (a) that process's own level alone and (b) the combined
    total across all processes — the two competing hypotheses this
    experiment was designed to separate. See FINDINGS.md's "Is the latency
    wall real, or one process's own bottleneck?" for the interpretation.
    """
    tags = sorted(p.stem.replace("multi_process_", "")
                  for p in RESULTS_DIR.glob("multi_process_*.jsonl"))
    if not tags:
        return

    print("\n=== multi_process_test (independent processes, simultaneous) ===")
    all_levels = set()
    for tag in tags:
        records = load(f"multi_process_{tag}")
        errors = [r for r in records if r["error_class"]]
        latencies = sorted(r["latency_ms"] for r in records if r["latency_ms"] is not None)
        p50 = percentile(latencies, 0.5)
        p95 = percentile(latencies, 0.95)
        levels = set(r["concurrency_level"] for r in records)
        all_levels |= levels
        print(f"  process={tag}  n={len(records)}  errors={len(errors)}"
              + (f"  classes={sorted(set(r['error_class'] for r in errors))}" if errors else "")
              + f"  p50={p50:.0f}ms  p95={p95:.0f}ms")

    if len(all_levels) == 1:
        level = all_levels.pop()
        lone_predicted = 42.0 * level - 665
        combined_predicted = 42.0 * (level * len(tags)) - 665
        print(f"  lone-process prediction at level={level}: {lone_predicted:.0f}ms")
        print(f"  lone-process prediction at combined={level * len(tags)}: {combined_predicted:.0f}ms")


if __name__ == "__main__":
    analyze_concurrency_sweep()
    analyze_thinking_budget_sweep()
    analyze_cost_model()
    analyze_output_variance()
    analyze_retry_amplification()
    analyze_multi_process_test()
    analyze_burst_test()
    analyze_escalation_test()

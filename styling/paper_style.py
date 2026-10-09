#!/usr/bin/env python3
"""Shared figure style for every ARMHiT plot.

A copy of DRAMHiT's scripts/eurosys_2026/paper_style.py with ARMHiT's series
(the prefetch flavours) in PALETTE_ORDER; the helpers are unchanged apart
from axis_labels(), which knows the sweeps used here.

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))
    import paper_style as ps

See PLOTTING.md next to this file for the guide that goes with it.

The one rule that matters across directories: the palette is always built at
``len(PALETTE_ORDER)`` colours and a series takes the slot its *canonical*
name has in that list. That is why a prefetch flavour is the same shade in
every figure even when a figure plots only some of them. Never rebuild the
palette at the size of the subset you happen to be drawing -- you will
silently recolour every other figure's series.
"""

import shutil
from collections import Counter

import matplotlib as mpl
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.lines import Line2D

# =============================================================================
# NAMES
# =============================================================================

# Plot order, which is also the palette order (rocket runs dark -> light).
# Everything ARMHiT compares lives here, whether or not a given figure draws
# it, so the colours are stable across scripts/ subdirectories. Appending is
# safe; reordering recolours every figure.
PALETTE_ORDER = ["DOUBLE", "DOUBLE_L2", "L1", "L2", "L3", "NTA", "L2STRM",
                 "L3STRM", "NONE"]

# What a collector might call a series -> its canonical name above.
ALIASES = {
    "double": "DOUBLE",
    "double_l2": "DOUBLE_L2",
    "none": "NONE",
}

# Canonical name -> the name shown in legends. Only the legend text changes;
# every lookup stays on the canonical names.
DISPLAY_NAMES = {
    "DOUBLE": "double (L3+L1)",
    "DOUBLE_L2": "double (L2+L1)",
    "L1": "L1",
    "L2": "L2",
    "L3": "L3",
    "NTA": "L1 strm",
    "L2STRM": "L2 strm",
    "L3STRM": "L3 strm",
    "NONE": "none",
}

# Runs that are a variant of one of the series above rather than a series of
# their own: same colour as their base, told apart by dash pattern and marker.
#   "DOUBLE (branched)": {"base": "DOUBLE", "linestyle": "--", "marker": "s"},
VARIANT_STYLE = {}


def canonical(name):
    """Collector's name for a series -> the name that owns a palette slot."""
    return ALIASES.get(name, name)


def display_name(name):
    return DISPLAY_NAMES.get(canonical(name), name)


def order_series(names):
    """Sort a set of series into PALETTE_ORDER; unknown names go last, sorted.

    Returns the names as given (not canonicalised), so the caller can keep
    indexing its own data with them.
    """
    def key(name):
        c = canonical(name)
        if c in PALETTE_ORDER:
            return (0, PALETTE_ORDER.index(c), "")
        return (1, 0, str(name))

    return sorted(names, key=key)


# =============================================================================
# STYLE
# =============================================================================


def configure_style():
    """Configures the base matplotlib fonts and seaborn style."""
    # usetex needs a real latex + dvipng on PATH; fall back to matplotlib's
    # own mathtext when they are missing so plotting still works.
    usetex = bool(shutil.which("latex") and shutil.which("dvipng"))
    if not usetex:
        print("[!] latex/dvipng not found, rendering without usetex")

    rc_fonts = {
        "text.usetex": usetex,
        "font.family": "serif",
        "font.serif": ["Linux Libertine O"],
        "font.weight": "bold",
    }
    mpl.rcParams.update(rc_fonts)
    sns.set_context("paper")
    sns.set_style("whitegrid")


def configure_palette(n=None):
    """Reversed rocket palette, set as the global default and returned.

    Defaults to len(PALETTE_ORDER) so a figure drawing a subset still gets
    each series' usual colour. Pass n only for a figure whose series are not
    in PALETTE_ORDER at all (a sweep of prefetch distances, say).
    """
    if n is None:
        n = len(PALETTE_ORDER)
    custom_palette = sns.color_palette("rocket", n_colors=n)[::-1]
    sns.set_palette(custom_palette)
    return custom_palette


def get_subplots(num_row, num_col, plot_w=4, plot_h=4):
    """A grid of 4x4-inch panels -- one column fits a two-column page."""
    fig, axes = plt.subplots(
        num_row, num_col, figsize=(num_col * plot_w, num_row * plot_h)
    )
    return fig, axes


def series_style(name, palette):
    """Colour, dash and marker for one plotted series."""
    variant = VARIANT_STYLE.get(name)
    base = canonical(variant["base"] if variant else name)
    colour = (
        palette[PALETTE_ORDER.index(base) % len(palette)]
        if base in PALETTE_ORDER
        else None
    )
    return {
        "color": colour,
        "linestyle": variant["linestyle"] if variant else "-",
        "marker": variant["marker"] if variant else "o",
    }


def styles_for(names, palette):
    """Style per series, for a figure drawing exactly `names`.

    A variant's dash and marker exist to tell it apart from the other series
    drawn in the same colour. When it is the only one on the figure with that
    colour there is nothing to tell it apart from, so it is drawn in the
    ordinary way (solid, round) and the caption carries the distinction
    instead. That is decided by how many drawn series share its colour, not by
    whether the base itself is drawn: a figure of nothing but variants (every
    table in both prefetcher states, say) still needs them distinguishable.
    """
    def base_of(name):
        variant = VARIANT_STYLE.get(name)
        return canonical(variant["base"] if variant else name)

    drawn = Counter(base_of(n) for n in names)
    out = {}
    for name in names:
        style = series_style(name, palette)
        if name in VARIANT_STYLE and drawn[base_of(name)] < 2:
            style = {**style, "linestyle": "-", "marker": "o"}
        out[name] = style
    return out


# =============================================================================
# DRAWING
# =============================================================================


def draw_band(ax, sub, style, lo="lo", hi="hi", x="x"):
    """Min/max band over a point's repeats, drawn under its line.

    Collections taken before the collector repeated its points carry one
    sample (or none), so there is nothing to shade and the band is skipped.
    """
    if lo not in sub.columns or hi not in sub.columns:
        return
    if sub[lo].isna().any() or sub[hi].isna().any():
        return
    if not (sub[hi] > sub[lo]).any():
        return
    ax.fill_between(sub[x], sub[lo], sub[hi], color=style["color"],
                    alpha=0.18, linewidth=0, zorder=1)


def tidy(ax):
    """Dashed major grid, and round the top of the axis up to a whole tick."""
    ax.grid(True, which="major", axis="both", linestyle="--")
    ticks = ax.get_yticks()
    if len(ticks) > 1:
        step_value = ticks[1] - ticks[0]
        ymin, ymax = ax.get_ylim()
        remainder = ymax % step_value
        if remainder != 0:
            ax.set_ylim(ymin, ymax + remainder)


def add_legend(fig, palette, names, ncol=None, styles=None):
    """One figure-level legend above the panels, in PALETTE_ORDER."""
    styles = styles or {n: series_style(n, palette) for n in names}
    custom_lines = [
        Line2D([0], [0], label=display_name(name), **styles[name])
        for name in names
    ]
    fig.legend(fontsize=8, handles=custom_lines, loc="upper center",
               ncol=ncol or len(names))


def axis_labels(param_name):
    """Axis labels for the sweeps the paper reuses across directories."""
    fill = "fill factor (\\%)" if mpl.rcParams["text.usetex"] else "fill factor (%)"
    if param_name == "fill":
        return fill, "throughput (Mops)"
    if param_name == "fill_bw":
        return fill, "DRAM bandwidth (GB/s)"
    if param_name == "threads":
        return "threads", "throughput (Mops)"
    if param_name == "threads_bw":
        return "threads", "DRAM bandwidth (GB/s)"
    raise ValueError(f"no axis labels for {param_name!r}")


def save(fig, path, legend_top=0.94, dpi=300):
    """tight_layout under the figure legend, save, close, and say where.

    legend_top is the fraction of the figure the panels may use: a lone panel
    needs far less headroom (0.94) than a grid whose legend wraps (0.92).
    """
    fig.tight_layout(rect=[0, 0, 1, legend_top])
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    print(f"[OK] Plot saved to {path}")

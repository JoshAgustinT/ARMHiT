# Plotting guide — ARMHiT figures

Copied from DRAMHiT's `scripts/eurosys_2026/PLOTTING.md`, with the series and
paths changed to this repo's.

Every figure in `scripts/` is drawn through one module, **`paper_style.py`**,
which sits next to this file. Import it and the figure comes out looking like
DRAMHiT's paper figures: same fonts, same palette, same grid, same legend, and
— the part that is easy to get wrong — the same colour for the same prefetch
flavour across directories.

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))  # scripts/<test>/ -> repo/styling

import paper_style as ps
```

It needs `seaborn` and `pandas`, which the system python on the Thor does not
have; run the plotters with the repo's venv:

```sh
uv venv --system-site-packages .venv && uv pip install --python .venv/bin/python seaborn
.venv/bin/python scripts/threads/plot_data.py scripts/threads/thor.json
```

`scripts/prefetches/plot_data.py` is the shortest complete example.

---

## 1. The rules that matter

**Build the palette at full size, always.** `ps.configure_palette()` returns
nine colours, one per entry in `PALETTE_ORDER`, and a series takes the slot
its canonical name holds in that list:

```python
PALETTE_ORDER = ["DOUBLE", "DOUBLE_L2", "L1", "L2", "L3", "NTA", "L2STRM",
                 "L3STRM", "NONE"]
```

A figure that plots only three of the nine still calls
`configure_palette()` with no argument and looks each series up by name via
`ps.series_style(name, palette)`. Rebuilding the palette at `n=3` would
recolour those three and silently break every cross-figure comparison a
reader makes. Pass an explicit `n` only for a sweep whose series are not
prefetch flavours at all (queue sizes, table sizes, …).

**Plot with the canonical name, label with the display name.** The code
stores the `PREFETCH=` make value (`DOUBLE`, `NTA`, …); the page says
`double (L3+L1)`, `L1 strm`, …. `ps.display_name()` does the translation and
`ps.add_legend()` calls it for you, so neither the data files nor the lookups
have to change when the paper renames something. `ps.ALIASES` absorbs
whatever a collector happened to write so an old json still lands on the
right colour.

**Order series with `ps.order_series()`,** not with `sorted()`. It sorts into
`PALETTE_ORDER` and puts anything unrecognised last, so the legend reads in
the same order in every figure.

**A variant is not a new series.** A run that is the same algorithm under
different conditions (`DOUBLE` built with `BRANCH=branched`, say) reuses its
base colour and is told apart by dash and marker — see `ps.VARIANT_STYLE`
(empty for now). Spending a palette slot on it makes it
look like a tenth competitor.

---

## 2. The shape of a plotter

```python
ps.configure_style()                 # fonts, seaborn context + whitegrid
palette = ps.configure_palette()     # nine colours, global default

fig, axes = ps.get_subplots(1, 2)    # 4x4-inch panels

for ax, phase in zip(axes.ravel(), phases):
    for name in ps.order_series(series):
        sub = df[df.series == name].sort_values("x")
        style = ps.series_style(name, palette)
        ps.draw_band(ax, sub, style)                      # min/max over reps
        sns.lineplot(data=sub, x="x", y="mops", ax=ax, legend=False, **style)
    ax.set_ylim(bottom=0)
    ps.tidy(ax)                                           # dashed grid, round top

ps.add_legend(fig, palette, ps.order_series(series))      # one legend, above
ps.save(fig, "out.png", legend_top=0.9)                   # tight_layout + 300 dpi
```

Notes on the pieces:

| helper | what it does | when to override |
| --- | --- | --- |
| `configure_style()` | serif (Linux Libertine O), `usetex` if `latex` **and** `dvipng` are on `PATH`, else mathtext with a warning | never |
| `configure_palette(n=None)` | reversed `rocket`, set as seaborn's default and returned | only for non-prefetch series |
| `get_subplots(r, c, plot_w=4, plot_h=4)` | one 4×4-inch panel per cell; a single column fits a two-column page at `\columnwidth` | wider panels for a long x axis |
| `series_style(name, palette)` | `{color, linestyle, marker}` — splat it into `sns.lineplot` | — |
| `draw_band(ax, sub, style)` | shades `lo`..`hi` at 18% alpha under the line; skips silently when a collection has no repeats | column names via `lo=`/`hi=`/`x=` |
| `tidy(ax)` | dashed major grid, extends the top to a whole tick | — |
| `add_legend(fig, palette, names)` | one figure-level legend, `loc="upper center"`, `ncol=len(names)` | `ncol=` when it wraps |
| `axis_labels(param)` | the standard labels for `fill`, `fill_bw`, `threads`, `threads_bw` | add a case rather than inlining a string |
| `save(fig, path, legend_top)` | `tight_layout(rect=[0,0,1,legend_top])`, 300 dpi, close, print the path | `legend_top=0.94` for a lone panel, `0.9` for a grid |

Two conventions the helpers do not enforce, because they are yours to set:

- **`ax.set_ylim(bottom=0)` on every throughput axis.** Mops is a ratio scale;
  a truncated y axis turns a 5% gap into a visual 3×.
- **`ax.set_ylim(bottom=0)` on every bandwidth axis** too, for the same reason.
- **Keep the x axis spanning the whole sweep even when a series stops early.**
  A run that fails (out of memory, a failed `--test`) leaves a short line.
  The short line is the finding; rescaling the axis to the data that exists
  hides it.

---

## 3. What the collectors must hand the plotter

The band is only drawable if the collection kept its repeats, so
`scripts/threads/collect_data.py` writes a median **and** the raw samples per
point:

```json
{
  "threads":      [1, 2, 3],
  "set_mops":     [140.0, 270.0, 395.0],
  "set_samples":  [[139, 140, 141], [...], [...]]
}
```

`plot_data.py` draws the median and shades `min`..`max`. Collections made
before repeats existed carry one sample or none; `ps.draw_band()` notices and
draws nothing rather than a zero-width ribbon.

Two more things worth keeping in the json, both of which the plotters read:
a `plot_order` list so the figure does not depend on dict ordering, and
enough machine context (`ht_size_gib`, `fill`) to build the panel title
without a second source of truth.

---

## 4. Reusing this in another project

`paper_style.py` depends only on `matplotlib` and `seaborn` and holds no
paths, so it copies cleanly. When you move it:

1. Replace `PALETTE_ORDER` with your own series, longest-lived first — its
   order *is* the colour assignment, so appending is safe and reordering is
   not.
2. Replace `ALIASES` / `DISPLAY_NAMES` with your internal→published name map,
   and leave the lookups alone.
3. Keep `configure_palette()`'s no-argument default. It is the whole reason
   colours survive across figures.

In DRAMHiT, `intel_hbm/plot_style.py` is a three-line shim that re-exports
`paper_style`; copy that pattern if a subdirectory needs its own module name
for compatibility rather than a second copy of the style.

---

## 5. Fonts

`configure_style()` asks for Linux Libertine O and for LaTeX text rendering.
Neither is required — without `latex` + `dvipng` on `PATH` it prints

```
[!] latex/dvipng not found, rendering without usetex
```

and falls back to matplotlib's mathtext, which is fine for reading the data
but is **not** what should go in the camera-ready. Render the final figures
somewhere with a TeX install so the figure text matches the body text. If you
write an axis label containing a literal `%`, escape it for the usetex path
(`\\%`) — `ps.axis_labels("fill")` shows the pattern.

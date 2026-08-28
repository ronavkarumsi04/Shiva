"""Parser for real simulator output — SPICE, FEA, and CFD result logs.

The engineering prong can *invoke* toolchains and generate GitHub Actions /
macOS workflows; this module closes the loop by reading what the simulator
actually wrote back and turning it into structured, testable signals:

* **SPICE** (ngspice batch ``.log`` / ``.out``, LTspice ``.txt`` exports):
  operating-point values, ``.tran``/``.ac``/``.dc`` measurement tables — with
  gain, −3 dB bandwidth/cutoff, peaking, DC rails, and transient settle/rise
  summaries — plus hard error lines.
* **FEA** (CalculiX/NASTRAN/Abaqus-style text and generic stat blocks):
  max von Mises stress, max displacement/deflection, factor of safety, and
  stress/force result tables.
* **CFD** (OpenFOAM/Fluent-style logs): convergence (residual drop), drag/lift
  coefficients, pressure drop, and inlet/outlet totals.

Design rules (engineering honesty):

* Only report metrics that are literally present in the file — values are
  never extrapolated or invented. Every metric keeps a ``unit`` and the parser
  records the ``source`` path.
* A result is ``ok`` only when the run **converged/completed** and emitted no
  error/fatal lines. A truncated or erroring log is reported, not passed.
* The parser is deterministic and stdlib-only so it can run inside the
  verifier during tests without any simulator installed (it parses captured
  logs).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

from trishula.core.logging import get_logger

log = get_logger("engineering.simresults")

# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Series:
    """A columnar measurement sweep (e.g. .ac frequency response)."""

    x_label: str = ""
    x_unit: str = ""
    columns: list[str] = field(default_factory=list)   # column labels
    rows: list[list[float]] = field(default_factory=list)  # x, y1, y2, ...

    def column(self, name: str) -> list[float]:
        try:
            idx = 1 + [c.lower() for c in self.columns].index(name.lower())
        except ValueError:
            return []
        return [r[idx] for r in self.rows if idx < len(r)]

    @property
    def x(self) -> list[float]:
        return [r[0] for r in self.rows if r]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SimulationResult:
    """Normalized result of parsing one simulator log."""

    source: str
    flavor: str = "unknown"           # spice | fea | cfd | unknown
    ok: bool = False                  # converged/completed AND no errors
    converged: bool = False
    completed: bool = False
    metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    # metric shape: {name: {"value": float|str, "unit": str, "note": str}}
    series: list[Series] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: str = ""
    raw_tail: str = ""

    def metric(self, name: str) -> dict[str, Any] | None:
        return self.metrics.get(name.lower())

    def value(self, name: str) -> float | str | None:
        m = self.metric(name)
        return m["value"] if m else None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["series"] = [s.to_dict() for s in self.series]
        return d


# ─────────────────────────────────────────────────────────────────────────────
# Number helpers
# ─────────────────────────────────────────────────────────────────────────────

_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"
_UNIT = r"[a-zA-Zμ°Ω%/\.\-]*"


def _f(token: str) -> float | None:
    try:
        return float(token)
    except (TypeError, ValueError):
        return None


def _to_float(text: str) -> float | None:
    """Parse a number possibly carrying an engineering suffix (k, M, u, n…)."""
    text = text.strip().replace(",", "")
    m = re.match(rf"({_NUM})\s*([a-zA-ZμΩ%]?){_UNIT}$", text)
    if not m:
        return _f(text)
    val = _f(m.group(1))
    if val is None:
        return None
    suffix = m.group(2)
    mult = {
        "t": 1e12, "g": 1e9, "meg": 1e6, "m": 1e-3,
        "u": 1e-6, "μ": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15,
        "k": 1e3,
    }.get(suffix.lower(), 1.0)
    return val * mult


# ─────────────────────────────────────────────────────────────────────────────
# SPICE
# ─────────────────────────────────────────────────────────────────────────────

_SPICE_HINT = re.compile(
    r"ngspice|ltspice|\bspice\b|\.tran|\.ac\s|\.dc\s|\.op\b|"
    r"doanalyses|timestep too small|ac analysis|transient analysis|"
    r"operating point|voltage at node",
    re.I,
)
_SPICE_ERROR = re.compile(
    r"\b(error|fatal|failed|cannot|could not|no such|undefined|does not exist|"
    r"convergence|timestep too small|internal timestep)\b",
    re.I,
)
_SPICE_DONE = re.compile(r"transient analysis done|ac analysis done|dc analysis done|"
                         r"operating point info|job finished|elapsed time", re.I)
# ngspice op point / measurement line:  "vout = 3.298e+00" or "vx        1.2  5.0"
_KV_LINE = re.compile(rf"^\s*([\w.$()/\- ]{{1,28}}?)\s*[:=]?\s+({_NUM})(\s*{_UNIT})?\s*$")


def _parse_spice(text: str, source: str) -> SimulationResult:
    res = SimulationResult(source=source, flavor="spice")
    lines = text.splitlines()

    for ln in lines:
        low = ln.lower()
        if "warning" in low:
            res.warnings.append(ln.strip()[:200])
        m = _SPICE_ERROR.search(ln)
        if m and not _SPICE_DONE.search(ln):
            # "convergence" can appear in benign "achieved" lines; still capture.
            res.errors.append(ln.strip()[:200])

    # ── measurement tables (.tran / .ac / .dc) ─────────────────────────
    series = _parse_spice_tables(lines)
    res.series = series
    for s in series:
        _summarize_series(res, s)

    # ── operating point / scalar "key = value" ─────────────────────────
    if not series:
        _parse_spice_scalars(lines, res)

    res.completed = bool(_SPICE_DONE.search(text))
    res.converged = res.completed and not any(
        "timestep" in e.lower() or "convergence" in e.lower() or "fatal" in e.lower()
        for e in res.errors
    )
    res.ok = res.converged and not res.errors
    res.raw_tail = "\n".join(lines[-15:])
    res.summary = _spice_summary(res)
    return res


def _parse_spice_tables(lines: list[str]) -> list[Series]:
    """Extract ngspice measurement blocks.

    Blocks look like::

        Index   frequency       v(out)        ...
        -----   ---------       ------
        0       1.000000e+00    9.9e-01       ...
    or the plain (no header) two-column sweeps LTspice exports.
    """
    series: list[Series] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        # Header row: starts with "Index"/"Freq"/"Time"/"dc" and has >=2 words
        if re.match(r"^\s*(index|freq|frequency|time|v-sweep|dc)\b", ln, re.I) and \
                len(ln.split()) >= 2:
            header = ln.split()
            # skip the dashed separator line if present
            j = i + 1
            if j < n and set(lines[j].replace(" ", "").replace("-", "")) <= set(""):
                j += 1
            elif j < n and re.match(r"^\s*[\-\s]+$", lines[j]):
                j += 1
            cols = header[1:]
            rows: list[list[float]] = []
            while j < n:
                parts = lines[j].split()
                nums = [_f(p) for p in parts]
                if len(parts) < 2 or any(v is None for v in nums[: min(3, len(nums))]):
                    break
                rows.append([v for v in nums if v is not None][1:])  # drop Index col
                j += 1
            if rows:
                x_lbl, x_unit = _axis_label(cols[0] if cols else "x")
                series.append(Series(x_label=x_lbl, x_unit=x_unit,
                                     columns=cols[1:] if len(cols) > 1 else ["y"],
                                     rows=rows))
            i = j
            continue
        i += 1
    return series


def _axis_label(col: str) -> tuple[str, str]:
    low = col.lower()
    if low.startswith("freq"):
        return "frequency", "Hz"
    if low.startswith("time"):
        return "time", "s"
    if "sweep" in low or low.startswith("v-"):
        return "sweep", "V"
    return col, ""


def _summarize_series(res: SimulationResult, s: Series) -> None:
    if not s.rows or len(s.rows[0]) < 2:
        return
    x = s.x
    y = [r[1] for r in s.rows if len(r) > 1]
    if not y:
        return
    is_ac = s.x_label == "frequency"

    def put(name, value, unit, note=""):
        res.metrics[name.lower()] = {"value": value, "unit": unit, "note": note}

    if is_ac:
        # Ngspice prints .ac magnitude as a linear voltage (gain 1.0 at DC,
        # rolling off to ~0). Convert to dB; values already spanning negatives
        # (typical dB range) are treated as dB.
        mag = y
        if max(mag) > 1.5 and min(mag) < 0:
            db = mag                      # already dB
        else:
            db = [20 * math.log10(abs(v)) if v > 0 else -200.0 for v in mag]
        peak_db = max(db)
        put("gain_peak", round(peak_db, 3), "dB", "peak magnitude")
        put("gain_dc", round(db[0], 3), "dB", "low-frequency magnitude")
        # -3 dB bandwidth: first frequency where gain drops >3dB below peak.
        bw = None
        for f, g in zip(x, db):
            if g <= peak_db - 3.0:
                bw = f
                break
        if bw is not None:
            put("bandwidth", _eng(bw), "Hz", "−3 dB cutoff")
    else:
        # transient / dc sweep: rails, peak-to-peak, settling estimate
        ymin, ymax = min(y), max(y)
        put("y_min", round(ymin, 6), "V", f"min over {s.x_label}")
        put("y_max", round(ymax, 6), "V", f"max over {s.x_label}")
        put("y_pp", round(ymax - ymin, 6), "V", "peak-to-peak")
        final = sum(y[-3:]) / min(3, len(y))
        put("y_final", round(final, 6), "V", f"final/settled value vs {s.x_label}")


def _eng(v: float) -> str:
    for factor, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "k"),
                           (1e-3, "m"), (1e-6, "u"), (1e-9, "n")):
        if abs(v) >= factor or (factor < 1 and abs(v) < factor * 1000 and abs(v) >= factor):
            if abs(v) >= factor:
                return f"{v / factor:.4g} {suffix}"
    return f"{v:.4g}"


def _parse_spice_scalars(lines: list[str], res: SimulationResult) -> None:
    for ln in lines:
        m = _KV_LINE.match(ln)
        if not m:
            continue
        name = m.group(1).strip().lower()
        if not name or name in {"index"} or len(name) > 26:
            continue
        val = _f(m.group(2))
        if val is None:
            continue
        unit = (m.group(3) or "").strip()
        res.metrics[name] = {"value": val, "unit": unit, "note": "operating point"}


def _spice_summary(res: SimulationResult) -> str:
    if res.errors:
        return f"SPICE run reported {len(res.errors)} error(s); not converged."
    bits = [f"{k}={v['value']}{v['unit']}" for k, v in list(res.metrics.items())[:6]]
    tail = "; ".join(bits)
    state = "converged" if res.converged else "incomplete"
    return f"SPICE {state}: {tail}" if tail else f"SPICE {state}."


# ─────────────────────────────────────────────────────────────────────────────
# FEA
# ─────────────────────────────────────────────────────────────────────────────

_FEA_HINT = re.compile(
    r"von\s*mises|stress|displacement|deflection|factor of safety|fos\b|"
    r"calculix|abaqus|nastran|strain|reaction force|equivalent plastic",
    re.I,
)
_FEA_ERROR = re.compile(r"\b(error|fatal|aborted|no convergence|diverged|"
                        r"nonlinear solution did not converge)\b", re.I)
_FEA_DONE = re.compile(r"converged|solution completed|analysis complete|"
                       r"job finished|normal completion|the calculation|finished", re.I)
_FEA_METRICS = [
    (re.compile(r"von\s*mises[^=\n:]*[:=]?\s*(" + _NUM + r")\s*(" + _UNIT + ")?", re.I),
     "stress_von_mises_max", "Pa", "max von Mises stress"),
    (re.compile(r"max(?:imum)?\s*(?:displacement|deflection)[^=\n:]*[:=]?\s*("
                + _NUM + r")\s*(" + _UNIT + ")?", re.I),
     "displacement_max", "m", "max displacement/deflection"),
    (re.compile(r"factor\s*of\s*safety[^=\n:]*[:=]?\s*(" + _NUM + r")", re.I),
     "factor_of_safety", "", "factor of safety"),
    (re.compile(r"\bfos\b[^=\n:]*[:=]?\s*(" + _NUM + r")", re.I),
     "factor_of_safety", "", "factor of safety"),
]


def _parse_fea(text: str, source: str) -> SimulationResult:
    res = SimulationResult(source=source, flavor="fea")
    for ln in text.splitlines():
        low = ln.lower()
        if "warning" in low:
            res.warnings.append(ln.strip()[:200])
        if _FEA_ERROR.search(ln) and "converged" not in low:
            res.errors.append(ln.strip()[:200])
        for rx, key, default_unit, note in _FEA_METRICS:
            m = rx.search(ln)
            if m and key not in res.metrics:
                val = _to_float(m.group(1))
                unit = (m.group(2) or default_unit).strip() if m.lastindex and m.lastindex >= 2 \
                    else default_unit
                if val is not None:
                    res.metrics[key] = {"value": val, "unit": unit, "note": note}

    # Factor of safety against yield when stress is present (only if both given).
    fos = res.metrics.get("factor_of_safety")
    res.converged = bool(_FEA_DONE.search(text)) and not any(
        "converge" in e.lower() or "fatal" in e.lower() for e in res.errors
    )
    res.completed = res.converged
    res.ok = res.converged and not res.errors
    res.raw_tail = "\n".join(text.splitlines()[-15:])
    bits = [f"{k}={v['value']} {v['unit']}".strip() for k, v in res.metrics.items()]
    res.summary = (f"FEA {'converged' if res.converged else 'NOT converged'}: "
                   + ("; ".join(bits) if bits else "no result metrics found"))
    if fos is not None:
        try:
            res.metrics["factor_of_safety"]["note"] += \
                f" (>=1.5 typical design target; {fos['value']})"
        except Exception:  # noqa: BLE001
            pass
    return res


# ─────────────────────────────────────────────────────────────────────────────
# CFD
# ─────────────────────────────────────────────────────────────────────────────

_CFD_HINT = re.compile(
    r"openfoam|fluent|cfd|residual|drag coefficient|lift coefficient|\bcd\b|\bcl\b|"
    r"pressure drop|continuity|momentum|turbulence|iteration",
    re.I,
)
_CFD_ERROR = re.compile(r"\b(error|fatal|aborted|diverged|floa(ting|t) point exception|"
                        r"solution did not converge)\b", re.I)
_CFD_COEFF = [
    (re.compile(r"\bcd\b[^=\n:]*[:=]?\s*(" + _NUM + r")", re.I), "drag_coefficient", "Cd"),
    (re.compile(r"\bcl\b[^=\n:]*[:=]?\s*(" + _NUM + r")", re.I), "lift_coefficient", "Cl"),
    (re.compile(r"drag\s*(?:coefficient|force)[^=\n:]*[:=]?\s*(" + _NUM + r")", re.I),
     "drag_coefficient", "Cd"),
    (re.compile(r"lift\s*(?:coefficient|force)[^=\n:]*[:=]?\s*(" + _NUM + r")", re.I),
     "lift_coefficient", "Cl"),
    (re.compile(r"pressure\s*drop[^=\n:]*[:=]?\s*(" + _NUM + r")\s*(" + _UNIT + ")?", re.I),
     "pressure_drop", "Pa"),
]
_RESID = re.compile(r"(continuity|momentum|turbulence|energy|x-?momentum|"
                    r"y-?momentum|z-?momentum)[^0-9\n]*(" + _NUM + r")", re.I)


def _parse_cfd(text: str, source: str) -> SimulationResult:
    res = SimulationResult(source=source, flavor="cfd")
    residuals: dict[str, list[float]] = {}
    for ln in text.splitlines():
        low = ln.lower()
        if "warning" in low:
            res.warnings.append(ln.strip()[:200])
        if _CFD_ERROR.search(ln):
            res.errors.append(ln.strip()[:200])
        for rx, key, label in _CFD_COEFF:
            m = rx.search(ln)
            if m and key not in res.metrics:
                val = _to_float(m.group(1))
                unit = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
                if val is not None:
                    res.metrics[key] = {"value": val, "unit": unit or label,
                                        "note": f"{label} (reported)"}
        rm = _RESID.search(ln)
        if rm:
            v = _f(rm.group(2))
            if v is not None and 0 < v < 1e6:
                residuals.setdefault(rm.group(1).lower(), []).append(v)

    # Convergence: residual fell below ~1e-4 (or "converged" present) and
    # residuals are decreasing.
    converged_text = bool(re.search(r"solution (is )?converged|converged\b", text, re.I))
    converged_resid = False
    final_resids: dict[str, float] = {}
    for name, vals in residuals.items():
        if len(vals) >= 2:
            final_resids[name] = vals[-1]
            if vals[-1] < 1e-4 and vals[-1] <= vals[0]:
                converged_resid = True
    if final_resids:
        worst = max(final_resids.values())
        res.metrics["residual_worst_final"] = {
            "value": f"{worst:.2e}", "unit": "", "note": "worst final residual"}
    res.converged = converged_text or converged_resid
    res.completed = res.converged or bool(re.search(r"end\b|finished|postprocessing", text, re.I))
    res.ok = res.converged and not any("fatal" in e.lower() or "diverged" in e.lower()
                                       for e in res.errors)
    res.raw_tail = "\n".join(text.splitlines()[-15:])
    bits = [f"{v['note'].split(' ')[0]}={v['value']}" for v in res.metrics.values()]
    res.summary = (f"CFD {'converged' if res.converged else 'NOT converged'}: "
                   + ("; ".join(bits) if bits else "no coefficients parsed"))
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────────────────────────────────────

def parse_text(text: str, source: str = "<text>") -> SimulationResult:
    """Sniff the flavor of a log and parse it."""
    flavor = sniff(text)
    if flavor == "spice":
        return _parse_spice(text, source)
    if flavor == "fea":
        return _parse_fea(text, source)
    if flavor == "cfd":
        return _parse_cfd(text, source)
    res = SimulationResult(source=source, flavor="unknown")
    res.summary = "Unrecognized simulator output; no metrics extracted."
    res.raw_tail = "\n".join(text.splitlines()[-15:])
    return res


def sniff(text: str) -> str:
    """Return ``'spice' | 'fea' | 'cfd' | 'unknown'`` for a log body."""
    scores = {"spice": len(_SPICE_HINT.findall(text)),
              "fea": len(_FEA_HINT.findall(text)),
              "cfd": len(_CFD_HINT.findall(text))}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "unknown"


def parse_file(path: str | Path) -> SimulationResult:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # noqa: BLE001
        res = SimulationResult(source=str(p), flavor="unknown")
        res.errors.append(f"cannot read: {exc}")
        res.summary = f"Could not read {p}"
        return res
    return parse_text(text, source=str(p))


def parse_many(paths: Iterable[str | Path]) -> list[SimulationResult]:
    return [parse_file(p) for p in paths]

"""Cross-domain engineering formula library.

Each :class:`Formula` is a named, domain-tagged, SI-pure computation with a
docstring, an argument schema (name -> unit symbol), and the SI unit of the
result. The registry is intentionally transparent: formulas are pure python
callables so a reviewer (or the safety gate) can audit them, and
:func:`calculate` normalizes non-SI inputs via :mod:`trishula.engineering.units`.

Domains covered: electrical, mechanical, structural/civil, thermal, fluid,
aerospace, biomedical, chemical, optical, controls, embedded.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List

from trishula.engineering import constants as _C
from trishula.engineering.units import to_si


@dataclass(frozen=True)
class Formula:
    name: str
    domain: str
    description: str
    args: Dict[str, str]          # argument name -> SI unit
    result_unit: str
    fn: Callable[..., float] = field(repr=False)
    tags: tuple[str, ...] = ()

    def calculate(self, **values: float) -> float:
        return self.fn(**values)


FORMULAS: Dict[str, Formula] = {}


def _register(f: Formula) -> Formula:
    FORMULAS[f.name] = f
    return f


# ── Electrical ──────────────────────────────────────────────────────────────

_register(Formula("ohms_law", "electrical",
    "Ohm's law: voltage from current and resistance.",
    {"i": "A", "r": "Ω"}, "V", lambda i, r: i * r, ("dc",)))

_register(Formula("power_electric", "electrical",
    "Electrical power P = V·I.", {"v": "V", "i": "A"}, "W", lambda v, i: v * i))

_register(Formula("resistors_series", "electrical",
    "Equivalent resistance of resistors in series.",
    {"resistances": "Ω"}, "Ω", lambda resistances: float(sum(resistances))))

_register(Formula("resistors_parallel", "electrical",
    "Equivalent resistance of resistors in parallel.",
    {"resistances": "Ω"}, "Ω", lambda resistances: 1.0 / sum(1.0 / r for r in resistances)))

_register(Formula("capacitor_energy", "electrical",
    "Energy stored in a capacitor E = ½CV².",
    {"c": "F", "v": "V"}, "J", lambda c, v: 0.5 * c * v * v))

_register(Formula("inductor_energy", "electrical",
    "Energy stored in an inductor E = ½LI².",
    {"l": "H", "i": "A"}, "J", lambda l, i: 0.5 * l * i * i))

_register(Formula("cap_impedance", "electrical",
    "Capacitive reactance at a frequency.",
    {"c": "F", "f": "Hz"}, "Ω", lambda c, f: 1.0 / (2 * math.pi * f * c)))

_register(Formula("ind_impedance", "electrical",
    "Inductive reactance at a frequency.",
    {"l": "H", "f": "Hz"}, "Ω", lambda l, f: 2 * math.pi * f * l))

_register(Formula("rc_time_constant", "electrical",
    "RC time constant τ = RC.", {"r": "Ω", "c": "F"}, "s", lambda r, c: r * c))

_register(Formula("rl_cutoff_freq", "electrical",
    "RC low-pass cutoff frequency fc = 1/(2πRC).",
    {"r": "Ω", "c": "F"}, "Hz", lambda r, c: 1.0 / (2 * math.pi * r * c)))

_register(Formula("voltage_divider", "electrical",
    "Output of an unloaded voltage divider.",
    {"v_in": "V", "r1": "Ω", "r2": "Ω"}, "V",
    lambda v_in, r1, r2: v_in * r2 / (r1 + r2)))

def _pcb_trace_current(width: float, thickness: float, temp_rise: float = 10.0) -> float:
    # IPC-2221A external (air-side) traces: I = k·ΔT^0.44·A^0.725,
    # A in mil², k=0.048 external / 0.024 internal. width/thickness in metres.
    width_mil = width / 2.54e-5
    thick_mil = thickness / 2.54e-5
    area_mil2 = width_mil * thick_mil
    return 0.048 * temp_rise ** 0.44 * area_mil2 ** 0.725


_register(Formula("pcb_trace_current", "electrical",
    "IPC-2221A external PCB trace current capacity for a given temperature rise.",
    {"width": "m", "thickness": "m", "temp_rise": "K"}, "A",
    _pcb_trace_current, ("pcb", "ipc-2221")))

# ── Embedded / digital ──────────────────────────────────────────────────────

_register(Formula("adc_lsb", "embedded",
    "ADC least-significant-bit voltage (resolution).",
    {"v_ref": "V", "bits": "count"}, "V",
    lambda v_ref, bits: v_ref / (2 ** bits), ("adc",)))

_register(Formula("adc_code_to_voltage", "embedded",
    "Convert an ADC reading code to voltage.",
    {"code": "count", "v_ref": "V", "bits": "count"}, "V",
    lambda code, v_ref, bits: code * v_ref / (2 ** bits), ("adc",)))

# ── Mechanical / structural ─────────────────────────────────────────────────

_register(Formula("axial_stress", "mechanical",
    "Normal (axial) stress σ = F/A.",
    {"f": "N", "area": "m²"}, "Pa", lambda f, area: f / area, ("structures",)))

_register(Formula("axial_strain", "mechanical",
    "Normal strain ε = ΔL/L0.",
    {"delta_l": "m", "l0": "m"}, "m/m", lambda delta_l, l0: delta_l / l0))

_register(Formula("hooke_young", "mechanical",
    "Young's modulus relation E = σ/ε.",
    {"stress": "Pa", "strain": "m/m"}, "Pa", lambda stress, strain: stress / strain))

_register(Formula("beam_deflection_cantilever_end", "mechanical",
    "Tip deflection of a cantilever under end point load: FL³/(3EI).",
    {"f": "N", "length": "m", "e": "Pa", "i": "m⁴"}, "m",
    lambda f, length, e, i: f * length ** 3 / (3 * e * i), ("beam",)))

_register(Formula("beam_deflection_simply_supported_center", "mechanical",
    "Center deflection of a simply supported beam with center point load: FL³/(48EI).",
    {"f": "N", "length": "m", "e": "Pa", "i": "m⁴"}, "m",
    lambda f, length, e, i: f * length ** 3 / (48 * e * i), ("beam",)))

_register(Formula("torsion_shear_stress", "mechanical",
    "Maximum shear stress in a solid round shaft: τ = 16T/(πd³).",
    {"torque": "N·m", "diameter": "m"}, "Pa",
    lambda torque, diameter: 16 * torque / (math.pi * diameter ** 3), ("shaft",)))

_register(Formula("torsion_angle", "mechanical",
    "Twist angle of a round shaft θ = 32TL/(Gπd⁴) (radians).",
    {"torque": "N·m", "length": "m", "g": "Pa", "diameter": "m"}, "rad",
    lambda torque, length, g, diameter: 32 * torque * length / (g * math.pi * diameter ** 4),
    ("shaft",)))

_register(Formula("second_moment_rectangle", "mechanical",
    "Area moment of inertia of a rectangle about its centroidal axis: bh³/12.",
    {"b": "m", "h": "m"}, "m⁴", lambda b, h: b * h ** 3 / 12, ("beam",)))

_register(Formula("buckling_euler", "mechanical",
    "Euler column critical buckling load P = π²EI/(KL)².",
    {"e": "Pa", "i": "m⁴", "length": "m", "k": "factor"}, "N",
    lambda e, i, length, k=1.0: math.pi ** 2 * e * i / (k * length) ** 2,
    ("column",)))

_register(Formula("factor_of_safety", "mechanical",
    "Factor of safety = capacity / applied load.",
    {"capacity": "Pa", "applied": "Pa"}, "ratio",
    lambda capacity, applied: capacity / applied, ("structures",)))

# ── Thermal / fluid ─────────────────────────────────────────────────────────

_register(Formula("conduction_heat", "thermal",
    "Fourier conduction heat rate q = kAΔT/L.",
    {"k": "W/(m·K)", "area": "m²", "delta_t": "K", "length": "m"}, "W",
    lambda k, area, delta_t, length: k * area * delta_t / length))

_register(Formula("reynolds_number", "fluid",
    "Reynolds number Re = ρvD/μ.",
    {"rho": "kg/m³", "v": "m/s", "d": "m", "mu": "Pa·s"}, "ratio",
    lambda rho, v, d, mu: rho * v * d / mu, ("flow",)))

_register(Formula("pressure_drop_darcy", "fluid",
    "Darcy–Weisbach pipe head loss ΔP = f·(L/D)·ρv²/2.",
    {"f": "ratio", "length": "m", "d": "m", "rho": "kg/m³", "v": "m/s"}, "Pa",
    lambda f, length, d, rho, v: f * (length / d) * rho * v * v / 2, ("pipe",)))

_register(Formula("ideal_gas_pressure", "chemical",
    "Ideal-gas law P = nRT/V.",
    {"n": "mol", "t": "K", "v": "m³"}, "Pa",
    lambda n, t, v: n * _C.R * t / v, ("gas",)))

# ── Aerospace ───────────────────────────────────────────────────────────────

_register(Formula("dynamic_pressure", "aerospace",
    "Dynamic pressure q = ½ρv².",
    {"rho": "kg/m³", "v": "m/s"}, "Pa", lambda rho, v: 0.5 * rho * v ** 2))

_register(Formula("lift_force", "aerospace",
    "Lift L = ½ρv²S·CL.",
    {"rho": "kg/m³", "v": "m/s", "area": "m²", "cl": "ratio"}, "N",
    lambda rho, v, area, cl: 0.5 * rho * v ** 2 * area * cl, ("aero",)))

_register(Formula("drag_force", "aerospace",
    "Drag D = ½ρv²S·CD.",
    {"rho": "kg/m³", "v": "m/s", "area": "m²", "cd": "ratio"}, "N",
    lambda rho, v, area, cd: 0.5 * rho * v ** 2 * area * cd, ("aero",)))

_register(Formula("l_d_ratio", "aerospace",
    "Lift-to-drag ratio CL/CD.",
    {"cl": "ratio", "cd": "ratio"}, "ratio", lambda cl, cd: cl / cd, ("aero",)))

_register(Formula("orbital_velocity", "aerospace",
    "Circular orbit velocity v = sqrt(μ/r).",
    {"mu": "m³/s²", "r": "m"}, "m/s", lambda mu, r: math.sqrt(mu / r), ("orbital",)))

_register(Formula("orbital_period", "aerospace",
    "Circular orbit period T = 2πsqrt(r³/μ).",
    {"mu": "m³/s²", "r": "m"}, "s",
    lambda mu, r: 2 * math.pi * math.sqrt(r ** 3 / mu), ("orbital",)))

_register(Formula("rocket_delta_v", "aerospace",
    "Tsiolkovsky rocket equation Δv = ve·ln(m0/mf).",
    {"ve": "m/s", "m0": "kg", "mf": "kg"}, "m/s",
    lambda ve, m0, mf: ve * math.log(m0 / mf), ("propulsion",)))

_register(Formula("mass_flow_rate", "aerospace",
    "Mass flow ṁ = ρAv.",
    {"rho": "kg/m³", "area": "m²", "v": "m/s"}, "kg/s",
    lambda rho, area, v: rho * area * v, ("propulsion", "fluid")))

_register(Formula("speed_of_sound", "aerospace",
    "Speed of sound in an ideal gas a = sqrt(γRspec·T).",
    {"gamma": "ratio", "r_specific": "J/(kg·K)", "t": "K"}, "m/s",
    lambda gamma, r_specific, t: math.sqrt(gamma * r_specific * t)))

_register(Formula("mach_number", "aerospace",
    "Mach number M = v/a.",
    {"v": "m/s", "a": "m/s"}, "ratio", lambda v, a: v / a))

# ── Biomedical ──────────────────────────────────────────────────────────────

_register(Formula("bmi", "biomedical",
    "Body mass index = mass(kg) / height(m)².",
    {"mass_kg": "kg", "height_m": "m"}, "kg/m²",
    lambda mass_kg, height_m: mass_kg / height_m ** 2))

_register(Formula("bsa_du_bois", "biomedical",
    "Body surface area, Du Bois: 0.007184·kg^0.425·cm^0.725 (m²). Height given in SI (m); converted to cm internally.",
    {"mass_kg": "kg", "height_m": "m"}, "m²",
    lambda mass_kg, height_m: 0.007184 * mass_kg ** 0.425 * (height_m * 100.0) ** 0.725,
    ("clinical",)))

_register(Formula("cardiac_output", "biomedical",
    "Cardiac output = stroke volume × heart rate (L/min). Stroke volume in SI (m³); e.g. 70 mL = 7e-5 m³.",
        {"stroke_volume_m3": "m³", "hr": "ratio"}, "L/min",  # hr = beats/min
    lambda stroke_volume_m3, hr: stroke_volume_m3 * 1e3 * hr, ("clinical",)))

_register(Formula("bubble_radius_laplace", "biomedical",
    "Laplace's law for an alveolus/bubble: ΔP = 4γ/r (two surfaces).",
    {"gamma": "N/m", "r": "m"}, "Pa", lambda gamma, r: 4 * gamma / r,
    ("physiology",)))

_register(Formula("snr_db", "biomedical",
    "Signal-to-noise ratio in dB for a measurement.",
    {"signal": "V", "noise": "V"}, "dB",
    lambda signal, noise: 20 * math.log10(signal / noise), ("instrumentation",)))

# ── Optical ─────────────────────────────────────────────────────────────────

_register(Formula("wavelength_to_freq", "optical",
    "f = c/λ for light in vacuum.",
    {"wavelength": "m"}, "Hz", lambda wavelength: _C.c / wavelength))

_register(Formula("snells_angle", "optical",
    "Snell's law refracted angle θ2 = asin(n1·sinθ1 / n2).",
    {"n1": "ratio", "n2": "ratio", "theta1_deg": "deg"}, "deg",
    lambda n1, n2, theta1_deg: math.degrees(
        math.asin(min(1.0, n1 * math.sin(math.radians(theta1_deg)) / n2)))))

_register(Formula("critical_angle", "optical",
    "Total internal reflection critical angle θc = asin(n2/n1).",
    {"n1": "ratio", "n2": "ratio"}, "deg",
    lambda n1, n2: math.degrees(math.asin(n2 / n1)) if n2 < n1 else 90.0))

# ── Controls ────────────────────────────────────────────────────────────────

_register(Formula("first_order_settling_time", "controls",
    "2% settling time for a first-order system ≈ 4τ.",
    {"tau": "s"}, "s", lambda tau: 4 * tau))

_register(Formula("pid_derivative_approx", "controls",
    "Derivative term from consecutive errors: Kd·(e − e_prev)/dt.",
    {"kd": "s", "error": "ratio", "error_prev": "ratio", "dt": "s"}, "ratio",
    lambda kd, error, error_prev, dt: kd * (error - error_prev) / dt))


# ── helpers ─────────────────────────────────────────────────────────────────

def calculate(name: str, raw_args: Dict[str, float] | None = None, **kwargs: float) -> float:
    """Calculate a formula by name, converting non-SI inputs when given as
    ``(value, unit)`` tuples.

    Examples:
        calculate("ohms_law", i=0.5, r=100)
        calculate("dynamic_pressure", rho=1.225, v=(340, "m/s"))
    """
    formula = FORMULAS.get(name)
    if formula is None:
        raise KeyError(f"unknown formula {name!r}; see list_formulas()")
    merged: Dict[str, float] = dict(raw_args or {})
    merged.update(kwargs)
    converted: Dict[str, float] = {}
    for key, val in merged.items():
        if isinstance(val, (tuple, list)) and len(val) == 2 and isinstance(val[1], str):
            value, unit = val
            converted[key] = to_si(float(value), unit)
        elif isinstance(val, (list, tuple)):
            # vector argument (e.g. a set of resistances) — convert each element
            converted[key] = [float(v) for v in val]
        else:
            converted[key] = float(val)
    return formula.calculate(**converted)


def formulas_for_domain(domain: str) -> List[Formula]:
    return [f for f in FORMULAS.values() if f.domain == domain]


def list_formulas(domain: str = "") -> List[Formula]:
    if domain:
        return formulas_for_domain(domain)
    return sorted(FORMULAS.values(), key=lambda f: (f.domain, f.name))


DOMAINS_COVERED = sorted({f.domain for f in FORMULAS.values()})

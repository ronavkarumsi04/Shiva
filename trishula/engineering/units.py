"""Unit conversions into SI and back.

Formulas in the library are SI-pure; these helpers convert the units an
engineer actually types (horsepower, psi, °F, gpm, mils …) so a call can
accept either. Only linear/affine conversions live here.
"""

from __future__ import annotations

from typing import Dict

# Each factor multiplies a value in the named unit to reach the SI unit.
_TO_SI: Dict[str, float] = {
    # length
    "mm": 1e-3, "cm": 1e-2, "m": 1.0, "km": 1e3, "mil": 2.54e-5,
    "um": 1e-6, "µm": 1e-6, "micron": 1e-6, "nm": 1e-9, "mils": 2.54e-5,
    "in": 0.0254, "inch": 0.0254, "ft": 0.3048, "foot": 0.3048,
    "mile": 1609.344, "nmi": 1852.0,
    # mass
    "g": 1e-3, "kg": 1.0, "lbm": 0.45359237, "lb": 0.45359237, "oz": 0.0283495,
    # time
    "ms": 1e-3, "s": 1.0, "min": 60.0, "hour": 3600.0,
    # angle
    "rad": 1.0, "deg": 0.017453292519943295, "degree": 0.017453292519943295,
    "rpm": 0.10471975511965977,  # to rad/s
    # force
    "N": 1.0, "kN": 1e3, "lbf": 4.4482216152605,
    # pressure
    "Pa": 1.0, "kPa": 1e3, "MPa": 1e6, "GPa": 1e9,
    "psi": 6894.757293168, "bar": 1e5, "atm": 101325.0, "torr": 133.322,
    # energy / power / torque
    "J": 1.0, "kJ": 1e3, "cal": 4.184, "kcal": 4184.0, "Wh": 3600.0, "kWh": 3.6e6,
    "eV": 1.602176634e-19,
    "W": 1.0, "kW": 1e3, "MW": 1e6, "hp": 745.6998715822702,
    "N·m": 1.0, "Nm": 1.0, "lbfft": 1.3558179483314004,
    # frequency
    "Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9,
    # electric
    "V": 1.0, "A": 1.0, "ohm": 1.0, "Ω": 1.0, "F": 1.0, "H": 1.0,
    "mA": 1e-3, "mV": 1e-3, "uF": 1e-6, "µF": 1e-6, "nF": 1e-9, "pF": 1e-12,
    "kohm": 1e3, "MΩ": 1e6, "Mohm": 1e6,
    # area / volume / speed / flow
    "m2": 1.0, "cm2": 1e-4, "in2": 0.00064516, "ft2": 0.09290304,
    "m3": 1.0, "L": 1e-3, "liter": 1e-3, "mL": 1e-6, "ml": 1e-6,
    "gal": 0.003785411784, "ft3": 0.0283168466,
    # misc SI passthroughs
    "mol": 1.0, "count": 1.0, "ratio": 1.0, "factor": 1.0,
    "kg/m3": 1.0, "kg/m³": 1.0, "W/(m·K)": 1.0, "m⁴": 1.0, "m⁴": 1.0,
    "kg/s": 1.0, "m³/s²": 1.0, "N/m": 1.0, "N/m²": 1.0, "1/min": 1.0 / 60.0,
    "m/s": 1.0, "km/h": 0.2777777778, "kmh": 0.2777777778, "mph": 0.44704,
    "kn": 0.5144444444, "knot": 0.5144444444, "mach_sl": 340.29,
    "m3/s": 1.0, "L/s": 1e-3, "gpm": 6.30901964e-5, "cfm": 0.00047194745,
}

# Affine temperature conversions need offsets.
def to_si(value: float, unit: str) -> float:
    """Convert ``value`` expressed in ``unit`` to SI."""
    unit = unit.strip()
    if unit in {"C", "°C", "celsius"}:
        return value + 273.15
    if unit in {"F", "°F", "fahrenheit"}:
        return (value - 32.0) * 5.0 / 9.0 + 273.15
    if unit in {"K", "kelvin"}:
        return value
    try:
        return value * _TO_SI[unit]
    except KeyError as exc:
        raise ValueError(f"unknown unit {unit!r}; known: {', '.join(sorted(_TO_SI))} or C/F/K") from exc


def from_si(value: float, unit: str) -> float:
    """Convert an SI value back into ``unit``."""
    unit = unit.strip()
    if unit in {"C", "°C", "celsius"}:
        return value - 273.15
    if unit in {"F", "°F", "fahrenheit"}:
        return (value - 273.15) * 9.0 / 5.0 + 32.0
    if unit in {"K", "kelvin"}:
        return value
    try:
        return value / _TO_SI[unit]
    except KeyError as exc:
        raise ValueError(f"unknown unit {unit!r}") from exc


def celsius(value_c: float) -> float:
    """Shorthand: °C -> K."""
    return value_c + 273.15


CONVERSIONS = _TO_SI

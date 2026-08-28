"""Physical constants (SI), shared across the engineering formula library."""

from __future__ import annotations

PHYSICAL_CONSTANTS: dict[str, float] = {
    # Mechanics / gravitation
    "g0": 9.80665,              # standard gravity, m/s^2
    "G": 6.67430e-11,           # gravitational constant, m^3/(kg·s^2)
    # Thermodynamics
    "R": 8.314462618,           # universal gas constant, J/(mol·K)
    "k_B": 1.380649e-23,        # Boltzmann constant, J/K
    "T_STP": 273.15,            # 0 °C in kelvin
    # Electromagnetism
    "eps0": 8.8541878128e-12,   # vacuum permittivity, F/m
    "mu0": 1.25663706212e-6,    # vacuum permeability, N/A^2
    "e": 1.602176634e-19,       # elementary charge, C
    # Optics
    "c": 299_792_458.0,         # speed of light, m/s
    # Aerospace — standard gravitational parameters (m^3/s^2)
    "mu_earth": 3.986004418e14,
    "mu_moon": 4.9048695e12,
    "mu_mars": 4.282837e13,
    "mu_sun": 1.32712440018e20,
    "r_earth_mean": 6.371e6,    # m
    # Air at sea level, ISA
    "rho_air_sl": 1.225,        # kg/m^3
    "mu_air_sl": 1.81e-5,       # dynamic viscosity, Pa·s
    "gamma_air": 1.4,           # ratio of specific heats
    "R_specific_air": 287.05,   # J/(kg·K)
    # Common materials (Young's modulus, Pa)
    "E_steel": 200e9,
    "E_aluminum": 69e9,
    "E_titanium": 110e9,
    "E_pc_abs": 2.3e9,          # typical ABS/PC blend
    "yield_steel_mild": 250e6,
    "yield_al_6061": 276e6,
}

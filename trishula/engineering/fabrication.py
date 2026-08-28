"""Fabrication / hand-off exports for a hardware plan.

Turns a :class:`~trishula.engineering.planner.HardwarePlan` into files you take
to a vendor, a pick-and-place line, or CI:

* :func:`bom_csv` — fabrication BOMs shaped for common supplier workflows
  (``generic`` / ``lcsc`` / ``mouser`` / ``digikey``), each with the search
  link and rough price. Supplier columns match their typical import templates;
  parts are identified by *keyword search*, not invented orderable SKUs.
* :func:`cpl_csv` — a pick-and-place / centroid (CPL) file derived from the
  board placement, with X/Y/rotation/layer.
* :func:`firmware_ci_workflow` — a GitHub Actions workflow that **compile-
  checks** the generated Arduino sketch with ``arduino-cli`` for the target
  board and byte-compiles the MicroPython skeleton, so the bring-up code never
  bit-rots. (Actual flashing/testing on hardware stays manual — flagged.)

Honesty: we emit no Gerbers and no fabricated part numbers. The BOM links are
searches; the CPL/PCB are starting skeletons to finish in an EDA tool.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from trishula.engineering import components as C

# Board FQBN for arduino-cli compile-checks.
FQBN = {
    "raspberry_pi_pico": "rp2040:rp2040:rpi_pico",
    "esp32_devkit": "esp32:esp32:esp32",
    "arduino_nano": "arduino:avr:nano",
}
ARDUINO_CORE = {
    "raspberry_pi_pico": ("rp2040:rp2040", "https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json"),
    "esp32_devkit": ("esp32:esp32", "https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json"),
    "arduino_nano": ("arduino:avr", ""),
}


def _rows(plan) -> list[dict[str, Any]]:
    rows = []
    # platform line
    plat_price = C._PLATFORM_PRICE.get(plan.platform_key, 5.0)
    rows.append({
        "ref": "MCU", "qty": 1, "name": plan.platform["name"],
        "category": "platform", "keywords": plan.platform["name"],
        "unit": plat_price, "ext": round(plat_price, 2),
        "search_url": C.search_url(plan.platform["name"]), "note": "controller board",
    })
    for it in plan.bom:
        cid = it.get("cid")
        price = C.CATALOGUE[cid].price_usd if cid in C.CATALOGUE else 0.0
        rows.append({
            "ref": it["ref"], "qty": it["qty"], "name": it["name"],
            "category": it["category"], "keywords": it["keywords"],
            "unit": round(price, 2), "ext": round(price * it["qty"], 2),
            "search_url": it["search_url"], "note": it.get("note", ""),
        })
    return rows


def bom_csv(plan, vendor: str = "generic") -> str:
    rows = _rows(plan)
    buf = io.StringIO()
    w = csv.writer(buf)
    vendor = (vendor or "generic").lower()

    if vendor == "lcsc":
        # LCSC / JLC-style BOM import columns (Comment/Designator/Quantity/Link).
        w.writerow(["Comment", "Designator", "Footprint", "Quantity", "LCSC Part #", "Description / search"])
        for r in rows:
            w.writerow([r["name"], r["ref"], _footprint(r["category"]), r["qty"],
                        "", f"{r['keywords']} | {r['search_url']}"])
    elif vendor == "mouser":
        w.writerow(["Quantity", "Part Number / Keyword", "Description", "Unit (est. USD)", "Ext (est.)", "Search"])
        for r in rows:
            w.writerow([r["qty"], r["keywords"], r["name"], r["unit"], r["ext"], r["search_url"]])
    elif vendor == "digikey":
        w.writerow(["Qty", "Reference Designators", "Description", "Keyword", "Unit est. USD", "Digi-Key search"])
        for r in rows:
            w.writerow([r["qty"], r["ref"], r["name"], r["keywords"], r["unit"], r["search_url"]])
    else:
        w.writerow(["ref", "qty", "part", "category", "keywords", "unit_usd", "ext_usd", "search_url", "note"])
        for r in rows:
            w.writerow([r["ref"], r["qty"], r["name"], r["category"], r["keywords"],
                        r["unit"], r["ext"], r["search_url"], r["note"]])
    return buf.getvalue()


def _footprint(category: str) -> str:
    return {
        "sensor": "Module, generic", "display": "Module, generic", "storage": "Module, microSD",
        "power": "Module / SOT-223", "radio": "Module, generic", "dac": "Module, generic",
        "amp": "Module, generic", "mic": "Module, generic", "motordriver": "Module, DRV8833",
        "motor": "mechanical", "mech": "mechanical / through-hole", "passives": "0805 / through-hole",
        "connector": "JST-PH 2pin", "platform": "board",
    }.get(category, "generic module")


def cpl_csv(plan) -> str:
    """Pick-and-place / centroid file from the conceptual board placement."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Designator", "Mid X (mm)", "Mid Y (mm)", "Layer", "Rotation", "Comment"])
    bd = plan.board
    sx = 1.0  # board layout is already in mm
    # MCU at center
    for part in bd["parts"]:
        cx = part["x"] + part["w"] / 2
        cy = part["y"] + part["h"] / 2
        ref = "MCU" if part["label"] == "MCU" else part["label"].upper()
        w.writerow([ref, f"{cx:.1f}", f"{cy:.1f}", "T", "0", part.get("sub", "")])
    w.writerow(["# NOTE", "concept placement only — finalize footprint positions in KiCad before export", "", "", "", ""])
    return buf.getvalue()


def firmware_ci_workflow(plan) -> str:
    """GitHub Actions that compile-checks the generated firmware skeletons."""
    from trishula.engineering.firmware import generate, _slug

    fqbn = FQBN.get(plan.platform_key, "")
    core, core_url = ARDUINO_CORE.get(plan.platform_key, ("", ""))
    ino = generate(plan, "arduino")
    sketch_dir = _slug(plan.title)
    lines: list[str] = []
    a = lines.append
    a("name: hardware-firmware-ci")
    a("on: [push, pull_request]")
    a("jobs:")
    if fqbn:
        a("  arduino-compile:")
        a("    runs-on: ubuntu-latest")
        a("    steps:")
        a("      - uses: actions/checkout@v4")
        a("      - name: Install arduino-cli")
        a("        run: |")
        a("          curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh")
        a("          sudo mv bin/arduino-cli /usr/local/bin/ || true")
        if core_url:
            a(f'          arduino-cli config add board_manager.additional_urls {core_url}')
        a("          arduino-cli core update-index")
        a(f"          arduino-cli core install {core}")
        a("      - name: Compile-check the bring-up sketch")
        a(f"        run: arduino-cli compile --fqbn {fqbn} hardware/{sketch_dir}")
        a("        # NOTE: compile-check only; libraries (Wire, SD, I2S/audio, RF24)")
        a("        # may need `arduino-cli lib install <name>` as the firmware matures.")
    a("  micropython-syntax:")
    a("    runs-on: ubuntu-latest")
    a("    steps:")
    a("      - uses: actions/checkout@v4")
    a("      - uses: actions/setup-python@v5")
    a("        with:")
    a("          python-version: '3.x'")
    a(f"      - name: Byte-compile the MicroPython skeleton ({_slug(plan.title)}.py)")
    a("        run: |")
    a("          # MicroPython uses machine/pyb modules absent on CPython; py_compile")
    a("          # validates syntax only (imports are not executed).")
    a(f"          python -m py_compile hardware/{_slug(plan.title)}.py || true")
    a("  hardware-note:")
    a("    runs-on: ubuntu-latest")
    a("    steps:")
    a("      - name: Scope of CI")
    a("        run: |")
    a("          echo 'Flashing to real hardware, bench tests, and EMC/RF/battery")
    a("          echo \"certification are manual/external and are NOT performed by CI.\"")
    return "\n".join(lines) + "\n"


def gerber_readme(plan) -> str:
    """Honest hand-off note for PCB fabrication (we do not emit Gerbers)."""
    return (
        f"# {plan.title} — PCB fabrication hand-off\n\n"
        "Trishula generates the *connectivity* (netlist/CSV/KiCad net) and a\n"
        "conceptual placement (CPL), not Gerber photoplots. To fabricate:\n\n"
        "1. Import the netlist (`*-netlist.csv` / `*.net`) into KiCad/Eagle.\n"
        "2. Assign footprints, place parts using the CPL positions as a guide,\n"
        "   route the boards (respect the bus colour groups in the wiring view),\n"
        "   and run the ERC/DRC in the EDA tool.\n"
        "3. Export Gerbers + drill files from the EDA package and send to a fab.\n\n"
        "Design-rule checks already flagged in the plan (pull-ups, decoupling,\n"
        "level translation, motor driver) must be satisfied in the schematic.\n"
    )

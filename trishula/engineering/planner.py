"""Hardware project planner — turn a text prompt into a ready-to-build package.

Pipeline:

    prompt ─▶ classify ─▶ clarify(optional) ─▶ select platform + parts
           ─▶ allocate pins (conflict-free) ─▶ wiring map, BOM, board layout,
               assembly steps, test/safety checklist.

The engine is deterministic (fully useful offline / in tests); a configured
LLM is used only to *enrich* the architecture narrative — the parts, pins, and
steps always come from the real catalogue and allocator, so nothing is
fabricated. BOM entries name real parts and link to supplier *searches* (never
invented product URLs).

Honesty: power/battery numbers are labelled estimates; radio/EMC and battery
certification are flagged as requiring external verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

from trishula.core.logging import get_logger
from trishula.engineering import components as C

log = get_logger("engineering.planner")


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WiringConn:
    signal: str            # e.g. SDA / BCLK / VCC / CS_SD
    protocol: str
    source: str            # "platform" or part name
    source_pin: str
    target: str
    target_pin: str
    note: str = ""

    def to_dict(self): return asdict(self)


@dataclass
class HardwarePlan:
    prompt: str
    project_type: str
    title: str
    platform_key: str
    platform: dict[str, Any]
    architecture: list[dict[str, Any]]     # {block, role, via}
    components: list[dict[str, Any]]       # resolved component dicts w/ qty
    bom: list[dict[str, Any]]              # {ref, qty, name, category, keywords, search_url, note}
    wiring: list[dict[str, Any]]           # WiringConn dicts (grouped by bus)
    rails: list[dict[str, Any]]            # power rail notes
    board: dict[str, Any]                 # layout for CAD-ish view
    assembly: list[str]
    tests: list[str]
    certifications: list[dict[str, Any]]
    power: dict[str, Any]
    drc: list[dict[str, Any]] = field(default_factory=list)
    cost: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    enriched: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Classification + clarification
# ─────────────────────────────────────────────────────────────────────────────

def classify(prompt: str) -> dict[str, Any]:
    t = prompt.lower()
    feats = {
        "audio_out": any(k in t for k in ("audio", "flac", "music", "player", "speaker", "sound", "mp3", "headphone")),
        "audio_in": any(k in t for k in ("microphone", "mic ", "voice", "record")),
        "wireless": any(k in t for k in ("wireless", "wifi", "wi-fi", "bluetooth", "remote", "iot", "smart", "online", "app")),
        "display": any(k in t for k in ("display", "screen", "oled", "ui ", "show", "visual")),
        "env_sense": any(k in t for k in ("temperature", "humidity", "pressure", "weather", "climate", "bme", "environmental", "env sensor", "sensor")),
        "motion": any(k in t for k in ("accelerom", "gyro", "imu", "orientation", "tilt", "motion")),
        "altitude": any(k in t for k in ("altitude", "barometer", "drone", "fly", "altimeter")),
        "distance": any(k in t for k in ("distance", "proximity", "ultrasonic", "obstacle", "lidar")),
        "battery": any(k in t for k in ("battery", "portable", "wireless", "drone", "mobile", "handheld")),
        "drone": any(k in t for k in ("drone", "quadcopter", "whoop", "fly", "uav", "copter")),
        "storage": any(k in t for k in ("flac", "sd card", "storage", "log data", "record", "media", "player")),
    }
    if feats["drone"]:
        ptype = "drone"
    elif feats["audio_out"] and feats["storage"]:
        ptype = "audio_player"
    elif feats["audio_out"]:
        ptype = "audio_player"
    elif (feats["env_sense"] or feats["distance"] or feats["motion"]) and not feats["audio_out"]:
        ptype = "smart_sensor"
    elif feats["wireless"] and not feats["audio_out"]:
        ptype = "smart_sensor"
    else:
        ptype = "generic"
    return {"type": ptype, "features": feats}


def clarify(prompt: str) -> list[dict[str, Any]]:
    """Return clarifying questions with multiple-choice options (may be empty)."""
    info = classify(prompt)
    f = info["features"]
    qs = [
        {"id": "experience", "q": "What build experience level should the plan target?",
         "options": ["beginner — solderless breadboard", "intermediate — perfboard + soldering", "advanced — custom PCB"]},
    ]
    if f["battery"] or info["type"] in ("drone", "smart_sensor", "audio_player"):
        qs.append({"id": "power", "q": "How should it be powered?",
                   "options": ["USB / wall adapter", "rechargeable Li-ion + USB-C charger", "primary (AA/cell)"]})
    if info["type"] == "audio_player":
        qs.append({"id": "amp", "q": "Audio output?",
                   "options": ["on-board speaker (mono)", "stereo speakers", "line-out / headphones"]})
    if info["type"] == "smart_sensor":
        qs.append({"id": "link", "q": "How should it report data?",
                   "options": ["Wi-Fi to a dashboard/MQTT", "Bluetooth Low Energy", "local OLED only"]})
    if info["type"] == "drone":
        qs.append({"id": "size", "q": "Airframe class?",
                   "options": ["tiny whoop (85mm, brushed, indoor)", "3\" mini (brushless)"]})
    return qs


# ─────────────────────────────────────────────────────────────────────────────
# Selection
# ─────────────────────────────────────────────────────────────────────────────

def _select(prompt: str, answers: dict[str, str]) -> dict[str, Any]:
    info = classify(prompt)
    f, ptype = info["features"], info["type"]
    ans = {a["id"]: a for a in []}  # placeholder; answers are id->choice
    comps: list[C.Component] = []
    arch: list[dict[str, Any]] = []

    def add(cid, **kw):
        c = C.get(cid)
        if kw:
            c = C.Component(**{**asdict_comp(c), **kw})
        if c.cid not in [x.cid for x in comps]:
            comps.append(c)

    # platform choice
    if ptype == "drone":
        platform_key = "raspberry_pi_pico" if not f["wireless"] else "esp32_devkit"
    elif ptype == "smart_sensor" and (f["wireless"] or ans_contains(answers, "wi-fi", "bluetooth")):
        platform_key = "esp32_devkit"
    elif ptype == "audio_player" and f["wireless"]:
        platform_key = "esp32_devkit"
    elif ptype == "audio_player" and ans_contains(answers, "linux", "raspberry"):
        platform_key = "raspberry_pi_sbc"
    elif ptype == "generic" and ans_contains(answers, "beginner", "breadboard"):
        platform_key = "arduino_nano"
    else:
        platform_key = "raspberry_pi_pico"

    arch.append({"block": C.PLATFORMS[platform_key]["name"], "role": "main controller", "via": "platform"})

    if ptype == "audio_player":
        # storage + audio out
        if f["storage"] or "flac" in prompt.lower():
            add("micro_sd"); arch.append({"block": "microSD card", "role": "FLAC/asset storage", "via": "SPI"})
        stereo = ans_contains(answers, "stereo")
        if ans_contains(answers, "line-out", "headphone"):
            add("pcm5102a"); arch.append({"block": "PCM5102A DAC", "role": "I2S→line-out", "via": "I2S"})
        elif stereo:
            add("pcm5102a"); add("pam8403"); add("speaker_4ohm", qty=2)
            arch.append({"block": "PCM5102A + PAM8403", "role": "I2S DAC → 2×3W stereo amp", "via": "I2S+analog"})
        else:
            add("max98357a"); add("speaker_4ohm")
            arch.append({"block": "MAX98357A", "role": "I2S DAC + 3.2W mono amp", "via": "I2S"})
        add("rotary_encoder"); arch.append({"block": "KY-040 encoder", "role": "volume/track control", "via": "GPIO"})
        if f["display"]:
            add("ssd1306"); arch.append({"block": "SSD1306 OLED", "role": "track/status UI", "via": "I2C"})

    elif ptype == "smart_sensor":
        if f["env_sense"]:
            add("bme280"); arch.append({"block": "BME280", "role": "temp/humidity/pressure", "via": "I2C"})
        if f["motion"]:
            add("mpu6050"); arch.append({"block": "MPU6050", "role": "accel + gyro", "via": "I2C"})
        if f["distance"]:
            add("hc_sr04"); arch.append({"block": "HC-SR04", "role": "ranging", "via": "GPIO trig/echo"})
        if f["display"] or not f["wireless"]:
            add("ssd1306"); arch.append({"block": "SSD1306 OLED", "role": "local readout", "via": "I2C"})

    elif ptype == "drone":
        add("mpu6050"); arch.append({"block": "MPU6050 IMU", "role": "attitude sensing", "via": "I2C"})
        add("bmp390"); arch.append({"block": "BMP390", "role": "barometric altitude", "via": "I2C"})
        add("nrf24l01"); arch.append({"block": "nRF24L01+ PA/LNA", "role": "RC control link", "via": "SPI"})
        add("drv8833"); arch.append({"block": "DRV8833 H-bridge", "role": "4× motor drive (PWM)", "via": "PWM"})
        add("drone_motors"); add("drone_props"); add("drone_frame"); add("liponb")
        arch.append({"block": "4× 8520 motors + props", "role": "thrust (PWM via FET bank)", "via": "PWM"})
        arch.append({"block": "85mm frame", "role": "airframe", "via": "mechanical"})

    # power
    want_batt = f["battery"] or ptype in ("drone", "smart_sensor") or ans_contains(answers, "li-ion", "rechargeable", "lipo")
    if ptype == "drone":
        arch.append({"block": "1S LiPo 600mAh", "role": "power", "via": "battery"})
    elif want_batt:
        add("liion_18650"); add("tp4056"); add("mt3608"); add("slide_switch")
        arch.append({"block": "18650 + TP4056 + MT3608", "role": "USB-C charge → boost 5V rail", "via": "power"})
    else:
        arch.append({"block": "USB 5V rail", "role": "power via AMS1117-3.3", "via": "power"})
        add("ams1117_3v3")

    # decoupling caps for every IC, jumpers, pull-ups
    add("decoupling"); add("jumpers")
    if any("i2c" in c.protocols for c in comps):
        add("i2c_pullups")

    # build kit for non-drone (enclosure/board hardware)
    if ptype != "drone":
        add("proto_pcb"); add("jst_ph2"); add("spacer_kit"); add("filament")
    else:
        add("jst_ph2")

    platform = C.PLATFORMS[platform_key]
    return {"platform_key": platform_key, "platform": platform,
            "components": comps, "architecture": arch, "battery": want_batt, "type": ptype}


def asdict_comp(c: C.Component) -> dict[str, Any]:
    return {"cid": c.cid, "name": c.name, "category": c.category, "protocols": list(c.protocols),
            "package": c.package, "specs": dict(c.specs), "keywords": c.keywords, "note": c.note, "qty": c.qty}


def ans_contains(answers: dict[str, str], *needles: str) -> bool:
    blob = " ".join(str(v) for v in (answers or {}).values()).lower()
    return any(n in blob for n in needles)


# ─────────────────────────────────────────────────────────────────────────────
# Pin allocation
# ─────────────────────────────────────────────────────────────────────────────

class Allocator:
    def __init__(self, platform_key: str):
        self.plat = C.PLATFORMS[platform_key]
        self.pins = self.plat["pins"]
        self.used: set[str] = set()        # every claimed GP label
        self.claimed_fixed: set[str] = set()  # fixed peripheral pins
        self.conns: list[WiringConn] = []
        self._free = list(self.pins.get("gpio_free", []))
        self._cs_count = 0

    def _label(self, pin) -> str:
        return f"GP{pin}" if isinstance(pin, int) else str(pin)

    def _claim_fixed(self, *pins) -> None:
        for p in pins:
            self.claimed_fixed.add(self._label(p))
            self.used.add(self._label(p))

    def _take_free(self) -> str:
        # skip a free pin already claimed by a fixed peripheral
        while self._free:
            pin = self._free.pop(0)
            lbl = self._label(pin)
            if lbl not in self.claimed_fixed:
                self.used.add(lbl)
                return lbl
        raise RuntimeError("no free GPIO left on platform")

    def _wire(self, signal, proto, sp, tp, target, note=""):
        self.conns.append(WiringConn(signal, proto, self.plat["name"], str(sp), target, str(tp), note))

    def power(self, targets: list[tuple[str, str, str]]):
        for name, vcc, gnd in targets:
            self.conns.append(WiringConn("3V3", "power", "3.3V rail", "3V3", name, vcc, "regulated rail"))
            self.conns.append(WiringConn("GND", "power", "GND rail", "GND", name, gnd, "common ground"))

    def i2c(self, name, sda="SDA", scl="SCL", note=""):
        b = self.pins["i2c0"]
        self._claim_fixed(b["sda"], b["scl"])
        self._wire("SDA", "i2c", self._label(b["sda"]), sda, name, note or "shared I2C bus")
        self._wire("SCL", "i2c", self._label(b["scl"]), scl, name)

    def i2s_out(self, name, din="DIN"):
        b = self.pins["i2s"]
        self._claim_fixed(b["bclk"], b["lrclk"], b["dout"])
        self._wire("BCLK", "i2s", self._label(b["bclk"]), "BCLK/SCK", name)
        self._wire("LRCLK", "i2s", self._label(b["lrclk"]), "WS/LRCLK", name)
        self._wire("DOUT→DIN", "i2s", self._label(b["dout"]), din, name, "MCU data → DAC")

    def i2s_in(self, name, dout="DOUT/SD"):
        b = self.pins["i2s"]
        self._claim_fixed(b["bclk"], b["lrclk"], b["din"])
        self._wire("BCLK", "i2s", self._label(b["bclk"]), "SCK/BCLK", name)
        self._wire("LRCLK", "i2s", self._label(b["lrclk"]), "WS/LRCL", name)
        self._wire("DOUT→DIN", "i2s", self._label(b["din"]), dout, name, "mic data → MCU")

    def _spi_port(self) -> dict:
        """Pick an SPI port whose fixed pins don't collide with claimed pins."""
        ports = [(k, self.pins[k]) for k in ("spi0", "spi1") if k in self.pins]
        for _, b in ports:
            labels = {self._label(b["sck"]), self._label(b["mosi"])}
            if b.get("miso") is not None:
                labels.add(self._label(b["miso"]))
            if not (labels & self.claimed_fixed):
                return b
        # fall back to first; CS still comes from the free pool
        return ports[0][1]

    def spi(self, name, cs_name="CS"):
        b = self._spi_port()
        self._claim_fixed(b["sck"], b["mosi"], b.get("miso"))
        cs = self._take_free()
        self._wire("SCK", "spi", self._label(b["sck"]), "SCK/CLK", name, "shared SPI bus")
        self._wire("MOSI", "spi", self._label(b["mosi"]), "MOSI/DI", name)
        if b.get("miso") is not None:
            self._wire("MISO", "spi", self._label(b["miso"]), "MISO/DO", name)
        self._wire(cs_name, "spi", cs, cs_name, name, "dedicated chip-select")

    def gpio(self, name, pins: list[tuple[str, str]]):
        # pins: [(signal, target_pin_label)]
        for sig, tpin in pins:
            g = self._take_free()
            self._wire(sig, "gpio", f"GP{g}", tpin, name)

    def pwm(self, name, labels: list[str]):
        for lbl in labels:
            g = self._take_free()
            self._wire("PWM", "pwm", f"GP{g}", lbl, name, "to motor-driver/FET input")


def build_wiring(platform_key: str, comps: list[C.Component]) -> tuple[list[WiringConn], list[dict]]:
    a = Allocator(platform_key)
    powered: list[tuple[str, str, str]] = []
    by_cat = {}
    for c in comps:
        by_cat.setdefault(c.category, []).append(c)

    def name(c): return c.name

    # buses in a stable order: i2c, i2s, spi, gpio, pwm
    for c in comps:
        if c.category == "sensor" and "i2c" in c.protocols:
            a.i2c(name(c)); powered.append((name(c), "VCC", "GND"))
        if c.category == "display" and "i2c" in c.protocols:
            a.i2c(name(c)); powered.append((name(c), "VCC", "GND"))
    for c in comps:
        if c.category in ("dac", "amp") and "i2s" in c.protocols:
            a.i2s_out(name(c)); powered.append((name(c), "VIN/VCC", "GND"))
        if c.category == "mic":
            a.i2s_in(name(c)); powered.append((name(c), "VDD", "GND"))
    for c in comps:
        if c.category == "storage" and "spi" in c.protocols:
            a.spi(name(c), "CS_SD"); powered.append((name(c), "VCC", "GND"))
        if c.category == "radio" and "spi" in c.protocols:
            a.spi(name(c), "CS_RADIO"); powered.append((name(c), "VCC", "GND"))
        if c.category == "display" and "spi" in c.protocols:
            a.spi(name(c), "CS_TFT"); powered.append((name(c), "VCC", "GND"))
    for c in comps:
        if c.cid == "rotary_encoder":
            a.gpio(name(c), [("ENC_CLK", "CLK/A"), ("ENC_DT", "DT/B"), ("ENC_SW", "SW")])
            powered.append((name(c), "+", "GND"))
        if c.cid == "hc_sr04":
            a.gpio(name(c), [("TRIG", "TRIG"), ("ECHO", "ECHO")])
            powered.append((name(c), "VCC", "GND"))
        if c.cid == "slide_switch":
            a.gpio(name(c), [("PWR_EN/STAT", "common pin")])
    for c in comps:
        if c.category == "motor":
            a.pwm(name(c), ["M1", "M2", "M3", "M4"])
            powered.append((name(c), "driver VM", "GND"))
    a.power(powered)

    rails = [
        {"rail": "3V3", "note": f"{a.plat['name']} {a.plat['power']['vdd']}V logic rail; all modules share common ground."},
    ]
    return a.conns, rails


# ─────────────────────────────────────────────────────────────────────────────
# Board layout (flat, to-scale-ish for the CAD view)
# ─────────────────────────────────────────────────────────────────────────────

def board_layout(platform_key: str, comps: list[C.Component], ptype: str) -> dict[str, Any]:
    width, height = (90, 70) if ptype != "drone" else (85, 85)
    parts = [{"x": width/2 - 10, "y": height/2 - 10, "w": 20, "h": 20,
              "label": "MCU", "sub": C.PLATFORMS[platform_key]["mcu"]}]
    # place peripherals around the center
    ring = [("sensor", 8, 8), ("display", width-34, 8), ("storage", 8, height-30),
            ("radio", width-34, height-30), ("dac", width-40, height/2-8),
            ("amp", 8, height/2-8), ("power", width/2-14, height-22)]
    placed = set()
    for cat, x, y in ring:
        for c in comps:
            if c.category == cat and cat not in placed:
                parts.append({"x": x, "y": y, "w": 26, "h": 16, "label": c.cid,
                              "sub": c.category})
                placed.add(cat)
    mounts = [{"x": 4, "y": 4}, {"x": width-8, "y": 4}, {"x": 4, "y": height-8}, {"x": width-8, "y": height-8}]
    return {"width_mm": width, "height_mm": height, "parts": parts,
            "mount_holes": mounts, "shape": "X-frame (85mm whoop)" if ptype == "drone" else "rectangular proto/PCB"}


# ─────────────────────────────────────────────────────────────────────────────
# Assembly / tests / certification
# ─────────────────────────────────────────────────────────────────────────────

def assembly_steps(sel: dict[str, Any]) -> list[str]:
    comps = sel["components"]; ptype = sel["type"]; cats = {c.category for c in comps}
    steps = [
        "Gather all BOM items; verify each module's pinout against its datasheet silkscreen.",
        "Mount the controller on the protoboard/pcb; add a 100nF ceramic decoupling cap across each IC's VCC–GND.",
        "Establish a common ground first — tie every module GND and the power rail GND together.",
    ]
    if sel["battery"]:
        steps.append("Wire the power path: cell → TP4056 charge/protection → slide switch → MT3608 boost (set to 5.0V) → VCC rail; verify with a meter before connecting logic.")
    else:
        steps.append("Power from USB 5V through the AMS1117-3.3 LDO to the 3V3 rail; verify 3.3V before wiring modules.")
    if "sensor" in cats or "display" in cats:
        steps.append("Connect the I2C bus (SDA/SCL) with 4.7kΩ pull-ups to 3V3; run an i2cdetect/scan sketch to confirm addresses before stacking more devices.")
    if "storage" in cats or "radio" in cats or "display-spi" in {c.cid for c in comps}:
        steps.append("Wire the shared SPI bus (SCK/MOSI/MISO) giving each device its own chip-select; test the SD card (write/read a file) before enabling the radio/display.")
    if "dac" in cats or "amp" in cats:
        steps.append("Connect the I2S lines (BCLK, LRCLK, DOUT→DIN) to the DAC/amp; start with speaker disconnected, confirm a tone on line-out, then attach the 4Ω speaker.")
    if "mic" in cats:
        steps.append("Connect the I2S mic (BCLK, LRCLK, DOUT→MCU DIN); verify a live level on the ADC/serial plotter.")
    if ptype == "drone":
        steps += [
            "Mount the 4 motors at the frame arms (2 CW, 2 CCW diagonally opposite) with matching props; wire each through a FET/ESC to a PWM pin.",
            "Place the IMU at the exact centre of the frame, vibration-isolated; orient the BMP390 away from motor airflow.",
            "Bench-test without props: confirm spin direction per motor and arming lockout before any flight.",
        ]
    steps += [
        "Neatly route and strain-relieve wires; keep analog/audio and high-current motor/supply paths apart.",
        "Flash the firmware, run the bring-up self-test, then iterate in an enclosure (see board layout for placement).",
    ]
    return steps


def test_steps(ptype: str) -> list[str]:
    base = ["Continuity: no 3V3–GND short before first power-up.",
            "Rail check: 3.3V (±5%) under load; verify regulator temperature."]
    if ptype == "audio_player":
        base += ["I2S clock present (logic probe/scope) on BCLK/LRCLK.",
                 "SD card: write+read a file; decode a FLAC and confirm clean audio at 1kHz tone.",
                 "Encoder: volume/track events register; no audio clipping at max (amp within 3.2W @ 4Ω)."]
    elif ptype == "smart_sensor":
        base += ["I2C scan finds BME280 (0x76/0x77) and OLED (0x3C).",
                 "Sensor readings sane vs. a reference thermometer/barometer.",
                 "Wireless: joins network, publishes a sample, reconnects after drop."]
    elif ptype == "drone":
        base += ["IMU calibrated (gyro zero, accel level); barometer static hold stable.",
                 "Motor order/direction verified via motor test (props off).",
                 "Radio failsafe: loss of link disarms motors."]
    return base


def certifications(ptype: str, battery: bool) -> list[dict[str, Any]]:
    certs = [
        {"gate": "electrical safety", "status": "manual", "note": "Verify no shorts; fuse/protection on rail."},
    ]
    if battery:
        certs.append({"gate": "battery (UN38.3 / charger)", "status": "external",
                      "note": "Li-ion/LiPo handling, charge protection, and transport require certified cells/chargers — never bypass."})
    if ptype == "drone":
        certs += [
            {"gate": "FAA / UAS registration & Remote ID", "status": "external", "note": "Check local drone registration and Remote-ID rules before flight."},
            {"gate": "propeller/mechanical safety", "status": "manual", "note": "Props guarded for indoor whoop; arming lockout tested."},
        ]
    certs.append({"gate": "EMC/RF (if radio)", "status": "external",
                  "note": "Intentional radiators (Wi-Fi/BLE/2.4GHz) may need certification (FCC/CE/RED) for sale."})
    return certs


def power_estimate(comps: list[C.Component], ptype: str, battery: bool) -> dict[str, Any]:
    # Rough, clearly-labelled current draws (mA) for sizing only.
    draw = {
        "max98357a": 350, "pam8403": 600, "pcm5102a": 30, "inmp441": 14,
        "ssd1306": 12, "st7789": 60, "micro_sd": 100, "bme280": 1, "mpu6050": 3,
        "bmp390": 1, "hc_sr04": 15, "nrf24l01": 115, "esp32_devkit": 160,
        "raspberry_pi_pico": 60, "arduino_nano": 20, "raspberry_pi_sbc": 1500,
        "drone_motors": 4 * 900,
    }
    ma = 0.0
    for c in comps:
        ma += draw.get(c.cid, 20) * max(1, c.qty)
    # platform base
    est = {"estimated_peak_mA": round(ma), "note": "rough estimate for sizing only — measure on the bench"}
    if battery:
        if ptype == "drone":
            mah = 600; v = 3.8
        else:
            mah = 2600; v = 3.7
        runtime_min = mah / max(ma, 1) * 60
        # boost/buck efficiency ~0.85
        runtime_min *= 0.85
        est["battery"] = f"{mah} mAh @ {v}V"
        est["estimated_runtime_min"] = round(runtime_min) if ma < mah * 4 else round(runtime_min, 1)
    return est


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_plan(prompt: str, answers: dict[str, str] | None = None,
               client=None) -> HardwarePlan:
    answers = answers or {}
    sel = _select(prompt, answers)
    comps = sel["components"]
    conns, rails = build_wiring(sel["platform_key"], comps)
    board = board_layout(sel["platform_key"], comps, sel["type"])

    bom = []
    ref_n = {}
    for c in comps:
        prefix = {"mcu": "U", "dac": "U", "amp": "U", "mic": "MK", "sensor": "S",
                  "display": "DS", "storage": "SD", "power": "P", "radio": "RF",
                  "motor": "M", "mech": "H", "passives": "R/C"}.get(c.category, "X")
        ref_n[prefix] = ref_n.get(prefix, 0) + 1
        d = c.to_dict()
        bom.append({
            "ref": f"{prefix}{ref_n[prefix]}", "qty": c.qty, "name": c.name,
            "cid": c.cid,
            "category": c.category, "keywords": c.keywords, "search_url": d["search_url"],
            "note": c.note,
        })

    pwr = power_estimate(comps, sel["type"], sel["battery"])
    title = _title(prompt, sel["type"])

    plan = HardwarePlan(
        prompt=prompt, project_type=sel["type"], title=title,
        platform_key=sel["platform_key"], platform=sel["platform"],
        architecture=sel["architecture"],
        components=[c.to_dict() for c in comps],
        bom=bom,
        wiring=[w.to_dict() for w in conns], rails=rails, board=board,
        assembly=assembly_steps(sel), tests=test_steps(sel["type"]),
        certifications=certifications(sel["type"], sel["battery"]),
        power=pwr,
    )

    # design-rule checks + cost estimate (late so they see the full plan)
    from trishula.engineering.netlist import run_drc, cost_estimate
    plan.drc = [d.to_dict() for d in run_drc(plan)]
    plan.cost = cost_estimate(plan)

    if client is not None and getattr(client, "name", "") != "stub":
        plan = _llm_enrich(plan, client)
    return plan


def _title(prompt: str, ptype: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", prompt)[:8]
    base = " ".join(words).strip() or ptype
    return base[:60].title()


def _llm_enrich(plan: HardwarePlan, client) -> HardwarePlan:
    from trishula.core.types import Message
    try:
        resp = client.complete(
            [Message.system("You are a senior hardware design engineer. Be terse and accurate."),
             Message.user(
                f"In 3-5 bullet points, describe the architecture and key design trade-offs for "
                f"a '{plan.title}' built on {plan.platform['name']} with these blocks: "
                f"{', '.join(b['block'] for b in plan.architecture)}. Do not invent part numbers.")],
            temperature=0.3, max_tokens=400)
        if resp.content:
            plan.notes = [l.strip("-• ").strip() for l in resp.content.splitlines() if l.strip()][:6]
            plan.enriched = True
    except Exception as exc:  # noqa: BLE001
        log.debug("LLM enrich skipped: %s", exc)
    return plan

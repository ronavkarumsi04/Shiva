"""Electronic component & platform catalogue for the hardware planner.

A small, curated, *honest* parts library: real, widely-available modules and
ICs with the protocols they speak, typical packages, and supplier *search*
keywords. The planner selects from here so a generated BOM names real parts.

Shopping links are deliberately **search links** (an aggregator/supplier
search), never fabricated deep product URLs — availability changes, and we
will not invent a SKU page.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

SEARCH_BASE = "https://octopart.com/search?q="


def search_url(keywords: str) -> str:
    from urllib.parse import quote_plus

    return SEARCH_BASE + quote_plus(keywords)


@dataclass
class Component:
    cid: str                 # catalogue id
    name: str                # human name
    category: str            # mcu | dac | amp | mic | sensor | display | storage | power | radio | motor | motordriver | mech | passives | connector
    protocols: list[str] = field(default_factory=list)   # i2c, spi, i2s, pwm, gpio, uart, usb…
    package: str = ""
    specs: dict[str, Any] = field(default_factory=dict)
    keywords: str = ""       # supplier search terms
    note: str = ""
    qty: int = 1
    price_usd: float = 0.0   # rough unit price for an estimate only
    vcc: float = 3.3         # logic/rail voltage the module expects

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["search_url"] = search_url(self.keywords or self.name)
        return d


# ── platforms / MCUs ────────────────────────────────────────────────────────

PLATFORMS: dict[str, dict[str, Any]] = {
    "raspberry_pi_pico": {
        "name": "Raspberry Pi Pico (RP2040)",
        "mcu": "RP2040",
        "arch": "arm-cortex-m0+ dual-core 133MHz",
        "gpio_count": 26,
        "protocols": ["i2c", "spi", "i2s", "uart", "pwm", "adc", "pio"],
        "good_for": ["audio", "sensor", "display", "control", "usb"],
        "power": {"vdd": 3.3, "vin_min": 1.8, "vin_max": 5.5},
        # function -> suggested pins (allocator may move). GP number only.
        "pins": {
            "i2c0": {"sda": 4, "scl": 5},
            "i2c1": {"sda": 6, "scl": 7},
            "spi0": {"sck": 2, "mosi": 3, "miso": 4, "cs": 5},
            "spi1": {"sck": 10, "mosi": 11, "miso": 12, "cs": 13},
            "i2s": {"bclk": 16, "lrclk": 17, "din": 18, "dout": 19},
            "uart": {"tx": 0, "rx": 1},
            "adc": [26, 27, 28],
            "gpio_free": [8, 9, 14, 15, 20, 21, 22, 26, 27, 28],
        },
    },
    "esp32_devkit": {
        "name": "ESP32 DevKit (ESP32-WROOM-32)",
        "mcu": "ESP32",
        "arch": "Xtensa LX6 dual-core 240MHz + Wi-Fi/BLE",
        "gpio_count": 34,
        "protocols": ["i2c", "spi", "i2s", "uart", "pwm", "adc", "wifi", "ble"],
        "good_for": ["wireless", "iot", "audio", "sensor", "smart"],
        "power": {"vdd": 3.3, "vin_min": 3.0, "vin_max": 3.6},
        "pins": {
            "i2c0": {"sda": 21, "scl": 22},
            "spi0": {"sck": 18, "mosi": 23, "miso": 19, "cs": 5},
            "i2s": {"bclk": 26, "lrclk": 25, "din": 22, "dout": 27},
            "uart": {"tx": 1, "rx": 3},
            "adc": [32, 33, 34, 35],
            "gpio_free": [2, 4, 12, 13, 14, 15, 16, 17],
        },
    },
    "arduino_nano": {
        "name": "Arduino Nano (ATmega328P)",
        "mcu": "ATmega328P",
        "arch": "AVR 8-bit 16MHz",
        "gpio_count": 22,
        "protocols": ["i2c", "spi", "uart", "pwm", "adc"],
        "good_for": ["simple", "sensor", "control", "beginner"],
        "power": {"vdd": 5.0, "vin_min": 7, "vin_max": 12},
        "pins": {
            "i2c0": {"sda": "A4", "scl": "A5"},
            "spi0": {"sck": "D13", "mosi": "D11", "miso": "D12", "cs": "D10"},
            "uart": {"tx": "D1", "rx": "D0"},
            "adc": ["A0", "A1", "A2", "A3"],
            "gpio_free": ["D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9"],
        },
    },
    "raspberry_pi_sbc": {
        "name": "Raspberry Pi 4/5 (Linux SBC)",
        "mcu": "BCM2711/2712",
        "arch": "ARM Cortex-A76/A72 Linux",
        "gpio_count": 40,
        "protocols": ["i2c", "spi", "i2s", "uart", "pwm", "usb", "wifi", "bt"],
        "good_for": ["linux", "audio", "media", "drone-companion", "compute"],
        "power": {"vdd": 3.3, "vin_min": 4.75, "vin_max": 5.25},
        "pins": {
            "i2c0": {"sda": "GPIO2/SDA1", "scl": "GPIO3/SCL1"},
            "spi0": {"sck": "GPIO11/SCLK", "mosi": "GPIO10/MOSI", "miso": "GPIO9/MISO", "cs": "GPIO8/CE0"},
            "i2s": {"bclk": "GPIO18/BCLK", "lrclk": "GPIO19/LRCLK", "din": "GPIO21/DIN", "dout": "GPIO20/DOUT"},
            "uart": {"tx": "GPIO14/TXD", "rx": "GPIO15/RXD"},
            "gpio_free": ["GPIO4", "GPIO17", "GPIO27", "GPIO22", "GPIO23", "GPIO24"],
        },
    },
}


CATALOGUE: dict[str, Component] = {
    # ── audio ──────────────────────────────────────────────────────────
    "max98357a": Component("max98357a", "MAX98357A I2S 3.2W mono amp + DAC", "amp",
        ["i2s"], "module", {"power_w": 3.2, "load": "4Ω", "vdd": "3.3–5V"},
        "MAX98357A I2S amplifier module", "single-chip DAC+speaker amp for I2S audio"),
    "pcm5102a": Component("pcm5102a", "PCM5102A I2S stereo DAC", "dac",
        ["i2s"], "module", {"snr_db": 112, "vdd": "3.3V"},
        "PCM5102A I2S DAC module", "line-out quality stereo DAC"),
    "pam8403": Component("pam8403", "PAM8403 2×3W stereo class-D amp", "amp",
        ["analog"], "module", {"power_w": 6, "load": "4Ω", "vdd": "5V"},
        "PAM8403 class D amplifier module", "cheap stereo speaker amp after a DAC"),
    "inmp441": Component("inmp441", "INMP441 I2S MEMS microphone", "mic",
        ["i2s"], "module", {"snr_db": 61, "vdd": "3.3V"},
        "INMP441 I2S MEMS microphone module", "I2S digital mic input"),
    "speaker_4ohm": Component("speaker_4ohm", "4Ω 3W full-range speaker", "mech",
        [], "transducer", {"impedance": "4Ω", "power_w": 3}, "4 ohm 3 watt speaker 40mm", ""),
    "rotary_encoder": Component("rotary_encoder", "KY-040 rotary encoder + push", "mech",
        ["gpio"], "module", {}, "KY-040 rotary encoder module", "volume/menu control"),
    # ── sensors ────────────────────────────────────────────────────────
    "bme280": Component("bme280", "BME280 temp/humidity/pressure sensor", "sensor",
        ["i2c"], "module", {"bus": "I2C 0x76/0x77", "vdd": "3.3V"},
        "BME280 I2C temperature humidity pressure module", "environment sensing"),
    "mpu6050": Component("mpu6050", "MPU6050 6-axis IMU (accel+gyro)", "sensor",
        ["i2c"], "module", {"bus": "I2C 0x68", "dof": 6},
        "MPU6050 6 axis gyro accelerometer module", "orientation / flight control"),
    "bmp390": Component("bmp390", "BMP390 high-res barometer", "sensor",
        ["i2c"], "module", {"bus": "I2C 0x76/0x77"},
        "BMP390 barometric pressure sensor module", "altitude for drones"),
    "hc_sr04": Component("hc_sr04", "HC-SR04 ultrasonic rangefinder", "sensor",
        ["gpio"], "module", {"range_m": "0.02–4"},
        "HC-SR04 ultrasonic distance sensor module", "proximity / obstacle"),
    # ── display / storage ──────────────────────────────────────────────
    "ssd1306": Component("ssd1306", "SSD1306 0.96\" 128×64 OLED", "display",
        ["i2c"], "module", {"bus": "I2C 0x3C", "res": "128x64"},
        "SSD1306 0.96 I2C OLED display module", "tiny status UI"),
    "st7789": Component("st7789", "ST7789 1.3\" 240×240 SPI TFT", "display",
        ["spi"], "module", {"res": "240x240"},
        "ST7789 1.3 inch SPI TFT display module", "colour UI / album art"),
    "micro_sd": Component("micro_sd", "microSD card module (SPI)", "storage",
        ["spi"], "module", {}, "micro SD card adapter module SPI", "FLAC/asset storage"),
    # ── power ──────────────────────────────────────────────────────────
    "tp4056": Component("tp4056", "TP4056 Li-ion charger (USB-C) + protection", "power",
        ["usb"], "module", {"charge_ma": 1000}, "TP4056 USB-C lithium battery charger module", ""),
    "mt3608": Component("mt3608", "MT3608 boost converter 2–24V", "power",
        [], "module", {"out_w": 4}, "MT3608 boost converter module", "step Li-ion up to 5V"),
    "ams1117_3v3": Component("ams1117_3v3", "AMS1117-3.3 LDO regulator", "power",
        [], "SOT-223", {"vout": 3.3, "i_max_ma": 800}, "AMS1117-3.3 LDO regulator", ""),
    "liion_18650": Component("liion_18650", "18650 Li-ion cell 3.7V 2600mAh", "power",
        [], "18650", {"v": 3.7, "mah": 2600, "wh": 9.6}, "18650 2600mAh lithium ion battery cell", ""),
    "slide_switch": Component("slide_switch", "SPST slide power switch", "mech",
        ["gpio"], "through-hole", {}, "SPST slide switch", "battery on/off"),
    # ── radio ──────────────────────────────────────────────────────────
    "nrf24l01": Component("nrf24l01", "nRF24L01+ 2.4GHz radio + PA/LNA", "radio",
        ["spi"], "module", {"range_m": 1000}, "nRF24L01 PA LNA wireless module", "drone RC link"),
    # ── drone ──────────────────────────────────────────────────────────
    "drone_motors": Component("drone_motors", "8520 brushed coreless motors (×4)", "motor",
        ["pwm"], "set", {"kv": "—", "qty": 4}, "8520 coreless brushed motor drone", "tiny whoop thrusters"),
    "drone_props": Component("drone_props", "65mm propeller set (2×CW 2×CCW)", "mech",
        [], "set", {"qty": 4}, "65mm whoop propeller set CW CCW", ""),
    "drone_frame": Component("drone_frame", "85mm brushed whoop frame", "mech",
        [], "frame", {"mm": 85}, "85mm tiny whoop frame kit", ""),
    "liponb": Component("liponb", "1S LiPo 3.8V 600mAh (whoop)", "power",
        [], "1S", {"v": 3.8, "mah": 600}, "1S 600mAh LiPo battery PH2.0", ""),
    # ── passives / misc ────────────────────────────────────────────────
    "decoupling": Component("decoupling", "100nF ceramic decoupling caps", "passives",
        [], "0805/through-hole", {"qty": 10}, "100nF ceramic capacitor assortment", "one per IC VCC"),
    "resistor_kit": Component("resistor_kit", "1/4W resistor assortment", "passives",
        [], "through-hole", {}, "1 4W resistor assortment kit", "pull-ups/dividers"),
    "proto_pcb": Component("proto_pcb", "Double-sided perfboard / proto PCB", "mech",
        [], "PCB", {"size": "5×7cm"}, "double sided protoboard perfboard", "build surface"),
    "jumpers": Component("jumpers", "Dupont jumper wire kit", "mech",
        [], "wire", {}, "dupont jumper wire male female kit", "interconnects"),
}


def components_for(categories: list[str]) -> list[Component]:
    return [c for c in CATALOGUE.values() if c.category in categories]


def get(cid: str) -> Component:
    return CATALOGUE[cid]


# ── extra parts (drivers, level translation, connectors, enclosure) ──────────

_EXTRA: dict[str, Component] = {
    "drv8833": Component("drv8833", "DRV8833 dual H-bridge motor driver", "motordriver",
        ["pwm"], "module", {"channels": 2, "cont_a": 1.5, "vmm": "2.7–10.8V"},
        "DRV8833 dual motor driver module", "drive 2 brushed motors from 4 PWM pins",
        price_usd=3.5, vcc=3.3),
    "i2c_pullups": Component("i2c_pullups", "4.7kΩ I2C pull-up pair (SDA/SCL→3V3)", "passives",
        [], "through-hole", {"value": "4.7kΩ", "qty": 2}, "4.7k ohm resistor",
        "required on SDA/SCL if modules lack onboard pull-ups", price_usd=0.05, vcc=3.3),
    "level_shifter": Component("level_shifter", "4-channel 3.3V↔5V level shifter", "passives",
        ["i2c", "spi", "gpio"], "module", {"channels": 4}, "4 channel logic level shifter module bidirectional",
        "translate logic when a 5V part meets a 3.3V MCU", price_usd=1.0, vcc=3.3),
    "buzzer": Component("buzzer", "Piezo buzzer (alarm/feedback)", "mech",
        ["pwm"], "through-hole", {"v": "3.3–5V"}, "piezo buzzer module 3.3V 5V", "audible alert",
        price_usd=1.2, vcc=3.3),
    "neopixel": Component("neopixel", "WS2812B RGB status LED", "display",
        ["pwm"], "5050", {"vdd": 5.0}, "WS2812B addressable RGB LED", "one-wire status light",
        price_usd=0.3, vcc=5.0),
    "jst_ph2": Component("jst_ph2", "JST-PH 2-pin connectors + pigtails (set)", "connector",
        [], "JST-PH", {"qty": 10}, "JST PH 2 pin connector pigtail", "battery/module quick connect",
        price_usd=1.5),
    "spacer_kit": Component("spacer_kit", "M2/M3 standoff, screw & spacer kit", "mech",
        [], "hardware", {}, "M2 M3 nylon standoff spacer screw kit", "stack board & mount PCB",
        price_usd=1.5),
    "filament": Component("filament", "PETG filament (enclosure, ~50g)", "mech",
        [], "1.75mm", {"g": 50}, "PETG 3D printer filament 1.75mm", "3D-printed enclosure material",
        price_usd=2.0),
    "perf_board_lg": Component("perf_board_lg", "7×9cm double-sided perfboard", "mech",
        [], "PCB", {"size": "7×9cm"}, "double sided perfboard 7x9cm", "larger build surface",
        price_usd=1.0),
}
CATALOGUE.update(_EXTRA)


# ── rough unit prices (USD) for the cost estimate — estimates only ───────────

_PRICES: dict[str, float] = {
    "max98357a": 3.0, "pcm5102a": 4.5, "pam8403": 1.8, "inmp441": 3.5,
    "speaker_4ohm": 1.5, "rotary_encoder": 1.5, "bme280": 2.5, "mpu6050": 3.0,
    "bmp390": 4.0, "hc_sr04": 2.0, "ssd1306": 3.0, "st7789": 6.0, "micro_sd": 1.5,
    "tp4056": 1.2, "mt3608": 1.5, "ams1117_3v3": 0.2, "liion_18650": 4.0,
    "slide_switch": 0.3, "nrf24l01": 6.0, "drone_motors": 8.0, "drone_props": 2.0,
    "drone_frame": 5.0, "liponb": 4.0, "decoupling": 0.5, "resistor_kit": 1.5,
    "proto_pcb": 1.0, "jumpers": 1.5,
    # platforms are bought as the board; rough street prices
}
_PLATFORM_PRICE = {
    "raspberry_pi_pico": 4.0, "esp32_devkit": 6.0, "arduino_nano": 12.0,
    "raspberry_pi_sbc": 55.0,
}
for cid, price in _PRICES.items():
    if cid in CATALOGUE:
        CATALOGUE[cid].price_usd = price


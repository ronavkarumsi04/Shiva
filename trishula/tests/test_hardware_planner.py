"""Tests for the hardware project planner and its Studio endpoints."""

import http.client
import json
import os
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trishula.core.config import TrishulaConfig
from trishula.engineering import components as C
from trishula.engineering.planner import build_plan, classify, clarify


class PlannerEngineTests(unittest.TestCase):
    def test_classify_project_types(self):
        self.assertEqual(classify("a custom FLAC audio player with speaker")["type"], "audio_player")
        self.assertEqual(classify("wireless temperature humidity pressure sensor")["type"], "smart_sensor")
        self.assertEqual(classify("autonomous indoor drone quadcopter")["type"], "drone")

    def test_clarify_returns_questions(self):
        qs = clarify("FLAC player")
        self.assertTrue(any(q["id"] == "experience" for q in qs))
        self.assertTrue(all("options" in q and q["options"] for q in qs))

    def test_audio_player_package(self):
        p = build_plan("custom FLAC audio player with on-board speaker and volume knob")
        self.assertEqual(p.project_type, "audio_player")
        self.assertIn("RP2040", p.platform["mcu"])
        # storage + an I2S amp + encoder + speaker
        cats = {c["category"] for c in p.components}
        self.assertIn("storage", cats)
        self.assertTrue(any("i2s" in c["protocols"] for c in p.components))
        # BOM names real parts and has search links
        self.assertTrue(all(it["search_url"].startswith("https://") for it in p.bom))
        # wiring includes I2S lines
        protos = {w["protocol"] for w in p.wiring}
        self.assertIn("i2s", protos)
        self.assertIn("power", protos)
        # assembly + tests + certification present
        self.assertGreaterEqual(len(p.assembly), 5)
        self.assertTrue(p.tests)
        self.assertTrue(any(c["status"] == "external" for c in p.certifications))

    def test_drone_package(self):
        p = build_plan("indoor drone quadcopter")
        self.assertEqual(p.project_type, "drone")
        cats = {c["category"] for c in p.components}
        self.assertIn("motor", cats)
        self.assertIn("sensor", cats)
        # motors get PWM, IMU on I2C, radio on SPI
        protos = {w["protocol"] for w in p.wiring}
        self.assertTrue({"pwm", "i2c", "spi"} <= protos)
        # battery runtime estimate present
        self.assertIn("battery", p.power)

    def test_smart_sensor_wireless_picks_esp32(self):
        p = build_plan("smart wireless environmental sensor",
                       {"link": "Wi-Fi to a dashboard"})
        self.assertIn("ESP32", p.platform["mcu"])
        self.assertTrue(any(c["category"] == "sensor" for c in p.components))

    def test_no_pin_conflicts(self):
        for prompt in ("FLAC audio player with speaker knob and SD card",
                       "wireless temperature humidity pressure sensor with OLED",
                       "autonomous indoor drone quadcopter"):
            p = build_plan(prompt)
            shared = {"SDA", "SCL", "SCK", "MOSI", "MISO", "BCLK", "LRCLK",
                      "DOUT→DIN", "3V3", "GND"}
            seen = {}
            for w in p.wiring:
                if w["signal"] in shared:
                    continue
                key = (w["signal"], w["source_pin"])
                self.assertNotIn(key, seen, f"{key} double-assigned in {prompt}")
                seen[key] = w["target"]

    def test_power_estimate_and_markdown(self):
        p = build_plan("battery powered sensor node")
        self.assertIn("estimated_peak_mA", p.power)
        # honesty: estimate labelled
        self.assertIn("sizing", p.power["note"])

    def test_catalogue_links_are_searches_not_products(self):
        for c in C.CATALOGUE.values():
            self.assertIn("search?q=", c.to_dict()["search_url"])


class PlannerEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from trishula.desktop.server import StudioServer
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = TrishulaConfig(home=os.path.join(cls.tmp.name, ".home"))
        cls.srv = StudioServer(config=cfg, host="127.0.0.1", port=0)
        cls.url = cls.srv.start(); cls.port = cls.srv.port

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop(); cls.tmp.cleanup()

    def _post(self, path, payload):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
        r = conn.getresponse(); body = r.read(); conn.close()
        return r.status, json.loads(body)

    def test_clarify_endpoint(self):
        st, d = self._post("/api/hw/clarify", {"prompt": "FLAC player"})
        self.assertEqual(st, 200)
        self.assertIn("questions", d)
        self.assertIn("type", d["type"])

    def test_plan_endpoint_package(self):
        st, d = self._post("/api/hw/plan", {"prompt": "smart sensor drone"})
        self.assertEqual(st, 200)
        self.assertIn("bom", d)
        self.assertIn("wiring", d)
        self.assertIn("board", d)
        self.assertIn("assembly", d)
        self.assertTrue(d["bom"])

    def test_plan_requires_prompt(self):
        st, d = self._post("/api/hw/plan", {"prompt": "  "})
        self.assertEqual(st, 400)


if __name__ == "__main__":
    unittest.main()

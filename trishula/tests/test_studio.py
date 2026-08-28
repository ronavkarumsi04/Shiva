"""Tests for the Trishula Studio desktop server (stdlib HTTP + SSE)."""

import http.client
import json
import os
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trishula.core.config import TrishulaConfig
from trishula.desktop.server import StudioServer


class StudioServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = os.path.join(cls.tmp.name, ".home")
        cfg = TrishulaConfig(home=cls.home)
        cls.srv = StudioServer(config=cfg, host="127.0.0.1", port=0)
        cls.url = cls.srv.start()
        cls.port = cls.srv.port

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        cls.tmp.cleanup()

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request("GET", path)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, body

    def _post(self, path, payload, read_all=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=40)
        conn.request("POST", path, json.dumps(payload),
                     {"Content-Type": "application/json"})
        r = conn.getresponse()
        chunks = []
        if read_all:
            while True:
                b = r.read(4096)
                if not b:
                    break
                chunks.append(b)
        conn.close()
        return r.status, b"".join(chunks).decode()

    def test_health_and_static(self):
        st, body = self._get("/api/health")
        self.assertEqual(st, 200)
        h = json.loads(body)
        self.assertIn("chat", h["capabilities"])
        self.assertIn("diagrams", h["capabilities"])
        for path in ("/", "/static/styles.css", "/static/app.js"):
            st, body = self._get(path)
            self.assertEqual(st, 200, path)
            self.assertGreater(len(body), 100)

    def test_404_and_traversal_guard(self):
        st, _ = self._get("/static/../../etc/passwd")
        # normalized to a bare filename that won't exist → 404, never /etc
        self.assertEqual(st, 404)

    def test_settings_persist(self):
        st, body = self._post("/api/settings",
                              {"chat_name": "Vyom", "theme": "aurora",
                               "accent": "emerald", "evil_key": "x"})
        self.assertEqual(st, 200)
        s = json.loads(body)
        self.assertEqual(s["chat_name"], "Vyom")
        self.assertEqual(s["theme"], "aurora")
        self.assertNotIn("evil_key", s)  # unknown keys rejected
        st2, body2 = self._get("/api/settings")
        self.assertEqual(json.loads(body2)["chat_name"], "Vyom")

    def test_chat_sse_streams(self):
        st, body = self._post("/api/chat", {"message": "hello", "history": []})
        self.assertEqual(st, 200)
        self.assertIn("event: token", body)
        self.assertIn("event: done", body)

    def test_code_run_sse_end_to_end(self):
        ws = os.path.join(self.tmp.name, "proj")
        os.makedirs(ws, exist_ok=True)
        Path(ws, "calc.py").write_text("def add(a,b):\n    return a-b\n")
        st, body = self._post("/api/code",
                              {"goal": "fix add()", "workspace": ws})
        self.assertEqual(st, 200)
        self.assertIn("event: run_started", body)
        self.assertIn("event: run_finished", body)
        self.assertIn("event: done", body)

    def test_memory_decision_endpoint(self):
        st, body = self._post("/api/memory/decision",
                              {"topic": "regulator", "choice": "LDO",
                               "rationale": "low noise", "domain": "electrical"})
        self.assertEqual(st, 200)
        rec = json.loads(body)
        self.assertEqual(rec["kind"], "decision")
        st2, body2 = self._get("/api/memory")
        self.assertIn("records", json.loads(body2))


if __name__ == "__main__":
    unittest.main()

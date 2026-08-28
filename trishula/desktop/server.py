"""Trishula Studio — local desktop app server (stdlib only).

A tiny, dependency-free HTTP server that serves the Studio UI and streams
agent activity over Server-Sent Events. It runs anywhere Shiva runs (no pip,
no node) and exposes two modes:

* **Chat** ("Saraswati" by default, fully renamable) — a free-form advanced
  chatbot backed by the configured model, streamed token-style.
* **Code** ("Shiva") — the autonomous coding agent. The coding loop runs in a
  worker thread; every journal event (tool call, edit, verdict, repair round)
  is published to the browser as it happens, rendered as the Claude-Code-style
  agent flow with rich blocks: tool cards, diffs, verification badges,
  coverage meters, architecture diagrams, and trace timelines.

Endpoints
---------
``GET  /``                       the app shell
``GET  /static/<f>``             css/js assets
``GET  /api/health``             capabilities + mode
``GET/POST /api/settings``       customization (name, theme, identity, model)
``POST /api/chat`` (SSE)         stream a chat turn
``POST /api/code`` (SSE)         stream a coding run (journal events + report)
``GET  /api/runs``               recent run metadata
``GET  /api/memory``             engineering memory records (for diagrams)
``POST /api/memory/decision``    save a design decision from the UI
``POST /api/memory/capture``     capture a component datasheet
``POST /api/memory/fact``        remember a measured/verified constant
``GET  /api/memory/search?q=``   ranked memory search
``GET  /api/conversations``      list saved chat/code conversations
``GET  /api/conversation?id=``   one conversation's messages
``POST /api/conversation/save``  upsert a conversation
``DELETE /api/conversation?id=`` delete a conversation
``GET  /api/eng/formulas?domain=``   formula catalogue
``POST /api/eng/calc``           evaluate a formula {name, args}
``GET  /api/eng/gates``          certification gate catalogue
``POST /api/eng/gate``           evaluate a gate against a workspace
``POST /api/hw/clarify``         clarification questions for a hardware idea
``POST /api/hw/plan``            full hardware build package (BOM/wiring/layout)
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

from trishula.core.config import TrishulaConfig
from trishula.core.logging import get_logger
from trishula.core.types import EventKind, Journal

log = get_logger("desktop.server")

STATIC_DIR = Path(__file__).resolve().parent / "static"

_DEFAULT_SETTINGS = {
    "chat_name": "Saraswati",
    "chat_tagline": "Your advanced reasoning companion",
    "agent_name": "Shiva",
    "agent_tagline": "Autonomous engineering agent",
    "theme": "midnight",       # midnight | ember | aurora | ice
    "accent": "indigo",
    "model_provider": "",
    "model": "",
    "workspace": "",
}


class StudioServer:
    def __init__(self, config: TrishulaConfig | None = None, host: str = "127.0.0.1",
                 port: int = 8765):
        self.cfg = config or TrishulaConfig()
        self.host = host
        self.port = port
        self.settings_path = Path(self.cfg.home or Path.home() / ".trishula") / "studio_settings.json"
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.conv_dir = self.settings_path.parent / "conversations"
        self.conv_dir.mkdir(parents=True, exist_ok=True)
        self.settings = self._load_settings()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._runs: dict[str, dict[str, Any]] = {}

    # ── settings ────────────────────────────────────────────────────────

    def _load_settings(self) -> dict[str, Any]:
        data = dict(_DEFAULT_SETTINGS)
        try:
            data.update(json.loads(self.settings_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
        return data

    def _save_settings(self) -> None:
        self.settings_path.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")

    # ── lifecycle ───────────────────────────────────────────────────────

    def start(self) -> str:
        handler = self._make_handler()
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        url = f"http://{'localhost' if self.host in ('0.0.0.0', '127.0.0.1') else self.host}:{self.port}"
        log.info("Trishula Studio on %s", url)
        return url

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()

    # ── conversation persistence ─────────────────────────────────────────

    def _conv_path(self, cid: str) -> Path:
        cid = "".join(ch for ch in cid if ch.isalnum() or ch in "-_") or "new"
        return self.conv_dir / f"{cid}.json"

    def _list_conversations(self) -> list[dict[str, Any]]:
        out = []
        for f in self.conv_dir.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                out.append({"id": d.get("id", f.stem), "mode": d.get("mode", "chat"),
                            "title": d.get("title", "conversation"),
                            "updated_at": d.get("updated_at", 0),
                            "messages": len(d.get("messages", []))})
            except (OSError, json.JSONDecodeError):
                continue
        out.sort(key=lambda c: c["updated_at"], reverse=True)
        return out[:60]

    def _load_conversation(self, cid: str) -> dict[str, Any] | None:
        p = self._conv_path(cid)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _save_conversation(self, conv: dict[str, Any]) -> dict[str, Any]:
        cid = conv.get("id") or uuid.uuid4().hex[:12]
        conv["id"] = cid
        conv["updated_at"] = time.time()
        conv.setdefault("created_at", conv["updated_at"])
        msgs = conv.get("messages", [])
        if not conv.get("title") and msgs:
            first = msgs[0].get("content", "") or ""
            conv["title"] = first[:48].replace("\n", " ").strip() or "conversation"
        self._conv_path(cid).write_text(json.dumps(conv, indent=2), encoding="utf-8")
        return {"id": cid, "title": conv["title"], "updated_at": conv["updated_at"]}

    def _delete_conversation(self, cid: str) -> None:
        p = self._conv_path(cid)
        try:
            p.unlink()
        except OSError:
            pass

    # ── SSE streams ─────────────────────────────────────────────────────

    def _stream_code(self, body: dict, out):
        goal = (body.get("goal") or "").strip()
        workspace = body.get("workspace") or self.settings.get("workspace") or "."
        if not goal:
            self._sse(out, "error", {"message": "no goal provided"})
            self._sse_done(out)
            return

        journal = Journal()
        run_id = uuid.uuid4().hex[:12]

        def publish(ev):
            try:
                self._sse(out, "event", {"kind": ev.kind.value if hasattr(ev.kind, "value")
                                         else str(ev.kind), "payload": ev.payload, "seq": ev.seq})
            except Exception:  # noqa: BLE001 - client gone
                pass

        unsub = journal.subscribe(publish)
        self._sse(out, "run_started", {"run_id": run_id, "goal": goal})

        result: dict[str, Any] = {}

        def worker():
            try:
                from trishula.coding.loop import CodingLoop
                from trishula.autonomy.prompt_evolution import PromptEvolution
                from trishula.coding.loop import _SYSTEM_PROMPT
                from trishula.llm import get_client

                client = get_client(self.cfg)
                pe = PromptEvolution(home=self.cfg.home,
                                     client=client if client.name != "stub" else None)
                loop = CodingLoop(
                    workspace, client=client, config=self.cfg, journal=journal,
                    system_prompt=pe.augment_system_prompt(_SYSTEM_PROMPT),
                )
                report = loop.run(goal)
                result["report"] = report.to_dict()
                try:
                    from trishula.autonomy.reflect import Reflector

                    retro = Reflector().reflect(goal, journal, report=report.to_dict())
                    pe.learn(retro)
                    result["retrospective"] = retro.to_dict()
                except Exception as exc:  # noqa: BLE001
                    result["retrospective"] = {"error": str(exc)}
                self._runs[run_id] = {"goal": goal, "at": time.time(),
                                      "ok": result["report"].get("ok")}
            except Exception as exc:  # noqa: BLE001
                result["error"] = f"{type(exc).__name__}: {exc}"

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        unsub()
        if "error" in result:
            self._sse(out, "error", {"message": result["error"]})
        else:
            self._sse(out, "run_finished", result)
        self._sse_done(out)

    def _stream_chat(self, body: dict, out):
        message = (body.get("message") or "").strip()
        history = body.get("history") or []
        if not message:
            self._sse_done(out)
            return
        try:
            from trishula.core.types import Message as Msg
            from trishula.llm import get_client

            client = get_client(self.cfg)
            msgs = []
            sys_persona = self.settings.get("chat_system", "") or (
                f"You are {self.settings.get('chat_name', 'Saraswati')}, an advanced, "
                "insightful assistant in the Trishula Studio app. Answer richly and use "
                "Markdown. When a plan, architecture, or flow would help, you may emit a "
                "diagram as a fenced ```diagram block with one node per line in the form "
                "'A -> B' or 'A: label'."
            )
            msgs.append(Msg.system(sys_persona))
            for h in history[-12:]:
                if h.get("role") == "user":
                    msgs.append(Msg.user(h.get("content", "")))
                elif h.get("role") == "assistant":
                    msgs.append(Msg.assistant(h.get("content", "")))
            msgs.append(Msg.user(message))
            resp = client.complete(msgs, temperature=0.5, max_tokens=2000)
            text = resp.content or "(no response)"
            # simulate token streaming for a live feel
            buf = ""
            words = text.split(" ")
            for i, w in enumerate(words):
                buf += ("" if i == 0 else " ") + w
                if len(buf) >= 24 or i == len(words) - 1:
                    self._sse(out, "token", {"text": buf})
                    buf = ""
                    time.sleep(0.004)
            self._sse(out, "chat_finished", {"model": resp.model or client.name})
        except Exception as exc:  # noqa: BLE001
            self._sse(out, "error", {"message": f"{type(exc).__name__}: {exc}"})
        self._sse_done(out)

    # ── SSE plumbing ────────────────────────────────────────────────────

    def _sse(self, out, event: str, data: dict) -> None:
        out.write(f"event: {event}\n".encode())
        out.write(f"data: {json.dumps(data)}\n\n".encode())
        out.flush()

    def _sse_done(self, out) -> None:
        self._sse(out, "done", {})

    # ── handler factory ─────────────────────────────────────────────────

    def _make_handler(self):
        studio = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "TrishulaStudio/1.0"

            def log_message(self, *args):  # silence default logging
                pass

            def _send(self, code: int, body: bytes, ctype: str = "application/json",
                      extra: dict | None = None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _json(self, obj, code: int = 200):
                self._send(code, json.dumps(obj).encode())

            def do_GET(self):  # noqa: N802
                url = urlparse(self.path)
                path = url.path
                if path in ("/", "/index.html"):
                    return self._static("index.html")
                if path.startswith("/static/"):
                    return self._static(path.split("/static/", 1)[1])
                if path == "/api/health":
                    from trishula.llm import get_client
                    client = get_client(studio.cfg)
                    return self._json({
                        "ok": True,
                        "mode": client.name,
                        "settings": studio.settings,
                        "capabilities": ["chat", "code", "diagrams", "diffs",
                                         "coverage", "timeline", "memory"],
                    })
                if path == "/api/settings":
                    return self._json(studio.settings)
                if path == "/api/runs":
                    runs = sorted(studio._runs.values(), key=lambda r: r["at"],
                                  reverse=True)[:20]
                    return self._json({"runs": runs})
                if path == "/api/memory":
                    try:
                        from trishula.engineering.memory import EngineeringMemory
                        mem = EngineeringMemory(home=studio.cfg.home)
                        return self._json({"stats": mem.stats(),
                                           "records": [r.to_dict() for r in mem.all()][:200]})
                    except Exception as exc:  # noqa: BLE001
                        return self._json({"error": str(exc), "records": []})
                if path == "/api/memory/search":
                    q = parse_qs(url.query).get("q", [""])[0]
                    try:
                        from trishula.engineering.memory import EngineeringMemory
                        mem = EngineeringMemory(home=studio.cfg.home)
                        hits = [r.to_dict() for r in mem.search(q, k=12)]
                        return self._json({"results": hits})
                    except Exception as exc:  # noqa: BLE001
                        return self._json({"error": str(exc), "results": []})
                if path == "/api/conversations":
                    return self._json({"conversations": studio._list_conversations()})
                if path == "/api/conversation":
                    cid = parse_qs(url.query).get("id", [""])[0]
                    conv = studio._load_conversation(cid)
                    if conv is None:
                        return self._json({"error": "not found"}, 404)
                    return self._json(conv)
                if path == "/api/eng/formulas":
                    from trishula.engineering.formulas import list_formulas, DOMAINS_COVERED
                    domain = parse_qs(url.query).get("domain", [""])[0]
                    fs = list_formulas(domain)
                    return self._json({
                        "domains": DOMAINS_COVERED,
                        "formulas": [{"name": f.name, "domain": f.domain,
                                      "description": f.description, "args": f.args,
                                      "result_unit": f.result_unit, "tags": list(f.tags)}
                                     for f in fs],
                    })
                if path == "/api/eng/gates":
                    from trishula.engineering.safety import GATES
                    return self._json({"gates": [
                        {"key": k, "name": g.name, "domain": g.domain,
                         "description": g.description, "items": len(g.items)}
                        for k, g in GATES.items()]})
                return self._json({"error": "not found"}, 404)

            def do_DELETE(self):  # noqa: N802
                url = urlparse(self.path)
                if url.path == "/api/conversation":
                    cid = parse_qs(url.query).get("id", [""])[0]
                    studio._delete_conversation(cid)
                    return self._json({"ok": True})
                return self._json({"error": "not found"}, 404)

            def do_POST(self):  # noqa: N802
                url = urlparse(self.path)
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    body = {}

                if url.path == "/api/settings":
                    studio.settings.update({k: v for k, v in body.items()
                                            if k in _DEFAULT_SETTINGS or k == "chat_system"})
                    studio._save_settings()
                    return self._json(studio.settings)

                if url.path in ("/api/chat", "/api/code"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.send_header("X-Accel-Buffering", "no")
                    self.end_headers()
                    self.close_connection = True
                    if url.path == "/api/code":
                        studio._stream_code(body, self.wfile)
                    else:
                        studio._stream_chat(body, self.wfile)
                    return

                if url.path == "/api/memory/decision":
                    try:
                        from trishula.engineering.memory import EngineeringMemory
                        mem = EngineeringMemory(home=studio.cfg.home)
                        rec = mem.remember_decision(
                            body.get("topic", "decision"),
                            body.get("choice", ""),
                            rationale=body.get("rationale", ""),
                            domain=body.get("domain", ""),
                            source="studio ui",
                        )
                        return self._json(rec.to_dict())
                    except Exception as exc:  # noqa: BLE001
                        return self._json({"error": str(exc)}, 500)

                if url.path == "/api/memory/capture":
                    try:
                        from trishula.engineering.memory import EngineeringMemory
                        mem = EngineeringMemory(home=studio.cfg.home)
                        rec = mem.capture_datasheet(
                            body.get("part", "unknown"),
                            body.get("parameters", {}),
                            manufacturer=body.get("manufacturer", ""),
                            source=body.get("source", "studio ui"),
                            domain=body.get("domain", ""),
                        )
                        return self._json(rec.to_dict())
                    except Exception as exc:  # noqa: BLE001
                        return self._json({"error": str(exc)}, 500)

                if url.path == "/api/memory/fact":
                    try:
                        from trishula.engineering.memory import EngineeringMemory
                        mem = EngineeringMemory(home=studio.cfg.home)
                        rec = mem.remember_fact(
                            body.get("name", "fact"), body.get("value"),
                            unit=body.get("unit", ""), domain=body.get("domain", ""),
                            note=body.get("note", ""), source=body.get("source", "studio ui"),
                        )
                        return self._json(rec.to_dict())
                    except Exception as exc:  # noqa: BLE001
                        return self._json({"error": str(exc)}, 500)

                if url.path == "/api/conversation/save":
                    conv = studio._save_conversation(body)
                    return self._json(conv)

                if url.path == "/api/eng/calc":
                    try:
                        from trishula.engineering.formulas import calculate, FORMULAS
                        name = body.get("name", "")
                        if name not in FORMULAS:
                            return self._json({"error": f"unknown formula {name!r}",
                                               "available": len(FORMULAS)}, 400)
                        args = body.get("args", {}) or {}
                        # args may arrive as strings or [value, unit] pairs
                        val = calculate(name, **args)
                        f = FORMULAS[name]
                        return self._json({"name": name, "value": val,
                                           "result_unit": f.result_unit,
                                           "description": f.description})
                    except Exception as exc:  # noqa: BLE001
                        return self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)

                if url.path == "/api/eng/gate":
                    try:
                        from trishula.engineering.safety import evaluate_gate, GATES
                        from trishula.tools.workspace import Workspace
                        key = body.get("gate", "")
                        if key not in GATES:
                            return self._json({"error": f"unknown gate {key!r}",
                                               "available": sorted(GATES)}, 400)
                        ws = Workspace(body.get("workspace", "."))
                        report = evaluate_gate(key, workspace=ws)
                        return self._json(report.to_dict())
                    except Exception as exc:  # noqa: BLE001
                        return self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)

                if url.path == "/api/hw/clarify":
                    try:
                        from trishula.engineering.planner import clarify, classify
                        prompt = body.get("prompt", "")
                        return self._json({
                            "type": classify(prompt),
                            "questions": clarify(prompt),
                        })
                    except Exception as exc:  # noqa: BLE001
                        return self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)

                if url.path == "/api/hw/plan":
                    try:
                        from trishula.engineering.planner import build_plan
                        from trishula.llm import get_client
                        prompt = body.get("prompt", "")
                        if not prompt.strip():
                            return self._json({"error": "prompt is required"}, 400)
                        answers = body.get("answers") or {}
                        use_llm = bool(body.get("enrich", True))
                        client = get_client(studio.cfg)
                        client = client if (use_llm and client.name != "stub") else None
                        plan = build_plan(prompt, answers, client=client)
                        return self._json(plan.to_dict())
                    except Exception as exc:  # noqa: BLE001
                        log.warning("hw plan failed: %s", exc)
                        return self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)

                return self._json({"error": "not found"}, 404)

            def _static(self, name: str):
                # prevent path traversal
                name = Path(name).name
                f = STATIC_DIR / name
                if not f.exists() or not f.is_file():
                    return self._json({"error": f"{name} not found"}, 404)
                ctype = {
                    ".html": "text/html; charset=utf-8",
                    ".css": "text/css; charset=utf-8",
                    ".js": "application/javascript; charset=utf-8",
                    ".svg": "image/svg+xml",
                }.get(f.suffix, "application/octet-stream")
                self._send(200, f.read_bytes(), ctype,
                           {"Cache-Control": "no-store"})

        return Handler


def serve(host: str = "127.0.0.1", port: int = 8765, config: TrishulaConfig | None = None,
          block: bool = True) -> StudioServer:
    """Start Studio; returns the server (open ``.start()`` url in a browser)."""
    srv = StudioServer(config=config, host=host, port=port)
    url = srv.start()
    if block:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            srv.stop()
    return srv

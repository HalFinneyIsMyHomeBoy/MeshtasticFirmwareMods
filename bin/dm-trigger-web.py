#!/usr/bin/env python3
"""Local web configurator for Heltec V3 DM GPIO triggers.

Run:
  ./bin/dm-trigger-web.py --port 8088

Then open http://127.0.0.1:8088 and select the device serial port.
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


PINOUT = [
    {"gpio": 1, "label": "GPIO1", "note": "Battery ADC; analog only", "analog": True, "output": False},
    {"gpio": 2, "label": "GPIO2", "note": "External GPIO / ADC", "analog": True, "output": True},
    {"gpio": 3, "label": "GPIO3", "note": "External GPIO / ADC", "analog": True, "output": True},
    {"gpio": 4, "label": "GPIO4", "note": "External GPIO / ADC", "analog": True, "output": True},
    {"gpio": 5, "label": "GPIO5", "note": "External GPIO / ADC", "analog": True, "output": True},
    {"gpio": 6, "label": "GPIO6 / J3-17", "note": "Default voltage input", "analog": True, "output": True},
    {"gpio": 7, "label": "GPIO7", "note": "Default output trigger", "analog": True, "output": True},
]


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Meshtastic DM Trigger Configurator</title>
  <style>
    :root { color-scheme: dark; --bg:#101418; --card:#181f26; --muted:#9aa8b5; --text:#eef4f8; --accent:#50c878; --danger:#ff6b6b; --line:#2a3540; }
    body { margin:0; font:15px/1.4 system-ui, -apple-system, Segoe UI, sans-serif; background:var(--bg); color:var(--text); }
    header { padding:20px 24px; border-bottom:1px solid var(--line); background:#0c1116; }
    h1 { margin:0 0 4px; font-size:22px; }
    .sub { color:var(--muted); }
    main { display:grid; grid-template-columns: 380px 1fr; gap:18px; padding:18px; }
    section { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }
    h2 { margin:0 0 14px; font-size:17px; }
    label { display:block; margin:12px 0 5px; color:var(--muted); font-size:13px; }
    input, select, button { box-sizing:border-box; width:100%; border-radius:8px; border:1px solid var(--line); background:#0e141a; color:var(--text); padding:10px; font:inherit; }
    button { cursor:pointer; background:#1d2a35; }
    button.primary { background:var(--accent); color:#06230f; border-color:var(--accent); font-weight:700; }
    button.danger { background:#3a1d22; color:#ffd4d4; border-color:#63313a; }
    button:disabled { opacity:.5; cursor:not-allowed; }
    .row { display:grid; grid-template-columns: 1fr 1fr; gap:10px; }
    .actions { display:grid; grid-template-columns: 1fr 1fr 1fr; gap:10px; margin-top:14px; }
    .pin-list { display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:8px; }
    .pin { text-align:left; border:1px solid var(--line); background:#101820; padding:10px; border-radius:9px; }
    .pin.active { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) inset; }
    .pin small { display:block; color:var(--muted); margin-top:2px; }
    .board { display:grid; grid-template-columns:1fr 1fr; gap:14px; align-items:start; }
    svg { width:100%; max-width:320px; background:#0e141a; border:1px solid var(--line); border-radius:12px; }
    .log { white-space:pre-wrap; min-height:120px; max-height:320px; overflow:auto; background:#0e141a; border:1px solid var(--line); border-radius:10px; padding:12px; color:#c7d4df; }
    table { width:100%; border-collapse:collapse; }
    th, td { border-bottom:1px solid var(--line); padding:8px; text-align:left; }
    th { color:var(--muted); font-weight:600; }
    .pill { display:inline-block; padding:2px 7px; border-radius:999px; background:#24313c; color:#cbd7e1; font-size:12px; }
    .hint { margin-top:8px; color:var(--muted); font-size:13px; }
    @media (max-width: 900px) { main { grid-template-columns:1fr; } .board { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>DM Trigger Configurator</h1>
    <div class="sub">Configure Heltec V3 GPIO output and analog-reading triggers over USB. No firmware rebuild required.</div>
  </header>
  <main>
    <section>
      <h2>Connection</h2>
      <label for="port">Serial port</label>
      <div class="row">
        <select id="port"></select>
        <button onclick="refreshPorts()">Refresh</button>
      </div>
      <div class="hint">The firmware must already include <code>DmTriggerModule</code>. Config commands are sent as self-DMs.</div>

      <h2 style="margin-top:24px">Trigger</h2>
      <label for="index">Slot</label>
      <select id="index">
        <option value="-1">Append new trigger</option>
        <option value="0">Replace slot 0</option>
        <option value="1">Replace slot 1</option>
        <option value="2">Replace slot 2</option>
        <option value="3">Replace slot 3</option>
        <option value="4">Replace slot 4</option>
        <option value="5">Replace slot 5</option>
        <option value="6">Replace slot 6</option>
        <option value="7">Replace slot 7</option>
      </select>
      <label for="name">Trigger name</label>
      <input id="name" maxlength="19" placeholder="Garage">
      <label for="type">Type</label>
      <select id="type" onchange="updateType()">
        <option value="output">Output</option>
        <option value="analog">Analog reading</option>
      </select>
      <label for="message">Exact direct message</label>
      <input id="message" maxlength="39" placeholder="Open_Seseme">
      <div class="row">
        <div>
          <label for="gpio">GPIO</label>
          <input id="gpio" type="number" min="1" max="48" value="7">
        </div>
        <div id="durationBox">
          <label for="duration">Duration (ms)</label>
          <input id="duration" type="number" min="1" value="10000">
        </div>
      </div>
      <div id="analogBox" style="display:none">
        <label for="multiplier">ADC multiplier</label>
        <input id="multiplier" type="number" min="0" step="0.01" value="1.0">
      </div>
      <button class="primary" style="margin-top:14px" onclick="saveTrigger()">Save Trigger</button>

      <div class="actions">
        <button onclick="listTriggers()">List</button>
        <button onclick="deleteTrigger()">Delete Slot</button>
        <button class="danger" onclick="clearTriggers()">Clear All</button>
      </div>
    </section>

    <div>
      <section>
        <h2>Heltec V3 Pinout</h2>
        <div class="board">
          <svg viewBox="0 0 260 360" role="img" aria-label="Simplified Heltec V3 pinout">
            <rect x="80" y="20" width="100" height="320" rx="16" fill="#1c2833" stroke="#40515e"/>
            <rect x="98" y="42" width="64" height="36" rx="4" fill="#0b1014" stroke="#607080"/>
            <text x="130" y="65" text-anchor="middle" fill="#9fb2c2" font-size="11">OLED</text>
            <rect x="100" y="230" width="60" height="70" rx="8" fill="#263747" stroke="#607080"/>
            <text x="130" y="270" text-anchor="middle" fill="#9fb2c2" font-size="11">LoRa</text>
            <g id="pinDots"></g>
          </svg>
          <div class="pin-list" id="pinList"></div>
        </div>
        <div class="hint">Reserved board pins are hidden here. GPIO1 is shown as analog only because firmware blocks it as an output.</div>
      </section>

      <section style="margin-top:18px">
        <h2>Triggers</h2>
        <table>
          <thead><tr><th>Slot</th><th>Name</th><th>Type</th><th>Message</th><th>GPIO</th></tr></thead>
          <tbody id="triggerRows"><tr><td colspan="5" class="sub">Click List to query the device.</td></tr></tbody>
        </table>
      </section>

      <section style="margin-top:18px">
        <h2>Activity</h2>
        <div id="log" class="log">Ready.</div>
      </section>
    </div>
  </main>

  <script>
    const pinout = __PINOUT__;

    function log(msg) {
      const el = document.getElementById('log');
      el.textContent = `${new Date().toLocaleTimeString()}  ${msg}\n\n${el.textContent}`;
    }

    async function api(path, body) {
      const res = await fetch(path, {
        method: body ? 'POST' : 'GET',
        headers: body ? {'Content-Type': 'application/json'} : {},
        body: body ? JSON.stringify(body) : undefined
      });
      const json = await res.json();
      if (!res.ok || json.ok === false) throw new Error(json.error || `HTTP ${res.status}`);
      return json;
    }

    async function refreshPorts() {
      try {
        const data = await api('/api/ports');
        const port = document.getElementById('port');
        port.innerHTML = '';
        data.ports.forEach(p => {
          const opt = document.createElement('option');
          opt.value = p;
          opt.textContent = p;
          port.appendChild(opt);
        });
        if (!data.ports.length) {
          const opt = document.createElement('option');
          opt.value = '';
          opt.textContent = 'Auto-detect';
          port.appendChild(opt);
        }
        log(`Found ${data.ports.length} serial port(s).`);
      } catch (err) {
        log(`Port refresh failed: ${err.message}`);
      }
    }

    function currentPort() {
      return document.getElementById('port').value || null;
    }

    async function sendCommand(command, parseList = false) {
      log(`Sending ${command}`);
      const data = await api('/api/command', {port: currentPort(), command});
      const replies = data.replies || [];
      if (replies.length) log(`Reply:\n${replies.join('\n')}`);
      else log('Command sent. No DM reply was captured before timeout.');
      if (parseList) renderTriggers(replies.join('\n'));
      return data;
    }

    function updateType() {
      const isAnalog = document.getElementById('type').value === 'analog';
      document.getElementById('durationBox').style.display = isAnalog ? 'none' : 'block';
      document.getElementById('analogBox').style.display = isAnalog ? 'block' : 'none';
    }

    function setPin(gpio) {
      document.getElementById('gpio').value = gpio;
      document.querySelectorAll('.pin').forEach(el => el.classList.toggle('active', el.dataset.gpio == gpio));
    }

    function buildPinout() {
      const list = document.getElementById('pinList');
      const dots = document.getElementById('pinDots');
      list.innerHTML = '';
      dots.innerHTML = '';
      pinout.forEach((pin, i) => {
        const btn = document.createElement('button');
        btn.className = 'pin';
        btn.dataset.gpio = pin.gpio;
        btn.innerHTML = `<strong>${pin.label}</strong><small>${pin.note}</small>`;
        btn.onclick = () => setPin(pin.gpio);
        list.appendChild(btn);

        const y = 104 + i * 26;
        const x = i % 2 === 0 ? 62 : 198;
        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.setAttribute('cx', x);
        circle.setAttribute('cy', y);
        circle.setAttribute('r', 8);
        circle.setAttribute('fill', pin.output ? '#50c878' : '#e9b44c');
        circle.style.cursor = 'pointer';
        circle.onclick = () => setPin(pin.gpio);
        dots.appendChild(circle);
        const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        label.setAttribute('x', x < 100 ? x - 12 : x + 12);
        label.setAttribute('y', y + 4);
        label.setAttribute('text-anchor', x < 100 ? 'end' : 'start');
        label.setAttribute('fill', '#c8d8e4');
        label.setAttribute('font-size', '10');
        label.textContent = `GPIO${pin.gpio}`;
        dots.appendChild(label);
      });
      setPin(7);
    }

    function validateForm() {
      const name = document.getElementById('name').value.trim();
      const type = document.getElementById('type').value;
      const message = document.getElementById('message').value.trim();
      const gpio = Number(document.getElementById('gpio').value);
      const duration = Number(document.getElementById('duration').value);
      const pin = pinout.find(p => p.gpio === gpio);
      if (!name || !message) throw new Error('Name and message are required.');
      if (!pin) throw new Error(`GPIO${gpio} is not in the allowed Heltec V3 pin list.`);
      if (type === 'output' && !pin.output) throw new Error(`GPIO${gpio} is not allowed as an output.`);
      if (type === 'output' && duration <= 0) throw new Error('Duration must be greater than zero.');
    }

    async function saveTrigger() {
      try {
        validateForm();
        const index = document.getElementById('index').value;
        const name = document.getElementById('name').value.trim();
        const type = document.getElementById('type').value;
        const message = document.getElementById('message').value.trim();
        const gpio = document.getElementById('gpio').value;
        const duration = type === 'output' ? document.getElementById('duration').value : 0;
        const multiplier = type === 'analog' ? document.getElementById('multiplier').value : 1.0;
        await sendCommand(`!dmtrigger:set|${index}|${name}|${type}|${message}|${gpio}|${duration}|${multiplier}`);
        await listTriggers();
      } catch (err) {
        log(`Save failed: ${err.message}`);
      }
    }

    async function listTriggers() {
      try { await sendCommand('!dmtrigger:list', true); } catch (err) { log(`List failed: ${err.message}`); }
    }

    async function deleteTrigger() {
      const index = prompt('Delete which trigger slot?');
      if (index === null) return;
      try {
        await sendCommand(`!dmtrigger:del|${Number(index)}`);
        await listTriggers();
      } catch (err) {
        log(`Delete failed: ${err.message}`);
      }
    }

    async function clearTriggers() {
      if (!confirm('Remove all triggers from the device?')) return;
      try {
        await sendCommand('!dmtrigger:clear');
        await listTriggers();
      } catch (err) {
        log(`Clear failed: ${err.message}`);
      }
    }

    function renderTriggers(text) {
      const tbody = document.getElementById('triggerRows');
      const rows = [];
      text.split(/\n/).forEach(line => {
        const m = line.match(/^(\d+):([^:]*):([^:]*):([^:]*):gpio(\d+)/);
        if (m) rows.push({slot:m[1], name:m[2], type:m[3], message:m[4], gpio:m[5]});
      });
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="sub">No trigger list reply captured.</td></tr>';
        return;
      }
      tbody.innerHTML = rows.map(r => `<tr><td>${r.slot}</td><td>${escapeHtml(r.name)}</td><td><span class="pill">${r.type}</span></td><td>${escapeHtml(r.message)}</td><td>GPIO${r.gpio}</td></tr>`).join('');
    }

    function escapeHtml(s) {
      return s.replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }

    buildPinout();
    updateType();
    refreshPorts();
  </script>
</body>
</html>
"""


def list_ports() -> list[str]:
    ports: list[str] = []
    for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/serial/by-id/*"):
        ports.extend(glob.glob(pattern))
    return sorted(set(ports))


def _packet_text(packet: dict[str, Any]) -> str | None:
    decoded = packet.get("decoded") if isinstance(packet, dict) else None
    if not isinstance(decoded, dict):
        return None
    text = decoded.get("text")
    if isinstance(text, str):
        return text
    payload = decoded.get("payload")
    if isinstance(payload, bytes):
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def send_dm_command(port: str | None, command: str, wait_s: float = 2.5) -> list[str]:
    try:
        import meshtastic.serial_interface
        from pubsub import pub
    except ImportError as exc:
        raise RuntimeError("Python package required: pip install meshtastic pypubsub") from exc

    replies: list[str] = []
    lock = threading.Lock()

    def on_receive(packet: dict[str, Any], interface: Any = None) -> None:
        text = _packet_text(packet)
        if not text:
            return
        if text.startswith("Triggers") or text.startswith("Trigger") or text.startswith("Set failed") or text.startswith("Delete failed"):
            with lock:
                replies.append(text)

    pub.subscribe(on_receive, "meshtastic.receive.text")
    pub.subscribe(on_receive, "meshtastic.receive")

    iface = None
    try:
        iface = meshtastic.serial_interface.SerialInterface(devPath=port) if port else meshtastic.serial_interface.SerialInterface()
        node_num = iface.localNode.nodeNum
        iface.sendText(command, destinationId=node_num, wantAck=True)
        time.sleep(wait_s)
    finally:
        try:
            pub.unsubscribe(on_receive, "meshtastic.receive.text")
        except Exception:
            pass
        try:
            pub.unsubscribe(on_receive, "meshtastic.receive")
        except Exception:
            pass
        if iface is not None:
            iface.close()

    with lock:
        return replies[:]


class Handler(BaseHTTPRequestHandler):
    server_version = "DmTriggerWeb/0.1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/" or self.path.startswith("/index.html"):
            body = HTML.replace("__PINOUT__", json.dumps(PINOUT)).encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
            return
        if self.path.startswith("/api/ports"):
            self._send_json({"ok": True, "ports": list_ports()})
            return
        self._send_json({"ok": False, "error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/command":
            self._send_json({"ok": False, "error": "not found"}, status=404)
            return

        try:
            payload = self._read_json()
            command = str(payload.get("command", ""))
            if not command.startswith("!dmtrigger:"):
                raise ValueError("Only !dmtrigger: commands are allowed")
            port = payload.get("port")
            if port is not None:
                port = str(port)
            replies = send_dm_command(port, command)
            self._send_json({"ok": True, "replies": replies})
        except Exception as exc:
            self._send_json({"ok": False, "error": html.escape(str(exc))}, status=400)

    def log_message(self, fmt: str, *args: Any) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _send_json(self, value: dict[str, Any], status: int = 200) -> None:
        self._send(status, json.dumps(value).encode("utf-8"), "application/json")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"DM trigger configurator running at http://{args.host}:{args.port}")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Local web configurator for DM GPIO triggers (Heltec V3 / V4).

Includes a Device console (Meshtastic serial session) to send DMs / !dmtrigger:
commands and stream replies + firmware log lines.

Setup (use the repo venv — do not pip install into system Python):

  .venv/bin/pip install meshtastic pypubsub

Run:

  .venv/bin/python bin/dm-trigger-web.py --port 8088

Then open http://127.0.0.1:8088, pick the board, and select the device serial port.
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import json
import os
import re
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


BOARDS = {
    "heltec-v3": {
        "id": "heltec-v3",
        "label": "Heltec V3",
        "env": "heltec-v3",
        "default_output": 7,
        "default_analog": 6,
        "pinout": [
            {"gpio": 1, "label": "GPIO1", "note": "Battery ADC; analog only", "analog": True, "output": False},
            {"gpio": 2, "label": "GPIO2", "note": "External GPIO / ADC", "analog": True, "output": True},
            {"gpio": 3, "label": "GPIO3", "note": "External GPIO / ADC", "analog": True, "output": True},
            {"gpio": 4, "label": "GPIO4", "note": "External GPIO / ADC", "analog": True, "output": True},
            {"gpio": 5, "label": "GPIO5", "note": "External GPIO / ADC", "analog": True, "output": True},
            {"gpio": 6, "label": "GPIO6 / J3-17", "note": "Default voltage input", "analog": True, "output": True},
            {"gpio": 7, "label": "GPIO7", "note": "Default output trigger", "analog": True, "output": True},
        ],
    },
    "heltec-v4": {
        "id": "heltec-v4",
        "label": "Heltec V4",
        "env": "heltec-v4",
        "default_output": 6,
        "default_analog": 3,
        "pinout": [
            {"gpio": 1, "label": "GPIO1", "note": "Battery ADC; analog only", "analog": True, "output": False},
            {"gpio": 3, "label": "GPIO3 / I2C SCL", "note": "Default voltage input; also external I2C SCL", "analog": True, "output": True},
            {"gpio": 4, "label": "GPIO4 / I2C SDA", "note": "Header GPIO / ADC; also external I2C SDA", "analog": True, "output": True},
            {"gpio": 6, "label": "GPIO6 / J3-17", "note": "Default output; also ADC. Buzzer on V4 TFT — skip on TFT", "analog": True, "output": True},
            {"gpio": 45, "label": "GPIO45", "note": "User GPIO (digital only)", "analog": False, "output": True},
        ],
    },
}


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DM Triggers</title>
  <style>
    :root { color-scheme: dark; --bg:#0f1419; --card:#1a222b; --muted:#8b9aab; --text:#eef3f7; --accent:#3dbe7a; --danger:#e85d5d; --line:#2c3845; --blue:#3d7eff; }
    * { box-sizing: border-box; }
    body { margin:0; font:15px/1.45 system-ui, -apple-system, Segoe UI, sans-serif; background:var(--bg); color:var(--text); }
    .wrap { max-width:720px; margin:0 auto; padding:20px 16px 48px; }
    h1 { margin:0 0 4px; font-size:22px; }
    h2 { margin:0 0 12px; font-size:16px; }
    .sub { color:var(--muted); margin-bottom:20px; }
    section { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:14px; }
    label { display:block; margin:10px 0 4px; color:var(--muted); font-size:13px; }
    label:first-child { margin-top:0; }
    input, select, button { width:100%; border-radius:8px; border:1px solid var(--line); background:#0e141a; color:var(--text); padding:10px 12px; font:inherit; }
    button { cursor:pointer; background:#24303c; }
    button.primary { background:var(--accent); color:#06230f; border-color:var(--accent); font-weight:700; }
    button.danger { background:transparent; color:var(--danger); border-color:#5a3030; }
    button.blue { background:var(--blue); color:#fff; border-color:var(--blue); font-weight:600; }
    button:disabled { opacity:.4; cursor:not-allowed; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
    .row3 { display:grid; grid-template-columns:1fr auto auto; gap:8px; align-items:end; }
    .btn-row { display:flex; gap:8px; margin-top:12px; flex-wrap:wrap; }
    .btn-row button { width:auto; min-width:100px; flex:1; }
    .status { font-size:13px; color:var(--muted); margin-top:8px; }
    .status.ok { color:var(--accent); }
    .status.fail { color:var(--danger); }
    .status.running { color:#e9b44c; }
    table { width:100%; border-collapse:collapse; }
    th, td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--line); font-size:14px; }
    th { color:var(--muted); font-weight:600; }
    .pill { display:inline-block; padding:2px 7px; border-radius:999px; background:#24313c; font-size:12px; }
    .empty { color:var(--muted); font-size:14px; }
    .mono { white-space:pre-wrap; font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size:12px; line-height:1.4; min-height:160px; max-height:320px; overflow:auto; background:#070b0f; border:1px solid var(--line); border-radius:8px; padding:10px; color:#b7c7d4; }
    details { margin-top:8px; }
    details summary { cursor:pointer; color:var(--muted); font-size:13px; padding:6px 0; }
    .hint { color:var(--muted); font-size:13px; margin-top:8px; }
    .send-row { display:grid; grid-template-columns:1fr 100px; gap:8px; margin-top:10px; }
    td button { width:auto; padding:6px 10px; font-size:13px; }
    @media (max-width:560px) { .row, .row3 { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>DM Triggers</h1>
    <p class="sub" id="boardHint">Save triggers to the device, then DM the message from another node.</p>

    <section>
      <h2>1. Connect</h2>
      <div class="row">
        <div>
          <label for="board">Board</label>
          <select id="board" onchange="onBoardChange()"></select>
        </div>
        <div>
          <label for="port">USB port</label>
          <select id="port"></select>
        </div>
      </div>
      <div class="btn-row">
        <button onclick="refreshPorts()">Refresh ports</button>
        <button class="primary" id="connectBtn" onclick="toggleConnect()">Connect</button>
      </div>
      <div id="deviceStatus" class="status">Not connected</div>
    </section>

    <section>
      <h2>2. Saved triggers</h2>
      <table>
        <thead><tr><th>Message</th><th>Type</th><th>Pin</th><th></th></tr></thead>
        <tbody id="triggerRows"><tr><td colspan="4" class="empty">Connect, then click Refresh list.</td></tr></tbody>
      </table>
      <div class="btn-row">
        <button onclick="listTriggers()">Refresh list</button>
        <button class="danger" onclick="clearTriggers()">Clear all</button>
      </div>
    </section>

    <section>
      <h2>3. Add / update trigger</h2>
      <input type="hidden" id="index" value="-1">
      <label for="message">Direct-message text (exact match)</label>
      <input id="message" maxlength="39" placeholder="open7">
      <label for="name">Label</label>
      <input id="name" maxlength="19" placeholder="Relay 7">
      <div class="row">
        <div>
          <label for="type">Action</label>
          <select id="type" onchange="updateType()">
            <option value="output">Pulse GPIO (relay)</option>
            <option value="analog">Read voltage (ADC)</option>
          </select>
        </div>
        <div>
          <label for="gpio">GPIO pin</label>
          <select id="gpio"></select>
        </div>
      </div>
      <div class="row" id="durationBox">
        <div>
          <label for="duration">Hold time (ms)</label>
          <input id="duration" type="number" min="1" value="10000">
        </div>
        <div></div>
      </div>
      <div id="analogBox" style="display:none">
        <label for="multiplier">ADC multiplier</label>
        <input id="multiplier" type="number" min="0" step="0.01" value="1.0">
      </div>
      <div class="btn-row">
        <button class="primary" onclick="saveTrigger()">Save to device</button>
        <button onclick="resetForm()">New trigger</button>
      </div>
      <p class="hint">Saves to device storage immediately. No firmware flash needed for trigger changes.</p>
    </section>

    <section>
      <h2>4. Test</h2>
      <p class="hint" style="margin-top:0">Sends a DM to this node over USB (same as a mesh DM).</p>
      <div class="send-row">
        <input id="monitorInput" placeholder="open7" autocomplete="off">
        <button class="primary" onclick="monitorSend()">Send</button>
      </div>
      <div id="monitorStatus" class="status">Log appears below when connected</div>
      <div id="serialMonitor" class="mono" style="margin-top:10px">Connect to see device logs.</div>
    </section>

    <section>
      <details>
        <summary>Advanced: write firmware</summary>
        <p class="hint">Only needed after changing firmware code — not for saving triggers.</p>
        <button id="writeFirmware" class="blue" disabled onclick="writeFirmware()">Write firmware to board</button>
        <div id="flashConsoleWrap" style="display:none; margin-top:12px">
          <div class="status" id="flashStatus">Idle</div>
          <div id="flashConsole" class="mono" style="margin-top:8px"></div>
        </div>
      </details>
    </section>
  </div>

  <script>
    const boards = __BOARDS__;
    let pinout = boards["heltec-v3"].pinout;
    let flashBusy = false;
    let monitorCursor = 0;
    let monitorConnected = false;
    let editingSlot = -1;

    function log(msg) {
      appendMonitorLocal(`[ui] ${msg}`);
    }

    function appendMonitorLocal(line) {
      const el = document.getElementById('serialMonitor');
      if (el.textContent.startsWith('Connect to see')) el.textContent = '';
      el.textContent += (el.textContent ? '\n' : '') + line;
      el.scrollTop = el.scrollHeight;
    }

    function setDeviceStatus(text, cls) {
      const el = document.getElementById('deviceStatus');
      el.className = 'status' + (cls ? ' ' + cls : '');
      el.textContent = text;
    }

    function updateWriteFirmwareButton() {
      const btn = document.getElementById('writeFirmware');
      btn.disabled = !currentPort() || flashBusy;
    }

    async function api(path, body, timeoutMs = 30000) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const res = await fetch(path, {
          method: body ? 'POST' : 'GET',
          headers: body ? {'Content-Type': 'application/json'} : {},
          body: body ? JSON.stringify(body) : undefined,
          signal: controller.signal
        });
        const json = await res.json();
        if (!res.ok || json.ok === false) throw new Error(json.error || `HTTP ${res.status}`);
        return json;
      } catch (err) {
        if (err.name === 'AbortError') throw new Error(`Timed out after ${Math.round(timeoutMs/1000)}s`);
        throw err;
      } finally {
        clearTimeout(timer);
      }
    }

    function currentBoard() {
      return boards[document.getElementById('board').value] || boards['heltec-v3'];
    }

    function fillBoardSelect() {
      const sel = document.getElementById('board');
      sel.innerHTML = '';
      Object.values(boards).forEach(b => {
        const opt = document.createElement('option');
        opt.value = b.id;
        opt.textContent = b.label;
        sel.appendChild(opt);
      });
      onBoardChange();
    }

    function onBoardChange() {
      const board = currentBoard();
      pinout = board.pinout;
      document.getElementById('boardHint').textContent =
        `${board.label} — save triggers to the device, then DM the message from another node.`;
      fillGpioSelect();
    }

    function currentPort() {
      return document.getElementById('port').value || null;
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
          opt.textContent = 'No USB device found';
          port.appendChild(opt);
        }
        updateWriteFirmwareButton();
      } catch (err) {
        setDeviceStatus('Port refresh failed: ' + err.message, 'fail');
      }
    }

    function fillGpioSelect() {
      const sel = document.getElementById('gpio');
      const type = document.getElementById('type').value;
      const cur = sel.value;
      sel.innerHTML = '';
      pinout.forEach(pin => {
        if (type === 'output' && !pin.output) return;
        if (type === 'analog' && !pin.analog) return;
        const opt = document.createElement('option');
        opt.value = pin.gpio;
        opt.textContent = `${pin.label} — ${pin.note}`;
        sel.appendChild(opt);
      });
      const board = currentBoard();
      const fallback = String(type === 'output' ? board.default_output : board.default_analog);
      if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
      else sel.value = fallback;
    }

    function updateType() {
      const isAnalog = document.getElementById('type').value === 'analog';
      document.getElementById('durationBox').style.display = isAnalog ? 'none' : 'grid';
      document.getElementById('analogBox').style.display = isAnalog ? 'block' : 'none';
      fillGpioSelect();
    }

    function resetForm() {
      editingSlot = -1;
      document.getElementById('index').value = '-1';
      document.getElementById('name').value = '';
      document.getElementById('message').value = '';
      document.getElementById('type').value = 'output';
      document.getElementById('duration').value = '10000';
      document.getElementById('multiplier').value = '1.0';
      updateType();
    }

    function editTrigger(slot, name, type, message, gpio, duration) {
      editingSlot = Number(slot);
      document.getElementById('index').value = String(slot);
      document.getElementById('name').value = name;
      document.getElementById('message').value = message;
      document.getElementById('type').value = type === 'analog' ? 'analog' : 'output';
      updateType();
      document.getElementById('gpio').value = String(gpio);
      if (duration) document.getElementById('duration').value = String(duration);
      document.getElementById('message').focus();
    }

    async function sendCommand(command, parseList = false) {
      const data = await api('/api/command', {port: currentPort(), command}, 90000);
      const replies = data.replies || [];
      if (replies.length) appendMonitorLocal(replies.join('\n'));
      if (parseList) renderTriggers(replies.join('\n'));
      return data;
    }

    function validateForm() {
      const name = document.getElementById('name').value.trim();
      const type = document.getElementById('type').value;
      const message = document.getElementById('message').value.trim();
      const gpio = Number(document.getElementById('gpio').value);
      const duration = Number(document.getElementById('duration').value);
      const pin = pinout.find(p => p.gpio === gpio);
      if (!message) throw new Error('Message text is required.');
      if (!name) document.getElementById('name').value = message.slice(0, 19);
      if (!pin) throw new Error(`GPIO${gpio} is not allowed.`);
      if (type === 'output' && !pin.output) throw new Error(`GPIO${gpio} cannot be an output.`);
      if (type === 'analog' && !pin.analog) throw new Error(`GPIO${gpio} is not ADC-capable.`);
      if (type === 'output' && duration <= 0) throw new Error('Hold time must be > 0.');
    }

    async function saveTrigger() {
      try {
        if (!monitorConnected) await monitorConnect();
        validateForm();
        const index = document.getElementById('index').value;
        const name = document.getElementById('name').value.trim();
        const type = document.getElementById('type').value;
        const message = document.getElementById('message').value.trim();
        const gpio = document.getElementById('gpio').value;
        const duration = type === 'output' ? document.getElementById('duration').value : 0;
        const multiplier = type === 'analog' ? document.getElementById('multiplier').value : 1.0;
        const data = await sendCommand(`!dmtrigger:set|${index}|${name}|${type}|${message}|${gpio}|${duration}|${multiplier}`);
        const replies = (data.replies || []).join('\n');
        if (replies.includes('Set failed') || replies.includes('Not authorized')) {
          setDeviceStatus('Save rejected by device', 'fail');
          return;
        }
        await listTriggers();
        const ok = replies.includes('Trigger saved') || document.getElementById('triggerRows').textContent.includes(message);
        setDeviceStatus(ok ? `Saved “${message}” on GPIO${gpio}` : 'Saved — refresh list to confirm', ok ? 'ok' : '');
        if (ok) resetForm();
      } catch (err) {
        setDeviceStatus('Save failed: ' + err.message, 'fail');
      }
    }

    async function listTriggers() {
      try {
        if (!monitorConnected) await monitorConnect();
        return await sendCommand('!dmtrigger:list', true);
      } catch (err) {
        setDeviceStatus('List failed: ' + err.message, 'fail');
        return null;
      }
    }

    async function deleteSlot(slot) {
      if (!confirm(`Delete trigger slot ${slot}?`)) return;
      try {
        await sendCommand(`!dmtrigger:del|${Number(slot)}`);
        await listTriggers();
      } catch (err) {
        setDeviceStatus('Delete failed: ' + err.message, 'fail');
      }
    }

    async function clearTriggers() {
      if (!confirm('Remove all triggers from the device?')) return;
      try {
        await sendCommand('!dmtrigger:clear');
        await listTriggers();
      } catch (err) {
        setDeviceStatus('Clear failed: ' + err.message, 'fail');
      }
    }

    function renderTriggers(text) {
      const tbody = document.getElementById('triggerRows');
      const rows = [];
      text.split(/\n/).forEach(line => {
        const m = line.match(/^(\d+):([^:]*):([^:]*):([^:]*):gpio(\d+)(?::(\d+)ms)?/);
        if (m) rows.push({slot:m[1], name:m[2], type:m[3], message:m[4], gpio:m[5], duration:m[6]||''});
      });
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty">No triggers on device.</td></tr>';
        return;
      }
      tbody.innerHTML = rows.map(r => {
        const detail = r.type === 'output'
          ? `GPIO${r.gpio}${r.duration ? ' · ' + r.duration + 'ms' : ''}`
          : `GPIO${r.gpio} ADC`;
        return `<tr>
          <td><code>${escapeHtml(r.message)}</code></td>
          <td><span class="pill">${r.type}</span></td>
          <td>${detail}</td>
          <td style="white-space:nowrap">
            <button onclick='editTrigger(${r.slot},${JSON.stringify(r.name)},${JSON.stringify(r.type)},${JSON.stringify(r.message)},${r.gpio},${JSON.stringify(r.duration)})'>Edit</button>
            <button class="danger" onclick="deleteSlot(${r.slot})">Delete</button>
          </td>
        </tr>`;
      }).join('');
    }

    function escapeHtml(s) {
      return String(s).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }

    async function toggleConnect() {
      if (monitorConnected) await monitorDisconnect();
      else await monitorConnect();
    }

    async function monitorConnect() {
      const btn = document.getElementById('connectBtn');
      btn.disabled = true;
      try {
        const data = await api('/api/monitor/connect', {port: currentPort()}, 90000);
        monitorConnected = true;
        monitorCursor = data.next || 0;
        btn.textContent = 'Disconnect';
        const who = [data.short_name, data.node_hex].filter(Boolean).join(' ');
        setDeviceStatus(who ? `Connected · ${who}` : `Connected · ${data.port || currentPort()}`, 'ok');
        document.getElementById('monitorStatus').textContent = 'Connected — watching device log';
        document.getElementById('monitorStatus').className = 'status ok';
        await listTriggers();
      } catch (err) {
        setDeviceStatus('Connect failed: ' + err.message, 'fail');
      } finally {
        btn.disabled = false;
        updateWriteFirmwareButton();
      }
    }

    async function monitorDisconnect() {
      try {
        await api('/api/monitor/disconnect', {}, 15000);
      } catch (_) {}
      monitorConnected = false;
      document.getElementById('connectBtn').textContent = 'Connect';
      setDeviceStatus('Disconnected');
      document.getElementById('monitorStatus').textContent = 'Disconnected';
      document.getElementById('monitorStatus').className = 'status';
    }

    async function monitorSend() {
      const input = document.getElementById('monitorInput');
      const text = input.value.trim();
      if (!text) return;
      try {
        if (!monitorConnected) await monitorConnect();
        appendMonitorLocal('> ' + text);
        await api('/api/monitor/send', {port: currentPort(), text}, 30000);
        input.value = '';
      } catch (err) {
        appendMonitorLocal('! ' + err.message);
      }
    }

    function startMonitorPolling() {
      setInterval(async () => {
        try {
          const st = await api(`/api/monitor/log?since=${monitorCursor}`, null, 5000);
          const lines = st.lines || [];
          if (lines.length) {
            const el = document.getElementById('serialMonitor');
            if (el.textContent.startsWith('Connect to see')) el.textContent = '';
            el.textContent += (el.textContent ? '\n' : '') + lines.join('\n');
            el.scrollTop = el.scrollHeight;
            monitorCursor = st.next || (monitorCursor + lines.length);
          }
          if (st.connected && !monitorConnected) {
            monitorConnected = true;
            document.getElementById('connectBtn').textContent = 'Disconnect';
          } else if (!st.connected && monitorConnected && !st.flashing) {
            monitorConnected = false;
            document.getElementById('connectBtn').textContent = 'Connect';
            setDeviceStatus('Disconnected');
          }
        } catch (_) {}
      }, 400);
    }

    async function writeFirmware() {
      const port = currentPort();
      if (!port || flashBusy) return;
      if (!confirm(`Flash ${currentBoard().label} firmware (${currentBoard().env}) to ${port}?\\n\\nDo not unplug during the write.`)) return;
      if (monitorConnected) await monitorDisconnect();

      const wrap = document.getElementById('flashConsoleWrap');
      const consoleEl = document.getElementById('flashConsole');
      const statusEl = document.getElementById('flashStatus');
      const btn = document.getElementById('writeFirmware');
      flashBusy = true;
      updateWriteFirmwareButton();
      btn.textContent = 'Writing…';
      wrap.style.display = 'block';
      consoleEl.textContent = '';
      statusEl.className = 'status running';
      statusEl.textContent = 'Starting…';

      try {
        await api('/api/flash', {port, env: currentBoard().env}, 15000);
        let cursor = 0;
        const startedAt = Date.now();
        while (true) {
          if (Date.now() - startedAt > 20 * 60 * 1000) throw new Error('Timed out after 20 minutes');
          const st = await api(`/api/flash/status?since=${cursor}`, null, 15000);
          const lines = st.lines || [];
          if (lines.length) {
            consoleEl.textContent += (consoleEl.textContent ? '\\n' : '') + lines.join('\\n');
            consoleEl.scrollTop = consoleEl.scrollHeight;
            cursor = st.next || (cursor + lines.length);
          }
          if (st.running) {
            statusEl.textContent = `Running… ${Math.round((Date.now() - startedAt) / 1000)}s`;
            await new Promise(r => setTimeout(r, 500));
            continue;
          }
          if (st.success) {
            statusEl.className = 'status ok';
            statusEl.textContent = 'Firmware write succeeded';
          } else {
            throw new Error(st.error || 'Firmware write failed');
          }
          break;
        }
      } catch (err) {
        statusEl.className = 'status fail';
        statusEl.textContent = 'Failed: ' + err.message;
      } finally {
        flashBusy = false;
        btn.textContent = 'Write firmware to board';
        updateWriteFirmwareButton();
      }
    }

    fillBoardSelect();
    fillGpioSelect();
    updateType();
    document.getElementById('port').addEventListener('change', updateWriteFirmwareButton);
    document.getElementById('monitorInput').addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') monitorSend();
    });
    refreshPorts();
    startMonitorPolling();
  </script>
</body>
</html>
"""


def list_ports() -> list[str]:
    # Prefer stable short device nodes; by-id aliases map to the same TTY and confuse locking.
    ports = sorted(set(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")))
    if ports:
        return ports
    return sorted(set(glob.glob("/dev/serial/by-id/*")))


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


def _busy_port_error(port: str | None, exc: BaseException) -> RuntimeError:
    label = port or "the selected serial port"
    return RuntimeError(
        f"Serial port busy ({label}). Close anything else using it, then retry:\n"
        "  - pio device monitor / PlatformIO Serial Monitor\n"
        "  - Meshtastic desktop/CLI connected to the same USB port\n"
        "  - another terminal or cursor serial session\n"
        f"Check with: lsof {port or '/dev/ttyUSB*'}\n"
        f"Original error: {exc}"
    )


FLASH_LOCK = threading.Lock()


class _DebugTee:
    """Captures meshtastic debugOut writes into the monitor ring buffer."""

    def __init__(self, sink: "PortSession") -> None:
        self._sink = sink
        self._buf = ""

    def write(self, data: str) -> int:
        if not data:
            return 0
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip("\r")
            if line:
                self._sink.append_monitor(line)
        return len(data)

    def flush(self) -> None:
        if self._buf.strip():
            self._sink.append_monitor(self._buf.rstrip("\r\n"))
            self._buf = ""


class PortSession:
    """Single shared Meshtastic serial connection; serializes commands + monitor log."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._iface: Any = None
        self._port: str | None = None
        self._replies: list[str] = []
        self._reply_lock = threading.Lock()
        self._subscribed = False
        self._monitor: list[str] = []
        self._monitor_lock = threading.Lock()
        self._debug_out = _DebugTee(self)

    def append_monitor(self, line: str) -> None:
        text = _ANSI_RE.sub("", line).rstrip("\r\n")
        if not text:
            return
        with self._monitor_lock:
            # Drop exact consecutive duplicates (debugOut + log.line often both fire).
            if self._monitor and self._monitor[-1] == text:
                return
            self._monitor.append(text)
            if len(self._monitor) > 4000:
                self._monitor = self._monitor[-3000:]

    def clear_monitor(self) -> None:
        with self._monitor_lock:
            self._monitor.clear()

    def monitor_snapshot(self, since: int = 0) -> dict[str, Any]:
        with self._monitor_lock:
            since = max(0, min(since, len(self._monitor)))
            lines = self._monitor[since:]
            nxt = len(self._monitor)
        return {
            "ok": True,
            "lines": lines,
            "next": nxt,
            "connected": self._iface is not None,
            "port": self._port,
            "flashing": FLASH_LOCK.locked(),
        }

    def close(self) -> None:
        with self._lock:
            was = self._iface is not None
            self._close_unlocked()
            if was:
                self.append_monitor("[session] disconnected")

    def _close_unlocked(self) -> None:
        iface = self._iface
        self._iface = None
        self._port = None
        if iface is not None:
            try:
                if hasattr(iface, "_wantExit"):
                    iface._wantExit = True
                stream = getattr(iface, "stream", None)
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                time.sleep(0.2)
                iface.close()
            except Exception:
                pass
        if self._subscribed:
            try:
                from pubsub import pub

                pub.unsubscribe(self._on_receive, "meshtastic.receive")
            except Exception:
                pass
            self._subscribed = False

    def _on_receive(self, packet: dict[str, Any], interface: Any = None) -> None:
        text = _packet_text(packet)
        if not text:
            return
        frm = packet.get("fromId") or packet.get("from") or "?"
        to = packet.get("toId") or packet.get("to") or "?"
        self.append_monitor(f"[rx] from={frm} to={to} text={text}")
        interesting = (
            text.startswith("Triggers")
            or text.startswith("Trigger")
            or text.startswith("Set failed")
            or text.startswith("Delete failed")
            or text.startswith("Unknown !dmtrigger")
            or text.startswith("Voltage")
            or ": " in text and text.endswith("V")
        )
        if interesting:
            with self._reply_lock:
                self._replies.append(text)

    def connect(self, port: str | None) -> dict[str, Any]:
        if FLASH_LOCK.locked():
            raise RuntimeError("Firmware write is in progress — wait until it finishes, then Connect.")
        with self._lock:
            iface = self._ensure_open(port)
            node = getattr(iface.localNode, "nodeNum", None)
            short_name = ""
            long_name = ""
            channel_note = ""
            try:
                info = iface.getMyNodeInfo() or {}
                user = info.get("user") or {}
                short_name = str(user.get("shortName") or "")
                long_name = str(user.get("longName") or "")
            except Exception:
                pass
            try:
                channels = getattr(iface.localNode, "channels", None) or []
                primary = None
                for ch in channels:
                    # role PRIMARY == 1 in protobuf
                    if getattr(ch, "role", 0) == 1:
                        primary = ch
                        break
                if primary is not None:
                    settings = primary.settings
                    name = getattr(settings, "name", "") or "(unnamed)"
                    psk = getattr(settings, "psk", b"") or b""
                    if len(psk) <= 1:
                        channel_note = (
                            f"Primary channel is <b>default/public</b> ({name}). "
                            "If your other radio uses a private NWIMesh (or custom) channel, "
                            "this Heltec will never decrypt <code>read5</code> — put both on the same channel URL."
                        )
                    else:
                        channel_note = f"Primary channel <code>{name}</code> uses a custom PSK ({len(psk)} bytes)."
            except Exception as exc:
                channel_note = f"(Could not read channels: {exc})"
            self.append_monitor(
                f"[session] connected port={self._port} node=0x{(node or 0):08x} short={short_name or '?'}"
            )
            return {
                "ok": True,
                "port": self._port,
                "node": node,
                "node_hex": f"!{(node or 0):08x}",
                "short_name": short_name,
                "long_name": long_name,
                "channel_note": channel_note,
                "next": len(self._monitor),
            }

    def disconnect(self) -> dict[str, Any]:
        self.close()
        return {"ok": True}

    def _ensure_open(self, port: str | None) -> Any:
        try:
            import meshtastic.serial_interface
            from pubsub import pub
        except ImportError as exc:
            raise RuntimeError(
                "Missing meshtastic/pypubsub. Install into the repo venv, then restart this server:\n"
                "  .venv/bin/pip install meshtastic pypubsub\n"
                "  .venv/bin/python bin/dm-trigger-web.py --port 8088"
            ) from exc

        if self._iface is not None and self._port == port:
            return self._iface

        self._close_unlocked()
        last_exc: BaseException | None = None
        for attempt in range(2):
            try:
                kwargs = dict(noNodes=True, timeout=60, debugOut=self._debug_out)
                self._iface = (
                    meshtastic.serial_interface.SerialInterface(devPath=port, **kwargs)
                    if port
                    else meshtastic.serial_interface.SerialInterface(**kwargs)
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                self._close_unlocked()
                msg = str(exc)
                if "exclusively lock" in msg or getattr(exc, "errno", None) == 11:
                    raise _busy_port_error(port, exc) from exc
                if attempt == 0:
                    time.sleep(2.0)
                    continue
                if "Timed out waiting for connection" in msg:
                    raise RuntimeError(
                        f"Timed out connecting to Meshtastic on {port or 'auto'}.\n"
                        "Close other serial tools, wait a few seconds (or unplug/replug USB), then try again.\n"
                        "Also make sure the device is running Meshtastic firmware and is not stuck in the bootloader."
                    ) from exc
                raise

        if last_exc is not None or self._iface is None:
            raise RuntimeError(f"Failed to open serial port {port}: {last_exc}")

        self._port = port
        if not self._subscribed:
            pub.subscribe(self._on_receive, "meshtastic.receive")
            self._subscribed = True
        time.sleep(0.8)
        return self._iface

    def send_text(self, port: str | None, text: str, wait_s: float = 0.3) -> None:
        if FLASH_LOCK.locked():
            raise RuntimeError("Firmware write is in progress.")
        if not text:
            raise ValueError("Empty command")
        with self._lock:
            iface = self._ensure_open(port)
            node_num = iface.localNode.nodeNum
            self.append_monitor(f"[tx] to=self text={text}")
            iface.sendText(text, destinationId=node_num, wantAck=False)
            time.sleep(wait_s)

    def send_command(self, port: str | None, command: str, wait_s: float = 4.0) -> list[str]:
        if FLASH_LOCK.locked():
            raise RuntimeError(
                "Firmware write is in progress. Wait until it finishes before sending config commands."
            )
        with self._lock:
            with self._reply_lock:
                self._replies.clear()
            try:
                iface = self._ensure_open(port)
                node_num = iface.localNode.nodeNum
                self.append_monitor(f"[tx] to=self text={command}")
                iface.sendText(command, destinationId=node_num, wantAck=True)
                time.sleep(wait_s)
            except Exception as exc:
                msg = str(exc)
                if "exclusively lock" in msg or getattr(exc, "errno", None) == 11:
                    self._close_unlocked()
                    raise _busy_port_error(port, exc) from exc
                self._close_unlocked()
                raise
            with self._reply_lock:
                return self._replies[:]


SESSION = PortSession()
FIRMWARE_ROOT = Path(__file__).resolve().parents[1]


def _resolve_pio() -> str:
    candidates = [
        shutil.which("pio"),
        str(Path.home() / ".platformio" / "penv" / "bin" / "pio"),
        str(FIRMWARE_ROOT / ".venv" / "bin" / "pio"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("PlatformIO `pio` not found on PATH or in ~/.platformio/penv/bin/pio")


class FlashJob:
    """Background pio build/upload with a ring buffer of console lines."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.lines: list[str] = []
        self.running = False
        self.ok = False
        self.error: str | None = None
        self.started_at = 0.0
        self.finished_at = 0.0
        self._thread: threading.Thread | None = None

    def status(self, since: int = 0) -> dict[str, Any]:
        with self._lock:
            since = max(0, min(since, len(self.lines)))
            finished = self.finished_at > 0 and not self.running
            return {
                "ok": True,
                "running": self.running,
                "success": finished and self.error is None,
                "finished": finished,
                "lines": self.lines[since:],
                "next": len(self.lines),
                "error": self.error,
                "elapsed_s": round((time.time() - self.started_at), 1) if self.started_at else 0,
            }

    def _append(self, line: str) -> None:
        text = line.rstrip("\r\n")
        if not text:
            return
        with self._lock:
            self.lines.append(text)
            if len(self.lines) > 5000:
                self.lines = self.lines[-4000:]
        print(text, flush=True)

    def start(self, port: str, env: str = "heltec-v3") -> None:
        if not port:
            raise ValueError("Serial port is required for firmware write")
        if env not in BOARDS:
            allowed = ", ".join(sorted(BOARDS))
            raise ValueError(f"Unsupported board env {env!r}. Use one of: {allowed}")
        if not Path(port).exists():
            raise RuntimeError(f"Serial port does not exist: {port}")
        if not FLASH_LOCK.acquire(blocking=False):
            raise RuntimeError("A firmware write is already in progress")

        with self._lock:
            self.lines = []
            self.running = True
            self.ok = False
            self.error = None
            self.started_at = time.time()
            self.finished_at = 0.0

        self._thread = threading.Thread(target=self._run, args=(port, env), daemon=True, name="flash-job")
        self._thread.start()

    def _run(self, port: str, env: str) -> None:
        proc: subprocess.Popen[str] | None = None
        try:
            self._append("Closing Meshtastic serial session so PlatformIO can use the port…")
            SESSION.close()
            time.sleep(0.5)

            pio = _resolve_pio()
            jobs = str(os.cpu_count() or 2)
            cmd = [
                pio,
                "run",
                "-e",
                env,
                "-j",
                jobs,
                "-t",
                "upload",
                "--upload-port",
                port,
            ]
            self._append("Command: " + " ".join(cmd))
            self._append(f"Working directory: {FIRMWARE_ROOT}")
            self._append("Do not unplug the device.")

            child_env = os.environ.copy()
            child_env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                cmd,
                cwd=str(FIRMWARE_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=child_env,
            )
            assert proc.stdout is not None
            deadline = time.time() + 20 * 60
            while True:
                if time.time() > deadline:
                    proc.kill()
                    raise RuntimeError("Firmware write timed out after 20 minutes")
                line = proc.stdout.readline()
                if line:
                    self._append(line)
                    continue
                if proc.poll() is not None:
                    # Drain any remaining buffered output.
                    for leftover in proc.stdout.readlines():
                        self._append(leftover)
                    break
                time.sleep(0.05)

            code = proc.wait()
            if code != 0:
                raise RuntimeError(f"pio upload failed (exit {code})")
            self._append("=== Firmware write SUCCESS ===")
            with self._lock:
                self.ok = True
                self.error = None
        except Exception as exc:
            self._append(f"=== Firmware write FAILED: {exc} ===")
            with self._lock:
                self.ok = False
                self.error = str(exc)
        finally:
            if proc is not None and proc.poll() is None:
                with contextlib.suppress(Exception):
                    proc.kill()
            with self._lock:
                self.running = False
                self.finished_at = time.time()
            FLASH_LOCK.release()


FLASH_JOB = FlashJob()


class Handler(BaseHTTPRequestHandler):
    server_version = "DmTriggerWeb/0.2"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path.startswith("/index.html"):
            body = HTML.replace("__BOARDS__", json.dumps(BOARDS)).encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/api/ports"):
            self._send_json({"ok": True, "ports": list_ports()})
            return
        if parsed.path.startswith("/api/flash/status"):
            qs = parse_qs(parsed.query)
            try:
                since = int((qs.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            self._send_json(FLASH_JOB.status(since))
            return
        if parsed.path.startswith("/api/monitor/log"):
            qs = parse_qs(parsed.query)
            try:
                since = int((qs.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            self._send_json(SESSION.monitor_snapshot(since))
            return
        self._send_json({"ok": False, "error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/command":
            try:
                payload = self._read_json()
                command = str(payload.get("command", ""))
                if not command.startswith("!dmtrigger:"):
                    raise ValueError("Only !dmtrigger: commands are allowed")
                port = payload.get("port")
                if port is not None:
                    port = str(port)
                replies = SESSION.send_command(port, command)
                self._send_json({"ok": True, "replies": replies})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        if self.path == "/api/monitor/connect":
            try:
                payload = self._read_json()
                port = payload.get("port")
                if port is not None:
                    port = str(port)
                self._send_json(SESSION.connect(port))
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        if self.path == "/api/monitor/disconnect":
            try:
                self._send_json(SESSION.disconnect())
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        if self.path == "/api/monitor/clear":
            try:
                SESSION.clear_monitor()
                self._send_json({"ok": True})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        if self.path == "/api/monitor/send":
            try:
                payload = self._read_json()
                text = str(payload.get("text") or "").strip()
                if not text:
                    raise ValueError("Empty text")
                # Cap length to avoid accidental flood; firmware TEXT_MESSAGE is ~200B.
                if len(text) > 200:
                    raise ValueError("Text too long (max 200 characters)")
                port = payload.get("port")
                if port is not None:
                    port = str(port)
                SESSION.send_text(port, text)
                self._send_json({"ok": True})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        if self.path == "/api/flash":
            try:
                payload = self._read_json()
                port = str(payload.get("port") or "")
                env = str(payload.get("env") or "heltec-v3")
                FLASH_JOB.start(port, env)
                self._send_json({"ok": True, "started": True})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        self._send_json({"ok": False, "error": "not found"}, status=404)

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
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Browser often aborts while we are still waiting on serial connect.
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()

    # Use a single-thread server so HTTP handlers never race on the serial port.
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"DM trigger configurator running at http://{args.host}:{args.port}")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        try:
            SESSION.close()
        except KeyboardInterrupt:
            pass
        try:
            server.server_close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

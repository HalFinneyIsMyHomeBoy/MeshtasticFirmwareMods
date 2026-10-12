#!/usr/bin/env python3
"""Local web configurator for DM GPIO triggers (Heltec V3 / V4).

Includes a Device console (Meshtastic serial session) to send DMs / !dmtrigger:
commands and stream replies + firmware log lines.

Setup (use the repo venv — do not pip install into system Python):

  .venv/bin/pip install -r bin/dm-trigger-requirements.txt

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
    input, select, textarea, button { width:100%; border-radius:8px; border:1px solid var(--line); background:#0e141a; color:var(--text); padding:10px 12px; font:inherit; }
    textarea { min-height:72px; resize:vertical; }
    label .count { float:right; }
    label .count.at-limit { color:var(--danger); font-weight:600; }
    textarea.at-limit { border-color:var(--danger); }
    .field-warn { color:var(--danger); font-size:13px; margin:4px 0 0; }
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
    table { width:100%; max-width:100%; border-collapse:collapse; table-layout:fixed; }
    th, td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--line); font-size:14px; vertical-align:top; overflow-wrap:anywhere; word-break:break-word; }
    td code { overflow-wrap:anywhere; word-break:break-word; }
    col.msg { width:28%; }
    col.kind { width:5.5rem; }
    col.act { width:9.5rem; }
    td.actions { overflow-wrap:normal; word-break:normal; }
    td.actions button { display:inline-block; width:auto; margin:0 4px 4px 0; }
    td.detail { white-space:pre-wrap; }
    th { color:var(--muted); font-weight:600; }
    .pill { display:inline-block; padding:2px 7px; border-radius:999px; background:#24313c; font-size:12px; }
    .empty { color:var(--muted); font-size:14px; }
    .loading-row td { border-bottom:none; padding:16px 8px; }
    .loading { display:flex; align-items:center; gap:12px; color:var(--muted); font-size:14px; }
    .loading strong { display:block; color:var(--text); font-weight:600; }
    .spinner { width:16px; height:16px; flex:none; border:2px solid var(--line); border-top-color:var(--accent); border-radius:50%; animation:spin .8s linear infinite; }
    @keyframes spin { to { transform:rotate(360deg); } }
    .mono { white-space:pre-wrap; font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size:12px; line-height:1.4; min-height:160px; max-height:320px; overflow:auto; background:#070b0f; border:1px solid var(--line); border-radius:8px; padding:10px; color:#b7c7d4; }
    details { margin-top:8px; }
    details summary { cursor:pointer; color:var(--muted); font-size:13px; padding:6px 0; }
    .hint { color:var(--muted); font-size:13px; margin-top:8px; }
    .identity { margin-top:10px; padding:10px 12px; background:#121920; border:1px solid var(--line); border-radius:8px; }
    .identity div + div { margin-top:4px; }
    .identity .label { color:var(--muted); display:inline-block; width:96px; }
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
      <div id="deviceIdentity" class="identity" hidden>
        <div><span class="label">Long name</span> <strong id="longName">—</strong></div>
        <div><span class="label">Short name</span> <strong id="shortName">—</strong></div>
      </div>
    </section>

    <section>
      <h2>2. Saved triggers</h2>
      <table>
        <colgroup><col class="msg"><col class="kind"><col class="detail"><col class="act"></colgroup>
        <thead><tr><th>Message</th><th>Type</th><th>Detail</th><th></th></tr></thead>
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
      <label for="message">Direct-message text (exact match) <span id="messageCount" class="count">0/233 bytes</span></label>
      <textarea id="message" rows="3" placeholder="open7" onbeforeinput="guardLimit(event)" oninput="updateCounts()"></textarea>
      <p id="messageWarn" class="field-warn" hidden>233 bytes is the maximum.</p>
      <label for="name">Label</label>
      <input id="name" maxlength="19" placeholder="Relay 7">
      <div class="row">
        <div>
          <label for="type">Action</label>
          <select id="type" onchange="updateType()">
            <option value="output">Pulse GPIO (relay)</option>
            <option value="analog">Read voltage (ADC)</option>
            <option value="reply">Reply with text</option>
          </select>
        </div>
        <div id="gpioBox">
          <label for="gpio">GPIO pin</label>
          <select id="gpio"></select>
        </div>
      </div>
      <div id="replyBox" style="display:none">
        <label for="reply">Reply text <span id="replyCount" class="count">0/233 bytes</span></label>
        <textarea id="reply" rows="3" placeholder="The door is opening" onbeforeinput="guardLimit(event)" oninput="updateCounts()"></textarea>
        <p id="replyWarn" class="field-warn" hidden>233 bytes is the maximum.</p>
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
      <p class="hint">Match text and reply text can each be 233 bytes, the size of one Meshtastic message. Write firmware once so the device accepts that length. Later text edits do not need another write.</p>
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

    function showDeviceNames(longName, shortName) {
      const box = document.getElementById('deviceIdentity');
      document.getElementById('longName').textContent = longName || '—';
      document.getElementById('shortName').textContent = shortName || '—';
      box.hidden = false;
    }

    function hideDeviceNames() {
      document.getElementById('deviceIdentity').hidden = true;
      document.getElementById('longName').textContent = '—';
      document.getElementById('shortName').textContent = '—';
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
      const type = document.getElementById('type').value;
      const isAnalog = type === 'analog';
      const isReply = type === 'reply';
      document.getElementById('durationBox').style.display = (isAnalog || isReply) ? 'none' : 'grid';
      document.getElementById('analogBox').style.display = isAnalog ? 'block' : 'none';
      document.getElementById('gpioBox').style.display = isReply ? 'none' : 'block';
      document.getElementById('replyBox').style.display = isReply ? 'block' : 'none';
      if (!isReply) fillGpioSelect();
    }

    const TEXT_BYTES = 233;
    let triggerCache = [];

    function utf8Size(value) {
      return new TextEncoder().encode(value).length;
    }

    function clampToBytes(value) {
      if (utf8Size(value) <= TEXT_BYTES) return value;
      let out = '';
      let used = 0;
      for (const ch of value) {
        const n = utf8Size(ch);
        if (used + n > TEXT_BYTES) break;
        out += ch;
        used += n;
      }
      return out;
    }

    function applyLimit(id, countId, warnId) {
      const el = document.getElementById(id);
      const count = document.getElementById(countId);
      const warn = document.getElementById(warnId);
      const clamped = clampToBytes(el.value);
      if (clamped !== el.value) {
        const caret = el.selectionStart || 0;
        el.value = clamped;
        const pos = Math.min(caret, clamped.length);
        el.setSelectionRange(pos, pos);
      }
      const full = utf8Size(el.value) >= TEXT_BYTES;
      count.textContent = utf8Size(el.value) + '/' + TEXT_BYTES + ' bytes';
      count.classList.toggle('at-limit', full);
      el.classList.toggle('at-limit', full);
      warn.hidden = !full;
    }

    function guardLimit(event) {
      const el = event.target;
      if (event.inputType && event.inputType.startsWith('delete')) return;
      if (event.data == null) return;
      const start = el.selectionStart ?? el.value.length;
      const end = el.selectionEnd ?? start;
      const prefix = el.value.slice(0, start);
      const suffix = el.value.slice(end);
      const next = prefix + event.data + suffix;
      if (utf8Size(next) <= TEXT_BYTES) return;
      event.preventDefault();
      const budget = TEXT_BYTES - utf8Size(prefix) - utf8Size(suffix);
      let extra = '';
      let used = 0;
      if (budget > 0) {
        for (const ch of event.data) {
          const n = utf8Size(ch);
          if (used + n > budget) break;
          extra += ch;
          used += n;
        }
      }
      if (extra) {
        el.value = prefix + extra + suffix;
        const pos = prefix.length + extra.length;
        el.setSelectionRange(pos, pos);
      }
      updateCounts();
    }

    function updateCounts() {
      applyLimit('message', 'messageCount', 'messageWarn');
      applyLimit('reply', 'replyCount', 'replyWarn');
    }

    function resetForm() {
      editingSlot = -1;
      document.getElementById('index').value = '-1';
      document.getElementById('name').value = '';
      document.getElementById('message').value = '';
      document.getElementById('reply').value = '';
      document.getElementById('type').value = 'output';
      document.getElementById('duration').value = '10000';
      document.getElementById('multiplier').value = '1.0';
      updateType();
      updateCounts();
    }

    function editTrigger(slot, name, type, message, gpio, duration, reply, multiplier) {
      editingSlot = Number(slot);
      document.getElementById('index').value = String(slot);
      document.getElementById('name').value = name;
      document.getElementById('message').value = message;
      document.getElementById('reply').value = reply || '';
      document.getElementById('type').value = (type === 'analog' || type === 'reply') ? type : 'output';
      updateType();
      if (type !== 'reply') document.getElementById('gpio').value = String(gpio);
      if (duration) document.getElementById('duration').value = String(duration);
      if (multiplier) document.getElementById('multiplier').value = String(multiplier);
      updateCounts();
      document.getElementById('message').focus();
    }

    function editCached(index) {
      const row = triggerCache[index];
      if (!row) return;
      editTrigger(row.slot, row.name, row.type, row.message, row.gpio, row.duration, row.reply, row.multiplier);
    }

    async function sendCommand(command, parseList = false) {
      const data = await api('/api/command', {port: currentPort(), command}, 90000);
      const replies = data.replies || [];
      if (replies.length) appendMonitorLocal(replies.join('\n'));
      if (parseList) renderTriggers(replies);
      return data;
    }

    function validateForm() {
      const name = document.getElementById('name').value.trim();
      const type = document.getElementById('type').value;
      const message = document.getElementById('message').value;
      const reply = document.getElementById('reply').value;
      const gpio = Number(document.getElementById('gpio').value);
      const duration = Number(document.getElementById('duration').value);
      if (!message.trim()) throw new Error('Message text is required.');
      if (name.includes('|')) throw new Error('Label cannot contain |.');
      if (utf8Size(message) > TEXT_BYTES) throw new Error('Match text is longer than 233 bytes.');
      if (utf8Size(reply) > TEXT_BYTES) throw new Error('Reply text is longer than 233 bytes.');
      if (!name) document.getElementById('name').value = message.trim().slice(0, 19);
      if (type === 'reply') {
        if (!reply.trim()) throw new Error('Reply text is required.');
        return;
      }
      const pin = pinout.find(p => p.gpio === gpio);
      if (!pin) throw new Error(`GPIO${gpio} is not allowed.`);
      if (type === 'output' && !pin.output) throw new Error(`GPIO${gpio} cannot be an output.`);
      if (type === 'analog' && !pin.analog) throw new Error(`GPIO${gpio} is not ADC-capable.`);
      if (type === 'output' && duration <= 0) throw new Error('Hold time must be > 0.');
    }

    const PACKET_BYTES = 200;

    function chunkFrom(text, index, field, byteOffset) {
      const header = `!dmtrigger:put|${index}|${field}|${byteOffset}|`;
      const headerBytes = utf8Size(header);
      let buf = '';
      let bufBytes = 0;
      let seen = 0;
      for (const ch of text) {
        const n = utf8Size(ch);
        if (seen < byteOffset) {
          seen += n;
          continue;
        }
        if (headerBytes + bufBytes + n > PACKET_BYTES) break;
        buf += ch;
        bufBytes += n;
        seen += n;
      }
      if (!buf) throw new Error('A single character does not fit in one message.');
      return buf;
    }

    function rewindBytes(text, byteOffset) {
      let seen = 0;
      for (const ch of text) {
        const n = utf8Size(ch);
        if (seen + n > byteOffset) return seen;
        seen += n;
        if (seen === byteOffset) return seen;
      }
      return seen;
    }

    async function putField(index, field, text) {
      let byteOffset = 0;
      const total = utf8Size(text);
      while (byteOffset < total) {
        const before = byteOffset;
        const chunk = chunkFrom(text, index, field, byteOffset);
        const replies = await expectOk(`!dmtrigger:put|${index}|${field}|${byteOffset}|${chunk}`);
        const match = replies.match(/Part ok\|(\d+)/);
        let stored = match ? Number(match[1]) : (replies.includes('Part ok') ? before + utf8Size(chunk) : NaN);
        if (!Number.isFinite(stored) || stored <= before) {
          throw new Error(replies.trim() || 'Device did not accept the text');
        }
        byteOffset = stored < before + utf8Size(chunk) ? rewindBytes(text, stored) : stored;
        if (byteOffset <= before) throw new Error('Device stopped accepting text at ' + stored + ' bytes');
      }
    }

    async function expectOk(command) {
      const data = await sendCommand(command);
      const replies = (data.replies || []).join('\n');
      if (replies.includes('Unknown !dmtrigger')) {
        throw new Error('Write firmware to the board first. This device build does not store 233-byte trigger text yet.');
      }
      if (replies.includes('Set failed') || replies.includes('Not authorized')) {
        throw new Error(replies.trim() || 'Save rejected by device');
      }
      return replies;
    }

    async function saveTrigger() {
      try {
        if (!monitorConnected) await monitorConnect();
        validateForm();
        const index = document.getElementById('index').value;
        const name = document.getElementById('name').value.trim();
        const type = document.getElementById('type').value;
        const message = document.getElementById('message').value;
        const reply = type === 'reply' ? document.getElementById('reply').value : '';
        const gpio = type === 'reply' ? 0 : document.getElementById('gpio').value;
        const duration = type === 'output' ? document.getElementById('duration').value : 0;
        const multiplier = type === 'analog' ? document.getElementById('multiplier').value : 1.0;
        await expectOk(`!dmtrigger:open|${index}|${name}|${type}|${gpio}|${duration}|${multiplier}`);
        await putField(index, 'm', message);
        if (reply) await putField(index, 'r', reply);
        const savedReplies = await expectOk(`!dmtrigger:commit|${index}`);
        await listTriggers();
        const ok = savedReplies.includes('Trigger saved');
        const preview = message.trim().slice(0, 40);
        const saved = type === 'reply' ? `Saved “${preview}”` : `Saved “${preview}” on GPIO${gpio}`;
        setDeviceStatus(ok ? saved : 'Saved — refresh list to confirm', ok ? 'ok' : '');
        if (ok) resetForm();
      } catch (err) {
        setDeviceStatus('Save failed: ' + err.message, 'fail');
      }
    }

    function setTriggerNote(text) {
      document.getElementById('triggerRows').innerHTML =
        '<tr><td colspan="4" class="empty">' + escapeHtml(text) + '</td></tr>';
    }

    function setTriggerLoading() {
      document.getElementById('triggerRows').innerHTML =
        '<tr class="loading-row"><td colspan="4"><div class="loading"><span class="spinner" aria-hidden="true"></span><span><strong>Loading saved triggers</strong>Checking the device. This usually takes a few seconds.</span></div></td></tr>';
    }

    async function listTriggers() {
      setTriggerLoading();
      try {
        if (!monitorConnected) await monitorConnect();
        return await sendCommand('!dmtrigger:list', true);
      } catch (err) {
        setTriggerNote('Could not load saved triggers.');
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

    function joinChunks(chunks) {
      return chunks.sort((a, b) => a.offset - b.offset).map(c => c.data).join('');
    }

    function parseChunk(packet, prefix) {
      if (!packet.startsWith(prefix)) return null;
      const body = packet.slice(prefix.length);
      const first = body.indexOf('|');
      const second = body.indexOf('|', first + 1);
      if (first < 0 || second < 0) return null;
      return {
        slot: body.slice(0, first),
        offset: Number(body.slice(first + 1, second)),
        data: body.slice(second + 1),
      };
    }

    function renderTriggers(replies) {
      const tbody = document.getElementById('triggerRows');
      const packets = Array.isArray(replies) ? replies : String(replies || '').split('\n');
      const bySlot = new Map();
      const oldLines = [];
      let sawNew = false;
      packets.forEach(packet => {
        const messageChunk = parseChunk(packet, 'TriggerM|');
        const replyChunk = parseChunk(packet, 'TriggerR|');
        if (messageChunk || replyChunk) {
          sawNew = true;
          const chunk = messageChunk || replyChunk;
          const row = bySlot.get(chunk.slot) || {slot: chunk.slot, name: '', type: '', message: '', gpio: '0', duration: '', reply: '', multiplier: '1', messageChunks: [], replyChunks: []};
          (messageChunk ? row.messageChunks : row.replyChunks).push(chunk);
          bySlot.set(chunk.slot, row);
          return;
        }
        if (packet.startsWith('Trigger|')) {
          sawNew = true;
          const parts = packet.split('|');
          const slot = parts[1] || '0';
          const row = bySlot.get(slot) || {slot, name: '', type: '', message: '', gpio: '0', duration: '', reply: '', multiplier: '1', messageChunks: [], replyChunks: []};
          row.name = parts[2] || '';
          row.type = parts[3] || '';
          row.gpio = parts[4] || '0';
          row.duration = parts[5] || '';
          row.multiplier = parts[6] || '1';
          bySlot.set(slot, row);
          return;
        }
        String(packet).split('\n').forEach(line => {
          const m = line.match(/^(\d+):([^:]*):([^:]*):([^:]*):gpio(\d+)(?::(\d+)ms)?(?::(.*))?$/);
          if (m) oldLines.push({slot:m[1], name:m[2], type:m[3], message:m[4], gpio:m[5], duration:m[6]||'', reply:m[7]||'', multiplier:'1'});
        });
      });
      let rows = [];
      if (sawNew) {
        rows = [...bySlot.values()].sort((a, b) => Number(a.slot) - Number(b.slot));
        rows.forEach(row => {
          row.message = joinChunks(row.messageChunks);
          row.reply = joinChunks(row.replyChunks);
        });
      } else {
        rows = oldLines;
      }
      triggerCache = rows;
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty">No triggers on device.</td></tr>';
        return;
      }
      tbody.innerHTML = rows.map((r, i) => {
        const detail = r.type === 'reply'
          ? escapeHtml(r.reply || '—')
          : (r.type === 'output'
            ? `GPIO${r.gpio}${r.duration ? ' · ' + r.duration + 'ms' : ''}`
            : `GPIO${r.gpio} ADC`);
        return `<tr>
          <td><code>${escapeHtml(r.message)}</code></td>
          <td><span class="pill">${escapeHtml(r.type)}</span></td>
          <td class="detail">${detail}</td>
          <td class="actions">
            <button onclick="editCached(${i})">Edit</button>
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
      setTriggerLoading();
      try {
        const data = await api('/api/monitor/connect', {port: currentPort()}, 90000);
        monitorConnected = true;
        monitorCursor = data.next || 0;
        btn.textContent = 'Disconnect';
        showDeviceNames(data.long_name, data.short_name);
        const who = [data.long_name, data.short_name ? `(${data.short_name})` : '', data.node_hex].filter(Boolean).join(' ');
        setDeviceStatus(who ? `Connected · ${who}` : `Connected · ${data.port || currentPort()}`, 'ok');
        document.getElementById('monitorStatus').textContent = 'Connected — watching device log';
        document.getElementById('monitorStatus').className = 'status ok';
        await listTriggers();
      } catch (err) {
        setTriggerNote('Connect, then click Refresh list.');
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
      hideDeviceNames();
      setTriggerNote('Connect, then click Refresh list.');
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
    updateCounts();
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
# Phone firmware drops a second USB text within 2s and answers with a routing error, not text.
# Messages to this node are exempt after the matching firmware write; until then, wait the gap out.
_TEXT_GAP_S = 2.05


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


def _command_satisfied(command: str, replies: list[str]) -> bool:
    prefixes: tuple[str, ...]
    if command.startswith("!dmtrigger:list"):
        prefixes = ("Triggers end",)
    elif command.startswith("!dmtrigger:put|"):
        prefixes = ("Part ok", "Set failed", "Not authorized", "Unknown !dmtrigger")
    elif command.startswith("!dmtrigger:open|"):
        prefixes = ("Opened", "Set failed", "Not authorized", "Unknown !dmtrigger")
    elif command.startswith("!dmtrigger:commit|") or command.startswith("!dmtrigger:set|"):
        prefixes = ("Trigger saved", "Set failed", "Not authorized", "Unknown !dmtrigger")
    elif command.startswith("!dmtrigger:del|"):
        prefixes = ("Trigger removed", "Delete failed", "Not authorized", "Unknown !dmtrigger")
    elif command.startswith("!dmtrigger:clear"):
        prefixes = ("Triggers cleared", "Not authorized", "Unknown !dmtrigger")
    else:
        return False
    return any(reply.startswith(prefixes) for reply in replies)


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
        self._last_text_mono = 0.0

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
            or text.startswith("Opened")
            or text.startswith("Part ok")
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
                f"[session] connected port={self._port} node=0x{(node or 0):08x} "
                f"long={long_name or '?'} short={short_name or '?'}"
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
                "  .venv/bin/pip install -r bin/dm-trigger-requirements.txt\n"
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

    def _pace_text(self) -> None:
        remain = _TEXT_GAP_S - (time.monotonic() - self._last_text_mono)
        if remain > 0.05:
            self.append_monitor(f"[session] waiting {remain:.1f}s so the board will accept the next text")
            time.sleep(remain)

    def send_text(self, port: str | None, text: str, wait_s: float = 0.3) -> None:
        if FLASH_LOCK.locked():
            raise RuntimeError("Firmware write is in progress.")
        if not text:
            raise ValueError("Empty command")
        with self._lock:
            iface = self._ensure_open(port)
            node_num = iface.localNode.nodeNum
            self._pace_text()
            self.append_monitor(f"[tx] to=self text={text}")
            iface.sendText(text, destinationId=node_num, wantAck=False)
            self._last_text_mono = time.monotonic()
            time.sleep(wait_s)

    def send_command(self, port: str | None, command: str, wait_s: float = 8.0) -> list[str]:
        if FLASH_LOCK.locked():
            raise RuntimeError(
                "Firmware write is in progress. Wait until it finishes before sending config commands."
            )
        with self._lock:
            try:
                iface = self._ensure_open(port)
                node_num = iface.localNode.nodeNum
                self._pace_text()
                with self._reply_lock:
                    self._replies.clear()
                self.append_monitor(f"[tx] to=self text={command}")
                iface.sendText(command, destinationId=node_num, wantAck=True)
                self._last_text_mono = time.monotonic()
                deadline = time.monotonic() + wait_s
                last_count = 0
                last_change = time.monotonic()
                while time.monotonic() < deadline:
                    with self._reply_lock:
                        got = self._replies[:]
                    if _command_satisfied(command, got):
                        return got
                    if len(got) != last_count:
                        last_count = len(got)
                        last_change = time.monotonic()
                    elif got and time.monotonic() - last_change >= 0.6:
                        return got
                    time.sleep(0.05)
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


def _find_pio() -> str | None:
    candidates = [
        shutil.which("pio"),
        str(Path.home() / ".platformio" / "penv" / "bin" / "pio"),
        str(FIRMWARE_ROOT / ".venv" / "bin" / "pio"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def _install_pio(log) -> str:
    """Install GUI requirements into the repo venv when pio is missing on a fresh clone."""
    venv_python = FIRMWARE_ROOT / ".venv" / "bin" / "python"
    requirements = FIRMWARE_ROOT / "bin" / "dm-trigger-requirements.txt"
    if not venv_python.is_file():
        raise RuntimeError(
            "PlatformIO is not installed, and the repo .venv does not exist.\n"
            "From the repo root:\n"
            "  python3 -m venv .venv\n"
            "  .venv/bin/pip install -r bin/dm-trigger-requirements.txt"
        )
    log("PlatformIO not found. Installing it into .venv (one time, needs network)…")
    proc = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-r", str(requirements)],
        cwd=str(FIRMWARE_ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            "Could not install PlatformIO into .venv.\n"
            f"{detail}\n"
            "Install it yourself, then retry:\n"
            "  .venv/bin/pip install -r bin/dm-trigger-requirements.txt"
        )
    found = _find_pio()
    if not found:
        raise RuntimeError("PlatformIO install finished, but .venv/bin/pio is still missing")
    log(f"PlatformIO ready: {found}")
    return found


def _ensure_uv(log) -> None:
    """pioarduino creates ~/.platformio/penv with uv when python3-venv is absent."""
    uv_path = FIRMWARE_ROOT / ".venv" / "bin" / "uv"
    if uv_path.is_file() or shutil.which("uv"):
        return
    venv_python = FIRMWARE_ROOT / ".venv" / "bin" / "python"
    if not venv_python.is_file():
        raise RuntimeError(
            "uv is not installed, and the repo .venv does not exist.\n"
            "  .venv/bin/pip install -r bin/dm-trigger-requirements.txt"
        )
    log("Installing uv so PlatformIO can create its build environment…")
    proc = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "uv"],
        cwd=str(FIRMWARE_ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not uv_path.is_file():
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            "Could not install uv into .venv.\n"
            f"{detail}\n"
            "On Debian/Ubuntu you can instead install python3-venv, then retry:\n"
            "  sudo apt install python3.12-venv"
        )


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

            pio = _find_pio() or _install_pio(self._append)
            _ensure_uv(self._append)
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

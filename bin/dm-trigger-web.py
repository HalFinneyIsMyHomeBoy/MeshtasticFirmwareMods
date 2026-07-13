#!/usr/bin/env python3
"""Local web configurator for Heltec V3 DM GPIO triggers.

Includes a Device console (Meshtastic serial session) to send DMs / !dmtrigger:
commands and stream replies + firmware log lines.

Setup (use the repo venv — do not pip install into system Python):

  .venv/bin/pip install meshtastic pypubsub

Run:

  .venv/bin/python bin/dm-trigger-web.py --port 8088

Then open http://127.0.0.1:8088 and select the device serial port.
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
    button.flash { background:#2a3340; color:#8b98a5; border-color:var(--line); font-weight:700; margin-top:14px; }
    button.flash.enabled { background:#3d7eff; color:#fff; border-color:#3d7eff; }
    button:disabled { opacity:.45; cursor:not-allowed; filter:grayscale(0.4); }
    .row { display:grid; grid-template-columns: 1fr 1fr; gap:10px; }
    .actions { display:grid; grid-template-columns: 1fr 1fr 1fr; gap:10px; margin-top:14px; }
    .pin-list { display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:8px; }
    .pin { text-align:left; border:1px solid var(--line); background:#101820; padding:10px; border-radius:9px; }
    .pin.active { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) inset; }
    .pin small { display:block; color:var(--muted); margin-top:2px; }
    .board { display:grid; grid-template-columns:1fr 1fr; gap:14px; align-items:start; }
    svg { width:100%; max-width:320px; background:#0e141a; border:1px solid var(--line); border-radius:12px; }
    .log { white-space:pre-wrap; min-height:120px; max-height:320px; overflow:auto; background:#0e141a; border:1px solid var(--line); border-radius:10px; padding:12px; color:#c7d4df; }
    .console-wrap { display:none; margin-top:18px; }
    .console-wrap.visible { display:block; }
    .console-head { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:10px; }
    .console-status { color:var(--muted); font-size:13px; }
    .console-status.running { color:#e9b44c; }
    .console-status.ok { color:var(--accent); }
    .console-status.fail { color:var(--danger); }
    #flashConsole { white-space:pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size:12px; line-height:1.4; min-height:220px; max-height:420px; overflow:auto; background:#070b0f; border:1px solid var(--line); border-radius:10px; padding:12px; color:#b7c7d4; }
    table { width:100%; border-collapse:collapse; }
    th, td { border-bottom:1px solid var(--line); padding:8px; text-align:left; }
    th { color:var(--muted); font-weight:600; }
    .pill { display:inline-block; padding:2px 7px; border-radius:999px; background:#24313c; color:#cbd7e1; font-size:12px; }
    .hint { margin-top:8px; color:var(--muted); font-size:13px; }
    .serial-monitor {
      white-space: pre-wrap;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      line-height: 1.4;
      min-height: 280px;
      max-height: 480px;
      overflow: auto;
      background: #070b0f;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 12px;
      color: #b7c7d4;
    }
    .send-row { display:grid; grid-template-columns: 1fr 120px; gap:10px; margin-top:10px; }
    .send-row button { width:100%; }
    .monitor-toolbar { display:grid; grid-template-columns: 1fr 1fr 1fr; gap:10px; margin-bottom:10px; }
    .full { grid-column: 1 / -1; }
    @media (max-width: 900px) { main { grid-template-columns:1fr; } .board { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>DM Trigger Configurator</h1>
    <div class="sub">Configure Heltec V3 GPIO output and analog-reading triggers over USB. Use the device console to talk to the node and watch logs.</div>
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
      <div id="deviceStatus" class="hint" style="margin-top:10px">Connect Device console to see this node's ID and channel.</div>

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
      <button id="writeFirmware" class="flash" disabled title="Optional: flash latest heltec-v3 firmware code" onclick="writeFirmware()">Write firmware</button>
      <div class="hint">Triggers save immediately to device prefs (<code>/prefs/dm_triggers.bin</code>) — they are <b>not</b> compiled into the binary. Use Write firmware only when you need a new firmware build on the board.</div>
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

      <section id="flashConsoleWrap" class="console-wrap">
        <div class="console-head">
          <h2 style="margin:0">Firmware console</h2>
          <div id="flashStatus" class="console-status">Idle</div>
        </div>
        <div id="flashConsole"></div>
        <div class="hint">Live PlatformIO build/upload output. Keep this open until it reports success or failure.</div>
      </section>
    </div>

    <section class="full">
      <h2>Device console</h2>
      <div class="monitor-toolbar">
        <button class="primary" onclick="monitorConnect()">Connect</button>
        <button onclick="monitorDisconnect()">Disconnect</button>
        <button onclick="monitorClear()">Clear</button>
      </div>
      <div id="monitorStatus" class="console-status">Disconnected</div>
      <div id="serialMonitor" class="serial-monitor">Click Connect to open the Meshtastic serial session and stream device logs / DM replies.</div>
      <div class="send-row">
        <input id="monitorInput" placeholder="!dmtrigger:list   or   read5   (sent as DM to this node)" autocomplete="off">
        <button class="primary" onclick="monitorSend()">Send DM</button>
      </div>
      <div class="hint">
        This uses the Meshtastic protobuf serial API (not a raw UART dump). Firmware <code>LOG_*</code> lines appear here when the API streams them.
        Try: <code>!dmtrigger:list</code>, then from another radio send <code>read5</code> as a Direct Message <em>or</em> a channel message.
        Watch for <code>DmTrigger: matched</code> / <code>replying</code> here. Disconnect before writing firmware.
      </div>
    </section>
  </main>

  <script>
    const pinout = __PINOUT__;
    let flashArmed = false;

    function log(msg) {
      const el = document.getElementById('log');
      el.textContent = `${new Date().toLocaleTimeString()}  ${msg}\n\n${el.textContent}`;
    }

    function setFlashArmed(armed) {
      flashArmed = !!armed;
      const btn = document.getElementById('writeFirmware');
      btn.disabled = !flashArmed;
      btn.classList.toggle('enabled', flashArmed);
      btn.title = flashArmed
        ? 'Compile and flash heltec-v3 firmware to the selected port'
        : 'Enabled after a trigger is saved successfully';
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
        if (err.name === 'AbortError') throw new Error(`Request timed out after ${Math.round(timeoutMs/1000)}s`);
        throw err;
      } finally {
        clearTimeout(timer);
      }
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
      const data = await api('/api/command', {port: currentPort(), command}, 90000);
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
        const data = await sendCommand(`!dmtrigger:set|${index}|${name}|${type}|${message}|${gpio}|${duration}|${multiplier}`);
        const replies = (data.replies || []).join('\n');
        if (replies.includes('Set failed') || replies.includes('Not authorized')) {
          setFlashArmed(false);
          log('Device rejected the save — trigger was not stored.');
          return;
        }
        const listed = await listTriggers();
        const listedText = ((listed && listed.replies) || []).join('\n');
        const saved =
          replies.includes('Trigger saved') ||
          listedText.includes(`:${message}:`) ||
          listedText.includes(`:${message}\n`) ||
          listedText.includes(message);
        if (saved) {
          setFlashArmed(true);
          log(`Trigger '${message}' is on the device (prefs, not firmware). No flash needed unless updating firmware code.`);
        } else {
          setFlashArmed(false);
          log('Save reply missing — open Device console, Connect, send !dmtrigger:list and check for your message.');
        }
      } catch (err) {
        log(`Save failed: ${err.message}`);
      }
    }

    async function writeFirmware() {
      if (!flashArmed) return;
      const port = currentPort();
      if (!port) {
        log('Select a serial port before writing firmware.');
        return;
      }
      const ok = confirm(
        'Write firmware to this device?\n\n' +
        'This will compile and flash heltec-v3 over USB.\n' +
        'Do NOT unplug the device while writing.\n\n' +
        `Port: ${port}\n\nContinue?`
      );
      if (!ok) return;

      const btn = document.getElementById('writeFirmware');
      const wrap = document.getElementById('flashConsoleWrap');
      const consoleEl = document.getElementById('flashConsole');
      const statusEl = document.getElementById('flashStatus');
      btn.disabled = true;
      btn.textContent = 'Writing firmware…';
      wrap.classList.add('visible');
      consoleEl.textContent = '';
      statusEl.className = 'console-status running';
      statusEl.textContent = 'Starting…';
      log(`Building and flashing heltec-v3 to ${port}. Watch the Firmware console below.`);

      try {
        await api('/api/flash', {port, env: 'heltec-v3'}, 15000);
        let cursor = 0;
        const startedAt = Date.now();
        const maxMs = 20 * 60 * 1000;
        while (true) {
          if (Date.now() - startedAt > maxMs) {
            throw new Error('Firmware write timed out after 20 minutes (no completion from server).');
          }
          const st = await api(`/api/flash/status?since=${cursor}`, null, 15000);
          const lines = st.lines || [];
          if (lines.length) {
            consoleEl.textContent += (consoleEl.textContent ? '\n' : '') + lines.join('\n');
            consoleEl.scrollTop = consoleEl.scrollHeight;
            cursor = st.next || (cursor + lines.length);
          }
            if (st.running) {
            const elapsed = Math.round((Date.now() - startedAt) / 1000);
            statusEl.className = 'console-status running';
            statusEl.textContent = `Running… ${elapsed}s`;
            await new Promise(r => setTimeout(r, 500));
            continue;
          }
          if (st.success) {
            statusEl.className = 'console-status ok';
            statusEl.textContent = `Succeeded in ${Math.round((Date.now() - startedAt) / 1000)}s`;
            log('Firmware write succeeded.');
            setFlashArmed(false);
          } else {
            statusEl.className = 'console-status fail';
            statusEl.textContent = 'Failed';
            throw new Error(st.error || 'Firmware write failed');
          }
          break;
        }
      } catch (err) {
        statusEl.className = 'console-status fail';
        statusEl.textContent = 'Failed';
        log(`Firmware write failed: ${err.message}`);
        setFlashArmed(true);
      } finally {
        btn.textContent = 'Write firmware';
        btn.disabled = !flashArmed;
        btn.classList.toggle('enabled', flashArmed);
      }
    }

    async function listTriggers() {
      try {
        return await sendCommand('!dmtrigger:list', true);
      } catch (err) {
        log(`List failed: ${err.message}`);
        return null;
      }
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
    setFlashArmed(false);
    refreshPorts();
    startMonitorPolling();
    document.getElementById('monitorInput').addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') monitorSend();
    });

    let monitorCursor = 0;
    let monitorConnected = false;

    function appendMonitorLocal(line) {
      const el = document.getElementById('serialMonitor');
      if (el.textContent.startsWith('Click Connect')) el.textContent = '';
      el.textContent += (el.textContent ? '\n' : '') + line;
      el.scrollTop = el.scrollHeight;
    }

    async function monitorConnect() {
      try {
        const data = await api('/api/monitor/connect', {port: currentPort()}, 90000);
        monitorConnected = true;
        monitorCursor = data.next || 0;
        document.getElementById('monitorStatus').className = 'console-status ok';
        document.getElementById('monitorStatus').textContent = `Connected on ${data.port || currentPort() || 'auto'}`;
        log('Device console connected.');
        const st = document.getElementById('deviceStatus');
        if (data.node_hex || data.short_name) {
          const ch = data.channel_note || '';
          st.innerHTML = `This node: <code>${data.short_name || '?'} ${data.node_hex || ''}</code>. ${ch}`;
        }
      } catch (err) {
        document.getElementById('monitorStatus').className = 'console-status fail';
        document.getElementById('monitorStatus').textContent = 'Connect failed';
        log(`Monitor connect failed: ${err.message}`);
      }
    }

    async function monitorDisconnect() {
      try {
        await api('/api/monitor/disconnect', {}, 15000);
        monitorConnected = false;
        document.getElementById('monitorStatus').className = 'console-status';
        document.getElementById('monitorStatus').textContent = 'Disconnected';
        log('Device console disconnected.');
      } catch (err) {
        log(`Monitor disconnect failed: ${err.message}`);
      }
    }

    function monitorClear() {
      document.getElementById('serialMonitor').textContent = '';
      api('/api/monitor/clear', {}, 5000).catch(() => {});
      monitorCursor = 0;
    }

    async function monitorSend() {
      const input = document.getElementById('monitorInput');
      const text = input.value.trim();
      if (!text) return;
      try {
        appendMonitorLocal(`> ${text}`);
        await api('/api/monitor/send', {port: currentPort(), text}, 30000);
        input.value = '';
        monitorConnected = true;
        document.getElementById('monitorStatus').className = 'console-status ok';
        document.getElementById('monitorStatus').textContent = `Connected on ${currentPort() || 'auto'}`;
      } catch (err) {
        appendMonitorLocal(`! send failed: ${err.message}`);
        log(`Monitor send failed: ${err.message}`);
      }
    }

    function startMonitorPolling() {
      setInterval(async () => {
        try {
          const st = await api(`/api/monitor/log?since=${monitorCursor}`, null, 5000);
          const lines = st.lines || [];
          if (lines.length) {
            const el = document.getElementById('serialMonitor');
            if (el.textContent.startsWith('Click Connect')) el.textContent = '';
            el.textContent += (el.textContent ? '\n' : '') + lines.join('\n');
            el.scrollTop = el.scrollHeight;
            monitorCursor = st.next || (monitorCursor + lines.length);
          }
          if (st.connected) {
            monitorConnected = true;
            const status = document.getElementById('monitorStatus');
            if (!status.textContent.startsWith('Connected')) {
              status.className = 'console-status ok';
              status.textContent = `Connected on ${st.port || currentPort() || 'auto'}`;
            }
          } else if (monitorConnected && !st.flashing) {
            monitorConnected = false;
            document.getElementById('monitorStatus').className = 'console-status';
            document.getElementById('monitorStatus').textContent = 'Disconnected';
          }
          if (st.flashing) {
            document.getElementById('monitorStatus').className = 'console-status running';
            document.getElementById('monitorStatus').textContent = 'Paused — firmware write in progress';
          }
        } catch (_) {
          // ignore transient poll errors
        }
      }, 400);
    }
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
        if env != "heltec-v3":
            raise ValueError("Only the heltec-v3 environment is supported by this configurator")
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
            body = HTML.replace("__PINOUT__", json.dumps(PINOUT)).encode("utf-8")
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

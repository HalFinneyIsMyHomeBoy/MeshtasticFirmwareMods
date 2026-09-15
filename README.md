<div align="center" markdown="1">

<img src=".github/meshtastic_logo.png" alt="Meshtastic Logo" width="80"/>
<h1>Meshtastic Firmware</h1>

![GitHub release downloads](https://img.shields.io/github/downloads/meshtastic/firmware/total)
[![CI](https://img.shields.io/github/actions/workflow/status/meshtastic/firmware/main_matrix.yml?branch=master&label=actions&logo=github&color=yellow)](https://github.com/meshtastic/firmware/actions/workflows/ci.yml)
[![CLA assistant](https://cla-assistant.io/readme/badge/meshtastic/firmware)](https://cla-assistant.io/meshtastic/firmware)
[![Fiscal Contributors](https://opencollective.com/meshtastic/tiers/badge.svg?label=Fiscal%20Contributors&color=deeppink)](https://opencollective.com/meshtastic/)
[![Vercel](https://img.shields.io/static/v1?label=Powered%20by&message=Vercel&style=flat&logo=vercel&color=000000)](https://vercel.com?utm_source=meshtastic&utm_campaign=oss)

<a href="https://trendshift.io/repositories/5524" target="_blank"><img src="https://trendshift.io/api/badge/repositories/5524" alt="meshtastic%2Ffirmware | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

</div>

</div>

<div align="center">
	<a href="https://meshtastic.org">Website</a>
	-
	<a href="https://meshtastic.org/docs/">Documentation</a>
</div>

## This fork: DM GPIO triggers

Heltec V3 and V4 boards can pulse a GPIO or return a voltage when they receive a matching direct message. The local web GUI is the easiest way to try it:

```bash
cd MeshtasticFirmwareMods
python3 -m venv .venv
.venv/bin/pip install meshtastic pypubsub
.venv/bin/python bin/dm-trigger-web.py --port 8088
```

Then open [http://127.0.0.1:8088](http://127.0.0.1:8088). Full notes (flashing, pinout, CLI): **[docs/dm-triggers.md](docs/dm-triggers.md)**. Lightning design: **[docs/dm-triggers-lightning.md](docs/dm-triggers-lightning.md)**.

## Overview

This repository is a fork of [Meshtastic firmware](https://github.com/meshtastic/firmware) with the DM GPIO trigger feature above. Upstream Meshtastic is an open-source LoRa mesh networking project for long-range, low-power communication without internet or cellular infrastructure. The firmware supports ESP32, nRF52, RP2040/RP2350, and Linux-based devices.

### Get Started

- 🔧 **[Building Instructions](https://meshtastic.org/docs/development/firmware/build)** - Learn how to compile the firmware from source.
- ⚡ **[Flashing Instructions](https://meshtastic.org/docs/getting-started/flashing-firmware/)** - Install or update the firmware on your device.

Join our community and help improve Meshtastic! 🚀

## Stats

![Alt](https://repobeats.axiom.co/api/embed/8025e56c482ec63541593cc5bd322c19d5c0bdcf.svg "Repobeats analytics image")

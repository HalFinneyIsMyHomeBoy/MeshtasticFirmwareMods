# Heltec V3 DM GPIO triggers

This fork adds **direct-message GPIO triggers** on Heltec V3: another node DMs a saved phrase, and this node pulses a pin or returns an analog voltage. Triggers are saved on the device; changing them does not require a firmware flash.

A local web GUI talks to the board over USB.

## Run the GUI

From a clone of this repo:

```bash
cd MeshtasticFirmwareMods
python3 -m venv .venv
.venv/bin/pip install meshtastic pypubsub
.venv/bin/python bin/dm-trigger-web.py --port 8088
```

Open **http://127.0.0.1:8088**, plug in the Heltec V3, pick its serial port, and click Connect.

Use a repo `.venv` — do not `pip install` into system Python.

## What you need

- A **Heltec V3** flashed with this fork (`heltec-v3`)
- Python 3 on the computer that will run the GUI
- USB serial access to the board

If the board is still on stock Meshtastic, flash this firmware first (PlatformIO), or use **Advanced → Write firmware** in the GUI after it is connected:

```bash
pio run -e heltec-v3 -t upload --upload-port /dev/ttyUSB0
```

## Using the page

1. **Connect** — select the USB port and connect.
2. **Saved triggers** — refresh the list already on the device.
3. **Add / update** — set the exact DM text, GPIO, and action (pulse pin or read voltage), then save.
4. **Test** — send that DM from the page (USB, same as a mesh DM) and watch the log.

Default first-boot triggers (until you change them):

| DM text        | Action                         | Pin    |
| -------------- | ------------------------------ | ------ |
| `Open_Seseme`  | Pulse GPIO high for 10 seconds | GPIO 7 |
| `Get_Reading`  | Reply with voltage             | GPIO 6 |

GPIO 1 is battery ADC (analog only). GPIO 2–7 are usable as outputs. Reserved radio/OLED pins are rejected by firmware.

## CLI (optional)

Same venv, no browser:

```bash
.venv/bin/python bin/configure-dm-triggers.py --port /dev/ttyUSB0 list
.venv/bin/python bin/configure-dm-triggers.py --port /dev/ttyUSB0 add \
    --name "Garage" --type output --message "Open_Seseme" --gpio 7 --duration-ms 10000
```

Commands are `!dmtrigger:` DMs to the local node (`list`, `set`, `del`, `clear`). The sender must be this node over USB, or hold a configured admin key.
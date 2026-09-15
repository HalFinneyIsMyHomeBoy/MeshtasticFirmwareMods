# DM GPIO triggers

This fork adds **direct-message GPIO triggers**: another node DMs a saved phrase, and this node pulses a pin or returns an analog voltage. Triggers are saved on the device; changing them does not require a firmware flash.

Supported boards: **Heltec V3** and **Heltec V4** (OLED). A local web GUI talks to the board over USB.

## Run the GUI

From a clone of this repo:

```bash
cd MeshtasticFirmwareMods
python3 -m venv .venv
.venv/bin/pip install meshtastic pypubsub
.venv/bin/python bin/dm-trigger-web.py --port 8088
```

Open **http://127.0.0.1:8088**, pick **Heltec V3** or **Heltec V4**, plug in the board, select its serial port, and click Connect.

Use a repo `.venv` — do not `pip install` into system Python.

## What you need

- A **Heltec V3** or **Heltec V4** flashed with this fork (`heltec-v3` or `heltec-v4`)
- Python 3 on the computer that will run the GUI
- USB serial access to the board

If the board is still on stock Meshtastic, flash this firmware first (PlatformIO), or use **Advanced → Write firmware** in the GUI after it is connected:

```bash
pio run -e heltec-v3 -t upload --upload-port /dev/ttyUSB0
# or
pio run -e heltec-v4 -t upload --upload-port /dev/ttyUSB0
```

## Using the page

1. **Connect** — choose the board type, select the USB port, and connect.
2. **Saved triggers** — refresh the list already on the device.
3. **Add / update** — set the exact DM text, GPIO, and action (pulse pin or read voltage), then save.
4. **Test** — send that DM from the page (USB, same as a mesh DM) and watch the log.

### Heltec V3 defaults

| DM text       | Action                         | Pin    |
| ------------- | ------------------------------ | ------ |
| `Open_Seseme` | Pulse GPIO high for 10 seconds | GPIO 7 |
| `Get_Reading` | Reply with voltage             | GPIO 6 |

GPIO 1 is battery ADC (analog only). GPIO 2–7 are usable as outputs. Radio/OLED pins are rejected by firmware.

### Heltec V4 defaults

GPIO 7 is **VFEM_Ctrl** (RF front-end power) on V4 — never use it as a user output. GPIO 2, 5, and 46 are FEM control pins.

| DM text       | Action                         | Pin    |
| ------------- | ------------------------------ | ------ |
| `Open_Seseme` | Pulse GPIO high for 10 seconds | GPIO 6 |
| `Get_Reading` | Reply with voltage             | GPIO 3 |

Safe header pins in the GUI: GPIO 3, 4, 6 (ADC + output), GPIO 45 (digital only). GPIO 6 is the buzzer on the V4 TFT variant — do not use it there.

## CLI (optional)

Same venv, no browser:

```bash
.venv/bin/python bin/configure-dm-triggers.py --port /dev/ttyUSB0 list
.venv/bin/python bin/configure-dm-triggers.py --port /dev/ttyUSB0 add \
    --name "Garage" --type output --message "Open_Seseme" --gpio 7 --duration-ms 10000
```

Commands are `!dmtrigger:` DMs to the local node (`list`, `set`, `del`, `clear`). The sender must be this node over USB, or hold a configured admin key.

## Adding another board

Same pattern as V3/V4:

1. In that board’s `variants/.../variant.h`, set `HAS_DM_TRIGGER 1` and optional `GPIO_TRIGGER_PIN` / `VOLTAGE_ADC_PIN` defaults.
2. Add a `BOARDS` entry in `bin/dm-trigger-web.py` with the PlatformIO env name and the pins that are safe to expose.
3. Firmware already rejects LoRa, OLED, GPS, FEM, and similar reserved pins automatically.

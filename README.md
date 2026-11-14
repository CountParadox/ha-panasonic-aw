# Panasonic AW PTZ Camera for Home Assistant

A Home Assistant custom integration (installable through HACS) for Panasonic
professional PTZ cameras that speak the AW HTTP protocol (`aw_ptz` / `aw_cam`),
such as the AW-HE38, AW-HE40, AW-HE42, AW-UE70 and similar.

It talks to the camera directly over your LAN. No cloud, no extra software.

> **Status:** written from Panasonic's *HD Integrated Camera Interface
> Specifications* and tested inside Home Assistant against a simulated camera.
> It has not yet been run against real hardware.

## Features

- **Live view**: JPEG snapshots and the camera's RTSP H.264 stream.
- **PTZ control**: continuous pan/tilt/zoom/focus moves with speed, nudge
  buttons, absolute pan/tilt/zoom positioning, home and stop.
- **Presets**: recall, save and delete presets 1-100.
- **Picture settings**: scene, white balance mode, gain and auto gain, iris and
  auto iris, focus and autofocus, night mode, noise reduction, colour bars,
  one-touch autofocus, auto white balance and auto black balance.
- **Other**: power (standby), tally lamp, preset speed, ceiling mount.
- **Raw command action** for anything in the interface specification that has
  no entity yet.

The integration asks the camera which settings it supports. Settings a model
rejects are not created as entities.

## Installation

### HACS

1. In HACS open the menu (top right) and choose **Custom repositories**.
2. Add `https://github.com/CountParadox/ha-panasonic-aw` with type **Integration**.
3. Install **Panasonic AW PTZ Camera** and restart Home Assistant.

### Manual

Copy `custom_components/panasonic_aw` into your Home Assistant `config/custom_components`
folder and restart.

## Setup

**Settings → Devices & services → Add integration → Panasonic AW PTZ Camera**,
then enter the camera's IP address. Username and password are only needed if
user authentication is switched on in the camera.

For the live stream, enable **H.264 transmission** in the camera's web interface
(Setup → Video over IP). Snapshots work without it.

Options (the **Configure** button on the integration): polling interval,
snapshot width and RTSP port.

## Entities

| Type | Entities |
| --- | --- |
| Camera | Live view |
| Switch | Power, Tally, Autofocus, Auto iris, Auto gain, Night mode, Color bars, Ceiling mount (disabled by default) |
| Select | Preset, Scene, White balance, Noise reduction |
| Number | Zoom, Focus, Iris (%), Pan, Tilt (°), Gain (dB), Preset speed, Preset to save |
| Button | Pan left/right, Tilt up/down, Zoom in/out, Stop, Home position, Save preset, One-touch autofocus, Auto white balance, Auto black balance |

Everything except **Power** is unavailable while the camera is in standby.

The **Preset** select lists the presets saved in the camera and shows the one
recalled last. Choosing an option recalls it.

To save a preset from the dashboard, frame the shot, set **Preset to save** to
the preset number and press **Save preset**. This overwrites that preset.

## Actions

All actions target the camera entity.

| Action | Fields | What it does |
| --- | --- | --- |
| `panasonic_aw.ptz` | `pan`, `tilt`, `zoom`, `focus` (-100 to 100), `duration` (s) | Start a continuous move. With `duration` it stops by itself; without it the camera keeps moving until `stop`. |
| `panasonic_aw.stop` | | Stop all movement. |
| `panasonic_aw.preset_recall` | `preset` (1-100) | Go to a preset. |
| `panasonic_aw.preset_save` | `preset` (1-100) | Save the current shot to a preset. |
| `panasonic_aw.preset_delete` | `preset` (1-100) | Delete a preset. |
| `panasonic_aw.move_absolute` | `pan`, `tilt` (degrees), `zoom` (%) | Go to an absolute position. |
| `panasonic_aw.send_command` | `interface` (`ptz` or `cam`), `command` | Send a raw protocol command and get the answer back. |

### Examples

Save the current shot as preset 5:

```yaml
action: panasonic_aw.preset_save
target:
  entity_id: camera.studio_cam
data:
  preset: 5
```

Hold-to-move joystick button (start on press, stop on release):

```yaml
# press
action: panasonic_aw.ptz
target:
  entity_id: camera.studio_cam
data:
  pan: -40
# release
action: panasonic_aw.stop
target:
  entity_id: camera.studio_cam
```

Read a value that has no entity:

```yaml
action: panasonic_aw.send_command
target:
  entity_id: camera.studio_cam
data:
  interface: cam
  command: QSV
response_variable: answer
```

## Notes

- Pan and tilt angles are derived from the protocol's position counts
  (about 121.37 counts per degree, 0° at the mechanical centre). Depending on
  the model and mounting, the sign of the pan angle may be mirrored.
- Command codes differ slightly between camera generations. If a picture
  setting is missing for your model, `panasonic_aw.send_command` with the code
  from the model's interface specification will still work.

## Development

```bash
pip install pytest-homeassistant-custom-component
```

```bash
pytest tests
```

The protocol client lives in `custom_components/panasonic_aw/api.py` and has no
Home Assistant dependency. Its tests also run with only `aiohttp`, `pytest` and
`pytest-asyncio` installed; the Home Assistant setup tests are skipped then.
The Home Assistant test harness does not support Windows.

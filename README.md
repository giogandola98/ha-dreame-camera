# Dreame Camera for Home Assistant

Live camera of **Dreame robot vacuums** in Home Assistant, free and open source.

It works with robots whose camera uses **Tencent XP2P** (`stream_tx` in the
`capabilities` attribute of the vacuum entity; most robots sold outside China).
Tested on a Dreame `r5039a` with Home Assistant 2026.11 on HA OS (amd64).

> 🇮🇹 Telecamera live dei robot Dreame in Home Assistant, gratis e open source.
> Richiede l'integrazione [Dreame Vacuum](https://github.com/Tasshack/dreame-vacuum) e un sistema amd64.

## How it works

| Part | What it does |
|---|---|
| **Integration** `dreame_camera` (HACS) | Reuses the cloud session of the [Dreame Vacuum](https://github.com/Tasshack/dreame-vacuum) integration (no extra credentials), unlocks the camera with your PIN, starts the robot's monitor mode, sends keep-alives and exposes a `camera` entity. |
| **Add-on** *Dreame Camera Bridge* | Runs Tencent's official XP2P SDK to receive the video (direct P2P on the LAN or Tencent relay) and republishes it, without re-encoding, as RTSP **only on Home Assistant's internal network**, password protected. |

The camera is **off by default**. The robot's camera starts only when someone opens the live view
or calls `camera.turn_on`, and it switches off automatically after a configurable time (default 5 minutes).
Showing the entity on a dashboard never wakes the robot.

## Requirements

- Home Assistant OS / Supervised on **amd64** (Tencent's SDK is x86-64 only)
- The [Dreame Vacuum](https://github.com/Tasshack/dreame-vacuum) integration, logged in with a Dreamehome account
- The camera PIN set in the Dreame app (video monitoring)

## Installation

1. **Add-on**: *Settings → Add-ons → Add-on store → ⋮ → Repositories*, add this repository's URL,
   install **Dreame Camera Bridge**. The first install builds the image (a few minutes).
   In *Configuration* set `api_token` to a random string of at least 16 characters, then start it.
2. **Integration**: in HACS add this repository as a custom repository (type *Integration*),
   install **Dreame Camera** and restart Home Assistant.
3. *Settings → Devices & services → Add integration → Dreame Camera*: choose the robot,
   enter the camera PIN and the add-on's `api_token`.
   Host `local-dreame-camera-bridge` and port `8099` are the defaults for the add-on.

The camera appears inside the existing device of the robot, together with an **Audio** switch
(robot microphone on/off; toggling it while the camera is on restarts the stream in a few seconds).
Dashboard cards always play camera streams muted: open the camera's more-info dialog to listen.

### Dashboard example

```yaml
type: grid
cards:
  - type: conditional
    conditions: [{condition: state, entity: camera.ROBOT_camera, state_not: streaming}]
    card:
      type: button
      name: Start camera
      icon: mdi:cctv
      tap_action: {action: perform-action, perform_action: camera.turn_on, target: {entity_id: camera.ROBOT_camera}}
  - type: conditional
    conditions: [{condition: state, entity: camera.ROBOT_camera, state: streaming}]
    card: {type: picture-entity, entity: camera.ROBOT_camera, camera_view: live}
```

## Patrols (optional, local AI)

The integration provides the `dreame_camera.inspect` service: the robot goes to the given points
(Dreame map coordinates in mm), turns on the spot to the given headings (0 = east, 90 = north) and
takes photos into `/media/dreame_camera/<timestamp>/`. The service returns the list of photos.
A point can also list `lights` (light or switch entities, e.g. a ceiling light on a relay): those that are off are switched on while the robot drives there and
takes that point's photos, then switched off again (lights already on are left alone).

- **Change detection**: each photo is compared with the reference photo of the same point
  (tolerant to small robot shifts and light changes). The first patrol creates the references.
- **Local analysis** (only changed photos by default): yes/no questions (person or animal, water on
  the floor, smoke or fire, broken objects) to a small vision model on a local **Ollama** server.
  Defaults: `SmolVLM2-2.2B-Instruct` (~55 s per photo on an old i3), second opinion with
  `qwen2.5vl:3b` only when something is found. Configure URL and models in the integration options.
- The **Ollama locale** add-on in this repository runs the official Ollama image with no host ports.
- `examples/` contains a patrol script (notification with photo only on anomalies) and an
  automation that runs it every 3 hours when nobody is home.

## Privacy and security

- No Dreame password is stored or transmitted by this project: the integration uses the session
  that Dreame Vacuum already has.
- The add-on publishes **no host ports**. The RTSP stream is reachable only from Home Assistant
  on the internal Supervisor network, with user `ha` and the `api_token` as password.
- The robot usually announces by voice that video monitoring has started (it's a Dreame feature).

## Third-party components

- **Tencent XP2P SDK** (`tencentyun/iot-p2p-build`, release v2.4.50): it is **not included** in this repository.
  The add-on downloads it from Tencent's GitHub release at build time and checks its sha256 hashes.
- **MediaMTX** (MIT): downloaded at build time from its official release, sha256 checked.
- The Dreame cloud protocol is used through the Dreame Vacuum integration by Tasshack (MIT).

The Tencent libraries are GCC 9 "slim LTO" objects, so the bridge is built on Ubuntu 20.04 (GCC 9.4).

## Status

Experimental, version 0.1.0. Known limits:
- amd64 only.
- Only the Tencent provider; robots that use Agora or Alibaba LinkVisual are not supported.
- No talk-back (audio from the robot to Home Assistant only).

## License

MIT. See [LICENSE](LICENSE). This project is not affiliated with Dreame or Tencent.

# Dreame Camera Bridge

Media bridge used by the **Dreame Camera** integration. It receives the robot's video through
Tencent's XP2P SDK and serves it as RTSP on Home Assistant's internal network only.

## Options

- `api_token` (required, at least 16 characters): shared secret. Use the same value in the
  Dreame Camera integration. It is also the RTSP password (user `ha`).
- `quality`: `high` (default), `standard` or `super`.

No ports are exposed on the host. You don't need to do anything else: the integration starts
and stops the stream on demand.

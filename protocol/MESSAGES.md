# Client ↔ PirateBox messages (v1)

Remote clients (the button test client, and the phone app over Wi-Fi or Bluetooth) exchange small
JSON messages with PirateBox. The transport can change; these messages stay the same.
Implemented in `bridge/piratebox/inputs/messages.py`.

## Client → bridge

```jsonc
{"type": "text", "text": "set the tempo to 142"}           // free text; "voice_command" is an alias
{"type": "command", "command": "set_tempo", "parameters": {"bpm": 142}, "request_id": "phone-17"}
{"type": "status"}                                           // cheap poll, safe every ~2 s
```

`command` messages use the vocabulary in [commands.json](commands.json). `text` is at most 500 characters.

## Bridge → client

```jsonc
{"type": "command_result", "request_id": "phone-17", "success": true, "status": "OK",
 "message": "Tempo set to 142 BPM", "command": "set_tempo", "parameters": {"bpm": 142},
 "state": {"tempo": 142.0, "playing": false, "recording": false, "metronome": false, "unsaved_changes": true}}

{"type": "status", "connected": true, "status": "OK", "state": {...}}
{"type": "status", "connected": false, "status": "FL_TIMEOUT", "message": "..."}
```

`message` is written to be shown to the user as-is (e.g. "Nothing to undo").
`status` is one of the codes in commands.json. UIs should branch on `status`, never on `message` text.

## HTTP binding (current)

- `POST /api/message` with a JSON body of at most 4 KB. Header `X-PirateBox-Token: <pairing token>` is required, otherwise the reply is 401 `UNAUTHORIZED`.
- `GET /` serves the button test client. It reads the token from the URL fragment (`#token=…`), so the token never appears in server logs.
- Binds to 127.0.0.1 by default. `--lan` binds to all interfaces so a phone on the same Wi-Fi can connect.

## Bluetooth LE binding

`--ble` makes the computer a BLE peripheral (`bridge/piratebox/inputs/ble.py`, phone side `mobile/src/ble.ts`):

| | UUID | |
|---|---|---|
| service | `6e3a0001-2c55-4b1e-9f51-7069726174b0` | advertised under the `--ble-name` (default `PirateBox`) |
| RX | `6e3a0002-2c55-4b1e-9f51-7069726174b0` | phone writes requests (write with response) |
| TX | `6e3a0003-2c55-4b1e-9f51-7069726174b0` | computer notifies replies |

Each message is UTF-8 JSON followed by `\n`, split into chunks of at most 180 bytes. Requests add the pairing
token as a field: `{"token": "…", "type": "status"}`. A wrong or missing token gets
`{"type": "command_result", "success": false, "status": "UNAUTHORIZED"}`. The phone sends one request at a
time, so replies arrive in request order.

## Pairing

The phone gets host, port and token from a QR code:
`piratebox://pair?host=192.168.1.20&port=7142&token=…&name=PirateBox`, plus `&ble=PirateBox` when Bluetooth is on. The token persists on the computer
until someone runs `--new-token`. The QR is printed in the terminal in `--lan` mode, and served at `/pair` to
loopback clients only.

Planned: a WebSocket binding (push `status` events instead of polling) carrying these same messages.

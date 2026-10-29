"""
Phone pairing: a persistent token and the QR code that hands it to the companion app.

The token is generated once and stored in the user's app-data folder, so a phone paired yesterday still
works after the computer restarts. `--new-token` rotates it, which un-pairs every phone.

Pairing URI (what the QR encodes):
    piratebox://pair?host=192.168.1.20&port=7142&token=...&name=PirateBox[&ble=PirateBox]
"""

import json
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlencode


def state_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "PirateBox"
    return Path.home() / ".piratebox"


def load_or_create_token(rotate: bool = False, directory: Path = None) -> str:
    path = (directory or state_dir()) / "pairing.json"
    if not rotate:
        try:
            token = json.loads(path.read_text(encoding="utf-8")).get("token")
            if isinstance(token, str) and len(token) >= 16:
                return token
        except (OSError, ValueError):
            pass
    token = secrets.token_urlsafe(18)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token}), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def pairing_uri(host: str, port: int, token: str, name: str = "PirateBox", ble: str = None) -> str:
    """ble: the Bluetooth name the computer advertises, when --ble is on."""
    params = {"host": host, "port": port, "token": token, "name": name}
    if ble:
        params["ble"] = ble
    return "piratebox://pair?" + urlencode(params)


def qr_terminal(uri: str) -> None:
    try:
        import segno
    except ImportError:
        print("  (pip install segno to show a QR code here)")
        return
    segno.make(uri, error="m").terminal(compact=True)


def qr_svg(uri: str) -> str:
    import segno
    import io

    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=8, border=2, dark="#e8eaed", light=None)
    return buf.getvalue().decode()

"""Console entry point: ``python -m pocketcompute`` / ``pocketcompute``.

Starts the agent, then prints the LAN URL and a scannable QR code that pairs a
phone in one tap. The QR encodes ``http://<lan-ip>:<port>/?pair=<secret>`` so the
web app can auto-pair on first open.
"""
from __future__ import annotations

import argparse
import socket
import sys

from .auth import pairing_secret
from .config import config


def _lan_ip() -> str:
    """Best guess at the LAN IP other devices can reach."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packets are actually sent; this just picks the outbound interface.
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def _print_qr(url: str) -> None:
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        print("(install 'qrcode' to show a scannable code)")


def _banner(host: str, port: int) -> None:
    ip = _lan_ip()
    secret = pairing_secret()
    pair_url = f"http://{ip}:{port}/?pair={secret}"
    device = config.get("device_name")

    line = "=" * 60
    print("\n" + line)
    print(f"  PocketCompute  ·  {device}")
    print(line)
    print("\n  Scan this with your phone camera to pair:\n")
    _print_qr(pair_url)
    print(f"\n  Or open this URL on your phone (same Wi-Fi):")
    print(f"    {pair_url}")
    print(f"\n  Local:   http://localhost:{port}/")
    print(f"  Pairing code: {secret}")
    print("\n  Remote access (away from home): expose this with a tunnel, e.g.")
    print(f"    cloudflared tunnel --url http://localhost:{port}")
    print(line + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pocketcompute",
        description="Message your computer from your phone.",
    )
    parser.add_argument("--host", default="0.0.0.0",
                        help="Bind address (default: 0.0.0.0 = all interfaces)")
    parser.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
    parser.add_argument("--name", help="Override this device's display name")
    parser.add_argument("--reset-pairing", action="store_true",
                        help="Generate a new pairing code (unpairs existing devices)")
    parser.add_argument("--show", action="store_true",
                        help="Just print the pairing QR / URL and exit")
    args = parser.parse_args(argv)

    if args.name:
        config.set("device_name", args.name)

    if args.reset_pairing:
        import secrets
        config.set("pairing_secret", secrets.token_urlsafe(18))
        config.set("jwt_secret", secrets.token_hex(32))
        print("New pairing code generated. All devices must re-pair.")

    if args.show:
        _banner(args.host, args.port)
        return 0

    _banner(args.host, args.port)

    try:
        import uvicorn
    except ImportError:
        print("uvicorn is required. Install with: pip install -r requirements.txt",
              file=sys.stderr)
        return 1

    uvicorn.run(
        "pocketcompute.server:app",
        host=args.host,
        port=args.port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

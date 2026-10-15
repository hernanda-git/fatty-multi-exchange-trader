#!/usr/bin/env python3
"""Generate the VAPID keypair used for iOS/Web Push and write the runtime files.

The private key is mounted read-only into the notification sender; the public
key is mounted read-only into the console web process and served to browsers.
Rotating the keypair invalidates every existing browser subscription; those
devices must subscribe again from Settings -> Push notifications.
"""

from __future__ import annotations

import argparse
import base64
import os
import stat
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

PRIVATE_NAME = "web-push-private.pem"
PUBLIC_NAME = "web-push-public.txt"


def public_key_b64(private_key: ec.EllipticCurvePrivateKey) -> str:
    """Uncompressed P-256 point in the base64url form the browser expects."""
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def main() -> int:
    runtime_default = Path(__file__).resolve().parents[1] / "runtime"
    parser = argparse.ArgumentParser(description="Provision web-push VAPID keys")
    parser.add_argument("--runtime-dir", default=str(runtime_default))
    parser.add_argument("--force", action="store_true", help="rotate an existing keypair")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--print-public-key", action="store_true")
    args = parser.parse_args()

    runtime = Path(args.runtime_dir)
    private_path = runtime / PRIVATE_NAME
    public_path = runtime / PUBLIC_NAME

    if args.check:
        private_ok = private_path.is_file()
        public_ok = public_path.is_file()
        print(
            f"private={'present' if private_ok else 'missing'} "
            f"public={'present' if public_ok else 'missing'}"
        )
        if args.print_public_key and public_ok:
            print(public_path.read_text(encoding="utf-8").strip())
        return 0 if private_ok and public_ok else 1

    if private_path.exists() and not args.force:
        print(f"refusing to overwrite {private_path}; pass --force to rotate", file=sys.stderr)
        return 1

    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    runtime.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(pem)
    os.chmod(private_path, stat.S_IRUSR | stat.S_IWUSR)
    public_path.write_text(public_key_b64(private_key) + "\n", encoding="utf-8")
    os.chmod(public_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    print(f"wrote {private_path} (0600)")
    print(f"wrote {public_path} (0644)")
    print(f"public key: {public_path.read_text(encoding='utf-8').strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

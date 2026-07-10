#!/usr/bin/env python3
"""Tiny serial/HTTP client for the custom Stack-chan firmware."""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
import urllib.error
import urllib.request

try:
    import serial
except ImportError:
    serial = None


def open_port(port: str, baud: int):
    if serial is None:
        raise RuntimeError(
            "pyserial is required for serial mode; "
            "use PlatformIO's Python or pass --host for HTTP"
        )
    return serial.Serial(port=port, baudrate=baud, timeout=0.25, write_timeout=1)


def discover_port() -> str:
    candidates: list[str] = []
    patterns = (
        "/dev/cu.usbmodem*",
        "/dev/cu.usbserial*",
        "/dev/ttyACM*",
        "/dev/ttyUSB*",
    )
    for pattern in patterns:
        candidates.extend(glob.glob(pattern))
    if not candidates:
        raise RuntimeError("no USB serial device found; pass --port explicitly")
    return sorted(set(candidates))[0]


def drain(ser, seconds: float) -> str:
    deadline = time.time() + seconds
    chunks: list[bytes] = []
    while time.time() < deadline:
        data = ser.read(4096)
        if data:
            chunks.append(data)
        else:
            time.sleep(0.02)
    return b"".join(chunks).decode("utf-8", errors="replace")


def send_command(port: str, baud: int, command: str, wait: float) -> int:
    try:
        with open_port(port, baud) as ser:
            time.sleep(0.2)
            if ser.in_waiting:
                ser.read(ser.in_waiting)
            ser.write((command.rstrip() + "\n").encode("utf-8"))
            ser.flush()
            output = drain(ser, wait)
    except RuntimeError as exc:
        print(f"serial error: {exc}", file=sys.stderr)
        return 2
    if output:
        print(output.rstrip())
    return 0


def normalize_base_url(host: str) -> str:
    host = host.rstrip("/")
    if host.startswith("http://") or host.startswith("https://"):
        return host
    return f"http://{host}"


def http_request(host: str, path: str, body: str | None = None, timeout: float = 5) -> str:
    base_url = normalize_base_url(host)
    data = None if body is None else body.encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers={"Content-Type": "text/plain; charset=utf-8"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def send_http_command(host: str, command: str, timeout: float) -> int:
    try:
        output = http_request(host, "/api/command", command.rstrip() + "\n", timeout)
    except urllib.error.URLError as exc:
        print(f"http error: {exc}", file=sys.stderr)
        return 2
    if output:
        print(output.rstrip())
    return 0


def print_http_status(host: str, timeout: float) -> int:
    try:
        output = http_request(host, "/api/status", timeout=timeout)
    except urllib.error.URLError as exc:
        print(f"http error: {exc}", file=sys.stderr)
        return 2
    try:
        print(json.dumps(json.loads(output), ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(output.rstrip())
    return 0


def interactive_serial(port: str, baud: int) -> int:
    try:
        with open_port(port, baud) as ser:
            print(f"connected to {port} at {baud}. Ctrl-D/Ctrl-C to quit.")
            print(drain(ser, 0.5).rstrip())
            while True:
                try:
                    line = input("stackchan> ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                if not line.strip():
                    continue
                ser.write((line.rstrip() + "\n").encode("utf-8"))
                ser.flush()
                print(drain(ser, 0.4).rstrip())
    except RuntimeError as exc:
        print(f"serial error: {exc}", file=sys.stderr)
        return 2


def interactive_http(host: str, timeout: float) -> int:
    print(f"connected to {normalize_base_url(host)}. Ctrl-D/Ctrl-C to quit.")
    print_http_status(host, timeout)
    while True:
        try:
            line = input("stackchan> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line.strip():
            continue
        send_http_command(host, line, timeout)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="*",
        help="Command to send, for example: face happy",
    )
    parser.add_argument("--port", default="", help="Serial device; auto-detected when omitted")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--host",
        help="Use HTTP instead of serial, for example: 192.168.4.1 or stackchan.local",
    )
    parser.add_argument("--status-json", action="store_true", help="Fetch /api/status over HTTP")
    parser.add_argument("--wait", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--interactive", "-i", action="store_true")
    args = parser.parse_args(argv)

    if args.host:
        if args.status_json:
            return print_http_status(args.host, args.timeout)
        if args.interactive or not args.command:
            return interactive_http(args.host, args.timeout)
        return send_http_command(args.host, " ".join(args.command), args.timeout)

    try:
        port = args.port or discover_port()
    except RuntimeError as exc:
        print(f"serial error: {exc}", file=sys.stderr)
        return 2
    if args.interactive or not args.command:
        return interactive_serial(port, args.baud)
    return send_command(port, args.baud, " ".join(args.command), args.wait)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

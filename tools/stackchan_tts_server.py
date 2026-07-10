#!/usr/bin/env python3
"""Tiny macOS TTS HTTP server for Stack-chan remote TTS."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


DEFAULT_EN_VOICE = os.environ.get("STACKCHAN_TTS_EN_VOICE", "Samantha")
DEFAULT_ZH_VOICE = os.environ.get("STACKCHAN_TTS_ZH_VOICE", "Tingting")
DEFAULT_RATE = int(os.environ.get("STACKCHAN_TTS_RATE", "185"))
DEFAULT_SAMPLE_RATE = int(os.environ.get("STACKCHAN_TTS_SAMPLE_RATE", "24000"))
CACHE_FORMAT_VERSION = 2
MAX_TEXT_CHARS = 300


def contains_cjk(text: str) -> bool:
    return any("\u3400" <= ch <= "\u9fff" for ch in text)


def choose_voice(text: str, query: dict[str, list[str]]) -> str:
    if "voice" in query and query["voice"]:
        return query["voice"][0]
    lang = query.get("lang", ["auto"])[0].lower()
    if lang.startswith("zh"):
        return DEFAULT_ZH_VOICE
    if lang.startswith("en"):
        return DEFAULT_EN_VOICE
    return DEFAULT_ZH_VOICE if contains_cjk(text) else DEFAULT_EN_VOICE


def cache_key(text: str, voice: str, rate: int, sample_rate: int) -> str:
    raw = json.dumps(
        {
            "version": CACHE_FORMAT_VERSION,
            "text": text,
            "voice": voice,
            "rate": rate,
            "sample_rate": sample_rate,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_chunk(data: bytes, offset: int) -> tuple[bytes, bytes, int]:
    if offset + 8 > len(data):
        raise ValueError("truncated WAV chunk")
    chunk_id = data[offset : offset + 4]
    size = struct.unpack_from("<I", data, offset + 4)[0]
    start = offset + 8
    end = start + size
    if end > len(data):
        raise ValueError("invalid WAV chunk size")
    return chunk_id, data[start:end], end + (size & 1)


def canonical_pcm_wav(data: bytes, expected_sample_rate: int) -> bytes:
    """Strip filler chunks so Moddable can parse the data chunk inside 512 bytes."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")

    fmt_chunk: bytes | None = None
    data_chunk: bytes | None = None
    offset = 12
    while offset + 8 <= len(data):
        chunk_id, chunk, offset = _read_chunk(data, offset)
        if chunk_id == b"fmt ":
            fmt_chunk = chunk
        elif chunk_id == b"data":
            data_chunk = chunk
            break

    if fmt_chunk is None or data_chunk is None:
        raise ValueError("missing WAV fmt/data chunk")
    if len(fmt_chunk) < 16:
        raise ValueError("invalid WAV fmt chunk")

    audio_format, channels, sample_rate, _byte_rate, _block_align, bits = struct.unpack_from(
        "<HHIIHH", fmt_chunk
    )
    if audio_format not in (1, 0xFFFE):
        raise ValueError(f"unsupported WAV format {audio_format}")
    if channels != 1:
        raise ValueError(f"expected mono WAV, got {channels} channels")
    if bits != 16:
        raise ValueError(f"expected 16-bit WAV, got {bits}-bit")
    if sample_rate != expected_sample_rate:
        raise ValueError(f"expected {expected_sample_rate} Hz WAV, got {sample_rate} Hz")

    block_align = channels * bits // 8
    byte_rate = sample_rate * block_align
    riff_size = 36 + len(data_chunk)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        len(data_chunk),
    )
    return header + data_chunk


def synthesize_wav(text: str, voice: str, rate: int, sample_rate: int, cache_dir: Path) -> Path:
    digest = cache_key(text, voice, rate, sample_rate)
    wav_path = cache_dir / f"{digest}.wav"
    if wav_path.exists() and wav_path.stat().st_size > 44:
        return wav_path

    cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stackchan-tts-") as tmp:
        aiff_path = Path(tmp) / "speech.aiff"
        tmp_wav = Path(tmp) / "speech.wav"
        subprocess.run(
            ["/usr/bin/say", "-v", voice, "-r", str(rate), "-o", str(aiff_path), text],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            [
                "/usr/bin/afconvert",
                "-f",
                "WAVE",
                "-d",
                f"LEI16@{sample_rate}",
                "-c",
                "1",
                str(aiff_path),
                str(tmp_wav),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        wav_path.write_bytes(canonical_pcm_wav(tmp_wav.read_bytes(), sample_rate))
    return wav_path


class TTSHandler(BaseHTTPRequestHandler):
    server_version = "StackChanTTS/0.1"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            self.send_json({"ok": True, "voices": {"en": DEFAULT_EN_VOICE, "zh": DEFAULT_ZH_VOICE}})
            return
        if parsed.path != "/say":
            self.send_error(404, "not found")
            return

        query = urllib.parse.parse_qs(parsed.query)
        text = query.get("text", [""])[0].strip()
        if not text:
            self.send_error(400, "missing text")
            return
        if len(text) > MAX_TEXT_CHARS:
            self.send_error(400, "text too long")
            return

        voice = choose_voice(text, query)
        rate = int(query.get("rate", [str(DEFAULT_RATE)])[0])
        sample_rate = int(query.get("sampleRate", [str(DEFAULT_SAMPLE_RATE)])[0])
        try:
            wav_path = synthesize_wav(text, voice, rate, sample_rate, self.server.cache_dir)
        except subprocess.CalledProcessError as error:
            message = error.stderr.decode("utf-8", "replace") if error.stderr else str(error)
            self.send_error(500, message)
            return
        except ValueError as error:
            self.send_error(500, str(error))
            return

        data = wav_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def send_json(self, payload: dict[str, object]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve macOS say() WAV files to Stack-chan.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--cache-dir", default="/private/tmp/stackchan-tts-cache")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), TTSHandler)
    server.cache_dir = Path(args.cache_dir)
    print(
        f"Stack-chan TTS server listening on http://{args.host}:{args.port} "
        f"(en={DEFAULT_EN_VOICE}, zh={DEFAULT_ZH_VOICE})",
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

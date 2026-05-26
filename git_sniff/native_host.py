import sys
import os
import json
import struct
import asyncio
import logging
import shutil
import argparse
from pathlib import Path
from typing import Optional

from git_sniff.engine import evaluate
from git_sniff.auth import resolve_token
from git_sniff.schemas import BadRepoError, GitSniffError

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger("git_sniff.native_host")

HOST_TIMEOUT = 30
MAX_MESSAGE_BYTES = 1 << 20

HOST_NAME = "com.jakobtfaber.git_sniff"
EXTENSION_ID = "nbhaknefbgabonfbnjeccpcpkikjoboc"  # pinned via extension/manifest.json "key"
CHROME_NM_DIR = (
    Path.home()
    / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts"
)


def manifest_path() -> Path:
    return CHROME_NM_DIR / f"{HOST_NAME}.json"


def host_binary_path() -> Optional[str]:
    found = shutil.which("git-sniff-host")
    return os.path.realpath(found) if found else None


def build_manifest(path: str) -> dict:
    return {
        "name": HOST_NAME,
        "description": "git-sniff native messaging host",
        "path": path,
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"],
    }


def install() -> None:
    path = host_binary_path()
    if not path:
        raise SystemExit(
            "git-sniff-host not found on PATH. Run: pip install -e . in the git-sniff repo."
        )
    CHROME_NM_DIR.mkdir(parents=True, exist_ok=True)
    target = manifest_path()
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(json.dumps(build_manifest(path), indent=2))
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    print(f"Installed native host manifest: {target}")
    print(f"  path:           {path}")
    print(f"  allowed origin: chrome-extension://{EXTENSION_ID}/")


def uninstall() -> None:
    target = manifest_path()
    try:
        target.unlink()
        print(f"Removed {target}")
    except FileNotFoundError:
        print(f"Nothing to remove at {target}")


def status() -> None:
    target = manifest_path()
    print(f"Host name:        {HOST_NAME}")
    print(f"Expected origin:  chrome-extension://{EXTENSION_ID}/")
    print(f"Manifest path:    {target}")
    binary = host_binary_path()
    print(f"Resolved binary:  {binary or '(git-sniff-host not on PATH)'}")
    if binary:
        print(f"  exists/executable: {os.path.isfile(binary) and os.access(binary, os.X_OK)}")
    if not target.exists():
        print("Manifest: NOT INSTALLED (run: git-sniff-host --install)")
        return
    try:
        data = json.loads(target.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"Manifest: INVALID ({e})")
        return
    origins = data.get("allowed_origins", [])
    expected = f"chrome-extension://{EXTENSION_ID}/"
    if expected in origins:
        print("Origin: OK (matches expected extension ID)")
    else:
        print(f"Origin: DRIFT/MISMATCH — manifest has {origins}, expected {expected}")
    registered = data.get("path")
    print(f"Registered path: {registered}")
    if binary and registered != binary:
        print(f"Path: DRIFT — manifest path {registered} != resolved binary {binary} (re-run --install)")


def encode_message(obj) -> bytes:
    data = json.dumps(obj).encode("utf-8")
    return struct.pack("@I", len(data)) + data


def read_message(stream) -> Optional[dict]:
    raw_len = stream.read(4)
    if len(raw_len) < 4:
        return None
    (length,) = struct.unpack("@I", raw_len)
    if length > MAX_MESSAGE_BYTES:
        raise ValueError(f"Incoming message length {length} exceeds {MAX_MESSAGE_BYTES} bytes.")
    data = stream.read(length)
    if len(data) < length:
        raise ValueError(f"Truncated message: expected {length} bytes, got {len(data)}.")
    return json.loads(data.decode("utf-8"))


def write_message(stream, obj) -> None:
    stream.write(encode_message(obj))
    stream.flush()


async def _handle(stdin_buf, stdout_buf) -> None:
    try:
        message = read_message(stdin_buf)
        if message is None:
            return
        if not isinstance(message, dict):
            raise BadRepoError("Request must be a JSON object with 'owner' and 'repo'.")
        owner = message.get("owner")
        repo = message.get("repo")
        if not owner or not repo:
            raise BadRepoError("Request must include non-empty 'owner' and 'repo'.")
        scorecard = await asyncio.wait_for(
            evaluate(owner, repo, token=resolve_token()),
            timeout=HOST_TIMEOUT,
        )
        write_message(stdout_buf, scorecard.model_dump())
    except asyncio.TimeoutError:
        write_message(stdout_buf, {
            "error": "Connection timed out. GitHub statistics took too long to compile."
        })
    except GitSniffError as e:
        write_message(stdout_buf, {"error": str(e)})
    except Exception as e:
        logger.exception("Unexpected native host failure")
        write_message(stdout_buf, {"error": f"git-sniff host error: {e}"})


def run_host() -> None:
    asyncio.run(_handle(sys.stdin.buffer, sys.stdout.buffer))


def main():
    parser = argparse.ArgumentParser(
        prog="git-sniff-host",
        description="git-sniff Chrome Native Messaging host.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--install", action="store_true", help="Write/update the Chrome native-host manifest.")
    group.add_argument("--uninstall", action="store_true", help="Remove the manifest.")
    group.add_argument("--status", action="store_true", help="Print manifest/host/origin status.")
    args, _ = parser.parse_known_args()

    if args.install:
        install()
    elif args.uninstall:
        uninstall()
    elif args.status:
        status()
    else:
        run_host()

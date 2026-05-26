import sys
import json
import struct
import asyncio
import logging
from typing import Optional

from git_sniff.engine import evaluate
from git_sniff.auth import resolve_token
from git_sniff.schemas import BadRepoError, GitSniffError

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger("git_sniff.native_host")

HOST_TIMEOUT = 30
MAX_MESSAGE_BYTES = 1 << 20


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
    run_host()

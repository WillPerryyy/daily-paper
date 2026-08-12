"""Hash live.json's content with the `updated` timestamp stripped.

The Action runs every three hours, and live.json always carries a fresh
timestamp, so comparing the raw file would report a change on every single run
and commit eight times a day whether or not a price or a headline actually
moved. Hashing the payload minus that one field is the difference between a
meaningful history and noise.

Reads a file path, or stdin with `-`. Prints "none" rather than raising when
there is nothing to read, so the workflow can use it on the first run.
"""
import hashlib
import json
import sys


def digest(text):
    try:
        d = json.loads(text)
    except Exception:  # noqa: BLE001
        return "none"
    d.pop("updated", None)
    return hashlib.sha256(
        json.dumps(d, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "data/live.json"
    try:
        raw = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
    except Exception:  # noqa: BLE001
        print("none")
    else:
        print(digest(raw))

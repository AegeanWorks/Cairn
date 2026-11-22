"""Python oracle for the `run --json` CLI envelope."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: python parity/run_dump.py <source.crn>", file=sys.stderr)
        return 2
    completed = subprocess.run(
        [sys.executable, "-m", "cairn", "run", arguments[0], "--json"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    sys.stdout.buffer.write(completed.stdout.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

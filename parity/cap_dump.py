"""Python oracle for the capability manifest parity check."""

import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from cairn.capabilities import capabilities  # noqa: E402


def main():
    sys.stdout.buffer.write((json.dumps(capabilities(), indent=2) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Python oracle for the magic-division helper parity check."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn.codegen import magic_division_parameters  # noqa: E402


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: python rust/magic_dump.py <spec>", file=sys.stderr)
        return 2
    for line in Path(arguments[0]).read_text(encoding="utf-8").splitlines():
        answer = magic_division_parameters(int(line))
        print(f"{line} -> {'NONE' if answer is None else ','.join(map(str, answer))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

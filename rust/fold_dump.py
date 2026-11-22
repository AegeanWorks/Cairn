"""Python oracle for the folding parity check."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn.optimize import fold_constant, fold_unary  # noqa: E402


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: python rust/fold_dump.py <spec>", file=sys.stderr)
        return 2
    for line in Path(arguments[0]).read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if fields[0] == "BIN":
            answer = fold_constant(fields[1], int(fields[2]), int(fields[3]))
        elif fields[0] == "UN":
            answer = fold_unary(fields[1], int(fields[2]))
        else:
            answer = None
        print(f"{line} -> {'NONE' if answer is None else answer}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

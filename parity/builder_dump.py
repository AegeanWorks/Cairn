"""Python oracle for the build pipeline parity check."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn.builder import compile_source  # noqa: E402


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: python parity/builder_dump.py <source.crn>", file=sys.stderr)
        return 2
    source = Path(arguments[0]).read_text(encoding="utf-8-sig")
    build = compile_source(source, arguments[0])
    lines = [
        f"FOLDED {build.folded}",
        f"SIZE {len(build.image)}",
        f"IMAGE {build.image.hex()}",
    ]
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

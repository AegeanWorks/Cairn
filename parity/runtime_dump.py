"""Python oracle for the runtime emitter parity check."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn import runtime  # noqa: E402
from cairn.image import ReadOnlyData, WritableData  # noqa: E402
from cairn.machine import Assembler  # noqa: E402


def main():
    assembler = Assembler()
    rodata = ReadOnlyData()
    writable = WritableData()
    runtime.emit_runtime(assembler, rodata, writable)
    # The Rust emitter stores labels in a sorted map; sorting here compares content, not
    # Python's insertion order, which is not part of the emitted-byte contract.
    lines = [f"CODE {bytes(assembler.code).hex()}",
             "LABELS " + "|".join(f"{n}={o}" for n, o in sorted(assembler.labels.items())),
             "FIXUPS " + "|".join(f"{o}={t}" for o, t in assembler.fixups),
             f"RODATA {bytes(rodata.data).hex()}",
             "ROLABELS " + "|".join(f"{n}={o}" for n, o in sorted(rodata.labels.items())),
             f"WRSIZE {len(writable.data)}",
             "WRLABELS " + "|".join(f"{n}={o}" for n, o in sorted(writable.labels.items()))]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

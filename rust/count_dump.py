"""Python oracle for the countdown-latch parity check."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dump import program  # noqa: E402
from cairn.diagnostics import DiagnosticError  # noqa: E402
from cairn.optimize import (  # noqa: E402
    apply_algebraic_simplification,
    apply_copy_propagation,
    apply_countdown_latches,
    lower_for_loops,
)
from cairn.parser import parse  # noqa: E402
from cairn.sema import analyze  # noqa: E402


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: python rust/count_dump.py <source.crn>", file=sys.stderr)
        return 2
    source = Path(arguments[0]).read_text(encoding="utf-8")
    try:
        program_node = parse(source, arguments[0])
        analyze(program_node, arguments[0])
        lower_for_loops(program_node)
        apply_copy_propagation(program_node)
        apply_algebraic_simplification(program_node)
        apply_countdown_latches(program_node)
    except DiagnosticError as error:
        text = "\n".join(
            f"ERROR[{item.code},{item.message},{item.fix},{item.span.start}]"
            for item in error.diagnostics
        )
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        return 0
    sys.stdout.buffer.write((program(program_node) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

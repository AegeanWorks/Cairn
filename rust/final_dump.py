"""Python oracle for the final optimizer passes (induction and rotation) parity."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dump import program  # noqa: E402
from cairn.diagnostics import DiagnosticError  # noqa: E402
from cairn.optimize import (  # noqa: E402
    Optimizer,
    apply_algebraic_simplification,
    apply_bottom_test_rotation,
    apply_constant_loop_unrolling,
    apply_copy_propagation,
    apply_countdown_latches,
    apply_induction_strength_reduction,
    apply_odd_pair_fusion,
    lower_for_loops,
)
from cairn.parser import parse  # noqa: E402
from cairn.sema import analyze  # noqa: E402


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: python rust/final_dump.py <source.crn>", file=sys.stderr)
        return 2
    source = Path(arguments[0]).read_text(encoding="utf-8")
    try:
        program_node = parse(source, arguments[0])
        analyze(program_node, arguments[0])
        lower_for_loops(program_node)
        apply_copy_propagation(program_node)
        apply_algebraic_simplification(program_node)
        apply_constant_loop_unrolling(program_node)
        apply_countdown_latches(program_node)
        if apply_odd_pair_fusion(program_node):
            Optimizer().run(program_node)
            apply_odd_pair_fusion(program_node)
        apply_induction_strength_reduction(program_node)
        apply_bottom_test_rotation(program_node)
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

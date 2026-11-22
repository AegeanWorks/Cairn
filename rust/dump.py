"""The Python reference's canonical front-end dump.

It prints exactly the same format as `cairn-rs dump`, so `check_parity.py` can prove that the Rust
compiler accepts the same language: same tokens, same AST shapes and spans, same diagnostics.
"""

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn import syntax  # noqa: E402
from cairn.diagnostics import DiagnosticError  # noqa: E402
from cairn.lexer import tokenize  # noqa: E402
from cairn.parser import Parser  # noqa: E402
from cairn.sema import analyze  # noqa: E402


def q(text):
    return json.dumps(text, ensure_ascii=True)


def span(value):
    return f"{value.start}:{value.end}:{value.line}:{value.column}:{value.end_line}:{value.end_column}"


def opt(value):
    return "NONE" if value is None else value


def boolean(value):
    return "true" if value else "false"


def expr(node):
    if isinstance(node, syntax.IntLiteral):
        return f"INT[{node.value}][{span(node.span)}]"
    if isinstance(node, syntax.StrLiteral):
        return f"STR[{q(node.text)}][{span(node.span)}]"
    if isinstance(node, syntax.Name):
        return f"NAME[{q(node.name)}][{node.slot}][{span(node.span)}]"
    if isinstance(node, syntax.Unary):
        return f"UNARY[{q(node.op)}][{span(node.span)}]({expr(node.operand)})"
    if isinstance(node, syntax.Binary):
        return (f"BINARY[{q(node.op)}][{span(node.span)}]"
                f"({expr(node.left)},{expr(node.right)})")
    if isinstance(node, syntax.Parens):
        return f"PARENS[{span(node.span)}]({expr(node.inner)})"
    if isinstance(node, syntax.Call):
        args = ",".join(expr(argument) for argument in node.arguments)
        return f"CALL[{q(node.name)}][{span(node.span)}]({args})"
    if isinstance(node, syntax.ArrayLiteral):
        items = ",".join(expr(element) for element in node.elements)
        return f"ARRAY[{span(node.span)}]({items})"
    if isinstance(node, syntax.Index):
        return (f"INDEX[{node.slot},{node.size}][{span(node.span)}]"
                f"({expr(node.operand)},{expr(node.index)})")
    raise TypeError(f"unhandled expression {type(node).__name__}")


def block(node):
    statements = "|".join(stmt(item) for item in node.statements)
    return f"BLOCK[{span(node.span)}]({statements})"


def stmt(node):
    if isinstance(node, syntax.Block):
        return block(node)
    if isinstance(node, syntax.Let):
        return (f"LET[{q(node.name)},{boolean(node.mutable)},{q(node.type_name)},"
                f"{node.slot},{opt(node.array_size)}][{span(node.span)}]({expr(node.value)})")
    if isinstance(node, syntax.Assign):
        return (f"ASSIGN[{q(node.name)},{node.slot},{opt(node.temp_slot)},"
                f"{opt(node.array_size)}][{span(node.span)}]"
                f"({opt(expr(node.index) if node.index is not None else None)},{expr(node.value)})")
    if isinstance(node, syntax.If):
        return (f"IF[{span(node.span)}]({expr(node.condition)},{block(node.then_block)},"
                f"{opt(block(node.else_block) if node.else_block is not None else None)})")
    if isinstance(node, syntax.While):
        return (f"WHILE[{boolean(node.countdown)}][{span(node.span)}]"
                f"({expr(node.condition)},{block(node.body)})")
    if isinstance(node, syntax.For):
        return (f"FOR[{q(node.name)},{boolean(node.inclusive)},{node.slot}]"
                f"[{span(node.span)}]({expr(node.start)},{expr(node.end)},{block(node.body)})")
    if isinstance(node, syntax.Return):
        return f"RETURN[{span(node.span)}]({expr(node.value)})"
    if isinstance(node, syntax.Break):
        return f"BREAK[{span(node.span)}]"
    if isinstance(node, syntax.Continue):
        return f"CONTINUE[{span(node.span)}]"
    if isinstance(node, syntax.ExprStmt):
        return f"EXPRSTMT[{span(node.span)}]({expr(node.expression)})"
    raise TypeError(f"unhandled statement {type(node).__name__}")


def program(node):
    functions = []
    for function in node.functions:
        parameters = "|".join(
            f"PARAM[{q(item.name)},{q(item.type_name)},{item.slot}][{span(item.span)}]"
            for item in function.parameters
        )
        functions.append(
            f"FN[{q(function.name)},{q(function.return_type)},{function.slots}]"
            f"[{span(function.span)}](PARAMS({parameters}),BODY({block(function.body)}))"
        )
    return f"PROGRAM[{span(node.span)}]({'|'.join(functions)})"


def errors(diagnostic_error):
    return "\n".join(
        f"ERROR[{q(item.code)},{q(item.message)},{q(item.fix)},{span(item.span)}]"
        for item in diagnostic_error.diagnostics
    )


def dump(source, path):
    lines = []
    try:
        tokens = tokenize(source, path)
    except DiagnosticError as error:
        return errors(error) + "\n"
    lines.append("\n".join(
        f"TOK[{q(token.kind)},{q(token.text)},{token.value},{span(token.span)}]"
        for token in tokens
    ))
    try:
        parsed = Parser(tokens, path).parse_program()
    except DiagnosticError as error:
        lines.append(errors(error))
        return "\n".join(lines) + "\n"
    lines.append(program(parsed))
    analysed = copy.deepcopy(parsed)
    try:
        analyze(analysed, path)
        lines.append(f"SEMA_OK({program(analysed)})")
    except DiagnosticError as error:
        lines.append(errors(error))
    return "\n".join(lines) + "\n"


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: python rust/dump.py <source.crn>", file=sys.stderr)
        return 2
    source = Path(arguments[0]).read_text(encoding="utf-8")
    sys.stdout.write(dump(source, arguments[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

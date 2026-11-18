"""Parser: recursive descent over the token list.

Precedence follows C for the operators that exist, so an agent's existing intuition transfers
without a table lookup. The grammar has no expression that is also a declaration: `let` and
`var` introduce names, and every other expression statement must be a call.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from cairn.diagnostics import DiagnosticError, Span, fail
from cairn.lexer import Token, tokenize
from cairn.optimize import INT64_MIN
from cairn import syntax

PRECEDENCE = {
    "||": 1,
    "&&": 2,
    "|": 3,
    "^": 4,
    "&": 5,
    "==": 6,
    "!=": 6,
    "<": 7,
    "<=": 7,
    ">": 7,
    ">=": 7,
    "<<": 8,
    ">>": 8,
    "+": 9,
    "-": 9,
    "*": 10,
    "/": 10,
    "%": 10,
}

UNARY_OPERATORS = ("-", "!", "~")
TYPE_NAMES = frozenset({"i64"})


@dataclass
class Parser:
    """A cursor over one token list; every parse method consumes what it matches."""

    tokens: List[Token]
    path: str = "<source>"
    index: int = 0

    def parse_program(self) -> syntax.Program:
        """Parse top-level functions until end of file."""
        if not self.tokens or self.tokens[-1].kind != "eof":
            span = self.tokens[-1].span if self.tokens else Span.point(0, 1, 1)
            raise fail("E2001", "expected a complete token list ending in end of file, "
                                "found a truncated list",
                       "Parse with cairn.parser.parse so the input ends in an 'eof' token.",
                       span, self.path)
        functions: List[syntax.Function] = []
        while not self._at_end():
            if not self._peek().is_keyword("fn"):
                raise self._expected("a function, starting with 'fn'")
            functions.append(self._parse_function())
        span = (Span.join(functions[0].span, functions[-1].span) if functions
                else self._peek().span)
        return syntax.Program(span=span, functions=functions)

    def _parse_function(self) -> syntax.Function:
        """`fn name(a: i64, ...) -> i64 { ... }`."""
        start = self._expect_keyword("fn")
        name = self._expect_ident("a function name")
        self._expect_symbol("(")
        parameters: List[syntax.Parameter] = []
        if not self._peek().is_symbol(")"):
            parameters.append(self._parse_parameter())
            while self._peek().is_symbol(","):
                self._advance()
                parameters.append(self._parse_parameter())
        self._expect_symbol(")")
        self._expect_symbol("->")
        _return_size, return_type = self._parse_type()
        body = self._parse_block()
        span = Span.join(start.span, body.span)
        return syntax.Function(span=span, name=name.text, parameters=parameters,
                               return_type=return_type, body=body)

    def _parse_parameter(self) -> syntax.Parameter:
        """One `name: i64` parameter."""
        name = self._expect_ident("a parameter name")
        self._expect_symbol(":")
        _size, type_name = self._parse_type()
        return syntax.Parameter(span=Span.join(name.span, self.tokens[self.index - 1].span),
                                name=name.text, type_name=type_name)

    def _parse_type(self, allow_array: bool = False) -> Tuple[Optional[int], str]:
        """One type: `i64`, or `[N]i64` where arrays are allowed; answers (size, name).

        Arrays stay a local-binding feature: a parameter or return type carrying
        `[N]` is refused outright (E2002).
        """
        token = self._peek()
        if token.is_symbol("["):
            open_bracket = self._advance()
            count = self._peek()
            if count.kind != "int":
                raise self._expected("an array length")
            self._advance()
            self._expect_symbol("]")
            element = self._peek()
            if element.kind != "ident" or element.text not in TYPE_NAMES:
                raise self._expected("an element type name")
            self._advance()
            if not allow_array:
                raise fail("E2002", "an array type needs a local 'let' or 'var' binding",
                           "Arrays bind only to locals; parameters and return types stay "
                           "'i64'.",
                           open_bracket.span, self.path)
            return count.value, element.text
        if token.kind != "ident":
            raise self._expected("a type name")
        if token.text not in TYPE_NAMES:
            raise fail("E2002", f"unknown type {token.text!r}",
                       "Cairn 0.1 has exactly one type, i64; write 'i64'.",
                       token.span, self.path)
        self._advance()
        return None, token.text

    def _parse_array_literal(self) -> syntax.ArrayLiteral:
        """A `[` elements `]` literal; the parser sees one element list.

        `[v; N]` is the whole literal: the fill expansion evaluates the fill
        expression once per element, in element order, which is the documented
        aggregate-literal semantics.
        """
        open_bracket = self._expect_symbol("[")
        span = open_bracket.span
        if self._peek().is_symbol("]"):
            close = self._advance()
            return syntax.ArrayLiteral(span=Span.join(span, close.span), elements=[])
        first = self._parse_expression()
        if self._peek().is_symbol(";"):
            self._advance()
            count = self._peek()
            if count.kind != "int":
                raise self._expected("an integer fill count")
            self._advance()
            close = self._expect_symbol("]")
            elements = [self._clone(first, 1) for _ in range(count.value)]
            return syntax.ArrayLiteral(span=Span.join(span, close.span),
                                       elements=elements)
        elements = [first]
        while self._peek().is_symbol(","):
            self._advance()
            elements.append(self._parse_expression())
        close = self._expect_symbol("]")
        return syntax.ArrayLiteral(span=Span.join(span, close.span),
                                   elements=elements)

    def _clone(self, expression: syntax.Expr, depth: int) -> syntax.Expr:
        """A deep copy of one expression; fill expansion needs one node per element."""
        if depth > 64:
            raise self._expected("a fill expression shallower than 64 nested operators")
        if isinstance(expression, (syntax.IntLiteral, syntax.Name, syntax.StrLiteral)):
            return expression
        if isinstance(expression, syntax.Parens):
            return syntax.Parens(span=expression.span,
                                 inner=self._clone(expression.inner, depth + 1))
        if isinstance(expression, syntax.Unary):
            return syntax.Unary(span=expression.span, op=expression.op,
                                operand=self._clone(expression.operand, depth + 1))
        if isinstance(expression, syntax.Binary):
            return syntax.Binary(span=expression.span, op=expression.op,
                                 left=self._clone(expression.left, depth + 1),
                                 right=self._clone(expression.right, depth + 1))
        if isinstance(expression, syntax.Call):
            return syntax.Call(span=expression.span, name=expression.name,
                               arguments=[self._clone(argument, depth + 1)
                                          for argument in expression.arguments])
        raise self._expected("a fill expression the parser can copy")

    def _parse_block(self) -> syntax.Block:
        """`{ statement* }`."""
        start = self._expect_symbol("{")
        statements: List[syntax.Stmt] = []
        while not self._peek().is_symbol("}"):
            if self._at_end():
                raise self._expected("'}' to close the block")
            statements.append(self._parse_statement())
        end = self._expect_symbol("}")
        return syntax.Block(span=Span.join(start.span, end.span), statements=statements)

    def _parse_statement(self) -> syntax.Stmt:
        """Dispatch on the first token of one statement."""
        token = self._peek()
        if token.is_keyword("let") or token.is_keyword("var"):
            return self._parse_binding()
        if token.is_keyword("if"):
            return self._parse_if()
        if token.is_keyword("while"):
            return self._parse_while()
        if token.is_keyword("for"):
            return self._parse_for()
        if token.is_keyword("return"):
            return self._parse_return()
        if token.is_keyword("break"):
            self._advance()
            semi = self._expect_symbol(";")
            return syntax.Break(span=Span.join(token.span, semi.span))
        if token.is_keyword("continue"):
            self._advance()
            semi = self._expect_symbol(";")
            return syntax.Continue(span=Span.join(token.span, semi.span))
        if token.is_symbol("{"):
            return self._parse_block()
        return self._parse_expression_statement()

    def _parse_binding(self) -> syntax.Let:
        """`let|var name: i64 = expr;` or `let|var name: [N]i64 = literal;`."""
        keyword = self._advance()
        name = self._expect_ident("a binding name")
        self._expect_symbol(":")
        size, type_name = self._parse_type(allow_array=True)
        self._expect_symbol("=")
        if size is not None and self._peek().is_symbol("["):
            value = self._parse_array_literal()
        else:
            value = self._parse_expression()
        semi = self._expect_symbol(";")
        return syntax.Let(span=Span.join(keyword.span, semi.span), name=name.text,
                          mutable=keyword.text == "var", type_name=type_name,
                          value=value, array_size=size)

    def _parse_if(self) -> syntax.If:
        """`if expr block [else (if | block)]`."""
        start = self._expect_keyword("if")
        condition = self._parse_expression()
        then_block = self._parse_block()
        else_block: Optional[syntax.Block] = None
        end_span = then_block.span
        if self._peek().is_keyword("else"):
            self._advance()
            if self._peek().is_keyword("if"):
                nested = self._parse_if()
                else_block = syntax.Block(span=nested.span, statements=[nested])
            else:
                else_block = self._parse_block()
            end_span = else_block.span
        return syntax.If(span=Span.join(start.span, end_span), condition=condition,
                         then_block=then_block, else_block=else_block)

    def _parse_while(self) -> syntax.While:
        """`while expr block`."""
        start = self._expect_keyword("while")
        condition = self._parse_expression()
        body = self._parse_block()
        return syntax.While(span=Span.join(start.span, body.span), condition=condition,
                            body=body)

    def _parse_for(self) -> syntax.For:
        """`for name in start..end block`, with `..=` for the inclusive upper bound."""
        start = self._expect_keyword("for")
        name = self._expect_ident("a loop variable name")
        self._expect_keyword("in")
        lower = self._parse_expression()
        token = self._peek()
        if token.is_symbol(".."):
            inclusive = False
            self._advance()
        elif token.is_symbol("..="):
            inclusive = True
            self._advance()
        else:
            raise fail("E2005", "expected '..' or '..=' in the for range",
                       "Write 'for i in 0..10 { ... }' or 'for i in 0..=10 { ... }'.",
                       token.span, self.path)
        upper = self._parse_expression()
        body = self._parse_block()
        return syntax.For(span=Span.join(start.span, body.span), name=name.text,
                          start=lower, end=upper, inclusive=inclusive, body=body)

    def _parse_return(self) -> syntax.Return:
        """`return expr;`."""
        start = self._expect_keyword("return")
        value = self._parse_expression()
        semi = self._expect_symbol(";")
        return syntax.Return(span=Span.join(start.span, semi.span), value=value)

    def _parse_expression_statement(self) -> syntax.Stmt:
        """An assignment to a name, or a call whose value is discarded."""
        start_span = self._peek().span
        expression = self._parse_expression()
        if self._peek().is_symbol("="):
            if isinstance(expression, syntax.Index):
                operand = expression.operand
                if not isinstance(operand, syntax.Name):
                    raise fail("E2003", "only an array element can be index-assigned",
                               "Write 'a[expression] = value;' with `a` a 'var' array.",
                               operand.span, self.path)
                self._advance()
                value = self._parse_expression()
                semi = self._expect_symbol(";")
                return syntax.Assign(span=Span.join(start_span, semi.span),
                                     name=operand.name, value=value,
                                     index=expression.index)
            if not isinstance(expression, syntax.Name):
                raise fail("E2003", "only a variable name can be assigned",
                           "Write 'name = expression;' with a name declared by 'var'.",
                           expression.span, self.path)
            self._advance()
            value = self._parse_expression()
            semi = self._expect_symbol(";")
            return syntax.Assign(span=Span.join(start_span, semi.span),
                                 name=expression.name, value=value)
        semi = self._expect_symbol(";")
        unwrapped = expression
        while isinstance(unwrapped, syntax.Parens):
            unwrapped = unwrapped.inner
        if not isinstance(unwrapped, syntax.Call):
            raise fail("E2004", "this expression has no effect",
                       "Assign it to a 'let' or 'var', or call a function instead.",
                       expression.span, self.path)
        return syntax.ExprStmt(span=Span.join(start_span, semi.span), expression=expression)

    def _parse_expression(self, minimum: int = 1) -> syntax.Expr:
        """Precedence climbing: parse operators at least as tight as `minimum`."""
        left = self._parse_unary()
        while True:
            token = self._peek()
            if token.kind != "symbol" or token.text not in PRECEDENCE:
                return left
            precedence = PRECEDENCE[token.text]
            if precedence < minimum:
                return left
            self._advance()
            right = self._parse_expression(precedence + 1)
            left = syntax.Binary(span=Span.join(left.span, right.span), op=token.text,
                                 left=left, right=right)

    def _parse_unary(self) -> syntax.Expr:
        """`-x`, `!x`, `~x`, otherwise a primary expression."""
        token = self._peek()
        if token.kind == "symbol" and token.text in UNARY_OPERATORS:
            self._advance()
            operand = self._parse_unary()
            unwrapped = operand
            while isinstance(unwrapped, syntax.Parens):
                unwrapped = unwrapped.inner
            if (token.text == "-" and isinstance(unwrapped, syntax.IntLiteral)
                    and unwrapped.value == -INT64_MIN):
                return syntax.IntLiteral(span=Span.join(token.span, operand.span),
                                         value=INT64_MIN)
            return syntax.Unary(span=Span.join(token.span, operand.span), op=token.text,
                                operand=operand)
        return self._parse_primary()

    def _parse_primary(self) -> syntax.Expr:
        """An integer, a string, a name, a call, a parenthesised expression, or `true`/`false`."""
        token = self._peek()
        if token.kind == "int":
            self._advance()
            return syntax.IntLiteral(span=token.span, value=token.value)
        if token.kind == "str":
            self._advance()
            return syntax.StrLiteral(span=token.span, text=token.text)
        if token.kind == "keyword" and token.text in ("true", "false"):
            self._advance()
            return syntax.IntLiteral(span=token.span, value=1 if token.text == "true" else 0)
        if token.kind == "ident":
            self._advance()
            if self._peek().is_symbol("("):
                return self._parse_call(token)
            if self._peek().is_symbol("["):
                return self._parse_index(syntax.Name(span=token.span, name=token.text))
            return syntax.Name(span=token.span, name=token.text)
        if token.is_symbol("("):
            self._advance()
            inner = self._parse_expression()
            close = self._expect_symbol(")")
            return syntax.Parens(span=Span.join(token.span, close.span), inner=inner)
        raise self._expected("an expression")

    def _parse_index(self, operand: syntax.Expr) -> syntax.Index:
        """An `operand[index]` load; the opening bracket is the current token."""
        self._expect_symbol("[")
        index = self._parse_expression()
        close = self._expect_symbol("]")
        return syntax.Index(span=Span.join(operand.span, close.span),
                            operand=operand, index=index)

    def _parse_call(self, name: Token) -> syntax.Call:
        """`name(arg, ...)`; the opening parenthesis is already the current token."""
        self._expect_symbol("(")
        arguments: List[syntax.Expr] = []
        if not self._peek().is_symbol(")"):
            arguments.append(self._parse_expression())
            while self._peek().is_symbol(","):
                self._advance()
                arguments.append(self._parse_expression())
        close = self._expect_symbol(")")
        return syntax.Call(span=Span.join(name.span, close.span), name=name.text,
                           arguments=arguments)

    def _peek(self) -> Token:
        """The current token without consuming it."""
        return self.tokens[self.index]

    def _advance(self) -> Token:
        """Consume and return the current token; `eof` stays put."""
        token = self.tokens[self.index]
        if token.kind != "eof":
            self.index += 1
        return token

    def _at_end(self) -> bool:
        """Whether the cursor reached the `eof` token."""
        return self._peek().kind == "eof"

    def _expect_symbol(self, text: str) -> Token:
        """Consume the given punctuation, or report where it should have been."""
        token = self._peek()
        if not token.is_symbol(text):
            raise self._expected(f"'{text}'")
        return self._advance()

    def _expect_keyword(self, text: str) -> Token:
        """Consume the given keyword, or report where it should have been."""
        token = self._peek()
        if not token.is_keyword(text):
            raise self._expected(f"'{text}'")
        return self._advance()

    def _expect_ident(self, what: str) -> Token:
        """Consume an identifier, or report where it should have been."""
        token = self._peek()
        if token.kind != "ident":
            raise self._expected(what)
        return self._advance()

    def _expected(self, what: str) -> DiagnosticError:
        """A positioned `E2001`: what the grammar wanted and what it found instead."""
        token = self._peek()
        found = "end of file" if token.kind == "eof" else repr(token.text)
        return fail("E2001", f"expected {what}, found {found}",
                    f"Insert {what} here, or remove the token before it.",
                    token.span, self.path)


def parse(source: str, path: str = "<source>") -> syntax.Program:
    """Tokenize and parse one source text."""
    return Parser(tokenize(source, path), path).parse_program()


__all__ = ["PRECEDENCE", "Parser", "TYPE_NAMES", "parse"]

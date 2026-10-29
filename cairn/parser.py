"""Parser: recursive descent over the token list.

Precedence follows C for the operators that exist, so an agent's existing intuition transfers
without a table lookup. The grammar has no expression that is also a declaration: `let` and
`var` introduce names, and every other expression statement must be a call.
"""

from dataclasses import dataclass
from typing import List, Optional

from cairn.diagnostics import DiagnosticError, Span, fail
from cairn.lexer import Token, tokenize
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
        return_type = self._parse_type()
        body = self._parse_block()
        span = Span.join(start.span, body.span)
        return syntax.Function(span=span, name=name.text, parameters=parameters,
                               return_type=return_type, body=body)

    def _parse_parameter(self) -> syntax.Parameter:
        """One `name: i64` parameter."""
        name = self._expect_ident("a parameter name")
        self._expect_symbol(":")
        type_name = self._parse_type()
        return syntax.Parameter(span=Span.join(name.span, self.tokens[self.index - 1].span),
                                name=name.text, type_name=type_name)

    def _parse_type(self) -> str:
        """`i64` is the only type in Cairn 0.1; anything else names what is missing."""
        token = self._peek()
        if token.kind != "ident":
            raise self._expected("a type name")
        if token.text not in TYPE_NAMES:
            raise fail("E2002", f"unknown type {token.text!r}",
                       "Cairn 0.1 has exactly one type, i64; write 'i64'.",
                       token.span, self.path)
        self._advance()
        return token.text

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
        if token.is_keyword("return"):
            return self._parse_return()
        if token.is_keyword("break"):
            self._advance()
            self._expect_symbol(";")
            return syntax.Break(span=token.span)
        if token.is_keyword("continue"):
            self._advance()
            self._expect_symbol(";")
            return syntax.Continue(span=token.span)
        if token.is_symbol("{"):
            return self._parse_block()
        return self._parse_expression_statement()

    def _parse_binding(self) -> syntax.Let:
        """`let|var name: i64 = expr;`."""
        keyword = self._advance()
        name = self._expect_ident("a binding name")
        self._expect_symbol(":")
        type_name = self._parse_type()
        self._expect_symbol("=")
        value = self._parse_expression()
        semi = self._expect_symbol(";")
        return syntax.Let(span=Span.join(keyword.span, semi.span), name=name.text,
                          mutable=keyword.text == "var", type_name=type_name,
                          value=value)

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
        if not isinstance(expression, syntax.Call):
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
            return syntax.Unary(span=Span.join(token.span, operand.span), op=token.text,
                                operand=operand)
        return self._parse_primary()

    def _parse_primary(self) -> syntax.Expr:
        """An integer, a name, a call, a parenthesised expression, or `true`/`false`."""
        token = self._peek()
        if token.kind == "int":
            self._advance()
            return syntax.IntLiteral(span=token.span, value=token.value)
        if token.kind == "keyword" and token.text in ("true", "false"):
            self._advance()
            return syntax.IntLiteral(span=token.span, value=1 if token.text == "true" else 0)
        if token.kind == "ident":
            self._advance()
            if self._peek().is_symbol("("):
                return self._parse_call(token)
            return syntax.Name(span=token.span, name=token.text)
        if token.is_symbol("("):
            self._advance()
            inner = self._parse_expression()
            close = self._expect_symbol(")")
            return syntax.Parens(span=Span.join(token.span, close.span), inner=inner)
        raise self._expected("an expression")

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

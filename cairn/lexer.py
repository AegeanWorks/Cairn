"""Lexer: source text to a flat token list.

The token set is deliberately small so an agent can hold all of it at once: one integer type,
one keyword per statement form, and only the operators that lower to one instruction.
"""

from dataclasses import dataclass
from typing import List

from cairn.diagnostics import Span, fail

KEYWORDS = frozenset({"fn", "let", "var", "if", "else", "while", "return",
                      "break", "continue", "true", "false"})

MULTI_CHAR_SYMBOLS = ("->", "==", "!=", "<=", ">=", "&&", "||", "<<", ">>")
SINGLE_CHAR_SYMBOLS = frozenset("(){}:,;=+-*/%<>!&|^~")

INT64_MAX = 2 ** 63 - 1


@dataclass(frozen=True)
class Token:
    """One lexical unit: its kind, its text, its value when it is an integer, and its span."""

    kind: str
    text: str
    span: Span
    value: int = 0

    def is_symbol(self, text: str) -> bool:
        """Whether this is the given punctuation or operator."""
        return self.kind == "symbol" and self.text == text

    def is_keyword(self, text: str) -> bool:
        """Whether this is the given keyword."""
        return self.kind == "keyword" and self.text == text


class Lexer:
    """A single-pass scanner over ASCII source.

    Identifiers are `[A-Za-z_][A-Za-z0-9_]*`. Comments run from `//` to the end of the line.
    An integer literal is decimal only, so there is no base prefix to misread.
    """

    def __init__(self, source: str, path: str = "<source>") -> None:
        self.source = source
        self.path = path
        self.offset = 0
        self.line = 1
        self.column = 1

    def tokenize(self) -> List[Token]:
        """Every token in order, ending with one `eof` token."""
        tokens: List[Token] = []
        while True:
            self._skip_blank()
            if self.offset >= len(self.source):
                tokens.append(Token("eof", "", Span.point(self.offset, self.line, self.column)))
                return tokens
            tokens.append(self._next_token())

    def _skip_blank(self) -> None:
        """Consume whitespace and `//` comments."""
        while self.offset < len(self.source):
            char = self.source[self.offset]
            if char in " \t\r\n":
                self._advance(char)
            elif char == "/" and self.source.startswith("//", self.offset):
                while self.offset < len(self.source) and self.source[self.offset] != "\n":
                    self._advance(self.source[self.offset])
            else:
                return

    def _advance(self, char: str) -> None:
        """Move one character forward, keeping the line and column accurate."""
        self.offset += 1
        if char == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1

    def _next_token(self) -> Token:
        """Scan one token that is not whitespace or a comment."""
        start = Span.point(self.offset, self.line, self.column)
        char = self.source[self.offset]
        if char.isdigit():
            return self._number(start)
        if char.isalpha() or char == "_":
            return self._identifier(start)
        for symbol in MULTI_CHAR_SYMBOLS:
            if self.source.startswith(symbol, self.offset):
                self._consume(len(symbol))
                return Token("symbol", symbol, self._span_from(start))
        if char in SINGLE_CHAR_SYMBOLS:
            self._advance(char)
            return Token("symbol", char, self._span_from(start))
        raise fail("E1001", f"unexpected character {char!r}",
                   "Cairn source is ASCII: use a letter, a digit, or one of (){}:,;=+-*/%<>!&|^~.",
                   Span(start.start, start.start + 1, start.line, start.column,
                        start.line, start.column + 1), self.path)

    def _consume(self, count: int) -> None:
        """Advance over `count` characters, tracking a newline inside the run."""
        for _ in range(count):
            self._advance(self.source[self.offset])

    def _span_from(self, start: Span) -> Span:
        """The span between `start` and the current position."""
        return Span(start.start, self.offset, start.line, start.column, self.line, self.column)

    def _number(self, start: Span) -> Token:
        """A decimal integer literal that must fit in a signed 64-bit integer."""
        while self.offset < len(self.source) and self.source[self.offset].isdigit():
            self._advance(self.source[self.offset])
        text = self.source[start.start:self.offset]
        value = int(text)
        if value > INT64_MAX:
            raise fail("E1003", f"integer literal {text} does not fit in i64",
                       f"Write a value at most {INT64_MAX}.",
                       self._span_from(start), self.path)
        return Token("int", text, self._span_from(start), value)

    def _identifier(self, start: Span) -> Token:
        """A name, or one of the fixed keywords."""
        while self.offset < len(self.source):
            char = self.source[self.offset]
            if not (char.isalnum() or char == "_"):
                break
            self._advance(char)
        text = self.source[start.start:self.offset]
        kind = "keyword" if text in KEYWORDS else "ident"
        return Token(kind, text, self._span_from(start))


def tokenize(source: str, path: str = "<source>") -> List[Token]:
    """Tokenize one source text."""
    return Lexer(source, path).tokenize()


__all__ = ["KEYWORDS", "Lexer", "Token", "tokenize"]

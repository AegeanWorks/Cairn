"""Lexer tests: token shape, spans, comments and the two literal failures."""

import unittest

from cairn.diagnostics import DiagnosticError
from cairn.lexer import tokenize


class LexerTest(unittest.TestCase):
    """The scanner is the first stage an agent's source touches, so its reports must be exact."""

    def test_tokens_carry_kind_text_value_and_span(self) -> None:
        tokens = tokenize("fn main() -> i64 { return 42; }")
        self.assertEqual(tokens[0].text, "fn")
        self.assertEqual(tokens[0].kind, "keyword")
        self.assertEqual(tokens[2].text, "(")
        self.assertEqual(tokens[4].text, "->")
        number = [token for token in tokens if token.kind == "int"][0]
        self.assertEqual(number.value, 42)
        self.assertEqual((number.span.line, number.span.column), (1, 27))
        self.assertEqual(tokens[-1].kind, "eof")

    def test_comment_runs_to_end_of_line(self) -> None:
        tokens = tokenize("// fn ignored() -> i64 {}\nlet\n")
        self.assertEqual(tokens[0].text, "let")
        self.assertEqual(tokens[0].span.line, 2)

    def test_two_character_operators_win_over_one(self) -> None:
        texts = [token.text for token in tokenize("<= >= == != && || << >>")]
        self.assertEqual(texts[:-1], ["<=", ">=", "==", "!=", "&&", "||", "<<", ">>"])

    def test_integer_beyond_i64_is_a_positioned_error(self) -> None:
        with self.assertRaises(DiagnosticError) as caught:
            tokenize("let x: i64 = 9223372036854775808;")
        diagnostic = caught.exception.diagnostics[0]
        self.assertEqual(diagnostic.code, "E1003")
        self.assertIn("i64", diagnostic.message)

    def test_unexpected_character_names_the_fix(self) -> None:
        with self.assertRaises(DiagnosticError) as caught:
            tokenize("let x: i64 = 1; @")
        diagnostic = caught.exception.diagnostics[0]
        self.assertEqual(diagnostic.code, "E1001")
        self.assertIn("ASCII", diagnostic.fix)
        self.assertEqual(diagnostic.span.column, 17)

    def test_tokenize_reports_eof_at_the_end(self) -> None:
        tokens = tokenize("")
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].kind, "eof")


if __name__ == "__main__":
    unittest.main()

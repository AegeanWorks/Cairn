//! A canonical text dump of the front end, used by `parity.py` to prove that the Rust compiler
//! accepts the same language as the Python reference: identical tokens, AST shapes, spans and
//! diagnostics. The format is deliberately simple so the Python oracle can reproduce it exactly.

use crate::diagnostics::Span;
use crate::lexer::{tokenize, Token};
use crate::parser::parse;
use crate::syntax::{Block, Expr, Program, Stmt};

fn span(span: Span) -> String {
    format!(
        "{}:{}:{}:{}:{}:{}",
        span.start, span.end, span.line, span.column, span.end_line, span.end_column
    )
}

fn opt(value: Option<String>) -> String {
    value.unwrap_or_else(|| "NONE".to_string())
}

fn expr(node: &Expr) -> String {
    match node {
        Expr::IntLiteral { span: at, value } => format!("INT[{}][{}]", value, span(*at)),
        Expr::StrLiteral { span: at, text, .. } => {
            format!("STR[{}][{}]", crate::diagnostics::json_string(text), span(*at))
        }
        Expr::Name { span: at, name, slot } => {
            format!("NAME[{}][{}][{}]", crate::diagnostics::json_string(name), slot, span(*at))
        }
        Expr::Unary { span: at, op, operand } => format!(
            "UNARY[{}][{}]({})",
            crate::diagnostics::json_string(op),
            span(*at),
            expr(operand)
        ),
        Expr::Binary { span: at, op, left, right } => format!(
            "BINARY[{}][{}]({},{})",
            crate::diagnostics::json_string(op),
            span(*at),
            expr(left),
            expr(right)
        ),
        Expr::Parens { span: at, inner } => format!("PARENS[{}]({})", span(*at), expr(inner)),
        Expr::Call { span: at, name, arguments } => format!(
            "CALL[{}][{}]({})",
            crate::diagnostics::json_string(name),
            span(*at),
            arguments.iter().map(expr).collect::<Vec<_>>().join(",")
        ),
        Expr::ArrayLiteral { span: at, elements } => format!(
            "ARRAY[{}]({})",
            span(*at),
            elements.iter().map(expr).collect::<Vec<_>>().join(",")
        ),
        Expr::Index { span: at, operand, index, slot, size } => format!(
            "INDEX[{},{}][{}]({},{})",
            slot,
            size,
            span(*at),
            expr(operand),
            expr(index)
        ),
    }
}

fn block(node: &Block) -> String {
    format!(
        "BLOCK[{}]({})",
        span(node.span),
        node.statements.iter().map(stmt).collect::<Vec<_>>().join("|")
    )
}

fn stmt(node: &Stmt) -> String {
    match node {
        Stmt::Block(inner) => block(inner),
        Stmt::Let { span: at, name, mutable, type_name, value, slot, array_size } => format!(
            "LET[{},{},{},{},{}][{}]({})",
            crate::diagnostics::json_string(name),
            mutable,
            crate::diagnostics::json_string(type_name),
            slot,
            opt(array_size.map(|size| size.to_string())),
            span(*at),
            expr(value)
        ),
        Stmt::Assign { span: at, name, value, slot, index, temp_slot, array_size } => format!(
            "ASSIGN[{},{},{},{}][{}]({},{})",
            crate::diagnostics::json_string(name),
            slot,
            opt(temp_slot.map(|slot| slot.to_string())),
            opt(array_size.map(|size| size.to_string())),
            span(*at),
            opt(index.as_ref().map(expr)),
            expr(value)
        ),
        Stmt::If { span: at, condition, then_block, else_block } => format!(
            "IF[{}]({},{},{})",
            span(*at),
            expr(condition),
            block(then_block),
            opt(else_block.as_ref().map(block))
        ),
        Stmt::While { span: at, condition, body, countdown } => format!(
            "WHILE[{}][{}]({},{})",
            countdown,
            span(*at),
            expr(condition),
            block(body)
        ),
        Stmt::For { span: at, name, start, end, inclusive, body, slot } => format!(
            "FOR[{},{},{}][{}]({},{},{})",
            crate::diagnostics::json_string(name),
            inclusive,
            slot,
            span(*at),
            expr(start),
            expr(end),
            block(body)
        ),
        Stmt::Return { span: at, value } => format!("RETURN[{}]({})", span(*at), expr(value)),
        Stmt::Break { span: at } => format!("BREAK[{}]", span(*at)),
        Stmt::Continue { span: at } => format!("CONTINUE[{}]", span(*at)),
        Stmt::ExprStmt { span: at, expression } => {
            format!("EXPRSTMT[{}]({})", span(*at), expr(expression))
        }
    }
}

fn program_dump(node: &Program) -> String {
    format!(
        "PROGRAM[{}]({})",
        span(node.span),
        node.functions
            .iter()
            .map(|function| format!(
                "FN[{},{},{}][{}](PARAMS({}),BODY({}))",
                crate::diagnostics::json_string(&function.name),
                crate::diagnostics::json_string(&function.return_type),
                function.slots,
                span(function.span),
                function
                    .parameters
                    .iter()
                    .map(|parameter| format!(
                        "PARAM[{},{},{}][{}]",
                        crate::diagnostics::json_string(&parameter.name),
                        crate::diagnostics::json_string(&parameter.type_name),
                        parameter.slot,
                        span(parameter.span)
                    ))
                    .collect::<Vec<_>>()
                    .join("|"),
                block(&function.body)
            ))
            .collect::<Vec<_>>()
            .join("|")
    )
}

/// Tokens of one source text, one `TOK[...]` line per token including the final `eof`.
pub fn token_dump(source: &str, path: &str) -> Result<String, crate::diagnostics::DiagnosticError> {
    let tokens: Vec<Token> = tokenize(source, path)?;
    Ok(tokens
        .iter()
        .map(|token| {
            format!(
                "TOK[{},{},{},{}]",
                crate::diagnostics::json_string(token.kind),
                crate::diagnostics::json_string(&token.text),
                token.value,
                span(token.span),
            )
        })
        .collect::<Vec<_>>()
        .join("\n"))
}

/// The canonical front-end dump of one source text: tokens, then the AST or every diagnostic.
pub fn dump_source(source: &str, path: &str) -> String {
    let mut lines = Vec::new();
    match token_dump(source, path) {
        Ok(tokens) => lines.push(tokens),
        Err(error) => {
            for diagnostic in &error.diagnostics {
                lines.push(format!(
                    "ERROR[{},{},{},{}]",
                    crate::diagnostics::json_string(&diagnostic.code),
                    crate::diagnostics::json_string(&diagnostic.message),
                    crate::diagnostics::json_string(&diagnostic.fix),
                    span(diagnostic.span)
                ));
            }
            return lines.join("\n") + "\n";
        }
    }
    match parse(source, path) {
        Ok(program) => {
            lines.push(program_dump(&program));
            let mut analysed = program.clone();
            match crate::sema::analyze(&mut analysed, path) {
                Ok(()) => lines.push(format!("SEMA_OK({})", program_dump(&analysed))),
                Err(error) => {
                    for diagnostic in &error.diagnostics {
                        lines.push(format!(
                            "ERROR[{},{},{},{}]",
                            crate::diagnostics::json_string(&diagnostic.code),
                            crate::diagnostics::json_string(&diagnostic.message),
                            crate::diagnostics::json_string(&diagnostic.fix),
                            span(diagnostic.span)
                        ));
                    }
                }
            }
        }
        Err(error) => {
            for diagnostic in &error.diagnostics {
                lines.push(format!(
                    "ERROR[{},{},{},{}]",
                    crate::diagnostics::json_string(&diagnostic.code),
                    crate::diagnostics::json_string(&diagnostic.message),
                    crate::diagnostics::json_string(&diagnostic.fix),
                    span(diagnostic.span)
                ));
            }
        }
    }
    lines.join("\n") + "\n"
}

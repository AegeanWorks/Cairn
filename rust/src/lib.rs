//! A Rust rewrite of the Cairn compiler.
//!
//! The Python compiler under `../cairn/` is the reference oracle. This crate mirrors its modules
//! bottom-up so every stage can be checked against the same contract: `codes`, `diagnostics`,
//! `values` and `version` are leaves; `syntax`, `lexer` and `parser` build on them. Later stages
//! (`sema`, `machine`, `codegen`, `optimize`, `cli`) are added in their own tasks.

pub mod codegen;
pub mod codes;
pub mod diagnostics;
pub mod dump;
pub mod generator;
pub mod image;
pub mod lexer;
pub mod listing;
pub mod machine;
pub mod optimize;
pub mod pe;
pub mod pipeline;
pub mod parser;
pub mod runtime;
pub mod sema;
pub mod spec;
pub mod syntax;
pub mod values;
pub mod verify;
pub mod version;

//! Build pipeline: source text to executable bytes.
//!
//! The stages are separate and each has one entry point, so a failing stage names itself: lex and
//! parse, analyse, lower, generate, link. The write step is the I/O edge and is atomic.

use std::path::{Path, PathBuf};

use crate::diagnostics::DiagnosticError;
use crate::generator::CodeGenerator;
use crate::image::Artifact;
use crate::optimize::{self, Optimizer};
use crate::parser::parse;
use crate::pe::link;
use crate::pipeline::Pipeline;
use crate::sema::analyze;
use crate::syntax::Program;

/// One successful build: the parsed program, the linked artifact and the image bytes.
pub struct Build {
    pub program: Program,
    pub image: Vec<u8>,
    pub artifact: Artifact,
    pub folded: usize,
}

/// Run every stage over one source text and return the executable image.
pub fn compile_source(source: &str, path: &str, optimise: bool) -> Result<Build, DiagnosticError> {
    let mut program = parse(source, path)?;
    analyze(&mut program, path)?;
    program = optimize::lower_for_loops(&program);
    let folded = if optimise {
        let mut optimiser = Optimizer::new();
        let mut folded_program = program.clone();
        folded_program = optimiser.fold_program(&folded_program);
        optimize::apply_pure_inlining(&mut folded_program);
        let _ = optimiser.fold_program(&folded_program);
        optimiser.folded
    } else {
        0
    };
    if optimise {
        let _ = Pipeline::new().run(&mut program, path);
    }
    let artifact = CodeGenerator::new().generate(&program);
    let image = link(&artifact).map_err(|message| DiagnosticError {
        diagnostics: vec![crate::diagnostics::Diagnostic::new(
            "V0008", &message, "Fix the artifact layout.",
            crate::diagnostics::Span::point(0, 1, 1), path,
        )],
    })?;
    Ok(Build { program, image, artifact, folded })
}

/// The executable path: `-o` when given, otherwise the source stem plus `.exe`.
pub fn output_path(source: &Path, requested: Option<&Path>) -> PathBuf {
    requested.map_or_else(|| source.with_extension("exe"), Path::to_path_buf)
}

/// A sibling path no other thread or process is writing.
fn temporary_path(destination: &Path) -> PathBuf {
    let name = format!(
        "{}.tmp_{}_{:?}",
        destination.file_name().unwrap_or_default().to_string_lossy(),
        std::process::id(),
        std::thread::current().id(),
    );
    destination.with_file_name(name)
}

/// Write the image to a temporary sibling, then rename it into place in one step.
pub fn write_executable(destination: &Path, image: &[u8]) -> std::io::Result<()> {
    if let Some(parent) = destination.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let temporary = temporary_path(destination);
    match std::fs::write(&temporary, image) {
        Ok(()) => {
            let mut last = None;
            for _ in 0..3 {
                match std::fs::rename(&temporary, destination) {
                    Ok(()) => return Ok(()),
                    Err(error) => {
                        last = Some(error);
                        std::thread::sleep(std::time::Duration::from_millis(50));
                    }
                }
            }
            let _ = std::fs::remove_file(&temporary);
            Err(last.unwrap_or_else(|| {
                std::io::Error::new(std::io::ErrorKind::Other, "rename failed")
            }))
        }
        Err(error) => {
            let _ = std::fs::remove_file(&temporary);
            Err(error)
        }
    }
}

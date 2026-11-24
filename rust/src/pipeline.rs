//! Explicit state machine for the compiler pipeline (architectural directive).
//!
//! The functional core is the pure stage transition (`Stage::next`); the imperative shell lives
//! in `main.rs`, which reads files, applies each stage to the AST and prints the result. No stage
//! performs I/O, so the same machine can drive a CLI, a test harness or a future library client.

use crate::optimize::{self, Optimizer};
use crate::syntax::Program;

/// One distinct compiler stage, in execution order.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Stage {
    Parse,
    Analyze,
    LowerFor,
    Inline,
    Fold,
    Algebraic,
    FoldAgain,
    CopyProp,
    Unroll,
    Countdown,
    OddPair,
    CopyPropAgain,
    Induction,
    Rotate,
    Generate,
    Done,
}

impl Stage {
    /// The pure transition: the next stage, or `Done` when the pipeline is finished.
    pub fn next(self) -> Self {
        match self {
            Self::Parse => Self::Analyze,
            Self::Analyze => Self::LowerFor,
            Self::LowerFor => Self::Inline,
            Self::Inline => Self::Fold,
            Self::Fold => Self::Algebraic,
            Self::Algebraic => Self::FoldAgain,
            Self::FoldAgain => Self::CopyProp,
            Self::CopyProp => Self::Unroll,
            Self::Unroll => Self::Countdown,
            Self::Countdown => Self::OddPair,
            Self::OddPair => Self::CopyPropAgain,
            Self::CopyPropAgain => Self::Induction,
            Self::Induction => Self::Rotate,
            Self::Rotate => Self::Generate,
            Self::Generate | Self::Done => Self::Done,
        }
    }

    pub fn is_terminal(self) -> bool {
        self == Self::Done
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Parse => "parse",
            Self::Analyze => "analyze",
            Self::LowerFor => "lower-for",
            Self::Inline => "inline",
            Self::Fold => "fold",
            Self::Algebraic => "algebraic",
            Self::FoldAgain => "fold-again",
            Self::CopyProp => "copy-prop",
            Self::Unroll => "unroll",
            Self::Countdown => "countdown",
            Self::OddPair => "odd-pair",
            Self::CopyPropAgain => "copy-prop-again",
            Self::Induction => "induction",
            Self::Rotate => "rotate",
            Self::Generate => "generate",
            Self::Done => "done",
        }
    }

    /// Apply this stage to the AST. Pure with respect to I/O; mutates only the program.
    pub fn apply(&self, program: &mut Program, path: &str) {
        match self {
            Self::Parse | Self::Analyze | Self::Done => {}
            Self::LowerFor => *program = optimize::lower_for_loops(program),
            Self::Inline => {
                optimize::apply_pure_inlining(program);
            }
            Self::Fold | Self::FoldAgain => {
                *program = Optimizer::new().fold_program(program);
            }
            Self::Algebraic => {
                optimize::apply_algebraic_simplification(program);
            }
            Self::CopyProp | Self::CopyPropAgain => {
                optimize::apply_copy_propagation(program);
            }
            Self::Unroll => {
                optimize::apply_constant_loop_unrolling(program);
            }
            Self::Countdown => {
                optimize::apply_countdown_latches(program);
            }
            Self::OddPair => {
                let first = optimize::apply_odd_pair_fusion(program);
                if first > 0 {
                    *program = Optimizer::new().fold_program(program);
                    optimize::apply_odd_pair_fusion(program);
                }
            }
            Self::Induction => {
                optimize::apply_induction_strength_reduction(program);
            }
            Self::Rotate => {
                optimize::apply_bottom_test_rotation(program);
            }
            Self::Generate => {
                let _ = path;
            }
        }
    }
}

/// The machine itself: a single stage cursor with a pure transition.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Pipeline {
    pub stage: Stage,
}

impl Pipeline {
    pub fn new() -> Self {
        Self { stage: Stage::Parse }
    }

    /// Pure transition, leaving the current state unchanged.
    pub fn advance(self) -> Self {
        Self { stage: self.stage.next() }
    }

    pub fn run(mut self, program: &mut Program, path: &str) -> Self {
        while !self.stage.is_terminal() {
            self.stage.apply(program, path);
            self = self.advance();
        }
        self
    }
}

impl Default for Pipeline {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stage_transitions_are_total_and_terminal() {
        let mut stage = Stage::Parse;
        let mut count = 0;
        while !stage.is_terminal() {
            stage = stage.next();
            count += 1;
            assert!(count <= 16);
        }
        assert_eq!(stage, Stage::Done);
        assert_eq!(Stage::Done.next(), Stage::Done);
    }

    #[test]
    fn the_pipeline_advances_without_side_effects() {
        let machine = Pipeline::new();
        let next = machine.advance();
        assert_eq!(machine.stage, Stage::Parse);
        assert_eq!(next.stage, Stage::Analyze);
    }
}

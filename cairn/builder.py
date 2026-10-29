"""Build pipeline: source text to executable bytes.

The stages are separate and each has one entry point, so a failing stage names itself: lex and
parse, analyse, generate, link. Writing the executable is atomic, so an interrupted build never
leaves a half-written `.exe` behind.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cairn import codegen, optimize, parser, pe, sema, syntax
from cairn.image import Artifact


@dataclass(frozen=True)
class Build:
    """One successful build: the parsed program, the linked artifact and the image bytes."""

    program: syntax.Program
    image: bytes
    artifact: Artifact
    folded: int = 0


def compile_source(source: str, path: str = "<source>", optimise: bool = True) -> Build:
    """Run every stage over one source text and return the executable image."""
    program = sema.analyze(parser.parse(source, path), path)
    folded = 0
    if optimise:
        folder = optimize.Optimizer()
        program = folder.run(program)
        folded = folder.folded
    artifact = codegen.generate(program)
    return Build(program=program, image=pe.link(artifact), artifact=artifact, folded=folded)


def compile_file(path: Path, optimise: bool = True) -> Build:
    """Read a `.crn` file as UTF-8 and compile it."""
    return compile_source(path.read_text(encoding="utf-8-sig"), str(path), optimise)


def output_path(source: Path, requested: Optional[Path] = None) -> Path:
    """The executable path: `-o` when given, otherwise the source stem plus `.exe`."""
    return requested if requested is not None else source.with_suffix(".exe")


def write_executable(destination: Path, image: bytes) -> None:
    """Write the image to a temporary sibling, then rename it into place in one step."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.tmp_{os.getpid()}")
    try:
        temporary.write_bytes(image)
        os.replace(temporary, destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


__all__ = ["Build", "compile_file", "compile_source", "output_path", "write_executable"]

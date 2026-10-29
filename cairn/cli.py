"""Command line: build, check and run Cairn programs.

Exit codes are part of the contract: 0 success, 1 the source did not compile (or the program
failed to start), 2 the command line or the input path was wrong, and `run` forwards the
program's own exit code. `--json` prints one object with the same diagnostics the human form
renders, which is what an agent should read.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from cairn import builder, capabilities, listing, verify
from cairn.diagnostics import DiagnosticError
from cairn.version import LANGUAGE_VERSION, VERSION

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
DEFAULT_RUN_TIMEOUT = 60.0


def _parser() -> argparse.ArgumentParser:
    """The whole command surface, declared in one place."""
    parser = argparse.ArgumentParser(
        prog="cairn",
        description="Cairn: an agent-first language with its own x86-64 backend.",
        epilog="Exit codes: 0 ok, 1 compile error or program failure, 2 usage error.")
    parser.add_argument("--version", action="version",
                        version=f"cairn {VERSION} ({LANGUAGE_VERSION})")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="compile a .crn file to a Windows .exe")
    build.add_argument("source", type=Path, help="the .crn file to compile")
    build.add_argument("-o", "--output", type=Path, default=None,
                       help="executable path (default: the source stem plus .exe)")
    build.add_argument("--json", action="store_true", help="print one JSON result object")
    _add_optimise_flag(build)

    check = commands.add_parser("check", help="parse and analyse without writing an executable")
    check.add_argument("source", type=Path, help="the .crn file to check")
    check.add_argument("--json", action="store_true", help="print one JSON result object")
    _add_optimise_flag(check)

    run = commands.add_parser("run", help="build to a temporary file and run it")
    run.add_argument("source", type=Path, help="the .crn file to run")
    _add_optimise_flag(run)
    run.add_argument("--timeout", type=float, default=DEFAULT_RUN_TIMEOUT,
                     help="seconds before the program is stopped (default: 60)")

    verify_command = commands.add_parser(
        "verify", help="check a built .exe or a .crn compiled in memory against the artifact rules")
    verify_command.add_argument("target", type=Path, help="the .exe or .crn file to verify")
    verify_command.add_argument("--json", action="store_true", help="print one JSON result object")
    _add_optimise_flag(verify_command)

    listing_command = commands.add_parser(
        "listing", help="print the emitted machine code by label without writing a file")
    listing_command.add_argument("source", type=Path, help="the .crn file to list")
    listing_command.add_argument("--json", action="store_true",
                                 help="print one JSON result object with the listing text")
    _add_optimise_flag(listing_command)

    surface = commands.add_parser(
        "capabilities", help="print the accepted surface and every diagnostic code")
    surface.add_argument("--json", action="store_true", help="print the machine-readable object")
    return parser


def _add_optimise_flag(parser: argparse.ArgumentParser) -> None:
    """Every command shares one switch for the folding pass, so builds can be compared."""
    parser.add_argument("--no-optimize", action="store_true",
                        help="skip constant folding, to compare against the unoptimised build")


def _read_source(path: Path) -> Tuple[Optional[str], int]:
    """The source text, or an explained usage failure."""
    try:
        return path.read_text(encoding="utf-8-sig"), EXIT_OK
    except OSError as error:
        print(f"[INVALID INPUT] cannot read {path}: {error.strerror or error}", file=sys.stderr)
        print("Why: the compiler needs a readable UTF-8 .crn file.", file=sys.stderr)
        print("Fix: pass an existing file path, for example 'cairn check examples/hello.crn'.",
              file=sys.stderr)
        return None, EXIT_USAGE


def _report_failure(error: DiagnosticError, source: str, json_mode: bool) -> int:
    """Print every diagnostic, as JSON or as positioned text."""
    if json_mode:
        print(json.dumps({"ok": False,
                          "diagnostics": [item.to_dict() for item in error.diagnostics]},
                         indent=2))
    else:
        print(error.render(source), file=sys.stderr)
    return EXIT_FAILURE


def _build_command(arguments: argparse.Namespace) -> int:
    """`build`: compile, write the executable, report its size."""
    source, status = _read_source(arguments.source)
    if source is None:
        return status
    try:
        result = builder.compile_source(source, str(arguments.source), not arguments.no_optimize)
    except DiagnosticError as error:
        return _report_failure(error, source, arguments.json)
    destination = builder.output_path(arguments.source, arguments.output)
    try:
        builder.write_executable(destination, result.image)
    except OSError as error:
        print(f"[OUTPUT ERROR] cannot write {destination}: {error}", file=sys.stderr)
        print("Why: the executable needs a writable destination folder.", file=sys.stderr)
        print("Fix: pass -o with a path inside a folder that exists and is writable.",
              file=sys.stderr)
        return EXIT_USAGE
    if arguments.json:
        print(json.dumps({"ok": True, "path": str(arguments.source),
                          "output": str(destination), "image_bytes": len(result.image),
                          "functions": len(result.program.functions), "folded": result.folded, "diagnostics": []},
                         indent=2))
    else:
        print(f"ok: {arguments.source} -> {destination} ({len(result.image)} bytes)")
    return EXIT_OK


def _check_command(arguments: argparse.Namespace) -> int:
    """`check`: compile in memory and report, writing nothing."""
    source, status = _read_source(arguments.source)
    if source is None:
        return status
    try:
        result = builder.compile_source(source, str(arguments.source), not arguments.no_optimize)
    except DiagnosticError as error:
        return _report_failure(error, source, arguments.json)
    if arguments.json:
        print(json.dumps({"ok": True, "path": str(arguments.source),
                          "functions": len(result.program.functions), "folded": result.folded,
                          "image_bytes": len(result.image), "diagnostics": []}, indent=2))
    else:
        print(f"ok: {arguments.source} ({len(result.program.functions)} function(s), "
              f"{len(result.image)} bytes)")
    return EXIT_OK


def _run_command(arguments: argparse.Namespace) -> int:
    """`run`: compile into a temporary folder, execute, and forward the exit code."""
    source, status = _read_source(arguments.source)
    if source is None:
        return status
    try:
        result = builder.compile_source(source, str(arguments.source), not arguments.no_optimize)
    except DiagnosticError as error:
        return _report_failure(error, source, False)
    with tempfile.TemporaryDirectory(prefix="cairn-run-") as folder:
        executable = Path(folder) / "program.exe"
        builder.write_executable(executable, result.image)
        return _execute(executable, arguments.timeout)


def _execute(executable: Path, timeout: float) -> int:
    """Run the produced executable with inherited streams and a hard time limit."""
    try:
        completed = subprocess.run([str(executable)], timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        print(f"[TIMEOUT] the program ran longer than {timeout:g} seconds", file=sys.stderr)
        print("Why: a Cairn program is expected to finish; an endless loop is a bug.",
              file=sys.stderr)
        print("Fix: correct the loop condition, or raise --timeout for a deliberate wait.",
              file=sys.stderr)
        return EXIT_FAILURE
    except OSError as error:
        print(f"[RUN ERROR] cannot start {executable}: {error}", file=sys.stderr)
        print("Why: the generated image could not be loaded by Windows.", file=sys.stderr)
        print("Fix: report the .crn file, because this is a compiler defect.", file=sys.stderr)
        return EXIT_FAILURE
    return completed.returncode


def _read_executable(path: Path) -> Tuple[Optional[bytes], int]:
    """The artifact bytes, or an explained usage failure."""
    try:
        return path.read_bytes(), EXIT_OK
    except OSError as error:
        print(f"[INVALID INPUT] cannot read {path}: {error.strerror or error}", file=sys.stderr)
        print("Why: verification needs a readable executable image.", file=sys.stderr)
        print("Fix: build one first with 'cairn build', then pass that path.",
              file=sys.stderr)
        return None, EXIT_USAGE


def _verify_command(arguments: argparse.Namespace) -> int:
    """`verify`: structural checks over a built image or over a source compiled in memory."""
    is_executable = arguments.target.suffix.lower() == ".exe"
    if is_executable:
        image, status = _read_executable(arguments.target)
        if image is None:
            return status
        kind = "executable"
    else:
        source, status = _read_source(arguments.target)
        if source is None:
            return status
        try:
            image = builder.compile_source(source, str(arguments.target),
                                           not arguments.no_optimize).image
        except DiagnosticError as error:
            return _report_failure(error, source, arguments.json)
        kind = "source"
    problems = verify.verify_image(image)
    if arguments.json:
        print(json.dumps({"ok": not problems, "path": str(arguments.target), "kind": kind,
                          "image_bytes": len(image),
                          "problems": [{"code": problem.code, "detail": problem.detail}
                                       for problem in problems],
                          "diagnostics": []}, indent=2))
    elif problems:
        for problem in problems:
            print(problem.render(), file=sys.stderr)
        print(f"failed: {arguments.target} breaks {len(problems)} artifact rule(s)",
              file=sys.stderr)
    else:
        print(f"ok: {arguments.target} verified ({len(image)} bytes)")
    return EXIT_FAILURE if problems else EXIT_OK


def _listing_command(arguments: argparse.Namespace) -> int:
    """`listing`: compile in memory and print the bytes of every label in `.text`."""
    source, status = _read_source(arguments.source)
    if source is None:
        return status
    try:
        result = builder.compile_source(source, str(arguments.source),
                                        not arguments.no_optimize)
    except DiagnosticError as error:
        return _report_failure(error, source, arguments.json)
    rendered = listing.render_listing(result.artifact)
    if arguments.json:
        print(json.dumps({"ok": True, "path": str(arguments.source),
                          "text_bytes": len(result.artifact.text),
                          "labels": sorted(result.artifact.text_labels),
                          "listing": rendered, "diagnostics": []}, indent=2))
    else:
        print(rendered)
    return EXIT_OK


def _capabilities_command(arguments: argparse.Namespace) -> int:
    """`capabilities`: the surface as JSON for an agent, or one screen of text for a human."""
    if arguments.json:
        print(json.dumps(capabilities.capabilities(), indent=2))
    else:
        print(capabilities.render_text())
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse the command line and dispatch one command."""
    arguments = _parser().parse_args(argv)
    handlers = {"build": _build_command, "check": _check_command, "run": _run_command,
                "verify": _verify_command, "listing": _listing_command,
                "capabilities": _capabilities_command}
    handler = handlers.get(arguments.command)
    if handler is None:  # pragma: no cover - argparse refuses unknown subcommands
        print(f"unknown command {arguments.command}", file=sys.stderr)
        return EXIT_USAGE
    return handler(arguments)


def _configure_streams() -> None:
    """Make source text and diagnostics survive a Windows pipe whatever the code page is."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            continue


if __name__ == "__main__":
    _configure_streams()
    sys.exit(main(sys.argv[1:]))


def command_line(argv: List[str]) -> int:
    """The entry point used by `python -m cairn` and the installed console script."""
    _configure_streams()
    return main(argv)


__all__ = ["EXIT_FAILURE", "EXIT_OK", "EXIT_USAGE", "command_line", "main"]



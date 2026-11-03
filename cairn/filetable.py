"""qwenFS toy for Cairn: a deterministic in-memory file table with integer handles.

Transfers the host-side shape of qwenFS without its storage or search:

- qwenFS circuit.py layers become one flat table here: files plus a directed
  link graph live in the same object, so every lookup is exact-match only.
- qwenFS merge.py widen becomes whole-table copy in the tests: mount is
  simulated by copying exact bytes from one table into another, never by
  merging partial state.
- qwenFS disk.py mount/page becomes scripted simulation in the tests: mount
  copies tables under a base offset, page chunks exact bytes and reassembles
  them, proving the table itself never does partial I/O.
- Limits transfer as constants: at most 32 files, exact integer-handle paths
  for the i64-only language, no glob or substring search, no partial
  read-modify-write (write replaces the whole value, read returns the whole
  value).

The emitter half mirrors runtime.emit_runtime on purpose: rodata.add plus
lea_rip, no new PE section, reusing Artifact rodata and fixups. It is never
invoked by the default build path (builder.compile_source and
codegen.CodeGenerator.generate do not import this module), so existing
images and the V0007/V0008/V0010 verifier invariants are untouched.
"""

from typing import Dict, List, Set

from cairn.optimize import INT64_MAX, INT64_MIN

MAX_FILES = 32

FILETABLE_MANIFEST_LABEL = "cairn.filetable.manifest"
FILETABLE_HELPER_LABEL = "cairn.filetable.describe"
FILETABLE_MAGIC = b"FTBL"


def _check_handle(handle: int) -> int:
    """Validate one integer-handle path for the i64-only language."""
    if isinstance(handle, bool) or not isinstance(handle, int):
        raise TypeError(f"file handle must be an int i64 handle, got {type(handle).__name__}")
    if not INT64_MIN <= handle <= INT64_MAX:
        raise ValueError(f"file handle {handle} is outside i64 range")
    return handle


def _check_data(data: bytes) -> bytes:
    """Validate one file payload: whole bytes, never a partial view."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(f"file data must be bytes, got {type(data).__name__}")
    return bytes(data)


class FileTable:
    """Deterministic exact-match file table plus a directed link graph.

    Files map an integer handle (the only path form Cairn i64 can name) to
    whole bytes. Links are directed edges between existing files:
    list_links answers outgoing edges, list_backlinks answers incoming
    edges. Every listing is sorted, so two tables built by the same scripted
    session compare equal.
    """

    def __init__(self) -> None:
        self._files: Dict[int, bytes] = {}
        self._forward: Dict[int, Set[int]] = {}
        self._backward: Dict[int, Set[int]] = {}

    def __len__(self) -> int:
        """The number of files currently stored."""
        return len(self._files)

    def __contains__(self, handle: object) -> bool:
        """Whether an integer handle names a stored file."""
        return isinstance(handle, int) and not isinstance(handle, bool) and handle in self._files

    def write(self, handle: int, data: bytes) -> None:
        """Create or overwrite one file with its whole content.

        Overwrite is always allowed, even when the table is full, because it
        changes no file count. A new handle past MAX_FILES raises
        OverflowError, keeping the 32-file cap exact.
        """
        _check_handle(handle)
        payload = _check_data(data)
        if handle not in self._files and len(self._files) >= MAX_FILES:
            raise OverflowError(f"file table full: at most {MAX_FILES} files")
        is_new = handle not in self._files
        self._files[handle] = payload
        if is_new:
            self._forward.setdefault(handle, set())
            self._backward.setdefault(handle, set())

    def read(self, handle: int) -> bytes:
        """Return the whole content of one file, or raise KeyError when missing."""
        _check_handle(handle)
        try:
            return self._files[handle]
        except KeyError:
            raise KeyError(f"no such file: {handle}") from None

    def list(self) -> List[int]:
        """Every stored handle in sorted order (no search, exact enumeration)."""
        return sorted(self._files)

    def link(self, source: int, target: int) -> None:
        """Add one directed edge source -> target between two existing files.

        A duplicate edge raises ValueError so scripted sessions can prove
        exactness; a missing endpoint raises KeyError.
        """
        _check_handle(source)
        _check_handle(target)
        if source not in self._files:
            raise KeyError(f"no such file: {source}")
        if target not in self._files:
            raise KeyError(f"no such file: {target}")
        if target in self._forward[source]:
            raise ValueError(f"link {source} -> {target} already exists")
        self._forward[source].add(target)
        self._backward[target].add(source)

    def unlink_link(self, source: int, target: int) -> None:
        """Remove one directed edge, or raise KeyError when it is absent."""
        _check_handle(source)
        _check_handle(target)
        if source not in self._files:
            raise KeyError(f"no such file: {source}")
        if target not in self._files:
            raise KeyError(f"no such file: {target}")
        if target not in self._forward[source]:
            raise KeyError(f"no such link: {source} -> {target}")
        self._forward[source].remove(target)
        self._backward[target].remove(source)

    def unlink(self, handle: int) -> None:
        """Remove one file and every incident link edge."""
        _check_handle(handle)
        if handle not in self._files:
            raise KeyError(f"no such file: {handle}")
        for target in sorted(self._forward[handle]):
            self._backward[target].discard(handle)
        for source in sorted(self._backward[handle]):
            self._forward[source].discard(handle)
        del self._forward[handle]
        del self._backward[handle]
        del self._files[handle]

    def list_links(self, handle: int) -> List[int]:
        """Outgoing edges of one file in sorted order."""
        _check_handle(handle)
        if handle not in self._files:
            raise KeyError(f"no such file: {handle}")
        return sorted(self._forward[handle])

    def list_backlinks(self, handle: int) -> List[int]:
        """Incoming edges of one file in sorted order."""
        _check_handle(handle)
        if handle not in self._files:
            raise KeyError(f"no such file: {handle}")
        return sorted(self._backward[handle])


def emit_filetable(assembler, rodata) -> None:
    """Emit the additive filetable descriptor, mirroring runtime.emit_runtime.

    Adds one read-only manifest (magic plus the 32-file cap plus a zero file
    count; the live table stays host-side in Python) and one tiny helper
    that materialises its address with lea_rip and returns. No new PE
    section is created: the manifest reuses the shared rodata blob and the
    helper reuses the shared text fixups, so the existing section layout and
    the V0007, V0008 and V0010 verifier invariants keep holding. The default
    build path never calls this function.
    """
    from cairn.machine import Assembler as _Asm
    from cairn.image import ReadOnlyData as _Rodata

    if not isinstance(assembler, _Asm):
        raise TypeError("emit_filetable needs a machine.Assembler")
    if not isinstance(rodata, _Rodata):
        raise TypeError("emit_filetable needs an image.ReadOnlyData")
    if FILETABLE_MANIFEST_LABEL in rodata.labels:
        raise ValueError("emit_filetable ran twice: the manifest label is already bound")
    if FILETABLE_HELPER_LABEL in assembler.labels:
        raise ValueError("emit_filetable ran twice: the helper label is already bound")
    payload = FILETABLE_MAGIC + MAX_FILES.to_bytes(8, "little") + (0).to_bytes(8, "little")
    rodata.add(FILETABLE_MANIFEST_LABEL, payload)
    assembler.label(FILETABLE_HELPER_LABEL)
    assembler.lea_rip("rax", FILETABLE_MANIFEST_LABEL)
    assembler.ret()


__all__ = [
    "FILETABLE_HELPER_LABEL",
    "FILETABLE_MAGIC",
    "FILETABLE_MANIFEST_LABEL",
    "MAX_FILES",
    "FileTable",
    "emit_filetable",
]

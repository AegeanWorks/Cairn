"""Image pieces shared by the backend: read-only data and the linked artifact.

Keeping these two small dataclasses apart from the PE writer lets the code generator build an
image without knowing how sections are laid out on disk.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

IMPORT_DLL = "kernel32.dll"

ENTRY_LABEL = "cairn.entry"

IMPORTS = ("GetStdHandle", "WriteFile", "ReadFile", "GetCommandLineA", "ExitProcess")


def iat_label(function: str) -> str:
    """The linker name of one import address table slot."""
    return f"iat.{function}"


@dataclass
class ReadOnlyData:
    """A byte blob with named offsets, placed in the `.rdata` section."""

    data: bytearray = field(default_factory=bytearray)
    labels: Dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        """The current size in bytes."""
        return len(self.data)

    def label(self, name: str) -> None:
        """Bind `name` to the current offset."""
        if name in self.labels:
            raise ValueError(f"duplicate rodata label {name!r}: a label may be bound once")
        self.labels[name] = len(self.data)

    def add(self, name: str, payload: bytes) -> None:
        """Bind `name` and append `payload`."""
        self.label(name)
        self.data.extend(payload)

    def align(self, boundary: int) -> None:
        """Pad with zero bytes to the next multiple of `boundary`."""
        if boundary <= 0:
            raise ValueError(f"alignment boundary must be positive, got {boundary}")
        padding = (-len(self.data)) % boundary
        self.data.extend(b"\x00" * padding)


@dataclass
class WritableData:
    """A byte blob with named offsets, placed after the IAT in the writable `.data`.

    The PE loader seeds the section with the file's bytes, so state emitted as zeros
    starts zeroed in every process; the runtime keeps its argv cache and stdin buffer
    here because programs change them across calls.
    """

    data: bytearray = field(default_factory=bytearray)
    labels: Dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        """The current size in bytes."""
        return len(self.data)

    def label(self, name: str) -> None:
        """Bind `name` to the current offset."""
        if name in self.labels:
            raise ValueError(f"duplicate writable-data label {name!r}: a label may be bound once")
        self.labels[name] = len(self.data)

    def reserve(self, name: str, size: int) -> None:
        """Bind `name` to the current offset and append `size` zero bytes of static state."""
        if size <= 0:
            raise ValueError(f"reserved state must be positive, got {size}")
        self.label(name)
        self.data.extend(b"\x00" * size)

    def align(self, boundary: int) -> None:
        """Pad with zero bytes to the next multiple of `boundary`."""
        if boundary <= 0:
            raise ValueError(f"alignment boundary must be positive, got {boundary}")
        padding = (-len(self.data)) % boundary
        self.data.extend(b"\x00" * padding)


@dataclass
class Artifact:
    """Everything the PE writer needs: code, fixups, data and the entry label."""

    text: bytes
    text_labels: Dict[str, int]
    fixups: List[Tuple[int, str]]
    rodata: ReadOnlyData
    entry: str = ENTRY_LABEL
    imports: Tuple[str, ...] = IMPORTS
    writable: bytes = b""
    writable_labels: Dict[str, int] = field(default_factory=dict)


__all__ = ["Artifact", "ENTRY_LABEL", "IMPORT_DLL", "IMPORTS", "ReadOnlyData",
           "WritableData", "iat_label"]

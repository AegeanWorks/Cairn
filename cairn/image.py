"""Image pieces shared by the backend: read-only data and the linked artifact.

Keeping these two small dataclasses apart from the PE writer lets the code generator build an
image without knowing how sections are laid out on disk.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

IMPORTS = ("GetStdHandle", "WriteFile", "ExitProcess")
IMPORT_DLL = "kernel32.dll"

ENTRY_LABEL = "cairn.entry"


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
        self.labels[name] = len(self.data)

    def add(self, name: str, payload: bytes) -> None:
        """Bind `name` and append `payload`."""
        self.label(name)
        self.data.extend(payload)

    def align(self, boundary: int) -> None:
        """Pad with zero bytes to the next multiple of `boundary`."""
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


__all__ = ["Artifact", "ENTRY_LABEL", "IMPORT_DLL", "IMPORTS", "ReadOnlyData", "iat_label"]

"""Machine-code listing: the emitted bytes, grouped by the label that owns them.

This is the "almost machine language" view of a build: every function, every runtime helper and
the entry stub, with the exact bytes the PE writer will place in `.text`. Nothing is written and
nothing is executed, so an agent can inspect a build the way a human reads assembly.
"""

from typing import List

from cairn.image import Artifact

BYTES_PER_ROW = 16


def _rows(chunk: bytes, start: int) -> List[str]:
    """Hex rows with the offset of each row's first byte."""
    lines: List[str] = []
    for index in range(0, len(chunk), BYTES_PER_ROW):
        piece = chunk[index:index + BYTES_PER_ROW]
        hexed = " ".join(f"{byte:02x}" for byte in piece)
        lines.append(f"  {start + index:06x}  {hexed}")
    return lines


def render_listing(artifact: Artifact) -> str:
    """Every label with its offset and the bytes up to the next label."""
    ordered = sorted(artifact.text_labels.items(), key=lambda item: item[1])
    lines = [f"# .text, {len(artifact.text)} bytes, {len(ordered)} labels"]
    for position, (name, offset) in enumerate(ordered):
        end = ordered[position + 1][1] if position + 1 < len(ordered) else len(artifact.text)
        lines.append("")
        lines.append(f"{offset:06x} <{name}>  ({end - offset} bytes)")
        lines.extend(_rows(artifact.text[offset:end], offset))
    if artifact.rodata.data:
        lines.append("")
        lines.append(f"# .rdata, {len(artifact.rodata.data)} bytes")
        for name, offset in sorted(artifact.rodata.labels.items(), key=lambda item: item[1]):
            lines.append(f"{offset:06x} <{name}>")
    return "\n".join(lines)


__all__ = ["BYTES_PER_ROW", "render_listing"]

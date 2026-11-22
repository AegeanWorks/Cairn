"""Python oracle for the backend parity check.

It reads the same artifact spec as `cairn-rs pe` and prints the same canonical dump: linked image,
verification results and listing from the Python compiler.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn import listing, pe, verify  # noqa: E402
from cairn.image import Artifact, ReadOnlyData, WritableData  # noqa: E402


def load(path):
    artifact = Artifact(text=b"", text_labels={}, fixups=[], rodata=ReadOnlyData(), imports=[])
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if not fields:
            continue
        key, values = fields[0], fields[1:]
        if key == "TEXT":
            artifact.text = bytes.fromhex(values[0])
        elif key == "LABEL":
            artifact.text_labels[values[0]] = int(values[1])
        elif key == "FIXUP":
            artifact.fixups.append((int(values[0]), values[1]))
        elif key == "RODATA":
            artifact.rodata = ReadOnlyData(data=bytearray(bytes.fromhex(values[0])))
        elif key == "ROLABEL":
            artifact.rodata.labels[values[0]] = int(values[1])
        elif key == "WRITABLE":
            artifact.writable = bytes.fromhex(values[0])
        elif key == "WRLABEL":
            artifact.writable_labels[values[0]] = int(values[1])
        elif key == "ENTRY":
            artifact.entry = values[0]
        elif key == "IMPORT":
            artifact.imports.append(values[0])
        else:
            raise ValueError(f"unknown spec line {key!r}")
    return artifact


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: python rust/pe_dump.py <spec>", file=sys.stderr)
        return 2
    artifact = load(Path(arguments[0]))
    image = pe.link(artifact)
    problems = verify.verify_image(image)
    lines = [f"IMAGE {image.hex()}",
             "VERIFY " + ";".join(f"{p.code}|{p.detail}" for p in problems),
             "LISTING", listing.render_listing(artifact), "ENDLISTING"]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

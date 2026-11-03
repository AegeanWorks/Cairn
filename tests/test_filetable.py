"""Scripted exactness proofs for the qwenFS toy file table.

Covers write/overwrite/read/missing/list/link/duplicate/unlink, a dense
link graph, mount/page simulation done with whole-value copies only, the
32-file cap with integer-handle validation, and the additive emitter that
mirrors runtime.emit_runtime without touching the default build path.
"""

import unittest
from pathlib import Path

from cairn import parser, pe, sema
from cairn.codegen import CodeGenerator
from cairn.filetable import (
    FILETABLE_HELPER_LABEL,
    FILETABLE_MANIFEST_LABEL,
    MAX_FILES,
    FileTable,
    emit_filetable,
)
from cairn.image import Artifact
from cairn.machine import Assembler
from cairn.image import ReadOnlyData
from cairn.verify import verify_image

PACKAGE = Path(__file__).resolve().parent.parent / "cairn"
HELLO = "fn main() -> i64 { return 0; }"


def scripted_session() -> FileTable:
    """One deterministic session every exactness test replays."""
    table = FileTable()
    table.write(1, b"alpha")
    table.write(2, b"beta")
    table.write(1, b"alpha2")
    table.link(1, 2)
    return table


class ScriptedSessionTest(unittest.TestCase):
    def test_write_overwrite_read_missing_list(self) -> None:
        table = scripted_session()
        self.assertEqual(table.read(1), b"alpha2")
        self.assertEqual(table.read(2), b"beta")
        self.assertEqual(table.list(), [1, 2])
        with self.assertRaises(KeyError):
            table.read(99)

    def test_link_duplicate_unlink(self) -> None:
        table = scripted_session()
        self.assertEqual(table.list_links(1), [2])
        self.assertEqual(table.list_backlinks(2), [1])
        self.assertEqual(table.list_links(2), [])
        with self.assertRaises(ValueError):
            table.link(1, 2)
        with self.assertRaises(KeyError):
            table.link(1, 99)
        table.unlink_link(1, 2)
        self.assertEqual(table.list_links(1), [])
        self.assertEqual(table.list_backlinks(2), [])
        with self.assertRaises(KeyError):
            table.unlink_link(1, 2)
        table.link(2, 1)
        table.unlink(1)
        self.assertEqual(table.list(), [2])
        self.assertEqual(table.list_links(2), [])
        self.assertEqual(table.list_backlinks(2), [])
        with self.assertRaises(KeyError):
            table.read(1)

    def test_session_is_deterministic(self) -> None:
        first, second = scripted_session(), scripted_session()
        self.assertEqual(first.list(), second.list())
        self.assertEqual(first.read(1), second.read(1))
        self.assertEqual(first.list_links(1), second.list_links(1))
        self.assertEqual(first.list_backlinks(2), second.list_backlinks(2))


class DenseGraphTest(unittest.TestCase):
    def test_dense_graph_links_and_backlinks_stay_exact(self) -> None:
        table = FileTable()
        handles = list(range(8))
        for handle in handles:
            table.write(handle, bytes([handle]))
        for source in handles:
            for target in handles:
                if source != target:
                    table.link(source, target)
        for handle in handles:
            self.assertEqual(len(table.list_links(handle)), len(handles) - 1)
            self.assertEqual(len(table.list_backlinks(handle)), len(handles) - 1)
            self.assertNotIn(handle, table.list_links(handle))
        table.unlink(0)
        self.assertEqual(table.list(), handles[1:])
        for handle in handles[1:]:
            self.assertNotIn(0, table.list_links(handle))
            self.assertNotIn(0, table.list_backlinks(handle))
            self.assertEqual(len(table.list_links(handle)), len(handles) - 2)


class MountPageSimulationTest(unittest.TestCase):
    def test_mount_copies_whole_values_under_a_base_offset(self) -> None:
        disk_a = FileTable()
        disk_a.write(1, b"one")
        disk_a.write(2, b"two")
        disk_a.link(1, 2)
        mounted = FileTable()
        base = 1000
        for handle in disk_a.list():
            mounted.write(base + handle, disk_a.read(handle))
        for handle in disk_a.list():
            self.assertEqual(mounted.read(base + handle), disk_a.read(handle))
        self.assertEqual(mounted.list(), [1001, 1002])
        # Links do not cross the mount boundary implicitly.
        self.assertEqual(mounted.list_links(1001), [])

    def test_page_chunks_reassemble_to_the_exact_value(self) -> None:
        table = FileTable()
        payload = b"abcdefghij0123456789"
        table.write(7, payload)
        page_size = 4
        raw = table.read(7)
        pages = [raw[index:index + page_size] for index in range(0, len(raw), page_size)]
        self.assertTrue(all(len(page) <= page_size for page in pages))
        self.assertEqual(b"".join(pages), payload)
        # No partial write exists: the only way to change bytes is whole overwrite.
        table.write(7, b"".join(pages))
        self.assertEqual(table.read(7), payload)


class CapAndHandlesTest(unittest.TestCase):
    def test_32_file_cap_with_overwrite_allowed_when_full(self) -> None:
        table = FileTable()
        for handle in range(MAX_FILES):
            table.write(handle, bytes([handle % 256]))
        self.assertEqual(len(table), MAX_FILES)
        with self.assertRaises(OverflowError):
            table.write(MAX_FILES, b"one too many")
        table.write(0, b"overwrite still works when full")
        self.assertEqual(table.read(0), b"overwrite still works when full")
        self.assertEqual(len(table), MAX_FILES)

    def test_integer_handles_are_i64_only(self) -> None:
        table = FileTable()
        with self.assertRaises(TypeError):
            table.write("1", b"x")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            table.write(True, b"x")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            table.write(1, "x")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            table.write(2 ** 63, b"x")
        with self.assertRaises(ValueError):
            table.write(-(2 ** 63) - 1, b"x")
        # Negative i64 handles are legal paths.
        table.write(-5, b"neg")
        self.assertEqual(table.read(-5), b"neg")


class EmitAdditiveTest(unittest.TestCase):
    def test_emit_uses_rodata_add_and_lea_rip_with_no_new_section(self) -> None:
        assembler = Assembler()
        rodata = ReadOnlyData()
        emit_filetable(assembler, rodata)
        self.assertIn(FILETABLE_MANIFEST_LABEL, rodata.labels)
        self.assertIn(FILETABLE_HELPER_LABEL, assembler.labels)
        targets = [target for _, target in assembler.fixups]
        self.assertIn(FILETABLE_MANIFEST_LABEL, targets)
        artifact = Artifact(text=bytes(assembler.code), text_labels=dict(assembler.labels),
                            fixups=list(assembler.fixups), rodata=rodata,
                            entry=FILETABLE_HELPER_LABEL)
        image = pe.link(artifact)
        self.assertEqual(verify_image(image), [])

    def test_additive_emit_on_a_real_build_still_verifies(self) -> None:
        program = sema.analyze(parser.parse(HELLO))
        generator = CodeGenerator()
        generator.generate(program)
        emit_filetable(generator.assembler, generator.rodata)
        artifact = Artifact(text=bytes(generator.assembler.code),
                            text_labels=dict(generator.assembler.labels),
                            fixups=list(generator.assembler.fixups),
                            rodata=generator.rodata)
        self.assertEqual(verify_image(pe.link(artifact)), [])

    def test_default_build_path_never_invokes_the_filetable(self) -> None:
        for name in ("builder.py", "codegen.py"):
            source = (PACKAGE / name).read_text(encoding="utf-8")
            self.assertNotIn("filetable", source.lower())

    def test_second_emit_is_rejected_instead_of_orphaning_bytes(self) -> None:
        assembler = Assembler()
        rodata = ReadOnlyData()
        emit_filetable(assembler, rodata)
        with self.assertRaises(ValueError):
            emit_filetable(assembler, rodata)
        self.assertEqual(len(rodata), 20)

    def test_emit_rejects_wrong_types_without_assert(self) -> None:
        with self.assertRaises(TypeError):
            emit_filetable(object(), ReadOnlyData())
        with self.assertRaises(TypeError):
            emit_filetable(Assembler(), object())


if __name__ == "__main__":
    unittest.main()

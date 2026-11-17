"""Tests for the runtime helpers' emitted shape and their static state."""
import unittest

from cairn import runtime
from cairn.image import ReadOnlyData, WritableData
from cairn.machine import Assembler


class StdinRefillTest(unittest.TestCase):
    """The stdin refill path pays one cached handle and 64 KiB per syscall.

    char_stream runs 1024 ReadFile calls and 1024 GetStdHandle calls over its
    4 MiB, with the handle fetch riding every refill although the handle is
    stable for the process lifetime. The refill caches the handle in static
    writable state (zero-initialized by the PE loader, fetched only when the
    cache is zero) and the buffer holds 65536 bytes instead of 4096, so a
    4 MiB stream refills 64 times and fetches the handle once.
    """

    def build_runtime(self) -> Assembler:
        assembler = Assembler()
        runtime.emit_runtime(assembler, ReadOnlyData(), WritableData())
        return assembler

    def test_the_stdin_buffer_holds_sixty_four_kib(self) -> None:
        self.assertEqual(runtime.STDIN_BUFFER_SIZE, 65536)

    def test_the_handle_cache_is_static_state(self) -> None:
        writable = WritableData()
        assembler = Assembler()
        runtime.emit_runtime(assembler, ReadOnlyData(), writable)
        self.assertIn(runtime.STDIN_HANDLE_LABEL, writable.labels,
                      "the handle cache is reserved writable state")
        self.assertGreaterEqual(len(writable.data), 65600,
                                "buffer plus cache ride the writable section")

    def test_the_refill_path_references_the_handle_cache(self) -> None:
        assembler = self.build_runtime()
        referenced = {target for _, target in assembler.fixups}
        self.assertIn(runtime.STDIN_HANDLE_LABEL, referenced,
                      "the refill loads the cached handle rip-relative")


if __name__ == "__main__":
    unittest.main()

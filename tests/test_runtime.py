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



class ReadCharPrologueTest(unittest.TestCase):
    """read_char's state loads stay lea+mov; the rip-direct rewrite loses here.

    A measured alternative: loading cursor/count as `mov r64,[rip+disp32]`
    (12 bytes versus 20) ran 1.22-1.42x SLOWER on char_stream over four
    interleaved same-window A/Bs (15.07->15.55, 15.73->19.16, 14.87->21.18,
    12.32->12.01 in the mixed window). The address-register round-trip
    pins the data addresses before the compare and the hot loop's fetch
    aligns better with it. The shape stays until a real workload shifts
    the balance; this test guards against an unmeasured 'cleanup'.
    """

    def test_the_prologue_keeps_its_address_registers(self) -> None:
        assembler = Assembler()
        runtime.emit_runtime(assembler, ReadOnlyData(), WritableData())
        start = assembler.labels[runtime.READ_CHAR_LABEL]
        prologue = assembler.code[start:start + 20]
        # lea r10,[rip+d32]; mov rax,[r10]; lea r11,[rip+d32]; mov rdx,[r11].
        self.assertEqual(prologue[0:3], bytes([0x4C, 0x8D, 0x15]),
                         "the cursor address is lea r10,[rip+disp32]")
        self.assertEqual(prologue[10:13], bytes([0x4C, 0x8D, 0x1D]),
                         "the count address is lea r11,[rip+disp32]")

if __name__ == "__main__":
    unittest.main()

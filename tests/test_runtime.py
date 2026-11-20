"""Tests for the runtime helpers' emitted shape and their static state."""
import unittest

from cairn import runtime
from cairn.image import ReadOnlyData, WritableData, iat_label
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



class StdoutBufferTest(unittest.TestCase):
    """The stdout buffer is static writable state the print family shares.

    The buffer, its cursor and the cached stdout handle ride the writable blob
    after the argv and stdin state, so the cold layout's offsets stay put.
    Every printer routes through out_append, and out_flush carries the cached
    handle plus the WriteFile that drains the buffer.
    """

    def test_the_stdout_buffer_holds_sixty_four_kib(self) -> None:
        self.assertEqual(runtime.STDOUT_BUFFER_SIZE, 65536)

    def test_the_stdout_state_rides_the_writable_blob(self) -> None:
        writable = WritableData()
        assembler = Assembler()
        runtime.emit_runtime(assembler, ReadOnlyData(), writable)
        for label in (runtime.STDOUT_BUFFER_LABEL, runtime.STDOUT_CURSOR_LABEL,
                      runtime.STDOUT_HANDLE_LABEL):
            self.assertIn(label, writable.labels,
                          "the stdout buffer, cursor and handle are reserved state")
        self.assertGreaterEqual(len(writable.data), 65536 * 2 + 8,
                                "buffer plus cursor plus handle is layered state")

    def test_every_printer_routes_through_out_append(self) -> None:
        assembler = Assembler()
        runtime.emit_runtime(assembler, ReadOnlyData(), WritableData())
        # Internal loop labels share each routine's range, so the boundaries are
        # the public `cairn.` labels only.
        bounds = sorted((offset for name, offset in assembler.labels.items()
                         if name.startswith("cairn.")))
        fixups = list(assembler.fixups)
        for label in (runtime.PRINT_LABEL, runtime.PRINT_CHAR_LABEL,
                      runtime.PRINT_HEX_LABEL, runtime.PRINT_STR_LABEL):
            start = assembler.labels[label]
            end = next(offset for offset in bounds if offset > start)
            inside = [target for offset, target in fixups if start <= offset < end]
            self.assertIn(runtime.OUT_APPEND_LABEL, inside,
                          f"{label} appends to the shared buffer")

    def test_out_flush_carries_a_cached_handle_and_the_write(self) -> None:
        assembler = Assembler()
        runtime.emit_runtime(assembler, ReadOnlyData(), WritableData())
        referenced = {target for _, target in assembler.fixups}
        self.assertIn(runtime.STDOUT_HANDLE_LABEL, referenced,
                      "the flush fetches or reuses the cached stdout handle")
        self.assertIn(iat_label("WriteFile"), referenced,
                      "the flush drains the buffer with WriteFile")

class PowerOfTwoPrinterTest(unittest.TestCase):
    """The fixed-width printers mask and shift instead of dividing by a power of two.

    A `div r64` is a slow, serially dependent instruction: print_hex peeled eleven
    nibbles with eleven of them, and print_char paid one just to isolate a byte.
    Masking the remainder and shifting the quotient keeps the exact unsigned
    semantics with three cheap ALU operations and no divide latency.
    """

    @staticmethod
    def helper_code(label: str) -> bytes:
        assembler = Assembler()
        runtime.emit_runtime(assembler, ReadOnlyData(), WritableData())
        bounds = sorted(offset for name, offset in assembler.labels.items()
                        if name.startswith("cairn."))
        start = assembler.labels[label]
        end = next(offset for offset in bounds if offset > start)
        return bytes(assembler.code[start:end])

    @staticmethod
    def has_divide(code: bytes) -> bool:
        return any(code[index] == 0xF7 and index + 1 < len(code)
                   and (code[index + 1] & 0x38) == 0x30
                   for index in range(len(code)))

    def test_print_hex_peels_a_nibble_with_and_and_shr(self) -> None:
        code = self.helper_code(runtime.PRINT_HEX_LABEL)
        self.assertIn(bytes([0x48, 0x83, 0xE2, 0x0F]), code,
                      "the nibble is the low four bits: and rdx, 15")
        self.assertIn(bytes([0x48, 0xC1, 0xE8, 0x04]), code,
                      "the next nibble is a logical shift: shr rax, 4")
        self.assertFalse(self.has_divide(code),
                         "print_hex must not peel nibbles with a serial div")

    def test_print_char_masks_the_low_byte(self) -> None:
        code = self.helper_code(runtime.PRINT_CHAR_LABEL)
        self.assertIn(bytes([0x48, 0x81, 0xE2, 0xFF, 0, 0, 0]), code,
                      "the byte is the low eight bits: and rdx, 255")
        self.assertFalse(self.has_divide(code),
                         "print_char must not divide just to isolate a byte")


if __name__ == "__main__":
    unittest.main()

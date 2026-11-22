//! Runtime emitter tests: every helper label and the reserved writable state.

use cairn_rs::runtime;

#[test]
fn every_runtime_label_is_emitted() {
    let (asm, _, _) = runtime::emit_all();
    for label in [
        runtime::PRINT_LABEL,
        runtime::PRINT_CHAR_LABEL,
        runtime::PRINT_HEX_LABEL,
        runtime::PRINT_HEX_LINE_LABEL,
        runtime::POPCOUNT_LABEL,
        runtime::DIV_ZERO_LABEL,
        runtime::ARGC_LABEL,
        runtime::ARG_BYTE_LABEL,
        runtime::READ_CHAR_LABEL,
        runtime::PRINT_STR_LABEL,
        runtime::STR_LEN_LABEL,
        runtime::STR_BYTE_LABEL,
        runtime::OUT_APPEND_LABEL,
        runtime::OUT_FLUSH_LABEL,
        runtime::OUT_FLUSH_WORK_LABEL,
    ] {
        assert!(asm.labels.contains_key(label), "missing {}", label);
    }
}

#[test]
fn the_runtime_reserves_all_static_state() {
    let (_, rodata, writable) = runtime::emit_all();
    assert_eq!(rodata.data, b"\ncairn: runtime error: divide by zero\n");
    assert_eq!(writable.data.len(),
               runtime::ARGV_CACHE_SIZE + runtime::ARGV_ARENA_SIZE as usize
                   + runtime::STDIN_BUFFER_SIZE as usize + 8 + 8 + 8 + 8 + 8
                   + runtime::STDOUT_BUFFER_SIZE as usize + 8 + 8);
    for label in [runtime::ARGV_CACHE_LABEL, runtime::ARGV_ARENA_LABEL,
                  runtime::STDIN_BUFFER_LABEL, runtime::STDOUT_BUFFER_LABEL] {
        assert!(writable.labels.contains_key(label), "missing {}", label);
    }
}

#[test]
fn every_fixup_names_a_bound_label() {
    let (asm, rodata, writable) = runtime::emit_all();
    for (_, target) in &asm.fixups {
        assert!(asm.labels.contains_key(target) || rodata.labels.contains_key(target)
                    || writable.labels.contains_key(target) || target.starts_with("iat."),
                "unresolved {}", target);
    }
}

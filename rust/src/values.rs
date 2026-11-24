//! The six core values, each bound to the command and the test that prove it.
//!
//! A value that lives only in prose cannot fail, so an agent cannot tell whether it still holds.
//! Every record here names the command that demonstrates the value and the guard that fails when
//! the compiler stops honouring it.

/// One core value: the claim, the command that shows it, and the test that defends it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Value {
    pub key: &'static str,
    pub name: &'static str,
    pub claim: &'static str,
    pub evidence: &'static str,
    pub guard: &'static str,
}

/// The frozen value list, in the order the capabilities manifest publishes.
pub const VALUES: &[Value] = &[
    Value {
        key: "no-dependency",
        name: "No Dependency",
        claim: "The compiler imports only the Rust standard library, and a produced .exe \
                needs 64-bit Windows and nothing else: no C toolchain, assembler, linker or \
                third-party crate.",
        evidence: "cargo test --quiet --manifest-path rust/Cargo.toml",
        guard: "rust/tests/values.rs::the_crate_declares_no_dependencies",
    },
    Value {
        key: "ai-compatible",
        name: "AI Compatible",
        claim: "An agent discovers the whole surface, every limit and every command from one \
                JSON object, and every failure is one machine-readable object with a stable code.",
        evidence: "rust/target/release/cairn-rs.exe capabilities",
        guard: "rust/tests/cli_surface.rs::capabilities_publishes_the_manifest",
    },
    Value {
        key: "easily-understandable",
        name: "Easily Understandable",
        claim: "One numeric type, one binding form per intent, one flat scope, no implicit \
                conversion and no case an agent must look up in a long reference.",
        evidence: "rust/target/release/cairn-rs.exe capabilities",
        guard: "rust/tests/golden_parity.rs::rust_output_matches_frozen_reference_hashes",
    },
    Value {
        key: "machine-language-like",
        name: "Almost Machine Language Like",
        claim: "Source maps to emitted x86-64 bytes with no interpreter, no garbage collector and \
                no hidden runtime, and those bytes can be read back label by label.",
        evidence: "rust/target/release/cairn-rs.exe listing examples/hello.crn",
        guard: "rust/tests/cli_surface.rs::listing_names_the_entry_and_main_labels",
    },
    Value {
        key: "verifiable",
        name: "Verifiable",
        claim: "A built artifact is proved well formed from its bytes alone, and two builds of the \
                same source are byte-identical.",
        evidence: "rust/target/release/cairn-rs.exe verify examples/hello.crn",
        guard: "rust/tests/backend.rs::a_fresh_image_has_no_verifier_problems",
    },
    Value {
        key: "error-codes",
        name: "Easily Problem Understood via Error Codes",
        claim: "Every diagnostic carries a stable code from a registry that is documented and \
                published, and a corrupted image names the invariant it broke.",
        evidence: "rust/target/release/cairn-rs.exe check broken.crn --json",
        guard: "rust/tests/front_end.rs::every_registered_code_has_a_stage_and_a_summary",
    },
];

/// The value with this key, if any.
pub fn by_key(key: &str) -> Option<&'static Value> {
    VALUES.iter().find(|entry| entry.key == key)
}

/// The values as record tuples in their frozen order: `(key, name, claim, evidence)`.
pub fn values_manifest() -> Vec<(&'static str, &'static str, &'static str, &'static str)> {
    VALUES
        .iter()
        .map(|entry| (entry.key, entry.name, entry.claim, entry.evidence))
        .collect()
}

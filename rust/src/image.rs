//! Image pieces shared by the backend: read-only data, writable state and the linked artifact.

use std::collections::BTreeMap;

/// The only DLL a Cairn executable imports.
pub const IMPORT_DLL: &str = "kernel32.dll";

/// The label of the entry stub.
pub const ENTRY_LABEL: &str = "cairn.entry";

/// The imported functions, in IAT order.
pub const IMPORTS: &[&str] = &[
    "GetStdHandle",
    "WriteFile",
    "ReadFile",
    "GetCommandLineA",
    "ExitProcess",
];

/// The linker name of one import address table slot.
pub fn iat_label(function: &str) -> String {
    format!("iat.{}", function)
}

fn align_buffer(data: &mut Vec<u8>, boundary: usize) {
    assert!(boundary > 0, "alignment boundary must be positive");
    let padding = (data.len().wrapping_neg()) % boundary;
    data.resize(data.len() + padding, 0);
}

/// A byte blob with named offsets, placed in the `.rdata` section.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ReadOnlyData {
    pub data: Vec<u8>,
    pub labels: BTreeMap<String, usize>,
}

impl ReadOnlyData {
    pub fn label(&mut self, name: &str) {
        assert!(!self.labels.contains_key(name), "duplicate rodata label {}", name);
        self.labels.insert(name.to_string(), self.data.len());
    }

    pub fn add(&mut self, name: &str, payload: &[u8]) {
        self.label(name);
        self.data.extend_from_slice(payload);
    }

    pub fn align(&mut self, boundary: usize) {
        align_buffer(&mut self.data, boundary);
    }
}

/// A byte blob with named offsets, placed after the IAT in the writable `.data`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct WritableData {
    pub data: Vec<u8>,
    pub labels: BTreeMap<String, usize>,
}

impl WritableData {
    pub fn label(&mut self, name: &str) {
        assert!(!self.labels.contains_key(name), "duplicate writable-data label {}", name);
        self.labels.insert(name.to_string(), self.data.len());
    }

    pub fn reserve(&mut self, name: &str, size: usize) {
        assert!(size > 0, "reserved state must be positive");
        self.label(name);
        self.data.resize(self.data.len() + size, 0);
    }

    pub fn align(&mut self, boundary: usize) {
        align_buffer(&mut self.data, boundary);
    }
}

/// Everything the PE writer needs: code, fixups, data and the entry label.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Artifact {
    pub text: Vec<u8>,
    pub text_labels: BTreeMap<String, usize>,
    pub fixups: Vec<(usize, String)>,
    pub rodata: ReadOnlyData,
    pub entry: String,
    pub imports: Vec<String>,
    pub writable: Vec<u8>,
    pub writable_labels: BTreeMap<String, usize>,
}

impl Default for Artifact {
    fn default() -> Self {
        Self {
            text: Vec::new(),
            text_labels: BTreeMap::new(),
            fixups: Vec::new(),
            rodata: ReadOnlyData::default(),
            entry: ENTRY_LABEL.to_string(),
            imports: IMPORTS.iter().map(|name| name.to_string()).collect(),
            writable: Vec::new(),
            writable_labels: BTreeMap::new(),
        }
    }
}

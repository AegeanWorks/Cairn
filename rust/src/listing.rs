//! Machine-code listing: the emitted bytes, grouped by the label that owns them.

use crate::image::Artifact;

/// Hex bytes per listing row.
pub const BYTES_PER_ROW: usize = 16;

fn rows(chunk: &[u8], start: usize) -> Vec<String> {
    chunk
        .chunks(BYTES_PER_ROW)
        .enumerate()
        .map(|(index, piece)| {
            let hexed = piece.iter().map(|byte| format!("{:02x}", byte)).collect::<Vec<_>>().join(" ");
            format!("  {:06x}  {}", start + index * BYTES_PER_ROW, hexed)
        })
        .collect()
}

/// Every label with its offset and the bytes up to the next label.
pub fn render_listing(artifact: &Artifact) -> Result<String, String> {
    let mut ordered: Vec<(&String, usize)> = if artifact.text_label_order.is_empty() {
        artifact.text_labels.iter().map(|(name, offset)| (name, *offset)).collect()
    } else {
        artifact
            .text_label_order
            .iter()
            .filter_map(|name| artifact.text_labels.get_key_value(name))
            .map(|(name, offset)| (name, *offset))
            .collect()
    };
    ordered.sort_by_key(|(_, offset)| *offset);
    for (name, offset) in &ordered {
        if *offset > artifact.text.len() {
            return Err(format!("label {:?} at offset {} is outside the {}-byte .text",
                               name, offset, artifact.text.len()));
        }
    }
    let mut lines = vec![format!("# .text, {} bytes, {} labels",
                                 artifact.text.len(), ordered.len())];
    for (position, (name, offset)) in ordered.iter().enumerate() {
        let end = ordered
            .get(position + 1)
            .map(|(_, offset)| *offset)
            .unwrap_or(artifact.text.len());
        lines.push(String::new());
        lines.push(format!("{:06x} <{}>  ({} bytes)", offset, name, end - offset));
        lines.extend(rows(&artifact.text[*offset..end], *offset));
    }
    if !artifact.rodata.data.is_empty() {
        lines.push(String::new());
        lines.push(format!("# .rdata, {} bytes", artifact.rodata.data.len()));
        let mut labels: Vec<(&String, usize)> =
            artifact.rodata.labels.iter().map(|(name, offset)| (name, *offset)).collect();
        labels.sort_by_key(|(_, offset)| *offset);
        for (name, offset) in labels {
            lines.push(format!("{:06x} <{}>", offset, name));
        }
    }
    Ok(lines.join("\n"))
}

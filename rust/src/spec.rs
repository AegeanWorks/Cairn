//! A tiny artifact spec used by the byte-level parity check. Both implementations read the same
//! text file, so the comparison tests the PE writer, listing and verifier rather than codegen.

use std::path::Path;

use crate::image::{Artifact, ReadOnlyData};

fn hex_decode(text: &str) -> Result<Vec<u8>, String> {
    if text.len() % 2 != 0 {
        return Err(format!("odd hex length: {}", text));
    }
    (0..text.len())
        .step_by(2)
        .map(|index| {
            u8::from_str_radix(&text[index..index + 2], 16)
                .map_err(|error| format!("bad hex at {}: {}", index, error))
        })
        .collect()
}

/// Load one artifact spec.
pub fn load(path: &Path) -> Result<Artifact, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read {}: {}", path.display(), error))?;
    let mut artifact = Artifact::default();
    artifact.text_labels.clear();
    artifact.imports.clear();
    for line in text.lines() {
        let mut fields = line.split_whitespace();
        let Some(key) = fields.next() else { continue };
        match key {
            "TEXT" => artifact.text = hex_decode(fields.next().unwrap_or(""))?,
            "LABEL" => {
                let name = fields.next().ok_or("LABEL needs a name")?;
                let offset = fields.next().ok_or("LABEL needs an offset")?.parse::<usize>()
                    .map_err(|error| error.to_string())?;
                artifact.text_labels.insert(name.to_string(), offset);
            }
            "FIXUP" => {
                let offset = fields.next().ok_or("FIXUP needs an offset")?.parse::<usize>()
                    .map_err(|error| error.to_string())?;
                let target = fields.next().ok_or("FIXUP needs a label")?;
                artifact.fixups.push((offset, target.to_string()));
            }
            "RODATA" => artifact.rodata = ReadOnlyData {
                data: hex_decode(fields.next().unwrap_or(""))?,
                labels: Default::default(),
            },
            "ROLABEL" => {
                let name = fields.next().ok_or("ROLABEL needs a name")?;
                let offset = fields.next().ok_or("ROLABEL needs an offset")?.parse::<usize>()
                    .map_err(|error| error.to_string())?;
                artifact.rodata.labels.insert(name.to_string(), offset);
            }
            "WRITABLE" => artifact.writable = hex_decode(fields.next().unwrap_or(""))?,
            "WRLABEL" => {
                let name = fields.next().ok_or("WRLABEL needs a name")?;
                let offset = fields.next().ok_or("WRLABEL needs an offset")?.parse::<usize>()
                    .map_err(|error| error.to_string())?;
                artifact.writable_labels.insert(name.to_string(), offset);
            }
            "ENTRY" => artifact.entry = fields.next().ok_or("ENTRY needs a label")?.to_string(),
            "IMPORT" => artifact.imports.push(fields.next().ok_or("IMPORT needs a name")?.to_string()),
            other => return Err(format!("unknown spec line {:?}", other)),
        }
    }
    Ok(artifact)
}

/// The canonical backend dump: linked image, verification results and the listing.
pub fn dump(path: &Path) -> Result<String, String> {
    let artifact = load(path)?;
    let image = crate::pe::link(&artifact)?;
    let mut lines = vec![format!("IMAGE {}", image.iter().map(|b| format!("{:02x}", b))
                                 .collect::<Vec<_>>().join(""))];
    let problems = crate::verify::verify_image(&image);
    lines.push(format!(
        "VERIFY {}",
        problems.iter().map(|p| format!("{}|{}", p.code, p.detail))
            .collect::<Vec<_>>().join(";")
    ));
    lines.push("LISTING".to_string());
    lines.push(crate::listing::render_listing(&artifact)?);
    lines.push("ENDLISTING".to_string());
    Ok(lines.join("\n") + "\n")
}

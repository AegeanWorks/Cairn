"""Listing tests: the label-grouped view of the bytes the writer will place."""

import unittest

from cairn.image import Artifact, ReadOnlyData
from cairn.listing import render_listing


class ListingTest(unittest.TestCase):
    """A corrupt artifact must fail loudly instead of printing negative sizes."""

    def test_label_past_the_text_is_refused(self) -> None:
        artifact = Artifact(text=b"\x00" * 10, text_labels={"x": 100}, fixups=[],
                            rodata=ReadOnlyData())
        with self.assertRaises(ValueError):
            render_listing(artifact)

    def test_labels_render_with_their_bytes(self) -> None:
        artifact = Artifact(text=b"\x90\x90\xcc\xcc", text_labels={"a": 0, "b": 2},
                            fixups=[], rodata=ReadOnlyData())
        listing = render_listing(artifact)
        self.assertIn("<a>", listing)
        self.assertIn("<b>", listing)


if __name__ == "__main__":
    unittest.main()

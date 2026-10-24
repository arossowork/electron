"""Round-trip design edits must remain readable by Design.load's YAML parser."""

import pytest

from hwflow.design import Design


@pytest.mark.parametrize("field", ["source", "sources"])
def test_save_quotes_question_marks_in_flow_values(tmp_path, field):
    path = tmp_path / "design.yaml"
    path.write_text(
        "# component evidence\n"
        "project: url-roundtrip\n"
        "components:\n"
        "  C241: {value: 100n, source: https://example.com/old, sources: [old]} # retain this\n"
        "  C242: {value: 1u}\n"
    )
    d = Design.load(path)
    url = "https://product.tdk.com/en/search/capacitor/ceramic/mlcc/info?part_no=C3216"
    value = url if field == "source" else [url]
    d.components["C241"].data[field] = value

    d.save()

    saved = Design.load(path)
    assert saved.components["C241"].get(field) == value
    text = path.read_text()
    assert "# component evidence" in text and "# retain this" in text
    assert "C241: {value: 100n" in text
    assert text.index("project:") < text.index("components:")
    assert list(saved.components) == ["C241", "C242"]
    # A second save must preserve the quoted URL and remain readable.
    saved.save()
    assert Design.load(path).components["C241"].get(field) == value


def test_save_preserves_block_urls_and_existing_quotes(tmp_path):
    path = tmp_path / "design.yaml"
    text = (
        "project: quoted-evidence\n"
        "source: https://example.com/info?part_no=C241\n"
        "components:\n"
        '  C241: {source: "https://example.com/info?part_no=C241", value: 100n}\n'
    )
    path.write_text(text)

    Design.load(path).save()

    assert path.read_text() == text
    assert Design.load(path).raw["source"] == "https://example.com/info?part_no=C241"

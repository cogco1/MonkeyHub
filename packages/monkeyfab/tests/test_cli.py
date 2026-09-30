import argparse
import json

import numpy as np
import pytest
import trimesh

from monkeyfab.cli import main, parse_scale


def test_scaled_stls_can_be_reassembled_without_changing_source(tmp_path):
    source = tmp_path / "source.stl"
    model = trimesh.creation.box(extents=[52, 28, 8])
    model.apply_translation([-40, 20, 7])
    model.export(source)
    original = source.read_bytes()
    output = tmp_path / "print"

    assert main(["prepare", str(source), "--input-unit", "m", "--scale", "1:100", "--printer", "x1c", "--output", str(output)]) == 0
    table = json.loads((output / "parts.json").read_text(encoding="utf-8"))
    assert table["output_unit"] == "mm"
    assert table["scale"] == 0.01
    assert table["source_coordinate_to_mm"] == 10.0
    assert len(table["parts"]) == 6
    restored = []
    for part in table["parts"]:
        saved = trimesh.load_mesh(output / part["file"])
        assert saved.is_watertight
        assert saved.is_volume
        assert np.all(saved.extents <= np.asarray(table["working_volume_mm"]) + 1e-4)
        assert np.allclose(saved.bounds[0], 0, atol=1e-4)
        saved.apply_translation(part["assembly_offset_mm"])
        restored.append(saved)
    combined = trimesh.util.concatenate(restored)
    assert np.allclose(combined.bounds, model.bounds * 10, atol=1e-4)
    assert combined.volume == pytest.approx(model.volume * 10**3, rel=1e-6)
    assert source.read_bytes() == original


def test_existing_results_are_not_overwritten(tmp_path, capsys):
    source = tmp_path / "source.stl"
    trimesh.creation.box().export(source)
    output = tmp_path / "print"
    output.mkdir()
    retained = output / "part_001.stl"
    retained.write_bytes(b"previous result")
    assert main(["prepare", str(source), "--input-unit", "mm", "--printer", "h2d", "--output", str(output)]) == 2
    assert retained.read_bytes() == b"previous result"
    assert "new or empty directory" in capsys.readouterr().err


def test_open_mesh_fails_before_creating_outputs(tmp_path, capsys):
    source = tmp_path / "open.stl"
    mesh = trimesh.creation.box()
    mesh.update_faces(np.arange(len(mesh.faces) - 1))
    mesh.export(source)
    output = tmp_path / "print"
    assert main(["prepare", str(source), "--input-unit", "mm", "--printer", "x1c", "--output", str(output)]) == 2
    assert not output.exists()
    assert capsys.readouterr().err


def test_uniform_scale_ratios():
    assert parse_scale("1:100") == 0.01
    assert parse_scale("2:1") == 2
    assert parse_scale("0.005") == 0.005
    for invalid in ("-1:-100", "1:0", "nan", "inf", "1:inf", "0", "1:2:3"):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_scale(invalid)


def test_invalid_obj_vertex_reference_is_an_input_error(tmp_path, capsys):
    source = tmp_path / "invalid.obj"
    source.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 999\n", encoding="utf-8")
    output = tmp_path / "print"
    assert main(["prepare", str(source), "--input-unit", "mm", "--printer", "x1c", "--output", str(output)]) == 2
    assert "invalid vertex reference" in capsys.readouterr().err
    assert not output.exists()

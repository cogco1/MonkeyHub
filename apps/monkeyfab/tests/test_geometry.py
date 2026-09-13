import numpy as np
import pytest
import trimesh

from monkeyfab.geometry import GeometryError, prepare_mesh


def _reassembled(parts):
    meshes = []
    for part in parts:
        restored = part.mesh.copy()
        restored.apply_translation(part.assembly_offset_mm)
        meshes.append(restored)
    return trimesh.util.concatenate(meshes)


def _assert_printable(parts, volume):
    assert parts
    for part in parts:
        assert part.mesh.is_watertight
        assert part.mesh.is_winding_consistent
        assert part.mesh.volume > 0
        np.testing.assert_allclose(part.mesh.bounds[0], (0, 0, 0), atol=1e-10)
        assert np.all(part.mesh.extents <= np.asarray(volume) + 1e-8)


def test_closed_box_crosses_grid_in_all_three_axes():
    mesh = trimesh.creation.box(extents=(600, 350, 300))
    parts = prepare_mesh(mesh, 1, (256, 256, 256))
    assert len(parts) == 12
    assert {p.grid_index for p in parts} == {
        (x, y, z) for x in range(3) for y in range(2) for z in range(2)
    }
    _assert_printable(parts, (256, 256, 256))
    restored = _reassembled(parts)
    assert restored.volume == pytest.approx(600 * 350 * 300, rel=1e-10)
    np.testing.assert_allclose(restored.bounds, mesh.bounds, atol=1e-10)
    np.testing.assert_allclose(restored.center_mass, mesh.center_mass, atol=1e-10)


def test_through_hole_remains_empty_after_capped_cuts():
    # A square tube, with a through-hole extending beyond both end faces.
    outer = trimesh.creation.box(extents=(60, 40, 20))
    hole = trimesh.creation.box(extents=(20, 20, 40))
    mesh = trimesh.boolean.difference([outer, hole], engine="manifold")
    parts = prepare_mesh(mesh, 1, (25, 25, 15))
    _assert_printable(parts, (25, 25, 15))
    restored = _reassembled(parts)
    assert restored.volume == pytest.approx(60 * 40 * 20 - 20 * 20 * 20, rel=1e-10)
    np.testing.assert_allclose(restored.bounds, outer.bounds, atol=1e-10)
    # Probe inside the original void, away from its shared boundary faces.
    void_probe = trimesh.creation.box(extents=(19, 19, 40))
    for part in parts:
        placed = part.mesh.copy()
        placed.apply_translation(part.assembly_offset_mm)
        overlap = trimesh.boolean.intersection([placed, void_probe], engine="manifold")
        assert overlap.is_empty


def test_scale_and_negative_coordinates_reassemble_without_mutating_source():
    mesh = trimesh.creation.box(extents=(3, 2, 1))
    mesh.apply_translation((-5, -2, 4))
    original_vertices, original_faces = mesh.vertices.copy(), mesh.faces.copy()
    parts = prepare_mesh(mesh, 100, (160, 150, 90))
    _assert_printable(parts, (160, 150, 90))
    restored = _reassembled(parts)
    np.testing.assert_allclose(restored.bounds, mesh.bounds * 100, atol=1e-9)
    np.testing.assert_allclose(restored.center_mass, mesh.center_mass * 100, atol=1e-9)
    assert restored.volume == pytest.approx(mesh.volume * 100**3, rel=1e-10)
    assert any(part.assembly_offset_mm[0] < 0 for part in parts)
    np.testing.assert_array_equal(mesh.vertices, original_vertices)
    np.testing.assert_array_equal(mesh.faces, original_faces)


def test_separate_overlapping_bodies_are_preserved_without_union():
    box = trimesh.creation.box(extents=(10, 10, 10))
    mesh = trimesh.util.concatenate([box, box.copy()])
    parts = prepare_mesh(mesh, 1, (20, 20, 20))
    assert len(parts) == 2
    assert [part.grid_index for part in parts] == [(0, 0, 0), (0, 0, 0)]
    assert sum(part.mesh.volume for part in parts) == pytest.approx(2000)
    np.testing.assert_allclose(_reassembled(parts).bounds, mesh.bounds)


def test_box_exactly_fits_print_volume_without_extra_slivers():
    mesh = trimesh.creation.box(extents=(256, 256, 256))
    parts = prepare_mesh(mesh, 1, (256, 256, 256))
    assert len(parts) == 1
    _assert_printable(parts, (256, 256, 256))
    assert parts[0].mesh.volume == pytest.approx(mesh.volume)


def test_open_mesh_is_refused_without_filling_a_single_triangle():
    mesh = trimesh.creation.box()
    mesh.update_faces(np.arange(len(mesh.faces) - 1))
    with pytest.raises(GeometryError, match="closed and watertight"):
        prepare_mesh(mesh, 1, (256, 256, 256))
    assert len(mesh.faces) == 11


def test_disconnected_inward_cavity_shell_is_refused():
    outer = trimesh.creation.box(extents=(10, 10, 10))
    inner = trimesh.creation.box(extents=(5, 5, 5))
    inner.invert()
    mesh = trimesh.util.concatenate([outer, inner])
    with pytest.raises(GeometryError, match="cavity shells"):
        prepare_mesh(mesh, 1, (256, 256, 256))


def test_inward_or_inconsistent_normals_are_refused_without_repair():
    inward = trimesh.creation.box()
    inward.invert()
    with pytest.raises(GeometryError, match="positive volume"):
        prepare_mesh(inward, 1, (256, 256, 256))
    inconsistent = trimesh.creation.box()
    inconsistent.faces[0] = inconsistent.faces[0][::-1]
    with pytest.raises(GeometryError, match="winding"):
        prepare_mesh(inconsistent, 1, (256, 256, 256))


@pytest.mark.parametrize("factor", [0, -1, np.inf, np.nan])
def test_invalid_scale_is_refused(factor):
    with pytest.raises(GeometryError, match="scale_factor"):
        prepare_mesh(trimesh.creation.box(), factor, (256, 256, 256))


@pytest.mark.parametrize("volume", [(0, 256, 256), (-1, 1, 1), (1, np.inf, 1), (1, np.nan, 1), (1, 1)])
def test_invalid_build_volume_is_refused(volume):
    with pytest.raises(GeometryError, match="build_volume_mm"):
        prepare_mesh(trimesh.creation.box(), 1, volume)


def test_empty_and_nonfinite_meshes_are_refused():
    with pytest.raises(GeometryError, match="non-empty"):
        prepare_mesh(trimesh.Trimesh(), 1, (256, 256, 256))
    mesh = trimesh.creation.box()
    mesh.vertices[0, 0] = np.nan
    with pytest.raises(GeometryError, match="finite"):
        prepare_mesh(mesh, 1, (256, 256, 256))

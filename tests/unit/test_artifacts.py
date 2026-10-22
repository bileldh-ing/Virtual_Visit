import json
from pathlib import Path

import numpy as np
import pytest
from roomscape_recon.artifacts import build_manifest, build_viewer_metadata, safe_child, validate_ply, verify_manifest, write_ply
from roomscape_recon.colmap_io import Camera, RegisteredImage, SparseModel, qvec_to_rotmat, read_text_model
from roomscape_recon.errors import ReconError


def test_ply_roundtrip_and_validation(tmp_path: Path):
    xyz = np.random.default_rng(0).normal(size=(100, 3)).astype(np.float32)
    rgb = np.full((100, 3), 200, dtype=np.uint8)
    p = tmp_path / "pts.ply"
    write_ply(p, xyz, rgb)
    info = validate_ply(p)
    assert info.vertex_count == 100 and info.fmt == "binary_little_endian"
    assert {"x", "y", "z", "red", "green", "blue"} <= set(info.properties)


def test_truncated_ply_is_rejected(tmp_path: Path):
    p = tmp_path / "pts.ply"
    write_ply(p, np.zeros((10, 3), np.float32), np.zeros((10, 3), np.uint8))
    p.write_bytes(p.read_bytes()[:-5])
    with pytest.raises(ReconError):
        validate_ply(p)
    (tmp_path / "empty.ply").write_bytes(b"")
    with pytest.raises(ReconError):
        validate_ply(tmp_path / "empty.ply")


def test_mesh_requires_faces(tmp_path: Path):
    p = tmp_path / "pts.ply"
    write_ply(p, np.zeros((3, 3), np.float32), np.zeros((3, 3), np.uint8))
    with pytest.raises(ReconError):
        validate_ply(p, expect_faces=True)


def test_path_containment(tmp_path: Path):
    assert safe_child(tmp_path, "a/b.txt") == (tmp_path / "a/b.txt").resolve()
    for bad in ("../x", "/etc/passwd", "a/../../x"):
        with pytest.raises(ReconError):
            safe_child(tmp_path, bad)


def test_manifest_checksums_detect_tampering(tmp_path: Path):
    (tmp_path / "a.json").write_text("{}")
    build_manifest(tmp_path, [{"path": "a.json", "kind": "X"}], {"name": "t"})
    assert verify_manifest(tmp_path) == []
    (tmp_path / "a.json").write_text('{"x":1}')
    assert verify_manifest(tmp_path) == ["a.json: checksum mismatch"]
    m = json.loads((tmp_path / "manifest.json").read_text())
    m["artifacts"][0]["path"] = "../escape"
    (tmp_path / "manifest.json").write_text(json.dumps(m))
    assert "escapes" in verify_manifest(tmp_path)[0]


def test_colmap_text_parsing_and_camera_center(tmp_path: Path):
    (tmp_path / "cameras.txt").write_text("# c\n1 SIMPLE_RADIAL 800 600 700 400 300 0.01\n")
    (tmp_path / "images.txt").write_text(
        "# i\n1 1 0 0 0 0 0 -2 1 a.jpg\n10 20 5 30 40 -1\n2 1 0 0 0 1 0 -2 1 b.jpg\n\n")
    (tmp_path / "points3D.txt").write_text("# p\n5 0.1 0.2 3.0 255 0 0 0.5 1 0 2 0\n")
    m = read_text_model(tmp_path)
    assert len(m.images) == 2 and m.images[1].num_points2d == 2 and m.images[2].num_points2d == 0
    assert np.allclose(m.images[1].center(), [0, 0, 2])
    assert m.xyz.shape == (1, 3) and m.track_lengths[0] == 2
    assert np.allclose(qvec_to_rotmat((1, 0, 0, 0)), np.eye(3))


def test_viewer_metadata_marks_scale_unknown():
    cams = {1: Camera(1, "PINHOLE", 100, 100, [80, 50, 50])}
    imgs = {i: RegisteredImage(i, (1, 0, 0, 0), (float(i), 0, 0), 1, f"{i}.jpg", 10) for i in range(1, 4)}
    model = SparseModel(cams, imgs, np.zeros((5, 3), np.float32), np.zeros((5, 3), np.uint8), np.zeros(5), np.full(5, 2))
    meta = build_viewer_metadata(model, {"1.jpg": "u1"})
    assert meta["scale"] == "unknown"
    assert meta["viewpoints"][0]["upload_id"] == "u1"
    assert np.allclose(meta["up_estimate"], [0, -1, 0])  # camera -Y in world for identity rotation

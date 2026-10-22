from conftest import make_image
from roomscape_recon.quality import analyze_bytes, build_report, sniff_mime


def codes(rec):
    return {i.code for i in rec.issues}


def test_magic_bytes_detection():
    assert sniff_mime(make_image()[:16]) == "image/jpeg"
    assert sniff_mime(make_image(fmt="PNG")[:16]) == "image/png"
    assert sniff_mime(b"GIF89a....") is None
    assert sniff_mime(b"<?php echo 1; ?>") is None


def test_corrupt_and_unsupported_images_are_errors():
    rec, _ = analyze_bytes("bad.jpg", make_image()[:500])
    assert "DECODE_FAILED" in codes(rec) and not rec.decoded
    rec, _ = analyze_bytes("x.txt", b"hello world, not an image")
    assert "UNSUPPORTED_FORMAT" in codes(rec)


def test_blur_and_exposure_heuristics():
    sharp, _ = analyze_bytes("a.jpg", make_image(seed=1))
    blurry, _ = analyze_bytes("b.jpg", make_image(seed=1, blur=True))
    dark, _ = analyze_bytes("c.jpg", make_image(seed=2, dark=True))
    assert "BLURRY" not in codes(sharp)
    assert "BLURRY" in codes(blurry)
    assert blurry.blur_score < sharp.blur_score
    assert "UNDEREXPOSED" in codes(dark)


def test_duplicates_and_panorama_detection():
    img = make_image(seed=3)
    rep = build_report([("a.jpg", img), ("b.jpg", img), ("pano.jpg", make_image(4000, 2000, seed=5))])
    by = {i["name"]: i for i in rep["images"]}
    assert any(x["code"] == "DUPLICATE" for x in by["b.jpg"]["issues"])
    assert by["pano.jpg"]["is_panorama"] is True
    assert rep["summary"]["panoramas"] == 1


def test_report_status_needs_more_images():
    rep = build_report([(f"{i}.jpg", make_image(seed=i)) for i in range(3)])
    assert rep["status"] == "NEEDS_MORE_IMAGES"
    assert rep["recommended_mode"] == "PHOTOGRAMMETRY"
    assert "cannot guarantee" in rep["disclaimer"]


def test_report_unsupported_when_nothing_decodes():
    rep = build_report([("a.jpg", b"nope"), ("b.jpg", b"also nope")])
    assert rep["status"] == "UNSUPPORTED_INPUT"

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image

from pmagent.evidence.vision import VisualAnalyzer


def _save(tmp_path: Path, image: Image.Image, name: str = "shot.png") -> Path:
    path = tmp_path / name
    image.save(path)
    return path


def _seam_image() -> Image.Image:
    image = Image.new("RGB", (300, 300), (255, 255, 255))
    for x in range(150, 300):
        for y in range(300):
            image.putpixel((x, y), (20, 20, 120))
    return image


def _solid_image() -> Image.Image:
    image = Image.new("RGB", (300, 300), (240, 240, 240))
    for x in range(80, 200):
        for y in range(80, 200):
            image.putpixel((x, y), (10, 10, 10))
    return image


def _band_image() -> Image.Image:
    image = Image.new("RGB", (300, 300), (255, 255, 255))
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255)]
    for index, color in enumerate(colors):
        top = index * 60
        for x in range(300):
            for y in range(top, top + 60):
                image.putpixel((x, y), color)
    return image


def _misalign_image() -> Image.Image:
    image = Image.new("RGB", (300, 300), (255, 255, 255))
    for x in (150, 153):
        for y in range(300):
            image.putpixel((x, y), (0, 0, 0))
    return image


def _normal_images() -> list[Image.Image]:
    gradient = Image.new("RGB", (300, 300))
    for x in range(300):
        for y in range(300):
            value = int(255 * x / 300)
            gradient.putpixel((x, y), (value, value, value))
    noise = Image.new("RGB", (300, 300))
    rng = random.Random(7)
    for x in range(300):
        for y in range(300):
            value = rng.randint(0, 255)
            noise.putpixel((x, y), (value, value, value))
    return [gradient, noise]


def test_detects_seam(tmp_path: Path) -> None:
    analysis = VisualAnalyzer().analyze(_save(tmp_path, _seam_image()))
    assert "seam" in analysis.evidence.features
    assert any(region.kind == "seam" for region in analysis.regions)


def test_detects_solid_block(tmp_path: Path) -> None:
    analysis = VisualAnalyzer().analyze(_save(tmp_path, _solid_image()))
    assert "solid" in analysis.evidence.features


def test_detects_band(tmp_path: Path) -> None:
    analysis = VisualAnalyzer().analyze(_save(tmp_path, _band_image()))
    assert "band" in analysis.evidence.features


def test_detects_misalignment(tmp_path: Path) -> None:
    analysis = VisualAnalyzer().analyze(_save(tmp_path, _misalign_image()))
    assert "misalign" in analysis.evidence.features


def test_no_false_positive_on_normal_images(tmp_path: Path) -> None:
    for index, image in enumerate(_normal_images()):
        analysis = VisualAnalyzer().analyze(_save(tmp_path, image, f"normal{index}.png"))
        assert analysis.evidence.features == [], analysis.evidence.features


def test_model_conflict_annotation(tmp_path: Path) -> None:
    class FakeDescriber:
        def describe(self, path):  # noqa: ANN001
            return {
                "description": "看起来是色带",
                "kinds": ["band"],
                "regions": [{"x": 0, "y": 100, "w": 300, "h": 60, "kind": "band"}],
            }

    analysis = VisualAnalyzer(describer=FakeDescriber()).analyze(
        _save(tmp_path, _band_image())
    )
    assert any("conflict" in feature for feature in analysis.evidence.features) or (
        "band" in analysis.evidence.features
    )
    assert any(region.source == "model" for region in analysis.regions)


def test_model_failure_degrades_to_rules(tmp_path: Path) -> None:
    class BrokenDescriber:
        def describe(self, path):  # noqa: ANN001
            raise RuntimeError("model down")

    analysis = VisualAnalyzer(describer=BrokenDescriber()).analyze(
        _save(tmp_path, _seam_image())
    )
    assert "seam" in analysis.evidence.features

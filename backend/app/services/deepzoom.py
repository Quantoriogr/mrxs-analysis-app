from io import BytesIO
import math
from pathlib import Path
import shutil
import zipfile

import openslide
from PIL import Image
from openslide.deepzoom import DeepZoomGenerator
from pydantic import BaseModel

from ..config import MAX_JPEG_QUALITY, SLIDE_ROOT, TILE_SIZE, OVERLAP
from ..storage import storage


class OpenSlideContext:
    def __init__(self, mrxs_path: str | Path):
        self.mrxs_path = str(mrxs_path)
        self.slide = None

    def __enter__(self):
        self.slide = openslide.OpenSlide(self.mrxs_path)
        return self.slide

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.slide:
            self.slide.close()
        return False


class DZIMetadata(BaseModel):
    width: int
    height: int
    tile_size: int
    overlap: int
    level_count: int
    level_tiles: list[list[int]]


def materialize_slide(
    slide_id: str,
    original_object: str,
) -> Path:
    """Materialize a slide from storage to local disk."""

    slide_dir = SLIDE_ROOT / slide_id
    slide_dir.mkdir(parents=True, exist_ok=True)

    # Check if already materialized
    mrxs_files = list(slide_dir.rglob("*.mrxs"))
    if mrxs_files:
        return mrxs_files[0]

    # Clean incomplete workspace.
    for item in slide_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    data = storage.get_object(original_object)

    upload = slide_dir / "upload"

    upload.write_bytes(data)

    if zipfile.is_zipfile(upload):

        package_dir = slide_dir / "package"
        package_dir.mkdir()

        with zipfile.ZipFile(upload) as archive:
            archive.extractall(package_dir)

        upload.unlink()

        mrxs_files = list(package_dir.rglob("*.mrxs"))

        if not mrxs_files:
            raise RuntimeError("ZIP does not contain an MRXS file")

        mrxs_path = mrxs_files[0]

    else:
        mrxs_path = slide_dir / "slide.mrxs"
        upload.rename(mrxs_path)

    return mrxs_path


def save_original(slide_id: str, filename: str, data: bytes) -> str:

    object_name = f"{slide_id}/original/{filename}"
    storage.save_object(object_name, data, "application/octet-stream")

    return object_name


def get_tile(
    slide_id: str,
    original_object: str,
    level: int,
    col: int,
    row: int,
) -> bytes:

    cache_object = f"{slide_id}/tiles/" f"{level}/{col}_{row}.jpeg"

    if storage.exists(cache_object):
        return storage.get_object(cache_object)

    mrxs_path = materialize_slide(
        slide_id,
        original_object,
    )

    with OpenSlideContext(mrxs_path) as slide:
        generator = DeepZoomGenerator(
            slide,
            tile_size=256,
            overlap=1,
            limit_bounds=False,
        )

        if level < 0 or level >= generator.level_count:
            raise ValueError(f"Invalid Deep Zoom level {level}")

        cols, rows = generator.level_tiles[level]

        if col < 0 or row < 0 or col >= cols or row >= rows:
            raise ValueError(f"Invalid tile {col},{row}")

        tile = generator.get_tile(
            level,
            (col, row),
        )

        output = BytesIO()

        tile.convert("RGB").save(
            output,
            format="JPEG",
            quality=MAX_JPEG_QUALITY,
        )

        data = output.getvalue()

        storage.save_object(
            cache_object,
            data,
            "image/jpeg",
        )

        return data


def crop_region(
    slide_id: str,
    original_object: str,
    x: int,
    y: int,
    width: int,
    height: int,
    deepzoom_level: int,
    max_pixels: int,
) -> bytes:

    if width <= 0 or height <= 0:
        raise ValueError("Crop dimensions must be positive")

    mrxs_path = materialize_slide(
        slide_id,
        original_object,
    )

    with OpenSlideContext(mrxs_path) as slide:
        slide_width, slide_height = slide.dimensions

        if x < 0 or y < 0 or x + width > slide_width or y + height > slide_height:
            raise ValueError("Crop is outside slide bounds")

        # Deep Zoom level 0 is the smallest pyramid level. Calculation is based on the largest slide dimension.
        max_dzi_level = int(math.ceil(math.log2(max(slide_width, slide_height))))

        if deepzoom_level < 0 or deepzoom_level > max_dzi_level:
            raise ValueError(f"Invalid Deep Zoom level {deepzoom_level}")

        level_scale = 1.0 / (2 ** (max_dzi_level - deepzoom_level))
        target_width = max(1, int(math.ceil(width * level_scale)))
        target_height = max(1, int(math.ceil(height * level_scale)))

        # Upper limit of area to be sent to analysis.
        if target_width * target_height > max_pixels:
            raise ValueError("Requested crop is too large at the selected zoom level")

        target_downsample = 1.0 / level_scale
        openslide_level = slide.get_best_level_for_downsample(target_downsample)
        actual_downsample = float(slide.level_downsamples[openslide_level])

        read_width = max(1, int(math.ceil(width / actual_downsample)))
        read_height = max(1, int(math.ceil(height / actual_downsample)))

        image = slide.read_region(
            (x, y),
            openslide_level,
            (read_width, read_height),
        ).convert("RGB")

        # Match the level between OpenSeaDragon and Deep Zoom and resample
        if image.size != (target_width, target_height):
            image = image.resize(
                (target_width, target_height),
                resample=Image.Resampling.LANCZOS,  # TODO: Check other resampling methods (picked the best quality)
            )

        output = BytesIO()

        image.save(
            output,
            format="JPEG",
            quality=MAX_JPEG_QUALITY,
            optimize=True,
        )

        return output.getvalue()


def build_dzi_metadata(
    slide_id: str,
    original_object: str,
) -> DZIMetadata:

    mrxs_path = materialize_slide(
        slide_id,
        original_object,
    )

    with OpenSlideContext(mrxs_path) as slide:
        generator = DeepZoomGenerator(
            slide,
            tile_size=TILE_SIZE,
            overlap=OVERLAP,
            limit_bounds=False,
        )

        return DZIMetadata(
            width=slide.dimensions[0],
            height=slide.dimensions[1],
            tile_size=TILE_SIZE,
            overlap=OVERLAP,
            level_count=generator.level_count,
            level_tiles=[list(x) for x in generator.level_tiles],
        )


def wrap_dzi_xml(meta: DZIMetadata) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Image xmlns="http://schemas.microsoft.com/deepzoom/2008"
       Format="jpeg"
       Overlap="{meta.overlap}"
       TileSize="{meta.tile_size}">
    <Size
        Width="{meta.width}"
        Height="{meta.height}"
    />
</Image>
"""

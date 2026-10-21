from io import BytesIO
from unittest.mock import Mock
import zipfile

import pytest
from PIL import Image
from pypdf import PdfReader
from minio.error import S3Error
from backend.app.services import deepzoom as dz
from backend.app.services.report import build_report_pdf
from backend.app.storage import ObjectStorage
from backend.app.config import Config


def test_config_lists():
    config = Config(_env_file=None, analysis_models=' a, b,a, ,', cors_origins=' http://a, ,http://b ')
    assert config.models == ['a', 'b']
    assert config.cors_origin_list == ['http://a', 'http://b']


def test_storage():
    storage = ObjectStorage()
    storage.client = Mock()
    storage.client.bucket_exists.return_value = False
    storage.ensure_bucket()
    storage.client.make_bucket.assert_called_once_with(storage.bucket)
    storage.save_object('image', b'abc', 'image/jpeg')
    args, kwargs = storage.client.put_object.call_args
    assert args[2].read() == b'abc'
    assert kwargs['length'] == 3
    response = storage.client.get_object.return_value
    response.read.return_value = b'abc'
    assert storage.get_object('image') == b'abc'
    response.close.assert_called_once()
    response.release_conn.assert_called_once()
    assert storage.exists('image')
    storage.client.stat_object.side_effect = S3Error('NoSuchKey', 'missing', 'x', 'x', 'x', None)
    assert not storage.exists('missing')


def test_storage_cleanup_on_error():
    storage = ObjectStorage()
    storage.client = Mock()
    response = storage.client.get_object.return_value
    response.read.side_effect = RuntimeError('read failed')
    with pytest.raises(RuntimeError):
        storage.get_object('image')
    response.close.assert_called_once()
    response.release_conn.assert_called_once()


@pytest.mark.parametrize('zipped', [False, True])
def test_materialize_cache(tmp_path, monkeypatch, zipped):
    data = b'slide-data'
    if zipped:
        output = BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('package/test.mrxs', data)
            archive.writestr('package/test/Slidedat.ini', b'companion')
        data = output.getvalue()
    storage = Mock()
    storage.get_object.return_value = data
    monkeypatch.setattr(dz, 'SLIDE_ROOT', tmp_path)
    monkeypatch.setattr(dz, 'storage', storage)
    path = dz.materialize_slide('s', 'original')
    assert path.read_bytes() == b'slide-data'
    assert dz.materialize_slide('s', 'original') == path
    storage.get_object.assert_called_once()


@pytest.fixture
def fake_slide(monkeypatch):
    slide = Mock(dimensions=(1024, 512), level_downsamples=[1, 4])
    slide.get_best_level_for_downsample.return_value = 1
    slide.read_region.side_effect = lambda origin, level, size: Image.new('RGBA', size, 'red')
    monkeypatch.setattr(dz, 'materialize_slide', lambda *args: 'test.mrxs')
    monkeypatch.setattr(dz.openslide, 'OpenSlide', lambda path: slide)
    return slide


def test_crop_resampling(fake_slide):
    data = dz.crop_region('s', 'o', 0, 0, 100, 80, 9, 10000)
    assert Image.open(BytesIO(data)).size == (50, 40)
    fake_slide.close.assert_called_once()


@pytest.mark.parametrize('x,y,width,height,level,limit', [(0, 0, 0, 1, 10, 100), (-1, 0, 10, 10, 10, 100), (1000, 0, 100, 10, 10, 10000), (0, 0, 10, 10, 11, 100), (0, 0, 100, 100, 10, 99)])
def test_crop_validation(fake_slide, x, y, width, height, level, limit):
    with pytest.raises(ValueError):
        dz.crop_region('s', 'o', x, y, width, height, level, limit)


def test_tiles_and_metadata(fake_slide, monkeypatch):
    storage = Mock()
    storage.exists.return_value = False
    monkeypatch.setattr(dz, 'storage', storage)
    generator = Mock(level_count=2, level_tiles=[(1, 1), (2, 1)])
    generator.get_tile.return_value = Image.new('RGB', (256, 256))
    monkeypatch.setattr(dz, 'DeepZoomGenerator', lambda *args, **kwargs: generator)
    assert dz.get_tile('s', 'o', 1, 1, 0).startswith(b'\xff\xd8')
    storage.save_object.assert_called_once()
    for level, col in [(-1, 0), (2, 0), (1, 2)]:
        with pytest.raises(ValueError):
            dz.get_tile('s', 'o', level, col, 0)
    meta = dz.build_dzi_metadata('s', 'o')
    assert (meta.width, meta.height, meta.level_tiles) == (1024, 512, [[1, 1], [2, 1]])
    assert 'TileSize="256"' in dz.wrap_dzi_xml(meta)
    storage.exists.return_value = True
    storage.get_object.return_value = b'cached'
    assert dz.get_tile('s', 'o', 1, 0, 0) == b'cached'


def test_pdf_pagination(jpeg):
    report = {'aggregate': {'analyses_completed': 1, 'analyses_requested': 2}, 'model_results': [{'model': 'test', 'highlights': 'Findings. ' * 1500}], 'errors': [{'model': 'failed', 'error': 'Unavailable'}]}
    pdf = build_report_pdf([jpeg], report)
    assert len(PdfReader(BytesIO(pdf)).pages) > 1
    assert len(PdfReader(BytesIO(build_report_pdf([jpeg], {}))).pages) >= 1
    with pytest.raises(ValueError):
        build_report_pdf([], {})

from io import BytesIO
from unittest.mock import AsyncMock
import zipfile

import pytest
from backend.app import main


def test_health_models_startup(client, store):
    assert client.get('/health').json() == {'status': 'ok'}
    assert client.get('/api/config/models').json() == {'models': ['model-a', 'model-b']}
    store.ensure_bucket.assert_called_once()


def test_jpeg_upload_and_read(client, slide, jpeg):
    assert slide['slide_type'] == 'jpeg'
    assert slide['image_count'] == 1
    response = client.get(slide['image_urls'][0])
    assert response.headers['content-type'] == 'image/jpeg'
    assert response.content.startswith(b'\xff\xd8')
    assert client.get(f"/api/slides/{slide['id']}/images/9.jpeg").status_code == 404
    assert client.get(slide['dzi_url']).status_code == 400


@pytest.mark.parametrize('filename,data', [('x.png', b'x'), ('x.jpg', b''), ('x.jpg', b'bad'), ('x.zip', b'bad')])
def test_invalid_upload(client, filename, data):
    assert client.post('/api/slides', files={'file': (filename, data)}).status_code == 400


def test_zip_upload(client, jpeg):
    archive = BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('images/a.jpg', jpeg)
        z.writestr('b.JPEG', jpeg)
        z.writestr('__MACOSX/ignored.jpg', b'bad')
        z.writestr('notes.txt', b'ignored')
    result = client.post('/api/slides', files={'file': ('images.zip', archive.getvalue())})
    assert result.status_code == 200
    assert result.json()['image_count'] == 2


@pytest.mark.parametrize('filename,data', [('x.pdf', b'hi'), ('x.txt', b''), ('x.txt', b'  '), ('x.txt', b'\xff'), ('x.txt', b'x' * 1000001)])
def test_invalid_prompt(client, slide, filename, data):
    assert client.post(f"/api/slides/{slide['id']}/prompt", files={'file': (filename, data)}).status_code == 400


def test_analysis_lifecycle(client, slide, monkeypatch):
    url = f"/api/slides/{slide['id']}"
    assert client.post(url + '/analyze', json={'models': ['model-a']}).status_code == 400
    assert client.post(url + '/prompt', files={'file': ('prompt.txt', b'Analyze this image')}).status_code == 200
    for models in [[], ['unknown'], ['model-a', 'model-a']]:
        assert client.post(url + '/analyze', json={'models': models}).status_code == 400
    analyze = AsyncMock(return_value={'model_results': [], 'errors': []})
    monkeypatch.setattr(main, 'analyze_slide', analyze)
    result = client.post(url + '/analyze', json={'models': ['model-a']}).json()
    job_url = '/api/jobs/' + result['job_id']
    job = client.get(job_url).json()
    assert job['status'] == 'completed'
    assert analyze.call_args.args[0].prompt == 'Analyze this image'
    assert analyze.call_args.args[2] == ['model-a']
    pdf = client.get(job['report_pdf_url'])
    assert pdf.content.startswith(b'%PDF-')
    assert 'attachment;' in pdf.headers['content-disposition']


def test_failed_job(client, slide, monkeypatch):
    main.slides[slide['id']].prompt = 'Analyze'
    monkeypatch.setattr(main, 'analyze_slide', AsyncMock(side_effect=RuntimeError('provider unavailable')))
    job_id = client.post(f"/api/slides/{slide['id']}/analyze", json={'models': ['model-a']}).json()['job_id']
    assert client.get('/api/jobs/' + job_id).json()['error'] == 'provider unavailable'
    assert client.get(f'/api/jobs/{job_id}/report.pdf').status_code == 409


@pytest.mark.parametrize('path', ['/api/slides/missing/dzi', '/api/jobs/missing', '/api/jobs/missing/report.pdf', '/api/crops/missing/missing.jpeg'])
def test_missing_resources(client, path):
    assert client.get(path).status_code == 404


def test_mrxs_crop_tiles(client, monkeypatch, jpeg):
    meta = main.DZIMetadata(width=100, height=80, tile_size=256, overlap=1, level_count=8, level_tiles=[[1, 1]] * 8)
    monkeypatch.setattr(main, 'save_original', lambda *args: 'original')
    monkeypatch.setattr(main, 'build_dzi_metadata', lambda *args: meta)
    slide = client.post('/api/slides', files={'file': ('slide.mrxs', b'mrxs')}).json()
    url = f"/api/slides/{slide['id']}"
    assert 'Width="100"' in client.get(url + '/dzi').text
    monkeypatch.setattr(main, 'get_tile', lambda *args: jpeg)
    assert client.get(url + '/dzi_files/7/0_0.jpeg').content == jpeg
    monkeypatch.setattr(main, 'crop_region', lambda *args: jpeg)
    crop = client.post(url + '/crop', json={'x': 0, 'y': 0, 'width': 10, 'height': 10, 'level': 7}).json()
    assert client.get(crop['image_url']).content == jpeg
    assert client.post(url + '/crop', json={'x': -1, 'y': 0, 'width': 0, 'height': 1, 'level': 0}).status_code == 422
    main.slides[slide['id']].prompt = 'Analyze'
    assert client.post(url + '/analyze', json={'models': ['model-a']}).status_code == 400
    assert client.post(url + '/analyze', json={'models': ['model-a'], 'crop_id': 'missing'}).status_code == 404

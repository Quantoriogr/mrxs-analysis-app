import os
from io import BytesIO
from unittest.mock import Mock

import pytest
from PIL import Image

# Set before app imports; never read developer credentials or contact real services.
for key, value in {
    'MINIO_ENDPOINT': 'localhost:9000', 'MINIO_ACCESS_KEY': 'test-access',
    'MINIO_SECRET_KEY': 'test-secret', 'MINIO_SECURE': 'false', 'MINIO_BUCKET': 'tests',
    'OPENROUTER_API_KEY': 'test-key', 'OPENROUTER_BASE_URL': 'https://example.invalid',
    'OPENROUTER_SITE_URL': 'http://localhost', 'OPENROUTER_APP_NAME': 'tests',
    'ANALYSIS_MODELS': 'model-a,model-b', 'MAX_CROP_PIXELS': '1000000',
    'CORS_ORIGINS': 'http://localhost:5173',
}.items():
    os.environ[key] = value

from backend.app import main
from fastapi.testclient import TestClient


@pytest.fixture
def jpeg():
    output = BytesIO()
    Image.new('RGB', (32, 24), 'red').save(output, 'JPEG')
    return output.getvalue()


@pytest.fixture
def store(monkeypatch):
    objects = {}
    fake = Mock()
    fake.save_object.side_effect = lambda name, data, content_type: objects.__setitem__(name, data)
    fake.get_object.side_effect = objects.__getitem__
    fake.exists.side_effect = objects.__contains__
    fake.objects = objects
    monkeypatch.setattr(main, 'storage', fake)
    return fake


@pytest.fixture
def client(store):
    main.slides.clear()
    main.jobs.clear()
    with TestClient(main.app) as client:
        yield client
    main.slides.clear()
    main.jobs.clear()


@pytest.fixture
def slide(client, jpeg):
    response = client.post('/api/slides', files={'file': ('slide.jpg', jpeg)})
    assert response.status_code == 200
    return response.json()

import json
from types import SimpleNamespace

import httpx
import pytest
from backend.app.services import analysis as a


def valid_result():
    return {'feature_scores': {key: {'score': 2, 'confidence': 'High', 'rationale': 'Visible feature'} for key in a.ALL_FEATURES}, 'highlights': 'Visible findings'}


@pytest.mark.parametrize('score,category', [(None, 'Unclear'), (-1, 'Unclear'), (0, 'HRT3'), (5, 'HRT3'), (6, 'HRT4'), (10, 'HRT4'), (11, 'HRT5'), (15, 'HRT5'), (16, 'HRT6'), (20, 'HRT6'), (21, 'HRT7'), (24, 'HRT7'), (25, 'Unclear')])
def test_categories(score, category):
    assert a.predict_category(score) == category


@pytest.mark.parametrize('field,value', [('score', True), ('score', 4), ('score', '2'), ('confidence', 'Maybe'), ('rationale', '')])
def test_invalid_features(field, value):
    result = valid_result()
    result['feature_scores'][a.ALL_FEATURES[0]][field] = value
    with pytest.raises(ValueError):
        a.validate_feature_scores(result)


def test_valid_and_unknown_scores():
    result = valid_result()
    a.validate_feature_scores(result)
    assert a.calculate_score(result['feature_scores'], a.GLAND_FEATURES) == 6
    assert a.overall_confidence(result['feature_scores']) == 'High'
    result['feature_scores'][a.ALL_FEATURES[0]]['score'] = None
    a.validate_feature_scores(result)
    assert a.calculate_score(result['feature_scores'], a.GLAND_FEATURES) is None
    assert a.overall_confidence({}) == 'Low'
    assert a.overall_confidence({'a': {'confidence': 'Medium'}}) == 'Medium'


async def test_provider_payload_and_parsing():
    async def handler(request):
        payload = json.loads(request.content)
        assert payload['model'] == 'model-a'
        assert payload['messages'][1]['content'][0]['text'] == 'Prompt'
        assert payload['messages'][1]['content'][1]['image_url']['url'] == 'data:image/jpeg;base64,aW1hZ2U='
        assert request.headers['authorization'] == 'Bearer test-key'
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(valid_result())}}], 'usage': {'cost': '0.1234567'}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await a.ask_model(client, 'model-a', b'image', 'Prompt')
    assert result['cost_usd'] == 0.123457


@pytest.mark.parametrize('status,body,error', [(429, {'error': {'message': 'Limit', 'metadata': {'raw': 'Quota'}}}, RuntimeError), (200, {'choices': []}, ValueError)])
async def test_provider_errors(status, body, error):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))) as client:
        with pytest.raises(error):
            await a.ask_model(client, 'model-a', b'image', 'Prompt')


async def test_analysis_partial_failure(monkeypatch):
    async def ask(client, model, image, prompt):
        if model == 'bad':
            raise RuntimeError('Unavailable')
        return {**valid_result(), 'cost_usd': 0.1}
    monkeypatch.setattr(a, 'ask_model', ask)
    slide = SimpleNamespace(id='slide', image_objects=['one', 'two'], prompt='Prompt')
    report = await a.analyze_slide(slide, [b'one', b'two'], ['good', 'bad'])
    assert report['aggregate'] == {'analyses_completed': 2, 'analyses_requested': 4, 'models_completed': 1, 'models_requested': 2, 'images_requested': 2}
    assert len(report['errors']) == 2
    assert report['model_results'][0]['total_score'] == 16
    assert report['model_results'][1]['image_object'] == 'two'


@pytest.mark.parametrize('prompt,objects,images,models', [(None, ['a'], [b'a'], ['m']), ('p', [], [], ['m']), ('p', ['a'], [b'a', b'b'], ['m']), ('p', ['a'], [b'a'], [])])
async def test_analysis_preconditions(prompt, objects, images, models):
    with pytest.raises(ValueError):
        await a.analyze_slide(SimpleNamespace(id='s', prompt=prompt, image_objects=objects), images, models)

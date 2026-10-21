import { beforeEach, expect, it, vi } from 'vitest';
import * as api from '../../frontend/src/api';

const fetchMock = vi.fn();
beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockImplementation(async () => new Response(JSON.stringify({ models: ['model-a'] })));
});

it('loads configured models', async () => {
  expect(await api.getAnalysisModels()).toEqual(['model-a']);
  expect(fetchMock).toHaveBeenCalledWith('/api/config/models');
});

it('uploads slides and prompts as multipart data', async () => {
  const file = new File(['image'], 'slide.jpg');
  await api.uploadSlide(file);
  expect(fetchMock.mock.calls[0][0]).toBe('/api/slides');
  expect(fetchMock.mock.calls[0][1].body.get('file')).toBe(file);
  await api.uploadPrompt('s', file);
  expect(fetchMock.mock.calls[1][0]).toBe('/api/slides/s/prompt');
});

it('rounds crop coordinates and preserves the selected level', async () => {
  await api.createCrop('s', { x: 1.2, y: 2.8, width: 10.4, height: 20.7, level: 4 });
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ x: 1, y: 3, width: 10, height: 21, level: 4 });
});

it.each([-1, 1.5, NaN])('rejects invalid level %s before sending a crop', async level => {
  await expect(api.createCrop('s', { x: 0, y: 0, width: 10, height: 10, level })).rejects.toThrow('Invalid Deep Zoom level');
  expect(fetchMock).not.toHaveBeenCalled();
});

it('submits selected models with an optional crop and polls jobs', async () => {
  await api.startAnalysis('s', ['model-a']);
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ models: ['model-a'], crop_id: null });
  await api.startAnalysis('s', ['model-b'], 'crop');
  expect(JSON.parse(fetchMock.mock.calls[1][1].body).crop_id).toBe('crop');
  await api.getJob('job');
  expect(fetchMock).toHaveBeenLastCalledWith('/api/jobs/job');
});

it('surfaces server errors', async () => {
  fetchMock.mockResolvedValue(new Response('Provider unavailable', { status: 500 }));
  await expect(api.getJob('job')).rejects.toThrow('Provider unavailable');
});

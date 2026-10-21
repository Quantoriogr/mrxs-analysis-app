import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import App from '../../frontend/src/App';
import * as api from '../../frontend/src/api';

vi.mock('../../frontend/src/api', () => ({
  getAnalysisModels: vi.fn(), uploadSlide: vi.fn(), uploadPrompt: vi.fn(),
  createCrop: vi.fn(), startAnalysis: vi.fn(), getJob: vi.fn(),
}));
vi.mock('../../frontend/src/components/SlideViewer', () => ({ default: () => <div>Slide viewer</div> }));

beforeEach(() => {
  vi.mocked(api.getAnalysisModels).mockResolvedValue(['model-a']);
  vi.mocked(api.uploadSlide).mockResolvedValue({ id: 's', filename: 'slide.jpg', slide_type: 'jpeg', image_count: 1, image_urls: ['/image.jpeg'], width: 0, height: 0, level_count: 0, dzi_url: '' });
  vi.mocked(api.uploadPrompt).mockResolvedValue({ filename: 'prompt.txt' });
  vi.mocked(api.startAnalysis).mockResolvedValue({ job_id: 'j', status: 'queued' });
});

async function upload(container: HTMLElement) {
  fireEvent.change(container.querySelector('input[accept=".mrxs,.zip,.jpg,.jpeg"]')!, { target: { files: [new File(['image'], 'slide.jpg')] } });
  await screen.findByAltText('slide.jpg — image 1');
  await waitFor(() => expect(screen.getByRole('button', { name: /Input prompt/ })).toBeEnabled());
  fireEvent.change(container.querySelector('input[accept=".txt,text/plain"]')!, { target: { files: [new File(['Prompt'], 'prompt.txt')] } });
  await waitFor(() => expect(screen.getByRole('button', { name: /3 Analyze/ })).toBeEnabled());
}

it('requires a slide and prompt before enabling analysis', async () => {
  render(<App />);
  expect(screen.getByRole('button', { name: /Input prompt/ })).toBeDisabled();
  expect(screen.getByRole('button', { name: /3 Analyze/ })).toBeDisabled();
  expect(screen.getByText('Download report').closest('a')).toHaveAttribute('aria-disabled', 'true');
  await screen.findByText('model-a');
});

it('uploads, selects models, analyzes, polls and enables the PDF download', async () => {
  const { container } = render(<App />);
  await upload(container);
  const model = screen.getByRole('checkbox');
  fireEvent.click(model);
  expect(screen.getByRole('button', { name: /3 Analyze/ })).toBeDisabled();
  fireEvent.click(model);
  vi.mocked(api.getJob).mockResolvedValue({ id: 'j', status: 'completed', report_pdf_url: '/report.pdf' });
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole('button', { name: /3 Analyze/ }));
  await act(async () => {});
  expect(api.startAnalysis).toHaveBeenCalledWith('s', ['model-a'], undefined);
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(screen.getByRole('link', { name: /Download report/ })).toHaveAttribute('href', '/report.pdf');
  expect(screen.getByText('completed')).toBeInTheDocument();
});

it('shows upload errors', async () => {
  vi.mocked(api.uploadSlide).mockRejectedValueOnce(new Error('Invalid image'));
  const { container } = render(<App />);
  fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [new File(['bad'], 'bad.jpg')] } });
  expect(await screen.findByText('Invalid image')).toBeInTheDocument();
});

it('shows model configuration errors', async () => {
  vi.mocked(api.getAnalysisModels).mockRejectedValueOnce(new Error('Configuration unavailable'));
  render(<App />);
  expect(await screen.findByText('Configuration unavailable')).toBeInTheDocument();
});

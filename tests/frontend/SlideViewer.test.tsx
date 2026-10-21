import React from 'react';
import { act, render } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import SlideViewer from '../../frontend/src/components/SlideViewer';

const state = vi.hoisted(() => ({ handlers: {} as any, viewer: null as any, tracker: null as any }));
vi.mock('openseadragon', () => {
  const create = vi.fn(() => state.viewer);
  return { default: Object.assign(create, {
    Rect: class { constructor(public x: number, public y: number, public width: number, public height: number) {} },
    MouseTracker: class {
      constructor(options: any) { state.handlers = options; return state.tracker; }
    },
  }) };
});

beforeEach(() => {
  state.viewer = {
    canvas: document.createElement('div'),
    viewport: {
      pointFromPixel: (p: any) => p,
      viewportToImageCoordinates: (p: any) => p,
      imageToViewportRectangle: (p: any) => p,
      getZoom: () => 1,
    },
    world: { getItemAt: () => ({ source: { minLevel: 0, maxLevel: 10 }, viewportToImageZoom: () => 0.5 }) },
    setMouseNavEnabled: vi.fn(), addOverlay: vi.fn(), updateOverlay: vi.fn(), removeOverlay: vi.fn(), destroy: vi.fn(),
  };
  state.tracker = { setTracking: vi.fn(), destroy: vi.fn() };
});

const event = (x: number, y: number) => ({ position: { x, y } });

it('clamps reverse drag selections and emits the current zoom level', () => {
  const onSelection = vi.fn();
  render(<SlideViewer slideId="s" width={100} height={80} selectionEnabled onSelection={onSelection} />);
  act(() => {
    state.handlers.pressHandler(event(120, 90));
    state.handlers.dragHandler(event(-20, 10));
    state.handlers.releaseHandler(event(-20, 10));
  });
  expect(onSelection).toHaveBeenCalledWith({ x: 0, y: 10, width: 100, height: 70, level: 9 });
});

it('ignores tiny selections and disabled selection mode', () => {
  const onSelection = vi.fn();
  const { rerender } = render(<SlideViewer slideId="s" width={100} height={80} selectionEnabled onSelection={onSelection} />);
  act(() => {
    state.handlers.pressHandler(event(10, 10));
    state.handlers.releaseHandler(event(11, 11));
  });
  expect(onSelection).not.toHaveBeenCalled();
  rerender(<SlideViewer slideId="s" width={100} height={80} selectionEnabled={false} onSelection={onSelection} />);
  act(() => {
    state.handlers.pressHandler(event(0, 0));
    state.handlers.releaseHandler(event(50, 50));
  });
  expect(onSelection).not.toHaveBeenCalled();
  expect(state.viewer.setMouseNavEnabled).toHaveBeenLastCalledWith(true);
});

it('destroys the viewer and mouse tracker on unmount', () => {
  const { unmount } = render(<SlideViewer slideId="s" width={100} height={80} selectionEnabled onSelection={vi.fn()} />);
  unmount();
  expect(state.tracker.destroy).toHaveBeenCalledOnce();
  expect(state.viewer.destroy).toHaveBeenCalledOnce();
  expect(state.tracker.setTracking).toHaveBeenLastCalledWith(false);
});

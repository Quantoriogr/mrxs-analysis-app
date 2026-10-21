import { useEffect, useRef } from "react";
import OpenSeadragon from "openseadragon";

export type Selection = {
  x: number;
  y: number;
  width: number;
  height: number;
  level: number;
};

type Props = {
  slideId: string;
  width: number;
  height: number;
  selectionEnabled: boolean;
  onSelection: (selection: Selection) => void;
};

type Point = {
  x: number;
  y: number;
};

type TrackerEvent = {
  position: OpenSeadragon.Point;
  originalEvent?: Event;
  preventDefaultAction?: boolean;
};

function clampPoint(point: Point, dimensions: { width: number; height: number }): Point {
  return {
    x: Math.max(0, Math.min(dimensions.width, point.x)),
    y: Math.max(0, Math.min(dimensions.height, point.y)),
  };
}

function imagePointFromTracker(viewer: OpenSeadragon.Viewer, event: TrackerEvent): Point {
  const viewportPoint = viewer.viewport.pointFromPixel(event.position, true);
  const imagePoint = viewer.viewport.viewportToImageCoordinates(viewportPoint);
  return { x: imagePoint.x, y: imagePoint.y };
}

function getCurrentDeepZoomLevel(viewer: OpenSeadragon.Viewer): number {
  const tiledImage = viewer.world.getItemAt(0);
  if (!tiledImage) {
    return 0;
  }

  const source = tiledImage.source as OpenSeadragon.TileSource & {
    minLevel?: number;
    maxLevel?: number;
  };
  const minLevel = Number(source.minLevel ?? 0);
  const maxLevel = Number(source.maxLevel ?? 0);

  const imageZoom = tiledImage.viewportToImageZoom(
    viewer.viewport.getZoom(true),
  );

  const rawLevel = maxLevel + Math.log2(
    Math.max(imageZoom, Math.pow(2, -maxLevel)),
  );

  return Math.max(
    minLevel,
    Math.min(maxLevel, Math.round(rawLevel)),
  );
}

export default function SlideViewer({
  slideId,
  width,
  height,
  selectionEnabled,
  onSelection,
}: Props) {
  const viewerHostRef = useRef<HTMLDivElement>(null);

  const viewerRef = useRef<OpenSeadragon.Viewer | null>(null);
  const trackerRef = useRef<OpenSeadragon.MouseTracker | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);

  const selectionEnabledRef = useRef(selectionEnabled);
  const onSelectionRef = useRef(onSelection);
  const dimensionsRef = useRef({ width, height });

  const drawingRef = useRef(false);
  const startRef = useRef<Point | null>(null);
  const selectionRef = useRef<Selection | null>(null);

  useEffect(() => {
    onSelectionRef.current = onSelection;
  }, [onSelection]);

  useEffect(() => {
    dimensionsRef.current = { width, height };
  }, [width, height]);

  useEffect(() => {
    selectionEnabledRef.current = selectionEnabled;

    const viewer = viewerRef.current;
    const tracker = trackerRef.current;

    if (!viewer || !tracker) {
      return;
    }

    viewer.setMouseNavEnabled(!selectionEnabled);
    tracker.setTracking(selectionEnabled);

    const canvas = viewer.canvas as HTMLElement | undefined;
    if (canvas) {
      canvas.style.cursor = selectionEnabled ? "crosshair" : "default";
    }

    if (!selectionEnabled) {
      drawingRef.current = false;
      startRef.current = null;
    }
  }, [selectionEnabled]);

  useEffect(() => {
    const host = viewerHostRef.current;
    if (!host) {
      return;
    }

    // Make initialization idempotent for Vite/HMR and development remounts.
    // Never leave DOM from a previous OSD instance in this host.
    host.replaceChildren();

    const viewer = OpenSeadragon({
      element: host,
      prefixUrl: "https://openseadragon.github.io/openseadragon/images/",
      showNavigator: true,
      showNavigationControl: true,
      animationTime: 0.8,
      visibilityRatio: 1,
      constrainDuringPan: true,
      tileSources: `/api/slides/${slideId}/dzi`,
    });

    viewerRef.current = viewer;

    const overlay = document.createElement("div");
    overlay.className = "selection-box";
    overlay.style.display = "none";
    overlay.style.pointerEvents = "none";
    overlayRef.current = overlay;

    const drawSelection = (start: Point, current: Point) => {
      const a = clampPoint(start, dimensionsRef.current);
      const b = clampPoint(current, dimensionsRef.current);

      const currentLevel = getCurrentDeepZoomLevel(viewer);

      const selection: Selection = {
        x: Math.min(a.x, b.x),
        y: Math.min(a.y, b.y),
        width: Math.abs(b.x - a.x),
        height: Math.abs(b.y - a.y),
        level: currentLevel,
      };

      selectionRef.current = selection;

      const imageRect = new OpenSeadragon.Rect(
        selection.x,
        selection.y,
        Math.max(selection.width, 1),
        Math.max(selection.height, 1),
      );
      const viewportRect = viewer.viewport.imageToViewportRectangle(imageRect);

      if (!overlay.parentElement) {
        viewer.addOverlay({
          element: overlay,
          location: viewportRect,
          checkResize: false,
        });
      } else {
        viewer.updateOverlay(overlay, viewportRect);
      }

      overlay.style.display = "block";
    };

    const tracker = new OpenSeadragon.MouseTracker({
      element: viewer.canvas,

      pressHandler: (rawEvent) => {
        const event = rawEvent as unknown as TrackerEvent;
        if (!selectionEnabledRef.current) {
          return;
        }

        const mouseEvent = event.originalEvent as MouseEvent | PointerEvent | undefined;
        if (mouseEvent && "button" in mouseEvent && mouseEvent.button !== 0) {
          return;
        }

        event.preventDefaultAction = true;

        const start = clampPoint(imagePointFromTracker(viewer, event), dimensionsRef.current);
        drawingRef.current = true;
        startRef.current = start;
        selectionRef.current = null;
        drawSelection(start, start);
      },

      dragHandler: (rawEvent) => {
        const event = rawEvent as unknown as TrackerEvent;
        if (
          !selectionEnabledRef.current ||
          !drawingRef.current ||
          !startRef.current
        ) {
          return;
        }

        event.preventDefaultAction = true;
        drawSelection(startRef.current, imagePointFromTracker(viewer, event));
      },

      releaseHandler: (rawEvent) => {
        const event = rawEvent as unknown as TrackerEvent;
        if (!drawingRef.current || !startRef.current) {
          return;
        }

        event.preventDefaultAction = true;
        drawSelection(startRef.current, imagePointFromTracker(viewer, event));

        drawingRef.current = false;
        startRef.current = null;

        const selection = selectionRef.current;
        if (!selection || selection.width < 3 || selection.height < 3) {
          overlay.style.display = "none";
          selectionRef.current = null;
          return;
        }

        onSelectionRef.current({
          x: Math.round(selection.x),
          y: Math.round(selection.y),
          width: Math.round(selection.width),
          height: Math.round(selection.height),
          level: getCurrentDeepZoomLevel(viewer),
        });
      },
    });

    trackerRef.current = tracker;

    viewer.setMouseNavEnabled(!selectionEnabledRef.current);
    tracker.setTracking(selectionEnabledRef.current);
    (viewer.canvas as HTMLElement).style.cursor = selectionEnabledRef.current
      ? "crosshair"
      : "default";

    return () => {
      drawingRef.current = false;
      startRef.current = null;
      selectionRef.current = null;

      tracker.setTracking(false);
      tracker.destroy();
      trackerRef.current = null;

      if (overlay.parentElement) {
        viewer.removeOverlay(overlay);
      }
      overlayRef.current = null;

      viewerRef.current = null;
      viewer.destroy();

      host.replaceChildren();
    };
  }, [slideId]);

  return (
    <div className="viewer-shell">
      <div ref={viewerHostRef} className="viewer" />
    </div>
  );
}

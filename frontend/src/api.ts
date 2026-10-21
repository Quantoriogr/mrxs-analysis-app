export type Slide = {
  id: string;
  filename: string;
  width: number;
  height: number;
  level_count: number;
  dzi_url: string;
  slide_type: "mrxs" | "jpeg";
  image_count: number;
  image_urls: string[];
};

export type Selection = {
  x: number;
  y: number;
  width: number;
  height: number;
  level: number;
};

export type Crop = Selection & {
  crop_id: string;
  slide_id: string;
  image_url: string;
};

export type Job = {
  id: string;
  models?: string[];
  status: "queued" | "running" | "completed" | "failed";
  report?: {
    model_results: Array<{
      model: string;
      image_index: number;
      image_object: string;
      feature_scores: Record<string, {
        score: number | null;
        confidence: "Low" | "Medium" | "High";
        rationale: string;
      }>;
      glands_score: number | null;
      stroma_score: number | null;
      total_score: number | null;
      final_prediction_category: string;
      confidence: "Low" | "Medium" | "High";
      highlights: string;
      cost_usd: number;
    }>;
    errors: Array<{
      model: string;
      image_index: number;
      image_object: string;
      error: string;
    }>;
    aggregate: {
      models_completed: number;
      models_requested: number;
      analyses_completed: number;
      analyses_requested: number;
      images_requested: number;
    };
  };
  report_pdf_url?: string;
  error?: string;
};

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || response.statusText);
  }

  return response.json();
}

export async function uploadSlide(file: File): Promise<Slide> {
  const form = new FormData();
  form.append("file", file);

  const response = await fetch("/api/slides", {
    method: "POST",
    body: form,
  });
  return readJson<Slide>(response);
}

export async function getAnalysisModels(): Promise<string[]> {
  const response = await fetch("/api/config/models");
  const result = await readJson<{ models: string[] }>(response);
  return result.models;
}

export async function uploadPrompt(
  slideId: string,
  file: File,
): Promise<{ filename: string }> {
  const form = new FormData();
  form.append("file", file);

  const response = await fetch(`/api/slides/${slideId}/prompt`, {
    method: "POST",
    body: form,
  });
  return readJson<{ filename: string }>(response);
}

export async function createCrop(slideId: string, crop: Selection): Promise<Crop> {
  const level = Number(crop.level);
  if (!Number.isInteger(level) || level < 0) {
    throw new Error(
      `Invalid Deep Zoom level for crop: ${String(crop.level)}. ` +
      "Reload the page and make a new selection.",
    );
  }

  const payload = {
    x: Math.round(crop.x),
    y: Math.round(crop.y),
    width: Math.round(crop.width),
    height: Math.round(crop.height),
    level,
  };

  const response = await fetch(`/api/slides/${slideId}/crop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson<Crop>(response);
}

export async function startAnalysis(
  slideId: string,
  models: string[],
  cropId?: string,
): Promise<{ job_id: string; status: string }> {
  const response = await fetch(`/api/slides/${slideId}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ crop_id: cropId ?? null, models }),
  });
  return readJson<{ job_id: string; status: string }>(response);
}

export async function getJob(jobId: string): Promise<Job> {
  const response = await fetch(`/api/jobs/${jobId}`);
  return readJson<Job>(response);
}

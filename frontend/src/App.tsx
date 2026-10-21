import { useCallback, useEffect, useRef, useState } from "react";
import SlideViewer, { Selection } from "./components/SlideViewer";
import {
  createCrop,
  getAnalysisModels,
  getJob,
  startAnalysis,
  uploadPrompt,
  uploadSlide,
  type Crop,
  type Job,
  type Slide,
} from "./api";

export default function App() {
  const slideInput = useRef<HTMLInputElement>(null);
  const promptInput = useRef<HTMLInputElement>(null);
  const [slide, setSlide] = useState<Slide | null>(null);
  const [promptName, setPromptName] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [selectionEnabled, setSelectionEnabled] = useState(false);
  const [crop, setCrop] = useState<Crop | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [selectedModels, setSelectedModels] = useState<string[]>([]);

  useEffect(() => {
    let active = true;
    getAnalysisModels()
      .then((models) => {
        if (active) {
          setAvailableModels(models);
          setSelectedModels(models);
        }
      })
      .catch((e) => {
        if (active) {
          setError(e instanceof Error ? e.message : String(e));
        }
      });
    return () => {
      active = false;
    };
  }, []);

  async function onSlide(file: File) {
    setBusy(true);
    setError(null);
    setSlide(null);
    setPromptName(null);
    setCrop(null);
    setJob(null);
    setSelection(null);
    setSelectionEnabled(false);

    try {
      setSlide(await uploadSlide(file));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onPrompt(file: File) {
    if (!slide) {
      return;
    }
    if (!file.name.toLowerCase().endsWith(".txt")) {
      setError("The prompt must be a plain-text (.txt) file.");
      return;
    }
    setBusy(true);
    setError(null);
    setJob(null);

    try {
      const result = await uploadPrompt(slide.id, file);
      setPromptName(result.filename);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const onSelection = useCallback(async (next: Selection) => {
    if (!slide) {
      return;
    }
    setSelection(next);
    setError(null);
    setBusy(true);

    try {
      setCrop(await createCrop(slide.id, next));
      setJob(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [slide]);

  async function analyze() {
    if (
      !slide ||
      !promptName ||
      selectedModels.length === 0 ||
      (slide.slide_type === "mrxs" && !crop)
    ) {
      return;
    }
    setBusy(true);
    setError(null);

    try {
      const started = await startAnalysis(slide.id, selectedModels, crop?.crop_id);
      setJob({ id: started.job_id, status: "queued" });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) {
      if (job) {
        setBusy(false);
      }
      return;
    }

    const timer = window.setInterval(async () => {

      try {
        setJob(await getJob(job.id));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setBusy(false);
      }
    }, 1500);

    return () => window.clearInterval(timer);
  }, [job]);

  let primaryStep: "slide" | "prompt" | "download" | "analyze";

  if (!slide) {
    primaryStep = "slide";
  } else if (!promptName) {
    primaryStep = "prompt";
  } else if (job?.report_pdf_url) {
    primaryStep = "download";
  } else {
    primaryStep = "analyze";
  }

  let analysisHint = "Ready";
  if (slide?.slide_type === "mrxs" && !crop) {
    analysisHint = "Select a crop first";
  } else if (!promptName) {
    analysisHint = "Add a prompt first";
  } else if (selectedModels.length === 0) {
    analysisHint = "Select at least one model";
  }

  const analyzeReady =
    !!slide &&
    !!promptName &&
    selectedModels.length > 0 &&
    (slide.slide_type === "jpeg" || !!crop);

  function acceptDropped(files: FileList) {
    const items = Array.from(files);
    const prompt = items.find((file) => file.name.toLowerCase().endsWith(".txt"));
    const image = items.find((file) => !file.name.toLowerCase().endsWith(".txt"));
    if (image) {
      void onSlide(image);
    } else if (prompt && slide) {
      void onPrompt(prompt);
    }
  }

  let preview;

  if (!slide) {
    preview = (
      <div className="empty">
        <h2>Drop an input slide here</h2>
        <p>Upload an MRXS, JPEG, or a ZIP file.</p>
      </div>
    );
  } else if (slide.slide_type === "mrxs") {
    preview = (
      <>
        <div className="viewer-panel">
          <SlideViewer
            slideId={slide.id}
            width={slide.width}
            height={slide.height}
            selectionEnabled={selectionEnabled}
            onSelection={onSelection}
          />
        </div>
        <div className="slide-details">
          <div>
            <strong>{slide.filename}</strong>
            <span>
              {slide.width.toLocaleString()} x {slide.height.toLocaleString()} · {slide.level_count} levels
            </span>
          </div>
          <label className="toggle-control">
            <input
              type="checkbox"
              checked={selectionEnabled}
              onChange={(event) => setSelectionEnabled(event.target.checked)}
            />
            <span className="toggle-slider" aria-hidden="true" />
            <span>{selectionEnabled ? "Crop enabled" : "Enable crop"}</span>
          </label>
          {selection && (
            <span className="selection-summary">
              {selection.width} × {selection.height}px selected
            </span>
          )}
        </div>
      </>
    );
  } else {
    preview = (
      <>
        <div className="image-previews" aria-label="Uploaded images">
          {slide.image_urls.map((url, index) => (
            <img
              key={url}
              src={url}
              alt={`${slide.filename} — image ${index + 1}`}
              loading="lazy"
              onError={() => setError(`Could not load preview for image ${index + 1}.`)}
            />
          ))}
        </div>
        <div className="slide-details">
          <div>
            <strong>{slide.filename}</strong>
            <span>{slide.image_count} JPEG image{slide.image_count === 1 ? "" : "s"}</span>
          </div>
        </div>
      </>
    );
  }

  return (
    <main className="app">
      <header>
        <div>
          <h1>MRXS Analysis Workbench</h1>
          <p>Upload → prompt → analyze → report</p>
        </div>
      </header>
      {error && <div className="error">{error}</div>}

      <section className="workspace">
        <div
          className={`main-panel ${dragging ? "is-dragging" : ""}`}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            acceptDropped(event.dataTransfer.files);
          }}
        >
          {preview}
          {dragging && <div className="drop-overlay">Drop file to upload</div>}
        </div>

        <aside className="action-panel">
          <input
            ref={slideInput}
            hidden
            type="file"
            accept=".mrxs,.zip,.jpg,.jpeg"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                void onSlide(file);
              }
              event.target.value = "";
            }}
          />
          <input
            ref={promptInput}
            hidden
            type="file"
            accept=".txt,text/plain"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                void onPrompt(file);
              }
              event.target.value = "";
            }}
          />

          <button
            className={`action-button ${primaryStep === "slide" ? "primary" : ""}`}
            disabled={busy}
            onClick={() => slideInput.current?.click()}
          >
            <span>1</span>
            <div>
              Input slide
              <small>{slide?.filename ?? "MRXS, JPG or ZIP"}</small>
            </div>
          </button>
          <button
            className={`action-button ${primaryStep === "prompt" ? "primary" : ""}`}
            disabled={busy || !slide}
            onClick={() => promptInput.current?.click()}
          >
            <span>2</span>
            <div>
              Input prompt
              <small>{promptName ?? "Plain-text file"}</small>
            </div>
          </button>
          <details className={`model-select ${promptName ? "" : "disabled"}`}>
            <summary
              aria-disabled={!promptName}
              onClick={(event) => {
                if (!promptName) {
                  event.preventDefault();
                }
              }}
            >
              Analysis models
              <small>
                {!promptName
                  ? "Load a prompt file first"
                  : availableModels.length === 0
                    ? "No models available"
                    : selectedModels.length === availableModels.length
                      ? `All ${availableModels.length} selected`
                      : `${selectedModels.length} of ${availableModels.length} selected`}
              </small>
            </summary>
            <div className="model-options">
              {availableModels.length === 0 ? (
                <span className="model-empty">No models configured</span>
              ) : (
                availableModels.map((model) => (
                  <label key={model}>
                    <input
                      type="checkbox"
                      checked={selectedModels.includes(model)}
                      disabled={busy || !promptName}
                      onChange={(event) => {
                        setSelectedModels((current) =>
                          event.target.checked
                            ? [...current, model]
                            : current.filter((selected) => selected !== model),
                        );
                      }}
                    />
                    <span>{model}</span>
                  </label>
                ))
              )}
            </div>
          </details>
          <button
            className={`action-button ${primaryStep === "analyze" ? "primary" : ""}`}
            disabled={busy || !analyzeReady}
            onClick={() => void analyze()}
          >
            <span>3</span>
            <div>
              {busy && job ? "Analyzing…" : "Analyze"}
              <small>{analysisHint}</small>
            </div>
          </button>

          {job && (
            <p className={`job-status ${job.status}`}>
              Analysis: <strong>{job.status}</strong>
            </p>
          )}

          <a
            className={`action-button download ${primaryStep === "download" ? "primary" : ""} ${job?.report_pdf_url ? "" : "disabled"}`}
            href={job?.report_pdf_url}
            download
            aria-disabled={!job?.report_pdf_url}
            onClick={(event) => {
              if (!job?.report_pdf_url) {
                event.preventDefault();
              }
            }}
          >
            <span>4</span>
            <div>
              Download report
              <small>{job?.report_pdf_url ? "PDF ready" : "Available after analysis"}</small>
            </div>
          </a>
          {crop && <img className="crop-preview" src={crop.image_url} alt="Selected crop" />}
          {job?.error && <div className="error">{job.error}</div>}
        </aside>
      </section>
    </main>
  );
}

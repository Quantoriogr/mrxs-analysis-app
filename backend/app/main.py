from pathlib import Path
from io import BytesIO
import zipfile
from uuid import uuid4
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image

from .config import config, MAX_JPEG_COUNT, MAX_JPEG_SIZE, MAX_JPEG_QUALITY, SUPPORTED_FILETYPES
from .services.analysis import analyze_slide
from .services.report import build_report_pdf
from .services.deepzoom import (
    build_dzi_metadata,
    crop_region,
    wrap_dzi_xml,
    get_tile,
    save_original,
    DZIMetadata,
)
from .storage import storage

app = FastAPI(title="MRXS Analysis API", version="0.1.0")


app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CropRequest(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    level: int = Field(ge=0)


class AnalysisRequest(BaseModel):
    crop_id: str | None = None
    models: list[str] = Field(default_factory=list)


class Slide(BaseModel):
    id: str
    filename: str
    original_object: str | None
    meta: DZIMetadata | None
    slide_type: str
    image_objects: list[str] = Field(default_factory=list)
    prompt: str | None = None
    prompt_filename: str | None = None
    created_at: str


class Job(BaseModel):
    id: str
    slide_id: str
    crop_id: str | None = None
    models: list[str] = Field(default_factory=list)
    status: str
    report: dict | None = None
    error: str | None = None
    report_pdf_url: str | None = None
    created_at: str


# TODO: Replace in-memory storage with external cache later.
slides: dict[str, Slide] = {}
jobs: dict[str, Job] = {}


@app.on_event("startup")
def startup() -> None:
    storage.ensure_bucket()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config/models")
def get_analysis_models() -> dict:
    return {"models": config.models}


async def _handle_jpeg_file(filename: str, data: bytes) -> tuple[str, list[tuple[str, bytes]]]:
    """Process a single JPEG file for upload."""
    return "jpeg", [(filename, data)]


async def _handle_zip_file(data: bytes) -> tuple[str, list[tuple[str, bytes]]]:
    """Process a ZIP file containing either MRXS package or JPEG images."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            files = []
            has_mrxs = False

            for item in archive.infolist():
                # Skip directories, hidden files, and unsupported file types
                if (
                    item.is_dir()
                    or item.filename.lower().startswith((".", "__"))
                    or not item.filename.lower().endswith(SUPPORTED_FILETYPES)
                ):
                    continue
                else:
                    files.append(item)
                    has_mrxs = has_mrxs or item.filename.lower().endswith(".mrxs")

            if not has_mrxs:
                if not files:
                    raise ValueError("ZIP must contain an MRXS package or JPEG images")
                if len(files) > MAX_JPEG_COUNT or sum(file.file_size for file in files) > MAX_JPEG_SIZE:
                    raise ValueError(f"JPEG ZIP is too large (maximum {MAX_JPEG_COUNT} images / {MAX_JPEG_SIZE} bytes)")
                slide_items = [(Path(file.filename).name, archive.read(file)) for file in files]
                return "jpeg", slide_items

            return "mrxs", []  # Currently only 1 .mrxs per zip file is supported;
    except (zipfile.BadZipFile, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


async def _process_mrxs(slide_id: str, filename: str, data: bytes) -> tuple[str | None, DZIMetadata]:
    """Process MRXS file and build DZI metadata."""
    original_object = save_original(slide_id, filename, data)
    try:
        meta = build_dzi_metadata(slide_id, original_object)
        return original_object, meta
    except Exception as exc:
        raise HTTPException(400, f"Could not open MRXS: {exc}") from exc


async def _process_jpeg_images(slide_id: str, slide_items: list[tuple[str, bytes]]) -> list[str]:
    """Process JPEG images and store them."""
    image_objects: list[str] = []
    for index, (_, image_data) in enumerate(slide_items):
        try:
            image = Image.open(BytesIO(image_data)).convert("RGB")
            output = BytesIO()
            image.save(output, "JPEG", quality=MAX_JPEG_QUALITY)
        except Exception as exc:
            raise HTTPException(400, f"Invalid JPEG image: {exc}") from exc
        object_name = f"{slide_id}/images/{index}.jpeg"
        storage.save_object(object_name, output.getvalue(), "image/jpeg")
        image_objects.append(object_name)
    return image_objects


@app.post("/api/slides")
async def upload_slide(file: UploadFile = File(...)) -> dict:
    filename = Path(file.filename or "slide.mrxs").name

    if not filename.lower().endswith(SUPPORTED_FILETYPES):
        raise HTTPException(400, "Upload an MRXS, JPG/JPEG, or supported ZIP file")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty upload")

    slide_id = str(uuid4())
    original_object = None
    meta: DZIMetadata | None = None
    image_objects: list[str] = []

    # Determine file type and get file items
    if filename.lower().endswith((".jpg", ".jpeg")):
        slide_type, slide_items = await _handle_jpeg_file(filename, data)
    elif filename.lower().endswith(".zip"):
        slide_type, slide_items = await _handle_zip_file(data)
    else:
        slide_type = "mrxs"
        slide_items = []

    # Process files based on slide type
    if slide_type == "mrxs":
        original_object, meta = await _process_mrxs(slide_id, filename, data)
    else:
        image_objects = await _process_jpeg_images(slide_id, slide_items)

    # Store slide metadata
    slides[slide_id] = Slide(
        id=slide_id,
        filename=filename,
        original_object=original_object,
        meta=meta,
        slide_type=slide_type,
        image_objects=image_objects,
        prompt=None,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    return {
        "id": slide_id,
        "filename": filename,
        "width": meta.width if meta else 0,
        "height": meta.height if meta else 0,
        "level_count": meta.level_count if meta else 0,
        "dzi_url": f"/api/slides/{slide_id}/dzi",
        "slide_type": slide_type,
        "image_count": len(image_objects) if slide_type == "jpeg" else 1,
        "image_urls": [f"/api/slides/{slide_id}/images/{index}.jpeg" for index in range(len(image_objects))],
    }


@app.post("/api/slides/{slide_id}/prompt")
async def upload_prompt(slide_id: str, file: UploadFile = File(...)) -> dict:
    slide = get_slide(slide_id)
    filename = Path(file.filename or "prompt.txt").name
    if not filename.lower().endswith(".txt"):
        raise HTTPException(400, "Upload a plain-text (.txt) prompt file")
    data = await file.read()
    if not data or len(data) > 1_000_000:
        raise HTTPException(400, "Prompt must be non-empty and no larger than 1 MB")
    try:
        prompt_content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "Prompt must be UTF-8 encoded") from exc
    if not prompt_content.strip():
        raise HTTPException(400, "Prompt must contain non-whitespace text")

    # Keep the decoded prompt on the Slide so the analysis task receives the
    # content itself rather than a filename or storage reference.
    slide.prompt = prompt_content
    slide.prompt_filename = filename
    return {"filename": filename}


def get_slide(slide_id: str) -> Slide:
    slide = slides.get(slide_id)
    if not slide:
        raise HTTPException(404, "Slide not found")
    return slide


@app.get("/api/slides/{slide_id}/images/{index}.jpeg")
def get_slide_image(slide_id: str, index: int) -> Response:
    slide = get_slide(slide_id)
    if slide.slide_type != "jpeg" or index < 0 or index >= len(slide.image_objects):
        raise HTTPException(404, "Image not found")
    try:
        data = storage.get_object(slide.image_objects[index])
    except Exception as exc:
        raise HTTPException(404, "Image not found") from exc
    return Response(data, media_type="image/jpeg")


@app.get("/api/slides/{slide_id}/dzi")
def get_dzi(slide_id: str) -> Response:
    slide = get_slide(slide_id)
    if not slide.meta:
        raise HTTPException(400, "DZI is only available for MRXS slides")
    return Response(wrap_dzi_xml(slide.meta), media_type="application/xml")


@app.get("/api/slides/{slide_id}/dzi_files/{level}/{col}_{row}.jpeg")
def get_slide_tile(slide_id: str, level: int, col: int, row: int) -> Response:
    slide = get_slide(slide_id)

    try:
        data = get_tile(
            slide_id,
            slide.original_object,
            level,
            col,
            row,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        import traceback

        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=(f"Tile generation failed: " f"{type(exc).__name__}: {exc}"),
        ) from exc

    return Response(
        content=data,
        media_type="image/jpeg",
    )


@app.post("/api/slides/{slide_id}/crop")
def create_crop(slide_id: str, request: CropRequest) -> dict:
    slide = get_slide(slide_id)

    try:
        jpeg = crop_region(
            slide_id,
            slide.original_object,
            request.x,
            request.y,
            request.width,
            request.height,
            request.level,
            config.max_crop_pixels,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Crop failed: {exc}") from exc

    crop_id = str(uuid4())
    object_name = f"{slide_id}/crops/{crop_id}.jpeg"
    storage.save_object(object_name, jpeg, "image/jpeg")

    return {
        "crop_id": crop_id,
        "slide_id": slide_id,
        "x": request.x,
        "y": request.y,
        "width": request.width,
        "height": request.height,
        "level": request.level,
        "image_url": f"/api/crops/{slide_id}/{crop_id}.jpeg",
        "object_name": object_name,
    }


@app.get("/api/crops/{slide_id}/{crop_id}.jpeg")
def get_crop(slide_id: str, crop_id: str) -> Response:
    object_name = f"{slide_id}/crops/{crop_id}.jpeg"
    try:
        data = storage.get_object(object_name)
    except Exception as exc:
        raise HTTPException(404, "Crop not found") from exc
    return Response(data, media_type="image/jpeg")


async def execute_analysis(job_id: str, slide: Slide, models: list[str]) -> None:
    try:
        jobs[job_id].status = "running"
        images = [storage.get_object(name) for name in slide.image_objects]
        report = await analyze_slide(slide, images, models)
        pdf = build_report_pdf(images, report)
        pdf_object = f"{slide.id}/reports/{job_id}.pdf"
        storage.save_object(pdf_object, pdf, "application/pdf")

        jobs[job_id].status = "completed"
        jobs[job_id].report = report
        jobs[job_id].report_pdf_url = f"/api/jobs/{job_id}/report.pdf"
    except Exception as exc:
        jobs[job_id].status = "failed"
        jobs[job_id].error = str(exc)


@app.post("/api/slides/{slide_id}/analyze")
async def analyze(slide_id: str, request: AnalysisRequest, background_tasks: BackgroundTasks) -> dict:
    slide = get_slide(slide_id)
    if not slide.prompt:
        raise HTTPException(400, "Upload a plain-text prompt before analysis")
    if not request.models:
        raise HTTPException(400, "Select at least one analysis model")
    if len(request.models) != len(set(request.models)):
        raise HTTPException(400, "Analysis model selection contains duplicates")
    unknown_models = [model for model in request.models if model not in config.models]
    if unknown_models:
        raise HTTPException(400, f"Unknown analysis models: {', '.join(unknown_models)}")
    if slide.slide_type == "mrxs":
        if not request.crop_id:
            raise HTTPException(400, "Select a crop before analysis")
        image_objects = [f"{slide_id}/crops/{request.crop_id}.jpeg"]
        if not storage.exists(image_objects[0]):
            raise HTTPException(404, "Crop not found")
    else:
        image_objects = slide.image_objects

    job_id = str(uuid4())
    jobs[job_id] = Job(
        id=job_id,
        slide_id=slide_id,
        crop_id=request.crop_id,
        models=list(request.models),
        status="queued",
        report=None,
        error=None,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    analysis_slide = slide.model_copy(update={"image_objects": image_objects})
    background_tasks.add_task(execute_analysis, job_id, analysis_slide, list(request.models))
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.model_dump()


@app.get("/api/jobs/{job_id}/report.pdf")
def get_report_pdf(job_id: str) -> Response:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != "completed":
        raise HTTPException(409, "Report is not ready")

    object_name = f"{job.slide_id}/reports/{job_id}.pdf"
    try:
        data = storage.get_object(object_name)
    except Exception as exc:
        raise HTTPException(404, "Report not found") from exc

    return Response(
        data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="mrxs-report-{job_id}.pdf"',
        },
    )


frontend_directory = Path(__file__).resolve().parent.parent / "static"
if frontend_directory.is_dir():
    app.mount("/", StaticFiles(directory=frontend_directory, html=True), name="frontend")

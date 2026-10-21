import asyncio
import base64
import json
from typing import Protocol

import httpx

from ..config import config

SYSTEM_PROMPT = """
You are an expert medical image analyst. Analyze only what is visible in the
provided image, follow the user's instructions, and return the requested JSON.
Do not invent clinical history or findings that cannot be supported by the image.
""".strip()

GLAND_FEATURES = [
    "glandular_morphology",
    "glandular_secretion",
    "glandular_lumen_size",
]

STROMA_FEATURES = [
    "stromal_cell_morphology",
    "stromal_edema",
    "stromal_cell_density",
    "vascular_development",
    "immune_cell_infiltration",
]

ALL_FEATURES = GLAND_FEATURES + STROMA_FEATURES

FEATURE_LABELS = {
    "glandular_morphology": "Glandular morphology",
    "glandular_secretion": "Glandular secretion",
    "glandular_lumen_size": "Glandular lumen size",
    "stromal_cell_morphology": "Stromal cell morphology",
    "stromal_edema": "Stromal edema",
    "stromal_cell_density": "Stromal cell density",
    "vascular_development": "Vascular development",
    "immune_cell_infiltration": "Immune cell infiltration",
}

FEATURE_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "anyOf": [
                {"type": "integer", "enum": [0, 1, 2, 3]},
                {"type": "null"},
            ]
        },
        "confidence": {"type": "string", "enum": ["Low", "Medium", "High"]},
        "rationale": {"type": "string"},
    },
    "required": ["score", "confidence", "rationale"],
    "additionalProperties": False,
}

ANALYSIS_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "image_analysis",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "feature_scores": {
                    "type": "object",
                    "properties": {feature: FEATURE_SCORE_SCHEMA for feature in ALL_FEATURES},
                    "required": ALL_FEATURES,
                    "additionalProperties": False,
                },
                "highlights": {"type": "string"},
            },
            "required": ["feature_scores", "highlights"],
            "additionalProperties": False,
        },
    },
}


class AnalysisSlide(Protocol):
    id: str
    image_objects: list[str]
    prompt: str | None


def _data_url(image_bytes: bytes) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


async def ask_model(
    client: httpx.AsyncClient,
    model: str,
    image_bytes: bytes,
    prompt: str,
) -> dict:
    """Send one image to one OpenRouter model and return its parsed response."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _data_url(image_bytes)}},
                ],
            },
        ],
        "max_tokens": 4096,
        "response_format": ANALYSIS_RESPONSE_FORMAT,
        "provider": {"require_parameters": True},
    }
    headers = {
        "Authorization": f"Bearer {config.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": config.openrouter_site_url,
        "X-Title": config.openrouter_app_name,
    }

    response = await client.post(
        f"{config.openrouter_base_url.rstrip('/')}/chat/completions",
        headers=headers,
        json=payload,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        try:
            error_data = response.json()
            detail = error_data.get("error", error_data)
            if isinstance(detail, dict):
                message_detail = detail.get("message")
                metadata = detail.get("metadata")
                provider_detail = None
                if isinstance(metadata, dict):
                    provider_detail = metadata.get("raw") or metadata.get("message")
                detail = message_detail or json.dumps(detail)
                if provider_detail and provider_detail != detail:
                    detail = f"{detail}: {provider_detail}"
        except (ValueError, TypeError):
            detail = response.text.strip()
        message = f"OpenRouter returned HTTP {response.status_code} for model {model}"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message) from exc
    data = response.json()

    try:
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content) if isinstance(content, str) else content
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("OpenRouter returned an invalid analysis response") from exc

    validate_feature_scores(parsed)
    usage = data.get("usage") or {}
    try:
        cost = float(usage.get("cost") or 0.0)
    except (TypeError, ValueError):
        cost = 0.0

    return {**parsed, "model": model, "cost_usd": round(cost, 6)}


def validate_feature_scores(parsed: object) -> None:
    """Validate the domain feature scores even when provider validation is unavailable."""
    if not isinstance(parsed, dict):
        raise ValueError("Analysis response must be an object")
    feature_scores = parsed.get("feature_scores", {})
    if not isinstance(feature_scores, dict):
        raise ValueError("Analysis response has invalid feature scores")

    missing = [feature for feature in ALL_FEATURES if feature not in feature_scores]
    if missing:
        raise ValueError(f"Missing feature scores: {missing}")

    for feature in ALL_FEATURES:
        data = feature_scores[feature]
        if not isinstance(data, dict):
            raise ValueError(f"Invalid feature score for {feature}")
        score = data.get("score")
        confidence = data.get("confidence")
        rationale = data.get("rationale")

        if score is not None and (not isinstance(score, int) or isinstance(score, bool) or score not in [0, 1, 2, 3]):
            raise ValueError(f"Invalid score for {feature}: {score}")
        if confidence not in ["Low", "Medium", "High"]:
            raise ValueError(f"Invalid confidence for {feature}: {confidence}")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError(f"Missing rationale for {feature}")

    highlights = parsed.get("highlights")
    if not isinstance(highlights, str) or not highlights.strip():
        raise ValueError("Missing highlights paragraph")


def calculate_score(feature_scores: dict, feature_names: list[str]) -> int | None:
    values = [feature_scores.get(feature, {}).get("score") for feature in feature_names]
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
        return None
    return sum(values)


def predict_category(total_score: int | None) -> str:
    if total_score is None:
        return "Unclear"
    for lower, upper, category in [
        (0, 5, "HRT3"),
        (6, 10, "HRT4"),
        (11, 15, "HRT5"),
        (16, 20, "HRT6"),
        (21, 24, "HRT7"),
    ]:
        if lower <= total_score <= upper:
            return category
    return "Unclear"


def overall_confidence(feature_scores: dict) -> str:
    confidences = [data.get("confidence", "Low") for data in feature_scores.values()]
    if not confidences or confidences.count("Low") >= 3:
        return "Low"
    if confidences.count("High") >= 5:
        return "High"
    return "Medium"


async def analyze_slide(slide: AnalysisSlide, image_bytes: list[bytes], models: list[str]) -> dict:
    """Analyze every slide image with every configured model."""
    if not config.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    if not slide.prompt or not slide.prompt.strip():
        raise ValueError("The slide does not have an analysis prompt")
    if not image_bytes:
        raise ValueError("The slide does not contain any images to analyze")
    if len(image_bytes) != len(slide.image_objects):
        raise ValueError("Loaded image count does not match the slide image count")
    if not models:
        raise ValueError("At least one analysis model must be selected")

    requests = [
        (model, image_index, object_name, image)
        for model in models
        for image_index, (object_name, image) in enumerate(zip(slide.image_objects, image_bytes))
    ]

    async with httpx.AsyncClient(timeout=180) as client:
        responses = await asyncio.gather(
            *(ask_model(client, model, image, slide.prompt) for model, _, _, image in requests),
            return_exceptions=True,
        )

    results: list[dict] = []
    errors: list[dict] = []
    for (model, image_index, object_name, _), response in zip(requests, responses):
        identity = {"model": model, "image_index": image_index, "image_object": object_name}
        if isinstance(response, BaseException):
            errors.append({**identity, "error": str(response)})
        else:
            feature_scores = response["feature_scores"]
            glands_score = calculate_score(feature_scores, GLAND_FEATURES)
            stroma_score = calculate_score(feature_scores, STROMA_FEATURES)
            total_score = glands_score + stroma_score if glands_score is not None and stroma_score is not None else None
            results.append(
                {
                    **identity,
                    "feature_scores": feature_scores,
                    "glands_score": glands_score,
                    "stroma_score": stroma_score,
                    "total_score": total_score,
                    "final_prediction_category": predict_category(total_score),
                    "confidence": overall_confidence(feature_scores),
                    "highlights": response["highlights"],
                    "cost_usd": response["cost_usd"],
                }
            )

    return {
        "slide_id": slide.id,
        "model_results": results,
        "errors": errors,
        "aggregate": {
            "analyses_completed": len(results),
            "analyses_requested": len(requests),
            "models_completed": len({result["model"] for result in results}),
            "models_requested": len(models),
            "images_requested": len(image_bytes),
        },
    }

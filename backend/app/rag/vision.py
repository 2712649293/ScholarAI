"""Optional image descriptions with per-document deadline and content cache."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
from pathlib import Path
import time

import httpx

from app.config import settings
from app.rag.markdown_pdf import Figure, ParsedPDF

PROMPT = (
    "描述这张论文图片的可见内容，用于学术检索。包括图类型、轴、图例、实体和可见趋势。"
    "不要编造精确数值或作者结论；看不清的内容明确说明。最多300字。"
)
PROMPT_VERSION = "figure-v1"


async def _describe(client: httpx.AsyncClient, figure: Figure) -> str:
    data = Path(figure.path).read_bytes()
    mime = mimetypes.guess_type(figure.path)[0] or "image/png"
    url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    text = f"{PROMPT}\n作者图注：{figure.caption}\n邻近正文：{figure.context}"
    if settings.vision_api_style == "responses":
        endpoint = "responses"
        payload = {"model": settings.vision_model, "input": [{"role": "user", "content": [
            {"type": "input_text", "text": text}, {"type": "input_image", "image_url": url},
        ]}], "max_output_tokens": 600}
    elif settings.vision_api_style == "chat_completions":
        endpoint = "chat/completions"
        payload = {"model": settings.vision_model, "messages": [{"role": "user", "content": [
            {"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": url}},
        ]}], "max_completion_tokens": 600}
    else:
        raise ValueError("VISION_API_STYLE must be responses or chat_completions")
    response = await client.post(f"{settings.vision_base_url.rstrip('/')}/{endpoint}", json=payload)
    response.raise_for_status()
    result = response.json()
    if endpoint == "responses":
        description = "\n".join(
            c["text"] for o in result.get("output", []) for c in o.get("content", [])
            if c.get("type") == "output_text"
        )
    else:
        description = result["choices"][0]["message"]["content"]
    if not isinstance(description, str) or not description.strip():
        raise ValueError("vision returned no description")
    return description.strip()[:4000]


async def enrich(parsed: ParsedPDF, cache_dir: Path) -> None:
    if not settings.vision_base_url or not settings.vision_model:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(settings.vision_max_concurrency)
    headers = {"Authorization": f"Bearer {settings.vision_api_key}"} if settings.vision_api_key else {}
    async with httpx.AsyncClient(timeout=settings.vision_timeout_seconds, headers=headers) as client:
        async def one(figure: Figure) -> None:
            async with semaphore:
                try:
                    key = hashlib.sha256(Path(figure.path).read_bytes() + json.dumps([
                        figure.caption, figure.context, settings.vision_model,
                        settings.vision_base_url, settings.vision_api_style, PROMPT_VERSION,
                    ], ensure_ascii=False).encode()).hexdigest()
                    cache = cache_dir / f"{key}.json"
                    if cache.exists():
                        cached = json.loads(cache.read_text())
                        figure.description = cached["description"]
                        figure.description_created_at = cached["created_at"]
                        figure.status = "cached"
                    else:
                        figure.description = await _describe(client, figure)
                        figure.description_created_at = time.time()
                        cache.write_text(json.dumps({"description": figure.description,
                            "model": settings.vision_model, "prompt_version": PROMPT_VERSION,
                            "created_at": figure.description_created_at}, ensure_ascii=False))
                        figure.status = "generated"
                    figure.model = settings.vision_model
                    figure.prompt_version = PROMPT_VERSION
                except asyncio.CancelledError:
                    figure.status = "timeout"
                    raise
                except Exception:
                    figure.status = "failed"
                    parsed.warnings.append(f"{figure.figure_id}: 图片描述失败，保留原图与图注")

        tasks = [asyncio.create_task(one(f)) for f in parsed.figures[:settings.vision_max_images_per_doc]]
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=settings.vision_document_budget_seconds)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
            if pending:
                parsed.warnings.append("图片描述达到文档时间预算，继续索引")
    for figure in parsed.figures:
        if not figure.description:
            continue
        for page in parsed.pages:
            if page.page != figure.page:
                continue
            pattern = rf"(<!-- figure:{re_escape(figure.figure_id)} -->\n!\[[^\]]*\]\([^\n]+\))"
            import re
            page.text = re.sub(pattern, lambda m: m.group(0) +
                f"\n\n> [AI 图片描述 · {figure.model}] " + figure.description.replace("\n", "\n> "), page.text)


def re_escape(value: str) -> str:
    import re
    return re.escape(value)

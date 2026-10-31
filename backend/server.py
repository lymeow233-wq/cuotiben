#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
试卷导入 Agent —— 本地接入服务

把用户空间里的 paper_import_agent.py（原样保留，未改一行）包成 HTTP 接口，
前端页面「拍照 / 粘贴文本 → 识别 → 逐题确认 → 画像 → 抽题」全部走真实链路。

运行：
    export OPENAI_API_KEY=sk-...          # 可选；不配就走规则兜底
    export OPENAI_BASE_URL=https://.../v1 # 可选
    export OPENAI_MODEL=gpt-4o-mini       # 可选
    python3 server.py                     # → http://127.0.0.1:8000

接口：
    GET  /api/health        运行环境（是否配了模型、OCR 语言包、引擎题量）
    GET  /api/meta          知识点 / 错误机制 / 难度档枚举
    POST /api/import/text   {text, exam_date, results?}
    POST /api/import/image  multipart: file, exam_date
    POST /api/recommend     {attempts, originals?, n, seed?}
"""

from __future__ import annotations

import os
import re
import sys
import time
import uuid
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
load_dotenv()  # 加载 .env 文件中的环境变量

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "app"))

import paper_import_agent as pia  # noqa: E402  空间原件，未修改
import engine  # noqa: E402
import simple_recommend  # noqa: E402  简化推荐引擎

MAX_CHARS = 20_000      # 单次粘贴文本上限
MAX_ITEMS = 60          # 单份试卷最多导入题数，防止额度被一次性烧掉
MAX_IMAGE_MB = 10

# 上传图片存储目录
UPLOAD_DIR = BASE / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="试卷导入 Agent 接入服务", docs_url="/api/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- 通用工具
def _llm_ready() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _kp_name(kid: int) -> str:
    try:
        return engine.kp_name(kid)
    except Exception:
        return str(kid)


def _em_name(eid: int) -> str:
    try:
        return engine.em_name(eid)
    except Exception:
        return str(eid)


def _decorate(attempts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """给前端补上可读名称；不改动引擎字段。"""
    out = []
    for i, a in enumerate(attempts):
        row = dict(a)
        row["kp_name"] = _kp_name(a.get("kp"))
        row["em_name"] = _em_name(a.get("mech"))
        row["idx"] = i + 1
        out.append(row)
    return out


def _strip_paper_head(text: str) -> str:
    """砍掉第一题之前的卷头（标题 / 来源 / 考试说明），否则会被当成第 0 题。"""
    m = re.search(r"(?m)^\s*1\s*[.、．]\s*", text)
    if m and m.start() > 0 and m.start() < 300:
        return text[m.start():]
    return text


def _result(attempts, originals, dropped, n_llm, mode, note="") -> Dict[str, Any]:
    attempts = attempts[:MAX_ITEMS]
    originals = (originals or [])[:MAX_ITEMS]
    return {
        "mode": mode,                       # llm | vlm | ocr | rule
        "note": note,
        "attempts": _decorate(attempts),
        "originals": _decorate(originals),
        "dropped": dropped[:20],
        "stats": {
            "n": len(attempts),
            "dropped": len(dropped),
            "llm_annotated": n_llm,
            "truncated": len(attempts) >= MAX_ITEMS,
        },
    }


# ---------------------------------------------------------------- 模型定义
class TextIn(BaseModel):
    text: str = Field(..., description="试卷原文")
    exam_date: Optional[str] = Field(None, description="作答日期 YYYY-MM-DD")
    results: Optional[List[str]] = Field(None, description="按题序的对错，如 ['错','对']")


class RecIn(BaseModel):
    attempts: List[Dict[str, Any]]
    originals: Optional[List[Dict[str, Any]]] = None
    n: int = 5
    seed: Optional[int] = 42


# ---------------------------------------------------------------- 接口
@app.get("/api/health")
def health():
    langs = []
    try:
        import subprocess
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True)
        langs = [l.strip() for l in out.stdout.splitlines()[1:] if l.strip()]
    except Exception:
        pass
    try:
        bank_n = len(engine.build_bank())
    except Exception:
        bank_n = 0
    return {
        "llm": _llm_ready(),
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "base_host": re.sub(r"^https?://([^/]+).*$", r"\1",
                            os.environ.get("OPENAI_BASE_URL", "api.openai.com")),
        "ocr": "chi_sim" in langs,
        "ocr_langs": langs,
        "bank_size": bank_n,
        "limits": {"max_chars": MAX_CHARS, "max_items": MAX_ITEMS, "max_image_mb": MAX_IMAGE_MB},
    }


@app.get("/api/meta")
def meta():
    kps = [{"id": v, "name": k} for k, v in pia.KP_ID.items()]
    ems = [{"id": v, "name": k} for k, v in pia.EM_ID.items()]
    return {"knowledge_points": kps, "error_mechanisms": ems, "tiers": {1: "单步套公式", 2: "两步需变形", 3: "新情境多知识点"}}


@app.post("/api/import/text")
def import_text(body: TextIn):
    text = _strip_paper_head(body.text.strip())
    if not text:
        raise HTTPException(400, "试卷内容为空")
    if len(text) > MAX_CHARS:
        raise HTTPException(413, f"文本超过 {MAX_CHARS} 字上限")

    t0 = time.time()
    try:
        attempts, originals, dropped, n_llm = pia.import_paper(
            text, exam_date=body.exam_date, results=body.results)
    except Exception as e:                                   # noqa: BLE001
        raise HTTPException(500, f"导入失败：{type(e).__name__}: {e}")

    mode = "llm" if n_llm > 0 else "rule"
    note = "" if _llm_ready() else "未配置模型（OPENAI_API_KEY），当前为关键词规则打标"
    out = _result(attempts, originals, dropped, n_llm, mode, note)
    out["stats"]["elapsed_ms"] = int((time.time() - t0) * 1000)
    return out


@app.post("/api/import/image")
async def import_image(file: UploadFile = File(...), exam_date: Optional[str] = Form(None)):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空文件")
    if len(raw) > MAX_IMAGE_MB * 1024 * 1024:
        raise HTTPException(413, f"图片超过 {MAX_IMAGE_MB}MB")

    # 保存原图到持久目录
    image_id = uuid.uuid4().hex
    suffix = Path(file.filename or "paper.jpg").suffix.lower() or ".jpg"
    saved_path = UPLOAD_DIR / f"{image_id}{suffix}"
    saved_path.write_bytes(raw)

    t0 = time.time()
    mode, note = "ocr", ""
    try:
        if _llm_ready():
            attempts, originals, dropped, src = pia.import_paper_image_vlm(str(saved_path), exam_date)
            if attempts is not None:
                mode = "vlm"
            else:                                            # 视觉模型失败 → OCR 兜底
                note = f"视觉模型未返回结果（{src}），已退回 OCR"
        if mode != "vlm":
            attempts, originals, dropped = pia.import_paper_image(str(saved_path), exam_date, prefer_vlm=False)
            if not _llm_ready():
                note = "未配置模型（OPENAI_API_KEY），当前为 OCR + 红笔勾叉检测兜底"
    except Exception as e:                                   # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        print(f"Image import error:\n{tb}")
        raise HTTPException(500, f"识别失败：{type(e).__name__}: {e}")

    out = _result(attempts or [], originals or [], dropped or [], len(attempts or []), mode, note)
    out["stats"]["elapsed_ms"] = int((time.time() - t0) * 1000)
    out["image_id"] = image_id
    if not attempts:
        out["error"] = "没有解析出题目：OCR 未能命中「题号 + 题干」结构，建议改用清晰单栏照片，或配置模型走视觉识别"
    return out


@app.post("/api/recommend")
def recommend(body: RecIn):
    try:
        bank = simple_recommend.build_simple_bank()
        res, prof, prior = simple_recommend.recommend_simple(
            body.attempts, bank, n=body.n, seed=body.seed)
    except Exception as e:                                   # noqa: BLE001
        raise HTTPException(500, f"抽题失败：{type(e).__name__}: {e}")

    rows = []
    for r in res:
        q = r.get("q", {})
        rows.append({
            "src": r.get("src"),
            "text": q.get("text", ""),
            "kp": _kp_name(q.get("kp")) if q.get("kp") else "",
            "tier": q.get("tier"),
            "reason": r.get("reason", ""),
        })
    profile = [{"kp": _kp_name(k), "err": round(v["err"], 3), "n": v["n"]}
               for k, v in sorted(prof.items(), key=lambda kv: -kv[1]["err"])]
    return {"recs": rows, "profile": profile, "prior": round(prior, 4)}


@app.get("/api/image/crop")
async def crop_image(image_id: str, x: int, y: int, w: int, h: int):
    """裁切图片接口，bbox 坐标范围 0-1000"""
    # 查找图片文件
    image_path = None
    for ext in [".jpg", ".jpeg", ".png"]:
        candidate = UPLOAD_DIR / f"{image_id}{ext}"
        if candidate.exists():
            image_path = candidate
            break

    if not image_path:
        raise HTTPException(404, f"图片不存在: {image_id}")

    try:
        from PIL import Image
        import io

        img = Image.open(image_path)
        width, height = img.size

        # 将归一化坐标转换为实际像素坐标
        actual_x = int(x * width / 1000)
        actual_y = int(y * height / 1000)
        actual_w = int(w * width / 1000)
        actual_h = int(h * height / 1000)

        # 裁切图片
        cropped = img.crop((actual_x, actual_y, actual_x + actual_w, actual_y + actual_h))

        # 转换为字节流返回
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=85)
        buf.seek(0)

        from fastapi.responses import StreamingResponse
        return StreamingResponse(buf, media_type="image/jpeg")
    except Exception as e:
        raise HTTPException(500, f"裁切失败：{type(e).__name__}: {e}")



# ---------------------------------------------------------------- 静态页
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.get("/")
def index():
    return FileResponse(str(BASE / "static" / "index.html"))


@app.get("/m.html")
@app.get("/m")
def mobile():
    return FileResponse(str(BASE / "static" / "m.html"))


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    print(f" → http://127.0.0.1:{port}   LLM: {'已配置' if _llm_ready() else '未配置（规则兜底）'}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")

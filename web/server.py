"""멜로디노트 웹 서버.

화면(web/static)을 보여 주고, 녹음 파일을 받아 파이프라인으로 변환한다.
- POST /api/transcribe : 음성 파일 → 악보 데이터(JSON). 동기 처리 (PRD 4장)
- POST /api/midi       : 화면에서 고친 음 → MIDI 파일
- POST /api/feedback   : 원래 인식 결과와 고친 결과를 data/feedback.jsonl 에 남김 (PRD 5·6장)
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pipeline.midi_out import build_midi
from pipeline.rhythm import QNote
from pipeline.transcribe import transcribe

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
DATA = ROOT / "data"
MAX_UPLOAD = 20 * 1024 * 1024  # 1분 녹음은 WAV로도 3MB 안팎
MSG_UNREADABLE = "이 파일은 읽을 수 없어요. wav, mp3, m4a 파일이나 브라우저 녹음을 써 주세요"

state = {"ready": False, "warmup_sec": None}


def _warm_up() -> None:
    """첫 변환이 느리지 않게 서버 시작 때 한 번 돌려 둔다 (numba 컴파일)."""
    t = time.perf_counter()
    sr = 22050
    tt = np.arange(int(sr * 1.5)) / sr
    y = (0.3 * np.sin(2 * np.pi * 440 * tt)).astype(np.float32)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        sf.write(path, y, sr)
        transcribe(path)
    finally:
        os.remove(path)
    state["ready"] = True
    state["warmup_sec"] = round(time.perf_counter() - t, 2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _warm_up()
    yield


app = FastAPI(title="MelodyNote", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict:
    return state


@app.post("/api/transcribe")
def transcribe_audio(audio: UploadFile = File(...), bpm: float | None = Form(None)) -> dict:
    data = audio.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "파일이 너무 커요. 1분 이내 녹음만 변환할 수 있어요")
    if bpm is not None and not (30 <= bpm <= 300):
        raise HTTPException(422, "템포는 30~300 사이로 정해 주세요")
    suffix = Path(audio.filename or "").suffix.lower() or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        path = f.name
    try:
        res, _ = transcribe(path, bpm=bpm)
    except Exception:
        raise HTTPException(422, MSG_UNREADABLE)
    finally:
        os.remove(path)
    return res.to_dict()


class NoteIn(BaseModel):
    pitch: int = Field(ge=0, le=127)
    start16: int = Field(ge=0)
    dur16: int = Field(ge=1, le=64)


class MidiIn(BaseModel):
    bpm: float = Field(ge=30, le=300)
    key: str = "C"
    notes: list[NoteIn] = Field(max_length=2000)


@app.post("/api/midi")
def export_midi(body: MidiIn) -> Response:
    q = [QNote(n.pitch, n.start16, n.dur16, 0.0, 0.0, 1.0, False) for n in body.notes]
    q.sort(key=lambda n: n.start16)
    try:
        mid = build_midi(q, body.bpm, body.key)
    except (KeyError, ValueError):
        mid = build_midi(q, body.bpm, "C")
    buf = io.BytesIO()
    mid.save(file=buf)
    return Response(
        buf.getvalue(),
        media_type="audio/midi",
        headers={"Content-Disposition": 'attachment; filename="melodynote.mid"'},
    )


@app.post("/api/feedback")
def feedback(body: dict) -> dict:
    line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **body}, ensure_ascii=False)
    if len(line) > 500_000:
        raise HTTPException(413, "기록이 너무 커요")
    DATA.mkdir(exist_ok=True)
    with open(DATA / "feedback.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return {"saved": True}

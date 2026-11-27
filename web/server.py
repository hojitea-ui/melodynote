"""멜로디노트 웹 서버.

화면(web/static)을 보여 주고, 녹음 파일을 받아 파이프라인으로 변환한다.
- POST /api/transcribe : 음성 파일 → 악보 데이터(JSON). 동기 처리 (PRD 4장)
                         mode=poly(양손·반주 → 멜로디, 기본) | mono(한 줄), floor=멜로디 최저음(MIDI)
- POST /api/midi       : 화면에서 고친 음 → MIDI 파일
- POST /api/feedback   : 원래 인식 결과와 고친 결과를 data/feedback.jsonl 에 남김 (PRD 5·6장)
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import time
from collections import OrderedDict
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
        transcribe(path, mode="mono")
        transcribe(path, mode="poly")  # 다성 모델(ONNX) 불러 두기
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


@app.middleware("http")
async def no_stale_files(request, call_next):
    """화면 파일을 고친 뒤 브라우저가 옛 파일을 쓰지 않게, 매번 바뀌었는지 확인하게 한다."""
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict:
    return state


class EventCache:
    """같은 녹음의 다성 인식 결과를 몇 개 기억한다. 템포·최저음만 바꿔 다시 맞출 때 인식을 건너뛴다."""

    def __init__(self, size: int = 4):
        self.size, self.items = size, OrderedDict()

    def get(self, key: str):
        if key in self.items:
            self.items.move_to_end(key)
            return self.items[key]
        return None

    def put(self, key: str, value) -> None:
        self.items[key] = value
        self.items.move_to_end(key)
        while len(self.items) > self.size:
            self.items.popitem(last=False)


events_cache = EventCache()


@app.post("/api/transcribe")
def transcribe_audio(
    audio: UploadFile = File(...),
    bpm: float | None = Form(None),
    mode: str = Form("poly"),
    floor: int | None = Form(None),
) -> dict:
    data = audio.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "파일이 너무 커요. 1분 이내 녹음만 변환할 수 있어요")
    if bpm is not None and not (30 <= bpm <= 300):
        raise HTTPException(422, "템포는 30~300 사이로 정해 주세요")
    if mode not in ("poly", "mono"):
        raise HTTPException(422, "mode는 poly 또는 mono예요")
    if floor is not None and not (21 <= floor <= 108):
        raise HTTPException(422, "최저음은 피아노 건반 범위(A0~C8) 안에서 정해 주세요")
    key = hashlib.sha1(data).hexdigest()
    suffix = Path(audio.filename or "").suffix.lower() or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        path = f.name
    try:
        res, _ = transcribe(
            path, bpm=bpm, mode=mode, floor=floor,
            events=events_cache.get(key) if mode == "poly" else None,
            on_events=lambda ev: events_cache.put(key, ev),
        )
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

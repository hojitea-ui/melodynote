"""박자 보정: 초 단위 음을 템포에 맞춰 16분음표 격자에 맞추고, 조성을 추정한다."""
from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

from .segment import Note

GRID_PER_BEAT = 4  # 16분음표 격자
# 허용하는 음가 (16분음표 개수): 16분, 8분, 점8분, 4분, 점4분, 2분, 점2분, 온음표
NOTE_VALUES = [1, 2, 3, 4, 6, 8, 12, 16]
BPM_RANGE = (50.0, 200.0)


@dataclass
class QNote:
    pitch: int
    start16: int  # 시작 위치 (16분음표 단위)
    dur16: int  # 길이 (16분음표 단위, NOTE_VALUES 중 하나)
    start_sec: float
    end_sec: float
    confidence: float
    uncertain: bool


def estimate_tempo(notes: list[Note]) -> float:
    """음 시작점들로 템포를 추정한다.

    녹음 전체의 어택이 아니라 '인식된 음의 시작점'만 펄스로 만들어 쓴다.
    배경 잡음의 어택에 흔들리지 않게 하려는 것.
    """
    if len(notes) < 3:
        return 120.0
    sr_env = 100  # 10ms 해상도 펄스열
    length = int(notes[-1].end * sr_env) + sr_env
    env = np.zeros(length)
    for nt in notes:
        # 긴 음일수록 박의 강세에 놓이는 경우가 많아 조금 더 무겁게 둔다
        env[int(nt.start * sr_env)] += 1.0 + np.log2(1.0 + (nt.end - nt.start) / 0.25)
    # 사람의 박자 흔들림(수십 ms)을 흡수하도록 넓게 퍼뜨린다
    env = np.convolve(env, np.hanning(11), mode="same")
    bpm = float(
        librosa.feature.tempo(onset_envelope=env, sr=sr_env, hop_length=1, start_bpm=100)[0]
    )
    while bpm < BPM_RANGE[0]:
        bpm *= 2
    while bpm > BPM_RANGE[1]:
        bpm /= 2
    return refine_tempo(bpm, np.array([n.start for n in notes]))


def refine_tempo(bpm: float, onsets: np.ndarray, span: float = 0.04, step: float = 0.05) -> float:
    """대략의 템포 주변(±4%)에서 음 시작점이 16분음표 격자에 가장 잘 맞는 템포를 고른다.

    템포가 1%만 틀려도 8마디면 16분음표 하나 넘게 밀리므로, 자기상관으로 얻은 값을 다듬는다.
    격자에 대한 위상을 원형 평균으로 재면 위상을 따로 찾지 않아도 된다.
    """
    if len(onsets) < 4:
        return bpm
    best, best_r = bpm, -1.0
    for b in np.arange(bpm * (1 - span), bpm * (1 + span), step):
        grid = 60.0 / b / GRID_PER_BEAT
        r = abs(np.mean(np.exp(2j * np.pi * onsets / grid)))  # 1이면 모든 시작점이 격자 위
        if r > best_r:
            best, best_r = float(b), r
    return round(best, 1)


def _best_phase(onsets: np.ndarray, grid: float) -> float:
    """격자의 시작 위치(초)를 음 시작점에 가장 잘 맞게 고른다."""
    phases = np.linspace(0, grid, 32, endpoint=False)
    errs = []
    for ph in phases:
        r = (onsets - ph) / grid
        errs.append(np.sum((r - np.round(r)) ** 2))
    return float(phases[int(np.argmin(errs))])


def _snap_value(units: int) -> int:
    """가장 가까운 허용 음가로 맞춘다 (길이는 비율로 비교)."""
    units = max(units, 1)
    return min(NOTE_VALUES, key=lambda v: abs(np.log2(v / units)))


def quantize(notes: list[Note], bpm: float) -> list[QNote]:
    if not notes:
        return []
    grid = 60.0 / bpm / GRID_PER_BEAT
    onsets = np.array([n.start for n in notes])
    phase = _best_phase(onsets, grid)
    first = int(np.floor((notes[0].start - phase) / grid + 0.5))

    starts = [int(np.floor((n.start - phase) / grid + 0.5)) - first for n in notes]
    # 같은 칸에 두 음이 몰리면 뒤 음을 한 칸 민다
    for i in range(1, len(starts)):
        if starts[i] <= starts[i - 1]:
            starts[i] = starts[i - 1] + 1

    out: list[QNote] = []
    for i, n in enumerate(notes):
        end_pos = (n.end - phase) / grid - first
        nxt = starts[i + 1] if i + 1 < len(notes) else None
        # 다음 음 직전까지 소리가 이어졌다면(16분음표 하나 미만의 틈) 레가토로 본다
        if nxt is not None and nxt - end_pos < 1.0:
            raw = nxt - starts[i]
        else:
            raw = int(round(end_pos)) - starts[i]
        dur = _snap_value(raw)
        if nxt is not None and starts[i] + dur > nxt:
            room = nxt - starts[i]
            dur = max(v for v in NOTE_VALUES if v <= room) if room >= 1 else 1
        out.append(QNote(n.pitch, starts[i], dur, n.start, n.end, n.confidence, n.uncertain))
    return out


# Krumhansl-Kessler 조성 프로파일
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
# MIDI 조표 이름 (mido 형식). 반음 0~11 기준
_MAJOR_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
_MINOR_NAMES = ["Cm", "C#m", "Dm", "Ebm", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "Bbm", "Bm"]
_FLAT_KEYS = {"F", "Bb", "Eb", "Ab", "Db", "Dm", "Gm", "Cm", "Fm", "Bbm", "Ebm"}
_SHARP_SPELL = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLAT_SPELL = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


def estimate_key(qnotes: list[QNote]) -> str:
    if not qnotes:
        return "C"
    hist = np.zeros(12)
    for q in qnotes:
        hist[q.pitch % 12] += q.dur16
    best, best_r = "C", -2.0
    for tonic in range(12):
        for prof, names in ((_MAJOR, _MAJOR_NAMES), (_MINOR, _MINOR_NAMES)):
            r = np.corrcoef(hist, np.roll(prof, tonic))[0, 1]
            if np.isfinite(r) and r > best_r:
                best, best_r = names[tonic], r
    return best


def note_name(pitch: int, key: str) -> str:
    spell = _FLAT_SPELL if key in _FLAT_KEYS else _SHARP_SPELL
    return f"{spell[pitch % 12]}{pitch // 12 - 1}"

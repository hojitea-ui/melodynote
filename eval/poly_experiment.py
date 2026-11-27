"""실험: 다성 모델(basic-pitch)로 양손 녹음에서 멜로디 뽑기.

1. basic-pitch로 동시에 울리는 음을 모두 잡는다 (양손 전체)
2. 가장 높은 음을 따라가는 'skyline' 방식으로 멜로디만 남긴다
3. 기존 박자 보정(pipeline.rhythm)으로 격자에 맞춰 MIDI로 쓴다

basic-pitch는 numpy 1.x가 필요해서 앱과 다른 가상환경(.venv-bp)에서 돌린다:
  py -3.11 -m venv .venv-bp
  .venv-bp\\Scripts\\python.exe -m pip install --no-deps basic-pitch
  .venv-bp\\Scripts\\python.exe -m pip install "numpy<2" onnxruntime librosa mir_eval pretty_midi resampy scipy scikit-learn typing_extensions mido
사용법: .venv-bp\\Scripts\\python.exe -m eval.poly_experiment 녹음.wav [-o samples]
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from pipeline.midi_out import write_midi
from pipeline.rhythm import QNote, estimate_key, estimate_tempo, note_name, quantize
from pipeline.segment import Note

CHORD_SEC = 0.05  # 이 안에 같이 시작한 음은 한 화음으로 본다


def detect_all(path: str):
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict

    _, _, events = predict(path, ICASSP_2022_MODEL_PATH)
    # (시작, 끝, 음높이, 세기)
    return sorted([(float(s), float(e), int(p), float(a)) for s, e, p, a, _ in events])


def skyline(events, min_pitch: int = 0):
    """가장 높은 음을 따라간다.

    - 같이 시작한 화음에서는 가장 높은 음만
    - 더 높은 음이 아직 울리는 동안 시작한 낮은 음은 반주로 보고 뺀다
    - 남은 음은 다음 멜로디 음이 시작하면 끊는다
    """
    ev = [e for e in events if e[2] >= min_pitch]
    # 화음 묶기 → 가장 높은 음
    tops, i = [], 0
    while i < len(ev):
        j = i
        while j + 1 < len(ev) and ev[j + 1][0] - ev[i][0] < CHORD_SEC:
            j += 1
        tops.append(max(ev[i : j + 1], key=lambda x: x[2]))
        i = j + 1
    melody = []
    for s, e, p, a in tops:
        ringing = [m for m in melody if m[1] > s + 0.03]
        if ringing and max(m[2] for m in ringing) > p:
            continue  # 더 높은 음이 울리는 중 → 반주
        melody.append([s, e, p, a])
    for k in range(len(melody) - 1):
        melody[k][1] = min(melody[k][1], melody[k + 1][0])
    return [tuple(m) for m in melody]


def to_qnotes(notes, bpm=None):
    seg = [Note(s, e, p, float(p), min(1.0, a), False) for s, e, p, a in notes]
    tempo = bpm or estimate_tempo(seg)
    return quantize(seg, tempo), tempo


def describe(qs, key):
    return " ".join(note_name(q.pitch, key) for q in qs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("-o", "--out", default="samples")
    ap.add_argument("--min-pitch", type=int, default=0, help="이보다 낮은 음은 멜로디 후보에서 뺀다 (MIDI 번호)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(args.audio).stem

    t = time.perf_counter()
    events = detect_all(args.audio)
    t_detect = time.perf_counter() - t
    pitches = [e[2] for e in events]
    print(f"[전체] {len(events)}음 · 음역 {min(pitches)}~{max(pitches)} · 인식 {t_detect:.1f}s")

    q_all, bpm_all = to_qnotes(events)
    key = estimate_key(q_all)
    write_midi(q_all, bpm_all, key, str(out / f"{stem}_poly_all.mid"))

    mel = skyline(events, args.min_pitch)
    q_mel, bpm = to_qnotes(mel)
    key_m = estimate_key(q_mel)
    write_midi(q_mel, bpm, key_m, str(out / f"{stem}_poly_melody.mid"))
    print(f"[멜로디] {len(mel)}음 · 템포 {bpm} · 조 {key_m}")
    print("  ", describe(q_mel, key_m))
    print(f"→ {out / (stem + '_poly_all.mid')}, {out / (stem + '_poly_melody.mid')}")


if __name__ == "__main__":
    main()

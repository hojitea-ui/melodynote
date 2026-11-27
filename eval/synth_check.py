"""합성 멜로디로 파이프라인을 점검한다 (정답 곡 비교 전의 1차 확인용).

정답을 아는 멜로디를 소리로 합성해 넣고, 음높이 정확도·음가 오차·템포·처리 시간을 잰다.
사람 목소리를 흉내 내려고 비브라토, 음 시작의 꺾임, 전체 음정 치우침, 박자 흔들림, 잡음을 넣는다.
실제 녹음과는 다르므로 여기 숫자는 '파이프라인이 제대로 돌아가는지'를 보는 용도다.

사용법: python -m eval.synth_check [--save 폴더]
"""
from __future__ import annotations

import argparse
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from pipeline.rhythm import NOTE_VALUES
from pipeline.transcribe import transcribe

SR = 22050


@dataclass
class Case:
    name: str
    bpm: float
    notes: list[tuple[int, int]]  # (MIDI 음높이 또는 -1=쉼표, 길이 16분음표 단위)
    vibrato: float = 0.0  # 반음 폭
    scoop: float = 0.0  # 음 시작 때 아래에서 올라오는 폭(반음)
    detune_cents: float = 0.0
    jitter_sec: float = 0.0  # 박자 흔들림 표준편차
    snr_db: float | None = None
    legato: float = 0.92  # 음 길이 중 실제 소리 나는 비율
    timbre: str = "voice"  # voice | sine
    seed: int = 0


def synth(case: Case) -> np.ndarray:
    rng = np.random.default_rng(case.seed)
    sec16 = 60.0 / case.bpm / 4
    total = sum(d for _, d in case.notes) * sec16 + 1.0
    y = np.zeros(int(total * SR))
    t_pos = 0.3
    harmonics = [1.0, 0.6, 0.35, 0.2, 0.12, 0.07] if case.timbre == "voice" else [1.0]
    for pitch, d in case.notes:
        start = t_pos + (rng.normal(0, case.jitter_sec) if case.jitter_sec else 0.0)
        t_pos += d * sec16
        if pitch < 0:
            continue
        length = d * sec16 * case.legato
        n = int(length * SR)
        tt = np.arange(n) / SR
        m = pitch + case.detune_cents / 100 + rng.normal(0, 0.05)
        m = m + case.vibrato * np.sin(2 * np.pi * 5.5 * tt) * np.clip(tt / 0.25, 0, 1)
        m = m - case.scoop * np.exp(-tt / 0.04)
        f = 440.0 * 2 ** ((m - 69) / 12)
        phase = 2 * np.pi * np.cumsum(f) / SR
        tone = sum(a * np.sin((k + 1) * phase) for k, a in enumerate(harmonics))
        env = np.minimum(1, tt / 0.02) * np.minimum(1, (length - tt) / 0.04)
        a = max(0, int(start * SR))
        seg = tone * env * 0.3
        y[a : a + len(seg)] += seg[: len(y) - a]
    if case.snr_db is not None:
        p = np.mean(y[y != 0] ** 2)
        y = y + rng.normal(0, np.sqrt(p / 10 ** (case.snr_db / 10)), len(y))
    return (y / max(1e-9, np.abs(y).max()) * 0.8).astype(np.float32)


def gt_onsets(case: Case) -> list[tuple[float, int, int]]:
    sec16 = 60.0 / case.bpm / 4
    out, pos = [], 0
    for p, d in case.notes:
        if p >= 0:
            out.append((0.3 + pos * sec16, p, d))
        pos += d
    return out


def _ladder(v: float) -> int:
    return int(np.argmin([abs(np.log2(max(v, 0.25) / x)) for x in NOTE_VALUES]))


def score(case: Case, res) -> dict:
    gt = gt_onsets(case)
    det = res.notes
    used = set()
    pitch_ok, dur_ok, pos_ok, matched = 0, 0, 0, 0
    gt_pos, pos = [], 0
    for p_, d_ in case.notes:
        if p_ >= 0:
            gt_pos.append(pos)
        pos += d_
    anchor = None  # 첫 매칭 음 기준으로 박자 위치를 맞춘다
    # 템포가 2배·절반으로 잡히는 건 표기 차이이므로 음가 비교 때 맞춰 준다
    ratio = case.bpm / res.bpm if res.bpm else 1.0
    octave = 2.0 ** round(np.log2(ratio)) if res.bpm else 1.0
    for j, (t, p, d) in enumerate(gt):
        cands = [(abs(n["start_sec"] - t), i) for i, n in enumerate(det) if i not in used]
        cands = [c for c in cands if c[0] < 0.08]
        if not cands:
            continue
        _, i = min(cands)
        used.add(i)
        matched += 1
        pitch_ok += det[i]["pitch"] == p
        det16 = det[i]["dur_beat"] * 4 * octave
        dur_ok += abs(_ladder(det16) - _ladder(d)) <= 1
        dpos = det[i]["start_beat"] * 4 * octave
        if anchor is None:
            anchor = dpos - gt_pos[j]
        pos_ok += abs(dpos - anchor - gt_pos[j]) < 0.5
    n = len(gt)
    return {
        "gt_notes": n,
        "detected": len(det),
        "matched": matched,
        "pitch_acc": pitch_ok / n if n else 0.0,
        "dur_within1": dur_ok / n if n else 0.0,
        "pos_acc": pos_ok / n if n else 0.0,  # 박자 위치(16분음표 칸)가 맞은 비율
        "bpm_est": res.bpm,
        "bpm_true": case.bpm,
        "time": res.timings.get("total", 0.0),
    }


def random_melody(seed: int, beats: int) -> list[tuple[int, int]]:
    rng = np.random.default_rng(seed)
    scale = [0, 2, 4, 5, 7, 9, 11, 12]
    pos, cur, out = 0, 3, []
    while pos < beats * 4:
        d = int(rng.choice([2, 2, 4, 4, 4, 6, 8, 1, 3]))
        if d in (1, 3) and pos % 2 == 0:
            d = 2
        cur = int(np.clip(cur + rng.integers(-2, 3), 0, len(scale) - 1))
        out.append((60 + scale[cur], d))
        pos += d
    return out


# 반복음, 점음표, 쉼표, 도약이 섞인 짧은 악구
PHRASE = [
    (60, 4), (60, 4), (67, 4), (67, 4), (69, 2), (71, 2), (72, 4), (67, 8),
    (65, 6), (64, 2), (62, 4), (-1, 4), (64, 2), (65, 2), (67, 4), (72, 8),
    (71, 4), (69, 2), (67, 2), (65, 4), (64, 4), (62, 2), (64, 2), (60, 12),
]

CASES = [
    Case("clean_sine", 100, PHRASE, timbre="sine"),
    Case("clean_voice", 100, PHRASE),
    Case("sung_like", 92, PHRASE, vibrato=0.3, scoop=0.6, detune_cents=30, jitter_sec=0.02, seed=1),
    Case("sung_noisy", 92, PHRASE, vibrato=0.3, scoop=0.6, detune_cents=-25, jitter_sec=0.03, snr_db=20, seed=2),
    Case("fast_staccato", 140, PHRASE, legato=0.6, seed=3),
    Case("one_minute", 100, random_melody(7, 98), vibrato=0.25, scoop=0.4, jitter_sec=0.02, seed=4),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", help="합성한 wav를 저장할 폴더")
    args = ap.parse_args()
    tmp = Path(args.save) if args.save else Path(tempfile.mkdtemp())
    tmp.mkdir(parents=True, exist_ok=True)

    print(f"{'case':14} {'len':>5} {'gt':>3} {'det':>3} {'pitch':>6} {'dur±1':>6} {'pos':>6} {'bpm(est/true)':>14} {'time':>6} | 고정 템포: pitch dur±1 pos")
    for case in CASES:
        y = synth(case)
        wav = tmp / f"{case.name}.wav"
        sf.write(wav, y, SR)
        res, _ = transcribe(str(wav))
        s = score(case, res)
        res_fixed, _ = transcribe(str(wav), bpm=case.bpm)
        sf_ = score(case, res_fixed)
        print(
            f"{case.name:14} {len(y)/SR:5.1f} {s['gt_notes']:3d} {s['detected']:3d} "
            f"{s['pitch_acc']:6.0%} {s['dur_within1']:6.0%} {s['pos_acc']:6.0%} {s['bpm_est']:7.1f}/{s['bpm_true']:<6.0f} "
            f"{s['time']:6.2f} | {sf_['pitch_acc']:6.0%} {sf_['dur_within1']:6.0%} {sf_['pos_acc']:6.0%}"
        )

    # 오류 대응 확인: 무음, 잡음, 화음
    silent = np.zeros(SR * 3, dtype=np.float32)
    noise = (np.random.default_rng(0).normal(0, 0.2, SR * 3)).astype(np.float32)
    tt = np.arange(SR * 3) / SR
    chord = sum(np.sin(2 * np.pi * 440 * 2 ** (k / 12) * tt) for k in (0, 4, 7)).astype(np.float32) * 0.2
    for name, y in (("silence", silent), ("white_noise", noise), ("chord_A", chord)):
        wav = tmp / f"{name}.wav"
        sf.write(wav, y, SR)
        res, _ = transcribe(str(wav))
        names = " ".join(n["name"] + ("?" if n["uncertain"] else "") for n in res.notes)
        print(f"{name:14} status={res.status} msg={res.message} notes=[{names}]")


if __name__ == "__main__":
    t = time.perf_counter()
    main()
    print(f"(전체 {time.perf_counter() - t:.1f}s)")

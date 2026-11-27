"""녹음 대신 쓸 테스트용 음성 파일을 만든다.

귀로 듣고 비교하기 쉽게 잘 알려진 공개 저작물 멜로디(작은 별, 환희의 송가)를 쓴다.
사람 목소리처럼 비브라토·음 시작의 꺾임·음정 치우침·박자 흔들림을 넣은 버전도 만든다.

사용법: python -m eval.make_samples [-o samples]
만든 파일을 웹 화면의 '파일 올리기'로 넣어 보면 된다.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import soundfile as sf

from eval.synth_check import PHRASE, SR, Case, synth

C, D, E, F, G, A, B, C5 = 60, 62, 64, 65, 67, 69, 71, 72
q, h, dq, e = 4, 8, 6, 2  # 4분, 2분, 점4분, 8분 (16분음표 단위)

TWINKLE = [
    (C, q), (C, q), (G, q), (G, q), (A, q), (A, q), (G, h),
    (F, q), (F, q), (E, q), (E, q), (D, q), (D, q), (C, h),
    (G, q), (G, q), (F, q), (F, q), (E, q), (E, q), (D, h),
    (G, q), (G, q), (F, q), (F, q), (E, q), (E, q), (D, h),
    (C, q), (C, q), (G, q), (G, q), (A, q), (A, q), (G, h),
    (F, q), (F, q), (E, q), (E, q), (D, q), (D, q), (C, h),
]

ODE_TO_JOY = [
    (E, q), (E, q), (F, q), (G, q), (G, q), (F, q), (E, q), (D, q),
    (C, q), (C, q), (D, q), (E, q), (E, dq), (D, e), (D, h),
    (E, q), (E, q), (F, q), (G, q), (G, q), (F, q), (E, q), (D, q),
    (C, q), (C, q), (D, q), (E, q), (D, dq), (C, e), (C, h),
]

SAMPLES = [
    # 파일 이름, 설명, Case
    ("01_twinkle_clean", "작은 별 · 깨끗한 소리 · 100 BPM",
     Case("twinkle_clean", 100, TWINKLE, legato=0.85)),
    ("02_twinkle_sung", "작은 별 · 노래 흉내(비브라토·꺾임·음정 +30센트·박자 흔들림) · 96 BPM",
     Case("twinkle_sung", 96, TWINKLE, vibrato=0.3, scoop=0.6, detune_cents=30, jitter_sec=0.02, legato=0.85, seed=11)),
    ("03_ode_to_joy_sung", "환희의 송가 · 노래 흉내 + 잡음 · 112 BPM",
     Case("ode_sung", 112, ODE_TO_JOY, vibrato=0.3, scoop=0.5, detune_cents=-20, jitter_sec=0.02, snr_db=25, seed=12)),
    ("04_phrase_mixed", "점음표·쉼표·도약이 섞인 악구 · 노래 흉내 · 92 BPM",
     Case("phrase", 92, PHRASE, vibrato=0.3, scoop=0.6, detune_cents=20, jitter_sec=0.02, seed=13)),
]


def chord() -> np.ndarray:
    """오류 대응 확인용: 도·미·솔 화음 3초 (멜로디 하나가 아님)"""
    t = np.arange(SR * 3) / SR
    y = sum(np.sin(2 * np.pi * 440 * 2 ** ((m - 69) / 12) * t) for m in (60, 64, 67))
    env = np.minimum(1, t / 0.05) * np.minimum(1, (3 - t) / 0.1)
    return (y * env * 0.25).astype(np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="samples")
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    for name, desc, case in SAMPLES:
        sf.write(out / f"{name}.wav", synth(case), SR)
        print(f"{name}.wav  {desc}")
    sf.write(out / "05_chord_error.wav", chord(), SR)
    print("05_chord_error.wav  도·미·솔 화음 3초 (오류 안내 확인용)")
    print(f"→ {out.resolve()}")


if __name__ == "__main__":
    main()

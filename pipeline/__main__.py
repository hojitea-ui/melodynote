"""사용법: python -m pipeline 녹음.wav [-o out] [--bpm 90]

결과로 <이름>.json(악보 데이터·처리 시간)과 <이름>.mid를 만든다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .midi_out import write_midi
from .transcribe import transcribe


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline", description="녹음한 멜로디를 악보 데이터와 MIDI로 변환")
    ap.add_argument("audio", help="음성 파일 (wav, mp3, m4a 등)")
    ap.add_argument("-o", "--out", default="out", help="출력 폴더 (기본: out)")
    ap.add_argument("--bpm", type=float, default=None, help="템포를 직접 지정 (기본: 자동 추정)")
    args = ap.parse_args(argv)

    res, qnotes = transcribe(args.audio, bpm=args.bpm)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(args.audio).stem
    (out / f"{stem}.json").write_text(
        json.dumps(res.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if qnotes:
        write_midi(qnotes, res.bpm, res.key, str(out / f"{stem}.mid"))

    print(f"상태: {res.status}" + (f" ({res.message})" if res.message else ""))
    print(f"길이 {res.duration_sec}s · 템포 {res.bpm} · {res.time_signature} · 조 {res.key} · 음 {len(res.notes)}개")
    if res.notes:
        print("음: " + " ".join(n["name"] + ("?" if n["uncertain"] else "") for n in res.notes))
    print("처리 시간(초): " + ", ".join(f"{k} {v}" for k, v in res.timings.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

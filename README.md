# 멜로디노트

녹음한 모티브(피아노 양손 또는 허밍)에서 멜로디를 골라 오선지 위의 음으로 바꾸는 웹앱. 로컬에서만 쓴다 (배포 안 함).

| 문서 | 내용 |
|---|---|
| [docs/PRD.md](docs/PRD.md) | 기획 |
| [docs/wizard-of-oz.md](docs/wizard-of-oz.md) | 오즈의 마법사 테스트 계획 |
| [docs/sequence.md](docs/sequence.md) | 시퀀스 다이어그램 |
| [docs/architecture.md](docs/architecture.md) | 아키텍처 다이어그램 |
| [docs/data-model.md](docs/data-model.md) | 데이터 모델 |

## 바로 쓰기 (웹 화면)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

basic-pitch는 TensorFlow를 끌고 오지 않도록 따로 설치한다 (ONNX로 돈다):

```powershell
.\.venv\Scripts\python.exe -m pip install --no-deps basic-pitch
```

```powershell
.\.venv\Scripts\python.exe -m web
```

브라우저에서 http://localhost:8000 을 연다. 서버가 켜질 때 파이프라인을 한 번 미리 돌려 두므로(numba 컴파일) 처음 몇 초는 상단 막대에 `SERVER WARMING`이 보인다.

| 화면 | 하는 일 |
|---|---|
| [01] 녹음하기 | 마이크 녹음(1분이 되면 자동 정지) 또는 파일 올리기. 브라우저가 22.05kHz 모노 WAV로 바꿔 서버에 보낸다 |
| MODE | **POLY**(기본): 양손·반주 → 다성 인식(basic-pitch) 후 가장 높은 줄을 멜로디로 / **MONO**: 허밍·노래 한 줄(pYIN) |
| LOWEST (POLY) | 멜로디로 볼 가장 낮은 음. 자동이면 왼손·오른손 음높이 경계. 바꾸면 인식 결과를 재사용해 1초 안에 다시 맞춘다 |
| 처리 단계 띠 | PREPARE → PITCH → SEGMENT → RHYTHM. 단계별 소요 시간은 서버가 잰 실제 값 |
| [02] 악보 | 조·박자·템포, 불확실한 음(?), 고친 음 비율(목표 20% 이하)을 보여 준다 |
| 고치기 | 음 누르기 → 반음·옥타브 올리고 내리기, 길이 바꾸기(뒤 음이 함께 밀림), 쉼표로, 되돌리기. 키보드: ← → ↑ ↓, Shift+↑↓, Del, Ctrl+Z |
| 아래 줄 | 들어 보기, 템포 고쳐 다시 맞추기(×½ ×2), MIDI 내보내기 |

MIDI를 내보낼 때 원래 인식 결과와 고친 결과를 `data/feedback.jsonl`에 남긴다(PRD 성공 기준 "고치는 음 20% 이하"를 재는 용도, 이 컴퓨터에만 저장).

디자인은 "오선지 위의 두 자루 연필": 빈 오선이 첫 화면의 무대이고, 녹음 버튼이 그 위에 놓인다. 색은 빨간 연필(녹음·불확실·오류)과 파란 연필(선택·고침·재생·진행)뿐이고, 글꼴은 고운바탕(강조)과 고운돋움(본문)이다. 악보는 [VexFlow 4.2.5](https://www.vexflow.com/)(CDN)로 그리므로 인터넷 연결이 필요하다.

## 구조

| 위치 | 하는 일 |
|---|---|
| `pipeline/pitch.py` | librosa pYIN으로 프레임별 음높이, 무음 구간 제거, 전체 음정 치우침 측정 |
| `pipeline/segment.py` | 음높이 변화·소리 세기의 골로 음을 나눔, 불확실한 음 표시 |
| `pipeline/poly.py` | basic-pitch 다성 인식, 멜로디 최저음 자동 설정, 가장 높은 줄 고르기 |
| `pipeline/rhythm.py` | 템포 추정(자기상관 후 ±4% 다듬기), 16분음표 격자에 맞춤, 조성 추정 |
| `pipeline/midi_out.py`, `transcribe.py` | MIDI 쓰기, PRD 오류 문구 처리, 단계별 처리 시간 기록 |
| `web/server.py` | FastAPI: `/api/transcribe`, `/api/midi`, `/api/feedback`, 화면 제공 |
| `web/static/` | 화면 (`app.js` 상태·고치기, `score.js` 악보 그리기, `audio.js` 녹음·WAV·미리 듣기) |
| `eval/synth_check.py` | 합성 멜로디로 정확도·속도 점검 |

## 파이프라인만 쓰기 (명령줄)

```powershell
.\.venv\Scripts\python.exe -m pipeline 녹음.wav -o out
```

기본은 POLY. 허밍은 `--mode mono`, 멜로디 최저음은 `--floor 60`(MIDI 번호), 템포는 `--bpm 90`. 결과는 `out/녹음.json`과 `out/녹음.mid`.

JSON의 `status`는 PRD 5장의 오류 대응과 맞춰 두었다.

| status | 뜻 | message |
|---|---|---|
| `ok` | 정상 | 없음 (화면에는 "틀린 음이 있나요? 눌러서 고칠 수 있어요") |
| `uncertain` | 불확실한 음이 30% 넘음 (화음·잡음 의심) | 멜로디 하나만 인식할 수 있어요. 불확실한 음은 ?로 표시했어요 |
| `no_notes` | 음을 못 찾음 | 음을 찾지 못했어요. 조용한 곳에서 다시 녹음해 주세요 |
| `too_long` | 1분 초과 | 1분 이내 녹음만 변환할 수 있어요 |

음마다 `uncertain`과 `reason`(`weak_voicing`, `between_pitches`, `unstable_pitch`, `missing_fundamental`)이 붙는다.

## 합성 멜로디 점검

```powershell
.\.venv\Scripts\python.exe -m eval.synth_check
```

정답을 아는 멜로디를 합성해 넣고 잰 결과 (Windows 노트북, 2026-11-27). "박자 위치"는 16분음표 칸이 정답과 같은 음의 비율:

| 경우 | 길이 | 음높이 | 음가 ±1단계 | 박자 위치 | 템포 추정/실제 | 처리 시간 |
|---|---|---|---|---|---|---|
| 순음 | 15s | 100% | 100% | 100% | 100.2/100 | 7.0s (첫 실행 포함) |
| 배음 있는 소리 | 15s | 100% | 100% | 100% | 100.1/100 | 1.7s |
| 노래 흉내 (비브라토·꺾임·음정 치우침·박자 흔들림) | 17s | 100% | 100% | 100% | 92.2/92 | 2.2s |
| 위 + 잡음 (SNR 20dB) | 17s | 100% | 100% | 100% | 92.0/92 | 2.4s |
| 빠른 스타카토 | 11s | 100% | 100% | 100% | 70.0/140 (절반) | 1.3s |
| 1분 무작위 멜로디 | 60s | 97% | 95% | 97% | 99.9/100 | 9.5s |

- 무음과 백색잡음은 `no_notes`, A·C#·E 화음은 `uncertain`으로 처리된다.
- 1분 녹음을 새 프로세스에서 처음 돌려도 11.5초 걸렸다. 웹 서버는 시작할 때 한 번 미리 돌려 둔다.

**주의:** 합성음이라 실제 녹음보다 쉽다. 이 숫자는 파이프라인이 제대로 도는지 확인하는 용도다. PRD 성공 기준은 실제 녹음과 직접 채보한 정답 곡으로 다시 재야 한다 (2단계).

## 알려진 한계

- 템포가 2배나 절반으로 잡힐 수 있다. 화면의 ×½ ×2 → 다시 맞추기로 고친다.
- 박자표는 4/4로 고정이다. 못갖춘마디는 처리하지 않는다 (첫 음을 첫 박에 둔다).
- 같은 음을 레가토로 이어 부르면, 소리 세기의 골이 5dB보다 얕을 때 한 음으로 합쳐진다.
- 화음 감지는 '기본음이 비어 있는지'만 본다. 기본음이 실제로 울리는 화음은 놓칠 수 있다.
- 악보 표기는 단순하다: 박 단위로 음표를 나누지 않아 마디 안 박 경계를 넘는 음이 생길 수 있다(붙임줄은 마디 경계에서만).

## 다음 단계

1. ~~핵심 파이프라인~~
2. 정답 곡과 비교하는 정확도 측정 스크립트 (`eval/`): 실제 녹음 10곡 내외 + 직접 채보한 정답
3. ~~웹 화면 (`web/`)~~: 실제 마이크 녹음으로 한 번 더 확인 필요

# 아키텍처

로컬 전용(배포 안 함). 한 컴퓨터에서 서버를 켜고 브라우저로 `http://localhost:8000`에 접속한다.

## 1. 전체 구성

```mermaid
flowchart TB
    subgraph Browser["브라우저 (localhost:8000)"]
        direction TB
        HTML["index.html · style.css<br/>objcat 키트 디자인"]
        APP["app.js<br/>상태 · 고치기 · 되돌리기 · 처리 띠"]
        SCORE["score.js<br/>마디 나누기 · 붙임줄 · VexFlow 그리기"]
        AUDIO["audio.js<br/>마이크 녹음 · WAV 변환 · 미리 듣기"]
        APP --> SCORE
        APP --> AUDIO
    end

    subgraph CDN["인터넷 (화면 꾸밈)"]
        VF["VexFlow 4.2.5<br/>jsDelivr"]
        GF["Google Fonts<br/>IBM Plex · Barlow"]
    end

    subgraph Server["로컬 서버 (FastAPI · uvicorn)"]
        direction TB
        API["web/server.py<br/>/api/transcribe · /api/midi · /api/feedback · /api/health"]
        CACHE["EventCache<br/>최근 녹음 4개의 다성 인식 결과"]
        API --- CACHE
    end

    subgraph Pipeline["pipeline 패키지"]
        direction TB
        TR["transcribe.py<br/>흐름 조립 · 오류 상태 · 처리 시간"]
        PI["pitch.py<br/>파일 읽기 · pYIN"]
        SE["segment.py<br/>음 나누기 · 불확실 판정"]
        PO["poly.py<br/>basic-pitch · 최저음 · 멜로디 고르기"]
        RH["rhythm.py<br/>템포 · 16분음표 격자 · 조성"]
        MI["midi_out.py<br/>MIDI 쓰기"]
        TR --> PI
        TR --> SE
        TR --> PO
        TR --> RH
    end

    subgraph Libs["라이브러리 · 모델"]
        LR["librosa · numba<br/>pYIN, 템포"]
        BP["basic-pitch ICASSP 2022<br/>onnxruntime"]
        AV["PyAV<br/>m4a·aac 디코딩"]
        MD["mido"]
    end

    subgraph Disk["로컬 디스크"]
        FB[("data/feedback.jsonl<br/>원래 결과 · 고친 결과")]
        TMP[("임시 파일<br/>업로드·변환 중에만")]
    end

    subgraph Eval["eval/ (명령줄)"]
        SC["synth_check.py<br/>합성 멜로디 점검"]
        MS["make_samples.py<br/>테스트 녹음 만들기"]
        PE["poly_experiment.py<br/>다성 실험"]
    end

    HTML -.-> VF
    HTML -.-> GF
    APP -- "WAV + mode · bpm · floor" --> API
    APP -- "고친 음 (JSON)" --> API
    API --> TR
    API --> MI
    API --> FB
    API --> TMP
    PI --> LR
    PI --> AV
    PO --> BP
    MI --> MD
    SC --> TR
    PE --> PO
```

## 2. 처리 흐름 (서버 안)

```mermaid
flowchart LR
    IN["WAV<br/>22.05kHz 모노"] --> LD["load_audio"]
    LD --> MODE{mode}
    MODE -- "mono<br/>허밍·노래" --> PY["pYIN<br/>프레임별 음높이"] --> SG["음 나누기<br/>음높이 변화 · 소리의 골"]
    MODE -- "poly<br/>양손·반주" --> BP["basic-pitch<br/>모든 음"] --> FL["최저음<br/>자동: 두 무리 경계"] --> SK["가장 높은 줄<br/>skyline"]
    SG --> NT["음 목록 (초)"]
    SK --> NT
    NT --> TP["템포 추정<br/>자기상관 → ±4% 다듬기"]
    TP --> QZ["16분음표 격자<br/>허용 음가로 맞춤"]
    QZ --> KY["조성 추정<br/>Krumhansl"]
    KY --> OUT["Result JSON"]
```

## 3. 설계 결정

| 결정 | 이유 | 대가 |
|---|---|---|
| 로컬 전용, 서버 1개 | 혼자 쓰는 도구, 배포 안 함 (2026-11-27 결정) | 휴대폰 마이크는 HTTPS가 필요해 쓰기 어려움 |
| 브라우저가 WAV로 바꿔 보냄 | 녹음 형식(webm·mp4)이 브라우저마다 달라서, 서버는 WAV만 받으면 되게 | 업로드 크기가 커짐 (1분 약 2.6MB) |
| 동기 처리 | PRD 4장. 1분 녹음이 10~30초 안에 끝남 (측정값) | 처리 중 서버가 그 요청에 묶임. 혼자 쓰니 괜찮음 |
| 서버 시작 때 미리 한 번 돌림 | numba 컴파일·ONNX 불러오기로 첫 요청이 몇 초 느려짐 | 서버가 켜지는 데 몇 초 걸림 |
| 다성 인식 결과 캐시 | 템포·최저음만 바꿀 때 25초 인식을 다시 하지 않으려고 | 메모리에 녹음 4개 분량 보관 |
| 고치기는 브라우저 안에서만 | 반응이 즉각적이고 서버 상태가 필요 없음 | 새로고침하면 고친 내용이 사라짐 |
| 악보는 VexFlow (CDN) | 오선지 그리기를 직접 만들지 않으려고 | 인터넷 연결이 필요 |
| basic-pitch를 ONNX로 | TensorFlow 없이 돌아가고, 앱의 numpy 2와 같은 환경에서 동작 | 패키지를 `--no-deps`로 설치해야 함 |

## 4. 측정값 (이 컴퓨터, 2026-11-27)

| 녹음 | 방식 | 처리 시간 |
|---|---|---|
| 합성 1분 멜로디 | mono | 9.5초 |
| 피아노 양손 59초 | mono | 32초 (대부분 불확실) |
| 피아노 양손 59초 | poly 인식 | 25초 (모델 첫 로딩 포함) / 9.5초 (불러 둔 뒤) |

# PDF2MD
> PDF로 작성된 문서를 Markdown으로 변환하고, vLLM 서버를 이용해 논문·기술 문서의 본문만 선택적으로 번역하는 파이프라인입니다.

![Framework](./Assets/Frameworks.png)

---

## 1. 프로젝트 설명

PDF 문서를 입력으로 받아 다음 단계를 자동으로 수행합니다.

1. **PDF → Markdown 변환** : [Marker](https://github.com/datalab-to/marker) 라이브러리를 사용하여 레이아웃·수식·표를 보존한 마크다운으로 변환합니다.
2. **선택적 번역** : 헤더·수식·코드블록·이미지·References 섹션은 원문을 유지하고, 본문 문장·표 셀·이미지 캡션만 번역합니다.
3. **ZIP 압축 & 다운로드** : 번역 완료된 폴더(마크다운 + 이미지)를 ZIP으로 압축하여 웹 UI에서 즉시 다운로드할 수 있습니다.

번역 엔진으로는 **vLLM OpenAI-Compatible Server**를 사용하며,  
사용자 인터페이스는 **Gradio** 기반 웹 앱(`app.py`)으로 제공됩니다.

---

## 2. 코드 구조

```
pdf2md/
│
├── app.py                  # Gradio 웹 앱 (메인 진입점)
├── run.sh                  # 실행 스크립트 (vLLM 서버 + Gradio 동시 실행)
├── config.yaml             # 전역 설정 (모델 ID, URL, 프롬프트 등)
├── requirements.yaml       # conda 환경 패키지 목록
│
├── src/
│   ├── __init__.py         # 패키지 진입점 (클래스 export)
│   ├── marker_manager.py   # PDF → Markdown 변환 (Marker 래퍼)
│   ├── vLLM_manager.py     # vLLM 서버 프로세스 시작·종료 관리
│   ├── chunk.py            # 마크다운 청크 분할 + 비동기 병렬 번역
│   ├── translation.py      # 단순 동기 번역 클래스 (Translator)
│   └── basic_utils.py      # 공통 유틸리티 함수
│
├── Assets/
│   └── Frameworks.png      # 아키텍처 다이어그램
└── output/                 # 변환·번역 결과 저장 폴더 (자동 생성)
```

### 주요 모듈 설명

| 모듈 | 클래스 / 역할 |
|---|---|
| `marker_manager.py` | `MarkerManager` – PDF를 마크다운+이미지로 변환, `output/<이름>/` 에 저장 |
| `vLLM_manager.py` | `VLLMManager` – `subprocess`로 vLLM API 서버를 실행·종료, 로그를 파일로 기록 |
| `chunk.py` | `MarkdownChunkTranslator` – 헤더 단위로 분할 → 보호 블록(수식·코드·이미지) 제외 → `asyncio.gather` 병렬 번역 → 파일 저장 |
| `translation.py` | `Translator` – 단일 텍스트를 동기 방식으로 번역하는 간단한 클래스 |
| `app.py` | Gradio UI – PDF 드래그&드롭 업로드, 진행 로그 자동 갱신, ZIP 다운로드 |

### `config.yaml` 설정 항목

```yaml
output_dir : "./output"          # 결과 저장 경로
URL        : "http://localhost:8000"  # vLLM 서버 주소

llm_config:
  model_id    : "google/gemma-3-4b-it"  # 사용할 모델
  language    : "korean"                # 번역 대상 언어
  device      : "cuda"
  sys_prompt  : "..."                   # 시스템 프롬프트
  user_prompt : "Translation this sentence"
  temperature : 0.3
```

---

## 3. 사용 방법

### 환경 설정

```bash
# conda 환경 복원
conda env create -f requirements.yaml
conda activate pdf2md
```

### 실행

```bash
./run.sh
```

`run.sh` 한 줄로 아래 두 프로세스를 동시에 실행합니다.

| 프로세스 | 주소 | 설명 |
|---|---|---|
| vLLM 서버 | `http://localhost:8000` | `config.yaml`의 모델을 GPU에 로드 |
| Gradio 웹 앱 | `http://localhost:7860` | 브라우저에서 접속하여 사용 |

### 웹 앱 사용 흐름

```
브라우저 접속 (localhost:7860)
        │
        ▼
 ⏳ 대기 화면  ──── vLLM 서버 준비 완료 시 자동 전환 ────▶  메인 페이지
                                                              │
                       ┌──────────────────────────────────────┤
                       │                                      │
                  📂 좌측 패널                           ⬇️ 우측 패널
              PDF 드래그 & 드롭 업로드               번역 완료 ZIP 다운로드
              [번역 시작] 버튼 클릭                  (번역 완료 시 자동 갱신)
              진행 로그 자동 표시
```

### Jupyter Notebook에서 직접 사용

```python
import nest_asyncio
nest_asyncio.apply()

from src import MarkdownChunkTranslator

translator = MarkdownChunkTranslator(
    yaml_path  = "config.yaml",
    file_path  = "output/sample1/sample1.md",
)
await translator.process_and_save("output/sample1/sample1_translated.md")
```

---

### 번역 대상/제외 기준

| 항목 | 번역 여부 |
|---|---|
| 헤더 (`#`, `##`, …) | ❌ 제외 |
| 수식 (`$$`, `$`) | ❌ 제외 |
| 코드 블록 (` ``` `) | ❌ 제외 |
| 단독 이미지 `![](…)` | ❌ 제외 |
| References 섹션 | ❌ 제외 |
| 일반 본문 문장 | ✅ 번역 |
| 표 셀 텍스트 | ✅ 번역 |
| 이미지 캡션 | ✅ 번역 |

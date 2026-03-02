import asyncio
import json
import os
import shutil
import threading
import queue
import zipfile
import yaml
import requests
import gradio as gr
from pathlib import Path
from src import MarkerManager
from src.chunk import MarkdownChunkTranslator

# ── 설정 로드 ─────────────────────────────────────────────────────────────────
YAML_PATH  = "config.yaml"
with open(YAML_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

SERVER_URL  = CONFIG.get("URL", "http://localhost:8000")
OUTPUT_DIR  = CONFIG.get("output_dir", "./output")
HEALTH_URL  = f"{SERVER_URL}/health"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# 번역 작업 큐 (순차 처리)
_job_queue: queue.Queue = queue.Queue()
# 다운로드 목록: {pdf_stem: zip_path}
_downloads: dict[str, str] = {}
# 로그 파일 경로
_LOG_FILE = os.path.join(OUTPUT_DIR, "app_log.jsonl")

# ── vLLM 서버 헬스 체크 ──────────────────────────────────────────────────────
def check_server_health() -> bool:
    try:
        r = requests.get(HEALTH_URL, timeout=3)
        return r.status_code == 200
    except Exception:
        return False

# ── 로그 기록 헬퍼 ────────────────────────────────────────────────────────────
def _log(msg: str):
    with open(_LOG_FILE, "a", encoding="utf-8") as lf:
        lf.write(json.dumps({"msg": msg}, ensure_ascii=False) + "\n")

# ── 번역 워커 (백그라운드 스레드) ────────────────────────────────────────────
def _translation_worker():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    while True:
        job = _job_queue.get()
        if job is None:
            break
        pdf_path = job
        try:
            loop.run_until_complete(_process_single_pdf(pdf_path))
        except Exception as e:
            _log(f"❌ [{Path(pdf_path).stem}] 오류 발생: {e}")
        finally:
            _job_queue.task_done()

threading.Thread(target=_translation_worker, daemon=True).start()

# ── 단일 PDF 처리 ─────────────────────────────────────────────────────────────
async def _process_single_pdf(pdf_path: str):
    pdf_name = Path(pdf_path).stem

    # 1) PDF → Markdown
    _log(f"⏳ [{pdf_name}] 마크다운 변환 중...")
    marker  = MarkerManager(yaml_path=YAML_PATH)
    md_path = marker.pdf_to_markdown(pdf_path)   # e.g. ./output/sample1/sample1.md
    md_dir  = Path(md_path).parent               # e.g. ./output/sample1/

    # 2) Markdown → 번역본 (같은 폴더에 저장)
    translated_path = md_dir / f"{pdf_name}_translated.md"
    _log(f"📝 [{pdf_name}] 번역 중...")
    translator = MarkdownChunkTranslator(yaml_path=YAML_PATH, file_path=str(md_path))
    await translator.process_and_save(str(translated_path))

    # 3) 출력 폴더 전체를 ZIP으로 압축 (output_dir 바로 아래에 저장)
    zip_path = os.path.join(OUTPUT_DIR, f"{pdf_name}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in md_dir.rglob("*"):
            if file.is_file():
                zf.write(file, arcname=file.relative_to(OUTPUT_DIR))

    _downloads[pdf_name] = zip_path
    _log(f"✅ [{pdf_name}] 번역 완료! 다운로드 목록에 추가됨.")

# ── 로그 읽기 헬퍼 ────────────────────────────────────────────────────────────
def _read_and_clear_log() -> list[str]:
    msgs = []
    if os.path.exists(_LOG_FILE):
        with open(_LOG_FILE, "r", encoding="utf-8") as lf:
            for line in lf:
                line = line.strip()
                if line:
                    try:
                        msgs.append(json.loads(line)["msg"])
                    except Exception:
                        pass
        open(_LOG_FILE, "w").close()
    return msgs

# ── CSS 스타일 ────────────────────────────────────────────────────────────────
CUSTOM_CSS = """
:root {
    --pb-lightest: #EBF5FF;
    --pb-light:    #BDD9F2;
    --pb:          #7ABDE8;
    --pb-dark:     #4A9FD4;
    --pb-darker:   #2878B5;
    --text-dark:   #1E3A5F;
    --text-mid:    #3D6B9F;
    --card-bg:     #F5FBFF;
    --border:      #C8E3F5;
}
body, .gradio-container {
    background: linear-gradient(135deg, #EBF5FF 0%, #D0EAFF 100%) !important;
    font-family: 'Segoe UI', 'Noto Sans KR', sans-serif !important;
}
.gradio-container { max-width: 1240px !important; margin: 0 auto !important; }

#app-header {
    background: linear-gradient(135deg, #4A9FD4 0%, #2878B5 100%);
    border-radius: 16px; padding: 28px 36px; margin-bottom: 24px;
    box-shadow: 0 4px 20px rgba(42,120,181,.25);
}
#app-header h1 { font-size: 2rem; font-weight: 700; margin: 0; color: #fff; }
#app-header p  { font-size: 1rem; margin: 6px 0 0; color: #D0E8FF; }

.card {
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 14px; padding: 20px;
    box-shadow: 0 2px 12px rgba(100,160,220,.1);
}

#upload-zone {
    border: 2.5px dashed var(--pb-dark) !important;
    background: var(--pb-lightest) !important;
    border-radius: 14px !important; min-height: 220px !important;
}

.btn-blue {
    background: linear-gradient(135deg, #4A9FD4 0%, #2878B5 100%) !important;
    color: #fff !important; border: none !important;
    border-radius: 10px !important; font-weight: 600 !important;
    padding: 10px 24px !important;
    box-shadow: 0 3px 10px rgba(42,120,181,.3) !important;
    transition: transform .15s;
}
.btn-blue:hover { transform: translateY(-1px); }

#log-box textarea {
    background: #F0F8FF !important; border: 1px solid var(--border) !important;
    border-radius: 10px !important; font-size: .88rem !important;
    color: var(--text-dark) !important; line-height: 1.6 !important;
}

#dl-table .table-wrap th { background: var(--pb-light) !important; font-weight: 600; }
#dl-table .table-wrap td { border-bottom: 1px solid var(--border); }

.sec-title {
    font-size: 1.05rem; font-weight: 700; color: var(--pb-darker);
    margin-bottom: 10px;
}

/* 대기 화면 애니메이션 */
@keyframes spin { to { transform: rotate(360deg); } }
.spinner { animation: spin 1.2s linear infinite; display: inline-block; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.45} }
#wait-msg { animation: pulse 2s infinite; font-size:1.3rem; font-weight:600; color:#2878B5; text-align:center; }
"""

# ── Gradio 앱 빌드 ────────────────────────────────────────────────────────────
with gr.Blocks(title="PDF 번역기") as demo:

    # ── 상태 ──────────────────────────────────────────────────────────────────
    log_state    = gr.State([])   # 누적 로그 메시지
    server_state = gr.State(False)

    # ── 헤더 ──────────────────────────────────────────────────────────────────
    gr.HTML("""
    <div id="app-header">
        <h1>📄 PDF 번역기</h1>
        <p>PDF를 업로드하면 vLLM 서버를 이용해 자동으로 번역합니다.</p>
    </div>
    """)

    # ═══════════════════════════════════════════════════════════════════════════
    # 대기 화면
    # ═══════════════════════════════════════════════════════════════════════════
    with gr.Group(visible=True) as wait_page:
        gr.HTML("""
        <div class="card" style="text-align:center;padding:48px;">
            <div style="font-size:4rem;" class="spinner">⟳</div>
            <div class="sec-title" style="justify-content:center;font-size:1.4rem;margin-top:16px;">
                vLLM 서버 연결 대기 중
            </div>
            <p style="color:#6499C4;margin:0;">
                서버가 준비되면 <b>자동으로</b> 메인 페이지로 이동합니다.<br>
                처음 실행 시 모델 로딩에 수 분이 소요될 수 있습니다.
            </p>
        </div>
        """)
        wait_msg = gr.HTML(
            value='<div id="wait-msg">⏳ 서버 상태 확인 중...</div>'
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # 메인 페이지
    # ═══════════════════════════════════════════════════════════════════════════
    with gr.Group(visible=False) as main_page:
        with gr.Row(equal_height=False):

            # ── 좌측: 업로드 ─────────────────────────────────────────────────
            with gr.Column(scale=1):
                gr.HTML('<div class="sec-title">📂 PDF 파일 업로드</div>')
                gr.HTML("""
                <p style="color:#5A8BB0;font-size:.9rem;margin-bottom:10px;">
                    PDF를 드래그&드롭하거나 클릭하여 선택하세요.<br>
                    여러 파일을 한 번에 추가 가능하며 순차적으로 번역됩니다.
                </p>
                """)
                pdf_input = gr.File(
                    label="", file_types=[".pdf"],
                    file_count="multiple", type="filepath",
                    elem_id="upload-zone",
                )
                upload_btn = gr.Button("🚀  번역 시작", elem_classes=["btn-blue"])

                gr.HTML('<hr style="border-color:#C8E3F5;margin:18px 0;">')
                gr.HTML('<div class="sec-title">📋 진행 상황 로그</div>')
                log_display = gr.Textbox(
                    label="", value="", lines=14, max_lines=14,
                    interactive=False, elem_id="log-box",
                    placeholder="번역 진행 상황이 여기에 표시됩니다...",
                )

            # ── 우측: 다운로드 ───────────────────────────────────────────────
            with gr.Column(scale=1):
                gr.HTML('<div class="sec-title">⬇️ 번역 완료 파일 다운로드</div>')
                gr.HTML("""
                <p style="color:#5A8BB0;font-size:.9rem;margin-bottom:10px;">
                    번역이 완료되면 <b>자동으로</b> 아래 목록에 추가됩니다.<br>
                    파일명을 선택 후 <b>ZIP 다운로드</b> 버튼을 클릭하세요.
                </p>
                """)
                download_table = gr.Dataframe(
                    headers=["파일명", "ZIP 경로"],
                    datatype=["str", "str"],
                    value=[],
                    interactive=True,
                    label="",
                    elem_id="dl-table",
                    row_count=(6, "dynamic"),
                )
                selected_name = gr.State("")
                dl_btn = gr.DownloadButton(
                    "⬇️  ZIP 다운로드",
                    elem_classes=["btn-blue"],
                    visible=False,
                )

                gr.HTML('<hr style="border-color:#C8E3F5;margin:24px 0;">')
                gr.HTML("""
                <div class="card" style="font-size:.88rem;color:#5A8BB0;line-height:1.8;">
                    <div style="font-weight:700;color:#2878B5;margin-bottom:8px;">💡 사용 안내</div>
                    <ol style="margin:0;padding-left:18px;">
                        <li>왼쪽에 PDF 파일을 업로드합니다.</li>
                        <li><b>번역 시작</b> 버튼을 클릭합니다.</li>
                        <li>로그에서 진행 상황을 확인합니다.</li>
                        <li>완료 후 오른쪽에서 ZIP 파일을 다운로드합니다.</li>
                    </ol>
                    <div style="margin-top:12px;font-size:.82rem;color:#88AACC;">
                        ※ ZIP 파일은 <code>output/</code> 폴더에 저장됩니다.
                    </div>
                </div>
                """)

    # ── Timer: 서버 폴링 & 로그·다운로드 자동 갱신 ──────────────────────────
    server_timer = gr.Timer(value=3)  # 3초마다 실행
    ui_timer     = gr.Timer(value=4)  # 4초마다 실행

    # ── 이벤트 ────────────────────────────────────────────────────────────────

    # [서버 폴링] vLLM이 켜지면 자동으로 메인 페이지로 전환
    def poll_server(is_ready):
        if is_ready:
            # 이미 전환됨 → 아무 것도 하지 않음
            return (
                is_ready,
                gr.update(), gr.update(), gr.update(),
            )
        alive = check_server_health()
        if alive:
            msg = '<div id="wait-msg">✅ 서버 연결 완료!</div>'
        else:
            msg = '<div id="wait-msg">⏳ vLLM 서버 준비 중... (자동 전환 대기)</div>'
        return (
            alive,
            gr.update(visible=not alive),   # wait_page
            gr.update(visible=alive),       # main_page
            msg,
        )

    server_timer.tick(
        fn=poll_server,
        inputs=[server_state],
        outputs=[server_state, wait_page, main_page, wait_msg],
    )

    # [UI 갱신] 로그 + 다운로드 목록 자동 갱신
    def auto_refresh(log_st):
        # 로그 갱신
        new_msgs = _read_and_clear_log()
        combined = (log_st + new_msgs)[-60:]
        log_text = "\n".join(combined)

        # 다운로드 목록 갱신
        rows = [[name, path] for name, path in _downloads.items()]
        btn_visible = len(rows) > 0

        return combined, log_text, rows, gr.update(visible=btn_visible)

    ui_timer.tick(
        fn=auto_refresh,
        inputs=[log_state],
        outputs=[log_state, log_display, download_table, dl_btn],
    )

    # [번역 시작] PDF 파일을 큐에 추가
    def on_upload(files, log_st):
        if not files:
            return log_st, "\n".join(log_st)

        if not check_server_health():
            msg = "❌ vLLM 서버가 응답하지 않습니다. 서버 상태를 확인해주세요."
            combined = log_st + [msg]
            return combined, "\n".join(combined[-60:])

        new_msgs = []
        for f in files:
            pdf_path = f if isinstance(f, str) else f.name
            name = Path(pdf_path).stem
            _job_queue.put(pdf_path)
            new_msgs.append(f"📥 [{name}] 번역 대기열에 추가됨")

        combined = (log_st + new_msgs)[-60:]
        return combined, "\n".join(combined)

    upload_btn.click(
        fn=on_upload,
        inputs=[pdf_input, log_state],
        outputs=[log_state, log_display],
    )

    # [다운로드] 테이블에서 행 선택 시 ZIP 경로를 DownloadButton에 연결
    def on_select(evt: gr.SelectData):
        try:
            name = evt.value  # 선택된 셀 값 (파일명 열)
            if isinstance(name, str) and name in _downloads:
                return _downloads[name], gr.update(visible=True)
        except Exception:
            pass
        return None, gr.update(visible=False)

    download_table.select(
        fn=on_select,
        inputs=None,
        outputs=[dl_btn, dl_btn],
    )

# ── 앱 실행 ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        css=CUSTOM_CSS,
    )

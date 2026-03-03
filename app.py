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
import torch
from src import MarkerManager
from src.chunk import MarkdownChunkTranslator

# ── 설정 로드 ─────────────────────────────────────────────────────────────────
YAML_PATH  = "config.yaml"
with open(YAML_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

SERVER_URL  = CONFIG.get("URL", "http://localhost:8000")
OUTPUT_DIR  = str(Path(CONFIG.get("output_dir", "./output")).resolve())  # 절대 경로
APP_PORT    = int(CONFIG.get("app_port", 7860))
HEALTH_URL  = f"{SERVER_URL}/health"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# 번역 작업 큐 (순차 처리)
_job_queue: queue.Queue = queue.Queue()
# 다운로드 목록: {pdf_stem: zip_abs_path}
_downloads: dict[str, str] = {}
# 로그 파일 경로
_LOG_FILE = os.path.join(OUTPUT_DIR, "app_log.jsonl")

# ── 기존 ZIP 파일 사전 로드 ───────────────────────────────────────────────────
def _preload_existing_zips():
    for zip_file in sorted(Path(OUTPUT_DIR).glob("*.zip")):
        _downloads[zip_file.stem] = str(zip_file.resolve())
    if _downloads:
        print(f"[앱 시작] 기존 ZIP {len(_downloads)}개를 다운로드 목록에 로드했습니다.")

_preload_existing_zips()

# ── Marker 모델 초기화 (앱 시작 시 1회만 수행) ──────────────────────────
_marker_instance = MarkerManager(yaml_path=YAML_PATH)

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

# ── 동일 PDF 기존 파일 삭제 ───────────────────────────────────────────────────
def _clean_existing(pdf_name: str):
    out_folder = Path(OUTPUT_DIR) / pdf_name
    zip_file   = Path(OUTPUT_DIR) / f"{pdf_name}.zip"
    if out_folder.exists():
        shutil.rmtree(out_folder)
        _log(f"🗑️  [{pdf_name}] 기존 출력 폴더 삭제 완료")
    if zip_file.exists():
        zip_file.unlink()
        _log(f"🗑️  [{pdf_name}] 기존 ZIP 파일 삭제 완료")
    if pdf_name in _downloads:
        del _downloads[pdf_name]

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
            loop.run_until_complete(_process_single_pdf(pdf_path, _marker_instance))
        except Exception as e:
            _log(f"❌ [{Path(pdf_path).stem}] 오류 발생: {e}")
        finally:
            _job_queue.task_done()

threading.Thread(target=_translation_worker, daemon=True).start()

# ── 단일 PDF 처리 ─────────────────────────────────────────────────────────────
async def _process_single_pdf(pdf_path: str, marker: MarkerManager):
    pdf_name = Path(pdf_path).stem
    _clean_existing(pdf_name)

    _log(f"⏳ [{pdf_name}] 마크다운 변환 중...")
    md_path = marker.pdf_to_markdown(pdf_path)
    md_dir  = Path(md_path).parent

    translated_path = md_dir / f"{pdf_name}_translated.md"
    _log(f"📝 [{pdf_name}] 번역 중...")
    translator = MarkdownChunkTranslator(yaml_path=YAML_PATH, file_path=str(md_path))
    await translator.process_and_save(str(translated_path))

    zip_base = Path(OUTPUT_DIR) / pdf_name
    shutil.make_archive(base_name=str(zip_base), format="zip", root_dir=str(md_dir))

    zip_path = str(zip_base) + ".zip"
    # with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    #     for file in md_dir.rglob("*"):
    #         if file.is_file():
    #             zf.write(file, arcname=file.relative_to(md_dir))

    _downloads[pdf_name] = str(Path(zip_path).resolve())
    _log(f"✅ [{pdf_name}] 번역 완료! 다운로드 목록에 추가됨.")
    torch.cuda.empty_cache()

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

# ── 다운로드 목록 HTML 렌더러 ─────────────────────────────────────────────────
def _render_download_list(downloads: dict) -> str:
    """다운로드 목록을 드래그 스크롤 + 더블클릭 다운로드 지원 HTML로 렌더링"""
    if not downloads:
        return """
        <div style="text-align:center;padding:60px 20px;color:#8BB0CC;font-size:.95rem;
                    background:#F5FBFF;border-radius:12px;border:1px dashed #C8E3F5;">
            📭 번역 완료된 파일이 없습니다.<br>
            <span style="font-size:.82rem;">PDF를 업로드하고 번역을 시작하세요.</span>
        </div>"""

    items_html = ""
    for name, abs_path in downloads.items():
        # Gradio 파일 서빙 엔드포인트: /gradio_api/file=<절대경로>
        file_url = f"/gradio_api/file={abs_path}"
        items_html += f"""
        <a href="{file_url}" download="{name}.zip" class="dl-item"
           title="{abs_path}&#10;더블클릭 또는 클릭하여 다운로드">
            <span class="dl-icon">📦</span>
            <div class="dl-info">
                <div class="dl-name">{name}.zip</div>
                <div class="dl-hint">클릭하여 다운로드</div>
            </div>
            <span class="dl-arrow">⬇️</span>
        </a>"""

    # 드래그 스크롤 JS (mousedown → mousemove)
    drag_js = """
    <script>
    (function() {
        function initDrag(el) {
            if (!el) return;
            let active = false, startY = 0, scrollTop = 0;
            el.addEventListener('mousedown', function(e) {
                // a 태그 클릭 동작 방해 안 함 (다운로드 허용)
                active = true;
                startY    = e.pageY;
                scrollTop = el.scrollTop;
                el.style.cursor = 'grabbing';
                el.style.userSelect = 'none';
            });
            document.addEventListener('mouseup', function() {
                active = false;
                if (el) { el.style.cursor = 'grab'; el.style.userSelect = ''; }
            });
            el.addEventListener('mousemove', function(e) {
                if (!active) return;
                var dy = e.pageY - startY;
                el.scrollTop = scrollTop - dy;
            });
        }
        // Gradio가 DOM을 늦게 그릴 수 있으므로 약간 대기
        setTimeout(function() { initDrag(document.getElementById('dl-scroll')); }, 300);
    })();
    </script>
    """

    return f"""
    <div id="dl-scroll" style="
        max-height: 320px;
        overflow-y: auto;
        overflow-x: hidden;
        cursor: grab;
        border-radius: 12px;
        border: 1px solid #C8E3F5;
        background: #F5FBFF;
        padding: 6px 0;
    ">
        {items_html}
    </div>
    {drag_js}
    <style>
    .dl-item {{
        display: flex; align-items: center; gap: 12px;
        padding: 11px 16px;
        border-bottom: 1px solid #E0EFF9;
        text-decoration: none;
        color: #1E3A5F;
        transition: background .15s;
        cursor: pointer;
    }}
    .dl-item:hover {{ background: #D9EEFB; }}
    .dl-item:last-child {{ border-bottom: none; }}
    .dl-icon {{ font-size: 1.4rem; flex-shrink: 0; }}
    .dl-info {{ flex: 1; min-width: 0; }}
    .dl-name  {{ font-weight: 600; font-size: .92rem; white-space: nowrap;
                 overflow: hidden; text-overflow: ellipsis; }}
    .dl-hint  {{ font-size: .75rem; color: #7ABDE8; margin-top: 2px; }}
    .dl-arrow {{ font-size: 1rem; color: #4A9FD4; flex-shrink: 0; }}
    </style>
    """

# ── CSS ───────────────────────────────────────────────────────────────────────
CUSTOM_CSS = """
:root {
    --pb-lightest: #EBF5FF; --pb-light: #BDD9F2; --pb: #7ABDE8;
    --pb-dark: #4A9FD4; --pb-darker: #2878B5;
    --text-dark: #1E3A5F; --card-bg: #F5FBFF; --border: #C8E3F5;
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
.sec-title {
    font-size: 1.05rem; font-weight: 700; color: var(--pb-darker);
    margin-bottom: 10px;
}
@keyframes spin   { to   { transform: rotate(360deg); } }
@keyframes pulse  { 0%,100%{opacity:1} 50%{opacity:.45} }
.spinner  { animation: spin 1.2s linear infinite; display: inline-block; }
#wait-msg { animation: pulse 2s infinite; font-size:1.3rem; font-weight:600;
            color:#2878B5; text-align:center; margin-top: 12px; }
"""

# ── Gradio 앱 빌드 ────────────────────────────────────────────────────────────
with gr.Blocks(title="PDF 번역기") as demo:

    log_state    = gr.State([])
    server_state = gr.State(False)

    gr.HTML("""
    <div id="app-header">
        <h1>📄 PDF 번역기</h1>
        <p>PDF를 업로드하면 vLLM 서버를 이용해 자동으로 번역합니다.</p>
    </div>
    """)

    # ── 대기 화면 ─────────────────────────────────────────────────────────────
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
        wait_msg = gr.HTML(value='<div id="wait-msg">⏳ 서버 상태 확인 중...</div>')

    # ── 메인 페이지 ───────────────────────────────────────────────────────────
    with gr.Group(visible=False) as main_page:
        with gr.Row(equal_height=False):

            # 좌측: 업로드
            with gr.Column(scale=1):
                gr.HTML('<div class="sec-title">📂 PDF 파일 업로드</div>')
                gr.HTML("""
                <p style="color:#5A8BB0;font-size:.9rem;margin-bottom:10px;">
                    PDF를 드래그&드롭하거나 클릭하여 선택하세요.<br>
                    여러 파일을 한 번에 추가 가능하며 순차적으로 번역됩니다.<br>
                    <span style="color:#e87070;">동일한 이름의 PDF는 기존 파일을 덮어씁니다.</span>
                </p>
                """)
                pdf_input = gr.File(
                    label="", file_types=[".pdf"],
                    file_count="multiple", type="filepath",
                    elem_id="upload-zone", height=220,
                )
                upload_btn = gr.Button("🚀  번역 시작", elem_classes=["btn-blue"])

                gr.HTML('<hr style="border-color:#C8E3F5;margin:18px 0;">')
                gr.HTML('<div class="sec-title">📋 진행 상황 로그</div>')
                log_display = gr.Textbox(
                    label="", value="", lines=12, max_lines=12,
                    interactive=False, elem_id="log-box",
                    placeholder="번역 진행 상황이 여기에 표시됩니다...",
                )

            # 우측: 다운로드
            with gr.Column(scale=1):
                gr.HTML('<div class="sec-title">⬇️ 번역 완료 파일 다운로드</div>')
                gr.HTML("""
                <p style="color:#5A8BB0;font-size:.9rem;margin-bottom:10px;">
                    번역이 완료되면 <b>자동으로</b> 아래 목록에 추가됩니다.<br>
                    항목을 <b>클릭</b>하면 ZIP 파일이 다운로드됩니다.<br>
                    목록이 많으면 <b>드래그</b> 또는 <b>마우스 휠</b>로 스크롤하세요.
                </p>
                """)
                # 커스텀 HTML 다운로드 목록 (드래그 스크롤 + 클릭 다운로드)
                dl_html = gr.HTML(value=_render_download_list(_downloads))

                gr.HTML('<hr style="border-color:#C8E3F5;margin:24px 0;">')
                gr.HTML("""
                <div class="card" style="font-size:.88rem;color:#5A8BB0;line-height:1.8;">
                    <div style="font-weight:700;color:#2878B5;margin-bottom:8px;">💡 사용 안내</div>
                    <ol style="margin:0;padding-left:18px;">
                        <li>왼쪽에 PDF 파일을 업로드합니다.</li>
                        <li><b>번역 시작</b> 버튼을 클릭합니다.</li>
                        <li>로그에서 진행 상황을 확인합니다.</li>
                        <li>완료 후 오른쪽 목록 항목을 클릭하여 다운로드합니다.</li>
                    </ol>
                    <div style="margin-top:12px;font-size:.82rem;color:#88AACC;">
                        ※ ZIP 파일은 <code>output/</code> 폴더에 저장됩니다.<br>
                        ※ 동일 이름의 PDF 재업로드 시 기존 파일이 삭제됩니다.<br>
                        ※ 포트는 <code>config.yaml</code>의 <code>app_port</code>로 변경 가능합니다.
                    </div>
                </div>
                """)

    # ── Timer ─────────────────────────────────────────────────────────────────
    server_timer = gr.Timer(value=3)
    ui_timer     = gr.Timer(value=4)

    # ── 서버 폴링 ─────────────────────────────────────────────────────────────
    def poll_server(is_ready):
        if is_ready:
            return is_ready, gr.update(), gr.update(), gr.update()
        alive = check_server_health()
        msg = (
            '<div id="wait-msg">✅ 서버 연결 완료!</div>'
            if alive else
            '<div id="wait-msg">⏳ vLLM 서버 준비 중... (자동 전환 대기)</div>'
        )
        return alive, gr.update(visible=not alive), gr.update(visible=alive), msg

    server_timer.tick(
        fn=poll_server,
        inputs=[server_state],
        outputs=[server_state, wait_page, main_page, wait_msg],
    )

    # ── UI 자동 갱신 ──────────────────────────────────────────────────────────
    def auto_refresh(log_st):
        new_msgs = _read_and_clear_log()
        combined = (log_st + new_msgs)[-60:]
        return combined, "\n".join(combined), _render_download_list(_downloads)

    ui_timer.tick(
        fn=auto_refresh,
        inputs=[log_state],
        outputs=[log_state, log_display, dl_html],
    )

    # ── 번역 시작 ─────────────────────────────────────────────────────────────
    def on_upload(files, log_st):
        if not files:
            return log_st, "\n".join(log_st)
        if not check_server_health():
            msg = "❌ vLLM 서버가 응답하지 않습니다."
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

# ── 앱 실행 ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=APP_PORT,
        share=False,
        css=CUSTOM_CSS,
        allowed_paths=[OUTPUT_DIR],   # Gradio 파일 서빙 허용 경로
    )

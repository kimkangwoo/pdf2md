import asyncio
import re
import os
import yaml
from typing import List, Dict
from functools import partial
from openai import AsyncOpenAI
from transformers import AutoConfig, AutoTokenizer
from langchain_text_splitters import MarkdownHeaderTextSplitter

class MarkdownChunkTranslator:
    def __init__(self, yaml_path: str, file_path: str):
        with open(yaml_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
            
        llm_config = self.config["llm_config"]
        self.model_id = llm_config["model_id"]
        self.base_url = self.config.get("URL", "http://localhost:8000") + "/v1"
        self.system_prompt = llm_config["sys_prompt"] + llm_config["language"]
        self.user_prompt = llm_config["user_prompt"]
        self.temperature = llm_config["temperature"]
        self.file_path = file_path
        
        self.client = AsyncOpenAI(base_url=self.base_url, api_key="EMPTY")
        self.tokenizer = None
        self.max_tokens = 0

    async def _initialize_model_info(self):
        loop = asyncio.get_event_loop()
        
        # functools.partial을 사용하여 키워드 인자(trust_remote_code)를 안전하게 전달
        config_func = partial(AutoConfig.from_pretrained, self.model_id, trust_remote_code=True)
        tokenizer_func = partial(AutoTokenizer.from_pretrained, self.model_id, trust_remote_code=True)
        
        config = await loop.run_in_executor(None, config_func)
        self.tokenizer = await loop.run_in_executor(None, tokenizer_func)
        self.max_tokens = getattr(config, "max_position_embeddings", 4096)

    def _split_by_headers(self, text: str) -> Dict[str, str]:
        headers_to_split_on = [("#", "H1"), ("##", "H2"), ("###", "H3"), ("####", "H4")]
        splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on, strip_headers=False)
        splits = splitter.split_text(text)
        
        header_content_map = {}
        for i, split in enumerate(splits):
            header_key = " > ".join(split.metadata.values()) if split.metadata else f"Section_{i}"
            header_content_map[header_key] = split.page_content
        return header_content_map

    # ── 번역 제외 패턴 ──────────────────────────────────────────
    # 헤더 (# ~ ####)
    _RE_HEADER      = re.compile(r'^#{1,4}\s')
    # 순수 이미지 행  (![alt](url)  – 캡션 없이 단독 등장)
    _RE_IMAGE_ONLY  = re.compile(r'^\s*!\[.*?\]\(.*?\)\s*$')
    # 블록 수식 ($$...$$)
    _RE_MATH_BLOCK  = re.compile(r'^\$\$[\s\S]*?\$\$\s*$')
    # 코드 펜스 시작/끝
    _RE_CODE_FENCE  = re.compile(r'^```')
    # HTML 태그만 있는 행
    _RE_HTML_TAG    = re.compile(r'^\s*<[^>]+>\s*$')
    # References 섹션 헤더 (대소문자 무관)
    _RE_REFERENCES  = re.compile(r'^#{0,4}\s*references?\s*$', re.IGNORECASE)
    # 표 구분선 (|---|---|)
    _RE_TABLE_SEP   = re.compile(r'^\|?\s*[-:]+[\s|:-]*$')

    def _classify_lines(self, text: str) -> List[Dict]:
        """
        텍스트를 줄 단위로 분석하여 세그먼트 목록을 반환합니다.
        각 세그먼트는 {"text": str, "translate": bool} 형식입니다.

        번역 제외: 헤더, 단독 이미지, 블록 수식, 코드 블록, References 이후 줄
        번역 포함: 일반 문장, 이미지 캡션(순수 이미지가 아닌 줄), 표 셀 내 텍스트
        """
        lines = text.split('\n')
        segments: List[Dict] = []
        
        in_code_block   = False
        in_math_block   = False
        in_references   = False

        def _flush(buf: List[str], translate: bool):
            """버퍼를 하나의 세그먼트로 추가"""
            chunk = '\n'.join(buf)
            if chunk.strip():
                segments.append({"text": chunk + '\n', "translate": translate})

        protected_buf: List[str] = []
        translate_buf:  List[str] = []

        def _push_protected(line: str):
            nonlocal translate_buf
            if translate_buf:
                _flush(translate_buf, True); translate_buf = []
            protected_buf.append(line)

        def _push_translate(line: str):
            nonlocal protected_buf
            if protected_buf:
                _flush(protected_buf, False); protected_buf = []
            translate_buf.append(line)

        for line in lines:
            stripped = line.strip()

            # 1) 코드 블록 토글
            if self._RE_CODE_FENCE.match(stripped):
                in_code_block = not in_code_block
                _push_protected(line)
                continue
            if in_code_block:
                _push_protected(line)
                continue

            # 2) 블록 수식 토글
            if stripped == '$$':
                in_math_block = not in_math_block
                _push_protected(line)
                continue
            if in_math_block:
                _push_protected(line)
                continue

            # 3) References 섹션 진입 (이후 모두 번역 제외)
            if self._RE_REFERENCES.match(stripped):
                in_references = True
                _push_protected(line)
                continue
            if in_references:
                _push_protected(line)
                continue

            # 4) 헤더 – 번역 제외
            if self._RE_HEADER.match(stripped):
                _push_protected(line)
                continue

            # 5) 단독 이미지 – 번역 제외 (캡션 없음)
            if self._RE_IMAGE_ONLY.match(stripped):
                _push_protected(line)
                continue

            # 6) 인라인 수식 (`$...$`) 이 포함된 줄은 번역 포함
            #    (줄 전체가 $$가 아닌 경우)
            if self._RE_MATH_BLOCK.match(stripped):
                _push_protected(line)
                continue

            # 7) 표 구분선 – 번역 제외
            if self._RE_TABLE_SEP.match(stripped):
                _push_protected(line)
                continue

            # 8) HTML 태그 단독 줄 – 번역 제외
            if self._RE_HTML_TAG.match(stripped):
                _push_protected(line)
                continue

            # 9) 나머지(일반 텍스트, 이미지 캡션, 표 셀, 인라인 수식 포함 줄) – 번역 포함
            _push_translate(line)

        # 남은 버퍼 처리
        if protected_buf:
            _flush(protected_buf, False)
        if translate_buf:
            _flush(translate_buf, True)

        return segments

    def _split_text_block(self, text: str, limit: int) -> List[str]:
        """토큰 제한을 초과하는 긴 텍스트 블록을 문단 단위로 분할"""
        paragraphs = text.split('\n')
        chunks = []
        current_chunk = ""
        
        for p in paragraphs:
            test_chunk = current_chunk + p + "\n"
            if len(self.tokenizer.encode(test_chunk)) <= limit:
                current_chunk = test_chunk
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = p + "\n"
        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    def _split_safely_by_tokens(self, text: str) -> List[Dict]:
        """
        줄 단위로 세그먼트를 분류하고, 번역 대상 세그먼트가
        토큰 제한을 초과하면 추가로 분할합니다.
        """
        safe_limit = int(self.max_tokens * 0.8)
        segments    = self._classify_lines(text)
        final       = []

        for seg in segments:
            if not seg["translate"]:
                final.append(seg)
            else:
                if len(self.tokenizer.encode(seg["text"])) <= safe_limit:
                    final.append(seg)
                else:
                    for sub in self._split_text_block(seg["text"], safe_limit):
                        final.append({"text": sub, "translate": True})
        return final

    async def _translate_chunk(self, text: str, max_retries: int = 5, base_wait: float = 5.0) -> str:
        """번역 요청. Connection 에러 발생 시 최대 max_retries 회까지 재시도합니다."""
        if not text.strip():
            return text

        for attempt in range(1, max_retries + 1):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model_id,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": f"{self.user_prompt} : {text}"}
                    ],
                    temperature=self.temperature,
                    stream=False
                )
                return response.choices[0].message.content

            except Exception as e:
                if attempt < max_retries:
                    wait_time = base_wait * attempt  # 5s → 10s → 15s → 20s
                    print(f"[재시도 {attempt}/{max_retries}] 연결 오류 발생. {wait_time:.0f}초 후 재시도... ({e})")
                    await asyncio.sleep(wait_time)
                else:
                    print(f"[최대 재시도 초과] 원문을 유지합니다. ({e})")
                    return text  # 모든 재시도 실패 시 원문 유지

    async def process_and_save(self, save_path: str):
        if not self.tokenizer:
            await self._initialize_model_info()

        with open(self.file_path, "r", encoding="utf-8") as f:
            file_content = f.read()

        print("분석 중...")
        header_map = self._split_by_headers(file_content)
        translated_sections = []

        total_headers = len(header_map)
        for idx, (header_key, content) in enumerate(header_map.items()):
            print(f"[{idx+1}/{total_headers}] 섹션 처리 중: {header_key}")

            segments = self._split_safely_by_tokens(content)

            # 원문으로 초기화 후, 번역 대상 세그먼트만 비동기 병렬 처리
            results       = [seg["text"] for seg in segments]
            tasks         = []
            task_indices  = []

            for i, seg in enumerate(segments):
                if seg["translate"] and seg["text"].strip():
                    tasks.append(self._translate_chunk(seg["text"]))
                    task_indices.append(i)

            if tasks:
                translated_results = await asyncio.gather(*tasks)
                for i, translated_text in zip(task_indices, translated_results):
                    results[i] = translated_text

            translated_sections.append("".join(results))

        final_markdown = "\n\n".join(translated_sections)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(final_markdown)

        print(f"완료! 저장 위치: {save_path}")
        return final_markdown
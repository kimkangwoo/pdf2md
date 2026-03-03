import subprocess
import atexit
import os
import yaml
from urllib.parse import urlparse

class VLLMManager:
    """
    vLLM 서버의 생명주기를 관리하는 클래스입니다.
    Gemma 모델 호환성 이슈 해결을 위해 실행 옵션을 자동으로 보정합니다.
    """
    def __init__(self, config_path="config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
            
        self.output_dir = self.config.get("output_dir", "./output")
        os.makedirs(self.output_dir, exist_ok=True)
        
        self.log_file_path = os.path.join(self.output_dir, "vllm_server.log")
        self.process = None
        self.log_file = None
        
        atexit.register(self.stop)

    def start(self, extra_args=None):
        if self.process is not None:
            print("vLLM server is already running.")
            return
            
        llm_config = self.config.get("llm_config", {})
        model_id = llm_config.get("model_id", "google/translategemma-4b-it")
        
        # URL에서 host와 port를 파싱하여 적용합니다
        url = self.config.get("URL", "http://localhost:8000")
        parsed_url = urlparse(url)
        host = parsed_url.hostname or "0.0.0.0"
        port = parsed_url.port or 8000
        
        self.log_file = open(self.log_file_path, "w", encoding="utf-8")
        
        # 기본 실행 명령어 구성
        cmd = [
            "python", "-m", "vllm.entrypoints.openai.api_server",
            "--model", model_id,
            "--host", host,
            "--port", str(port),
            "--trust-remote-code",
            "--dtype", "auto",
            "--gpu-memory-utilization", str(llm_config.get("gpu_memory_utilization", 0.9)),
        ]
        
        if extra_args:
            cmd.extend(extra_args)
            
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=self.log_file,
                stderr=subprocess.STDOUT
            )
            print(f"vLLM server started: http://{host}:{port} (PID: {self.process.pid})")
            print(f"Model: {model_id}")
            print(f"Logs: {self.log_file_path}")
        except Exception as e:
            print(f"Failed to start vLLM server: {e}")
            self.stop()

    def stop(self):
        if self.process is not None:
            print("Stopping vLLM server...")
            try:
                self.process.terminate()
                self.process.wait(timeout=10) # 타임아웃을 10초로 연장하여 안전한 종료 유도
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            except ProcessLookupError:
                pass
            finally:
                self.process = None
                
            print("vLLM server stopped.")
            
        if self.log_file is not None and not self.log_file.closed:
            self.log_file.flush()
            self.log_file.close()
            self.log_file = None
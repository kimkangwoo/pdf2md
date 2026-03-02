import os
import yaml
import requests
import json

from src import check_file_path


class Translator():
    """
    마크다운으로 변환된 파일에서 본문 부분만을 번역합니다.
    마크다운 문법이 적용된 부분에서 처리는 안됩니다. 
    """

    def __init__(self, yaml_path:str, file_path:str, url:str="http://localhost:8000"):
        super().__init__()

        self.url = f"{url}/v1/chat/completions"
        self.headers = {"Content-Type": "application/json"}

        # check server health
        self.check_server_health()

        check_file_path(yaml_path)
        check_file_path(file_path)

        # load config.yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        # load mark down
        with open(file_path, "r", encoding="utf-8") as f:
            mark_down_data = f.readline()


        # varriable settings
        llm_config = self.config["llm_config"]

        self.model_id = llm_config["model_id"]
        self.system_prompt = llm_config["system_prompt"] + llm_config["language"]
        self.user_prompt = llm_config["user_prompt"]
        self.temperature = llm_config["temperature"]
        self.mark_down_data = mark_down_data

        
    def __call__(self):
        print(self.config)

    # translation of text
    def __call__(self, text):
        return self.inference(text)

    # inference of call
    def inference(self, text):
        # setting on prompts
        data = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"{self.user_prompt} : {text}"}
            ],
            "stream": False,  # 한 번에 전체 결과를 받음
            "temperature" : self.temperature
        }

        response = requests.post(self.url, headers=self.headers, data=json.dumps(data))
        result = response.json()

        # output results
        return result['choices'][0]['message']['content']

    def check_server_health(self):
        import time
        import requests
        
        health_url = f"{self.url.replace('/v1/chat/completions', '')}/health"
        print("Waiting for vLLM server to be ready...")
        
        # 60초 동안 서버가 열렸는지 확인합니다
        for _ in range(12):
            try:
                response = requests.get(health_url, timeout=3)
                if response.status_code == 200:
                    print("Server is healthy and ready to accept requests!")
                    break
            except requests.exceptions.RequestException:
                print("Server is not healthy. Retrying in 5 seconds...")
            
                # 5초마다 서버가 열렸는지 계속 확인합니다
                time.sleep(5)
        else:
            raise Exception("Server is not healthy. Please start the server manually.")




#!/bin/bash
# ========================================== #
source ~/miniconda3/etc/profile.d/conda.sh
trap 'kill 0' SIGINT SIGTERM EXIT # ALL_stop

conda activate pdf2md
# ========================================== #

# vLLM 서버를 백그라운드로 실행
echo "🚀 vLLM 서버를 시작합니다..."
CUDA_VISIBLE_DEVICES=0 python -m vllm.entrypoints.openai.api_server \
    --model "$(python -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['llm_config']['model_id'])")" \
    --host 0.0.0.0 \
    --port 8000 \
    --trust-remote-code \
    --dtype auto \
    --gpu-memory-utilization "$(python -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['llm_config'].get('gpu_memory_utilization', 0.9))")" \
    >> ./output/vllm_server.log 2>&1 &

echo "🌐 Gradio 앱을 시작합니다 (http://localhost:7860)"
python app.py

wait

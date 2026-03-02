import os 

# check file exists
def check_file_path(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"적합하지 않거나, 경로에 파일이 없습니다. {path}")
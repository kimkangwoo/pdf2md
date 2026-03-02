from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict
from marker.output import text_from_rendered
import yaml
import os

class MarkerManager:
    def __init__(self, yaml_path="./config.yaml"):
        with open(yaml_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        self.output_dir = self.config["output_dir"]

        self.converter = PdfConverter(
            artifact_dict=create_model_dict(),
        )
    
    def pdf_to_markdown(self, path_pdf):
        # check file exists
        if not os.path.exists(path_pdf): 
            raise FileNotFoundError("파일이 존재하지 않습니다.")

        # convert pdf to markdown
        rendered = self.converter(path_pdf)
        text, _, images = text_from_rendered(rendered)

        file_name = os.path.splitext(os.path.basename(path_pdf))[0]
        save_dir_path = os.path.join(self.output_dir, file_name)

        # make the save directory 
        os.makedirs(save_dir_path, exist_ok=True)

        # save file of markdown
        file_path = os.path.join(save_dir_path, f"{file_name}.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(text)

        # save file of images
        for name, image in images.items():
            image.save(os.path.join(save_dir_path, name))
        
        return file_path
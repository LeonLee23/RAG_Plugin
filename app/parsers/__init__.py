from dataclasses import dataclass, field
from pathlib import Path

from app.errors import AppError


@dataclass
class Block:
    block_id: str
    kind: str
    text: str
    location: dict
    heading_path: list[str] = field(default_factory=list)
    asset_id: str | None = None


def save_image(data: bytes, directory: Path, asset_id: str):
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("Image too large")
            target = directory / f"{asset_id}.png"
            image.convert('RGB').save(target, format='PNG')
            return target
    except Exception as exc:
        raise AppError("UNSUPPORTED_IMAGE", "图片格式无法处理或分辨率超过限制", 422) from exc


def parse_document(path, media_type, output_dir):
    from app.parsers.docx import parse_docx
    from app.parsers.pdf import parse_pdf

    output_dir.mkdir(parents=True, exist_ok=True)
    if media_type == 'pdf':
        return parse_pdf(path, output_dir)
    if media_type == 'docx':
        return parse_docx(path, output_dir)
    raise AppError("UNSUPPORTED_FILE_TYPE", "不支持的文件格式", 415)

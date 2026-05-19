import re


def sanitize_filename(filename: str) -> str:
    filename = re.sub(r"[<>:\"/\\|?*#]", "", filename)
    filename = filename.replace(" ", "_")
    return filename[:100]

import re
import json
from typing import List, Dict
from bs4 import BeautifulSoup

def classify_slide_structure(html: str) -> Dict:
    """Classify the structure of a slide based on its HTML content."""
    return {
        "has_table": "<table" in html,
        "has_code": bool(re.search(r'class |function |public |import |<html|{.*}|;|</?(pre|code)>', html, re.IGNORECASE)),
        "has_image": "<img" in html,
        "has_nested_list": html.count("<ul") >= 2 and "<ul><li><ul" in html,
        "has_heading": any(f"<h{i}>" in html for i in range(1, 4))
    }

def collect_html_and_steps(slide: dict) -> tuple[List[int], List[str]]:
    """Recursively collect HTML content and step numbers from slide and its children."""
    steps = [slide["step"]]
    htmls = [slide["html"]]
    for child in slide.get("children", []):
        c_steps, c_htmls = collect_html_and_steps(child)
        steps.extend(c_steps)
        htmls.extend(c_htmls)
    return steps, htmls

def find_step_from_text(source_text: str, para_points: List[Dict]) -> int:
    """Find the most relevant step number for a given source text."""
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())

    target = normalize(source_text)
    best_score = 0
    best_step = para_points[0]["step"] if para_points else 1

    for p in para_points:
        src = normalize(p["text"])
        common = len(set(target.split()) & set(src.split()))
        total = len(set(src.split()))
        score = common / total if total > 0 else 0

        if score > best_score and score > 0.5:
            best_score = score
            best_step = p["step"]

    return best_step 
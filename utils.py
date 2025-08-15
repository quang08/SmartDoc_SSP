import re
import json
import logging
from typing import List, Dict
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

def classify_slide_structure(html: str) -> Dict:
    """Classify the structure of a slide based on its HTML content."""
    logger.info(f"Classifying slide structure, HTML length: {len(html) if html else 0}")
    
    try:
        structure = {
            "has_table": "<table" in html,
            "has_code": bool(re.search(r'class |function |public |import |<html|{.*}|;|</?(pre|code)>', html, re.IGNORECASE)),
            "has_image": "<img" in html,
            "has_nested_list": html.count("<ul") >= 2 and "<ul><li><ul" in html,
            "has_heading": any(f"<h{i}>" in html for i in range(1, 4))
        }
        
        logger.info(f"Slide structure classification: {structure}")
        return structure
        
    except Exception as e:
        logger.error(f"Error classifying slide structure: {str(e)}")
        logger.error(f"HTML content: {html[:200] if html else 'None'}...")
        raise e

def collect_html_and_steps(slide: dict) -> tuple[List[int], List[str]]:
    """Recursively collect HTML content and step numbers from slide and its children."""
    logger.info(f"Collecting HTML and steps from slide: {slide.get('title', 'Unknown')}")
    logger.info(f"Slide step: {slide.get('step', 'Unknown')}")
    logger.info(f"Slide has children: {bool(slide.get('children'))}")
    
    try:
        steps = [slide["step"]]
        htmls = [slide["html"]]
        
        for child in slide.get("children", []):
            c_steps, c_htmls = collect_html_and_steps(child)
            steps.extend(c_steps)
            htmls.extend(c_htmls)
        
        logger.info(f"Collected {len(steps)} steps and {len(htmls)} HTML blocks")
        return steps, htmls
        
    except Exception as e:
        logger.error(f"Error collecting HTML and steps: {str(e)}")
        logger.error(f"Slide data: {slide}")
        raise e

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
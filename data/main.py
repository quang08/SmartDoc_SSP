import json
from typing import List, Dict
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup
from google import generativeai as genai
import os
import re

# Setup Gemini client
def get_gemini_model():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is not set")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-2.5-flash")

# Initialize model lazily
model = None

# --- Schema ---
class Topic(BaseModel):
    title: str
    key_points: List[str]
    explanation: str
    source_pages: List[int] = Field(default=[])
    source_texts: List[str] = Field(default=[])

class MultipleChoiceQuestion(BaseModel):
    question: str
    choices: Dict[str, str]
    correct: str
    explanation: str
    source_page: int
    source_text: str

class ShortAnswerQuestion(BaseModel):
    question: str
    ideal_answer: str
    source_page: int
    source_text: str

class Quiz(BaseModel):
    multiple_choice: List[MultipleChoiceQuestion]
    short_answer: List[ShortAnswerQuestion] = []

class TopicQuiz(BaseModel):
    topic: str
    quizzes: Quiz

# --- Detection Utilities ---
def classify_slide_structure(html: str) -> Dict:
    return {
        "has_table": "<table" in html,
        "has_code": bool(re.search(r'class |function |public |import |<html|{.*}|;|</?(pre|code)>', html, re.IGNORECASE)),
        "has_image": "<img" in html,
        "has_nested_list": html.count("<ul") >= 2 and "<ul><li><ul" in html,
        "has_heading": any(f"<h{i}>" in html for i in range(1, 4))
    }

# --- Recursive Utilities ---
def collect_html_and_steps(slide: dict) -> (List[int], List[str]):
    steps = [slide["step"]]
    htmls = [slide["html"]]
    for child in slide.get("children", []):
        c_steps, c_htmls = collect_html_and_steps(child)
        steps.extend(c_steps)
        htmls.extend(c_htmls)
    return steps, htmls

# --- Functions ---
def extract_topic_from_slide(slide: dict) -> Topic:
    steps, html_blocks = collect_html_and_steps(slide)
    soup = BeautifulSoup("".join(html_blocks), "html.parser")
    # Collect key_points and paragraphs with their originating step
    key_points = []
    para_points = []
    for step, html in zip(steps, html_blocks):
        s_soup = BeautifulSoup(html, "html.parser")
        for li in s_soup.find_all("li"):
            text = li.get_text(strip=True)
            if text:
                key_points.append({"text": text, "step": step})
        for p in s_soup.find_all("p"):
            text = p.get_text(strip=True)
            if text:
                para_points.append({"text": text, "step": step})
    explanation = para_points[0]["text"] if para_points else ""
    source_texts = para_points[:5]
    return Topic(
        title=slide["title"],
        key_points=[kp["text"] for kp in key_points],
        explanation=explanation,
        source_pages=sorted(set([kp["step"] for kp in key_points] + [pt["step"] for pt in para_points if pt["step"] is not None])),
        source_texts=[json.dumps(source_texts, ensure_ascii=False)]
    )

def generate_quiz(topic: Topic, language="Vietnamese", structure=None) -> TopicQuiz:
    if structure is None:
        structure = {}

    structure_notes = []
    if structure.get("has_table"):
        structure_notes.append("Tài liệu có bảng dữ liệu.")
    if structure.get("has_code"):
        structure_notes.append("Tài liệu có đoạn mã tĩnh.")
    if structure.get("has_image"):
        structure_notes.append("Tài liệu có hình ảnh.")
    if structure.get("has_nested_list"):
        structure_notes.append("Tài liệu có danh sách lồng nhau.")

    structure_description = "\n".join(structure_notes)

    try:
        para_points = json.loads(topic.source_texts[0])
    except Exception:
        para_points = []

    prompt = f"""
Tạo câu hỏi trắc nghiệm và tự luận từ tài liệu sau. Trả lời BẰNG TIẾNG {language.upper()}.

Tiêu đề: {topic.title}

{structure_description}

Gạch đầu dòng:
{json.dumps(topic.key_points, ensure_ascii=False, indent=2)}

Giải thích:
{topic.explanation}

Trích đoạn nguồn (dạng list các đoạn, mỗi đoạn có step):
{json.dumps(para_points, ensure_ascii=False, indent=2)}

Hãy trả lời với định dạng JSON:
{{
  "topic": "{topic.title}",
  "quizzes": {{
    "multiple_choice": [{{
      "question": "",
      "choices": {{"A": "", "B": "", "C": "", "D": ""}},
      "correct": "A",
      "explanation": "",
      "source_page": <step của đoạn hoặc ý liên quan>,
      "source_text": <đoạn hoặc ý liên quan>
    }}],
    "short_answer": [{{
      "question": "",
      "ideal_answer": "",
      "source_page": <step của đoạn hoặc ý liên quan>,
      "source_text": <đoạn hoặc ý liên quan>
    }}]
  }}
}}

Chỉ trả về JSON, không giải thích, không thêm bất cứ ký tự nào ngoài JSON.
"""
    try:
        # Get model lazily
        global model
        if model is None:
            model = get_gemini_model()
        
        response = model.generate_content(prompt)
        output = response.text.strip()
        print(f"[LLM RAW OUTPUT] {output!r}")

        if output.startswith("```json"):
            output = output.removeprefix("```json").removesuffix("```").strip()
        elif output.startswith("```"):
            output = output.removeprefix("```").removesuffix("```").strip()

        quiz_data = json.loads(output)

        def find_step_from_text(source_text: str, para_points: List[Dict]) -> int:
            for p in para_points:
                if p["text"].strip() in source_text.strip():
                    return p["step"]
            return topic.source_pages[0]

        for q in quiz_data["quizzes"].get("multiple_choice", []):
            q["source_page"] = find_step_from_text(q.get("source_text", ""), para_points)

        for q in quiz_data["quizzes"].get("short_answer", []):
            q["source_page"] = find_step_from_text(q.get("source_text", ""), para_points)

        quizzes = Quiz.model_validate(quiz_data["quizzes"])
        return TopicQuiz(topic=topic.title, quizzes=quizzes)

    except Exception as e:
        print(f"⚠️ Error generating quiz for {topic.title}: {e}")
        return None

# --- Pipeline ---
def process_flattened_slides(input_json_path: str, output_path: str):
    print(f"[INFO] Reading input from {input_json_path}")
    with open(input_json_path, "r", encoding="utf-8") as f:
        slides = json.load(f)

    print(f"[INFO] Loaded {len(slides)} slides.")
    result = []
    for idx, slide in enumerate(slides):
        print(f"\n[SLIDE {idx+1}/{len(slides)}] Title: {slide.get('title', 'N/A')} (Step: {slide.get('step', 'N/A')})")
        structure = classify_slide_structure(slide["html"])
        print(f"[STRUCTURE] {structure}")
        topic = extract_topic_from_slide(slide)
        print(f"[INFO] Extracted topic: {topic.title} | Steps: {topic.source_pages}")
        print(f"[INFO] Generating quiz...")
        quiz = generate_quiz(topic, language="Vietnamese", structure=structure)
        if quiz:
            print(f"[SUCCESS] Quiz generated for: {topic.title}")
            result.append(quiz.model_dump())
        else:
            print(f"[FAIL] Quiz generation failed for: {topic.title}")

    print(f"[INFO] Writing output to {output_path}")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"✅ Finished generating quizzes. Output saved to {output_path}")

if __name__ == "__main__":
    process_flattened_slides("parsed_output.json", "quiz_output.json")

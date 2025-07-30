import json
from typing import List, Dict
from bs4 import BeautifulSoup
from google import generativeai as genai
from models import Topic, TopicQuiz, Quiz
from utils import classify_slide_structure, collect_html_and_steps, find_step_from_text
from database import MongoDBManager
from datetime import datetime, timezone

class QuizGenerator:
    def __init__(self, api_key: str):
        """Initialize the quiz generator with Gemini API key."""
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-2.5-flash")
        self.db_manager = MongoDBManager()
    
    def extract_topic_from_slide(self, slide: dict) -> Topic:
        """Extract topic information from a slide."""
        steps, html_blocks = collect_html_and_steps(slide)
        key_points, para_points = [], []
        
        for step, html in zip(steps, html_blocks):
            soup = BeautifulSoup(html, "html.parser")
            key_points += [{"text": li.get_text(strip=True), "step": step} for li in soup.find_all("li")]
            para_points += [{"text": p.get_text(strip=True), "step": step} for p in soup.find_all("p")]
        
        explanation = para_points[0]["text"] if para_points else ""
        source_texts = para_points
        
        return Topic(
            title=slide["title"],
            key_points=[kp["text"] for kp in key_points if kp["text"]],
            explanation=explanation,
            source_pages=sorted(set(kp["step"] for kp in key_points + para_points)),
            source_texts=[json.dumps(source_texts, ensure_ascii=False, indent=2)]
        )
    
    def generate_quiz(self, topic: Topic, structure: dict, language: str = "Vietnamese") -> TopicQuiz:
        """Generate quiz questions for a given topic."""
        notes = []
        if structure.get("has_table"): notes.append("Tài liệu có bảng dữ liệu.")
        if structure.get("has_code"): notes.append("Tài liệu có đoạn mã tĩnh.")
        if structure.get("has_image"): notes.append("Tài liệu có hình ảnh.")
        if structure.get("has_nested_list"): notes.append("Tài liệu có danh sách lồng nhau.")

        try:
            para_points = json.loads(topic.source_texts[0])
        except:
            para_points = []

        # prompt = f"""
        #     Tạo câu hỏi trắc nghiệm và tự luận từ tài liệu sau. Trả lời BẰNG TIẾNG {language.upper()}.

        #     Tiêu đề: {topic.title}

        #     {chr(10).join(notes)}

        #     Gạch đầu dòng:
        #     {json.dumps(topic.key_points, ensure_ascii=False, indent=2)}

        #     Giải thích:
        #     {topic.explanation}

        #     Trích đoạn nguồn (dạng list các đoạn, mỗi đoạn có step):
        #     {json.dumps([
        #         {"step": p["step"], "text": p["text"]}
        #         for p in para_points
        #     ], ensure_ascii=False, indent=2)}

        #     Hãy trả lời với định dạng JSON:
        #     {{
        #     "topic": "{topic.title}",
        #     "quizzes": {{
        #         "multiple_choice": [{{
        #         "question": "",
        #         "choices": {{"A": "", "B": "", "C": "", "D": ""}},
        #         "correct": "A",
        #         "explanation": "",
        #         "source_page": <step của đoạn hoặc ý liên quan>,
        #         "source_text": <đoạn hoặc ý liên quan>
        #         }}],
        #         "short_answer": [{{
        #         "question": "",
        #         "ideal_answer": "",
        #         "source_page": <step của đoạn hoặc ý liên quan>,
        #         "source_text": <đoạn hoặc ý liên quan>
        #         }}]
        #     }}
        #     }}
        #     Chỉ trả về JSON, không giải thích thêm.
        #     """

        prompt = f"""
Tạo câu hỏi trắc nghiệm và tự luận từ tài liệu học tập sau đây. Trả lời HOÀN TOÀN BẰNG TIẾNG VIỆT.

HƯỚNG DẪN TẠO CÂU HỎI:
1. Phân tích từng điểm chính để xác định mức độ phức tạp và tầm quan trọng
2. Đối với mỗi điểm chính:
   - Tạo số lượng câu hỏi trắc nghiệm kiểm tra hiểu biết cơ bản
   - Tạo số lượng câu hỏi tự luận yêu cầu phân tích sâu hơn
3. Xem xét mối quan hệ giữa các điểm chính khi tạo câu hỏi
4. Đảm bảo câu hỏi từ dễ đến khó, từ cơ bản đến nâng cao
5. Tổng số câu hỏi phải phù hợp với độ phức tạp và phạm vi của tài liệu

YÊU CẦU CHẤT LƯỢNG CÂU HỎI:
- Câu hỏi trắc nghiệm: rõ ràng, có 4 lựa chọn hợp lý, chỉ 1 đáp án đúng
- Câu hỏi tự luận: yêu cầu giải thích, phân tích hoặc áp dụng kiến thức
- Mỗi câu hỏi phải có nguồn gốc rõ ràng từ tài liệu
- Tránh câu hỏi quá dễ hoặc quá khó so với nội dung

THÔNG TIN TÀI LIỆU:
Tiêu đề chủ đề: {topic.title}

Đặc điểm tài liệu:
{chr(10).join(notes)}

Các điểm chính:
{json.dumps(topic.key_points, ensure_ascii=False, indent=2)}

Giải thích chi tiết:
{topic.explanation}

Nội dung nguồn theo từng bước:
{json.dumps([
    {"step": p["step"], "text": p["text"]}
    for p in para_points
], ensure_ascii=False, indent=2)}

ĐỊNH DẠNG TRẢ LỜI JSON (bắt buộc):
{{
  "topic": "{topic.title}",
  "quizzes": {{
    "multiple_choice": [
      {{
        "question": "Câu hỏi trắc nghiệm rõ ràng, cụ thể",
        "choices": {{
          "A": "Lựa chọn A",
          "B": "Lựa chọn B", 
          "C": "Lựa chọn C",
          "D": "Lựa chọn D"
        }},
        "correct": "A",
        "explanation": <Giải thích tại sao đáp án này đúng và các đáp án khác sai>,
        "source_page": <số bước tương ứng>,
        "source_text": <đoạn văn bản gốc liên quan>
      }}
    ],
    "short_answer": [
      {{
        "question": "Câu hỏi tự luận yêu cầu phân tích hoặc giải thích",
        "ideal_answer": "Câu trả lời mẫu chi tiết, đầy đủ",
        "source_page": "số bước tương ứng",
        "source_text": "đoạn văn bản gốc liên quan"
      }}
    ]
  }}
}}

LƯU Ý: Chỉ trả về JSON hợp lệ, không thêm văn bản giải thích nào khác.
"""

        response = self.model.generate_content(prompt)
        output = response.text.strip().removeprefix("```json").removesuffix("```")
        quiz_data = json.loads(output)

        # Patch correct step
        for q in quiz_data["quizzes"].get("multiple_choice", []):
            q["source_page"] = find_step_from_text(q.get("source_text", ""), para_points)

        for q in quiz_data["quizzes"].get("short_answer", []):
            q["source_page"] = find_step_from_text(q.get("source_text", ""), para_points)

        quizzes = Quiz(**quiz_data["quizzes"])
        return TopicQuiz(topic=topic.title, quizzes=quizzes)
    
    def save_quizzes_to_db(self, quizzes: List[Dict], metadata: Dict = None) -> str:
        """Save generated quizzes to MongoDB."""
        return self.db_manager.save_generated_questions(quizzes, metadata)
    
    def generate_qna_content(self, request_data: Dict) -> Dict:
      """Generate Q&A content for a specific slide step."""
      message = request_data.get("message", "")
      extracted_content = request_data.get("extractedContent", [])
      step = request_data.get("step", 1)
      step_name = request_data.get("step_name", "")
      structured_data = request_data.get("structuredData", {})

      # Extract clean text content from current step
      current_step_content = "\n".join([
          item.get("text_content", "").strip()
          for item in extracted_content
          if item.get("text_content")
      ])

      # Collect contextual info from other steps
      all_content = []
      relevant_step_ids = []
      context_info = ""

      if structured_data.get("content"):
          for slide in structured_data["content"]:
              slide_step = slide.get("step", 0)
              soup = BeautifulSoup(slide.get("html", "") or "", "html.parser")
              slide_text = soup.get_text(strip=True)
              all_content.append({
                  "step": slide_step,
                  "title": slide.get("title", ""),
                  "content": slide_text,
                  "is_current": slide_step == step
              })

          related_lines = [
              f"- Bước {c['step']} ({c['title']}): {c['content'][:200]}..."
              for c in all_content if not c["is_current"] and c["content"]
          ]
          if related_lines:
              context_info = "\n\nTHÔNG TIN LIÊN QUAN TỪ CÁC BƯỚC KHÁC:\n" + "\n".join(related_lines)
              relevant_step_ids = [c["step"] for c in all_content if not c["is_current"]]

      # Check if there are images in the current step
      has_images = any(item.get("images") for item in extracted_content)
      escaped_context_info = context_info.strip().replace('"', '\\"') if context_info else ""

      # Construct the prompt
      prompt = f"""
  Bạn là một trợ lý học tập thông minh. Hãy tạo nội dung Hỏi và Đáp (Q&A) dựa trên nội dung của một bước cụ thể trong slide học tập. Trả lời HOÀN TOÀN BẰNG TIẾNG VIỆT.

  === THÔNG TIN BƯỚC HIỆN TẠI ===
  - Bước: {step}
  - Tên bước: {step_name}
  - Yêu cầu từ người dùng: {message}
  → Hãy làm đúng theo yêu cầu này trong phần trả lời, KHÔNG tự ý đổi thành một yêu cầu khác.
  - Có hình ảnh: {"Có" if has_images else "Không"}

  === NỘI DUNG CHÍNH CỦA BƯỚC NÀY ===
  {current_step_content or "(Không có nội dung rõ ràng để trả lời yêu cầu này.)"}

  {context_info}

  === HƯỚNG DẪN TẠO NỘI DUNG ===
1. Phân tích kỹ nội dung của bước hiện tại.
2. Câu trả lời phải bắt đầu bằng: **“Dựa trên slide {step}: ...”**
3. Nếu nội dung slide trình bày các khái niệm, đặc điểm, hay nguyên lý cơ bản, hãy tạo một ví dụ minh họa ngắn gọn và thân thiện, gắn với các tình huống quen thuộc trong cuộc sống hằng ngày (ví dụ như mua hàng, học tập, thời tiết, v.v.).
4. Hạn chế sử dụng thuật ngữ kỹ thuật (như “hồi quy”, “mô hình thống kê”) nếu không giải thích kèm theo. Ưu tiên lối trình bày dễ hiểu cho người mới học.
5. Chỉ trả lời “Không có đủ thông tin...” nếu nội dung hoàn toàn không cung cấp khái niệm hoặc đặc điểm nào để tạo ví dụ.
6. Nếu cần, có thể tham khảo thông tin từ các bước khác để hỗ trợ nhưng không được bịa đặt.
7. Trình bày câu trả lời liền mạch, rõ ràng, trong khoảng 100-300 từ. KHÔNG sử dụng định dạng Q/A.


  === ĐỊNH DẠNG JSON TRẢ VỀ ===
  {{
    "step": {step},
    "step_name": "{step_name}",
    "answer": "Nội dung hỏi đáp hoặc lời phản hồi, bắt đầu bằng 'Dựa trên slide {step}: ...'",
    "relevant_info": "{escaped_context_info}",
    "relevant_steps": {relevant_step_ids}
  }}
  """.strip()

      try:
          response = self.model.generate_content(prompt)
          output = response.text.strip()
          # Log raw output for debugging
          print(f"[LLM RAW OUTPUT] {output!r}")
          # Remove code block markers if present
          if output.startswith("```json"):
              output = output.removeprefix("```json").removesuffix("```").strip()
          elif output.startswith("```"):
              output = output.removeprefix("```").removesuffix("```").strip()
          if not output:
              return {"error": "LLM returned empty output. Check prompt or model quota."}
          try:
              qna_data = json.loads(output)
          except Exception as e:
              return {"error": f"LLM did not return valid JSON. Raw output: {output[:200]}... Error: {str(e)}"}
          return qna_data
      except Exception as e:
          return {"error": f"Failed to generate Q&A content: {str(e)}"}
    
    def save_qna_to_chat(self, qna_data: Dict, request_data: Dict) -> str:
        """Save Q&A content to chat collection."""
        structured_data = request_data.get("structuredData", {})
        
        # Extract metadata from structured data
        lab_name = structured_data.get("labName", "Unknown")
        room_id = structured_data.get("roomId", "")
        doc_id = structured_data.get("docID", "")
        user_id = structured_data.get("userID", "")
        user_email = structured_data.get("userEmail", "")
        
        # Create Q&A entry
        qna_entry = {
            "step": qna_data.get("step", 0),
            "step_name": qna_data.get("step_name", ""),
            "message": request_data.get("message", ""),
            "answer": qna_data.get("answer", ""),
            "relevant_info": qna_data.get("relevant_info", ""),
            "relevant_steps": qna_data.get("relevant_steps", []),
            "created_at": datetime.now(timezone.utc)
        }
        
        # Prepare conversation data
        conversation_data = {
            "room_id": room_id,
            "doc_id": doc_id,
            "user_id": user_id,
            "user_email": user_email,
            "lab_name": lab_name,
            "qna_entry": qna_entry
        }
        
        return self.db_manager.save_chat_conversation(conversation_data)


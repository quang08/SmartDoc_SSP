
import json
from typing import List, Dict
from bs4 import BeautifulSoup
import os
from openai import OpenAI
from models import Topic, TopicQuiz, Quiz
from utils import classify_slide_structure, collect_html_and_steps, find_step_from_text
from database import MongoDBManager
from datetime import datetime, timezone
import re
HINT_CAP = 2

class QuizGenerator:
    def __init__(self, api_key: str):
        """Initialize the quiz generator with Qwen API key (OpenAI compatible)."""
        self.client = OpenAI(
           api_key=os.getenv("OPENAI_API_KEY"),
           base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        )
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
    
    def save_quizzes_to_db(self, quizzes: List[Dict], metadata: Dict = None) -> str:
        """Save generated quizzes to MongoDB."""
        return self.db_manager.save_generated_questions(quizzes, metadata)

    def _instruction_for_level(self, response_level: str) -> str:
        if response_level == "Hint":
            return "Chỉ đưa ra một gợi ý nhỏ, không tiết lộ lời giải hay từng bước cụ thể."
        if response_level == "Steps":
            return "Trình bày các bước giải theo thứ tự rõ ràng, để chỗ trống cho người học tự điền, KHÔNG cho đáp án cuối."
        if response_level == "Worked Solution":
            return "Giải chi tiết đầy đủ kèm lập luận. Được phép đưa đáp án nếu cần thiết theo ngữ cảnh học tập."
        if response_level == "Answer":
            return "Chỉ viết đáp án cuối cùng, thật ngắn gọn, KHÔNG kèm giải thích."
        return "Chỉ đưa ra gợi ý nhỏ."

    def _next_buttons(self, response_level: str, hint_count: int) -> Dict[str, List[str]]:
        # Elimination / funnel rules
        if response_level == "Hint":
            if hint_count >= HINT_CAP:
                return {"buttons_displayed": ["Show Steps", "Worked Solution", "Just the Answer"],
                        "next_options_removed": ["More Hint"]}
            return {"buttons_displayed": ["More Hint", "Show Steps", "Worked Solution", "Just the Answer"],
                    "next_options_removed": []}
        if response_level == "Steps":
            return {"buttons_displayed": ["Worked Solution", "Just the Answer"],
                    "next_options_removed": ["More Hint"]}
        if response_level == "Worked Solution":
            return {"buttons_displayed": ["Just the Answer"],
                    "next_options_removed": ["More Hint", "Show Steps"]}
        if response_level == "Answer":
            return {"buttons_displayed": [],
                    "next_options_removed": ["More Hint", "Show Steps", "Worked Solution"]}
        return {"buttons_displayed": ["Show Steps", "Worked Solution", "Just the Answer"],
                "next_options_removed": ["More Hint"]}

    def _count_prior_hints_for_step(self, room_id: str, user_id: str, step: int) -> int:
        doc = self.db_manager.chat_collection.find_one({"room_id": room_id, "user_id": user_id, "deleted": {"$ne": True}})
        if not doc:
            return 0
        cnt = 0
        for q in doc.get("qna_list", []):
            if q.get("step") == step and not q.get("deleted", False):
                # count both current level and escalation history
                if q.get("chatbot_interaction", {}).get("response_level") == "Hint":
                    cnt += 1
                for h in q.get("escalation_history", []):
                    if h.get("response_level") == "Hint":
                        cnt += 1
        return cnt
    
    def generate_qna_content(self, request_data: Dict) -> Dict:
        """Generate Q&A content for a specific slide step using Qwen API."""
        message = request_data.get("message", "")
        extracted_content = request_data.get("extractedContent", [])
        step = request_data.get("step", 1)
        step_name = request_data.get("step_name", "")
        structured_data = request_data.get("structuredData") or {}
        response_level = request_data.get("response_level", "Hint")
        code_ctx = request_data.get("code_context") or None

        # enforce hint cap (compute prior hints first)
        room_id = request_data.get("room_id", "")
        user_id = request_data.get("user_id", "")
        prior_hints = self._count_prior_hints_for_step(room_id, user_id, step)
        if response_level == "Hint" and prior_hints >= HINT_CAP:
            # auto-promote to Steps to avoid infinite hints
            response_level = "Steps"

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
        escaped_context_info = context_info.strip().replace('"', '\"') if context_info else ""

        level_instruction = self._instruction_for_level(response_level)

        code_ctx_block = ""
        if code_ctx and (code_ctx.get("snippet") or code_ctx.get("language")):
            code_ctx_block = f"""
    === NGỮ CẢNH MÃ NGUỒN (NẾU CÓ) ===
    - Ngôn ngữ: {code_ctx.get("language") or "không rõ"}
    - Dòng: {code_ctx.get("lines") or "không rõ"}
    - Đoạn mã:
    {code_ctx.get("snippet") or "(không có)"}"""
            
        prompt = f"""
        Bạn là một trợ lý học tập thông minh. Hãy tạo nội dung Hỏi và Đáp (Q&A) dựa trên nội dung của một bước cụ thể trong slide học tập.
        Trả lời theo mức độ: **{response_level}**.
        Hướng dẫn mức độ: {level_instruction}
        Trả lời HOÀN TOÀN BẰNG TIẾNG VIỆT. 

        QUAN TRỌNG:
        - CHỈ ĐƯỢC SỬ DỤNG THÔNG TIN TỪ SLIDE HIỆN TẠI. KHÔNG ĐƯỢC SỬ DỤNG THÔNG TIN TỪ CÁC SLIDE KHÁC HOẶC BẤT KỲ NGỮ CẢNH TRƯỚC ĐÓ. 
        - Nếu không có đủ thông tin trong slide hiện tại, hãy trả lời trước: "Không có đủ thông tin để trả lời yêu cầu này." 
        Ngay sau đó, vẫn phải đưa ra một lời giải thích hoặc gợi ý ngắn gọn, phù hợp với câu hỏi, mang tính khái quát hoặc tình huống minh họa.
        - KHÔNG được viết trực tiếp đáp án cuối cùng, KHÔNG được cung cấp toàn bộ đoạn mã hay kết quả cụ thể, trừ khi yêu cầu của người dùng chứa đúng cụm từ: **"đưa câu trả lời chính xác"**.
        - Khi KHÔNG có cụm này, chỉ được giải thích ý tưởng, hướng tiếp cận, hoặc các bước gợi ý (ví dụ: “hãy bắt đầu bằng cách kiểm tra điều kiện”, “sau đó nghĩ đến trường hợp điểm dưới 5”, v.v.), tuyệt đối tránh viết đáp án hoặc code hoàn chỉnh.
        - Không được bịa đặt thông tin ngoài slide.

        === QUY ĐỊNH THEO MỨC ĐỘ (response_level) ===
        - **Hint**: Chỉ đưa ra gợi ý rất ngắn gọn, định hướng cách tiếp cận, không nêu đầy đủ các bước hay câu trả lời.  
        - **More Hint**: Mạnh hơn Hint, có thể gợi ý về thứ tự hoặc phạm vi cần xem xét, nhưng vẫn chưa phải toàn bộ quy trình.  
        - **Steps**: Liệt kê ngắn gọn các bước hành động theo thứ tự logic (mỗi bước là một câu ngắn hoặc cụm động từ). Không giải thích lý do, không mô tả cách triển khai kỹ thuật, không đưa ví dụ mã, không dùng cú pháp mã, và không nêu đáp án hay giá trị cuối cùng. 
        - **Worked Solution**: Trình bày lý luận và phân tích cho từng bước — giải thích tại sao mỗi bước cần làm và hệ quả logic giữa các bước. Tuyệt đối không đưa đoạn mã, biểu thức code, hoặc câu lệnh cụ thể (ví dụ: không viết diem = float(input(...)), không in công thức hay gọi hàm cụ thể). Có thể sử dụng thuật ngữ chuyên môn nhưng chỉ dưới dạng mô tả bằng văn, không phải code.  
        - **Answer**: Trình bày ngắn gọn đáp án cuối cùng. Nếu là bài lập trình, chỉ tóm tắt kết quả (như phân loại điểm: Giỏi/Khá/Trung bình/Yếu) chứ KHÔNG in ra đoạn mã. Chỉ khi người dùng ghi rõ "đưa câu trả lời chính xác" mới cung cấp code hoặc đáp án chi tiết.

        === THÔNG TIN BƯỚC HIỆN TẠI ===
        - Bước: {step}
        - Tên bước: {step_name}
        - Yêu cầu từ người dùng: {message}
        → Hãy làm đúng theo yêu cầu này trong phần trả lời, KHÔNG tự ý đổi thành một yêu cầu khác.
        - Có hình ảnh: {"Có" if has_images else "Không"}

        === NỘI DUNG CHÍNH CỦA BƯỚC NÀY ===
        {current_step_content or "(Không có nội dung rõ ràng để trả lời yêu cầu này.)"}

        {context_info}

        {code_ctx_block}

        === HƯỚNG DẪN TẠO NỘI DUNG ===
        1. Phân tích kỹ nội dung của bước hiện tại.
        2. Câu trả lời phải bắt đầu bằng: **“Dựa trên slide {step}: ...”**
        3. Đưa gợi ý phù hợp với MỨC {response_level}. NẾU KHÔNG ĐỦ THÔNG TIN ĐỂ TRẢ LỜI VỚI MỨC {response_level}, hãy trả lời trước: "Không có đủ thông tin trong slide hiện tại..." tuy nhiên tự đưa ra câu trả lời phù hợp với {response_level}.
        4. Nếu người dùng có cụm "đưa câu trả lời chính xác", mới đưa đáp án hoặc code đầy đủ.
        5. Trình bày câu trả lời rõ ràng, liền mạch, 100-300 từ. KHÔNG sử dụng định dạng Q/A.
        6. Chỉ trả về một khối JSON duy nhất như mẫu, KHÔNG giải thích, KHÔNG thêm văn bản ngoài JSON.

        === ĐỊNH DẠNG JSON TRẢ VỀ ===
        {{
            "step": {step},
            "step_name": "{step_name}",
            "answer": "Bắt đầu bằng 'Dựa trên slide {step}:' nếu dùng nội dung của slide; nếu không đủ, nói rõ 'Không có đủ thông tin trong slide hiện tại...' tự đưa ra câu trả lời phù hợp với {response_level}.",
            "relevant_info": "{escaped_context_info}",
            "relevant_steps": {relevant_step_ids}
        }}
        """.strip()

        try:
            messages = [
                {"role": "system", "content": "Bạn là một trợ lý học tập thông minh."},
                {"role": "user", "content": prompt}
            ]
            print(f"[LLM INPUT PROMPT] {prompt}")
            completion = self.client.chat.completions.create(
                model="qwen-max",
                messages=messages,
                extra_body={"enable_thinking": False},
                response_format={"type": "json_object"}
            )
            output = completion.choices[0].message.content.strip()
            print(f"[LLM RAW OUTPUT] {output!r}")
            # No need to strip code block markers, should be pure JSON
            if not output:
                return {"error": "LLM returned empty output. Check prompt or model quota."}
            try:
                qna_data = json.loads(output)
            except Exception as e:
                return {"error": f"LLM did not return valid JSON. Raw output: {output[:200]}... Error: {str(e)}"}
            # Attach pathway UX (buttons after THIS response)
            buttons = self._next_buttons(response_level, prior_hints + (1 if response_level == "Hint" else 0))
            qna_data["_pathway"] = {
                "response_level": response_level,
                **buttons
            }
            # machine flag about whether slide context existed
            qna_data["_context_used"] = {
                "step_context_included": bool(current_step_content.strip()),
                "student_code_context": code_ctx or None
            }
            return qna_data
        except Exception as e:
            return {"error": f"Failed to generate Q&A content: {str(e)}"}

    def save_qna_to_chat(self, qna_data: Dict, request_data: Dict) -> str:
        """Save Q&A content to chat collection with scaffold pathway tracking."""
        if not qna_data:
            return "No Q&A data to save"

        lab_name = request_data.get("lab_name", "Unknown Lab")
        room_id = request_data.get("room_id", "")
        doc_id = request_data.get("doc_id", "")
        user_id = request_data.get("user_id", "")
        user_email = request_data.get("user_email", "")

        response_level = qna_data.get("_pathway", {}).get("response_level", request_data.get("response_level", "Hint"))
        buttons_displayed = qna_data.get("_pathway", {}).get("buttons_displayed", [])
        next_options_removed = qna_data.get("_pathway", {}).get("next_options_removed", [])
        context_used = qna_data.get("_context_used", {"step_context_included": False, "student_code_context": None})

        chatbot_interaction = {
            "timestamp": datetime.now(timezone.utc),
            "response_level": response_level,
            "context_used": context_used,
            "llm_response": qna_data.get("answer", ""),
            "buttons_displayed": buttons_displayed,
            "next_options_removed": next_options_removed
        }

        qna_entry = {
            "step": qna_data.get("step", 0),
            "step_name": qna_data.get("step_name", ""),
            "student_query": request_data.get("message", ""),
            "chatbot_interaction": chatbot_interaction,
            "escalation_history": [],   # will fill if this is an escalation
            "relevant_info": qna_data.get("relevant_info", ""),
            "relevant_steps": qna_data.get("relevant_steps", []),
            "deleted": False
        }

        conversation_data = {
            "room_id": room_id,
            "doc_id": doc_id,
            "user_id": user_id,
            "user_email": user_email,
            "lab_name": lab_name,
            "qna_entry": qna_entry
        }

        return self.db_manager.save_chat_conversation_with_pathway(conversation_data)

    
    def generate_quiz(self, topic: Topic, structure: dict, language: str = "Vietnamese") -> TopicQuiz:
                """Generate quiz questions for a given topic using Qwen API."""
                notes = []
                if structure.get("has_table"): notes.append("Tài liệu có bảng dữ liệu.")
                if structure.get("has_code"): notes.append("Tài liệu có đoạn mã tĩnh.")
                if structure.get("has_image"): notes.append("Tài liệu có hình ảnh.")
                if structure.get("has_nested_list"): notes.append("Tài liệu có danh sách lồng nhau.")

                try:
                        para_points = json.loads(topic.source_texts[0])
                except:
                        para_points = []

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

                # Qwen API expects OpenAI-compatible chat format
                messages = [
                        {"role": "system", "content": "Bạn là một trợ lý học tập thông minh."},
                        {"role": "user", "content": prompt}
                ]
                completion = self.client.chat.completions.create(
                        model="qwen-plus",
                        messages=messages,
                        extra_body={"enable_thinking": False}
                )
                output = completion.choices[0].message.content.strip()
                # Remove code block markers if present
                if output.startswith("```json") or output.startswith("````json") or output.startswith("```"):
                        output = re.sub(r"^```(?:json)?\s*([\s\S]+?)\s*```$", r"\1", output, flags=re.MULTILINE).strip()

                quiz_data = json.loads(output)

                # Patch correct step
                for q in quiz_data["quizzes"].get("multiple_choice", []):
                        q["source_page"] = find_step_from_text(q.get("source_text", ""), para_points)

                for q in quiz_data["quizzes"].get("short_answer", []):
                        q["source_page"] = find_step_from_text(q.get("source_text", ""), para_points)

                quizzes = Quiz(**quiz_data["quizzes"])
                return TopicQuiz(topic=topic.title, quizzes=quizzes)
    


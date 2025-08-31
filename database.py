from pymongo import MongoClient
from typing import List, Dict, Optional
from datetime import datetime, timezone
from config.config import MONGODB_URI, MONGODB_DATABASE, MONGODB_COLLECTION, MONGODB_CHAT_COLLECTION
from uuid import uuid4

class MongoDBManager:
    def __init__(self):
        """Initialize MongoDB connection."""
        import os
        print("DEBUG: Loaded MONGODB_URI from environment:", os.getenv("MONGODB_URI"))
        print("DEBUG: Final MONGODB_URI used in config:", MONGODB_URI)
        self.client = MongoClient(MONGODB_URI)
        self.db = self.client[MONGODB_DATABASE]
        self.collection = self.db[MONGODB_COLLECTION]
        self.chat_collection = self.db[MONGODB_CHAT_COLLECTION]
    
    def save_chat_conversation_with_pathway(self, conversation_data: Dict) -> str:
        """Upsert conversation; per step, push prior interaction into escalation_history and replace with the latest."""
        room_id = conversation_data.get("room_id")
        user_id = conversation_data.get("user_id")
        qna_entry = conversation_data["qna_entry"]
        step = qna_entry.get("step")

        # Find or create conversation
        existing = self.chat_collection.find_one({"room_id": room_id, "user_id": user_id, "deleted": {"$ne": True}})
        now = datetime.now(timezone.utc)

        if existing:
            conversation_id = existing.get("conversation_id")

            # Try to find an existing qna for this step (not deleted)
            qna_list = existing.get("qna_list", [])
            found_idx = None
            for i, q in enumerate(qna_list):
                if not q.get("deleted", False) and q.get("step") == step:
                    found_idx = i
                    break

            if found_idx is None:
                # Append new qna entry
                self.chat_collection.update_one(
                    {"_id": existing["_id"]},
                    {"$push": {"qna_list": qna_entry}, "$set": {"last_updated": now}}
                )
            else:
                # Escalation: push prior interaction to history, replace with latest interaction
                prior = qna_list[found_idx]
                prior_interaction = prior.get("chatbot_interaction", {})
                if prior_interaction:
                    # append to history
                    self.chat_collection.update_one(
                        {"_id": existing["_id"]},
                        {
                          "$push": {f"qna_list.{found_idx}.escalation_history": {
                              "timestamp": prior_interaction.get("timestamp", now),
                              "response_level": prior_interaction.get("response_level"),
                              "llm_response": prior_interaction.get("llm_response")
                          }},
                          "$set": {
                              f"qna_list.{found_idx}.student_query": qna_entry.get("student_query"),
                              f"qna_list.{found_idx}.chatbot_interaction": qna_entry.get("chatbot_interaction"),
                              f"qna_list.{found_idx}.relevant_info": qna_entry.get("relevant_info"),
                              f"qna_list.{found_idx}.relevant_steps": qna_entry.get("relevant_steps"),
                              "last_updated": now
                          }
                        }
                    )
                else:
                    # If no prior interaction, just set it
                    self.chat_collection.update_one(
                        {"_id": existing["_id"]},
                        {
                          "$set": {
                              f"qna_list.{found_idx}.student_query": qna_entry.get("student_query"),
                              f"qna_list.{found_idx}.chatbot_interaction": qna_entry.get("chatbot_interaction"),
                              f"qna_list.{found_idx}.relevant_info": qna_entry.get("relevant_info"),
                              f"qna_list.{found_idx}.relevant_steps": qna_entry.get("relevant_steps"),
                              "last_updated": now
                          }
                        }
                    )

            return conversation_id

        # Create new conversation
        conversation_id = str(uuid4())
        document = {
            "room_id": room_id,
            "doc_id": conversation_data.get("doc_id"),
            "user_id": user_id,
            "user_email": conversation_data.get("user_email"),
            "lab_name": conversation_data.get("lab_name"),
            "conversation_id": conversation_id,
            "started_at": now,
            "qna_list": [qna_entry],
            "last_updated": now,
            "deleted": False
        }
        self.chat_collection.insert_one(document)
        return conversation_id
    
    def save_generated_questions(self, quizzes: List[Dict], metadata: Optional[Dict] = None) -> str:
        documents = []
        lab_name = metadata.get("lab_name", "Unknown")

        for topic_data in quizzes:
            topic = topic_data.get("topic", "Untitled Section")
            quiz = topic_data.get("quizzes", {})
            mc_questions = quiz.get("multiple_choice", [])
            short_answer_questions = quiz.get("short_answer", [])

            document = {
                "practice_test_id": str(uuid4()),
                "study_guide_title": lab_name,
                "section_title": topic,
                "guide_type": "slides",
                "questions": mc_questions,
                "short_answer": short_answer_questions,
                "created_at": datetime.now(timezone.utc),
                **{k: v for k, v in metadata.items() if k not in ["total_slides", "slide_titles"]}  # roomId, userEmail, etc.
            }
            documents.append(document)

        result = self.collection.insert_many(documents)
        return str(result.inserted_ids[0])
    
    def get_all_questions(self, limit: int = 100) -> List[Dict]:
        """Retrieve all generated questions."""
        cursor = self.collection.find().sort("created_at", -1).limit(limit)
        questions = list(cursor)
        
        # Convert ObjectIds to strings for serialization
        for question in questions:
            if "_id" in question:
                question["_id"] = str(question["_id"])
        
        return questions
    
    def get_questions_by_id(self, question_id: str) -> Optional[Dict]:
        """Retrieve questions by ID."""
        from bson import ObjectId
        try:
            question = self.collection.find_one({"_id": ObjectId(question_id)})
            if question:
                question["_id"] = str(question["_id"])
            return question
        except:
            return None
    
    def delete_questions(self, question_id: str) -> bool:
        """Delete questions by ID."""
        from bson import ObjectId
        try:
            result = self.collection.delete_one({"_id": ObjectId(question_id)})
            return result.deleted_count > 0
        except:
            return False
    
    def close(self):
        """Close MongoDB connection."""
        self.client.close()
    
    def save_chat_conversation(self, conversation_data: Dict) -> str:
        """Save or update chat conversation."""
        room_id = conversation_data.get("room_id")
        user_id = conversation_data.get("user_id")
        
        # Check if conversation already exists
        existing_conversation = self.chat_collection.find_one({
            "room_id": room_id,
            "user_id": user_id
        })
        
        if existing_conversation:
            # Update existing conversation
            conversation_id = existing_conversation.get("conversation_id")
            qna_list = existing_conversation.get("qna_list", [])
            qna_list.append(conversation_data["qna_entry"])
            
            self.chat_collection.update_one(
                {"_id": existing_conversation["_id"]},
                {
                    "$set": {
                        "qna_list": qna_list,
                        "last_updated": datetime.now(timezone.utc)
                    }
                }
            )
            return conversation_id
        else:
            # Create new conversation
            conversation_id = str(uuid4())
            document = {
                "room_id": room_id,
                "doc_id": conversation_data.get("doc_id"),
                "user_id": user_id,
                "user_email": conversation_data.get("user_email"),
                "lab_name": conversation_data.get("lab_name"),
                "conversation_id": conversation_id,
                "started_at": datetime.now(timezone.utc),
                "qna_list": [conversation_data["qna_entry"]],
                "last_updated": datetime.now(timezone.utc)
            }
            
            result = self.chat_collection.insert_one(document)
            return conversation_id
    
    def get_conversation(self, room_id: str, user_id: str) -> Optional[Dict]:
        """Get conversation by room_id and user_id."""
        conversation = self.chat_collection.find_one({
            "room_id": room_id,
            "user_id": user_id
        })
        
        if conversation:
            # Convert ObjectId to string for serialization
            conversation["_id"] = str(conversation["_id"])
            return conversation
        return None
    
    def get_conversation_by_id(self, conversation_id: str) -> Optional[Dict]:
        """Get conversation by conversation_id."""
        conversation = self.chat_collection.find_one({
            "conversation_id": conversation_id
        })
        
        if conversation:
            # Convert ObjectId to string for serialization
            conversation["_id"] = str(conversation["_id"])
            return conversation
        return None 
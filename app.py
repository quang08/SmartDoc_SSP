from typing import List, Optional
from fastapi import FastAPI, Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config.config import (
    GEMINI_API_KEY, API_TITLE, API_VERSION, 
    CORS_ORIGINS, CORS_CREDENTIALS, CORS_METHODS, CORS_HEADERS,
    DEFAULT_LANGUAGE
)
from models import QuizResponse, QuizRequest, TestsResponse, PracticeTest, QnARequest, QnAResponse, ConversationResponse
from quiz_generator import QuizGenerator
from utils import classify_slide_structure
from database import MongoDBManager

# --- FastAPI Setup ---
app = FastAPI(title=API_TITLE, version=API_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://fit.neu.edu.vn", "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=CORS_CREDENTIALS,
    allow_methods=CORS_METHODS,
    allow_headers=CORS_HEADERS,
)

quiz_generator = QuizGenerator(GEMINI_API_KEY)
db_manager = MongoDBManager()

@app.post("/generate-quiz", response_model=QuizResponse)
async def generate_quiz_from_slides(payload: QuizRequest):
    """Generate quizzes from slide data."""
    try:
        result = []
        slides = payload.content

        for slide in slides:
            structure = classify_slide_structure(slide.html)
            topic = quiz_generator.extract_topic_from_slide(slide.dict())
            quiz = quiz_generator.generate_quiz(topic, structure, language="Vietnamese")
            result.append(quiz.model_dump())

        metadata = {
            "lab_name": payload.labName,
            "room_id": payload.roomId,
            "doc_id": payload.docID,
            "user_id": payload.userID,
            "user_email": payload.userEmail,
            "total_slides": len(slides),
            "slide_titles": [slide.title for slide in slides]
        }

        saved_id = quiz_generator.save_quizzes_to_db(result, metadata)

        return QuizResponse(
            id=saved_id,
            practice_guide_id=saved_id,
            labName=payload.labName,
            roomId=payload.roomId,
            docID=payload.docID,
            userID=payload.userID,
            userEmail=payload.userEmail,
            topics=result,
            message=f"Generated {len(result)} quizzes for lab '{payload.labName}'",
            success=True
        )
    except Exception as e:
        return QuizResponse(
            success=False,
            error=str(e),
            labName=payload.labName,
            roomId=payload.roomId,
            docID=payload.docID,
            userID=payload.userID,
            userEmail=payload.userEmail
        )

@app.get("/")
def root():
    """Root endpoint with API information."""
    return {"message": f"{API_TITLE} v{API_VERSION} is running."}

@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "api_key_configured": bool(GEMINI_API_KEY)}

@app.get("/get-tests", response_model=TestsResponse)
async def get_all_tests(limit: int = 100, skip: int = 0):
    """Retrieve all practice tests from MongoDB with pagination."""
    try:
        # Get tests with pagination
        cursor = db_manager.collection.find().sort("created_at", -1).skip(skip).limit(limit)
        tests_data = list(cursor)
        
        # Convert to PracticeTest models
        tests = []
        for test_data in tests_data:
            # Convert MongoDB ObjectId to string for practice_test_id
            if "_id" in test_data:
                test_data["_id"] = str(test_data["_id"])
            test_data["practice_test_id"] = str(test_data.get("_id", ""))
            tests.append(PracticeTest(**test_data))
        
        return TestsResponse(
            success=True,
            tests=tests,
            total=len(tests),
            message=f"Retrieved {len(tests)} practice tests"
        )
    except Exception as e:
        return TestsResponse(
            success=False,
            tests=[],
            total=0,
            error=f"Database error: {str(e)}"
        )
    
@app.delete("/conversation/{room_id}/{user_id}")
async def delete_conversation(room_id: str, user_id: str):
    """Delete all chat history for a given room and user."""
    try:
        result = db_manager.collection.delete_many({"room_id": room_id, "user_id": user_id})
        chat_result = None
        if hasattr(db_manager, 'chat_collection'):
            chat_result = db_manager.chat_collection.delete_many({"room_id": room_id, "user_id": user_id})
        deleted_count = result.deleted_count + (chat_result.deleted_count if chat_result else 0)
        return {
            "success": True,
            "deleted_count": deleted_count,
            "message": f"Deleted {deleted_count} chat entries for room {room_id} and user {user_id}."
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to delete chat history: {str(e)}"
        }

@app.get("/questions")
async def get_all_questions(limit: int = 100):
    """Retrieve all generated questions from MongoDB."""
    try:
        questions = db_manager.get_all_questions(limit=limit)
        return {
            "success": True,
            "questions": questions,
            "total": len(questions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/check-availability")
async def check_practice_tests_availability(roomId: str):
    """Check if any practice tests are available for a given room."""
    try:
        count = db_manager.collection.count_documents({"room_id": roomId})
        return {
            "available": count > 0,
            "count": count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/chat", response_model=QnAResponse)
async def generate_qna_content(request: QnARequest):
    """Generate Q&A content for a specific slide step."""
    try:
        qna_content = quiz_generator.generate_qna_content(request.dict())
        
        if "error" in qna_content:
            return QnAResponse(
                success=False,
                error=qna_content["error"]
            )
        
        # Save to chat collection
        conversation_id = quiz_generator.save_qna_to_chat(qna_content, request.dict())
        
        return QnAResponse(
            success=True,
            qna_content=qna_content,
            message=f"Successfully generated Q&A content for step {request.step}: {request.step_name} (Conversation ID: {conversation_id})"
        )
    except Exception as e:
        return QnAResponse(
            success=False,
            error=f"Failed to generate Q&A content: {str(e)}"
        )

@app.get("/conversation/{room_id}/{user_id}", response_model=ConversationResponse)
async def get_conversation(room_id: str, user_id: str):
    """Get conversation by room_id and user_id."""
    try:
        conversation = db_manager.get_conversation(room_id, user_id)
        
        if not conversation:
            return ConversationResponse(
                success=False,
                error="Conversation not found",
                message=f"No conversation found for room {room_id} and user {user_id}"
            )
        
        return ConversationResponse(
            success=True,
            conversation=conversation,
            message=f"Retrieved conversation for room {room_id} and user {user_id}"
        )
    except Exception as e:
        return ConversationResponse(
            success=False,
            error=f"Database error: {str(e)}"
        )

@app.get("/conversation/id/{conversation_id}", response_model=ConversationResponse)
async def get_conversation_by_id(conversation_id: str):
    """Get conversation by conversation_id."""
    try:
        conversation = db_manager.get_conversation_by_id(conversation_id)
        
        if not conversation:
            return ConversationResponse(
                success=False,
                error="Conversation not found",
                message=f"No conversation found with ID {conversation_id}"
            )
        
        return ConversationResponse(
            success=True,
            conversation=conversation,
            message=f"Retrieved conversation with ID {conversation_id}"
        )
    except Exception as e:
        return ConversationResponse(
            success=False,
            error=f"Database error: {str(e)}"
        )

if __name__ == "__main__":
    import uvicorn
    from config.config import API_HOST, API_PORT
    uvicorn.run("app:app", host=API_HOST, port=API_PORT, reload=True)

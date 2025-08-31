from typing import List, Dict, Optional, Literal
from pydantic import BaseModel, Field, model_validator
from datetime import datetime


ResponseLevel = Literal["Hint", "Steps", "Worked Solution", "Answer"]

class CodeContext(BaseModel):
    language: Optional[str] = None
    snippet: Optional[str] = None
    lines: Optional[List[int]] = None

class Topic(BaseModel):
    title: str
    key_points: List[str]
    explanation: str
    source_pages: List[int] = Field(default_factory=list)
    source_texts: List[str] = Field(default_factory=list)

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
    short_answer: List[ShortAnswerQuestion]

class TopicQuiz(BaseModel):
    topic: str
    quizzes: Quiz

class QuizResponse(BaseModel):
    id: Optional[str] = None
    practice_guide_id: Optional[str] = None
    labName: Optional[str] = None
    roomId: Optional[str] = None
    docID: Optional[str] = None
    userID: Optional[str] = None
    userEmail: Optional[str] = None
    topics: Optional[List[dict]] = None
    success: bool = True
    error: Optional[str] = None
    message: Optional[str] = None

class SlideContent(BaseModel):
    title: str
    html: str
    children: Optional[List[dict]] = []
    step: Optional[int] = None

class QuizRequest(BaseModel):
    labName: str
    roomId: Optional[str] = None
    docID: Optional[str] = None
    userID: Optional[str] = None
    userEmail: Optional[str] = None
    content: List[SlideContent]

class PracticeTest(BaseModel):
    practice_test_id: str
    study_guide_title: str
    section_title: str
    guide_type: str
    questions: List[Dict]
    short_answer: List[Dict]
    created_at: datetime
    room_id: Optional[str] = None
    doc_id: Optional[str] = None
    user_id: Optional[str] = None
    user_email: Optional[str] = None

class TestsResponse(BaseModel):
    success: bool
    tests: List[PracticeTest]
    total: int
    message: Optional[str] = None
    error: Optional[str] = None

class ExtractedContent(BaseModel):
    text_content: str
    images: Optional[List[str]] = None
    @model_validator(mode="before")
    @classmethod
    def at_least_one_field(cls, values):
        if not values.get("text_content") and not values.get("images"):
            raise ValueError("At least one of 'text_content' or 'images' must be provided.")
        return values

class QnARequest(BaseModel):
    message: str
    extractedContent: List[ExtractedContent]
    step: int
    step_name: str
    currentStepIndex: Optional[int] = None
    totalSteps: Optional[int] = None
    room_id: Optional[str] = None
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    lab_name: Optional[str] = None
    structuredData: Optional[Dict] = None
    response_level: Optional[ResponseLevel] = "Hint"
    code_context: Optional[CodeContext] = None

class QnAResponse(BaseModel):
    success: bool
    qna_content: Optional[Dict] = None
    error: Optional[str] = None
    message: Optional[str] = None

class ConversationResponse(BaseModel):
    success: bool
    conversation: Optional[Dict] = None
    error: Optional[str] = None
    message: Optional[str] = None

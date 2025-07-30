import os

# Gemini API Configuration
GEMINI_API_KEY = "key"

# MongoDB Configuration
MONGODB_URI = "mongodb://admin:password@ip:port/?authMechanism=SCRAM-SHA-1"
MONGODB_DATABASE = "smart_study"
MONGODB_COLLECTION = "practice_tests"
MONGODB_CHAT_COLLECTION = "chat"

# FastAPI Configuration
API_TITLE = "Quiz Generator API"
API_VERSION = "2.0"
API_HOST = "0.0.0.0"
API_PORT = 8000

# CORS Configuration
CORS_ORIGINS = ["*"]
CORS_CREDENTIALS = True
CORS_METHODS = ["*"]
CORS_HEADERS = ["*"]

# Default Language
DEFAULT_LANGUAGE = "Vietnamese" 
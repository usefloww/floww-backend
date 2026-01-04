import os

from app.settings import settings

# Configure OpenRouter API key for litellm if available
if settings.OPENROUTER_API_KEY:
    os.environ["OPENROUTER_API_KEY"] = settings.OPENROUTER_API_KEY

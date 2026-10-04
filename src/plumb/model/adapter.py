"""One OpenAI-compatible model, served by Ollama. The model name is a setting."""

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider


def build_model(name: str, endpoint: str) -> Model:
    return OpenAIChatModel(name, provider=OllamaProvider(base_url=endpoint))

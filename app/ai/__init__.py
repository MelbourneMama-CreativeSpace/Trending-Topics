"""AI research, summarisation and personalisation (PRD 27-41, 65-67)."""

from app.ai.briefing import ResearchedTopic, research_topic
from app.ai.client import OpenRouterClient, build_ai_client, parse_json_object
from app.ai.schemas import BriefSource, SparkIdea, TopicBrief
from app.ai.service import AiResult, AiService
from app.ai.validation import (
    retrieved_sources,
    scrub_brief,
    scrub_fabricated_urls,
    select_sources,
)

__all__ = [
    "AiResult",
    "AiService",
    "BriefSource",
    "OpenRouterClient",
    "ResearchedTopic",
    "SparkIdea",
    "TopicBrief",
    "build_ai_client",
    "parse_json_object",
    "research_topic",
    "retrieved_sources",
    "scrub_brief",
    "scrub_fabricated_urls",
    "select_sources",
]

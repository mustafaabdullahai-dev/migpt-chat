"""Optional follow-up suggestion generation after an answer."""

from __future__ import annotations

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.services.llm import call_with_retry, get_fast_llm

_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Based on the user's question and the assistant's answer, suggest 3 concise, "
            "follow-up questions the user might want to ask next. Return ONLY a JSON array of "
            "strings of at most 12 words each. No markdown.",
        ),
        ("human", "Question: {question}\n\nAnswer: {answer}"),
    ]
)


def generate_followups(question: str, answer: str, count: int = 3) -> list[str]:
    llm = get_fast_llm(temperature=0.4, max_tokens=256)
    try:
        chain = _PROMPT | llm | JsonOutputParser()
        raw = call_with_retry(
            lambda: chain.invoke({"question": question[:1500], "answer": answer[:3000]}),
            attempts=3,
            desc="follow-ups generation",
        )
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if isinstance(x, str)][:count]
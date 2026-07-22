
"""
Chatbot Service — Google Gemini integration for maternal health counselling.

Design principles:
- FAQ table is READ-ONLY here. We SELECT matching FAQs to ground Gemini's
  response in vetted NHM-approved content, but NEVER INSERT user Q&A into it.
- User conversation history lives in ChatbotConversation.messages (JSONB).
  If you need analytics on what users ask, query that table.
- Gemini is configured once at module level; model instances are created per
  request (cheap — no network call) so system_instruction can carry FAQ context.
"""

import google.generativeai as genai
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import FAQ

# ── One-time SDK configuration ────────────────────────────────────────────────
# Called lazily on first request so the app starts even without a key
# (useful during local dev / CI).

_sdk_configured = False


def _ensure_configured() -> bool:
    global _sdk_configured
    if not settings.GEMINI_API_KEY:
        return False
    if not _sdk_configured:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        _sdk_configured = True
    return True


# ── System prompts ────────────────────────────────────────────────────────────

_SYSTEM_HI = """\
आप "Sangwari Maa" एक AI सहायिका हैं जो Chhattisgarh की गर्भवती महिलाओं की मदद करती हैं।
आप केवल मातृ स्वास्थ्य, गर्भावस्था, BPCR, ANC सेवाएं, सरकारी योजनाएं (JSY, JSSK, PMSMA, PMMVY, Minimata),
और बच्चे की देखभाल के विषय में ही बात करें।

नियम:
1. हमेशा सरल, स्पष्ट हिंदी में जवाब दें।
2. खतरे के लक्षण दिखने पर तुरंत 102 या 108 डायल करने को कहें।
3. कोई भी दवा या इलाज खुद मत सुझाएं — डॉक्टर के पास भेजें।
4. जवाब 3-4 वाक्य से ज़्यादा लंबा न हो।
5. हमेशा ASHA या ANM से मिलने की सलाह दें।
6. अगर प्रश्न मातृ स्वास्थ्य से संबंधित नहीं है, तो विनम्रता से मना करें।
7. यदि नीचे संदर्भ FAQ दिए गए हैं, तो उनके आधार पर उत्तर दें।\
"""

_SYSTEM_EN = """\
You are "Sangwari Maa", an AI assistant helping pregnant women in Chhattisgarh, India.
You ONLY answer questions about maternal health, pregnancy, BPCR, ANC services,
government schemes (JSY, JSSK, PMSMA, PMMVY, Minimata), and newborn care.

Rules:
1. Give clear, simple answers.
2. For danger signs, always say: "Call 102 or 108 immediately."
3. Never prescribe medications — always refer to a doctor.
4. Keep responses brief (3-4 sentences max).
5. Always encourage seeing the ASHA or ANM worker.
6. If the question is unrelated to maternal health, politely decline.
7. If reference FAQs are provided below, base your answer on them.\
"""

SUGGESTED_QUESTIONS_HI = [
    "खून की कमी में क्या खाएं?",
    "ANC जांच कब-कब होती है?",
    "बच्चे की किक कम हो तो क्या करें?",
]

SUGGESTED_QUESTIONS_EN = [
    "What should I eat for anemia?",
    "When are my ANC checkups due?",
    "What to do if I feel less fetal movements?",
]


# ── FAQ retrieval (grounding) ─────────────────────────────────────────────────

_STOP_WORDS = {
    # Hindi
    "का", "की", "के", "है", "हैं", "में", "से", "को", "और", "पर",
    "यह", "वह", "मैं", "हम", "आप", "क्या", "कैसे", "कब", "क्यों", "कहाँ",
    # English
    "the", "is", "in", "of", "to", "a", "an", "and", "or", "for",
    "what", "how", "when", "why", "where", "my", "me", "i", "do",
}


async def _get_faq_context(
    user_message: str,
    language: str,
    db: AsyncSession,
) -> str:
    """
    Keyword-search the FAQ table for entries relevant to the user's message.
    Returns a formatted context block to inject into the system prompt.
    READ-ONLY — never writes to the FAQ table.
    """
    words = [
        w.strip("?।,.!()") for w in user_message.split()
        if len(w.strip("?।,.!()")) > 2
        and w.strip("?।,.!()").lower() not in _STOP_WORDS
    ][:6]  # cap to avoid over-broad OR queries

    if not words:
        return ""

    conditions = []
    for word in words:
        conditions.append(FAQ.title_en.ilike(f"%{word}%"))
        conditions.append(FAQ.title_hi.ilike(f"%{word}%"))
        conditions.append(FAQ.content_en.ilike(f"%{word}%"))

    result = await db.execute(
        select(FAQ)
        .where(FAQ.is_active.is_(True))
        .where(or_(*conditions))
        .limit(4)
    )
    faqs = result.scalars().all()
    if not faqs:
        return ""

    lines = []
    for faq in faqs:
        q = (faq.title_hi or faq.title_en) if language == "hi" else (faq.title_en or faq.title_hi)
        a = (faq.content_hi or faq.content_en) if language == "hi" else (faq.content_en or faq.content_hi)
        if q and a:
            lines.append(f"Q: {q}\nA: {a}")

    return "\n\n".join(lines)


def _build_system_prompt(base: str, faq_context: str, language: str) -> str:
    if not faq_context:
        return base
    header = (
        "\n\n--- संदर्भ FAQ (इन्हें प्राथमिकता दें) ---\n"
        if language == "hi"
        else "\n\n--- Reference FAQs (prioritise these) ---\n"
    )
    return base + header + faq_context


# ── Gemini model factory ──────────────────────────────────────────────────────

def _make_model(system_instruction: str) -> genai.GenerativeModel:
    """
    Create a GenerativeModel with the given system instruction.
    This is pure Python object creation — no network call.
    Called per-request so FAQ context can be baked into system_instruction.
    """
    return genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=system_instruction,
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=600,
            temperature=0.4,
        ),
        safety_settings=[
            # Keep safety settings permissive enough for medical Q&A
            # while still blocking genuinely harmful content.
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
        ],
    )


# ── Main entry point ──────────────────────────────────────────────────────────

async def get_ai_reply(
    user_message: str,
    language: str,
    history: list[dict],
    db: AsyncSession | None = None,
) -> tuple[str, list[str], list[str]]:
    """
    Returns (reply_text, sources, suggested_questions).

    Flow:
    1. GEMINI_API_KEY missing → safe fallback immediately (no crash).
    2. Retrieve relevant FAQs from DB and inject into system prompt.
    3. Build Gemini chat with last 10 history turns.
    4. Send user message, return reply.
    5. Any exception → graceful Hindi/English fallback.
    """
    suggestions = SUGGESTED_QUESTIONS_HI if language == "hi" else SUGGESTED_QUESTIONS_EN

    if not _ensure_configured():
        fallback = (
            "कृपया अपनी ASHA कार्यकर्ता से संपर्क करें।"
            if language == "hi"
            else "Please contact your ASHA worker for guidance."
        )
        return fallback, [], suggestions

    try:
        base_prompt = _SYSTEM_HI if language == "hi" else _SYSTEM_EN

        # Ground response in vetted FAQ content when DB session is available.
        faq_context = ""
        if db is not None:
            faq_context = await _get_faq_context(user_message, language, db)

        system_prompt = _build_system_prompt(base_prompt, faq_context, language)
        model = _make_model(system_prompt)

        # Convert stored history to Gemini format.
        # ChatbotConversation stores role as 'user'/'assistant';
        # Gemini expects 'user'/'model'.
        gemini_history = [
            {
                "role": "model" if h["role"] == "assistant" else "user",
                "parts": [h["content"]],
            }
            for h in history[-10:]
        ]

        chat = model.start_chat(history=gemini_history)
        response = await chat.send_message_async(user_message)
        reply = response.text.strip()

        return reply, [], suggestions

    except Exception as e:
        # Log full exception for debugging on Render; don't expose to client.
        print(f"[Gemini] Error generating reply: {type(e).__name__}: {e}")
        fallback = (
            "इस समय सेवा उपलब्ध नहीं है। कृपया अपनी ASHA से संपर्क करें।"
            if language == "hi"
            else "Service temporarily unavailable. Please contact your ASHA worker."
        )
        return fallback, [], suggestions
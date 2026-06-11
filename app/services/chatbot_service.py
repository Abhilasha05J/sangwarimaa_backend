"""Chatbot Service — OpenAI/Gemini integration for BPCR counselling"""

from openai import AsyncOpenAI
from app.core.config import settings

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

SYSTEM_PROMPT_HI = """
आप "Sangwari Maa" एक AI सहायिका हैं जो Chhattisgarh की गर्भवती महिलाओं की मदद करती हैं।
आप केवल मातृ स्वास्थ्य, गर्भावस्था, BPCR, ANC सेवाएं, सरकारी योजनाएं (JSY, JSSK, PMSMA, PMMVY, Minimata),
और बच्चे की देखभाल के विषय में ही बात करें।

नियम:
1. हमेशा सरल, स्पष्ट हिंदी में जवाब दें
2. खतरे के लक्षण दिखने पर तुरंत 102/108 डायल करने को कहें
3. कोई भी दवा या इलाज खुद मत सुझाएं — डॉक्टर के पास भेजें
4. जवाब 3-4 वाक्य से ज़्यादा लंबा न हो
5. हमेशा ASHA या ANM से मिलने की सलाह दें
"""

SYSTEM_PROMPT_EN = """
You are "Sangwari Maa", an AI assistant helping pregnant women in Chhattisgarh, India.
You only answer questions about maternal health, pregnancy, BPCR, ANC services, 
government schemes (JSY, JSSK, PMSMA, PMMVY, Minimata), and newborn care.

Rules:
1. Give clear, simple answers
2. For danger signs, always say: "Call 102 or 108 immediately"
3. Never prescribe medications — always refer to a doctor
4. Keep responses brief (3-4 sentences max)
5. Always encourage seeing the ASHA or ANM worker
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


async def get_ai_reply(
    user_message: str,
    language: str,
    history: list[dict],
) -> tuple[str, list[str], list[str]]:
    """
    Returns (reply_text, sources, suggested_questions).
    Falls back to a default message if OpenAI is unavailable.
    """
    if not settings.OPENAI_API_KEY:
        fallback = (
            "कृपया अपनी ASHA कार्यकर्ता से संपर्क करें।"
            if language == "hi"
            else "Please contact your ASHA worker for guidance."
        )
        return fallback, [], SUGGESTED_QUESTIONS_HI if language == "hi" else SUGGESTED_QUESTIONS_EN

    system_prompt = SYSTEM_PROMPT_HI if language == "hi" else SYSTEM_PROMPT_EN

    # Build message history (last 10 messages for context)
    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-10:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=300,
            temperature=0.5,
        )
        reply = response.choices[0].message.content.strip()
        suggestions = SUGGESTED_QUESTIONS_HI if language == "hi" else SUGGESTED_QUESTIONS_EN
        return reply, [], suggestions
    except Exception as e:
        print(f"OpenAI error: {e}")
        fallback = (
            "इस समय सेवा उपलब्ध नहीं है। अपनी ASHA से संपर्क करें।"
            if language == "hi"
            else "Service temporarily unavailable. Please contact your ASHA worker."
        )
        return fallback, [], []

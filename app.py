import streamlit as st
import json
import os
import re
import base64
from openai import OpenAI
from datetime import datetime

# ════════════════════════════════════════════════════════════════
# 1. DATA LOADING
# ════════════════════════════════════════════════════════════════

@st.cache_data
def load_programs():
    try:
        if os.path.exists('programs.json'):
            with open('programs.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            st.error("⚠️ programs.json not found. Please add it to the repository.")
    except json.JSONDecodeError as e:
        st.error(f"⚠️ programs.json has a syntax error: {e}")
    except Exception as e:
        st.error(f"⚠️ Error loading programs.json: {e}")
    return None


@st.cache_data
def load_personas():
    try:
        if os.path.exists('personas.json'):
            with open('personas.json', 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {"personas": []}


PROGRAMS_DATA = load_programs()
PERSONAS_DATA = load_personas()


# ════════════════════════════════════════════════════════════════
# 2. INTAKE FIELD DEFINITIONS (OPTIONAL, CAN BE SKIPPED)
# ════════════════════════════════════════════════════════════════

INTAKE_FIELDS = [
    {
        "key": "household_size",
        "question": "How many people are in your household, including yourself?",
        "type": "int",
    },
    {
        "key": "monthly_income",
        "question": "What's your household's total monthly income before taxes?",
        "type": "money",
    },
    {
        "key": "state",
        "question": "What state do you live in?",
        "type": "text",
    },
    {
        "key": "household_composition",
        "question": "Does your household include children under 18, seniors 65+, or anyone with a disability?",
        "type": "text",
    },
    {
        "key": "employment_status",
        "question": "What's your current work situation?",
        "type": "text",
    },
    {
        "key": "situation_text",
        "question": "What's your main concern right now?",
        "type": "text",
    },
]

FIELD_KEYS = [f["key"] for f in INTAKE_FIELDS]


# ════════════════════════════════════════════════════════════════
# 3. ELIGIBILITY REASONING ENGINE
# ════════════════════════════════════════════════════════════════

def get_poverty_line_monthly(household_size: int, guidelines: dict) -> float | None:
    key = str(min(max(household_size, 1), 6))
    annual = guidelines.get(key)
    if annual is None:
        return None
    return annual / 12


def evaluate_criterion(criterion: dict, profile: dict, guidelines: dict) -> dict:
    """Evaluate a single eligibility criterion against a user profile."""
    result = {
        "id": criterion["id"],
        "label": criterion["label"],
        "description": criterion["description"],
        "status": "unclear",
        "reason": "",
        "flag": criterion.get("flag"),
    }

    ctype = criterion.get("type")

    if ctype == "income_threshold":
        hh_size = profile.get("household_size")
        income = profile.get("monthly_income")
        if hh_size is None or income is None:
            result["reason"] = "Missing household size or income information."
            return result

        applies_if = criterion.get("applies_if")
        if applies_if == "household_has_children":
            comp = profile.get("household_composition", [])
            if "children_under_18" not in comp:
                result["status"] = "not_applicable"
                result["reason"] = "This criterion applies to households with children."
                return result

        monthly_poverty_line = get_poverty_line_monthly(hh_size, guidelines)
        if monthly_poverty_line is None:
            result["reason"] = "No poverty guideline data available."
            return result

        threshold = monthly_poverty_line * criterion.get("multiplier", 1.0)
        if income <= threshold:
            result["status"] = "met"
            result["reason"] = (
                f"Your income (${income:,.0f}/mo) is at or below the limit (${threshold:,.0f}/mo)."
            )
        else:
            result["status"] = "not_met"
            result["reason"] = (
                f"Your income (${income:,.0f}/mo) is above the limit (${threshold:,.0f}/mo)."
            )
        return result

    if ctype == "state_match":
        state = (profile.get("state") or "").strip().lower()
        target = (PROGRAMS_DATA.get("state") or "").strip().lower()
        if not state:
            result["reason"] = "State not provided."
            return result
        if state == target:
            result["status"] = "met"
            result["reason"] = f"You live in {PROGRAMS_DATA.get('state')}."
        else:
            result["status"] = "unclear"
            result["reason"] = (
                f"This data is for {PROGRAMS_DATA.get('state')}. Rules may differ in your state."
            )
        return result

    if ctype == "manual_review":
        result["status"] = "unclear"
        result["reason"] = "This requires human review for your specific case."
        return result

    if ctype == "info_only":
        result["status"] = "not_applicable"
        result["reason"] = "Informational only."
        return result

    return result


def evaluate_program(program: dict, profile: dict, guidelines: dict) -> dict:
    """Evaluate all criteria for one program."""
    criterion_results = [
        evaluate_criterion(c, profile, guidelines) for c in program["criteria"]
    ]

    relevant = [r for r in criterion_results if r["status"] != "not_applicable"]
    statuses = [r["status"] for r in relevant]

    if "not_met" in statuses:
        confidence = "Likely not a match"
        confidence_score = 0
    elif all(s == "met" for s in statuses):
        confidence = "Likely match"
        confidence_score = 100
    elif "unclear" in statuses and "not_met" not in statuses:
        confidence = "Possible match - needs review"
        confidence_score = 50
    else:
        confidence = "Unclear"
        confidence_score = 25

    return {
        "program_id": program["program_id"],
        "name": program["name"],
        "plain_language_summary": program["plain_language_summary"],
        "confidence": confidence,
        "confidence_score": confidence_score,
        "criteria_results": criterion_results,
        "required_documents": program["required_documents"],
        "next_steps": program["next_steps"],
        "source_name": program["source_name"],
        "source_url": program["source_url"],
    }


def run_eligibility_check(profile: dict) -> list[dict]:
    if not PROGRAMS_DATA:
        return []
    guidelines = PROGRAMS_DATA.get("poverty_guidelines_annual", {})
    return [
        evaluate_program(p, profile, guidelines)
        for p in PROGRAMS_DATA.get("programs", [])
    ]


def parse_household_composition(text: str) -> list[str]:
    text = (text or "").lower()
    tags = []
    if any(w in text for w in ["child", "kid", "son", "daughter", "baby", "infant"]):
        tags.append("children_under_18")
    if any(w in text for w in ["elderly", "65", "senior", "grandparent", "retired"]):
        tags.append("elderly")
    if "disab" in text:
        tags.append("disability")
    if any(w in text for w in ["none", "no one", "just me", "myself"]):
        return []
    return tags


def parse_money(text: str) -> float | None:
    cleaned = re.sub(r"[^\d.]", "", text or "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_int(text: str) -> int | None:
    match = re.search(r"\d+", text or "")
    if match:
        return int(match.group(0))
    return None


# ════════════════════════════════════════════════════════════════
# 4. RESULT FORMATTING
# ════════════════════════════════════════════════════════════════

STATUS_ICON = {
    "met": "✅",
    "not_met": "❌",
    "unclear": "⚠️",
    "not_applicable": "➖",
}


def format_results_context(results: list[dict]) -> str:
    blocks = []
    for r in results:
        lines = [f"PROGRAM: {r['name']} ({r['program_id']})"]
        lines.append(f"Summary: {r['plain_language_summary']}")
        lines.append(f"Confidence: {r['confidence']}")
        lines.append("Criteria checked:")
        for c in r["criteria_results"]:
            if c["status"] == "not_applicable":
                continue
            icon = STATUS_ICON.get(c["status"], "")
            lines.append(f"  {icon} {c['label']}: {c['status'].upper()}")
            lines.append(f"     Reason: {c['reason']}")
        lines.append("Required documents: " + "; ".join(r["required_documents"]))
        lines.append("Next steps: " + "; ".join(r["next_steps"]))
        lines.append(f"Source: {r['source_name']} ({r['source_url']})")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


# ════════════════════════════════════════════════════════════════
# 5. PAGE CONFIG & CUSTOM UI
# ════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="ClarityNet — Benefits Navigator",
    layout="wide",
    page_icon="🧭",
    initial_sidebar_state="collapsed"
)


def get_image_b64(path: str) -> str | None:
    if os.path.exists(path):
        with open(path, "rb") as f:
            ext = path.rsplit(".", 1)[-1].lower()
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "png": "image/png", "svg": "image/svg+xml"}.get(ext, "image/png")
            return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"
    return None


logo_b64 = get_image_b64("claritynet_logo.png") or get_image_b64("claritynet_logo.jpg")

if logo_b64:
    logo_html = f'<img src="{logo_b64}" class="cn-logo" />'
else:
    logo_html = '<span class="cn-logo-fallback">🧭</span>'

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=Bebas+Neue&display=swap');

* { transition: all 0.3s ease; }

#MainMenu, footer, header { visibility: hidden; }
.stApp { 
    background: linear-gradient(135deg, #f5f7fa 0%, #e9ecef 100%) !important; 
}
.block-container { padding: 2rem 1rem !important; max-width: 100% !important; }
section[data-testid="stSidebar"] { display: none; }
.stChatMessage { background: transparent !important; border: none !important; }

/* ── Header ── */
.cn-header {
    position: sticky;
    top: 0;
    z-index: 10;
    padding: 18px 40px;
    display: flex;
    align-items: center;
    gap: 14px;
    background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
    border-bottom: 2px solid #e9ecef;
    box-shadow: 0 4px 12px rgba(0,0,0,0.08);
    margin: -2rem -1rem 2rem -1rem;
    padding-left: 40px;
}
.cn-logo { width: 40px; height: 40px; object-fit: contain; }
.cn-logo-fallback { font-size: 34px; line-height: 1; }
.cn-brand {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 28px;
    letter-spacing: 3px;
    background: linear-gradient(135deg, #1a3c5e 0%, #2d5a8c 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    line-height: 1;
}
.cn-tagline {
    font-family: 'DM Sans', sans-serif;
    font-size: 12px;
    color: #6c757d;
    margin-left: 12px;
    font-weight: 500;
    border-left: 2px solid #dee2e6;
    padding-left: 12px;
}

/* ── Chat Container ── */
.cn-chat-container {
    max-width: 900px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 20px;
}

/* ── Dashboard Card ── */
.cn-dashboard {
    background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
    border: 2px solid #e9ecef;
    border-radius: 20px;
    padding: 24px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.08);
    margin-bottom: 8px;
}
.cn-dashboard-title {
    font-family: 'DM Sans', sans-serif;
    font-size: 18px;
    font-weight: 700;
    color: #1a3c5e;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.cn-dashboard-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
}
.cn-dashboard-item {
    background: linear-gradient(135deg, #f0f4f8 0%, #e9ecef 100%);
    padding: 16px;
    border-radius: 12px;
    border-left: 4px solid #1a3c5e;
}
.cn-dashboard-item-label {
    font-size: 11px;
    font-weight: 700;
    color: #6c757d;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 6px;
}
.cn-dashboard-item-value {
    font-size: 16px;
    font-weight: 700;
    color: #1a3c5e;
}

/* ── Empty state ── */
.cn-empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 60px 40px;
    gap: 15px;
    background: linear-gradient(135deg, #ffffff 0%, #f0f4f8 100%);
    border-radius: 20px;
    border: 2px solid #e9ecef;
    font-family: 'DM Sans', sans-serif;
    font-size: 15px;
    color: #495057;
    text-align: center;
    box-shadow: 0 8px 24px rgba(0,0,0,0.06);
}
.cn-empty div:first-child { font-size: 54px; animation: float 3s ease-in-out infinite; }
@keyframes float {
    0%, 100% { transform: translateY(0px); }
    50% { transform: translateY(-10px); }
}

/* ── Message rows ── */
.msg-row {
    display: flex;
    gap: 12px;
    align-items: flex-start;
    animation: slideIn 0.4s cubic-bezier(0.4, 0.0, 0.2, 1);
    margin-bottom: 8px;
}
.msg-row.user { flex-direction: row-reverse; }
@keyframes slideIn {
    from { opacity: 0; transform: translateY(12px); }
    to   { opacity: 1; transform: translateY(0); }
}

.msg-avatar {
    width: 36px; height: 36px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 11px; font-weight: 700;
    flex-shrink: 0;
    letter-spacing: 0.5px;
    font-family: 'DM Sans', sans-serif;
    box-shadow: 0 2px 8px rgba(0,0,0,0.12);
}
.msg-avatar.bot  { 
    background: linear-gradient(135deg, #1a3c5e 0%, #2d5a8c 100%);
    color: #fff; 
}
.msg-avatar.user { 
    background: linear-gradient(135deg, #2f9e6e 0%, #27b578 100%);
    color: #fff; 
}

.msg-bubble {
    max-width: 70%;
    padding: 14px 18px;
    font-family: 'DM Sans', sans-serif;
    font-size: 14px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}
.msg-bubble.bot  {
    background: #ffffff;
    color: #1a1a1a;
    border: 1px solid #e9ecef;
    border-radius: 18px 18px 18px 4px;
}
.msg-bubble.user {
    background: linear-gradient(135deg, #2f9e6e 0%, #27b578 100%);
    color: #ffffff;
    border-radius: 18px 18px 4px 18px;
}

/* ── Program Match Card ── */
.cn-program-card {
    background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
    border: 2px solid #e9ecef;
    border-radius: 16px;
    padding: 18px 20px;
    margin-top: 12px;
    font-family: 'DM Sans', sans-serif;
    box-shadow: 0 4px 16px rgba(0,0,0,0.08);
}
.cn-program-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
}
.cn-program-title { 
    font-weight: 700; 
    font-size: 16px; 
    color: #1a3c5e;
}
.cn-program-badge {
    display: inline-block;
    padding: 6px 14px;
    border-radius: 50px;
    font-size: 11.5px;
    font-weight: 700;
    box-shadow: 0 2px 6px rgba(0,0,0,0.08);
}
.badge-likely { 
    background: linear-gradient(135deg, #d4edda 0%, #c3e6cb 100%);
    color: #1d7a4c; 
    border: 1px solid #bee5eb;
}
.badge-possible { 
    background: linear-gradient(135deg, #fff3cd 0%, #ffeaa7 100%);
    color: #a4660a; 
    border: 1px solid #ffdab9;
}
.badge-unlikely { 
    background: linear-gradient(135deg, #f8d7da 0%, #f5c6cb 100%);
    color: #b3261e; 
    border: 1px solid #f1b0b7;
}
.cn-program-summary {
    font-size: 13px;
    color: #495057;
    margin-bottom: 12px;
    line-height: 1.5;
}
.cn-program-criteria {
    font-size: 13px;
    margin-bottom: 12px;
}
.cn-crit-row { 
    padding: 6px 0;
    display: flex;
    gap: 8px;
}
.cn-crit-icon { min-width: 20px; }
.cn-program-footer {
    font-size: 11.5px;
    color: #6c757d;
    border-top: 1px solid #dee2e6;
    padding-top: 10px;
    font-style: italic;
}

/* ── Input ── */
.stChatInput {
    max-width: 900px !important;
    margin: 0 auto !important;
}
.stChatInput textarea {
    border-radius: 50px !important;
    border: 2px solid #dee2e6 !important;
    background: #ffffff !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 14px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06) !important;
    transition: all 0.3s ease !important;
    min-height: 50px !important;
    padding: 14px 18px !important;
    resize: vertical !important;
}
.stChatInput textarea:focus {
    border-color: #1a3c5e !important;
    box-shadow: 0 4px 12px rgba(26, 60, 94, 0.2) !important;
}

/* ── Responsive ── */
@media (max-width: 768px) {
    .cn-header { padding: 14px 20px; }
    .cn-brand { font-size: 22px; }
    .cn-tagline { display: none; }
    .msg-bubble { max-width: 85%; }
    .cn-dashboard-grid { grid-template-columns: 1fr; }
}
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════
# 6. SESSION STATE
# ════════════════════════════════════════════════════════════════

if "messages" not in st.session_state:
    st.session_state.messages = []
if "profile" not in st.session_state:
    st.session_state.profile = {}
if "results" not in st.session_state:
    st.session_state.results = None
if "assessment_triggered" not in st.session_state:
    st.session_state.assessment_triggered = False


# ════════════════════════════════════════════════════════════════
# 7. API CLIENT
# ════════════════════════════════════════════════════════════════

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url="https://ai.hackclub.com/proxy/v1"
)


# ════════════════════════════════════════════════════════════════
# 8. SYSTEM PROMPT BUILDER
# ════════════════════════════════════════════════════════════════

def build_system_prompt(has_assessment: bool = False, results_ctx: str = "") -> str:
    base_rules = """
You are **ClarityNet**, a compassionate and knowledgeable assistant helping people 
understand public support programs (SNAP food assistance, Medicaid health coverage, 
LIHEAP home energy assistance) in Texas.

━━━ 🚨 CORE RULES ━━━
1. ALWAYS use: "you may qualify", "possible match", "may not be a match" — NEVER say "you qualify" or "you don't qualify".
2. Ground ALL eligibility statements in the provided RESULTS data. Never invent criteria.
3. Mention program sources when discussing results.
4. Mark criteria as "REQUIRES HUMAN/CASEWORKER REVIEW" when flagged - don't guess outcomes.
5. Always remind that this is NOT an official determination - encourage contacting agencies.
6. Use warm, supportive, 8th-grade reading level language.
7. Do NOT ask for or store: names, addresses, SSNs, or sensitive identifiers.
8. Answer general questions as a helpful assistant, not just about benefits.
9. If user mentions their situation/income/household, proactively offer to run eligibility check.
"""

    if has_assessment:
        return base_rules + f"""

━━━ STAGE: SMART ASSESSMENT MODE ━━━
The user has provided information about their situation. Below are their results.
Your job is to:
1. Summarize findings warmly and in plain language.
2. Explain why each program is a match/not-match using the specific reasons below.
3. List next steps and documents for promising programs.
4. End with encouragement to follow up with official agencies.
5. Continue answering follow-up questions using ONLY this data.

━━━ ELIGIBILITY RESULTS ━━━
{results_ctx}
"""
    else:
        return base_rules + """

━━━ STAGE: CONVERSATIONAL ASSISTANT ━━━
The user hasn't asked for an assessment yet. Your job is to:
1. Answer any questions they ask warmly and helpfully.
2. Provide general information about SNAP, Medicaid, or LIHEAP if asked.
3. Ask clarifying questions if they seem to have questions about eligibility.
4. If they mention their income, household size, situation, etc., suggest: "I can check 
   which programs you may qualify for if you'd like - just tell me a bit more about your situation."
5. Only run formal assessment if they explicitly ask or provide specific details.
"""


def call_llm(system_prompt: str, history: list[dict], user_message: str) -> str:
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": system_prompt},
                *history,
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
        )
        return resp.choices[0].message.content
    except Exception as e:
        err = str(e)
        if "401" in err or "invalid_api_key" in err:
            return "⚠️ API key issue. Please check your configuration."
        elif "429" in err:
            return "⚠️ Too many requests. Please wait a moment and try again."
        elif "500" in err or "503" in err:
            return "⚠️ AI service temporarily unavailable. Please try again shortly."
        else:
            return f"⚠️ Error: {err}"


# ════════════════════════════════════════════════════════════════
# 9. RENDER HEADER
# ════════════════════════════════════════════════════════════════

st.markdown(f"""
<div class="cn-header">
  {logo_html}
  <span class="cn-brand">CLARITYNET</span>
  <span class="cn-tagline">AI Benefits Navigator for Texas</span>
</div>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════
# 10. RENDER DASHBOARD (CURRENT SITUATION)
# ════════════════════════════════════════════════════════════════

def render_dashboard():
    """Render user's current situation dashboard."""
    if not st.session_state.profile:
        return
    
    profile = st.session_state.profile
    items_html = ""
    
    if profile.get("household_size"):
        items_html += f"""
        <div class="cn-dashboard-item">
            <div class="cn-dashboard-item-label">👥 Household Size</div>
            <div class="cn-dashboard-item-value">{profile['household_size']} people</div>
        </div>
        """
    
    if profile.get("monthly_income"):
        income = profile["monthly_income"]
        items_html += f"""
        <div class="cn-dashboard-item">
            <div class="cn-dashboard-item-label">💰 Monthly Income</div>
            <div class="cn-dashboard-item-value">${income:,.0f}</div>
        </div>
        """
    
    if profile.get("state"):
        items_html += f"""
        <div class="cn-dashboard-item">
            <div class="cn-dashboard-item-label">📍 State</div>
            <div class="cn-dashboard-item-value">{profile['state']}</div>
        </div>
        """
    
    if profile.get("employment_status"):
        items_html += f"""
        <div class="cn-dashboard-item">
            <div class="cn-dashboard-item-label">💼 Employment</div>
            <div class="cn-dashboard-item-value">{profile['employment_status']}</div>
        </div>
        """
    
    household_comp = profile.get("household_composition", [])
    if household_comp:
        comp_text = ", ".join(household_comp).replace("_", " ").title()
        items_html += f"""
        <div class="cn-dashboard-item">
            <div class="cn-dashboard-item-label">👨‍👩‍👧‍👦 Household Composition</div>
            <div class="cn-dashboard-item-value">{comp_text}</div>
        </div>
        """
    
    if profile.get("situation_text"):
        items_html += f"""
        <div class="cn-dashboard-item">
            <div class="cn-dashboard-item-label">⚠️ Current Situation</div>
            <div class="cn-dashboard-item-value">{profile['situation_text']}</div>
        </div>
        """
    
    if items_html:
        st.markdown(f"""
        <div class="cn-dashboard">
            <div class="cn-dashboard-title">📊 Your Current Situation</div>
            <div class="cn-dashboard-grid">
                {items_html}
            </div>
        </div>
        """, unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════
# 11. RENDER CHAT
# ════════════════════════════════════════════════════════════════

def render_chat():
    """Render chat messages and results."""
    chat_html = '<div class="cn-chat-container">'
    
    if not st.session_state.messages and not st.session_state.results:
        chat_html += (
            '<div class="cn-empty">'
            '<div>🧭</div>'
            '<div><strong>Welcome to ClarityNet</strong><br>'
            'Ask me anything about SNAP, Medicaid, LIHEAP, or your eligibility.<br>'
            'I can answer general questions or help you find the right programs.</div>'
            '</div>'
        )
    else:
        for msg in st.session_state.messages:
            role = msg["role"]
            content = msg["content"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if role == "assistant":
                chat_html += (
                    '<div class="msg-row bot">'
                    '<div class="msg-avatar bot">CN</div>'
                    f'<div class="msg-bubble bot">{content}</div>'
                    '</div>'
                )
            else:
                chat_html += (
                    '<div class="msg-row user">'
                    '<div class="msg-avatar user">ME</div>'
                    f'<div class="msg-bubble user">{content}</div>'
                    '</div>'
                )
    
    # Render program cards if results exist
    if st.session_state.results:
        for r in st.session_state.results:
            if r["confidence"] == "Likely match":
                badge_class = "badge-likely"
                badge_icon = "✅"
            elif r["confidence"] == "Likely not a match":
                badge_class = "badge-unlikely"
                badge_icon = "❌"
            else:
                badge_class = "badge-possible"
                badge_icon = "⚠️"
            
            crit_html = ""
            for c in r["criteria_results"]:
                if c["status"] == "not_applicable":
                    continue
                icon = STATUS_ICON.get(c["status"], "?")
                crit_html += f"""
                <div class="cn-crit-row">
                    <span class="cn-crit-icon">{icon}</span>
                    <span><b>{c["label"]}</b><br/><small>{c["reason"]}</small></span>
                </div>
                """
            
            docs = ", ".join(r["required_documents"]) if r["required_documents"] else "Check with agency"
            steps = ", ".join(r["next_steps"]) if r["next_steps"] else "Contact the program"
            
            chat_html += f"""
            <div class="msg-row bot">
                <div class="msg-avatar bot">CN</div>
                <div class="cn-program-card" style="max-width:70%;">
                    <div class="cn-program-header">
                        <span class="cn-program-title">📋 {r["name"]}</span>
                        <span class="cn-program-badge {badge_class}">{badge_icon} {r["confidence"]}</span>
                    </div>
                    <div class="cn-program-summary">{r["plain_language_summary"]}</div>
                    <div class="cn-program-criteria">{crit_html}</div>
                    <div style="font-size:12px;margin:8px 0;">
                        <b>Required Documents:</b> {docs}
                    </div>
                    <div style="font-size:12px;">
                        <b>Next Steps:</b> {steps}
                    </div>
                    <div class="cn-program-footer">
                        This is not an official determination. Source: {r["source_name"]}. 
                        Contact {r["source_name"]} or speak with a caseworker for final confirmation.
                    </div>
                </div>
            </div>
            """
    
    chat_html += '</div>'
    st.markdown(chat_html, unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════
# 12. EXTRACT PROFILE INFO FROM MESSAGE (SMART PARSING)
# ════════════════════════════════════════════════════════════════

def try_extract_profile_from_message(text: str) -> dict:
    """Attempt to extract household size, income, etc. from natural language."""
    profile_update = {}
    
    # Income patterns: "make $2000", "earn about $1500", "income is $3000"
    income_match = re.search(r'\$?\s*(\d{3,5})\s*(a month|month|monthly|/month)', text, re.IGNORECASE)
    if income_match:
        profile_update["monthly_income"] = float(income_match.group(1))
    
    # Household size: "family of 4", "3 of us", "household of 5"
    household_match = re.search(r'(?:family|household|people|us) of (\d)|(\d) people', text, re.IGNORECASE)
    if household_match:
        size = household_match.group(1) or household_match.group(2)
        profile_update["household_size"] = int(size)
    
    # State: "live in texas", "from california"
    state_match = re.search(r'(?:live in|from|in|state of)\s+([a-z]+)', text, re.IGNORECASE)
    if state_match:
        profile_update["state"] = state_match.group(1).strip()
    
    # Employment
    if any(w in text.lower() for w in ["unemployed", "no job", "lost job", "laid off"]):
        profile_update["employment_status"] = "Unemployed"
    elif any(w in text.lower() for w in ["part-time", "part time"]):
        profile_update["employment_status"] = "Part-time"
    elif any(w in text.lower() for w in ["full-time", "full time", "working"]):
        profile_update["employment_status"] = "Full-time"
    
    # Household composition
    if any(w in text.lower() for w in ["kids", "children", "baby"]):
        if "household_composition" not in profile_update:
            profile_update["household_composition"] = []
        profile_update["household_composition"].append("children_under_18")
    
    return profile_update


# ════════════════════════════════════════════════════════════════
# 13. MAIN CHAT HANDLER
# ════════════════════════════════════════════════════════════════

if prompt := st.chat_input("Ask me anything or tell me about your situation..."):
    # Add user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    # Try to extract profile info from the message
    extracted = try_extract_profile_from_message(prompt)
    st.session_state.profile.update(extracted)
    
    # Check if we should trigger assessment
    should_assess = (
        st.session_state.assessment_triggered == False
        and len(extracted) > 0  # Some info was extracted
        and st.session_state.profile.get("household_size")
        and st.session_state.profile.get("monthly_income")
    )
    
    # Get LLM response
    history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages[:-1]]
    
    if should_assess and st.session_state.results is None:
        # Run eligibility check
        st.session_state.assessment_triggered = True
        st.session_state.results = run_eligibility_check(st.session_state.profile)
        results_ctx = format_results_context(st.session_state.results)
        sys_prompt = build_system_prompt(has_assessment=True, results_ctx=results_ctx)
        
        response = call_llm(
            sys_prompt, 
            history, 
            "Based on what I've shared, can you check my eligibility for these programs?"
        )
    else:
        # Normal conversation
        sys_prompt = build_system_prompt(
            has_assessment=st.session_state.results is not None,
            results_ctx=format_results_context(st.session_state.results or [])
        )
        response = call_llm(sys_prompt, history, prompt)
    
    st.session_state.messages.append({"role": "assistant", "content": response})
    st.rerun()


# ════════════════════════════════════════════════════════════════
# 14. RENDER UI
# ════════════════════════════════════════════════════════════════

render_dashboard()
render_chat()

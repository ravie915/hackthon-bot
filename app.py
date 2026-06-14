import streamlit as st
import json
import os
import re
import base64
from openai import OpenAI
import time

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
        st.error(f"⚠️ programs.json has a syntax error: {e}. Please validate the JSON file.")
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
# 2. INTAKE FIELD DEFINITIONS
# ════════════════════════════════════════════════════════════════

INTAKE_FIELDS = [
    {
        "key": "household_size",
        "question": "Let's start simple - how many people are in your household, including yourself?",
        "type": "int",
        "required": True,
    },
    {
        "key": "monthly_income",
        "question": "What's your household's total monthly income before taxes? A rough estimate is fine.",
        "type": "money",
        "required": True,
    },
    {
        "key": "state",
        "question": "What state do you live in? (This version focuses on Texas, but tell me anyway.)",
        "type": "text",
        "required": True,
    },
    {
        "key": "household_composition",
        "question": "Does your household include any children under 18, anyone 65 or older, or anyone with a disability? (You can say 'none' if not.)",
        "type": "text",
        "required": True,
    },
    {
        "key": "employment_status",
        "question": "What's your current work situation? (e.g., full-time, part-time, unemployed, recently changed)",
        "type": "text",
        "required": True,
    },
    {
        "key": "situation_text",
        "question": "In a few words, what's going on right now? (e.g., 'lost my job', 'hours got cut', 'can't afford heating bill')",
        "type": "text",
        "required": True,
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
                result["reason"] = "This criterion applies to households with children; none reported."
                return result

        monthly_poverty_line = get_poverty_line_monthly(hh_size, guidelines)
        if monthly_poverty_line is None:
            result["reason"] = "No poverty guideline data available for this household size."
            return result

        threshold = monthly_poverty_line * criterion.get("multiplier", 1.0)
        if income <= threshold:
            result["status"] = "met"
            result["reason"] = (
                f"Your reported monthly income (${income:,.0f}) is at or below the "
                f"program's limit for a household of {hh_size} (about ${threshold:,.0f}/month)."
            )
        else:
            result["status"] = "not_met"
            result["reason"] = (
                f"Your reported monthly income (${income:,.0f}) is above the "
                f"program's limit for a household of {hh_size} (about ${threshold:,.0f}/month)."
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
            result["reason"] = f"You indicated you live in {PROGRAMS_DATA.get('state')}, which this version covers."
        else:
            result["status"] = "unclear"
            result["reason"] = (
                f"You indicated '{profile.get('state')}'. This prototype's data is built for "
                f"{PROGRAMS_DATA.get('state')} - rules may differ in your state."
            )
        return result

    if ctype == "manual_review":
        result["status"] = "unclear"
        result["reason"] = (
            "This depends on details that vary case-by-case or by state and "
            "cannot be reliably determined automatically."
        )
        return result

    if ctype == "info_only":
        result["status"] = "not_applicable"
        result["reason"] = "Informational only - does not affect eligibility."
        return result

    result["reason"] = "Unrecognized criterion type."
    return result


def evaluate_program(program: dict, profile: dict, guidelines: dict) -> dict:
    """Evaluate all criteria for one program and produce an overall confidence label."""
    criterion_results = [
        evaluate_criterion(c, profile, guidelines) for c in program["criteria"]
    ]

    relevant = [r for r in criterion_results if r["status"] != "not_applicable"]
    statuses = [r["status"] for r in relevant]

    if "not_met" in statuses:
        confidence = "Likely not a match"
    elif all(s == "met" for s in statuses):
        confidence = "Likely match"
    elif "unclear" in statuses and "not_met" not in statuses:
        confidence = "Possible match - needs review"
    else:
        confidence = "Unclear"

    return {
        "program_id": program["program_id"],
        "name": program["name"],
        "plain_language_summary": program["plain_language_summary"],
        "confidence": confidence,
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
        lines.append(f"Overall confidence label: {r['confidence']}")
        lines.append("Criteria checked:")
        for c in r["criteria_results"]:
            if c["status"] == "not_applicable":
                continue
            icon = STATUS_ICON.get(c["status"], "")
            flag_note = ""
            if c.get("flag") == "needs_human_review":
                flag_note = " [REQUIRES HUMAN/CASEWORKER REVIEW]"
            lines.append(f"  {icon} {c['label']} - {c['status'].upper()}{flag_note}")
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
.block-container { padding: 0 !important; max-width: 100% !important; }
section[data-testid="stSidebar"] { display: none; }
.stChatMessage { background: transparent !important; border: none !important; }

/* ── Header ── */
.cn-header {
    position: fixed;
    top: 0; left: 0; right: 0;
    z-index: 10;
    padding: 18px 40px;
    display: flex;
    align-items: center;
    gap: 14px;
    background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
    border-bottom: 2px solid #e9ecef;
    box-shadow: 0 4px 12px rgba(0,0,0,0.08);
}
.cn-logo { width: 40px; height: 40px; object-fit: contain; }
.cn-logo-fallback { font-size: 34px; line-height: 1; }
.cn-brand {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 30px;
    letter-spacing: 4px;
    background: linear-gradient(135deg, #1a3c5e 0%, #2d5a8c 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    line-height: 1;
}
.cn-tagline {
    font-family: 'DM Sans', sans-serif;
    font-size: 12.5px;
    color: #6c757d;
    margin-left: 6px;
    font-weight: 500;
}

/* ── Chat area ── */
.cn-chat {
    position: relative;
    z-index: 5;
    padding: 100px 52px 170px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    max-width: 760px;
    margin: 0 auto;
}

/* ── Empty state ── */
.cn-empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 80px 40px;
    gap: 15px;
    background: linear-gradient(135deg, #ffffff 0%, #f0f4f8 100%);
    border-radius: 24px;
    border: 2px solid #e9ecef;
    font-family: 'DM Sans', sans-serif;
    font-size: 15px;
    color: #495057;
    text-align: center;
    box-shadow: 0 8px 24px rgba(0,0,0,0.06);
}
.cn-empty div:first-child { font-size: 60px; animation: float 3s ease-in-out infinite; }
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
    flex-shrink: 0; margin-top: 2px;
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
    color: #fff; font-size: 11px; 
}

.msg-bubble {
    max-width: 75%;
    padding: 14px 18px;
    font-family: 'DM Sans', sans-serif;
    font-size: 14.5px;
    line-height: 1.65;
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

/* ── Eligibility cards ── */
.cn-elig-card {
    border: none;
    border-radius: 16px;
    padding: 18px 20px;
    margin-top: 4px;
    background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
    font-family: 'DM Sans', sans-serif;
    font-size: 13.5px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.08);
    border-left: 4px solid #1a3c5e;
}
.cn-elig-title { 
    font-weight: 700; 
    font-size: 15px; 
    margin-bottom: 8px; 
    color: #1a3c5e;
    background: linear-gradient(135deg, #1a3c5e 0%, #2d5a8c 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.cn-elig-badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 50px;
    font-size: 11.5px;
    font-weight: 700;
    margin-bottom: 10px;
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
.cn-crit-row { padding: 4px 0; }

/* ── Disclaimer ── */
.cn-disclaimer {
    font-family: 'DM Sans', sans-serif;
    font-size: 11.5px;
    color: #6c757d;
    border-top: 1px solid #dee2e6;
    margin-top: 12px;
    padding-top: 10px;
    font-style: italic;
}

/* ── Chips ── */
.cn-chips {
    display: flex; gap: 10px; flex-wrap: wrap;
    margin-bottom: 10px;
    max-width: 760px;
    margin-left: auto; margin-right: auto;
    padding: 0 16px;
    justify-content: center;
}
.cn-chip {
    padding: 8px 18px;
    border-radius: 50px;
    border: 2px solid #dee2e6;
    background: #ffffff;
    font-family: 'DM Sans', sans-serif;
    font-size: 12.5px; 
    color: #495057;
    white-space: nowrap;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    font-weight: 500;
    cursor: pointer;
    transition: all 0.3s ease;
}
.cn-chip:hover {
    border-color: #1a3c5e;
    box-shadow: 0 4px 12px rgba(26, 60, 94, 0.15);
    transform: translateY(-2px);
}

/* ── Input ── */
div[data-testid="stChatInput"] {
    max-width: 760px !important;
    margin: 0 auto !important;
}
div[data-testid="stChatInput"] textarea {
    border-radius: 50px !important;
    border: 2px solid #dee2e6 !important;
    background: #ffffff !important;
    font-family: 'DM Sans', sans-serif !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06) !important;
    transition: all 0.3s ease !important;
}
div[data-testid="stChatInput"] textarea:focus {
    border-color: #1a3c5e !important;
    box-shadow: 0 4px 12px rgba(26, 60, 94, 0.2) !important;
}

/* ── Progress bar ── */
.progress-track {
    width: 100%;
    height: 6px;
    background: #e9ecef;
    border-radius: 10px;
    margin: 12px 0;
    overflow: hidden;
}
.progress-fill {
    height: 100%;
    background: linear-gradient(90deg, #1a3c5e 0%, #2d5a8c 100%);
    border-radius: 10px;
    animation: slideProgress 0.6s ease;
}
@keyframes slideProgress {
    from { width: 0; }
    to { width: 100%; }
}

/* ── Button ── */
.stButton > button {
    background: linear-gradient(135deg, #1a3c5e 0%, #2d5a8c 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 50px !important;
    padding: 10px 24px !important;
    font-weight: 600 !important;
    box-shadow: 0 4px 12px rgba(26, 60, 94, 0.2) !important;
    transition: all 0.3s ease !important;
}
.stButton > button:hover {
    box-shadow: 0 6px 16px rgba(26, 60, 94, 0.3) !important;
    transform: translateY(-2px) !important;
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
if "intake_index" not in st.session_state:
    st.session_state.intake_index = 0
if "intake_complete" not in st.session_state:
    st.session_state.intake_complete = False
if "results" not in st.session_state:
    st.session_state.results = None


def reset_session():
    st.session_state.messages = []
    st.session_state.profile = {}
    st.session_state.intake_index = 0
    st.session_state.intake_complete = False
    st.session_state.results = None


# ════════════════════════════════════════════════════════════════
# 7. RENDER HEADER
# ════════════════════════════════════════════════════════════════

st.markdown(f"""
<div class="cn-header">
  {logo_html}
  <span class="cn-brand">CLARITYNET</span>
  <span class="cn-tagline">Benefits Navigator (Texas Demo) — SNAP · Medicaid · LIHEAP</span>
</div>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════
# 8. RENDER CHAT HISTORY
# ════════════════════════════════════════════════════════════════

chat_html = '<div class="cn-chat">'

if not st.session_state.messages:
    chat_html += (
        '<div class="cn-empty">'
        '<div>🧭</div>'
        "<div><strong>Welcome to ClarityNet</strong><br>"
        "I'll ask a few quick questions, then check what support you may be able to get.<br>"
        '✨ Nothing you share here is saved or sent anywhere outside this demo.</div>'
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

# Progress indicator
if st.session_state.intake_index > 0 and not st.session_state.intake_complete:
    progress = (st.session_state.intake_index / len(INTAKE_FIELDS)) * 100
    chat_html += f'<div class="progress-track"><div class="progress-fill" style="width: {progress}%"></div></div>'

# Render eligibility result cards
if st.session_state.results:
    for r in st.session_state.results:
        if r["confidence"] == "Likely match":
            badge_class = "badge-likely"
        elif r["confidence"] == "Likely not a match":
            badge_class = "badge-unlikely"
        else:
            badge_class = "badge-possible"

        crit_html = ""
        for c in r["criteria_results"]:
            if c["status"] == "not_applicable":
                continue
            icon = STATUS_ICON.get(c["status"], "")
            crit_html += f'<div class="cn-crit-row">{icon} <b>{c["label"]}</b>: {c["reason"]}</div>'

        chat_html += (
            '<div class="msg-row bot">'
            '<div class="msg-avatar bot">CN</div>'
            '<div class="cn-elig-card" style="max-width:75%;">'
            f'<div class="cn-elig-title">📋 {r["name"]}</div>'
            f'<span class="cn-elig-badge {badge_class}">{r["confidence"]}</span>'
            f'<div>{crit_html}</div>'
            '<div class="cn-disclaimer">'
            f'This is not an official eligibility determination. Source: {r["source_name"]}. '
            'To get a final answer, apply through the official agency or speak with a caseworker.'
            '</div>'
            '</div>'
            '</div>'
        )

chat_html += "</div>"
st.markdown(chat_html, unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════
# 9. SUGGESTION CHIPS
# ════════════════════════════════════════════════════════════════

if not st.session_state.messages:
    chips_html = (
        '<div class="cn-chips">'
        '<span class="cn-chip">💬 Start Chat</span>'
        '<span class="cn-chip">🍎 SNAP Food Aid</span>'
        '<span class="cn-chip">🏥 Medicaid Health</span>'
        '<span class="cn-chip">🔌 LIHEAP Energy</span>'
        '</div>'
    )
    st.markdown(chips_html, unsafe_allow_html=True)

st.markdown("<div style='height: 80px;'></div>", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════
# 10. API CLIENT
# ════════════════════════════════════════════════════════════════

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url="https://ai.hackclub.com/proxy/v1"
)


# ════════════════════════════════════════════════════════════════
# 11. SYSTEM PROMPT BUILDER
# ════════════════════════════════════════════════════════════════

def build_system_prompt(stage: str, results_ctx: str = "") -> str:
    base_rules = """
You are **ClarityNet**, a friendly assistant that helps people understand whether they
may qualify for public support programs (SNAP food assistance, Medicaid health coverage,
and LIHEAP home energy assistance) in Texas, and what to do next.

━━━ 🚨 ABSOLUTE RULES — NEVER BREAK THESE ━━━
1. NEVER say "you qualify", "you are eligible", or "you don't qualify" / "you are not eligible".
   ALWAYS use: "you may qualify", "this looks like a possible match", "this looks like it
   may not be a match based on what you shared", or "this needs a human/caseworker to confirm".
2. Every eligibility-related statement you make MUST be grounded in the ELIGIBILITY RESULTS
   data provided below. Do NOT invent criteria, numbers, or outcomes not present there.
3. Always mention the source for each program when discussing results.
4. Any criterion marked "REQUIRES HUMAN/CASEWORKER REVIEW" must be explicitly described to
   the user as something a caseworker needs to confirm - do not guess the outcome.
5. Always end any eligibility discussion with a reminder that this is not an official
   determination and the user should apply through the official agency or speak with a
   caseworker for a final answer.
6. Keep language warm, calm, and at roughly an 8th-grade reading level. The user may be
   stressed - be supportive, not clinical.
7. Do not ask for or store names, addresses, SSNs, or other identifying information.
"""

    if stage == "intake":
        return base_rules + """

━━━ CURRENT STAGE: GUIDED INTAKE ━━━
You are currently gathering basic information one question at a time. A structured
intake flow is asking the questions outside of you - your job right now is just to
respond warmly and briefly to whatever the user says (e.g., acknowledge their answer
in one short sentence), without giving any eligibility conclusions yet, since the
full picture isn't ready.
"""

    if stage == "results":
        return base_rules + f"""

━━━ CURRENT STAGE: RESULTS ━━━
The structured eligibility check has been completed. Below are the results for each
program, already evaluated against the user's answers. Your job is to:
1. Briefly and warmly summarize what you found across all three programs.
2. For each program, explain the result in plain language, referencing the specific
   criteria that were met, not met, or unclear - using the exact reasons given below.
3. Clearly state next steps and required documents for any "Likely match" or
   "Possible match - needs review" program.
4. For anything flagged for human/caseworker review, say so explicitly.
5. End with a short, warm reminder that this is a starting point, not a final answer,
   and encourage the user to follow up with the official agency.

━━━ ELIGIBILITY RESULTS (structured, already computed - do not recompute or contradict) ━━━
{results_ctx}
"""

    return base_rules + f"""

━━━ CURRENT STAGE: FOLLOW-UP ━━━
The user has already seen their results below. Answer follow-up questions using ONLY
this data. If they ask about something not covered here (e.g., a different program,
a different state), say this prototype currently only covers SNAP, Medicaid, and LIHEAP
in Texas, and that a caseworker or 2-1-1 can help with other programs.

━━━ ELIGIBILITY RESULTS (for reference) ━━━
{results_ctx}
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
            return "⚠️ API key is invalid or missing. Please check your environment configuration."
        elif "429" in err or "rate_limit" in err:
            return "⚠️ Too many requests right now. Please wait a moment and try again."
        elif "500" in err or "502" in err or "503" in err:
            return "⚠️ The AI service is temporarily unavailable. Please try again shortly."
        elif "context_length" in err or "maximum context" in err:
            return "⚠️ This conversation got too long. Please start a new session."
        else:
            return f"⚠️ Something went wrong: {err}"


# ════════════════════════════════════════════════════════════════
# 12. MAIN CHAT HANDLER
# ════════════════════════════════════════════════════════════════

if prompt := st.chat_input("Type your answer here..."):
    st.session_state.messages.append({"role": "user", "content": prompt})

    if st.session_state.intake_index == 0 and not st.session_state.profile:
        first_field = INTAKE_FIELDS[0]
        history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages[:-1]]
        sys_prompt = build_system_prompt("intake")
        ack = call_llm(sys_prompt, history, prompt)
        followup = f"\n\n{first_field['question']}"
        st.session_state.messages.append({"role": "assistant", "content": ack + followup})
        st.session_state.intake_index = 1
        st.rerun()

    elif not st.session_state.intake_complete:
        current_field = INTAKE_FIELDS[st.session_state.intake_index - 1]
        key = current_field["key"]
        ftype = current_field["type"]

        if ftype == "int":
            val = parse_int(prompt)
            st.session_state.profile[key] = val if val is not None else 0
        elif ftype == "money":
            val = parse_money(prompt)
            st.session_state.profile[key] = val if val is not None else 0.0
        elif key == "household_composition":
            st.session_state.profile[key] = parse_household_composition(prompt)
            st.session_state.profile["_household_composition_raw"] = prompt
        else:
            st.session_state.profile[key] = prompt.strip()

        if st.session_state.intake_index < len(INTAKE_FIELDS):
            next_field = INTAKE_FIELDS[st.session_state.intake_index]
            history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages[:-1]]
            sys_prompt = build_system_prompt("intake")
            ack = call_llm(sys_prompt, history, prompt)
            followup = f"\n\n{next_field['question']}"
            st.session_state.messages.append({"role": "assistant", "content": ack + followup})
            st.session_state.intake_index += 1
            st.rerun()
        else:
            st.session_state.intake_complete = True
            results = run_eligibility_check(st.session_state.profile)
            st.session_state.results = results
            results_ctx = format_results_context(results)

            history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages[:-1]]
            sys_prompt = build_system_prompt("results", results_ctx)
            answer = call_llm(
                sys_prompt, history,
                "Please summarize my results now based on everything I've shared."
            )
            st.session_state.messages.append({"role": "assistant", "content": answer})
            st.rerun()

    else:
        results_ctx = format_results_context(st.session_state.results or [])
        history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages[:-1]]
        sys_prompt = build_system_prompt("followup", results_ctx)
        answer = call_llm(sys_prompt, history, prompt)
        st.session_state.messages.append({"role": "assistant", "content": answer})
        st.rerun()


# ════════════════════════════════════════════════════════════════
# 13. RESET BUTTON
# ════════════════════════════════════════════════════════════════

with st.container():
    cols = st.columns([6, 1])
    with cols[1]:
        if st.button("🔄 Start over"):
            reset_session()
            st.rerun()
